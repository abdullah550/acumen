"""Trade plans and an honest bar-by-bar simulation of them.

Rules of the simulation (they are the same rules the live plans follow, so the history is a fair
description of what the plans would have done):

* A signal is decided on the close of bar ``s`` using only bars ``<= s``.
* Market entries fill at the open of bar ``s+1``. Limit entries wait ``entry_wait_bars`` bars and fill at
  the limit (or at the open if it opens through it). If price runs to the first target or through the stop
  before the limit fills, the order is cancelled.
* Stop, targets and entry reference are fixed price levels decided at signal time. Risk per unit ``R`` is
  measured from the actual fill, so a gap changes the R multiple the way it would for a real trader.
* Inside a bar, if the stop and a target are both reachable, the stop is assumed to come first. A gap
  through a level fills at the open, not at the level.
* After the first target the stop moves to break-even (from the next bar) and the rest runs to the
  second target or the time stop.
* Bars whose high/low were approximated (``approx_hl``) are treated as close-only: stops and targets are
  tested against the close. This avoids inventing intraday ranges the data source never published.
* Costs are a round-trip fraction of notional, converted to R at the fill.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .factors import LIMIT_SETUPS


MIN_SHORT_TARGET_FRAC = 0.10  # a short target never goes below 10% of the entry price


@dataclass
class PlanLevels:
    sign: int
    kind: str  # "market" | "limit"
    ref: float  # reference entry (limit price, or the signal close for a market order)
    stop: float
    targets: list[float]
    dist: float  # |ref - stop|
    stop_kind: str  # "atr" | "swing"


def plan_levels(close: float, atr: float, swing: float | None, sign: int, setup: str, pcfg: dict) -> PlanLevels | None:
    """Entry reference, stop and target ladder for a signal on the current bar. ``None`` if not computable."""
    if not (np.isfinite(close) and np.isfinite(atr)) or atr <= 0 or close <= 0:
        return None
    limit = setup in LIMIT_SETUPS
    off = 0.5 if setup in ("Trend pullback", "Trend rally fade") else 0.25
    ref = close - sign * off * atr if limit else close
    base = float(pcfg["stop_atr"]) * atr
    cap = float(pcfg["max_stop_atr"]) * atr
    dist, kind = base, "atr"
    if swing is not None and np.isfinite(swing):
        # Snap the stop just beyond a nearby swing point, but only when that is close to the ATR stop.
        # A structure stop far away would push the targets (which are multiples of the risk) out of reach.
        d_sw = sign * (ref - swing) + 0.25 * atr
        if 0.75 * base <= d_sw <= 1.35 * base:
            dist, kind = d_sw, "swing"
    dist = min(dist, cap)
    stop = ref - sign * dist
    if stop <= 0:
        return None
    targets = [ref + sign * float(r) * dist for r in pcfg["targets_r"]]
    if sign < 0:
        # A short cannot make more than the whole price: keep every target above a floor. If even the first target
        # would sit below it, the stop is too wide for a short to pay (no plan); a later target is clamped.
        floor = MIN_SHORT_TARGET_FRAC * ref
        if targets[0] < floor:
            return None
        targets = [max(x, floor) for x in targets]
    return PlanLevels(sign, "limit" if limit else "market", float(ref), float(stop), targets, float(dist), kind)


def find_fill(plan: PlanLevels, s: int, o, h, l, wait: int, n: int) -> tuple[str, int | None, float | None, bool]:
    """Locate the entry for a plan created on bar ``s``.

    Returns ``(state, bar, price, intrabar)`` with state ``"filled"``, ``"waiting"`` (the order window has not
    ended and no bar has decided it yet) or ``"none"`` (cancelled or expired without a fill).
    """
    sg = plan.sign
    t1 = plan.targets[0]
    if plan.kind == "market":
        k = s + 1
        if k >= n:
            return "waiting", None, None, False
        return "filled", k, float(o[k]), False
    for k in range(s + 1, min(n, s + 1 + wait)):
        # open through the limit on the favourable side: fill at the open
        if sg * (o[k] - plan.ref) <= 0:
            return "filled", k, float(o[k]), False
        reach = l[k] if sg > 0 else h[k]
        if sg * (reach - plan.ref) <= 0:
            return "filled", k, float(plan.ref), True
        # not filled: cancel if price already went to target 1 (missed)
        far = h[k] if sg > 0 else l[k]
        if sg * (far - t1) >= 0:
            return "none", None, None, False
    if s + wait > n - 1:
        return "waiting", None, None, False
    return "none", None, None, False


def simulate_trade(
    s: int,
    plan: PlanLevels,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    approx: np.ndarray | None,
    *,
    time_stop: int,
    wait: int,
    scale_out: list[float],
    cost_frac: float,
    breakeven_after_t1: bool = True,
) -> dict | None:
    """Simulate the plan created on bar ``s``. ``None`` = never filled / invalid.

    The returned dict has ``status`` "closed" or "open" (data ran out before an exit; ``r`` is then the
    mark-to-market result at the last close and the trade must not be counted in statistics).
    """
    n = len(c)
    state, k0, fill, intrabar = find_fill(plan, s, o, h, l, wait, n)
    if state != "filled":
        return None
    sg = plan.sign
    stop_s, t1_s, t2_s = sg * plan.stop, sg * plan.targets[0], sg * plan.targets[1]
    fill_s = sg * fill
    rdist = fill_s - stop_s
    if rdist <= 0.25 * plan.dist or fill_s >= t1_s:
        return None  # gapped through or beside the stop, or already at the first target: nothing to trade
    f1 = float(scale_out[0])
    f2 = 1.0 - f1
    cost_r = cost_frac * fill / rdist
    last = min(n - 1, k0 + time_stop - 1)
    hit1 = hit2 = False
    realised = 0.0  # in R, gross
    rem = 1.0
    bars_t1 = bars_t2 = None
    exit_k, reason, exit_px_s = None, "", None
    cur_stop = stop_s
    be_from = None
    mfe = 0.0
    mae = 0.0
    for k in range(k0, last + 1):
        ap = bool(approx[k]) if approx is not None else False
        ok, hk, lk, ck = (c[k], c[k], c[k], c[k]) if ap else (o[k], h[k], l[k], c[k])
        # signed space: adverse extreme is sl, favourable is sh
        a, b = sg * hk, sg * lk
        sl, sh, so, sc = min(a, b), max(a, b), sg * ok, sg * ck
        if k == k0 and intrabar:
            sh = min(sh, fill_s)  # entered mid-bar at the limit: do not credit the rest of the bar's upside
        if be_from is not None and k >= be_from:
            cur_stop = max(cur_stop, fill_s)
        mfe = max(mfe, (sh - fill_s) / rdist)
        mae = min(mae, (sl - fill_s) / rdist)
        if sl <= cur_stop:
            px = min(so, cur_stop) if (k > k0 or not intrabar) else cur_stop
            realised += rem * (px - fill_s) / rdist
            rem = 0.0
            exit_k, reason, exit_px_s = k, ("stop" if not hit1 else "breakeven"), px
            break
        if not hit1 and sh >= t1_s:
            px = max(so, t1_s) if not (k == k0 and intrabar) else t1_s
            realised += f1 * (px - fill_s) / rdist
            rem -= f1
            hit1, bars_t1 = True, k - k0 + 1
            if breakeven_after_t1:
                be_from = k + 1
        if hit1 and not hit2 and sh >= t2_s:
            px = max(so, t2_s)
            realised += rem * (px - fill_s) / rdist
            rem = 0.0
            hit2, bars_t2 = True, k - k0 + 1
            exit_k, reason, exit_px_s = k, "target2", px
            break
        if k == last and k - k0 + 1 >= time_stop:
            realised += rem * (sc - fill_s) / rdist
            rem = 0.0
            exit_k, reason, exit_px_s = k, ("time" if not hit1 else "target1"), sc
            break
    status = "closed"
    if exit_k is None:
        # ran out of data before any exit condition
        status = "open"
        exit_k = n - 1
        mark = sg * c[n - 1]
        realised += rem * (mark - fill_s) / rdist
        rem = 0.0
        exit_px_s = mark
        reason = "open"
    elif reason == "breakeven":
        reason = "target1"  # first target was banked, the remainder stopped at break-even
    r_net = realised - cost_r
    return {
        "status": status,
        "sig_idx": int(s),
        "fill_idx": int(k0),
        "exit_idx": int(exit_k),
        "sign": int(sg),
        "fill": float(fill),
        "exit": float(sg * exit_px_s),
        "stop": float(plan.stop),
        "t1": float(plan.targets[0]),
        "t2": float(plan.targets[1]),
        "rdist": float(rdist * 1.0),
        "r_gross": float(realised),
        "cost_r": float(cost_r),
        "r": float(r_net),
        "bars": int(exit_k - k0 + 1),
        "reason": reason,
        "hit1": bool(hit1),
        "hit2": bool(hit2),
        "bars_t1": bars_t1,
        "bars_t2": bars_t2,
        "mfe_r": float(mfe),
        "mae_r": float(mae),
        "stop_kind": plan.stop_kind,
        "entry_kind": plan.kind,
    }


def run_strategy(
    idx: pd.DatetimeIndex,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    approx: np.ndarray | None,
    sig_dir: np.ndarray,
    sig_setup: np.ndarray,
    atr: np.ndarray,
    sw_lo: np.ndarray,
    sw_hi: np.ndarray,
    conv: np.ndarray,
    score: np.ndarray,
    pcfg: dict,
    *,
    time_stop: int,
    cooldown: int,
    cost_frac: float,
    start: int = 0,
) -> list[dict]:
    """Walk forward through the bars taking one position at a time. Returns closed trades only."""
    n = len(c)
    trades: list[dict] = []
    t = max(start, 0)
    wait = int(pcfg["entry_wait_bars"])
    while t < n - 1:
        d = int(sig_dir[t])
        if d == 0 or not np.isfinite(atr[t]):
            t += 1
            continue
        swing = sw_lo[t] if d > 0 else sw_hi[t]
        plan = plan_levels(float(c[t]), float(atr[t]), float(swing) if np.isfinite(swing) else None, d, str(sig_setup[t]), pcfg)
        if plan is None:
            t += 1
            continue
        tr = simulate_trade(
            t, plan, o, h, l, c, approx,
            time_stop=time_stop, wait=wait, scale_out=pcfg["scale_out"], cost_frac=cost_frac,
        )
        if tr is None:
            t += 1
            continue
        if tr["status"] != "closed":
            break
        tr["setup"] = str(sig_setup[t])
        tr["conv"] = float(conv[t])
        tr["score"] = float(score[t])
        tr["sig_date"] = idx[t]
        tr["entry_date"] = idx[tr["fill_idx"]]
        tr["exit_date"] = idx[tr["exit_idx"]]
        tr["approx_share"] = float(approx[tr["fill_idx"]: tr["exit_idx"] + 1].mean()) if approx is not None else 0.0
        trades.append(tr)
        t = tr["exit_idx"] + max(int(cooldown), 1)
    return trades


TRADE_COLUMNS = [
    "sig_idx", "fill_idx", "exit_idx", "sign", "fill", "exit", "stop", "t1", "t2", "rdist", "r_gross", "cost_r", "r",
    "bars", "reason", "hit1", "hit2", "bars_t1", "bars_t2", "mfe_r", "mae_r", "stop_kind", "entry_kind", "setup", "conv",
    "score", "sig_date", "entry_date", "exit_date", "approx_share",
]


def trades_frame(trades: list[dict]) -> pd.DataFrame:
    """Trades as a DataFrame (empty with the right columns when there are none)."""
    if not trades:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in TRADE_COLUMNS})
    return pd.DataFrame(trades)[TRADE_COLUMNS]


def _nz(x: float | None, nd: int = 4) -> float | None:
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), nd)


def trade_stats(tr: pd.DataFrame, oos_fraction: float = 0.4, min_reliable: int = 20) -> dict:
    """Summary of a trade list: n, win rate, mean R, profit factor, max drawdown in R, mean bars, OOS block."""
    empty = {"n": 0, "win": None, "avg_r": None, "pf": None, "max_dd_r": None, "avg_bars": None, "reliable": False,
             "oos": {"n": 0, "win": None, "avg_r": None}}
    if tr is None or len(tr) == 0:
        return empty
    tr = tr.sort_values("exit_idx") if "exit_date" not in tr else tr.sort_values("exit_date")
    r = tr["r"].astype(float).to_numpy()
    n = len(r)
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if n else 0.0
    dd = max(dd, float(-eq.min()) if eq.min() < 0 else 0.0)
    k = int(round(n * (1.0 - oos_fraction)))
    oos = r[k:] if n - k >= 1 and n >= 5 else np.array([])
    return {
        "n": int(n),
        "win": _nz((r > 0).mean(), 3),
        "avg_r": _nz(r.mean(), 3),
        "pf": _nz(gp / gl, 2) if gl > 0 else None,
        "max_dd_r": _nz(dd, 2),
        "avg_bars": int(round(float(tr["bars"].mean()))),
        "reliable": bool(n >= min_reliable),
        "oos": {"n": int(len(oos)), "win": _nz((oos > 0).mean(), 3) if len(oos) else None, "avg_r": _nz(oos.mean(), 3) if len(oos) else None},
    }


def equity_curve(tr: pd.DataFrame, max_points: int = 120) -> list[list]:
    """Cumulative R over exit dates as ``[[date, cum_r], ...]`` (thinned to at most ``max_points``)."""
    if tr is None or len(tr) == 0:
        return []
    t = tr.sort_values("exit_date")
    cum = t["r"].astype(float).cumsum().to_numpy()
    dates = pd.to_datetime(t["exit_date"]).dt.strftime("%Y-%m-%d").tolist()
    pts = [[d, round(float(v), 2)] for d, v in zip(dates, cum)]
    if len(pts) > max_points:
        keep = np.unique(np.linspace(0, len(pts) - 1, max_points).round().astype(int))
        pts = [pts[i] for i in keep]
    return pts
