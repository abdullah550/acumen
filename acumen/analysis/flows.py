"""Where is money entering and leaving the Pakistan market, and which shares sit in the sectors it is moving to?

What is measured
----------------
Price and volume only, for every liquid share, every day: the traded value (price x shares) counted as *buying* on a day the close
rose and as *selling* on a day it fell (the tick rule applied to daily closes). Over a window of calendar days, a sector's **buying
pressure** is its net signed value divided by its total traded value, a number between -100% and +100%. It is measured over 7 and over
30 days, and a sector is judged *against the rest of the market*, because in a rising market every sector has positive pressure.

This is not investor-category data. It cannot say whether foreigners, funds or individuals did the buying; the National Clearing
Company's foreign/local tables say that, and they are not available to this app (``docs/DATA_SOURCES.md``). The page says so.

Four states, from the two windows (each a cross-sector z-score of pressure relative to the market, cut at +-``state_z``):
entering (both above), leaving (both below), turning in (7 days above, 30 days not), fading (7 days below, 30 days not).

What is *not* claimed
---------------------
That any of this predicts anything. ``flows_backtest.py`` asks, at weekly anchors over the stored history, whether the 7- and 30-day
indicators ranked the sectors' *later* relative returns; the answer is part of the output and the page leads with it. The weekly
archive kept in the store records each week's call as it was made, so it can be scored out of sample as time passes.

Candidates
----------
A screen for research, not a forecast: shares showing (1) accumulation: 30-day buying pressure in the top third of liquid shares, not
negative over 7 days, and not already up more than ``max_chase_pct`` in a month; (2) improving fundamentals: profitable with earnings up
at least ``min_eps_growth_pct`` on a year earlier; (3) trading interest: turnover over the last 7 days at least ``min_rvol`` times its
90-day norm. Interest is the stand-in for institutional participation, which price and volume cannot show.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import flows_backtest as fb

STATE_NAME = "flows_weekly"
FAST, SLOW, NORM = 7, 30, 90  # calendar days
STATES = ("entering", "turning_in", "neutral", "fading", "leaving")
STATE_LABEL = {"entering": "Entering", "turning_in": "Turning in", "neutral": "Neutral", "fading": "Fading", "leaving": "Leaving"}
KEEP_WEEKS = 104


# ---------------------------------------------------------------------------------------------- the panel
@dataclass
class Panel:
    """Aligned daily frames (dates x share ids) for the liquid shares, plus what each share is."""

    close: pd.DataFrame
    value: pd.DataFrame  # traded value: close x shares, only on days with volume
    flow: pd.DataFrame  # +value on days the close rose, -value on days it fell
    sector: pd.Series  # id -> sector
    sym: dict[str, str]
    name: dict[str, str]

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index


def make_panel(bars: dict[str, pd.DataFrame], shares: list[dict[str, Any]]) -> Panel | None:
    """``shares``: dicts with id, sym, name, sector. Returns ``None`` when fewer than three shares have usable bars."""
    use = [s for s in shares if s.get("sector") and s["id"] in bars and bars[s["id"]] is not None and len(bars[s["id"]]) >= 30]
    if len(use) < 3:
        return None
    raw = pd.DataFrame({s["id"]: bars[s["id"]]["close"] for s in use}).sort_index()
    vol = pd.DataFrame({s["id"]: bars[s["id"]]["volume"] for s in use}).reindex(raw.index)
    close = raw.ffill(limit=5)
    value = (raw * vol).where(vol > 0)
    flow = np.sign(close.diff()) * value
    return Panel(close=close, value=value, flow=flow, sector=pd.Series({s["id"]: s["sector"] for s in use}), sym={s["id"]: s["sym"] for s in use}, name={s["id"]: s.get("name") or s["sym"] for s in use})


def eligible_sectors(panel: Panel, min_members: int) -> list[str]:
    counts = panel.sector.value_counts()
    return sorted(str(s) for s in counts[counts >= min_members].index)


def _by_sector(frame: pd.DataFrame, panel: Panel, sectors: list[str]) -> pd.DataFrame:
    return pd.DataFrame({s: frame[list(panel.sector[panel.sector == s].index)].sum(axis=1, min_count=1) for s in sectors}, index=frame.index)


def _mean_by_sector(frame: pd.DataFrame, panel: Panel, sectors: list[str]) -> pd.DataFrame:
    return pd.DataFrame({s: frame[list(panel.sector[panel.sector == s].index)].mean(axis=1) for s in sectors}, index=frame.index)


def _roll(frame: pd.DataFrame | pd.Series, days: int, how: str = "sum"):
    r = frame.rolling(f"{days}D", min_periods=1)
    return r.sum() if how == "sum" else r.mean()


def _zscore(frame: pd.DataFrame) -> pd.DataFrame:
    sd = frame.std(axis=1, ddof=1).replace(0.0, np.nan)
    return frame.sub(frame.mean(axis=1), axis=0).div(sd, axis=0)


@dataclass
class Frames:
    """The indicators for every date: what the table shows at the as-of date and what the backtest reads at each anchor."""

    sectors: list[str]
    pvf: dict[int, pd.DataFrame]  # window -> sector buying pressure, -1..1
    mkt: dict[int, pd.Series]
    rel: dict[int, pd.DataFrame]  # pressure minus the market's
    z: dict[int, pd.DataFrame]
    score: pd.DataFrame
    net: dict[int, pd.DataFrame]  # signed value over the window, PKR
    sector_value: pd.DataFrame  # daily traded value by sector


def indicator_frames(panel: Panel, sectors: list[str]) -> Frames:
    S = _by_sector(panel.flow, panel, sectors)
    V = _by_sector(panel.value, panel, sectors)
    Sm = panel.flow.sum(axis=1, min_count=1)
    Vm = panel.value.sum(axis=1, min_count=1)
    pvf, mkt, rel, z, net = {}, {}, {}, {}, {}
    for w in (FAST, SLOW):
        net[w] = _roll(S, w)
        pvf[w] = net[w] / _roll(V, w).replace(0.0, np.nan)
        mkt[w] = _roll(Sm, w) / _roll(Vm, w).replace(0.0, np.nan)
        rel[w] = pvf[w].sub(mkt[w], axis=0)
        z[w] = _zscore(rel[w])
    return Frames(sectors=sectors, pvf=pvf, mkt=mkt, rel=rel, z=z, score=(z[FAST] + z[SLOW]) / 2.0, net=net, sector_value=V)


def stock_pressure(panel: Panel, window: int) -> pd.DataFrame:
    return _roll(panel.flow, window) / _roll(panel.value, window).replace(0.0, np.nan)


def _at(obj: pd.DataFrame | pd.Series, day: pd.Timestamp):
    """The last row on or before ``day``."""
    sub = obj.loc[:day]
    return sub.iloc[-1] if len(sub) else None


def classify(z_fast: float | None, z_slow: float | None, cut: float) -> str:
    if z_fast is None or z_slow is None or not (math.isfinite(z_fast) and math.isfinite(z_slow)):
        return "neutral"
    if z_fast >= cut and z_slow >= cut:
        return "entering"
    if z_fast <= -cut and z_slow <= -cut:
        return "leaving"
    if z_fast >= cut:
        return "turning_in"
    if z_fast <= -cut:
        return "fading"
    return "neutral"


def _pct(x: Any, digits: int = 1) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x) * 100.0, digits)


def _num(x: Any, digits: int = 2) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), digits)


# ---------------------------------------------------------------------------------------------- the sector table
def sector_table(panel: Panel, fr: Frames, asof: pd.Timestamp, adv: dict[str, float], cfg_get) -> list[dict[str, Any]]:
    cut = float(cfg_get("flows.state_z", 0.5))
    thin_below = int(cfg_get("flows.thin_members", 5))
    close_now = _at(panel.close, asof)
    ret: dict[int, pd.Series] = {}
    for w in (FAST, SLOW):
        then = _at(panel.close, asof - pd.Timedelta(days=w))
        ret[w] = (close_now / then - 1.0) if then is not None else pd.Series(dtype=float)
    mkt_ret = {w: float(ret[w].mean()) if len(ret[w]) else float("nan") for w in ret}
    v7 = _at(_roll(fr.sector_value, FAST, "mean"), asof)
    v90 = _at(_roll(fr.sector_value, NORM, "mean"), asof)
    rows = []
    for s in fr.sectors:
        members = list(panel.sector[panel.sector == s].index)
        zf, zs = _at(fr.z[FAST], asof)[s], _at(fr.z[SLOW], asof)[s]
        r7, r30 = ret[FAST].reindex(members).mean(), ret[SLOW].reindex(members).mean()
        up = ret[SLOW].reindex(members).dropna()
        top = sorted(members, key=lambda i: adv.get(i, 0.0), reverse=True)[:3]
        rt = (v7[s] / v90[s]) if v90[s] and np.isfinite(v90[s]) and v90[s] > 0 else None
        rows.append({
            "name": s, "n": len(members), "thin": len(members) < thin_below, "top": [panel.sym[i] for i in top],
            "adv": _num(sum(adv.get(i, 0.0) for i in members), 0),
            "pressure7": _pct(_at(fr.pvf[FAST], asof)[s]), "pressure30": _pct(_at(fr.pvf[SLOW], asof)[s]),
            "rel7": _pct(_at(fr.rel[FAST], asof)[s]), "rel30": _pct(_at(fr.rel[SLOW], asof)[s]),
            "z7": _num(zf), "z30": _num(zs), "score": _num(None if not (np.isfinite(zf) and np.isfinite(zs)) else (zf + zs) / 2.0),
            "net7": _num(_at(fr.net[FAST], asof)[s] / 1e6, 0), "net30": _num(_at(fr.net[SLOW], asof)[s] / 1e6, 0),  # PKR million
            "ret7": _pct(r7), "ret30": _pct(r30), "rel_ret30": _pct(r30 - mkt_ret[SLOW]) if np.isfinite(mkt_ret[SLOW]) else None,
            "breadth30": _pct((up > 0).mean(), 0) if len(up) else None, "turnover": _num(rt),
            "state": classify(zf, zs, cut),
        })
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0.0)))
    return rows


def market_row(panel: Panel, fr: Frames, asof: pd.Timestamp) -> dict[str, Any]:
    close_now = _at(panel.close, asof)
    out: dict[str, Any] = {"n": int(panel.close.shape[1]), "pressure7": _pct(_at(fr.mkt[FAST], asof)), "pressure30": _pct(_at(fr.mkt[SLOW], asof))}
    for w, k in ((FAST, "ret7"), (SLOW, "ret30")):
        then = _at(panel.close, asof - pd.Timedelta(days=w))
        out[k] = _pct((close_now / then - 1.0).mean()) if then is not None else None
    vm = panel.value.sum(axis=1, min_count=1)
    a, b = _at(_roll(vm, FAST, "mean"), asof), _at(_roll(vm, NORM, "mean"), asof)
    out["turnover"] = _num(a / b) if b and np.isfinite(b) and b > 0 else None
    out["value7"] = _num(a / 1e6, 0) if np.isfinite(a) else None  # PKR million a day
    return out


# ---------------------------------------------------------------------------------------------- candidate shares
def candidates(panel: Panel, asof: pd.Timestamp, sector_state: dict[str, str], funds: dict[str, dict[str, Any]], adv: dict[str, float], bargain_ids: set[str], cfg_get) -> list[dict[str, Any]]:
    top_pct = float(cfg_get("flows.accumulation_top_pct", 66.7))
    max_chase = float(cfg_get("flows.max_chase_pct", 25.0))
    min_growth = float(cfg_get("flows.min_eps_growth_pct", 10.0))
    min_rvol = float(cfg_get("flows.min_rvol", 1.25))
    cap = int(cfg_get("flows.max_candidates", 25))
    p7 = _at(stock_pressure(panel, FAST), asof)
    p30 = _at(stock_pressure(panel, SLOW), asof)
    v7, v90 = _at(_roll(panel.value, FAST, "mean"), asof), _at(_roll(panel.value, NORM, "mean"), asof)
    rvol = (v7 / v90.replace(0.0, np.nan))
    close_now, then = _at(panel.close, asof), _at(panel.close, asof - pd.Timedelta(days=SLOW))
    chg30 = (close_now / then - 1.0) * 100.0 if then is not None else pd.Series(dtype=float)
    cut30 = float(np.nanpercentile(p30.dropna().values, top_pct)) if p30.notna().sum() >= 10 else None
    pct30 = p30.rank(pct=True)
    out = []
    for iid in panel.close.columns:
        f = funds.get(iid) or {}
        a30, a7, c30 = p30.get(iid), p7.get(iid), chg30.get(iid) if len(chg30) else None
        acc = bool(cut30 is not None and a30 is not None and np.isfinite(a30) and a30 >= cut30 and a7 is not None and np.isfinite(a7) and a7 >= 0
                   and (c30 is None or not np.isfinite(c30) or c30 <= max_chase))
        eps, growth, margin = f.get("eps"), f.get("eps_growth_pct"), f.get("net_margin_pct")
        fund = bool(eps is not None and eps > 0 and growth is not None and growth >= min_growth and (margin is None or margin >= 0))
        rv = rvol.get(iid)
        interest = bool(rv is not None and np.isfinite(rv) and rv >= min_rvol)
        n = int(acc) + int(fund) + int(interest)
        if n < 2:
            continue
        sec = str(panel.sector[iid])
        why = []
        if acc:
            why.append(f"30-day buying pressure {a30 * 100:+.0f}% (top third of liquid shares), 7-day {a7 * 100:+.0f}%")
        if fund:
            why.append(f"profit per share up {growth:.0f}% on a year earlier" + (f", net margin {margin:.0f}%" if margin is not None else ""))
        if interest:
            why.append(f"turnover {rv:.1f}x its 90-day norm")
        out.append({
            "id": iid, "sym": panel.sym[iid], "name": panel.name[iid], "sector": sec, "sector_state": sector_state.get(sec),
            "accumulation": acc, "fundamentals": fund, "interest": interest, "pillars": n, "bargain": iid in bargain_ids,
            "pressure7": _pct(a7), "pressure30": _pct(a30), "turnover": _num(rv), "chg30": _num(c30, 1),
            "eps_growth": _num(growth, 0), "roe": _num(f.get("roe"), 1), "pe": _num(f.get("pe"), 1), "adv": _num(adv.get(iid), 0), "why": why,
            "_rank": float(pct30.get(iid, 0.0) or 0.0),
        })
    order = {"entering": 0, "turning_in": 1, "neutral": 2, "fading": 3, "leaving": 4}
    out.sort(key=lambda c: (-c["pillars"], order.get(c["sector_state"] or "neutral", 2), -c["_rank"]))
    for c in out:
        c.pop("_rank", None)
    return out[:cap]


# ---------------------------------------------------------------------------------------------- the weekly report
def iso_week(day: str | dt.date) -> str:
    d = dt.date.fromisoformat(day) if isinstance(day, str) else day
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _names(rows: list[dict[str, Any]], state: str, k: int = 5) -> list[str]:
    return [r["name"] for r in rows if r["state"] == state][:k]


def _join(names: list[str]) -> str:
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else (names[0] if names else "")


def weekly_report(rows: list[dict[str, Any]], cands: list[dict[str, Any]], market: dict[str, Any], prev: dict[str, Any] | None, asof: str) -> dict[str, Any]:
    entering = _names(rows, "entering")
    leaving = [r["name"] for r in reversed(rows) if r["state"] == "leaving"][:5]
    turning = _names(rows, "turning_in", 3)
    fading = [r["name"] for r in reversed(rows) if r["state"] == "fading"][:3]
    benefit = [c for c in cands if c["sector_state"] in ("entering", "turning_in") and c["pillars"] >= 2][:8]
    lines = []
    lines.append(("Money is entering: " + _join(entering) + ".") if entering else "No sector shows buying pressure above the market's over both 7 and 30 days.")
    lines.append(("Money is leaving: " + _join(leaving) + ".") if leaving else "No sector shows selling pressure below the market's over both windows.")
    if turning:
        lines.append("Recently turning in (last 7 days strong, 30 days not yet): " + _join(turning) + ".")
    if fading:
        lines.append("Fading (last 7 days weak after earlier support): " + _join(fading) + ".")
    if benefit:
        lines.append("Shares in those sectors that also pass the screen: " + ", ".join(c["sym"] for c in benefit) + ".")
    elif entering or turning:
        lines.append("No share in the entering sectors passes at least two of the three screen tests this week.")
    if market.get("pressure30") is not None:
        lines.append(f"The whole market: 30-day buying pressure {market['pressure30']:+.1f}%, 7-day {market['pressure7']:+.1f}%" + (f"; turnover {market['turnover']:.2f}x its 90-day norm." if market.get("turnover") else "."))
    changes = []
    if prev:
        before = {n: "entering" for n in prev.get("entering", [])} | {n: "leaving" for n in prev.get("leaving", [])} | {n: "turning_in" for n in prev.get("turning_in", [])}
        for r in rows:
            was, now = before.get(r["name"], "neutral"), r["state"]
            if was != now and (was != "neutral" or now in ("entering", "leaving", "turning_in")):
                changes.append({"sector": r["name"], "from": was, "to": now})
    return {
        "week": iso_week(asof), "asof": asof, "entering": entering, "leaving": leaving, "turning_in": turning, "fading": fading,
        "beneficiaries": [c["id"] for c in benefit], "lines": lines, "changes": changes[:10],
    }


# ---------------------------------------------------------------------------------------------- the weekly archive and its live check
def archive_entry(rep: dict[str, Any], rows: list[dict[str, Any]], market: dict[str, Any]) -> dict[str, Any]:
    return {
        "week": rep["week"], "asof": rep["asof"], "entering": rep["entering"], "leaving": rep["leaving"], "turning_in": rep["turning_in"], "fading": rep["fading"],
        "beneficiaries": rep["beneficiaries"], "score": {r["name"]: r["score"] for r in rows if r["score"] is not None},
        "market": {k: market.get(k) for k in ("pressure7", "pressure30", "ret7", "ret30")},
    }


def update_archive(state: dict[str, Any] | None, entry: dict[str, Any]) -> dict[str, Any]:
    """One entry per ISO week (the newest build of the week replaces the earlier one); the last ``KEEP_WEEKS`` weeks are kept."""
    weeks = [w for w in ((state or {}).get("weeks") or []) if w.get("week") != entry["week"]]
    weeks.append(entry)
    weeks.sort(key=lambda w: w["asof"])
    return {"v": 1, "weeks": weeks[-KEEP_WEEKS:]}


def live_check(state: dict[str, Any], panel: Panel, asof: pd.Timestamp, horizons: tuple[int, ...] = (5, 21)) -> dict[str, Any]:
    """Each archived week's call, scored once enough trading days have passed: the sectors it said were entering against those it said were leaving,
    measured from the close on the day of the call. Only weeks recorded by this app count: nothing is reconstructed after the fact."""
    sectors = sorted(set(panel.sector))
    per_sector = {}
    for h in horizons:
        r = panel.close.shift(-h) / panel.close - 1.0
        per_sector[h] = _mean_by_sector(r, panel, sectors).sub(r.mean(axis=1), axis=0)
    out: dict[str, Any] = {"weeks": len(state.get("weeks") or []), "horizons": {}, "rows": []}
    for h in horizons:
        spreads = []
        for w in state.get("weeks") or []:
            d = pd.Timestamp(w["asof"])
            if d not in panel.dates or panel.dates.get_loc(d) + h >= len(panel.dates) or panel.dates[panel.dates.get_loc(d) + h] > asof:
                continue
            e = [s for s in w["entering"] if s in per_sector[h].columns]
            l = [s for s in w["leaving"] if s in per_sector[h].columns]
            if not e or not l:
                continue
            spreads.append((w["week"], float(per_sector[h].loc[d, e].mean() - per_sector[h].loc[d, l].mean())))
        out["horizons"][str(h)] = {"n": len(spreads), "mean_spread": _pct(np.mean([s for _, s in spreads]), 2) if spreads else None,
                                    "hit": _pct(np.mean([s > 0 for _, s in spreads]), 0) if spreads else None}
        for wk, sp in spreads:
            row = next((r for r in out["rows"] if r["week"] == wk), None)
            if row is None:
                row = {"week": wk}
                out["rows"].append(row)
            row[f"spread{h}"] = _pct(sp, 2)
    out["rows"].sort(key=lambda r: r["week"], reverse=True)
    out["rows"] = out["rows"][:26]
    return out


# ---------------------------------------------------------------------------------------------- everything
def build(
    cfg_get, *, bars: dict[str, pd.DataFrame], shares: list[dict[str, Any]], funds: dict[str, dict[str, Any]], asof: str | None, adv: dict[str, float],
    bargain_ids: set[str], regime: dict[str, Any] | None, macro: dict[str, Any] | None, archive: dict[str, Any] | None, now_iso: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The flows file and the updated weekly archive (``None`` when there was nothing to analyse)."""
    min_members = int(cfg_get("flows.min_sector_members", 3))
    panel = make_panel(bars, shares)
    base: dict[str, Any] = {"generated_at": now_iso, "asof": asof, "windows": {"fast": FAST, "slow": SLOW}, "states": STATE_LABEL, "investor": investor_note()}
    if panel is None or asof is None:
        return {**base, "ready": False, "note": "Not enough price history to measure money flows yet."}, None
    day = pd.Timestamp(asof)
    sectors = eligible_sectors(panel, min_members)
    if len(sectors) < 4:
        return {**base, "ready": False, "note": "Too few sectors with enough liquid shares to compare."}, None
    fr = indicator_frames(panel, sectors)
    rows = sector_table(panel, fr, day, adv, cfg_get)
    market = market_row(panel, fr, day)
    state_of = {r["name"]: r["state"] for r in rows}
    cands = candidates(panel, day, state_of, funds, adv, bargain_ids, cfg_get)
    prev = None
    prior = [w for w in ((archive or {}).get("weeks") or []) if w["week"] != iso_week(asof)]
    if prior:
        prev = prior[-1]
    rep = weekly_report(rows, cands, market, prev, asof)
    new_archive = update_archive(archive, archive_entry(rep, rows, market))
    bt = fb.run(panel, fr, stock_pressure(panel, FAST), stock_pressure(panel, SLOW), cfg_get)
    live = live_check(new_archive, panel, day)
    out = {
        **base, "ready": True, "market": market, "regime": regime, "macro": macro, "sectors": rows, "candidates": cands, "report": rep,
        "weeks": [{k: w[k] for k in ("week", "asof", "entering", "leaving", "turning_in", "fading")} for w in reversed(new_archive["weeks"])][:26],
        "live": live, "backtest": bt, "counts": {"sectors": len(rows), "shares": int(panel.close.shape[1]), "candidates": len(cands),
                                                  "entering": sum(1 for r in rows if r["state"] == "entering"), "leaving": sum(1 for r in rows if r["state"] == "leaving")},
        "thresholds": {"state_z": float(cfg_get("flows.state_z", 0.5)), "min_members": min_members, "min_rvol": float(cfg_get("flows.min_rvol", 1.25)),
                       "min_eps_growth_pct": float(cfg_get("flows.min_eps_growth_pct", 10.0)), "max_chase_pct": float(cfg_get("flows.max_chase_pct", 25.0))},
    }
    return out, new_archive


def investor_note() -> dict[str, Any]:
    return {"available": False, "note": "These flows are measured from price and volume. They cannot say whether foreigners, mutual funds, banks or individuals were buying: "
                                         "that comes from the National Clearing Company's investor-category tables, which this app cannot read."}


def summary(flows: dict[str, Any]) -> dict[str, Any]:
    """The few numbers the home page shows."""
    if not flows.get("ready"):
        return {"ready": False}
    rep = flows["report"]
    bt = flows["backtest"]
    return {"ready": True, "asof": flows["asof"], "entering": rep["entering"][:3], "leaving": rep["leaving"][:3], "candidates": flows["counts"]["candidates"], "verdict": bt.get("verdict")}
