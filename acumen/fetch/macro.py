"""The macro backdrop shown beside the forex ideas: dollar index, US yields, gold, oil, equities, volatility, and Pakistan's rates.

Sources
-------
Yahoo (``data.macro.yahoo_symbols``)
    Two years of daily closes for each symbol: the dollar index, the US 10-year yield, gold and Brent futures, the S&P 500,
    the VIX and an emerging-markets fund.
FRED (``data.macro.fred_series``)
    The Federal Reserve's own data as plain CSV, no key: the fed funds rate, the 2-year yield and the 10y-2y spread. The 10-year
    yield (``DGS10``) is also read, as the fallback for the Yahoo symbol, whose units have changed in the past.
IMF DataMapper, then the World Bank
    Pakistan's consumer-price inflation. These are *annual averages* (the IMF's includes an estimate for the current year), not the
    latest monthly reading: the Pakistan Bureau of Statistics publishes that as a PDF, which this app does not parse. The note says so.
Policy rate
    The State Bank's rate is kept by hand in the configuration (``rates.policy_rate_pct``). An IMF-reported series on FRED
    (``data.macro.pk_policy_fred``) is used instead only when its newest observation is more recent than the configured table.

A source that fails is replaced by the copy saved on a previous run (up to ``MAX_SAVED_AGE_DAYS`` old) and the note says so.
Nothing here ever stops the run.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

import pandas as pd

from ..config import Config
from ..store import HistoryStore
from . import yahoo as yahoo_mod
from .common import iso, status, to_float, utc_now
from .fx import final_day
from .http import Client, FetchError, ParseError

STATE = "macro_state"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
IMF_URL = "https://www.imf.org/external/datamapper/api/v1/PCPIPCH/PAK"
WORLD_BANK_URL = "https://api.worldbank.org/v2/country/PAK/indicator/FP.CPI.TOTL.ZG"
KEEP_POINTS = 520
MAX_SAVED_AGE_DAYS = 14
FRED_NAMES = {"FEDFUNDS": "FEDFUNDS", "DGS2": "DGS2", "T10Y2Y": "T10Y2Y"}  # FRED id -> series name in the app


# ---------------------------------------------------------------------------------------------- parsers
def parse_fred_csv(text: str, series_id: str) -> pd.Series:
    """``DATE,ID`` rows with a "." for a missing value. Refuses anything that is not that (a login or error page)."""
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ParseError(f"FRED returned nothing for {series_id}")
    header = [h.strip().strip('"').upper() for h in lines[0].split(",")]
    if len(header) < 2 or header[1] != series_id.upper():
        raise ParseError(f"FRED answered something other than {series_id}")
    days, values = [], []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < 2:
            continue
        v = to_float(parts[1])
        try:
            d = pd.Timestamp(parts[0].strip().strip('"'))
        except (ValueError, TypeError):
            continue
        if v is not None:
            days.append(d)
            values.append(v)
    if not days:
        raise ParseError(f"FRED has no observations for {series_id}")
    s = pd.Series(values, index=pd.DatetimeIndex(days), dtype="float64").sort_index()
    return s[~s.index.duplicated(keep="last")]


def parse_imf_inflation(payload: Any, year: int) -> tuple[float, int]:
    """The newest annual-average inflation figure up to ``year`` (the current year's is an estimate)."""
    try:
        values = payload["values"]["PCPIPCH"]["PAK"]
        points = {int(y): to_float(v) for y, v in values.items() if str(y).isdigit()}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ParseError("unexpected IMF answer") from exc
    years = [y for y, v in points.items() if v is not None and y <= year]
    if not years:
        raise ParseError("the IMF has no inflation figure for Pakistan up to this year")
    best = max(years)
    return float(points[best]), best  # type: ignore[arg-type]


def parse_worldbank_inflation(payload: Any) -> tuple[float, int]:
    """The newest year with a value: ``[paging, [{"date": "2024", "value": 23.4}, ...]]``."""
    try:
        rows = payload[1]
    except (KeyError, IndexError, TypeError) as exc:
        raise ParseError("unexpected World Bank answer") from exc
    best: tuple[float, int] | None = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        v, y = to_float(r.get("value")), to_float(r.get("date"))
        if v is not None and y is not None and (best is None or int(y) > best[1]):
            best = (float(v), int(y))
    if best is None:
        raise ParseError("the World Bank has no inflation figure for Pakistan")
    return best


def fix_yield_units(s: pd.Series) -> pd.Series:
    """Yahoo has quoted the 10-year yield index at ten times the yield (42.5 for 4.25 percent). No real yield is above 20."""
    return s.where(s <= 20.0, s / 10.0)


# ---------------------------------------------------------------------------------------------- saved copies
def _pack(s: pd.Series, now: dt.datetime) -> dict[str, Any]:
    s = s.dropna().iloc[-KEEP_POINTS:]
    return {"saved_at": iso(now), "dates": [d.strftime("%Y-%m-%d") for d in s.index], "values": [round(float(v), 6) for v in s]}


def _unpack(blob: Any, today: pd.Timestamp) -> tuple[pd.Series, pd.Timestamp] | None:
    """A saved series and the day it was saved, unless it is missing, malformed or older than ``MAX_SAVED_AGE_DAYS``."""
    try:
        s = pd.Series([float(v) for v in blob["values"]], index=pd.DatetimeIndex(pd.to_datetime(blob["dates"])), dtype="float64")
        saved = pd.Timestamp(str(blob["saved_at"])[:10])
    except (KeyError, TypeError, ValueError):
        return None
    return (s, saved) if len(s) and (today - saved).days <= MAX_SAVED_AGE_DAYS else None


# ---------------------------------------------------------------------------------------------- orchestration
def fetch_macro(
    cfg: Config,
    store: HistoryStore,
    http: Client,
    *,
    log: Callable[[str], None] = print,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any], list, list[str]]:
    """``({"series": {name: Series}, "pk": {...}}, health lines, notes)``."""
    now = (now or utc_now()).astimezone(dt.timezone.utc)
    today = pd.Timestamp(now.date())
    mcfg = cfg.get_path("data.macro", {}) or {}
    symbols: dict[str, str] = dict(mcfg.get("yahoo_symbols") or {})
    fred_ids = [str(x) for x in (mcfg.get("fred_series") or [])]
    policy_id = str(mcfg.get("pk_policy_fred") or "")
    last_ok = final_day(now)
    start = (today - pd.Timedelta(days=800)).strftime("%Y-%m-%d")
    saved = store.load_json(STATE, {}) or {}
    saved_series: dict[str, Any] = dict(saved.get("series") or {})
    series: dict[str, pd.Series] = {}
    statuses: list = []
    notes: list[str] = []

    # ---- Yahoo
    served = failed = 0
    for name, sym in symbols.items():
        try:
            df, _ = yahoo_mod.chart(http, sym, range_="2y")
            s = df["close"]
            s = s[s.index <= last_ok]
            if name == "US10Y":
                s = fix_yield_units(s)
            if len(s) >= 3:
                series[name] = s
                served += 1
                continue
        except FetchError:
            pass
        failed += 1
    if symbols:
        statuses.append(status("yahoo_macro", served > 0, f"{served} of {len(symbols)} series" + (f", {failed} failed" if failed else ""), items=served, now=now))

    # ---- FRED
    fred: dict[str, pd.Series] = {}
    ids = list(dict.fromkeys([*fred_ids, "DGS10"])) if fred_ids else []
    f_failed = 0
    for sid in ids:
        try:
            fred[sid] = parse_fred_csv(http.get(FRED_URL, params={"id": sid, "cosd": start}, interval=0.5).text, sid)
        except FetchError:
            f_failed += 1
    if ids:
        statuses.append(status("fred", len(fred) > 0, f"{len(fred)} series" + (f", {f_failed} failed" if f_failed else ""), items=len(fred), now=now))
    for sid, name in FRED_NAMES.items():
        if sid in fred:
            series[name] = fred[sid]
    if "US10Y" not in series and "DGS10" in fred:
        series["US10Y"] = fred["DGS10"]
        notes.append("US 10-year yield comes from FRED: the Yahoo symbol was unavailable")
    policy_series = None
    if policy_id:
        try:
            policy_series = parse_fred_csv(http.get(FRED_URL, params={"id": policy_id, "cosd": start}, interval=0.5).text, policy_id)
        except FetchError:
            policy_series = None  # optional: the configured table is the normal source

    # ---- anything missing comes from an earlier run's copy, and says so
    wanted = set(symbols) | {FRED_NAMES[i] for i in fred_ids if i in FRED_NAMES} | ({"US10Y"} if ids else set())
    reused: dict[str, pd.Timestamp] = {}
    for name in sorted(wanted - set(series)):
        got = _unpack(saved_series.get(name), today)
        if got is not None:
            series[name], reused[name] = got
    if reused:
        notes.append("kept from an earlier run because the live source did not answer: " + ", ".join(f"{n} (saved {d.date()})" for n, d in reused.items()))
    absent = sorted(wanted - set(series))
    if absent:
        notes.append(f"no data for {', '.join(absent)}")

    # ---- Pakistan
    pk: dict[str, Any] = {}
    pk_notes: list[str] = []
    year = now.year
    cpi: tuple[float, int] | None = None
    cpi_src = ""
    for src, url, params in (("imf", IMF_URL, None), ("worldbank", WORLD_BANK_URL, {"format": "json", "per_page": 8})):
        try:
            body = http.get(url, params=params, interval=0.5).json()
            cpi = parse_imf_inflation(body, year) if src == "imf" else parse_worldbank_inflation(body)
            cpi_src = src
            statuses.append(status(f"{src}_inflation", True, f"Pakistan inflation {cpi[0]:.1f}% for {cpi[1]}", items=1, now=now))
            break
        except FetchError as exc:
            statuses.append(status(f"{src}_inflation", False, str(exc), now=now))
    old_pk = saved.get("pk") if isinstance(saved.get("pk"), dict) else {}
    if cpi is not None:
        label = "IMF estimate" if (cpi_src == "imf" and cpi[1] >= year) else ("IMF figure" if cpi_src == "imf" else "World Bank figure")
        pk["cpi_yoy"] = round(cpi[0], 2)
        pk_notes.append(f"Inflation: {label} for {cpi[1]} (annual average), not the latest monthly reading.")
    elif old_pk.get("cpi_yoy") is not None and old_pk.get("saved_at") and (today - pd.Timestamp(str(old_pk["saved_at"])[:10])).days <= MAX_SAVED_AGE_DAYS:
        pk["cpi_yoy"] = old_pk["cpi_yoy"]
        pk_notes.append(f"Inflation as saved on {str(old_pk['saved_at'])[:10]}: {old_pk.get('basis') or 'annual average'}.")
    rates_as_of = str(cfg.get_path("rates.as_of", "") or "")
    if policy_series is not None and len(policy_series):
        obs, val = policy_series.index[-1], float(policy_series.iloc[-1])
        try:
            newer = obs > pd.Timestamp(rates_as_of) if rates_as_of else True
        except ValueError:
            newer = True
        if newer and 0.0 <= val <= 40.0 and (today - obs).days <= 400:
            pk["policy_rate"] = round(val, 2)
            pk["asof"] = obs.strftime("%Y-%m-%d")
            pk_notes.append("Policy rate: IMF-reported series on FRED, newer than the configured table.")
    pk["note"] = " ".join(pk_notes) or None

    # ---- persistence: only what was read fresh this run gets a new save date
    for n, s in series.items():
        if n not in reused:
            saved_series[n] = _pack(s, now)
    new_pk = dict(old_pk)
    if cpi is not None:
        new_pk = {"cpi_yoy": pk["cpi_yoy"], "saved_at": iso(now), "basis": pk_notes[0].removeprefix("Inflation: ").rstrip(".")}
    store.save_json(STATE, {"series": saved_series, "pk": new_pk})
    for n in notes:
        log(f"  macro: {n}")
    log(f"  macro: {len(series)} series ({len(reused)} from an earlier run), Pakistan inflation {pk.get('cpi_yoy')}")
    return {"series": series, "pk": pk}, statuses, notes
