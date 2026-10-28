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

A *big* factor (a 10-for-1 split is 0.1, a consolidation is 10 or more) has to be corroborated: the day's own close must
sit near the adjusted ``LDCP``, otherwise a bad row would rescale a whole history by ten. Stored history that still holds
such a change of units (found by an earlier version of this rule that stopped just short of 0.1) is repaired on every run
by :func:`heal_splits`: a one-day move no market can make, that stays, is a split the portal's row did not show.

Joining blocks of days
----------------------
Stored history is always expressed in today's prices: every event after a bar has already scaled it. A block of older days
arrives in the prices of its own last day, so before it is joined it has to be scaled by everything that happened *after*
it too. That factor is read off the first stored bar of each symbol: the stored (adjusted) close over the raw close the
portal gave for that day, which is remembered in ``state["edges"]``. The block is also checked for an event that fell
exactly between its last day and that first stored day (the portal's ``LDCP`` of that day against the block's last close).
Symbols whose first stored bar cannot be matched to a remembered raw bar (a store from before this rule, history from
another source) get no older days: joining them unscaled would leave a cliff at the seam.

``HISTORY_VERSION`` marks stores built with this rule. A store without it was joined without the carry, so its older
blocks sit at the wrong scale: the first run that can reach the portal rebuilds the stock history from nothing (and
keeps the old bars if the portal hands over nothing).

Holidays
--------
A day that returns no rows is only remembered as a holiday once a later day returns rows, so an outage or throttling
(which also returns nothing) never leaves a permanent hole in the history.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..config import Config
from ..model import Instrument, MarketData, SourceStatus, make_id, normalise_bars
from ..store import HistoryStore
from . import psx_dps as dps_mod
from . import psx_tv as tv
from . import yahoo
from .common import bars_frame, iso, pkt_now, splice, status, to_float
from .http import Client, FetchError

HISTORY_VERSION = 2  # 2: older blocks of days carry the corporate actions that followed them (see "Joining blocks of days")
REBUILD_KEYS = ("first_date", "last_date", "reached_start", "edges", "edge_ldcp")  # the state that describes the stored stock history
REBUILD_MIN_DAYS = 250  # a rebuild replaces the old bars only once it has brought this many trading days (or the whole target)
STATE = "psx_state"
NODES = "psx_nodes"
UNIVERSE = "psx_universe"
FUNDAMENTALS = "psx_fundamentals"
SESSION_FINAL_HOUR_PKT = 17  # the regular market is over (Friday sessions end late), so a day's numbers are final
EVENT_STORM_SHARE = 0.25
BONUS_VOLUME_BELOW = 0.92  # a price factor below this looks like a bonus issue or split: share volume rises by 1/f; smaller ones are dividends
CONSOLIDATION_VOLUME_ABOVE = 1.5  # a factor above this is a consolidation (fewer, dearer shares): share volume falls by 1/f
FACTOR_RANGE = (0.01, 100.0)  # a factor outside this is a data error; the ends are inclusive because a 10-for-1 split is exactly 0.1
BIG_FACTOR = 2.0  # below 1/2 or above 2 the day's own close must agree with the adjusted LDCP before the event is believed
CLOSE_NEAR_LDCP = (0.6, 1 / 0.6)  # ... within this band (price limits keep a real session far inside it)
SHIFT_RATIO = (0.2, 5.0)  # a close-to-close ratio beyond this cannot be trading: the price changed units
SHIFT_CONFIRM_BARS = 3  # the new level must hold for this many bars before the history is rescaled
ROUND_FACTORS = tuple(sorted({1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 1 / 50, 1 / 100} | {2.0, 3.0, 4.0, 5.0, 10.0, 20.0, 50.0, 100.0}))  # the ratios companies actually use
ROUND_FACTOR_SLACK = 1.25  # the day's own move rides on top of the split ratio: snap to a round ratio within this much
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
def volume_divisor(f: float) -> float:
    """What share volume is divided by when prices are scaled by ``f``: splits, bonus issues and consolidations change the
    number of shares (so volume goes the other way), a dividend only changes the price."""
    return f if (f < BONUS_VOLUME_BELOW or f > CONSOLIDATION_VOLUME_ABOVE) else 1.0


