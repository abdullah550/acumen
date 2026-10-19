"""Fundamental scores, valuation models and the conviction tilt they produce.

Fundamentals cannot be back-tested point in time with free data, so they never enter the backtest.
They become a bounded tilt on live conviction (``signals.max_tilt_points``) and are shown separately.

Normalised keys expected in ``MarketData.fundamentals[id]`` (missing keys are fine):
  PSX equities: pe, pb, eps, dy (dividend yield, %), roe (%), bvps, mcap (PKR), shares, free_float_pct,
                payout_pct, eps_growth_pct, net_margin_pct, dividends [{date, amount}], eps_hist [{period, value}]
  Crypto:       mcap, rank, volume_24h, circ_supply, max_supply, ath, ath_date, cg_id
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .texts import ok

HORIZON_FUND_WEIGHT = {"short": 0.25, "medium": 0.6, "long": 1.0}
HORIZON_CARRY_WEIGHT = {"short": 0.15, "medium": 0.5, "long": 1.0}
SCORE_WEIGHTS = {"value": 0.30, "quality": 0.25, "income": 0.15, "growth": 0.15, "safety": 0.15}


def _pos(x: Any) -> float | None:
    return float(x) if ok(x) and float(x) > 0 else None


def _num(x: Any) -> float | None:
    return float(x) if ok(x) else None


def _mean(vals: list[float | None]) -> float | None:
    v = [x for x in vals if x is not None and math.isfinite(x)]
    return float(np.mean(v)) if v else None


def _median(vals: list[float]) -> float | None:
    v = [x for x in vals if math.isfinite(x)]
    return float(np.median(v)) if len(v) >= 1 else None


def sector_stats(funds: dict[str, dict], sectors: dict[str, str | None]) -> dict[str, dict]:
    """Median pe / pb / dy / roe per sector plus an ``"*"`` entry over everything. ``n`` = members with a P/E."""
    groups: dict[str, list[dict]] = {"*": []}
    for i, f in funds.items():
        groups["*"].append(f)
        sec = sectors.get(i)
        if sec:
            groups.setdefault(sec, []).append(f)
    out = {}
    for sec, members in groups.items():
        pe = [float(m["pe"]) for m in members if _pos(m.get("pe")) and float(m["pe"]) < 200]
        pb = [float(m["pb"]) for m in members if _pos(m.get("pb")) and float(m["pb"]) < 50]
        dy = [float(m["dy"]) for m in members if ok(m.get("dy")) and float(m["dy"]) >= 0]
        roe = [float(m["roe"]) for m in members if ok(m.get("roe"))]
        out[sec] = {"pe": _median(pe), "pb": _median(pb), "dy": _median(dy), "roe": _median(roe), "n": len(pe)}
    return out


def _ref(stats: dict[str, dict], sector: str | None, key: str, min_n: int = 4) -> float | None:
    s = stats.get(sector or "", None)
    if s and s.get(key) is not None and s.get("n", 0) >= min_n:
        return s[key]
    return stats.get("*", {}).get(key)


def dividend_years(dividends: list[dict] | None, asof_year: int, span: int = 5) -> int:
    years = set()
    for d in dividends or []:
        try:
            years.add(int(str(d.get("date", ""))[:4]))
        except ValueError:
            continue
    return sum(1 for y in range(asof_year - span + 1, asof_year + 1) if y in years)


def psx_fundamental(inst_id: str, f: dict, sector: str | None, stats: dict[str, dict], mcap_pct: float | None, vol30: float | None, vol30_med: float | None, asof_year: int) -> dict | None:
    """Scores and metrics for one PSX equity, or ``None`` when there is nothing to score."""
    pe, pb, eps, dy, roe = _pos(f.get("pe")), _pos(f.get("pb")), _num(f.get("eps")), _num(f.get("dy")), _num(f.get("roe"))
    bvps, mcap = _pos(f.get("bvps")), _pos(f.get("mcap"))
    payout, epsg, margin = _num(f.get("payout_pct")), _num(f.get("eps_growth_pct")), _num(f.get("net_margin_pct"))
    have = [x is not None for x in (pe, pb, eps, dy, roe, bvps, mcap, payout, epsg, margin)]
    if sum(have) < 2:
        return None
    pe_ref, pb_ref, dy_ref, roe_ref = (_ref(stats, sector, k) for k in ("pe", "pb", "dy", "roe"))
    flags: list[str] = []
    v1 = -math.tanh(math.log(pe / pe_ref) / 0.5) if (pe and pe_ref and pe < 200) else None
    if eps is not None and eps <= 0:
        v1 = -0.8
        flags.append("Loss-making (EPS is not positive)")
    v2 = -math.tanh(math.log(pb / pb_ref) / 0.5) if (pb and pb_ref) else None
    v3 = math.tanh(((dy or 0.0) - (dy_ref or 4.0)) / 4.0) if dy is not None else None
    value = _mean([v1, v2, v3])
    q1 = math.tanh((roe - 15.0) / 10.0) if roe is not None else None
    q2 = math.tanh((margin - 8.0) / 8.0) if margin is not None else None
    q3 = (0.3 if (eps is not None and eps > 0) else -1.0) if eps is not None else None
    quality = _mean([q1, q2, q3])
    years = dividend_years(f.get("dividends"), asof_year)
    i1 = math.tanh((dy - 5.0) / 3.0) if dy is not None else None
    i2 = None
    if payout is not None:
        i2 = 0.3 if 0 < payout <= 90 else (-0.5 if payout > 100 else 0.0)
    i3 = (0.4 if years >= 4 else 0.2 if years >= 2 else -0.2) if f.get("dividends") is not None else None
    income = _mean([i1, i2, i3])
    growth = math.tanh(epsg / 20.0) if epsg is not None else None
    s1 = (2.0 * mcap_pct - 1.0) if mcap_pct is not None else None
    s2 = -math.tanh((vol30 - vol30_med) / 15.0) if (vol30 is not None and vol30_med is not None) else None
    s3 = (0.3 if (eps is not None and eps > 0) else -0.6) if eps is not None else None
    safety = _mean([s1, s2, s3])
    scores = {"value": value, "quality": quality, "income": income, "growth": growth, "safety": safety}
    avail = {k: v for k, v in scores.items() if v is not None}
    if not avail:
        return None
    wsum = sum(SCORE_WEIGHTS[k] for k in avail)
    composite = sum(SCORE_WEIGHTS[k] * v for k, v in avail.items()) / wsum
    if pb is not None and pb < 1.0:
        flags.append(f"Trades below book value (P/B {pb:.2f})")
    if pe is not None and pe_ref:
        flags.append(f"P/E {pe:.1f} vs {'sector' if (stats.get(sector or '') or {}).get('n', 0) >= 4 else 'market'} median {pe_ref:.1f}")
    if years >= 4:
        flags.append(f"Dividend paid in {years} of the last 5 years")
    if payout is not None and payout > 100:
        flags.append(f"Payout ratio {payout:.0f}% exceeds earnings")
    coverage = round(len(avail) / len(SCORE_WEIGHTS) * 0.5 + (sum(have) / len(have)) * 0.5, 2)
    rel = lambda a, b: None if (a is None or not b) else round(a / b - 1.0, 3)
    return {
        "metrics": {
            "pe": _r(pe), "pb": _r(pb), "dy": _r(dy), "roe": _r(roe), "eps": _r(eps), "bvps": _r(bvps), "mcap": _r(mcap, 0),
            "payout_pct": _r(payout, 1), "eps_growth_pct": _r(epsg, 1), "net_margin_pct": _r(margin, 1),
        },
        "sector_rel": {"pe_vs_sector": rel(pe, pe_ref), "pb_vs_sector": rel(pb, pb_ref), "dy_vs_sector": rel(dy, dy_ref), "roe_vs_sector": rel(roe, roe_ref)},
        "scores": {**{k: _r(v, 2) for k, v in scores.items()}, "composite": round(float(composite), 2)},
        "flags": flags,
        "coverage": coverage,
        "history": {"dividends": (f.get("dividends") or [])[-8:], "eps": (f.get("eps_hist") or [])[-6:]},
    }


def _r(x, nd: int = 2):
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def valuation(f: dict, price: float, beta: float | None, sector: str | None, stats: dict[str, dict], vcfg: dict) -> dict | None:
    """Intrinsic value estimates for a PSX equity from a few simple models, each shown with its inputs.

    Models are deliberately plain (they are cross-checks, not forecasts). A model far outside the price range
    (beyond 4x or below 0.25x) is down-weighted. Returns ``None`` when no model can be computed.
    """
    eps, bvps, dy, roe = _num(f.get("eps")), _pos(f.get("bvps")), _num(f.get("dy")), _num(f.get("roe"))
    payout = _num(f.get("payout_pct"))
    if not ok(price) or price <= 0:
        return None
    rf, erp, g_cap = float(vcfg["risk_free_pkr"]), float(vcfg["equity_risk_premium"]), float(vcfg["terminal_growth"])
    b = float(np.clip(beta if (beta is not None and math.isfinite(beta)) else 1.0, 0.6, 1.6))
    ke = rf + b * erp
    pe_ref = _ref(stats, sector, "pe") or float(vcfg["fair_pe_default"])
    pe_ref = float(np.clip(pe_ref, 3.0, 15.0))
    models: list[dict] = []
    notes = [f"Cost of equity assumed {ke * 100:.0f}% (risk-free {rf * 100:.0f}% + beta {b:.1f} x premium {erp * 100:.0f}%)"]
    if eps and eps > 0 and bvps:
        models.append({"name": "Graham number", "value": math.sqrt(22.5 * eps * bvps), "weight": 0.15, "note": f"sqrt(22.5 x EPS {eps:.1f} x BVPS {bvps:.1f})"})
    if eps and eps > 0:
        models.append({"name": "Sector P/E", "value": eps * pe_ref, "weight": 0.30, "note": f"EPS {eps:.1f} x peer median P/E {pe_ref:.1f}"})
        models.append({"name": "Earnings power", "value": eps / ke, "weight": 0.10, "note": f"EPS {eps:.1f} / cost of equity {ke * 100:.0f}% (no growth)"})
    dps = (dy / 100.0) * price if (dy is not None and dy > 0) else None
    if dps and ke > g_cap:
        sust = (roe / 100.0) * (1.0 - min(payout or 50.0, 100.0) / 100.0) if (roe is not None and roe > 0) else g_cap
        g = float(np.clip(sust, 0.0, g_cap))
        models.append({"name": "Dividend discount", "value": dps * (1 + g) / (ke - g), "weight": 0.20, "note": f"DPS {dps:.1f} growing {g * 100:.0f}% at {ke * 100:.0f}% required return"})
    if roe is not None and roe > 0 and bvps and ke > g_cap:
        fair_pb = float(np.clip((roe / 100.0 - g_cap) / (ke - g_cap), 0.3, 3.0))
        models.append({"name": "Justified P/B", "value": bvps * fair_pb, "weight": 0.25, "note": f"BVPS {bvps:.1f} x (ROE {roe:.0f}% - g) / (ke - g) = {fair_pb:.2f}x"})
    models = [m for m in models if math.isfinite(m["value"]) and m["value"] > 0]
    if not models:
        return None
    for m in models:
        ratio = m["value"] / price
        if ratio > 4.0 or ratio < 0.25:
            m["weight"] *= 0.25
            m["note"] += " (far from price, down-weighted)"
    wsum = sum(m["weight"] for m in models)
    fv = sum(m["value"] * m["weight"] for m in models) / wsum
    vals = np.array([m["value"] for m in models])
    wts = np.array([m["weight"] for m in models])
    mean = fv
    sd = float(np.sqrt(np.sum(wts * (vals - mean) ** 2) / wts.sum()))
    cv = sd / mean if mean > 0 else 9.9
    low, high = max(0.0, fv - sd), fv + sd
    upside = (fv / price - 1.0) * 100.0
    mos = (fv - price) / fv if fv > 0 else -1.0
    mos_buy = float(vcfg["margin_of_safety_buy"])
    verdict = "undervalued" if mos >= mos_buy else "overvalued" if mos <= -mos_buy else "fair"
    conf = "high" if (len(models) >= 4 and cv < 0.25) else "medium" if (len(models) >= 2 and cv < 0.5) else "low"
    return {
        "models": [{"name": m["name"], "value": round(m["value"], 2), "weight": round(m["weight"] / wsum, 2), "note": m["note"]} for m in models],
        "fair_value": round(fv, 2), "low": round(low, 2), "high": round(high, 2), "upside_pct": round(upside, 1),
        "margin_of_safety_pct": round((fv - price) / fv * 100.0, 1), "verdict": verdict, "confidence": conf, "notes": notes,
    }


def crypto_fundamental(f: dict, price: float) -> dict | None:
    mcap, rank, vol = _pos(f.get("mcap")), _num(f.get("rank")), _num(f.get("volume_24h"))
    circ, mx = _pos(f.get("circ_supply")), _pos(f.get("max_supply"))
    ath = _pos(f.get("ath"))
    if mcap is None and ath is None:
        return None
    flags = []
    vol_mcap = (vol / mcap) if (vol is not None and mcap) else None
    circ_pct = (circ / mx * 100.0) if (circ and mx) else None
    ath_dd = (price / ath - 1.0) * 100.0 if ath else None
    if circ_pct is not None and circ_pct < 60:
        flags.append(f"Only {circ_pct:.0f}% of the maximum supply is circulating (future unlocks can dilute holders)")
    if vol_mcap is not None and vol_mcap < 0.01:
        flags.append("Daily volume is under 1% of market cap (thin trading)")
    if ath_dd is not None and ath_dd < -80:
        flags.append(f"{abs(ath_dd):.0f}% below its all-time high")
    return {
        "metrics": {"mcap": _r(mcap, 0), "rank": None if rank is None else int(rank), "vol_mcap": _r(vol_mcap, 3), "circ_supply_pct": _r(circ_pct, 1), "ath_dd_pct": _r(ath_dd, 1), "age_days": _r(_num(f.get("age_days")), 0)},
        "sector_rel": {}, "scores": {}, "flags": flags, "coverage": 0.5, "history": {},
    }


def fx_carry(base: str, quote: str, rates: dict[str, float]) -> float | None:
    """Annualised interest differential in percent for being long base / short quote (None if a rate is unknown)."""
    if base in rates and quote in rates:
        return float(rates[base]) - float(rates[quote])
    return None


def tilt_points(score: float | None, direction: int, horizon: str, max_points: float, weights: dict[str, float]) -> float:
    """Conviction points added to a trade in ``direction`` from a signed fundamental/carry ``score`` in [-1, 1]."""
    if score is None or not math.isfinite(score) or direction == 0:
        return 0.0
    return float(np.clip(direction * score, -1.0, 1.0) * max_points * weights[horizon])
