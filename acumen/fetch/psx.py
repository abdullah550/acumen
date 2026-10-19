"""Pakistan Stock Exchange: universe, quotes, daily bars with true high/low, fundamentals.

Source order
------------
1. **PSX data portal** (:mod:`acumen.fetch.psx_dps`): the symbol list, today's market watch, and one request per past
   trading day that returns *every* symbol's open, high, low, close, volume and last-day close (``LDCP``). A first run
   walks backwards from today; later runs add the new days and keep extending the history until the target is reached
   (``data.psx.history_years``), within the per-run request budget (``data.psx.max_requests_per_run``).
2. **TradingView screener** (:mod:`acumen.fetch.psx_tv`): fundamentals for every stock in one request; also a price
   snapshot and the symbol list when the portal is unreachable.
3. **Yahoo Finance** (:mod:`acumen.fetch.yahoo`): daily history for the most liquid stocks that have too little history,
   and the KSE-100 when the portal's index route fails.

Corporate actions
-----------------
The portal's prices are not adjusted for bonus shares, rights or dividends, but its ``LDCP`` (last-day close price) is
adjusted on the ex-date. When a day's ``LDCP`` differs from the previous stored close by more than rounding, that ratio
is a corporate action and all earlier prices are scaled by it, so a 20% bonus issue does not look like a 17% crash to
every indicator and back-test. If more than a quarter of all symbols "have an event" on the same day, a trading day was
missed rather than a corporate action happened, and no adjustment is made that day.

Holidays
--------
A day that returns no rows is only remembered as a holiday once a later day returns rows, so an outage or throttling
(which also returns nothing) never leaves a permanent hole in the history.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any, Callable

import pandas as pd

from ..config import Config
from ..model import Instrument, MarketData, SourceStatus, make_id, normalise_bars
from ..store import HistoryStore
from . import psx_dps as dps_mod
from . import psx_tv as tv
from . import yahoo
from .common import bars_frame, iso, pkt_now, splice, status, to_float
from .http import Client, FetchError

STATE = "psx_state"
NODES = "psx_nodes"
UNIVERSE = "psx_universe"
FUNDAMENTALS = "psx_fundamentals"
SESSION_FINAL_HOUR_PKT = 17  # the regular market is over (Friday sessions end late), so a day's numbers are final
EVENT_STORM_SHARE = 0.25
BONUS_VOLUME_BELOW = 0.92  # a price factor below this looks like a bonus issue or split: share volume rises by 1/f; smaller ones are dividends
HISTORY_SLACK_DAYS = 4  # the oldest stored day may sit this far inside the target (weekend, holiday) and still count as "reached"
BLANK_STREAK_STOP = 12  # weekdays in a row with no data: stop asking (no holiday run is that long)
INDEX_CODES = {"KSE100": "KSE-100 Index", "KMI30": "KMI-30 Index"}
YAHOO_INDEX = {"KSE100": "^KSE"}


def title_sector(name: str) -> str:
    """``COMMERCIAL BANKS`` -> ``Commercial Banks`` (keeps ``&`` and small words tidy)."""
    small = {"and", "of", "the", "for"}
    out = []
    for i, w in enumerate(str(name).strip().split()):
        lw = w.lower()
        out.append(lw if (i and lw in small) else (w if w == "&" else lw.capitalize()))
    return " ".join(out)


# ---------------------------------------------------------------------------------------------- corporate actions
def apply_events(df: pd.DataFrame | None, events: list[tuple[pd.Timestamp, float]]) -> pd.DataFrame | None:
    """Scale every bar dated before each event by its factor (prices) and, for bonus issues and splits, by 1/factor (volume)."""
    if df is None or len(df) == 0 or not events:
        return df
    out = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = out[col].astype("float64")
    for when, f in events:
        mask = out.index < pd.Timestamp(when)
        if not mask.any():
            continue
        out.loc[mask, ["open", "high", "low", "close"]] = out.loc[mask, ["open", "high", "low", "close"]] * f
        if f < BONUS_VOLUME_BELOW:
            out.loc[mask, "volume"] = out.loc[mask, "volume"] / f
    return out


class Block:
    """A run of consecutive trading days turned into per-symbol bars, with corporate actions detected from LDCP.

    ``last_close`` holds each symbol's last stored close when the block continues stored history (forward update);
    leave it empty for a block that starts a history or extends it backwards.
    """

    def __init__(self, allowed: set[str], last_close: dict[str, float] | None = None):
        self.allowed = allowed
        self.last: dict[str, float] = dict(last_close or {})
        self.rows: dict[str, list[tuple]] = {}
        self.first_ldcp: dict[str, float] = {}
        self.events: dict[str, list[tuple[pd.Timestamp, float]]] = {}
        self.notes: list[str] = []
        self.days = 0

    @staticmethod
    def _tol(prev: float) -> float:
        return max(0.004, 0.0075 / max(prev, 1e-9))

    def add_day(self, day: dt.date, rows: list[dict[str, Any]]) -> None:
        ts = pd.Timestamp(day)
        prepared = []
        for r in rows:
            sym = r["symbol"]
            c = r.get("close")
            if sym not in self.allowed or c is None or c <= 0:
                continue
            o, h, lo = (v if (v is not None and v > 0) else None for v in (r.get("open"), r.get("high"), r.get("low")))
            prepared.append((sym, r.get("ldcp"), o, h, lo, float(c), r.get("volume") or 0.0))
        facs: dict[str, float] = {}
        with_prev = 0
        for sym, ldcp, *_ in prepared:
            prev = self.last.get(sym)
            if prev is None:
                continue
            with_prev += 1
            if ldcp is None or ldcp <= 0 or prev <= 0:
                continue
            f = ldcp / prev
            if abs(f - 1.0) > self._tol(prev) and 0.1 < f < 10.0:
                facs[sym] = f
        if with_prev >= 20 and len(facs) / with_prev > EVENT_STORM_SHARE:
            self.notes.append(f"{day.isoformat()}: {len(facs)} of {with_prev} symbols differed from the previous close (a missed day, not corporate actions); no adjustment made")
            facs = {}
        for sym, f in facs.items():
            self.events.setdefault(sym, []).append((ts, f))
        for sym, ldcp, o, h, lo, c, v in prepared:
            self.rows.setdefault(sym, []).append((ts, o, h, lo, c, v))
            self.last[sym] = c
            if ldcp and sym not in self.first_ldcp:
                self.first_ldcp[sym] = float(ldcp)
        self.days += 1

    def build(self, junction: dict[str, float] | None = None, junction_date: dt.date | None = None) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
        """``({PSX:SYM: bars}, [event records])``.

        ``junction`` (symbol -> LDCP on the first stored day) joins this block to history stored *after* it, which is how a
        backward extension finds a corporate action that fell exactly between the two.
        """
        frames: dict[str, pd.DataFrame] = {}
        records: list[dict[str, Any]] = []
        jev: dict[str, float] = {}
        if junction and junction_date is not None:
            tests = 0
            for sym, rows in self.rows.items():
                edge = junction.get(sym)
                if not edge or edge <= 0:
                    continue
                last_close = rows[-1][4]
                tests += 1
                f = edge / last_close
                if abs(f - 1.0) > self._tol(last_close) and 0.1 < f < 10.0:
                    jev[sym] = f
            if tests >= 20 and len(jev) / tests > EVENT_STORM_SHARE:
                self.notes.append(f"{junction_date.isoformat()}: {len(jev)} of {tests} symbols differed across the join (a missed day); no adjustment made")
                jev = {}
        for sym, rows in self.rows.items():
            df = bars_frame(rows, ["open", "high", "low", "close", "volume"])
            evs = list(self.events.get(sym, []))
            if sym in jev and junction_date is not None:
                evs.append((pd.Timestamp(junction_date), jev[sym]))
            for when, f in evs:
                records.append({"date": pd.Timestamp(when).strftime("%Y-%m-%d"), "symbol": sym, "factor": round(f, 5)})
            frames[make_id("psx", sym)] = apply_events(df, evs)  # type: ignore[assignment]
        return frames, records


def looks_unchanged(rows: list[dict[str, Any]], bars: dict[str, pd.DataFrame]) -> bool:
    """True when a market-watch snapshot just repeats the last stored session (a holiday): same close and volume for nearly everyone."""
    same = total = 0
    for r in rows:
        df = bars.get(make_id("psx", r["symbol"]))
        if df is None or len(df) == 0 or not r.get("volume"):
            continue
        total += 1
        last = df.iloc[-1]
        if abs(float(last["close"]) - (r.get("close") or 0.0)) < 1e-9 and abs(float(last["volume"]) - r["volume"]) < 1.0:
            same += 1
    return total >= 20 and same / total > 0.8


def _snapshot_unchanged(tv_rows: dict[str, dict[str, Any]], bars: dict[str, pd.DataFrame]) -> bool:
    """True when the TradingView snapshot shows the same closes as the last stored session (a holiday or a stale snapshot)."""
    same = total = 0
    for sym, r in tv_rows.items():
        df = bars.get(make_id("psx", sym))
        px = to_float(r.get("close"))
        if df is None or len(df) == 0 or px is None:
            continue
        total += 1
        if abs(float(df["close"].iloc[-1]) - px) < 1e-9:
            same += 1
    return total >= 20 and same / total > 0.8


def bootstrap_state(state: dict[str, Any], bars: dict[str, pd.DataFrame]) -> None:
    """Rebuild the date range from stored bars when the state file is missing (a store copied by hand, a lost file)."""
    stocks = [df for iid, df in bars.items() if len(df) and iid.split(":", 1)[1] not in INDEX_CODES]
    if state.get("last_date") or not stocks:
        return
    state["last_date"] = max(df.index[-1] for df in stocks).strftime("%Y-%m-%d")
    state["first_date"] = min(df.index[0] for df in stocks).strftime("%Y-%m-%d")


def _last_closes(bars: dict[str, pd.DataFrame], upto: pd.Timestamp | None = None) -> dict[str, float]:
    """Each symbol's last stored close, optionally only counting days up to ``upto`` (rows after the last day the portal
    confirmed can be approximate snapshots from another source and must not serve as the base for LDCP comparisons)."""
    out: dict[str, float] = {}
    for iid, df in bars.items():
        part = df if upto is None else df[df.index <= upto]
        if len(part):
            out[iid.split(":", 1)[1]] = float(part["close"].iloc[-1])
    return out


def _append(bars: dict[str, pd.DataFrame], frames: dict[str, pd.DataFrame], blk: Block) -> None:
    """Add a block that continues stored history: earlier stored bars get the block's corporate-action factors."""
    for iid, fr in frames.items():
        old = apply_events(bars.get(iid), blk.events.get(iid.split(":", 1)[1], []))
        bars[iid] = normalise_bars(pd.concat([old, fr])) if old is not None and len(old) else fr


