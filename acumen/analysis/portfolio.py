"""Model portfolios built from the actionable ideas, with position sizes, risk statistics and stress tests."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .ideas import GRADE_RISK_MULT
from .instrument import Snapshot

GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}


def _r(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def returns_frame(ids: list[str], snaps: dict[str, Snapshot], window: int = 250) -> pd.DataFrame:
    cols = {i: snaps[i].returns for i in ids if i in snaps and snaps[i].returns is not None}
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols).astype(float)
    return df.iloc[-window:]


def _pair_corr(rets: pd.DataFrame, a: str, b: str, min_obs: int = 60) -> float | None:
    if a not in rets or b not in rets:
        return None
    both = rets[[a, b]].dropna()
    if len(both) < min_obs:
        return None
    c = both[a].corr(both[b])
    return float(c) if np.isfinite(c) else None


def build_profile(name: str, pcfg: dict, ideas: list[dict], snaps: dict[str, Snapshot], cfg: Any, equity_ref: float, usd_pkr: float) -> dict:
    port_cfg = cfg["portfolio"]
    max_pos_pct = float(port_cfg["max_position_pct"])
    max_adv = float(port_cfg["max_pct_of_adv"]) / 100.0
    max_corr = float(port_cfg["max_pair_correlation"])
    min_rank = GRADE_RANK[pcfg["min_grade"]]
    acct_cur = cfg.get_path("app.account_currency", "PKR")
    cands = [i for i in ideas if i["actionable"] and GRADE_RANK.get(i["grade"], 0) >= min_rank and i.get("exp_r") is not None]
    cands.sort(key=lambda i: (i["exp_r"], i["conviction"]), reverse=True)
    rets_all = returns_frame([i["id"] for i in cands[:80]], snaps)
    chosen: list[dict] = []
    heat, sector_n, market_w = 0.0, {}, {}
    for idea in cands:
        if len(chosen) >= int(pcfg["max_positions"]):
            break
        if any(c["id"] == idea["id"] for c in chosen):
            continue
        stop_pct = abs(idea["stop_pct"] or 0.0)
        if stop_pct <= 0:
            continue
        sector = idea["sector"] if idea["market"] == "psx" else None
        if sector and sector_n.get(sector, 0) >= int(pcfg["max_per_sector"]):
            continue
        risk = float(pcfg["risk_per_trade_pct"]) * GRADE_RISK_MULT[idea["grade"]] * (1.0 if idea["calibrated"] else 0.5)
        weight = risk / stop_pct * 100.0
        weight = min(weight, max_pos_pct)
        adv_units = idea["liquidity"].get("max_units_by_adv")
        if adv_units and idea["market"] in ("psx", "crypto"):
            eq_quote = equity_ref if (idea["market"] == "psx" or acct_cur == "USD") else equity_ref / usd_pkr
            weight = min(weight, adv_units * idea["price"] / eq_quote * 100.0)
        risk_eff = weight * stop_pct / 100.0
        if risk_eff < 0.05 or heat + risk_eff > float(pcfg["max_heat_pct"]) + 1e-9:
            continue
        if market_w.get(idea["market"], 0.0) + weight > float(pcfg["max_per_market_pct"]):
            continue
        too_corr = False
        for c in chosen:
            rho = _pair_corr(rets_all, idea["id"], c["id"])
            if rho is not None and rho > max_corr:
                too_corr = True
                break
        if too_corr:
            continue
        chosen.append({
            "id": idea["id"], "sym": idea["sym"], "name": idea["name"], "market": idea["market"], "sector": idea["sector"],
            "horizon": idea["horizon"], "dir": idea["dir"], "conviction": idea["conviction"], "grade": idea["grade"],
            "entry": idea["entry"]["px"], "stop": idea["stop"], "target1": idea["targets"][0]["px"], "target2": idea["targets"][1]["px"],
            "risk_pct": _r(risk_eff, 2), "weight_pct": _r(weight, 1), "rr": idea["rr"], "p_win": idea["p_win"], "exp_r": idea["exp_r"],
        })
        heat += risk_eff
        market_w[idea["market"]] = market_w.get(idea["market"], 0.0) + weight
        if sector:
            sector_n[sector] = sector_n.get(sector, 0) + 1
    stats = _stats(chosen, snaps)
    ids = [c["id"] for c in chosen]
    rets = returns_frame(ids, snaps)
    corr = {"ids": ids, "matrix": []}
    if len(ids) >= 2 and not rets.empty:
        cm = rets.corr(min_periods=60).reindex(index=ids, columns=ids)
        corr["matrix"] = [[_r(cm.loc[a, b], 2) for b in ids] for a in ids]
    elif len(ids) == 1:
        corr["matrix"] = [[1.0]]
    return {
        "label": name.capitalize(), "risk_per_trade_pct": pcfg["risk_per_trade_pct"], "max_heat_pct": pcfg["max_heat_pct"],
        "positions": chosen, "stats": stats, "correlation": corr, "stress": _stress(chosen, snaps, stats),
    }


def _share(chosen: list[dict], key: str) -> dict[str, float]:
    tot = sum(c["weight_pct"] for c in chosen) or 1.0
    out: dict[str, float] = {}
    for c in chosen:
        k = c.get(key) or "Other"
        out[k] = out.get(k, 0.0) + c["weight_pct"]
    return {k: round(v / tot * 100.0, 1) for k, v in out.items()}


def _stats(chosen: list[dict], snaps: dict[str, Snapshot]) -> dict:
    if not chosen:
        return {"n": 0, "heat_pct": 0.0, "exp_r_total": 0.0, "expected_return_pct": 0.0, "worst_case_pct": 0.0, "var95_pct": None, "cvar95_pct": None,
                "diversification_ratio": None, "by_market": {}, "by_sector": {}, "by_horizon": {}}
    heat = sum(c["risk_pct"] for c in chosen)
    exp_r_total = sum(c["exp_r"] for c in chosen)
    exp_ret = sum(c["exp_r"] * c["risk_pct"] for c in chosen)
    ids = [c["id"] for c in chosen]
    rets = returns_frame(ids, snaps).fillna(0.0)
    var95 = cvar95 = div = None
    if not rets.empty and len(rets) >= 60:
        w = pd.Series({c["id"]: c["weight_pct"] / 100.0 * (1 if c["dir"] == "long" else -1) for c in chosen})
        port = (rets[w.index] * w).sum(axis=1)
        q = np.percentile(port, 5)
        var95, cvar95 = float(q * 100.0), float(port[port <= q].mean() * 100.0)
        sig_i = rets[w.index].std(ddof=1) * w.abs()
        sig_p = port.std(ddof=1)
        div = float(sig_i.sum() / sig_p) if sig_p > 0 else None
    return {
        "n": len(chosen), "heat_pct": _r(heat, 2), "exp_r_total": _r(exp_r_total, 2), "expected_return_pct": _r(exp_ret, 2),
        "worst_case_pct": _r(-heat, 2), "var95_pct": _r(var95, 2), "cvar95_pct": _r(cvar95, 2), "diversification_ratio": _r(div, 2),
        "by_market": _share(chosen, "market"), "by_sector": _share(chosen, "sector"), "by_horizon": _share(chosen, "horizon"),
    }


def _stress(chosen: list[dict], snaps: dict[str, Snapshot], stats: dict) -> list[dict]:
    def shock(market: str, move_pct: float) -> float:
        tot = 0.0
        for c in chosen:
            if c["market"] != market:
                continue
            beta = snaps[c["id"]].row.get("beta")
            beta = 1.0 if beta is None else beta
            tot += c["weight_pct"] / 100.0 * beta * move_pct * (1 if c["dir"] == "long" else -1)
        return tot
    out = [
        {"name": "KSE-100 falls 10%", "pnl_pct": _r(shock("psx", -10.0), 2), "detail": "Each PSX holding moves by its beta to the KSE-100 (stops may limit the loss)"},
        {"name": "Bitcoin falls 25%", "pnl_pct": _r(shock("crypto", -25.0), 2), "detail": "Each crypto holding moves by its beta to Bitcoin"},
        {"name": "Every stop is hit", "pnl_pct": _r(-(stats.get("heat_pct") or 0.0), 2), "detail": "Sum of the money at risk across positions; gaps can be worse"},
    ]
    return out


def build_portfolios(ideas: list[dict], snaps: dict[str, Snapshot], cfg: Any, usd_pkr: float) -> dict:
    equity = float(cfg.get_path("app.default_equity", 1_000_000))
    profiles = {name: build_profile(name, p, ideas, snaps, cfg, equity, usd_pkr) for name, p in cfg["portfolio"]["profiles"].items()}
    return {
        "equity_reference": equity, "profiles": profiles,
        "notes": ["Sizes assume every stop is hit at its level; gaps can be worse.",
                  "Weights and risk are for the reference equity. The app recomputes them for your own equity and risk setting."],
    }
