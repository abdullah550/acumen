"""Pass 2: turn snapshots into trade ideas using pooled calibration, fundamentals and market context."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import texts as tx
from .calibrate import blend_with_own, predict
from .factors import conviction_from_score, grade_from_conviction
from .fundamental import HORIZON_CARRY_WEIGHT, HORIZON_FUND_WEIGHT, tilt_points
from .instrument import CAL_DAYS, HORIZONS, Snapshot, _dec

CURRENCY = {"psx": "PKR", "crypto": "USD"}
GRADE_RISK_MULT = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.25}


def money_str(v: float | None, cur: str = "") -> str:
    if v is None or not math.isfinite(v):
        return "n/a"
    a = abs(v)
    s = f"{v / 1e9:.1f}b" if a >= 1e9 else f"{v / 1e6:.1f}m" if a >= 1e6 else f"{v / 1e3:.0f}k" if a >= 1e3 else f"{v:.0f}"
    return f"{cur} {s}".strip()


def _r(x, nd=2):
    return None if x is None or not math.isfinite(float(x)) else round(float(x), nd)


def _bucket_label(conv: float) -> str:
    lo = int(min(90, max(30, (conv // 10) * 10)))
    return f"{lo}-{lo + 10}"


def edge_warning(edge: dict) -> str:
    """Why this pool's ideas are research candidates: it has not been shown to beat random entries with the same exits."""
    why = edge.get("why") or "not tested"
    r = edge.get("edge_r")
    if not edge.get("tested"):
        return f"Acumen has not shown that signals in this market and horizon beat random entries: {why}. Treat this as a research candidate, not a validated trade."
    if r is not None and r <= 0:
        return (f"Random entries with the same exits did as well or better in this period (signals {r:+.2f}R against them), so past results here are mostly the market "
                "drifting rather than skill. Research candidate only.")
    if "newer" in why:
        return "Signals beat random entries over the whole history but not in the newer trades, so the edge may have faded. Research candidate only."
    return f"Signals did better than random entries by {r:+.2f}R per trade, but not by enough to rule out luck. Research candidate only."