def apply_events(df: pd.DataFrame | None, events: list[tuple]) -> pd.DataFrame | None:
    """Scale every bar dated before each event ``(day, price factor[, volume divisor])``.

    Without an explicit divisor the volume follows :func:`volume_divisor`. A carried factor (the product of many events) has
    to say what its volume divisor is: ten dividends of 3% make 0.74, but they did not change a single share.
    """
    if df is None or len(df) == 0 or not events:
        return df
    out = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = out[col].astype("float64")
    for ev in events:
        when, f = ev[0], ev[1]
        vdiv = ev[2] if len(ev) > 2 else volume_divisor(f)
        mask = out.index < pd.Timestamp(when)
        if not mask.any():
            continue
        out.loc[mask, ["open", "high", "low", "close"]] = out.loc[mask, ["open", "high", "low", "close"]] * f
        if vdiv != 1.0:
            out.loc[mask, "volume"] = out.loc[mask, "volume"] / vdiv
    return out


def _tolerance(prev: float) -> float:
    """How far LDCP may sit from the previous close and still be rounding (the portal quotes two decimals)."""
    return max(0.004, 0.0075 / max(prev, 1e-9))


def corporate_factor(prev: float, ldcp: float | None, close: float | None) -> float | None:
    """The price factor of a corporate action implied by a day's LDCP against the previous stored close, or ``None``.

    Rounding never counts. A big factor (a split or a consolidation) is only believed when the day's own close sits near
    the adjusted LDCP, because one bad row is far likelier than a real 10-for-1 split and would rescale a whole history.
    """
    if ldcp is None or ldcp <= 0 or prev <= 0:
        return None
    f = ldcp / prev
    if abs(f - 1.0) <= _tolerance(prev) or not (FACTOR_RANGE[0] <= f <= FACTOR_RANGE[1]):
        return None
    if f < 1.0 / BIG_FACTOR or f > BIG_FACTOR:
        if close is None or close <= 0 or not (CLOSE_NEAR_LDCP[0] <= close / ldcp <= CLOSE_NEAR_LDCP[1]):
            return None
    return f


def snap_factor(ratio: float) -> float:
    """The round split ratio (1/10, 1/20, 20 ...) nearest to an observed one-day ratio, or the ratio itself when none is near."""
    best = min(ROUND_FACTORS, key=lambda n: abs(math.log(ratio / n)))
    return best if abs(math.log(ratio / best)) <= math.log(ROUND_FACTOR_SLACK) else ratio


def find_level_shifts(close: pd.Series) -> list[tuple[pd.Timestamp, float]]:
    """Days on which a price changed units for good: ``[(first day at the new level, factor for the older bars)]``.

    A close-to-close move of -80% or +400% cannot be trading (daily price limits are a few percent), so it is a split or a
    consolidation. It is only believed when the new level then holds (a bad print comes straight back), and only once
    :data:`SHIFT_CONFIRM_BARS` bars are there to show it: a last bar that looks like a split is left for the next run.
    """
    c = close.to_numpy(dtype="float64")
    if len(c) < SHIFT_CONFIRM_BARS + 2:
        return []
    ratio = c[1:] / c[:-1]
    found: list[tuple[pd.Timestamp, float]] = []
    for i in np.flatnonzero((ratio <= SHIFT_RATIO[0]) | (ratio >= SHIFT_RATIO[1])):
        if len(c) - (i + 1) < SHIFT_CONFIRM_BARS:
            continue
        before = float(np.median(c[max(0, i - 4): i + 1]))
        after = float(np.median(c[i + 1: i + 6]))
        if before <= 0 or not (0.6 <= (after / before) / ratio[i] <= 1 / 0.6):
            continue
        found.append((close.index[i + 1], snap_factor(float(ratio[i]))))
    return found


