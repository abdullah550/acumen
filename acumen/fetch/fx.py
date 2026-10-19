"""Forex, gold and silver, and the Pakistani rupee: daily bars for about sixty instruments.

Sources (``data.fx.sources``, tried in this order, each only for what the previous ones could not deliver)
------------------------------------------------------------------------------------------------------
``yahoo``
    One request per pair: true open/high/low/close for five years. Gold, silver and USD/PKR come from here too. It is an
    unofficial endpoint that sometimes refuses shared cloud addresses.
``frankfurter``
    European Central Bank reference rates: one fixing a day, so the bars are close-only (flagged ``approx_hl``). Every
    currency against the dollar in a handful of requests, so the whole G8 and most exotics, but no metals and no rupee.
``fawaz``
    A daily snapshot of (almost) every currency, gold, silver and the rupee, one request per date. Can only bridge a short gap.

Bars from the last two are provisional: when Yahoo is reachable again it replaces them (``fallback_from`` in the saved state).

Pairs are built from the market convention for the G8 (EUR > GBP > AUD > NZD > USD > CAD > CHF > JPY: the earlier currency is the
base), exotics are quoted per US dollar, and the rupee crosses (EUR/PKR, AED/PKR ...) are *synthetic*: the dollar rate times the
other currency's dollar rate (the dirham and the riyal are pegged to the dollar, so their rates are constants). Synthetic pairs are
shown for information and are flagged as not offered by most retail brokers.

A day's bar is only kept once it cannot change any more: the calendar day (UTC) before today, or today itself on a Friday after the
New York close.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from ..config import Config
from ..model import Instrument, MarketData, make_id, normalise_bars
from ..store import HistoryStore
from . import yahoo as yahoo_mod
from .common import bars_frame, clean_bars, epoch_to_date, iso, positive, splice, status, to_float, utc_now
from .http import Client, FetchError, ParseError

STATE = "fx_state"
COLS = ["open", "high", "low", "close", "volume"]
DEFAULT_SOURCES = ["yahoo", "frankfurter", "fawaz"]

ORDER = ("EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY")  # market convention: an earlier currency is the base
EXOTICS = ("TRY", "ZAR", "MXN", "SGD", "NOK", "SEK", "PLN", "HUF", "CZK", "CNH", "INR")
PKR_CROSSES = ("EUR", "GBP", "AED", "SAR", "CNH", "JPY", "CHF", "AUD", "CAD")
PEGS = {"AED": 3.6725, "SAR": 3.75}  # units per US dollar, fixed by the central banks
METALS = {"XAU": ("Gold", "GC=F"), "XAG": ("Silver", "SI=F")}  # name, a futures symbol Yahoo can serve if the spot symbol is unknown
FRANKFURTER = {"EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "TRY", "ZAR", "MXN", "SGD", "NOK", "SEK", "PLN", "HUF", "CZK", "INR"}  # CNH is not the ECB's CNY
LEG_OK = FRANKFURTER | {"USD"}

# What a single bar can plausibly do, by group: a close that jumps this far and straight back is a bad tick, a wick this long never traded.
SPIKE = {"major": 0.08, "minor": 0.08, "exotic": 0.25, "pkr": 0.05, "metal": 0.15}
WICK = {"major": 0.10, "minor": 0.10, "exotic": 0.30, "pkr": 0.05, "metal": 0.15}
AGREE_TOL = 0.02  # two sources must agree about the days they share within this much


@dataclass(frozen=True)
class Pair:
    symbol: str
    base: str
    quote: str
    group: str  # major | minor | exotic | pkr | metal
    kind: str = "fx"  # fx | metal
    proxy: str | None = None  # a Yahoo symbol to try when the spot symbol is unknown
    synthetic: bool = False

    @property
    def id(self) -> str:
        return make_id("fx", self.symbol)

    @property
    def label(self) -> str:
        return f"{METALS[self.base][0]} / {self.quote}" if self.kind == "metal" else f"{self.base}/{self.quote}"

    @property
    def yahoo(self) -> list[str]:
        out = [f"{self.symbol}=X"]
        if self.base == "USD" and self.kind == "fx":
            out.append(f"{self.quote}=X")  # Yahoo's short form for dollar pairs
        if self.proxy:
            out.append(self.proxy)
        return out


def universe(ucfg: dict[str, Any]) -> list[Pair]:
    pairs: list[Pair] = []
    for i, b in enumerate(ORDER):
        for q in ORDER[i + 1:]:
            pairs.append(Pair(b + q, b, q, "major" if "USD" in (b, q) else "minor"))
    if ucfg.get("include_exotics", True):
        pairs += [Pair("USD" + c, "USD", c, "exotic") for c in EXOTICS]
    pairs.append(Pair("USDPKR", "USD", "PKR", "pkr"))  # always: the app prices everything in rupees with it
    if ucfg.get("include_metals", True):
        pairs += [Pair(m + "USD", m, "USD", "metal", "metal", proxy) for m, (_, proxy) in METALS.items()]
    if ucfg.get("include_pkr_crosses", True):
        pairs += [Pair(c + "PKR", c, "PKR", "pkr", synthetic=True) for c in PKR_CROSSES]
    return pairs


# ---------------------------------------------------------------------------------------------- calendar
def final_day(now: dt.datetime) -> pd.Timestamp:
    """The newest calendar day whose bar can no longer change."""
    now = now.astimezone(dt.timezone.utc)
    d = now.date()
    if d.weekday() == 4 and now.hour >= 22:  # Friday after the New York close: the market is shut until Sunday evening
        return pd.Timestamp(d)
    return pd.Timestamp(d - dt.timedelta(days=1))


def expected_last(now: dt.datetime) -> pd.Timestamp:
    """The newest weekday that should have a final bar."""
    d = final_day(now)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def weekdays_between(first: pd.Timestamp, last: pd.Timestamp) -> list[pd.Timestamp]:
    return [d for d in pd.date_range(first, last, freq="D") if d.weekday() < 5]


def range_for(days: int) -> str:
    for limit, name in ((25, "1mo"), (85, "3mo"), (175, "6mo"), (350, "1y"), (700, "2y"), (1840, "5y"), (3660, "10y")):
        if days <= limit:
            return name
    return "max"


# ---------------------------------------------------------------------------------------------- cleaning
def repair_wicks(df: pd.DataFrame, cap: float) -> tuple[pd.DataFrame, int]:
    """Pull in highs and lows that sit further from the body than any real day could: a one-tick error, not a price that traded."""
    if df is None or len(df) == 0:
        return df, 0
    body_hi = df[["open", "close"]].max(axis=1)
    body_lo = df[["open", "close"]].min(axis=1)
    bad_hi = (df["high"] / body_hi - 1.0) > cap
    bad_lo = (1.0 - df["low"] / body_lo) > cap
    bad = bad_hi | bad_lo
    if not bad.any():
        return df, 0
    out = df.copy()
    out.loc[bad_hi, "high"] = body_hi[bad_hi]
    out.loc[bad_lo, "low"] = body_lo[bad_lo]
    if "approx_hl" not in out.columns:
        out["approx_hl"] = False
    out.loc[bad, "approx_hl"] = True
    return out, int(bad.sum())


def close_only(px: pd.Series) -> pd.DataFrame:
    """Bars from daily closes alone: the open is the previous close, so the range still carries the day-to-day move."""
    px = px.dropna()
    px = px[px > 0].sort_index()
    if len(px) == 0:
        return normalise_bars(pd.DataFrame())
    opn = px.shift(1).fillna(px)
    df = pd.DataFrame({"open": opn, "high": pd.concat([opn, px], axis=1).max(axis=1), "low": pd.concat([opn, px], axis=1).min(axis=1), "close": px, "volume": 0.0})
    df["approx_hl"] = True
    return normalise_bars(df)


def pair_from_legs(legs: pd.DataFrame, base: str, quote: str) -> pd.DataFrame:
    """Close-only bars for BASE/QUOTE from columns of "units per US dollar" (the dollar itself is 1)."""
    def col(c: str) -> pd.Series | None:
        if c == "USD":
            return pd.Series(1.0, index=legs.index)
        return legs[c] if c in legs.columns else None

    b, q = col(base), col(quote)
    if b is None or q is None:
        return normalise_bars(pd.DataFrame())
    return close_only(q / b)


# ---------------------------------------------------------------------------------------------- parsers
def parse_frankfurter(payload: Any) -> pd.DataFrame:
    """A time series answer as a frame of units per dollar: one row per fixing date, one column per currency."""
    try:
        rates = payload["rates"]
    except (KeyError, TypeError) as exc:
        raise ParseError("unexpected Frankfurter answer") from exc
    if not isinstance(rates, dict) or not rates:
        raise ParseError("Frankfurter returned no rates")
    rows: dict[pd.Timestamp, dict[str, float | None]] = {}
    for day, r in rates.items():
        if not isinstance(r, dict):
            continue
        try:
            ts = pd.Timestamp(str(day))
        except (ValueError, TypeError):
            continue
        rows[ts] = {str(c).upper(): positive(v) for c, v in r.items()}
    if not rows:
        raise ParseError("Frankfurter returned no usable rates")
    return pd.DataFrame.from_dict(rows, orient="index").astype("float64").sort_index()


def parse_fawaz(payload: Any, day: pd.Timestamp) -> dict[str, float]:
    """One day's snapshot as units per dollar, keyed by upper-case code. Refuses a snapshot dated otherwise than asked."""
    try:
        got = pd.Timestamp(str(payload["date"]))
        raw = payload["usd"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError("unexpected currency snapshot") from exc
    if got != day:
        raise ParseError(f"snapshot is dated {got.date()}, not {day.date()}")
    if not isinstance(raw, dict):
        raise ParseError("unexpected currency snapshot")
    out = {str(c).upper(): v for c, v in ((c, positive(v)) for c, v in raw.items()) if v}
    if len(out) < 20:
        raise ParseError("currency snapshot is nearly empty")
    return out


# ---------------------------------------------------------------------------------------------- providers
class Frankfurter:
    host = "api.frankfurter.dev"
    CHUNK_DAYS = 90  # longer ranges are summarised weekly or monthly instead of daily

    def __init__(self, http: Client):
        self.http = http
        http.set_interval(self.host, 0.3)

    def legs(self, currencies: list[str], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        frames, a = [], start
        while a <= end:
            b = min(a + pd.Timedelta(days=self.CHUNK_DAYS - 1), end)
            reply = self.http.get(f"https://{self.host}/v1/{a.date()}..{b.date()}", params={"base": "USD", "symbols": ",".join(sorted(currencies))})
            frames.append(parse_frankfurter(reply.json()))
            a = b + pd.Timedelta(days=1)
        out = pd.concat(frames).sort_index()
        return out[~out.index.duplicated(keep="last")]


class Fawaz:
    hosts = ("cdn.jsdelivr.net", "{date}.currency-api.pages.dev")

    def __init__(self, http: Client):
        self.http = http
        self.cache: dict[pd.Timestamp, dict[str, float]] = {}
        http.set_interval("cdn.jsdelivr.net", 0.3)

    def day(self, day: pd.Timestamp) -> dict[str, float]:
        if day in self.cache:
            return self.cache[day]
        d = str(day.date())
        urls = [f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{d}/v1/currencies/usd.json", f"https://{d}.currency-api.pages.dev/v1/currencies/usd.json"]
        last: FetchError | None = None
        for url in urls:
            try:
                self.cache[day] = parse_fawaz(self.http.get(url).json(), day)
                return self.cache[day]
            except FetchError as exc:
                last = exc
        raise last or FetchError(f"no snapshot for {d}")


# ---------------------------------------------------------------------------------------------- orchestration
def _live_quote(meta: dict[str, Any], have: pd.DataFrame, now: dt.datetime) -> dict[str, Any] | None:
    """Yahoo's live price against the close of the day before the one it belongs to; ``None`` when it looks stale or wrong."""
    price, t = positive(meta.get("regularMarketPrice")), to_float(meta.get("regularMarketTime"))
    if price is None or t is None or have is None or len(have) == 0:
        return None
    stamp = dt.datetime.fromtimestamp(t, tz=dt.timezone.utc)
    if not (now - dt.timedelta(days=4) <= stamp <= now + dt.timedelta(minutes=10)):
        return None
    prior = have[have.index < epoch_to_date(t + (to_float(meta.get("gmtoffset")) or 0.0))]
    if len(prior) == 0:
        return None
    prev = float(prior["close"].iloc[-1])
    if prev <= 0 or abs(price / prev - 1.0) > 0.3:
        return None
    return {"price": price, "prev_close": prev, "chg_pct": (price / prev - 1.0) * 100.0, "volume": 0.0, "ts": iso(stamp)}


def _synthetic(p: Pair, bars: dict[str, pd.DataFrame]) -> pd.DataFrame | None:
    """BASE/PKR as the rupee's dollar rate times the other currency's dollar rate (open and close both)."""
    usdpkr = bars.get("FX:USDPKR")
    if usdpkr is None or len(usdpkr) == 0:
        return None
    if p.base in PEGS:
        k = 1.0 / PEGS[p.base]
        opn, close = usdpkr["open"] * k, usdpkr["close"] * k
    else:
        direct = bars.get(make_id("fx", p.base + "USD"))  # the currency is the base of its dollar pair: dollars per unit
        inverse = bars.get(make_id("fx", "USD" + p.base))  # the currency is the quote of its dollar pair: units per dollar
        if direct is not None and len(direct):
            leg_open, leg_close = direct["open"], direct["close"]
        elif inverse is not None and len(inverse):
            leg_open, leg_close = 1.0 / inverse["open"], 1.0 / inverse["close"]
        else:
            return None
        idx = usdpkr.index.intersection(leg_close.index)
        if len(idx) == 0:
            return None
        opn, close = usdpkr.loc[idx, "open"] * leg_open.loc[idx], usdpkr.loc[idx, "close"] * leg_close.loc[idx]
    both = pd.concat([opn, close], axis=1)
    df = pd.DataFrame({"open": opn, "high": both.max(axis=1), "low": both.min(axis=1), "close": close, "volume": 0.0})
    df["approx_hl"] = True  # the two legs' highs and lows did not happen at the same moment
    return normalise_bars(df)


def _triangle_problems(bars: dict[str, pd.DataFrame], pairs: list[Pair], tol: float = 0.03) -> dict[str, float]:
    """A cross that disagrees with its two dollar legs by more than ``tol`` is wrong somewhere: name it and by how much."""
    def usd_per_unit(ccy: str) -> float | None:
        if ccy == "USD":
            return 1.0
        d, i = bars.get(make_id("fx", ccy + "USD")), bars.get(make_id("fx", "USD" + ccy))
        if d is not None and len(d):
            return float(d["close"].iloc[-1])
        if i is not None and len(i):
            return 1.0 / float(i["close"].iloc[-1])
        return None

    bad: dict[str, float] = {}
    for p in pairs:
        if p.group != "minor" or p.id not in bars or len(bars[p.id]) == 0:
            continue
        a, b = usd_per_unit(p.base), usd_per_unit(p.quote)
        if a is None or b is None:
            continue
        implied, actual = a / b, float(bars[p.id]["close"].iloc[-1])
        dev = abs(actual / implied - 1.0)
        if dev > tol:
            bad[p.symbol] = dev
    return bad


def fetch_fx(
    cfg: Config,
    store: HistoryStore,
    http: Client,
    *,
    log: Callable[[str], None] = print,
    now: dt.datetime | None = None,
) -> MarketData:
    t_run = time.time()
    now = (now or utc_now()).astimezone(dt.timezone.utc)
    fcfg = cfg.get_path("data.fx", {}) or {}
    ucfg = cfg.get_path("universe.fx", {}) or {}
    sources = list(fcfg.get("sources") or DEFAULT_SOURCES)
    years = float(fcfg.get("history_years", 5))
    max_seconds = float(fcfg.get("max_seconds", 900))
    fawaz_max_days = int(fcfg.get("fawaz_max_days", 10))
    min_bars = int(ucfg.get("min_bars", 200))
    keep = int(cfg.get_path("backtest.max_history_bars", 1500))
    last_ok = final_day(now)
    want_last = expected_last(now)
    history_start = pd.Timestamp(now.date()) - pd.Timedelta(days=int(years * 365.25))

    pairs = universe(ucfg)
    real = [p for p in pairs if not p.synthetic]
    md = MarketData("fx")
    state = store.load_json(STATE, {}) or {}
    src_state: dict[str, str] = dict(state.get("src") or {})
    fallback_from: dict[str, str] = dict(state.get("fallback_from") or {})
    bars = store.load_bars("fx")
    statuses = []
    notes: list[str] = []
    served: Counter = Counter()
    failed: Counter = Counter()
    quotes: dict[str, dict[str, Any]] = {}

    def fresh(p: Pair) -> bool:
        have = bars.get(p.id)
        return have is not None and len(have) >= min_bars and have.index[-1] >= want_last

    def wants_yahoo(p: Pair) -> bool:
        return not fresh(p) or p.symbol in fallback_from  # bars a fallback supplied are replaced as soon as Yahoo can

    def accept(p: Pair, df: pd.DataFrame, source: str) -> bool:
        """Clean ``df`` and splice it onto what is stored. True when it was used."""
        have = bars.get(p.id)
        df = df[(df.index <= last_ok) & (df.index >= history_start)]
        df, _ = clean_bars(df, max_gap=SPIKE[p.group], label=p.symbol)
        df, _ = repair_wicks(df, WICK[p.group])
        if len(df) == 0:
            return False
        cut = pd.Timestamp(fallback_from[p.symbol]) if (source == "yahoo" and p.symbol in fallback_from) else None
        base = have[have.index < cut] if (cut is not None and have is not None) else have  # Yahoo replaces what a fallback supplied
        if base is not None and len(base) and df.index[0] > base.index[-1] + pd.Timedelta(days=6):
            return False  # would leave a hole in the series
        merged, why = splice(base, df, tol=AGREE_TOL)
        if why:
            return False
        prev_last = base.index[-1] if base is not None and len(base) else None
        bars[p.id] = merged  # type: ignore[assignment]
        src_state[p.symbol] = source
        if source == "yahoo":
            fallback_from.pop(p.symbol, None)
        elif p.symbol not in fallback_from:
            new_days = merged.index if prev_last is None else merged.index[merged.index > prev_last]
            fallback_from[p.symbol] = str(new_days[0].date())
        return True

    # ---- yahoo
    yahoo_hosts = yahoo_mod.HOSTS
    if "yahoo" in sources:
        t0 = time.time()
        for p in real:
            if not wants_yahoo(p):
                continue
            if time.time() - t_run >= max_seconds:
                notes.append("time limit reached while reading Yahoo: the remaining pairs keep their stored history")
                break
            if all(http.is_open(h) for h in yahoo_hosts):
                break
            have = bars.get(p.id)
            if have is not None and len(have) >= min_bars:
                oldest_needed = min(have.index[-1], pd.Timestamp(fallback_from[p.symbol]) if p.symbol in fallback_from else have.index[-1])
                gap = (pd.Timestamp(now.date()) - oldest_needed).days + 5
            else:
                gap = int(years * 365.25) + 10
            got = False
            for sym in p.yahoo:
                try:
                    df, meta = yahoo_mod.chart(http, sym, range_=range_for(gap))
                except FetchError:
                    continue
                if not accept(p, df, "yahoo"):
                    continue
                if sym == p.proxy:
                    notes.append(f"{p.symbol} follows the {sym} futures contract: Yahoo has no spot series for it")
                q = _live_quote(meta, bars[p.id], now)
                if q:
                    quotes[p.id] = q
                got = True
                break
            if got:
                served["yahoo"] += 1
            else:
                failed["yahoo"] += 1
        if served["yahoo"] or failed["yahoo"]:
            tripped = all(http.is_open(h) for h in yahoo_hosts)
            statuses.append(status("yahoo_fx", not tripped and served["yahoo"] > 0, f"bars for {served['yahoo']} pair(s)" + (f", {failed['yahoo']} failed" if failed["yahoo"] else ""), items=served["yahoo"], started=t0, now=now))

    # ---- the European Central Bank's fixings, for what Yahoo could not give
    behind = [p for p in real if not fresh(p)]
    if "frankfurter" in sources and behind:
        usable = [p for p in behind if p.base in LEG_OK and p.quote in LEG_OK]
        if usable:
            t0 = time.time()
            starts = []
            for p in usable:
                have = bars.get(p.id)
                starts.append(have.index[-1] - pd.Timedelta(days=8) if have is not None and len(have) >= min_bars else history_start)  # overlap, to check the two sources agree
            ccys = sorted({c for p in usable for c in (p.base, p.quote)} - {"USD"})
            try:
                legs = Frankfurter(http).legs(ccys, min(starts), last_ok)
                n = 0
                for p in usable:
                    if accept(p, pair_from_legs(legs, p.base, p.quote), "frankfurter"):
                        n += 1
                served["frankfurter"] = n
                statuses.append(status("frankfurter", n > 0, f"ECB fixings for {n} pair(s)", items=n, started=t0, now=now))
                if n:
                    notes.append(f"{n} pair(s) were extended with the ECB's daily fixings (no highs or lows) because Yahoo was unavailable")
            except FetchError as exc:
                statuses.append(status("frankfurter", False, str(exc), started=t0, now=now))

    # ---- one dated snapshot per missing day, for a short gap in what is left
    behind = [p for p in real if not fresh(p) and bars.get(p.id) is not None and len(bars[p.id]) >= min_bars]
    if "fawaz" in sources and behind:
        t0 = time.time()
        fw = Fawaz(http)
        n_pairs = n_fail = 0
        snaps: dict[pd.Timestamp, dict[str, float]] = {}
        for p in behind:
            have = bars[p.id]
            days = weekdays_between(have.index[-1] + pd.Timedelta(days=1), want_last)
            if not days or len(days) > fawaz_max_days:
                continue
            try:
                for d in days:
                    if d not in snaps:
                        snaps[d] = fw.day(d)
            except FetchError:
                n_fail += 1
                continue
            rows = []
            for d in days:
                s = snaps[d]
                b, q = (1.0 if p.base == "USD" else s.get(p.base)), (1.0 if p.quote == "USD" else s.get(p.quote))
                if b and q:
                    rows.append(q / b)
                else:
                    rows.append(None)
            px = pd.Series(rows, index=pd.DatetimeIndex(days), dtype="float64")
            have_close = bars[p.id]["close"]
            ext = close_only(pd.concat([have_close.iloc[-1:], px.dropna()]))
            ext = ext[ext.index > have.index[-1]]
            if len(ext) and accept(p, ext, "fawaz"):
                n_pairs += 1
        if n_pairs or n_fail:
            statuses.append(status("fawaz", n_pairs > 0, f"daily rates for {n_pairs} pair(s)" + (f", {n_fail} failed" if n_fail else ""), items=n_pairs, started=t0, now=now))
            if n_pairs:
                notes.append(f"{n_pairs} pair(s) were extended with daily rate snapshots (no highs or lows) because Yahoo and the ECB were unavailable")

    # ---- derived and checked
    for p in pairs:
        if p.synthetic:
            df = _synthetic(p, bars)
            if df is not None and len(df):
                bars[p.id] = df
    wrong = _triangle_problems(bars, pairs)
    for sym, dev in wrong.items():
        notes.append(f"{sym} was left out: it disagrees with its two dollar pairs by {dev * 100:.1f}%")

    stale_ids = [p.symbol for p in real if p.id in bars and bars[p.id].index[-1] < want_last - pd.Timedelta(days=4)]
    if stale_ids:
        notes.append(f"{len(stale_ids)} pair(s) have no bars from the last few days: " + ", ".join(stale_ids[:8]))
    missing = [p.symbol for p in real if p.id not in bars]
    if missing:
        notes.append(f"no data for {len(missing)} pair(s): " + ", ".join(missing[:8]))

    # ---- instruments
    for p in pairs:
        df = bars.get(p.id)
        if df is None or len(df) == 0 or p.symbol in wrong:
            continue
        meta: dict[str, Any] = {"group": p.group, "base": p.base, "quote": p.quote, "source": src_state.get(p.symbol)}
        if p.synthetic:
            meta.update({"synthetic": True, "peg": PEGS.get(p.base)})
        md.instruments[p.id] = Instrument(p.id, "fx", p.symbol, p.label, None, p.kind, p.group != "pkr", meta)
        md.bars[p.id] = df
        if p.id in quotes:
            md.quotes[p.id] = quotes[p.id]
    lengths = sorted(len(b) for b in md.bars.values())
    median_len = lengths[len(lengths) // 2] if lengths else 0
    md.extras["history_pct"] = int(round(100 * min(1.0, median_len / (years * 252.0)))) if lengths else 0
    md.extras["notes"] = notes
    md.status = statuses

    # ---- persistence
    state["src"] = src_state
    state["fallback_from"] = fallback_from
    store.save_json(STATE, state)
    real_ids = {p.id for p in real}
    keepers = {i: b for i, b in bars.items() if i in real_ids and len(b)}
    if keepers:
        store.save_bars("fx", keepers, keep_days=keep)  # the synthetic crosses are rebuilt from their legs every run
    for n in notes:
        log(f"  fx: {n}")
    log(f"  fx: {len(md.instruments)} instruments, {sum(served.values())} updated, {round(time.time() - t_run)}s")
    return md