def make_idea(
    snap: Snapshot,
    h: str,
    cal: dict | None,
    fund: dict | None,
    val: dict | None,
    carry_pct: float | None,
    cfg: Any,
    ctx: dict,
    bench_name: str | None,
    usd_pkr: float,
) -> dict | None:
    """The full idea object (see docs/DATA_CONTRACT.md) for one instrument and horizon, or ``None``."""
    hr = snap.horizons[h]
    if not snap.enough_history or hr.comp is None or hr.raw_dir == 0 or hr.plan is None or hr.conv is None:
        return None
    scfg, pcfg = cfg["signals"], cfg["plan"][h]
    market = snap.market
    sign = hr.plan.sign
    plan = hr.plan
    dec = _dec(snap.last)
    cal_days = CAL_DAYS[market]

    # conviction = technical conviction + bounded tilt from things that cannot be backtested
    f_score, tilt, tilt_why = None, 0.0, None
    if market == "psx" and fund and fund.get("scores", {}).get("composite") is not None:
        f_score = float(fund["scores"]["composite"])
        tilt = tilt_points(f_score, sign, h, float(scfg["max_tilt_points"]), HORIZON_FUND_WEIGHT)
    carry_score = None
    if market == "fx" and carry_pct is not None:
        carry_score = math.tanh(carry_pct / 4.0)
        tilt = tilt_points(carry_score, sign, h, float(scfg["max_tilt_points"]), HORIZON_CARRY_WEIGHT)
    conv = float(np.clip(hr.conv + tilt, 0.0, 100.0))
    grade = grade_from_conviction(conv)
    if grade == "D":
        return None
    calibrated = bool(cal and cal.get("calibrated"))
    if grade == "A" and not calibrated:
        grade = "B"  # an A needs out-of-sample evidence behind it

    pred = predict(cal, hr.conv)
    k = float(cfg.get_path("backtest.shrink_k", 100))
    n_own = int(hr.stats.get("n") or 0)
    p_win = blend_with_own(pred["p_win"], hr.stats.get("win"), n_own, k)
    exp_r = blend_with_own(pred["exp_r"], hr.stats.get("avg_r"), n_own, k)

    entry_ref = plan.ref if plan.kind == "limit" else snap.last
    atr = snap.atr or 0.0
    t1, t2 = plan.targets
    min_rr = float(cfg.get_path("plan.min_rr", 1.5))
    chase = (t1 + min_rr * plan.stop) / (1.0 + min_rr)  # worst entry that still leaves min_rr to target 1
    so = pcfg["scale_out"]
    # R actually offered at each target (a short's far target can be clamped above zero, so it can be below nominal)
    risk_dist = abs(entry_ref - plan.stop) or 1e-12
    r_act = [abs(tp - entry_ref) / risk_dist for tp in plan.targets]
    rr = float(sum(f * r for f, r in zip(so, r_act)))
    wait = int(pcfg["entry_wait_bars"])
    if plan.kind == "market":
        if sign > 0:
            low, high = entry_ref - 0.3 * atr, min(entry_ref + 0.3 * atr, chase)
            skip = f"Skip it if it opens above {tx.px_str(chase)} (reward to risk would drop below {min_rr:.1f})."
        else:
            low, high = max(entry_ref - 0.3 * atr, chase), entry_ref + 0.3 * atr
            skip = f"Skip it if it opens below {tx.px_str(chase)} (reward to risk would drop below {min_rr:.1f})."
        entry = {"type": "market", "px": _r(entry_ref, dec), "low": _r(low, dec), "high": _r(high, dec), "valid_bars": 1,
                 "note": f"{'Buy' if sign > 0 else 'Sell short'} at the next daily open, around {tx.px_str(entry_ref)}. " + skip}
    else:
        low, high = (entry_ref - 0.5 * atr, entry_ref) if sign > 0 else (entry_ref, entry_ref + 0.5 * atr)
        entry = {"type": "limit", "px": _r(entry_ref, dec), "low": _r(low, dec), "high": _r(high, dec), "valid_bars": wait,
                 "note": f"Place a limit order at {tx.px_str(entry_ref)} for up to {wait} sessions. Cancel it if price reaches {tx.px_str(t1)} first or closes {'below' if sign > 0 else 'above'} {tx.px_str(plan.stop)}."}
    rdist = abs(entry_ref - plan.stop)
    eta = (cal or {}).get("eta_bars", {"t1": [5, 20], "t2": [10, 40]})
    ts_days = int(round(int(cfg["horizons"][h]["time_stop_bars"]) * cal_days))
    targets = []
    for i, (tp, r_mult, frac) in enumerate(zip(plan.targets, r_act, so)):
        key = "t1" if i == 0 else "t2"
        lo_b, hi_b = eta.get(key, [5, 20])
        targets.append({
            "px": _r(tp, dec), "r": round(float(r_mult), 2), "pct": _r((tp / entry_ref - 1.0) * 100.0, 1),
            "prob": _r(pred["p_t1" if i == 0 else "p_t2"], 3),
            "eta_days": [int(round(lo_b * cal_days)), int(round(hi_b * cal_days))], "scale_out": float(frac),
        })
    exp_pct = None if exp_r is None else _r(exp_r * rdist / entry_ref * 100.0, 1)

    # where does the trade sit in the liquidity picture
    adv = snap.liquidity.get("adv_value")
    max_pct = float(cfg.get_path("portfolio.max_pct_of_adv", 10))
    max_units = None if not adv else int(adv * max_pct / 100.0 / snap.last) if snap.last > 0 else None
    tier = snap.liquidity.get("tier")

    # actionability
    blockers: list[str] = []
    tradable_dir = (hr.dir != 0)
    if not tradable_dir:
        blockers.append("short_psx")
    if not snap.tradable:
        blockers.append("not_tradable")
    if ctx.get("stale"):
        blockers.append("stale")
    if tier == "D" and market != "fx":
        blockers.append("illiquid")
    edge = (cal or {}).get("edge") or {}
    if not edge.get("passed"):
        blockers.append("no_edge")  # the pool has not shown that its signals beat random entries: everything below is then only a base rate
    if exp_r is None:
        blockers.append("no_evidence")
    else:
        # the instrument's own record may lower the pooled expectation, never lift it over the line
        gate = exp_r if pred["exp_r"] is None else min(exp_r, pred["exp_r"])
        if gate <= float(scfg.get("min_exp_r", 0.02)):
            blockers.append("negative_expectancy")
    if rr < min_rr:
        blockers.append("rr")
    max_stop_pct = float(cfg.get_path(f"plan.max_stop_pct.{market}", {"psx": 25.0, "fx": 8.0, "crypto": 40.0}[market]))
    if entry_ref > 0 and abs(plan.stop / entry_ref - 1.0) * 100.0 > max_stop_pct:
        blockers.append("wide_stop")
    actionable = not blockers
    if not tradable_dir:
        action = "AVOID"
    elif not snap.tradable:
        action = "AVOID"
    else:
        action = "BUY" if sign > 0 else "SELL"

    # narrative
    L = snap.last_feat
    reasons = tx.reasons(L, hr.factors, sign, hr.setup or "", h, bench_name, snap.levels.get("support"), snap.levels.get("resistance"), snap.pattern_name, limit=4)
    warnings: list[str] = []
    if "short_psx" in blockers:
        warnings.append("Short selling is not practical for retail investors on PSX: read this as a reason to avoid or trim a long position, not a trade to take.")
    if "not_tradable" in blockers and market == "fx":
        warnings.append("This pair is not offered by most retail brokers; shown for information.")
    elif "not_tradable" in blockers:
        warnings.append("This instrument cannot be traded directly.")
    if ctx.get("stale"):
        warnings.append(f"Latest data for this market is {ctx.get('stale_days', '?')} days old. Do not act on old levels.")
    if tier in ("C", "D") and market != "fx":
        warnings.append(f"Thin trading: median daily value {money_str(adv, CURRENCY.get(market, ''))}. Expect slippage and keep size small.")
    if "approx_hl" in snap.flags:
        warnings.append("Recent high and low prices are approximated from open and close; ATR and stop distance may be understated.")
    stop_pct = (plan.stop / entry_ref - 1.0) * 100.0
    wide = {"psx": 12.0, "crypto": 20.0, "fx": 3.0}[market]
    if abs(stop_pct) > wide:
        warnings.append(f"Wide stop ({abs(stop_pct):.0f}% from entry): size the position small so the money at risk stays fixed.")
    gapf = snap.detail["risk"].get("gap_freq")
    if gapf is not None and gapf >= 8:
        warnings.append(f"Frequent gaps ({gapf:.0f}% of sessions open far from the previous close): a stop can fill worse than its level.")
    if market == "psx" and (snap.row.get("atrp") or 0) >= 3.0:
        warnings.append("PSX daily price limits (7.5%) can stop you from exiting on extreme days.")
    res, sup = snap.levels.get("resistance"), snap.levels.get("support")
    if sign > 0 and res is not None and entry_ref < res < t1:
        warnings.append(f"Resistance at {tx.px_str(res)} sits before target 1 ({tx.px_str(t1)}).")
    if sign < 0 and sup is not None and t1 < sup < entry_ref:
        warnings.append(f"Support at {tx.px_str(sup)} sits before target 1 ({tx.px_str(t1)}).")
    if n_own == 0:
        warnings.append("No earlier trades of this kind on this instrument; the estimate comes from the pooled history only.")
    elif not hr.stats.get("reliable"):
        warnings.append(f"Only {n_own} earlier trades on this instrument at this horizon; the pooled history carries most of the weight.")
    status = (cal or {}).get("status", "insufficient")
    if status == "unvalidated":
        warnings.append("Conviction has not ranked outcomes reliably out of sample for this market and horizon, so probabilities are pooled base rates.")
    elif status == "insufficient":
        warnings.append("Not enough pooled trades yet to calibrate; probabilities are rough base rates.")
    if "no_edge" in blockers:
        warnings.append(edge_warning(edge))
    if "wide_stop" in blockers:
        warnings.append("The stop would have to sit too far from the entry for this to be a sensible plan: volatility is too high for this horizon.")
    if "negative_expectancy" in blockers:
        warnings.append("Historical expected result is not positive after costs for signals like this one.")
    if market == "psx" and fund is None and snap.kind == "equity":
        warnings.append("Fundamentals were not available for this company.")
    if val and sign > 0:
        up = val.get("upside_pct")
        if val.get("verdict") == "undervalued" and val.get("confidence") in ("medium", "high"):
            reasons.append(f"Valuation models put fair value near {tx.px_str(val['fair_value'])} ({up:+.0f}% from here)")
        elif val.get("verdict") == "overvalued":
            warnings.append(f"Valuation models put fair value {abs(up):.0f}% below the price ({tx.px_str(val['fair_value'])}).")
    if fund and sign > 0 and market == "psx":
        dy = (fund.get("metrics") or {}).get("dy")
        if dy is not None and dy >= 6:
            reasons.append(f"Dividend yield {dy:.1f}%")
    if carry_pct is not None and market == "fx":
        c = sign * carry_pct
        if c >= 0.5:
            reasons.append(f"Carry works for this direction: {c:+.2f}% a year from the rate difference")
        elif c <= -1.0:
            warnings.append(f"Negative carry: holding this direction costs about {abs(c):.2f}% a year in interest.")

    bt = hr.stats
    base_risk = float(cfg.get_path("portfolio.profiles.balanced.risk_per_trade_pct", 1.0))
    risk_suggest = base_risk * GRADE_RISK_MULT[grade] * (1.0 if calibrated else 0.5)
    fac = {k2: _r(v, 2) for k2, v in hr.factors.items()}
    fac["fundamental"] = _r(f_score, 2)
    fac["carry"] = _r(carry_score, 2)
    return {
        "id": snap.id, "sym": snap.sym, "name": snap.name, "market": market, "sector": snap.sector, "kind": snap.kind,
        "horizon": h, "dir": "long" if sign > 0 else "short", "action": action, "actionable": bool(actionable),
        "setup": hr.setup, "conviction": int(round(conv)), "grade": grade, "score": _r(hr.comp, 2), "calibrated": calibrated,
        "asof": snap.asof, "price": _r(snap.last, dec),
        "entry": entry, "stop": _r(plan.stop, dec), "stop_pct": _r(stop_pct, 1), "risk_per_unit": _r(rdist, dec + 1),
        "targets": targets, "time_stop_days": ts_days,
        "horizon_days": [int(cfg["horizons"][h]["min_days"]), int(cfg["horizons"][h]["max_days"])],
        "rr": _r(rr, 2), "p_win": _r(p_win, 3), "exp_r": _r(exp_r, 2), "exp_pct": exp_pct,
        "risk_pct_suggest": _r(risk_suggest, 2),
        "reasons": reasons[:6], "warnings": warnings, "factors": fac, "tilt": _r(tilt, 1),
        "backtest": {"n": int(bt.get("n") or 0), "win": bt.get("win"), "avg_r": bt.get("avg_r"), "pf": bt.get("pf"), "max_dd_r": bt.get("max_dd_r"),
                     "avg_bars": bt.get("avg_bars"), "reliable": bool(bt.get("reliable")), "oos": bt.get("oos")},
        "evidence": {"pool_n": (cal or {}).get("n_trades", 0), "pool_win": (cal or {}).get("win"), "pool_avg_r": (cal or {}).get("avg_r"),
                     "bucket": _bucket_label(hr.conv), "status": status, "skill": bool(edge.get("passed")), "edge_r": edge.get("edge_r")},
        "liquidity": {"adv_value": _r(adv, 0), "tier": tier, "max_units_by_adv": max_units},
        "blockers": blockers,
    }