def heal_splits(bars: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """Rescale, in place, the history before every unadjusted change of units in the stored stock bars; one record per repair.

    Nothing is left to do once the history is consistent, so running it every time is harmless.
    """
    records: list[dict[str, Any]] = []
    for iid in list(bars):
        df = bars[iid]
        code = iid.split(":", 1)[1]
        if df is None or code in INDEX_CODES or len(df) < SHIFT_CONFIRM_BARS + 2:
            continue
        shifts = find_level_shifts(df["close"])
        if not shifts:
            continue
        bars[iid] = apply_events(df, shifts)  # type: ignore[assignment]
        records += [{"date": when.strftime("%Y-%m-%d"), "symbol": code, "factor": round(f, 5), "inferred": True} for when, f in shifts]
    return records


class Block:
    """A run of consecutive trading days turned into per-symbol bars, with corporate actions detected from LDCP.

    ``last_close`` holds each symbol's last stored close when the block continues stored history (forward update);
    leave it empty for a block that starts a history or extends it backwards.

    ``edge`` keeps, per symbol, the raw facts of its first bar in the block (day, close, volume, and the portal's LDCP of
    that day). They never change, and they are what a block of still older days is later joined to (:func:`carry_factors`).
    """

    def __init__(self, allowed: set[str], last_close: dict[str, float] | None = None):
        self.allowed = allowed
        self.last: dict[str, float] = dict(last_close or {})
        self.rows: dict[str, list[tuple]] = {}
        self.edge: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[tuple[pd.Timestamp, float]]] = {}
        self.notes: list[str] = []
        self.days = 0

    _tol = staticmethod(_tolerance)

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
        for sym, ldcp, _o, _h, _lo, c, _v in prepared:
            prev = self.last.get(sym)
            if prev is None:
                continue
            with_prev += 1
            f = corporate_factor(prev, ldcp, c)
            if f is not None:
                facs[sym] = f
        if with_prev >= 20 and len(facs) / with_prev > EVENT_STORM_SHARE:
            self.notes.append(f"{day.isoformat()}: {len(facs)} of {with_prev} symbols differed from the previous close (a missed day, not corporate actions); no adjustment made")
            facs = {}
        for sym, f in facs.items():
            self.events.setdefault(sym, []).append((ts, f))
        for sym, ldcp, o, h, lo, c, v in prepared:
            self.rows.setdefault(sym, []).append((ts, o, h, lo, c, v))
            self.last[sym] = c
            if sym not in self.edge:
                self.edge[sym] = {"day": day.isoformat(), "close": c, "volume": float(v), "ldcp": float(ldcp) if ldcp and ldcp > 0 else None}
        self.days += 1

    def build(
        self,
        edges: dict[str, dict[str, Any]] | None = None,
        junction_date: dt.date | None = None,
        carry: dict[str, tuple[float, float]] | None = None,
    ) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
        """``({PSX:SYM: bars}, [event records])``.

        A block that extends stored history backwards is joined to it with two things. ``edges`` (symbol -> the
        :attr:`edge` facts of the first stored bar) finds a corporate action that fell exactly between the two: the portal's
        LDCP of that first day against this block's last close, and a big factor (a split) has to be backed by that day's own
        raw close. ``carry`` (symbol -> ``(price factor, volume divisor)``, see :func:`carry_factors`) brings the block into the
        prices of the stored history, which every later event has already scaled. ``junction_date`` is any day after
        the block that is not after the first stored bar.
        """
        frames: dict[str, pd.DataFrame] = {}
        records: list[dict[str, Any]] = []
        jev: dict[str, float] = {}
        if edges and junction_date is not None:
            tests = 0
            for sym, rows in self.rows.items():
                e = edges.get(sym)
                if not e or not e.get("ldcp") or e["ldcp"] <= 0:
                    continue
                tests += 1
                f = corporate_factor(rows[-1][4], e["ldcp"], e.get("close"))
                if f is not None:
                    jev[sym] = f
            if tests >= 20 and len(jev) / tests > EVENT_STORM_SHARE:
                self.notes.append(f"{junction_date.isoformat()}: {len(jev)} of {tests} symbols differed across the join (a missed day); no adjustment made")
                jev = {}
        for sym, rows in self.rows.items():
            df = bars_frame(rows, ["open", "high", "low", "close", "volume"])
            evs: list[tuple] = list(self.events.get(sym, []))
            for when, f in evs:
                records.append({"date": pd.Timestamp(when).strftime("%Y-%m-%d"), "symbol": sym, "factor": round(f, 5)})
            if junction_date is not None:
                pf, vd = (carry or {}).get(sym, (1.0, 1.0))
                j = jev.get(sym)
                if j is not None:
                    records.append({"date": junction_date.isoformat(), "symbol": sym, "factor": round(j, 5)})
                    pf, vd = pf * j, vd * volume_divisor(j)
                if pf != 1.0 or vd != 1.0:
                    evs.append((pd.Timestamp(junction_date), pf, vd))
            frames[make_id("psx", sym)] = apply_events(df, evs)  # type: ignore[assignment]
        return frames, records