# ---------------------------------------------------------------------------------------------- orchestration
def fetch_psx(
    cfg: Config,
    store: HistoryStore,
    http: Client,
    *,
    log: Callable[[str], None] = print,
    now: dt.datetime | None = None,
    dps: dps_mod.Dps | None = None,
) -> MarketData:
    t_run = time.time()
    now = now or dt.datetime.now(dt.timezone.utc)
    pcfg = cfg.get_path("data.psx", {}) or {}
    ucfg = cfg.get_path("universe.psx", {}) or {}
    max_requests = int(pcfg.get("max_requests_per_run", 450))
    ohlc_days = int(pcfg.get("ohlc_days", 400))
    target_years = float(pcfg.get("history_years", 5))
    keep = int(cfg.get_path("backtest.max_history_bars", 1500))
    md = MarketData("psx")
    state = store.load_json(STATE, {}) or {}
    nodes = store.load_json(NODES, {}) or {}
    bars = store.load_bars("psx")
    bootstrap_state(state, bars)
    statuses: list[SourceStatus] = []
    notes: list[str] = []
    pk = pkt_now(now)
    today = pk.date()
    target_first = today - dt.timedelta(days=int(target_years * 365.25))
    final_today = pk.weekday() < 5 and pk.hour >= SESSION_FINAL_HOUR_PKT
    dps = dps or dps_mod.Dps(http, interval=float(pcfg.get("min_interval_s", 1.2)), state=nodes, log=log)
    used = lambda: http.requests_to(dps_mod.HOST)  # noqa: E731

    universe: dict[str, dict[str, Any]] = {}
    watch: list[dict[str, Any]] = []
    dps_ok = False
    adjustments: list[dict[str, Any]] = list(state.get("adjustments", []))[-100:]
    n_adj_before = len(adjustments)

    # ---- portal: symbols and market watch
    t0 = time.time()
    try:
        if dps.ensure_node():
            syms = dps.symbols()
            watch = dps.market_watch()
            universe = {s["symbol"]: s for s in syms if not s["is_debt"] and "-" not in s["symbol"]}
            dps_ok = True
        else:
            statuses.append(status("psx_portal", False, "no node of the portal served data", started=t0, now=now))
    except FetchError as exc:
        statuses.append(status("psx_portal", False, str(exc), started=t0, now=now))
    if not dps_ok:
        log(f"  psx: portal unavailable: {statuses[-1].message}")

    # ---- TradingView: fundamentals, and the fallback universe and snapshot
    tv_rows: dict[str, dict[str, Any]] = {}
    t0 = time.time()
    try:
        tv_rows, dropped = tv.scan(http)
        statuses.append(status("psx_tradingview", True, f"{len(tv_rows)} symbols" + (f", {len(dropped)} column(s) unavailable" if dropped else ""), items=len(tv_rows), started=t0, now=now))
    except FetchError as exc:
        statuses.append(status("psx_tradingview", False, str(exc), started=t0, now=now))
        log(f"  psx: tradingview unavailable: {exc}")
    saved_universe = store.load_json(UNIVERSE, {}) or {}
    if dps_ok:
        store.save_json(UNIVERSE, {"saved_at": iso(now), "symbols": universe})
    else:
        # The saved list came from the portal (proper names, sectors, ETF flags). TradingView only adds symbols the copy
        # lacks, and its coarser answer is never saved over the portal's.
        universe = dict(saved_universe.get("symbols") or {})
        if universe:
            notes.append(f"symbol list from the copy saved on {str(saved_universe.get('saved_at', ''))[:10]}")
        for sym, r in tv_rows.items():
            if sym in universe or "-" in sym or r.get("type") not in (None, "stock", "fund", "dr"):
                continue
            universe[sym] = {"symbol": sym, "name": str(r.get("description") or sym), "sector": str(r.get("sector") or ""), "is_etf": r.get("type") == "fund", "is_debt": False}
    saved_fund = store.load_json(FUNDAMENTALS, {}) or {}
    if tv_rows:
        store.save_json(FUNDAMENTALS, {"saved_at": iso(now), "rows": {k: tv.fundamentals_from_row(v) for k, v in tv_rows.items()}})
    elif saved_fund.get("rows"):
        notes.append(f"fundamentals from the copy saved on {str(saved_fund.get('saved_at', ''))[:10]}")

    # ---- bars from the portal
    new_days = back_days = 0
    if dps_ok:
        t0 = time.time()
        allowed = set(universe)
        empty = {dt.date.fromisoformat(x) for x in state.get("empty", [])}
        try:
            # (1) forward: the days after the last stored one
            last_date = dt.date.fromisoformat(state["last_date"]) if state.get("last_date") else None
            if last_date is not None and bars:
                days = [last_date + dt.timedelta(days=k) for k in range(1, (today - last_date).days + 1)]
                days = [d for d in days if d.weekday() < 5 and d not in empty and (d < today or final_today)]
                base = _last_closes(bars, pd.Timestamp(last_date))
                blk = Block(allowed | set(base), base)
                got: list[dt.date] = []
                pending: list[dt.date] = []
                for d in days:
                    if used() >= max_requests:
                        break
                    rows = dps.day(d)
                    if not rows:
                        pending.append(d)
                        continue
                    empty.update(pending)  # blanks followed by real data were holidays
                    pending = []
                    blk.add_day(d, rows)
                    got.append(d)
                if got:
                    frames, recs = blk.build()
                    _append(bars, frames, blk)
                    adjustments += recs
                    notes += blk.notes
                    state["last_date"] = max(got).isoformat()
                    new_days = len(got)
                    state.setdefault("first_date", min(got).isoformat())
            # (2) today's numbers from the market watch when the portal's day table is not out yet
            ld = dt.date.fromisoformat(state["last_date"]) if state.get("last_date") else None
            if watch and final_today and bars and ld is not None and ld < today and not looks_unchanged(watch, bars):
                base = _last_closes(bars, pd.Timestamp(ld))
                blk = Block(allowed | set(base), base)
                blk.add_day(today, watch)
                frames, recs = blk.build()
                _append(bars, frames, blk)
                adjustments += recs
                state["last_date"] = today.isoformat()
                new_days += 1
            # (3) backward: the first run, and every run after it until the target history is reached
            first_date = dt.date.fromisoformat(state["first_date"]) if state.get("first_date") else None
            need_back = not state.get("reached_start") and (first_date is None or first_date > target_first + dt.timedelta(days=HISTORY_SLACK_DAYS))
            if need_back and max_requests - used() > 5:
                d = (today if final_today else today - dt.timedelta(days=1)) if first_date is None else first_date - dt.timedelta(days=1)
                got_back: list[tuple[dt.date, list[dict[str, Any]]]] = []
                pending = []
                streak = 0
                while used() < max_requests and d >= target_first:
                    if d.weekday() >= 5 or d in empty:
                        d -= dt.timedelta(days=1)
                        continue
                    rows = dps.day(d)
                    if rows:
                        if got_back or first_date is not None:
                            empty.update(pending)  # data on both sides: those blanks were holidays
                        # (blanks newer than every data day may only mean "not published yet": never remembered)
                        pending, streak = [], 0
                        got_back.append((d, rows))
                    else:
                        pending.append(d)
                        streak += 1
                        if streak >= BLANK_STREAK_STOP:
                            # a genuine start of history is only believed close to the portal's documented five-year reach
                            if d <= today - dt.timedelta(days=int(4.5 * 365)) and (got_back or first_date is not None):
                                state["reached_start"] = True
                            break
                    d -= dt.timedelta(days=1)
                if got_back:
                    got_back.sort(key=lambda x: x[0])
                    blk = Block(allowed | {r["symbol"] for _, rows in got_back for r in rows if r["symbol"] in universe})
                    for day, rows in got_back:
                        blk.add_day(day, rows)
                    frames, recs = blk.build(junction=(state.get("edge_ldcp") or None) if first_date is not None else None, junction_date=first_date)
                    for iid, fr in frames.items():
                        old = bars.get(iid)
                        bars[iid] = normalise_bars(pd.concat([old, fr])) if old is not None and len(old) else fr  # on a shared day the portal's row wins
                    adjustments += recs
                    notes += blk.notes
                    edge = dict(state.get("edge_ldcp") or {}) if first_date is not None else {}
                    edge.update(blk.first_ldcp)
                    state["edge_ldcp"] = edge
                    state["first_date"] = min(x[0] for x in got_back).isoformat()
                    state.setdefault("last_date", max(x[0] for x in got_back).isoformat())
                    back_days = len(got_back)
            horizon = today - dt.timedelta(days=int(target_years * 365.25) + 30)
            state["empty"] = sorted(x.isoformat() for x in empty if x >= horizon)
            parts = [f"{len(universe)} symbols", f"{new_days} new day(s)"]
            if back_days:
                parts.append(f"{back_days} older day(s)")
            if len(adjustments) > n_adj_before:
                parts.append(f"{len(adjustments) - n_adj_before} price adjustment(s) for corporate actions")
            statuses.append(status("psx_portal", True, ", ".join(parts), items=len(universe), started=t0, now=now))
        except FetchError as exc:
            statuses.append(status("psx_portal", False, f"history update stopped: {exc}", items=len(universe), started=t0, now=now))
            log(f"  psx: portal history update stopped: {exc}")

    # ---- the benchmark indices (portal first, Yahoo second)
    index_ids: dict[str, str] = {}
    for code in INDEX_CODES:
        iid = make_id("psx", code)
        df = None
        if dps_ok and not http.is_open(dps_mod.HOST):
            try:
                df = dps.eod(code)
            except FetchError as exc:
                notes.append(f"{code} from the portal: {exc}")
        if (df is None or len(df) < 30) and code in YAHOO_INDEX and not http.is_open(yahoo.HOSTS[0]):
            try:
                df, _ = yahoo.chart(http, YAHOO_INDEX[code], range_="5y")
            except FetchError as exc:
                notes.append(f"{code} from Yahoo: {exc}")
        if df is not None and len(df):
            cur = {iid: bars[iid]} if iid in bars else {}
            store.merge_bars(cur, {iid: df}, overwrite_days=5)
            bars[iid] = cur[iid]
            index_ids[code] = iid
        elif iid in bars and len(bars[iid]):
            index_ids[code] = iid  # keep the benchmark from the stored copy rather than lose relative strength for a day
            notes.append(f"{code} could not be refreshed; using the stored history")

    # ---- history for stocks that have too little (portal down, or newly listed once the portal's back-fill is complete)
    min_bars = int(ucfg.get("min_bars", 120))
    first_stored = dt.date.fromisoformat(state["first_date"]) if state.get("first_date") else None
    backfill_done = bool(state.get("reached_start")) or (first_stored is not None and first_stored <= target_first + dt.timedelta(days=HISTORY_SLACK_DAYS))
    if universe and (not dps_ok or backfill_done) and not http.is_open(yahoo.HOSTS[0]):
        thin = [s for s in universe if len(bars.get(make_id("psx", s), ())) < min_bars]
        if not dps_ok:
            thin.sort(key=lambda s: -(to_float((tv_rows.get(s) or {}).get("market_cap_basic")) or 0.0))
            budget = int(pcfg.get("yahoo_fallback_max", 120))
        else:
            budget = min(20, int(pcfg.get("yahoo_fallback_max", 120)))
        t0 = time.time()
        got_y = 0
        for sym in thin[:budget]:
            try:
                df, _ = yahoo.chart(http, f"{sym}.KA", range_="5y")
            except FetchError:
                if http.is_open(yahoo.HOSTS[0]):
                    break
                continue
            iid = make_id("psx", sym)
            merged, why = splice(bars.get(iid), df)
            if why:
                continue
            bars[iid] = merged  # type: ignore[assignment]
            got_y += 1
        if thin and not dps_ok:
            statuses.append(status("psx_yahoo", got_y > 0, f"history for {got_y} of {len(thin)} symbols with too little", items=got_y, started=t0, now=now))

    # ---- portal down: add today's close from the TradingView snapshot (high and low approximated)
    if not dps_ok and tv_rows and final_today and not _snapshot_unchanged(tv_rows, bars):
        today_ts = pd.Timestamp(today)
        added = 0
        for sym in universe:
            q = tv.quote_from_row(tv_rows.get(sym) or {}, iso(now))
            iid = make_id("psx", sym)
            df = bars.get(iid)
            if q and df is not None and len(df) and df.index[-1] < today_ts and (q.get("volume") or 0) > 0:
                prev = float(df["close"].iloc[-1])
                row = pd.DataFrame({"open": [prev], "high": [max(prev, q["price"])], "low": [min(prev, q["price"])], "close": [q["price"]], "volume": [q["volume"]], "approx_hl": [True]},
                                   index=pd.DatetimeIndex([today_ts], name="date"))
                bars[iid] = normalise_bars(pd.concat([df, row]))
                added += 1
        if added:
            notes.append(f"{added} stocks got today's close from the TradingView snapshot (high and low approximated)")

    # ---- instruments, quotes, fundamentals
    watch_by = {r["symbol"]: r for r in watch}
    for sym, s in universe.items():
        iid = make_id("psx", sym)
        df = bars.get(iid)
        if df is None or len(df) == 0:
            continue
        w = watch_by.get(sym, {})
        md.instruments[iid] = Instrument(iid, "psx", sym, s["name"], title_sector(s["sector"]) or None, "etf" if s["is_etf"] else "equity", True,
                                         {"listed_in": w.get("listed", []), "sector_code": w.get("sector")})
        md.bars[iid] = df
        traded = (w.get("close") or 0) > 0
        if w and (traded or w.get("ldcp")):
            md.quotes[iid] = {"price": w["close"] if traded else w["ldcp"], "prev_close": w.get("ldcp"), "chg_pct": w.get("pct") if traded else 0.0, "volume": w.get("volume") or 0.0, "ts": iso(now)}
        elif sym in tv_rows:
            q = tv.quote_from_row(tv_rows[sym], iso(now))
            if q:
                md.quotes[iid] = q
        f = tv.fundamentals_from_row(tv_rows[sym]) if sym in tv_rows else (saved_fund.get("rows") or {}).get(sym)
        if f:
            f = dict(f)
            if f.get("mcap") is None and f.get("shares"):
                f["mcap"] = f["shares"] * float(df["close"].iloc[-1])
            md.fundamentals[iid] = f
    for code, iid in index_ids.items():
        md.instruments[iid] = Instrument(iid, "psx", code, INDEX_CODES[code], None, "index", False, {})
        md.bars[iid] = bars[iid]
    if "KSE100" in index_ids:
        md.extras["benchmark"] = {"symbol": "KSE100", "name": INDEX_CODES["KSE100"]}

    # ---- history progress and persistence
    dates: set = set()
    for iid, df in md.bars.items():
        if iid.split(":", 1)[1] not in INDEX_CODES:
            dates.update(df.index[-ohlc_days:])
    if state.get("reached_start") or len(dates) >= ohlc_days:
        pct = 100
    else:
        pct = int(round(100 * len(dates) / max(ohlc_days, 1)))
    md.extras["history_pct"] = pct
    state["adjustments"] = adjustments[-100:]
    store.save_json(STATE, state)
    store.save_json(NODES, nodes)
    if bars:
        today_ts = pd.Timestamp(today)
        keep_ids = [i for i, df in bars.items() if len(df) and ((today_ts - df.index[-1]).days <= 45 or i.split(":", 1)[1] in INDEX_CODES)]
        store.save_bars("psx", {i: bars[i] for i in keep_ids}, keep_days=keep)
    for n in notes:
        log(f"  psx: {n}")
    md.status = statuses
    md.extras["notes"] = notes
    log(f"  psx: {len(md.instruments)} instruments, history {pct}%, {used()} portal requests, {round(time.time() - t_run)}s")
    return md
