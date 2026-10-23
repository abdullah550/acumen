"""``python -m acumen doctor``: ask every data source one small question and say whether the answer still makes sense.

Acumen reads public websites that nobody promised to keep the same. The tests in this repository run against recorded
shapes, so they cannot tell whether a site has changed since. This module can: it makes a handful of small requests per
source with the same client and the same parsing code the real run uses, and reports for each one

* ``ok``    it answered and the answer parsed into something plausible (how many rows, how fresh),
* ``warn``  a source the app can do without is down (the app falls back to another one; the line says to what),
* ``fail``  a source the market cannot do without is down.

Then it gives one verdict per market: ``ok``, ``degraded`` (works, with less detail or with a fallback) or ``down``.

Run it once from your own machine (``python -m acumen doctor``) and once from the workflow (the "Check data sources" step
of the deploy guide) before trusting the numbers: some providers answer home connections and refuse cloud addresses, or the
other way round, and only a real request shows which.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from ..config import Config
from . import crypto as cr
from . import fx as fxm
from . import macro as mc
from . import psx_dps as dps_mod
from . import psx_tv
from . import yahoo as yahoo_mod
from .common import utc_now
from .http import Client, FetchError, ParseError

OK, WARN, FAIL = "ok", "warn", "fail"
GROUPS = ("psx", "fx", "crypto", "macro")
GROUP_LABEL = {"psx": "Pakistan Stock Exchange", "fx": "Forex and metals", "crypto": "Crypto", "macro": "Macro context"}
PREFERRED_PSX = ("OGDC", "HBL", "LUCK", "ENGRO", "PPL", "UBL", "MCB", "FFC")
# "Plausible" sizes. A source that answers with far fewer rows than this has changed (or is showing an error page).
# ``doctor:`` in config.yml can override any of them; the tests do, because their simulated markets are small.
LIMITS = {"psx_shares": 100, "psx_watch": 50, "tradingview_shares": 100, "exchange_markets": 50, "psx_history_bars": 200, "yahoo_history_bars": 100, "coins": 10}


@dataclass
class Ctx:
    http: Client
    cfg: Config
    now: dt.datetime
    memo: dict[str, Any] = field(default_factory=dict)

    def limit(self, name: str) -> int:
        return int(self.cfg.get_path(f"doctor.{name}", LIMITS[name]))


class Problem(Exception):
    """The source answered but the answer is not what the app needs."""


@dataclass(frozen=True)
class Check:
    key: str
    group: str
    title: str
    run: Callable[[Ctx], str]
    critical: bool = False  # the market is down without it (or without one of its alternatives; see ``verdicts``)
    fallback: str = ""  # what the app does when this one is down


@dataclass
class Result:
    key: str
    group: str
    title: str
    state: str
    detail: str
    seconds: float
    fallback: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "group": self.group, "title": self.title, "state": self.state, "detail": self.detail, "seconds": round(self.seconds, 1)}


# ---------------------------------------------------------------------------------------------- small helpers
def _days_old(df: pd.DataFrame, now: dt.datetime) -> int:
    return int((pd.Timestamp(now.date()) - pd.Timestamp(df.index[-1])).days)


def _bars_detail(label: str, df: pd.DataFrame, now: dt.datetime, *, min_rows: int, max_age: int) -> str:
    if len(df) < min_rows:
        raise Problem(f"{label}: only {len(df)} daily bars (expected at least {min_rows})")
    age = _days_old(df, now)
    if age > max_age:
        raise Problem(f"{label}: the newest bar is {age} days old ({pd.Timestamp(df.index[-1]).date()})")
    last = float(df["close"].iloc[-1])
    if not last > 0:
        raise Problem(f"{label}: the last close is {last}")
    return f"{label}: {len(df):,} daily bars, newest {pd.Timestamp(df.index[-1]).date()}, close {last:,.4g}"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


# ---------------------------------------------------------------------------------------------- PSX
def _dps(ctx: Ctx) -> dps_mod.Dps:
    if "dps" not in ctx.memo:
        ctx.memo["dps"] = dps_mod.Dps(ctx.http, interval=float(ctx.cfg.get_path("data.psx.interval_s", 1.2)), log=lambda m: None)
    return ctx.memo["dps"]


def check_psx_portal(ctx: Ctx) -> str:
    d = _dps(ctx)
    if not d.ensure_node():
        raise Problem("the portal's data routes do not answer on any known address")
    rows = d.symbols()
    ctx.memo["psx_symbols"] = rows
    equities = [r for r in rows if not r["is_etf"] and not r["is_debt"]]
    if len(equities) < ctx.limit("psx_shares"):
        raise Problem(f"only {len(equities)} listed shares (expected several hundred)")
    where = f" through node {d.node}" if d.node else ""
    return f"{_plural(len(equities), 'listed share')} and {len(rows) - len(equities)} other instruments{where}"


def _psx_symbol(ctx: Ctx) -> str:
    """A liquid share to test with: one of the usual names when the portal lists it, else the first listed, else OGDC."""
    rows = ctx.memo.get("psx_symbols")
    if rows is None:
        try:
            rows = _dps(ctx).symbols()
        except FetchError:  # the list is its own check; the others carry on without it
            rows = []
        ctx.memo["psx_symbols"] = rows
    listed = {r["symbol"] for r in rows if not r["is_etf"] and not r["is_debt"]}
    return next((s for s in PREFERRED_PSX if s in listed), None) or (sorted(listed)[0] if listed else PREFERRED_PSX[0])


def check_psx_eod(ctx: Ctx) -> str:
    sym = _psx_symbol(ctx)
    ctx.memo["psx_sym"] = sym
    return _bars_detail(sym, _dps(ctx).eod(sym), ctx.now, min_rows=ctx.limit("psx_history_bars"), max_age=7)


def check_psx_watch(ctx: Ctx) -> str:
    rows = [r for r in _dps(ctx).market_watch() if r.get("close") or r.get("current")]
    if len(rows) < ctx.limit("psx_watch"):
        raise Problem(f"the market watch table holds {len(rows)} priced rows (expected several hundred)")
    return f"{_plural(len(rows), 'row')} in the live market watch"


def check_psx_day(ctx: Ctx) -> str:
    d = _dps(ctx)
    day = ctx.now.date()
    tried = []
    for _ in range(8):
        day -= dt.timedelta(days=1)
        if day.weekday() >= 5:
            continue
        tried.append(day)
        rows = d.day(day)
        if rows:
            return f"{_plural(len(rows), 'symbol')} traded on {day}"
        if len(tried) >= 4:
            break
    raise Problem("no session found in the last four weekdays (a long holiday, or the page changed)")


def check_psx_tv(ctx: Ctx) -> str:
    rows, dropped = psx_tv.scan(ctx.http)
    if len(rows) < ctx.limit("tradingview_shares"):
        raise Problem(f"TradingView listed {len(rows)} Pakistani shares (expected several hundred)")
    with_pe = sum(1 for r in rows.values() if psx_tv.fundamentals_from_row(r).get("pe"))
    note = f"; its newer fields {', '.join(dropped)} were refused" if dropped else ""
    return f"{_plural(len(rows), 'share')}, {with_pe} with a P/E{note}"


def check_psx_yahoo(ctx: Ctx) -> str:
    sym = f"{ctx.memo.get('psx_sym') or 'OGDC'}.KA"
    df, _ = yahoo_mod.chart(ctx.http, sym, range_="1y")
    return _bars_detail(sym, df, ctx.now, min_rows=min(100, ctx.limit("yahoo_history_bars")), max_age=10)


# ---------------------------------------------------------------------------------------------- forex
def _yahoo_any(ctx: Ctx, pair: fxm.Pair, *, min_rows: int = 15, max_age: int = 6) -> str:
    errors = []
    for sym in pair.yahoo:
        try:
            df, _ = yahoo_mod.chart(ctx.http, sym, range_="1mo")
            return _bars_detail(sym, df, ctx.now, min_rows=min_rows, max_age=max_age)
        except (FetchError, Problem) as exc:
            errors.append(f"{sym}: {exc}")
    raise Problem("; ".join(errors))


def check_fx_yahoo(ctx: Ctx) -> str:
    return _yahoo_any(ctx, fxm.Pair("EURUSD", "EUR", "USD", "major"))


def check_fx_pkr(ctx: Ctx) -> str:
    return _yahoo_any(ctx, fxm.Pair("USDPKR", "USD", "PKR", "pkr"), min_rows=10, max_age=8)


def check_fx_pkr_peg(ctx: Ctx) -> str:
    """Yahoo's dollar-rupee closes against the rupee's crosses with the dirham and the riyal, which are pegged to the dollar.

    In autumn 2026 Yahoo's own series mixed a second quote level, about 3% away, into most of its daily bars (see
    :func:`acumen.fetch.fx.pkr_reference`). A run repairs such bars; this says whether Yahoo is doing it right now.
    """
    usd, _ = yahoo_mod.chart(ctx.http, "USDPKR=X", range_="1mo")
    ref = fxm.pkr_reference(ctx.http, "1mo")
    if ref is None:
        raise Problem("neither the dirham's nor the riyal's cross with the rupee answered, so the dollar-rupee bars could not be checked")
    days = usd.index.intersection(ref.index[ref["close"].notna()])
    if len(days) < 5:
        raise Problem(f"only {len(days)} days to set Yahoo's dollar-rupee bars against the crosses (expected at least 5)")
    gap = (usd.loc[days, "close"] / ref.loc[days, "close"] - 1.0).abs()
    off = gap[gap > fxm.PKR_CLOSE_TOL]
    if len(off):
        raise Problem(f"{len(off)} of {len(days)} dollar-rupee closes differ from the crosses by more than {fxm.PKR_CLOSE_TOL:.1%} (largest {gap.max():.1%}, on {gap.idxmax().date()}): "
                      "Yahoo has mixed a second quote level into its bars")
    return f"{len(days)} closes agree with the crosses (largest difference {gap.max():.2%})"


def check_fx_metal(ctx: Ctx) -> str:
    return _yahoo_any(ctx, fxm.Pair("XAUUSD", "XAU", "USD", "metal", "metal", fxm.METALS["XAU"][1]))


def check_fx_frankfurter(ctx: Ctx) -> str:
    end = pd.Timestamp(ctx.now.date()) - pd.Timedelta(days=1)
    legs = fxm.Frankfurter(ctx.http).legs(["EUR", "JPY", "GBP"], end - pd.Timedelta(days=20), end)
    if len(legs) < 5 or legs.shape[1] < 3:
        raise Problem(f"only {len(legs)} fixings for {legs.shape[1]} currencies")
    return f"{_plural(len(legs), 'daily fixing')}, newest {legs.index[-1].date()}"


def check_fx_fawaz(ctx: Ctx) -> str:
    day = pd.Timestamp(ctx.now.date()) - pd.Timedelta(days=2)
    snap = fxm.Fawaz(ctx.http).day(day)
    if len(snap) < 20:
        raise Problem(f"the snapshot for {day.date()} holds {len(snap)} currencies")
    return f"snapshot for {day.date()}: {len(snap)} currencies" + (", including PKR" if "PKR" in snap else "")


# ---------------------------------------------------------------------------------------------- crypto
def check_cg_markets(ctx: Ctx) -> str:
    cg = cr.CoinGecko(ctx.http, os.environ.get(str(ctx.cfg.get_path("data.crypto.coingecko_key_env", "COINGECKO_API_KEY"))) or None)
    rows = cg.markets(20)
    if len(rows) < ctx.limit("coins"):
        raise Problem(f"CoinGecko listed {len(rows)} coins")
    g = cg.global_stats()
    top = rows[0]
    dom = g.get("btc_dominance")
    return f"{_plural(len(rows), 'coin')} (top: {top.get('symbol', '?')}); BTC dominance {dom:.1f}%" if dom is not None else f"{_plural(len(rows), 'coin')}"


def _exchange_check(name: str) -> Callable[[Ctx], str]:
    def run(ctx: Ctx) -> str:
        ex = cr.EXCHANGES[name](ctx.http)
        listing = ex.listing()
        if len(listing) < ctx.limit("exchange_markets"):
            raise Problem(f"{ex.label} lists {len(listing)} markets")
        btc = listing.get("BTC")
        if btc is None:
            raise Problem(f"{ex.label} has no BTC market in its list")
        df = ex.daily(btc.pair, 30, ctx.now)
        # the newest candle may be today's unfinished one; the pipeline drops it, so it is the day before that which must be recent
        if len(df) and pd.Timestamp(df.index[-1]).date() >= ctx.now.date():
            df = df.iloc[:-1]
        text = _bars_detail(btc.pair, df, ctx.now, min_rows=20, max_age=3)
        if not 1_000 < float(df["close"].iloc[-1]) < 10_000_000:
            raise Problem(f"{btc.pair} closed at {float(df['close'].iloc[-1]):,.2f}: not a plausible Bitcoin price")
        return f"{_plural(len(listing), 'market')}; {text}"

    return run


def check_crypto_fng(ctx: Ctx) -> str:
    v = cr.fear_greed(ctx.http)
    if v is None or not 0 <= v <= 100:
        raise Problem(f"unexpected reading {v}")
    return f"fear and greed index {v:.0f}"


# ---------------------------------------------------------------------------------------------- macro
def check_macro_yahoo(ctx: Ctx) -> str:
    symbols: dict[str, str] = dict(ctx.cfg.get_path("data.macro.yahoo_symbols", {}) or {})
    if not symbols:
        return "no symbols configured"
    good, bad = [], []
    for name, sym in symbols.items():
        try:
            df, _ = yahoo_mod.chart(ctx.http, sym, range_="1mo")
            (good if len(df) >= 3 else bad).append(f"{name} ({sym})")
        except FetchError:
            bad.append(f"{name} ({sym})")
    if not good:
        raise Problem("none of " + ", ".join(bad) + " answered")
    return f"{len(good)} of {len(symbols)} series" + (f"; not answering: {', '.join(bad)}" if bad else "")


def check_macro_fred(ctx: Ctx) -> str:
    ids = [str(x) for x in (ctx.cfg.get_path("data.macro.fred_series", []) or [])] or ["FEDFUNDS"]
    pol = str(ctx.cfg.get_path("data.macro.pk_policy_fred", "") or "")
    start = (pd.Timestamp(ctx.now.date()) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    got, bad, down = mc.read_fred(ctx.http, list(dict.fromkeys([*ids, "DGS10", *([pol] if pol else [])])), start)
    if not got:
        raise Problem("no FRED series answered" + (" in time" if down else ""))
    return "; ".join(f"{sid} {pd.Timestamp(s.index[-1]).date()}" for sid, s in got.items()) + (f"; not answering: {', '.join(bad)}" if bad else "")


def check_macro_inflation(ctx: Ctx) -> str:
    year = ctx.now.year
    errors = []
    try:
        v, y = mc.parse_imf_inflation(ctx.http.get(mc.IMF_URL).json(), year)
        return f"IMF: Pakistan inflation {v:.1f}% for {y}"
    except FetchError as exc:
        errors.append(f"IMF: {exc}")
    try:
        v, y = mc.parse_worldbank_inflation(ctx.http.get(mc.WORLD_BANK_URL, params={"format": "json", "per_page": 8}).json())
        return f"World Bank: Pakistan inflation {v:.1f}% for {y} (the IMF did not answer: {errors[0].removeprefix('IMF: ')})"
    except FetchError as exc:
        errors.append(f"World Bank: {exc}")
    raise Problem("; ".join(errors))


# ---------------------------------------------------------------------------------------------- the list
def checks() -> list[Check]:
    c = Check
    return [
        c("psx.portal", "psx", "PSX data portal: share list", check_psx_portal, True),
        c("psx.eod", "psx", "PSX data portal: price history", check_psx_eod, True, "history comes from Yahoo (.KA symbols) when it is up"),
        c("psx.day", "psx", "PSX data portal: a past session", check_psx_day, False, "daily bars are then built from the portal's price history only"),
        c("psx.watch", "psx", "PSX data portal: live market watch", check_psx_watch, False, "the last close stands in for a live price"),
        c("psx.tradingview", "psx", "TradingView: fundamentals", check_psx_tv, False, "valuation and quality scores are left out for every share"),
        c("psx.yahoo", "psx", "Yahoo: fallback history", check_psx_yahoo, False, "no fallback if the portal fails"),
        c("fx.yahoo", "fx", "Yahoo: forex prices", check_fx_yahoo, True, "daily fixings from the European Central Bank replace them (no highs or lows)"),
        c("fx.pkr", "fx", "Yahoo: USD/PKR", check_fx_pkr, False, "the rupee rate comes from the ECB fixings and daily snapshots"),
        c("fx.pkr_peg", "fx", "Yahoo: USD/PKR against its crosses", check_fx_pkr_peg, False, "dollar-rupee bars that stray are repaired from the crosses, when the crosses answer"),
        c("fx.metal", "fx", "Yahoo: gold", check_fx_metal, False, "gold and silver are left out"),
        c("fx.frankfurter", "fx", "ECB daily fixings (Frankfurter)", check_fx_frankfurter, False, "Yahoo alone supplies forex"),
        c("fx.fawaz", "fx", "Daily rate snapshots (currency-api)", check_fx_fawaz, False, "the rupee has one source less to be checked against"),
        c("crypto.coingecko", "crypto", "CoinGecko: coin list and market caps", check_cg_markets, True, "the universe comes from the last run"),
        *[c(f"crypto.{n}", "crypto", f"{cr.EXCHANGES[n].label}: daily candles", _exchange_check(n), False, "the next exchange in the list is used") for n in cr.DEFAULT_SOURCES if n in cr.EXCHANGES],
        c("crypto.fng", "crypto", "Fear and greed index", check_crypto_fng, False, "the sentiment line is left out"),
        c("macro.yahoo", "macro", "Yahoo: dollar index, yields, oil, gold, equities", check_macro_yahoo, False, "series come from FRED or from a copy saved by an earlier run"),
        c("macro.fred", "macro", "FRED: US rates and Pakistan policy rate", check_macro_fred, False, "the policy-rate table in config.yml is used"),
        c("macro.inflation", "macro", "IMF and World Bank: Pakistan inflation", check_macro_inflation, False, "the inflation figure in config.yml is used"),
    ]


def select(items: list[Check], only: str | None) -> list[Check]:
    """``only`` is a comma-separated list of groups (``psx,fx``) or check keys (``crypto.okx``)."""
    if not only:
        return items
    wanted = [w.strip().lower() for w in only.replace(";", ",").split(",") if w.strip()]
    unknown = [w for w in wanted if w not in GROUPS and not any(i.key == w for i in items)]
    if unknown:
        raise ValueError(f"unknown {', '.join(repr(u) for u in unknown)}; use a group ({', '.join(GROUPS)}) or a check such as crypto.okx")
    return [i for i in items if i.group in wanted or i.key in wanted]


def _problem(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:240] if text else type(exc).__name__


def run_checks(cfg: Config, http: Client | None = None, only: str | None = None, *, now: dt.datetime | None = None, log: Callable[[Result], None] | None = None) -> list[Result]:
    now = (now or utc_now()).astimezone(dt.timezone.utc)
    http = http or Client(cfg.user_agent, timeout=float(cfg.get_path("data.request_timeout_s", 30)), max_retries=1)
    ctx = Ctx(http, cfg, now)
    results: list[Result] = []
    for chk in select(checks(), only):
        started = time.time()
        try:
            detail = chk.run(ctx)
            state = OK
        except (FetchError, Problem, ParseError) as exc:
            state, detail = (FAIL if chk.critical else WARN), _problem(exc)
        except Exception as exc:  # noqa: BLE001 - a doctor must never stop at the first surprise
            state, detail = (FAIL if chk.critical else WARN), f"unexpected {type(exc).__name__}: {_problem(exc)}"
        res = Result(chk.key, chk.group, chk.title, state, detail, time.time() - started, chk.fallback if state != OK else "")
        results.append(res)
        if log:
            log(res)
    return results


# ---------------------------------------------------------------------------------------------- verdicts
def complete_groups(results: list[Result]) -> set[str]:
    """Groups whose every check was run: a verdict on a group needs all of its checks (``--only crypto.okx`` gives none)."""
    ran = {r.key for r in results}
    return {g for g in GROUPS if {c.key for c in checks() if c.group == g} <= ran and any(r.group == g for r in results)}


def _state(results: list[Result], key: str) -> str | None:
    for r in results:
        if r.key == key:
            return r.state
    return None


def verdicts(results: list[Result]) -> dict[str, tuple[str, str]]:
    """``{group: ("ok" | "degraded" | "down", why)}`` for the groups that were checked.

    Sources that back each other up (the five exchanges, the two European Central Bank feeds) only matter when all of them
    are down; the others each take something away when they fail, and the verdict says what.
    """
    out: dict[str, tuple[str, str]] = {}
    have = complete_groups(results)

    def ok(key: str) -> bool:
        return _state(results, key) == OK

    def lost(keys: list[str]) -> str:
        return "; ".join(f"{r.title}: {r.fallback or 'not available'}" for r in results if r.key in keys and r.state != OK)

    def verdict(group: str, down: str | None, extras: list[str]) -> None:
        if down:
            out[group] = ("down", down)
        else:
            why = lost(extras)
            out[group] = ("degraded", why) if why else ("ok", "")

    if "psx" in have:
        portal = ok("psx.portal") and ok("psx.eod")
        if not portal and not ok("psx.yahoo"):
            verdict("psx", "neither the portal nor Yahoo returned usable price history", [])
        elif not portal:
            out["psx"] = ("degraded", "the portal is down: Yahoo history is used, without highs and lows")
        else:
            verdict("psx", None, ["psx.day", "psx.watch", "psx.tradingview"])
    if "fx" in have:
        ecb = ok("fx.frankfurter") or ok("fx.fawaz")
        if not ok("fx.yahoo") and not ecb:
            verdict("fx", "neither Yahoo nor the ECB fixings answered", [])
        elif not ok("fx.yahoo"):
            out["fx"] = ("degraded", "Yahoo is down: daily ECB fixings replace it (no highs or lows)")
        else:
            verdict("fx", None, ["fx.pkr", "fx.pkr_peg", "fx.metal"])
    if "crypto" in have:
        venues = [r.key for r in results if r.group == "crypto" and r.key not in ("crypto.coingecko", "crypto.fng")]
        any_venue = any(ok(k) for k in venues)
        if not any_venue and not ok("crypto.coingecko"):
            verdict("crypto", "no exchange and not CoinGecko returned usable data", [])
        elif not any_venue:
            out["crypto"] = ("degraded", "no exchange answered: CoinGecko supplies closing prices only (no highs, lows or volume)")
        else:
            verdict("crypto", None, ["crypto.coingecko", "crypto.fng"])
    if "macro" in have:
        verdict("macro", None, [r.key for r in results if r.group == "macro"])
    return out


def exit_code(results: list[Result], *, strict: bool = False) -> int:
    v = verdicts(results)
    if any(state == "down" for state, _ in v.values()):
        return 1
    whole = complete_groups(results)
    if any(r.state == FAIL for r in results if r.group not in whole):  # a partial run has no verdict: a failed essential source decides
        return 1
    if strict and (any(state != "ok" for state, _ in v.values()) or any(r.state != OK for r in results)):
        return 1
    return 0


# ---------------------------------------------------------------------------------------------- text
MARK = {OK: "ok  ", WARN: "WARN", FAIL: "FAIL"}


def format_result(r: Result) -> str:
    head = f"  {MARK[r.state]}  {r.title:<46} {r.seconds:5.1f}s  "
    text = head + r.detail
    if r.state != OK and r.fallback:
        text += f"\n{'':8}-> {r.fallback}"
    return text


def render(results: list[Result], cfg: Config, now: dt.datetime) -> str:
    lines = [f"Acumen doctor, {now.strftime('%Y-%m-%d %H:%M UTC')}", f"user agent: {cfg.user_agent}", ""]
    for g in GROUPS:
        part = [r for r in results if r.group == g]
        if not part:
            continue
        lines.append(GROUP_LABEL[g])
        lines += [format_result(r) for r in part]
        lines.append("")
    lines.append("Verdict")
    for g, (state, why) in verdicts(results).items():
        lines.append(f"  {GROUP_LABEL[g]:<26} {state.upper():<9} {why}".rstrip())
    partial = [GROUP_LABEL[g] for g in GROUPS if any(r.group == g for r in results) and g not in complete_groups(results)]
    if partial:
        lines.append(f"  (only some checks were run for: {', '.join(partial)}; no verdict for those)")
    return "\n".join(lines)