def carry_factors(bars: dict[str, pd.DataFrame], edges: dict[str, dict[str, Any]]) -> dict[str, tuple[float, float]]:
    """``{symbol: (price factor, volume divisor)}`` that bring raw prices from the day of a symbol's first stored bar back into
    the stored (adjusted) prices: the stored close over the raw close the portal gave that day, and the same for volume.

    A symbol only gets an entry when its first stored bar is the very bar remembered in ``edges``; a store from before the
    rule, history that another source extended or that was trimmed, cannot be matched, and joining it would leave a cliff.
    """
    out: dict[str, tuple[float, float]] = {}
    for iid, df in bars.items():
        code = iid.split(":", 1)[1]
        e = edges.get(code)
        if not e or df is None or len(df) == 0 or code in INDEX_CODES:
            continue
        if df.index[0].strftime("%Y-%m-%d") != e.get("day"):
            continue
        raw, stored = to_float(e.get("close")), float(df["close"].iloc[0])
        if raw is None or raw <= 0 or not math.isfinite(stored) or stored <= 0:
            continue
        pf = stored / raw
        if not (1e-3 <= pf <= 1e3):
            continue
        raw_v, stored_v = to_float(e.get("volume")) or 0.0, float(df["volume"].iloc[0])
        out[code] = (pf, raw_v / stored_v if raw_v > 0 and stored_v > 0 else volume_divisor(pf))
    return out


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


def _append(bars: dict[str, pd.DataFrame], frames: dict[str, pd.DataFrame], blk: Block, edges: dict[str, dict[str, Any]]) -> None:
    """Add a block that continues stored history: earlier stored bars get the block's corporate-action factors.

    A symbol this block brings in for the first time starts its history here, so its first bar is remembered as its edge.
    """
    for iid, fr in frames.items():
        code = iid.split(":", 1)[1]
        old = apply_events(bars.get(iid), blk.events.get(code, []))
        if old is not None and len(old):
            bars[iid] = normalise_bars(pd.concat([old, fr]))
        else:
            bars[iid] = fr
            if code in blk.edge:
                edges[code] = blk.edge[code]


