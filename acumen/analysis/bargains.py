"""Bargains: Pakistani shares that cost far less than a deliberately conservative estimate of what they are worth.

"Buying a dollar for fifty cents" means a price of at most half of fair value. The fair value used here is not the blend
shown in the valuation panel of a stock's page. It is built to err low, so a share only looks cheap when three different
yardsticks agree:

* Profit is normalised. After a rise, the average of this year and last is used (a lucky year is not capitalised for ever);
  after a fall, the lower current figure; a rebound of 100% or more counts for half, because it may be a recovery from a loss.
* *Earnings power* assumes no growth at all; *peer P/E* and *justified P/B* assume no more than ``bargains.growth``
  (below inflation).
* The return on equity credited is capped, and can never exceed what profit actually earns on book value.
* The required return is the risk-free rate plus the full equity premium, with a beta of at least one.
* Fair value is the middle of the three, never an average that one generous model can lift.

A share only makes the list when it also passes the checks that keep value traps out: too small, too thin to trade, a
one-off gain, a price that has just collapsed, a price the data cannot confirm, heavy debt. What looked cheap but failed a
check is reported together with the reason, so a trap is visible as a trap.

None of this is back-tested: free sources have no point-in-time fundamentals to test it on. It is a screen that tells
you where to look, not a promise that the price will move to fair value, or when.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from . import fundamental as fu
from .fundamental import _num, _pos, _r, _ref
from .texts import ok

FINANCIAL = ("bank", "inv.", "insur", "modaraba", "leasing", "securit", "fund", "financ")
FUND_LIKE = ("investment trust", "mutual fund", "exchange traded")  # their profit includes unrealised gains, so a profit-based value would mislead
CYCLICAL = ("textile", "sugar", "cement", "chemical", "engineering", "paper", "automobile", "synthetic", "glass", "refiner", "oil & gas", "fertili", "steel")

SCOPE = {
    "psx": "Pakistani shares are valued from their earnings and book value, which is what this page is built on.",
    "fx": "A currency has no earnings or assets, so there is no fair value to compare its price with. Nothing is listed here for forex.",
    "crypto": "A coin has no earnings or assets either. How far it is below its old high says nothing about what it is worth, so nothing is listed here for crypto.",
}

MODELS = [
    {"name": "Earnings power", "text": "Normalised profit per share divided by the required return, with no growth."},
    {"name": "Peer P/E", "text": "Normalised profit per share times the median P/E of the same sector, never above what no-growth cash flows would justify."},
    {"name": "Justified P/B", "text": "Book value per share times (return on equity less growth) over (required return less growth), return on equity capped."},
]


def normalise_eps(eps: float, growth_pct: float | None) -> tuple[float, str]:
    """Profit per share to capitalise, and how it was arrived at: ``reported``, ``averaged``, ``rebound`` or ``unknown``."""
    if growth_pct is None or not math.isfinite(growth_pct):
        return eps, "unknown"
    if growth_pct <= 0:
        return eps, "reported"
    if growth_pct >= 100.0:
        return 0.5 * eps, "rebound"
    prev = eps / (1.0 + growth_pct / 100.0)
    return 0.5 * (eps + prev), "averaged"


def required_return(vcfg: dict, beta: float | None) -> float:
    b = float(np.clip(beta if (beta is not None and math.isfinite(beta)) else 1.0, 1.0, 1.6))
    return float(vcfg["risk_free_pkr"]) + b * float(vcfg["equity_risk_premium"])


def fair_value(f: dict, price: float, beta: float | None, sector: str | None, stats: dict[str, dict], vcfg: dict, bcfg: dict) -> dict | None:
    """The three conservative estimates and their middle value, or ``None`` when fewer than ``min_models`` can be computed."""
    eps, bvps, roe, epsg = _num(f.get("eps")), _pos(f.get("bvps")), _num(f.get("roe")), _num(f.get("eps_growth_pct"))
    if not ok(price) or price <= 0 or eps is None or eps <= 0 or bvps is None:
        return None
    roe_given = roe is not None
    ke = required_return(vcfg, beta)
    g = min(float(bcfg["growth"]), ke - 0.05)
    eps_n, how = normalise_eps(eps, epsg)
    if roe is None:
        roe = eps / bvps * 100.0  # profit on book, when the feed has no return on equity
    peer_pe = _ref(stats, sector, "pe")
    pe_ref = float(np.clip(peer_pe if peer_pe else float(vcfg["fair_pe_default"]), 3.0, 1.0 / (ke - g)))
    roe_n = min(roe / 100.0, float(bcfg["max_roe_pct"]) / 100.0, eps_n / bvps)
    pb_fair = float(np.clip((roe_n - g) / (ke - g), 0.1, 2.0))
    models = [
        {"name": "Earnings power", "value": eps_n / ke, "note": f"profit {eps_n:.2f} a share / {ke * 100:.0f}% required return, no growth"},
        {"name": "Peer P/E", "value": eps_n * pe_ref, "note": f"profit {eps_n:.2f} a share x peer P/E {pe_ref:.1f}"},
        {"name": "Justified P/B", "value": bvps * pb_fair, "note": f"book {bvps:.2f} a share x {pb_fair:.2f} (return on equity {roe_n * 100:.0f}% credited, growth {g * 100:.0f}%)"},
    ]
    models = [m for m in models if math.isfinite(m["value"]) and m["value"] > 0]
    if len(models) < int(bcfg["min_models"]):
        return None
    vals = sorted(m["value"] for m in models)
    fair = vals[(len(vals) - 1) // 2]  # the lower median: the lowest of two, the middle of three
    return {
        "models": [{"name": m["name"], "value": round(m["value"], 2), "note": m["note"]} for m in models],
        "fair": fair, "low": vals[0], "high": vals[-1], "ke": ke, "g": g, "eps_norm": eps_n, "eps_how": how, "peer_pe": peer_pe,
        "roe": roe, "roe_given": roe_given,
    }


def _financial(sector: str | None) -> bool:
    s = (sector or "").lower()
    return any(w in s for w in FINANCIAL)


def fund_like(sector: str | None) -> bool:
    s = (sector or "").lower()
    return any(w in s for w in FUND_LIKE)


def _cyclical(sector: str | None) -> bool:
    s = (sector or "").lower()
    return any(w in s for w in CYCLICAL)


def _bn(x: float) -> str:
    return f"{x / 1e9:.1f}bn"


def _m(x: float) -> str:
    return f"{x / 1e6:.1f}m"


def checks(it: dict, val: dict, bcfg: dict, min_adv: float) -> tuple[list[str], list[str]]:
    """``(reasons it cannot be listed, cautions that go on its card)``, in plain words."""
    f = it["f"]
    hard: list[str] = []
    soft: list[str] = []
    px = it["px"]
    eps, bvps = _num(f.get("eps")), _pos(f.get("bvps"))
    epsg, roe, dy, payout = _num(f.get("eps_growth_pct")), _num(f.get("roe")), _num(f.get("dy")), _num(f.get("payout_pct"))
    ff, mcap = _num(f.get("free_float_pct")), _pos(f.get("mcap"))
    if mcap is None and _pos(f.get("shares")):
        mcap = float(f["shares"]) * px
    adv, sector = it.get("adv"), it.get("sector")
    chg1m, chg3m = it.get("chg1m"), it.get("chg3m")
    # --- the data cannot be trusted
    px_fund = (float(f["pe"]) * eps) if (_pos(f.get("pe")) and eps) else None
    if px_fund and abs(px / px_fund - 1.0) > float(bcfg["max_price_gap"]):
        hard.append(f"The price (PKR {px:,.2f}) and the reported ratios (which imply PKR {px_fund:,.2f}) do not agree: a share split, bonus issue or stale figures")
    last_bar = it.get("bar_close")
    if last_bar and abs(px / last_bar - 1.0) > float(bcfg["max_price_gap"]):
        hard.append(f"The latest price is {abs(px / last_bar - 1) * 100:.0f}% away from the last stored close: a share split, bonus issue or bad quote is more likely than a real move")
    if it.get("stale_days") is not None and it["stale_days"] > int(bcfg["max_stale_days"]):
        hard.append(f"No trade for {it['stale_days']} days: the price is not current")
    if val["eps_how"] == "unknown":
        hard.append("No profit history is available, so there is no way to tell whether this year is typical")
    if epsg is not None and epsg >= float(bcfg["max_eps_jump_pct"]):
        hard.append(f"Profit rose {epsg:,.0f}% in a year: a one-off gain is more likely than a new level")
    pe_now = px / eps if eps else None
    if pe_now is not None and pe_now < float(bcfg["min_pe"]):
        hard.append(f"A P/E of {pe_now:.1f} is too low to believe: it is usually a one-off gain or a data error")
    if roe is not None and roe > float(bcfg["roe_outlier_pct"]):
        hard.append(f"A return on equity of {roe:.0f}% is not believable as a lasting level")
    pb_now = px / bvps if bvps else None
    if pb_now is not None and pb_now < float(bcfg["min_pb"]):
        hard.append(f"The price is only {pb_now * 100:.0f}% of book value: far more likely a data problem or a company in distress than a bargain")
    # --- cannot be bought or sold
    if mcap is None:
        hard.append("The company's market value is unknown")
    elif mcap < float(bcfg["min_mcap"]):
        hard.append(f"A market value of PKR {_bn(mcap)} is below PKR {_bn(float(bcfg['min_mcap']))}: too small to trust the numbers")
    if adv is None or adv < min_adv:
        hard.append(f"Trades only PKR {_m(adv or 0.0)} a day (at least PKR {_m(min_adv)} needed): you could not get in or out in a meaningful size")
    if ff is not None and ff < float(bcfg["min_free_float_pct"]):
        hard.append(f"Only {ff:.0f}% of the shares are free to trade")
    # --- the price may already know something the figures do not
    if chg1m is not None and chg1m <= -float(bcfg["max_drop_1m_pct"]):
        hard.append(f"The price fell {abs(chg1m):.0f}% in a month: the reported profit may not show the reason yet")
    elif chg3m is not None and chg3m <= -float(bcfg["max_drop_3m_pct"]):
        hard.append(f"The price fell {abs(chg3m):.0f}% in three months: the reported profit may not show the reason yet")
    # --- debt (not meaningful for lenders and insurers)
    de, cr, fcf = _num(f.get("de_ratio")), _num(f.get("current_ratio")), _num(f.get("fcf"))
    lender = _financial(sector)
    if de is not None and not lender:
        if de > float(bcfg["max_debt_to_equity"]):
            hard.append(f"Debt is {de:.1f} times equity")
        elif de > 1.5:
            soft.append(f"Debt is {de:.1f} times equity")
    if cr is not None and not lender and cr < 1.0:
        soft.append(f"Current liabilities exceed current assets (ratio {cr:.2f})")
    if fcf is not None and fcf < 0 and not lender:
        soft.append("Free cash flow was negative over the last year")
    if val["high"] > 2.5 * val["low"]:
        soft.append(f"The three yardsticks disagree widely (PKR {val['low']:,.1f} to {val['high']:,.1f})")
    # --- cautions for the card
    if val["eps_how"] == "rebound":
        soft.append(f"Profit rebounded {epsg:,.0f}%: only half of it is counted, in case it came from a loss or a one-off")
    elif epsg is not None and epsg >= 25 and _cyclical(sector):
        soft.append(f"Profit is up {epsg:,.0f}% in a sector whose profits swing with the cycle: this year may be near a peak")
    if epsg is not None and epsg <= -15:
        soft.append(f"Profit fell {abs(epsg):.0f}% over the last year")
    if not dy:
        soft.append("Pays no dividend: you are only rewarded if the price catches up with value")
    elif payout is not None and payout > 100:
        soft.append(f"The dividend ({payout:.0f}% of profit) is more than profit can cover")
    elif dy > 20:
        soft.append(f"A yield of {dy:.0f}% is unlikely to be repeated")
    if ff is None:
        soft.append("How many shares are free to trade is not reported")
    elif ff < 25:
        soft.append(f"Only {ff:.0f}% of the shares are free to trade: the price can jump on small orders")
    if mcap is not None and float(bcfg["min_mcap"]) <= mcap < 5e9:
        soft.append(f"A small company (PKR {_bn(mcap)}): fewer eyes on it and less margin for error")
    if adv is not None and min_adv <= adv < 2 * min_adv:
        soft.append(f"Trades PKR {_m(adv)} a day: buy in pieces over several days")
    if chg3m is not None and chg3m <= -15 and not hard:
        soft.append(f"The price is down {abs(chg3m):.0f}% in three months and has not turned yet")
    if (it.get("bars") or 0) and it["bars"] < 250:
        soft.append("Less than a year of price history")
    return hard, soft


def _why(it: dict, val: dict, px: float) -> str:
    f = it["f"]
    eps, bvps, roe, dy = _num(f.get("eps")), _pos(f.get("bvps")), _num(f.get("roe")), _num(f.get("dy"))
    cents = px / val["fair"] * 100.0
    parts = [f"Priced at {cents:.0f} cents on the dollar: PKR {px:,.2f} against a conservative fair value of PKR {val['fair']:,.2f}."]
    bits = []
    if eps:
        pe = px / eps
        bits.append(f"P/E {pe:.1f}" + (f" (peers {val['peer_pe']:.1f})" if val.get("peer_pe") else ""))
    if bvps:
        bits.append(f"P/B {px / bvps:.2f}")
    if roe is not None:
        bits.append(f"earns {roe:.0f}% on book value")
    if dy:
        bits.append(f"yields {dy:.1f}%")
    if bits:
        text = ", ".join(bits)
        parts.append(text[0].upper() + text[1:] + ".")
    return " ".join(parts)


def evaluate(it: dict, stats: dict[str, dict], cfg: Any, min_adv: float) -> dict | None:
    """Value one share and run the checks. ``None`` when no fair value can be computed at all."""
    f, px = it["f"], it["px"]
    bcfg, vcfg = cfg["bargains"], cfg["valuation"]
    val = fair_value(f, px, it.get("beta"), it.get("sector"), stats, vcfg, bcfg)
    if val is None:
        return None
    hard, soft = checks(it, val, bcfg, min_adv)
    mos = 1.0 - px / val["fair"]
    n_above = sum(1 for m in val["models"] if m["value"] > px)
    if n_above < len(val["models"]):
        hard.insert(0, f"Only {n_above} of its {len(val['models'])} yardsticks put its value above the price: they must all agree it is cheap")
    return {"it": it, "val": val, "mos": mos, "hard": hard, "soft": soft}


def _tier(mos_pct: float, tiers: list[float]) -> float | None:
    for t in sorted(tiers, reverse=True):
        if mos_pct >= t:
            return float(t)
    return None


def _tier_defs(tiers: list[float]) -> list[dict]:
    ts = sorted((float(t) for t in tiers), reverse=True)
    out = []
    for i, t in enumerate(ts):
        cents = 100.0 - t
        lo = None if i == 0 else 100.0 - ts[i - 1]
        label = f"{cents:.0f} cents on the dollar or less" if lo is None else f"{lo + 1:.0f} to {cents:.0f} cents on the dollar"
        out.append({"min_mos": t, "max_cents": cents, "label": label, "name": "A dollar for fifty cents" if abs(t - 50.0) < 1e-9 else f"{t:.0f}% or more below fair value"})
    return out


def _row(ev: dict, tier: float | None, bcfg: dict, asof: str | None) -> dict:
    it, val, mos = ev["it"], ev["val"], ev["mos"]
    f, px = it["f"], it["px"]
    fair = val["fair"]
    eps, bvps = _num(f.get("eps")), _pos(f.get("bvps"))
    mcap = _pos(f.get("mcap"))
    if mcap is None and _pos(f.get("shares")):
        mcap = float(f["shares"]) * px
    lo_m, hi_m = int(bcfg["horizon_months"][0]), int(bcfg["horizon_months"][1])
    return {
        "id": it["id"], "sym": it["sym"], "name": it["name"], "sector": it.get("sector"),
        "px": _r(px, 2), "px_asof": it.get("px_asof") or asof,
        "fair": _r(fair, 2), "low": _r(val["low"], 2), "high": _r(val["high"], 2),
        "mos_pct": _r(mos * 100.0, 1), "cents": int(round(px / fair * 100.0)), "upside_pct": _r((fair / px - 1.0) * 100.0, 1), "tier": tier,
        "target1": _r(px + 0.5 * (fair - px), 2), "target2": _r(fair, 2), "review_below": _r(px * (1.0 - float(bcfg["review_drop_pct"]) / 100.0), 2),
        "horizon": f"{lo_m} to {hi_m} months", "horizon_months": [lo_m, hi_m],
        "models": val["models"],
        "pe": _r(px / eps, 2) if eps else None, "pe_peer": _r(val.get("peer_pe"), 1), "pb": _r(px / bvps, 2) if bvps else None,
        "roe": _r(val["roe"], 1), "roe_derived": not val["roe_given"], "dy": _r(_num(f.get("dy")), 2), "eps": _r(eps, 2), "eps_norm": _r(val["eps_norm"], 2), "bvps": _r(bvps, 2),
        "eps_growth_pct": _r(_num(f.get("eps_growth_pct")), 1), "mcap": _r(mcap, 0), "adv": _r(it.get("adv"), 0), "free_float_pct": _r(_num(f.get("free_float_pct")), 1),
        "chg1m": _r(it.get("chg1m"), 1), "chg3m": _r(it.get("chg3m"), 1), "chg1y": _r(it.get("chg1y"), 1), "d200": _r(it.get("d200"), 1),
        "lo52d": _r(it.get("lo52d"), 1), "hi52d": _r(it.get("hi52d"), 1),
        "why": _why(it, val, px), "risks": ev["soft"],
    }


def screen(items: list[dict], cfg: Any, *, now_iso: str, asof: str | None = None, stale_days: int | None = None) -> dict:
    """The Bargains file. ``items`` are PSX equities: ``{id, sym, name, sector, px, px_asof, bar_close, adv, chg1m, chg3m, chg1y, d200,
    lo52d, hi52d, beta, bars, stale_days, f}`` where ``f`` holds the normalised fundamentals (see :mod:`acumen.analysis.fundamental`)."""
    bcfg, vcfg = cfg["bargains"], cfg["valuation"]
    min_adv = float(cfg.get_path("universe.psx.min_adv_value", 5e6))
    tiers = [float(t) for t in bcfg["tiers"]]
    watch = float(bcfg["watch_min_mos"])
    funds = {it["id"]: it["f"] for it in items if it.get("f")}
    stats = fu.sector_stats(funds, {it["id"]: it.get("sector") for it in items})
    picks: list[dict] = []
    excluded: list[dict] = []
    valued = skipped = 0
    for it in items:
        if not it.get("f") or not ok(it.get("px")) or it["px"] <= 0:
            continue
        if fund_like(it.get("sector")):
            skipped += 1
            continue
        ev = evaluate(it, stats, cfg, min_adv)
        if ev is None:
            continue
        valued += 1
        mos_pct = ev["mos"] * 100.0
        if ev["hard"]:
            if mos_pct >= watch:
                row = _row(ev, None, bcfg, asof)
                row["reasons"] = ev["hard"]
                excluded.append(row)
            continue
        tier = _tier(mos_pct, tiers)
        if tier is not None:
            picks.append(_row(ev, tier, bcfg, asof))
    picks.sort(key=lambda r: (-r["tier"], -(r["mos_pct"] or 0.0)))
    excluded.sort(key=lambda r: -(r["mos_pct"] or 0.0))
    cap = int(bcfg["max_list"])
    counts = {f"{t:.0f}": sum(1 for p in picks if p["tier"] == t) for t in sorted(tiers, reverse=True)}
    picks = picks[:cap]
    excluded = excluded[: int(bcfg["max_excluded"])]
    for r in excluded:  # keep the file small: the cards of the excluded need the headline numbers and the reasons only
        for k in ("models", "target1", "target2", "review_below", "horizon", "horizon_months", "why", "risks"):
            r.pop(k, None)
    ke = required_return(vcfg, 1.0)
    notes = []
    if skipped:
        notes.append(f"{skipped} funds and real estate trusts were not valued: their reported profit includes unrealised gains, so a value built on it would mislead.")
    if stale_days is not None and stale_days > 3:
        notes.append(f"The latest Pakistani prices are {stale_days} days old, so every discount below is measured against an old price.")
    return {
        "generated_at": now_iso, "asof": asof, "stale_days": stale_days, "tiers": _tier_defs(tiers),
        "counts": {"screened": len(items), "valued": valued, "picks": len(picks), "by_tier": counts, "excluded": len(excluded)},
        "picks": picks, "excluded": excluded,
        "method": {
            "required_return_pct": _r(ke * 100.0, 1), "growth_pct": _r(float(bcfg["growth"]) * 100.0, 1), "risk_free_pct": _r(float(vcfg["risk_free_pkr"]) * 100.0, 1),
            "equity_premium_pct": _r(float(vcfg["equity_risk_premium"]) * 100.0, 1), "models": MODELS, "horizon_months": list(bcfg["horizon_months"]),
            "review_drop_pct": float(bcfg["review_drop_pct"]), "max_position_pct": float(bcfg["max_position_pct"]),
            "checks": {"min_mcap": float(bcfg["min_mcap"]), "min_adv": min_adv, "min_free_float_pct": float(bcfg["min_free_float_pct"]), "min_pe": float(bcfg["min_pe"]),
                       "roe_outlier_pct": float(bcfg["roe_outlier_pct"]), "max_eps_jump_pct": float(bcfg["max_eps_jump_pct"]), "max_drop_1m_pct": float(bcfg["max_drop_1m_pct"]),
                       "max_drop_3m_pct": float(bcfg["max_drop_3m_pct"]), "max_debt_to_equity": float(bcfg["max_debt_to_equity"])},
        },
        "scope": SCOPE, "notes": notes,
        "caveats": [
            "Fair values are estimates from reported profit and book value, with no knowledge of the company beyond the numbers. Read the latest accounts and announcements before acting.",
            "Cheap shares are often cheap for a reason. The checks remove the obvious traps, not all of them.",
            "Nothing here is back-tested: free sources have no point-in-time fundamentals to test it on.",
            "A price can stay far below fair value for years, and fair value can fall. The horizon is a typical wait for the gap to close, not a forecast.",
        ],
    }


def summary(body: dict, n: int = 3) -> dict:
    """The compact form used in overview.bargains (the home tile)."""
    return {
        "counts": body["counts"], "asof": body["asof"], "stale_days": body.get("stale_days"),
        "top": [{k: p[k] for k in ("id", "sym", "name", "px", "fair", "mos_pct", "cents", "tier")} for p in body["picks"][:n]],
    }