def fill_rows(snaps: list[Snapshot], ideas: list[dict], fund_blocks: dict[str, dict], carry: dict[str, float | None], cfg: Any, ctx: dict[str, dict]) -> None:
    """Complete the screener rows in place: signals per horizon, best idea, fundamentals, flags."""
    by_id: dict[str, list[dict]] = {}
    for i in ideas:
        by_id.setdefault(i["id"], []).append(i)
    for s in snaps:
        row = s.row
        mine = {i["horizon"]: i for i in by_id.get(s.id, [])}
        for h in HORIZONS:
            hr = s.horizons[h]
            if hr.comp is None or hr.raw_dir == 0:
                row["sig"][h] = {"dir": None, "conv": None if hr.comp is None else int(round(conviction_from_score(abs(hr.comp)))), "grade": None, "score": None if hr.comp is None else round(hr.comp, 2)}
                continue
            idea = mine.get(h)
            conv = idea["conviction"] if idea else int(round(hr.conv))
            grade = idea["grade"] if idea else grade_from_conviction(conv)
            row["sig"][h] = {"dir": "long" if hr.raw_dir > 0 else "short", "conv": conv, "grade": grade, "score": round(hr.comp, 2)}
        if mine:
            top = max(mine.values(), key=lambda i: (i["actionable"], i["conviction"]))
            row["best"] = {"h": top["horizon"], "dir": top["dir"], "conv": top["conviction"], "grade": top["grade"]}
        fb = fund_blocks.get(s.id) or {}
        fu = fb.get("fundamental")
        if fu:
            m = fu.get("metrics", {})
            for key in ("pe", "pb", "dy", "roe", "eps", "mcap"):
                row[key] = m.get(key)
            if s.market == "crypto":
                row["rank"] = m.get("rank")
        va = fb.get("valuation")
        if va:
            row["fv"], row["upside"] = va["fair_value"], va["upside_pct"]
        if s.market == "fx":
            row["carry"] = None if carry.get(s.id) is None else round(carry[s.id], 2)
        c = ctx.get(s.market, {})
        if c.get("stale") and "stale" not in row["flags"]:
            row["flags"].append("stale")
        if s.market == "psx" and s.kind == "equity" and not fu and "no_fundamentals" not in row["flags"]:
            row["flags"].append("no_fundamentals")


def summary(idea: dict) -> dict:
    """The compact form used in overview.top_ideas."""
    keys = ("id", "sym", "name", "market", "horizon", "dir", "action", "actionable", "setup", "conviction", "grade", "price", "rr", "p_win", "exp_r")
    out = {k: idea[k] for k in keys}
    out["entry"] = idea["entry"]["px"]
    out["stop"] = idea["stop"]
    out["targets"] = [t["px"] for t in idea["targets"]]
    out["eta"] = idea["targets"][0]["eta_days"]
    return out