def audit_jumps(bars: dict[str, pd.DataFrame], limit: float = 0.5) -> list[tuple[str, str, float]]:
    """``[(symbol, day, ratio)]``: the largest one-day close-to-close change of each stock that moved more than ``limit``.

    No exchange lets a share move like that in a session, so each hit is a change of units or a bad row the adjustments did
    not catch. Reported in the log of every run so that a repair that does not hold is seen, not discovered in a chart.
    """
    out: list[tuple[str, str, float]] = []
    for iid, df in bars.items():
        code = iid.split(":", 1)[1]
        if df is None or len(df) < 2 or code in INDEX_CODES:
            continue
        r = df["close"].to_numpy(dtype="float64")
        ratio = r[1:] / r[:-1]
        move = np.abs(ratio - 1.0)
        i = int(np.nanargmax(move)) if np.isfinite(move).any() else -1
        if i >= 0 and move[i] > limit:
            out.append((code, df.index[i + 1].strftime("%Y-%m-%d"), float(ratio[i])))
    return sorted(out, key=lambda x: -abs(math.log(max(x[2], 1e-9))))


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
            statuses.append(status("psx_portal", False, "no node of the portal served data" + (f" ({dps.token_note})" if dps.token_note else ""), started=t0, now=now))
    except FetchError as exc:
        statuses.append(status("psx_portal", False, str(exc) + (f" ({dps.token_note})" if dps.token_note else ""), started=t0, now=now))
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

    # ---- a store built before the seam rule is rebuilt from nothing; the old bars wait aside until new days have really arrived
    stash: dict[str, Any] | None = None
    if dps_ok and state.get("history_version") != HISTORY_VERSION:
        old_ids = [iid for iid in bars if iid.split(":", 1)[1] not in INDEX_CODES]
        if old_ids:
            stash = {"bars": {iid: bars.pop(iid) for iid in old_ids}, "state": {k: state.pop(k) for k in REBUILD_KEYS if k in state}, "adjustments": adjustments}
            adjustments, n_adj_before = [], 0
    edges: dict[str, dict[str, Any]] = dict(state.get("edges") or {})

    # ---- stored history that still holds a split the day-by-day rule missed (see heal_splits)
    healed = heal_splits(bars)
    if healed:
        adjustments += healed
        n_adj_before = len(adjustments)  # reported on their own line, not as part of today's portal update
        notes.append(f"{len(healed)} stock split(s) were still unadjusted in the stored history and were repaired: " + ", ".join(f"{h['symbol']} ({h['date']}, x{h['factor']:g})" for h in healed[:8]) + (" ..." if len(healed) > 8 else ""))

    # ---- bars from the portal
    new_days = back_days = 0
    if dps_ok:
        t0 = time.time()
        allowed = set(universe)
        empty = {dt.date.fromisoformat(x) for x in state.get("empty", [])}
        stopped: FetchError | None = None  # the portal gave up part-way: the days it did give are still kept
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
                    try:
                        rows = dps.day(d)
                    except FetchError as exc:
                        stopped = exc
                        break
                    if not rows:
                        pending.append(d)
                        continue
                    empty.update(pending)  # blanks followed by real data were holidays
                    pending = []
                    blk.add_day(d, rows)
                    got.append(d)
                if got:
                    frames, recs = blk.build()
                    _append(bars, frames, blk, edges)
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
                _append(bars, frames, blk, edges)
                adjustments += recs
                state["last_date"] = today.isoformat()
                new_days += 1
            # (3) backward: the first run, and every run after it until the target history is reached
            first_date = dt.date.fromisoformat(state["first_date"]) if state.get("first_date") else None
            need_back = not state.get("reached_start") and (first_date is None or first_date > target_first + dt.timedelta(days=HISTORY_SLACK_DAYS))
            if need_back and stopped is None and max_requests - used() > 5:
                d = (today if final_today else today - dt.timedelta(days=1)) if first_date is None else first_date - dt.timedelta(days=1)
                got_back: list[tuple[dt.date, list[dict[str, Any]]]] = []
                pending = []
                streak = 0
                while used() < max_requests and d >= target_first:
                    if d.weekday() >= 5 or d in empty:
                        d -= dt.timedelta(days=1)
                        continue
                    try:
                        rows = dps.day(d)
                    except FetchError as exc:
                        stopped = exc
                        break
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
                    join = first_date is not None
                    carry = carry_factors(bars, edges) if join else {}
                    frames, recs = blk.build(edges=edges if join else None, junction_date=first_date, carry=carry)
                    unmatched = 0
                    for iid, fr in frames.items():
                        code = iid.split(":", 1)[1]
                        old = bars.get(iid)
                        if old is not None and len(old):
                            if code not in carry:
                                unmatched += 1  # no way to put these days at the scale of the stored ones
                                continue
                            bars[iid] = normalise_bars(pd.concat([old, fr]))  # on a shared day the portal's row wins
                        else:
                            bars[iid] = fr
                        edges[code] = blk.edge[code]
                    adjustments += recs
                    notes += blk.notes
                    if unmatched:
                        notes.append(f"{unmatched} stock(s) got no older days: their first stored bar does not match the portal's, so the days could not be scaled to it")
                    state["first_date"] = min(x[0] for x in got_back).isoformat()
                    state.setdefault("last_date", max(x[0] for x in got_back).isoformat())
                    back_days = len(got_back)
        except FetchError as exc:  # the portal's list or a page was unusable for the whole step
            stopped = stopped or exc
        horizon = today - dt.timedelta(days=int(target_years * 365.25) + 30)
        state["empty"] = sorted(x.isoformat() for x in empty if x >= horizon)
        parts = [f"{len(universe)} symbols", f"{new_days} new day(s)"]
        if back_days:
            parts.append(f"{back_days} older day(s)")
        if len(adjustments) > n_adj_before:
            parts.append(f"{len(adjustments) - n_adj_before} price adjustment(s) for corporate actions")
        if stopped is not None:
            statuses.append(status("psx_portal", False, f"history update stopped: {stopped}" + (f" (kept {new_days + back_days} day(s) fetched before it)" if new_days + back_days else ""), items=len(universe), started=t0, now=now))
            log(f"  psx: portal history update stopped: {stopped}")
        else:
            statuses.append(status("psx_portal", True, ", ".join(parts), items=len(universe), started=t0, now=now))

    # ---- a rebuild replaces the old bars once it has really brought days (or the whole target); until then they come back untouched
    if dps_ok and stash is None:
        state["history_version"] = HISTORY_VERSION
    if stash is not None:
        first_now = dt.date.fromisoformat(state["first_date"]) if state.get("first_date") else None
        reached = bool(state.get("reached_start")) or (first_now is not None and first_now <= target_first + dt.timedelta(days=HISTORY_SLACK_DAYS))
        if back_days >= REBUILD_MIN_DAYS or (back_days > 0 and reached):
            state["history_version"] = HISTORY_VERSION
            notes.append(f"price history of {len(stash['bars'])} stocks is being rebuilt from the portal, {back_days} trading days so far, because older days had been joined without the splits and dividends that came after them")
        else:
            for iid in [i for i in bars if i.split(":", 1)[1] not in INDEX_CODES]:
                del bars[iid]
            bars.update(stash["bars"])
            state.update(stash["state"])
            adjustments = stash["adjustments"]
            n_adj_before = len(adjustments)
            edges = dict(state.get("edges") or {})
            notes.append("the stored price history needs rebuilding, but the portal gave too few days this time; it was left as it is until the next run")
        stash = None

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
    state.pop("edge_ldcp", None)  # the old, partial form of what "edges" now holds
    live = {iid.split(":", 1)[1] for iid in bars}
    state["edges"] = {sym: e for sym, e in edges.items() if sym in live}
    store.save_json(STATE, state)
    store.save_json(NODES, nodes)
    if bars:
        today_ts = pd.Timestamp(today)
        keep_ids = [i for i, df in bars.items() if len(df) and ((today_ts - df.index[-1]).days <= 45 or i.split(":", 1)[1] in INDEX_CODES)]
        store.save_bars("psx", {i: bars[i] for i in keep_ids}, keep_days=keep)
    for n in notes:
        log(f"  psx: {n}")
    jumps = audit_jumps(bars)
    if jumps:  # log only: a note here would reach the data-health card, and these are for the person maintaining the pipeline
        log(f"  psx: {len(jumps)} stock(s) still have a one-day close-to-close move of more than 50% in their history: " + ", ".join(f"{s} {d} x{r:.3g}" for s, d, r in jumps[:12]) + (" ..." if len(jumps) > 12 else ""))
    md.status = statuses
    md.extras["notes"] = notes
    log(f"  psx: {len(md.instruments)} instruments, history {pct}%, {used()} portal requests, {round(time.time() - t_run)}s")
    return md
