"""Live track record: every actionable idea Acumen issues is recorded and then followed with the same
simulation rules the backtest uses, so what is reported is what the plan would have done."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd

from .instrument import CAL_DAYS, cost_fraction
from .simulate import PlanLevels, find_fill, simulate_trade

MIN_CONVICTION = 60
STATE_NAME = "ledger"


def _r(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _plan_of(e: dict) -> PlanLevels:
    sign = 1 if e["dir"] == "long" else -1
    return PlanLevels(sign, e["entry_type"], float(e["entry"]), float(e["stop"]), [float(e["t1"]), float(e["t2"])], abs(float(e["entry"]) - float(e["stop"])), "atr")


def resolve(entry: dict, bars: pd.DataFrame, cfg: Any, cost_frac: float) -> dict:
    """Update one ledger entry from the bars seen so far. Returns the changed fields."""
    if bars is None or len(bars) == 0:
        return {}
    idx = bars.index
    s = int(idx.searchsorted(pd.Timestamp(entry["issued"]), side="right") - 1)
    if s < 0:
        return {}
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    approx = bars["approx_hl"].to_numpy(dtype=bool) if "approx_hl" in bars.columns else None
    plan = _plan_of(entry)
    n = len(c)
    tr = simulate_trade(
        s, plan, o, h, l, c, approx, time_stop=int(entry["time_stop_bars"]), wait=int(entry["valid_bars"]),
        scale_out=entry["scale_out"], cost_frac=cost_frac,
    )
    if tr is None:
        state = find_fill(plan, s, o, h, l, int(entry["valid_bars"]), n)[0]
        if state == "waiting":
            return {"status": "pending", "last": _r(c[-1], 6)}
        reason = "expired" if state == "none" else "skipped"
        return {"status": "closed", "reason": reason, "closed": idx[min(n - 1, s + int(entry["valid_bars"]))].strftime("%Y-%m-%d"), "r": None, "last": _r(c[-1], 6)}
    upd = {"fill_date": idx[tr["fill_idx"]].strftime("%Y-%m-%d"), "fill": _r(tr["fill"], 6), "last": _r(c[-1], 6)}
    if tr["status"] == "open":
        upd.update({"status": "filled", "r_now": _r(tr["r"], 2)})
    else:
        upd.update({"status": "closed", "reason": tr["reason"], "closed": idx[tr["exit_idx"]].strftime("%Y-%m-%d"), "exit": _r(tr["exit"], 6), "r": _r(tr["r"], 2)})
    return upd


def update(state: dict | None, ideas: list[dict], bars_by_id: dict[str, pd.DataFrame], insts: dict[str, Any], cfg: Any, today: str) -> tuple[dict, dict]:
    """Advance the ledger one run. Returns (new_state, ledger_view)."""
    state = state or {"version": 1, "entries": [], "since": today}
    entries: list[dict] = state.get("entries", [])
    # 1. follow existing entries
    for e in entries:
        if e["status"] in ("pending", "filled"):
            inst = insts.get(e["id"])
            cost = cost_fraction(inst, cfg) if inst else 0.0
            try:
                e.update(resolve(e, bars_by_id.get(e["id"]), cfg, cost))
            except Exception as exc:  # one bad entry must not stop the run
                e["error"] = str(exc)[:120]
    # 2. issue new ideas
    live = {(e["id"], e["horizon"]) for e in entries if e["status"] in ("pending", "filled")}
    recent_closed = {(e["id"], e["horizon"]): e.get("closed") for e in entries if e["status"] == "closed" and e.get("closed")}
    issued = 0
    for i in ideas:
        if not i["actionable"] or i["conviction"] < MIN_CONVICTION:
            continue
        key = (i["id"], i["horizon"])
        if key in live:
            continue
        last_close = recent_closed.get(key)
        if last_close and (pd.Timestamp(i["asof"]) - pd.Timestamp(last_close)).days < 5:
            continue
        pcfg = cfg["plan"][i["horizon"]]
        entries.append({
            "id": i["id"], "market": i["market"], "horizon": i["horizon"], "dir": i["dir"], "issued": i["asof"], "setup": i["setup"],
            "entry_type": i["entry"]["type"], "entry": i["entry"]["px"], "stop": i["stop"], "t1": i["targets"][0]["px"], "t2": i["targets"][1]["px"],
            "scale_out": list(pcfg["scale_out"]), "valid_bars": i["entry"]["valid_bars"], "time_stop_bars": int(cfg["horizons"][i["horizon"]]["time_stop_bars"]),
            "conviction": i["conviction"], "grade": i["grade"], "status": "pending", "p_win": i.get("p_win"), "exp_r": i.get("exp_r"),
        })
        live.add(key)
        issued += 1
    # 3. prune
    keep_days = int(cfg.get_path("output.ledger_keep_days", 400))
    cutoff = (pd.Timestamp(today) - pd.Timedelta(days=keep_days)).strftime("%Y-%m-%d")
    entries = [e for e in entries if e["status"] != "closed" or (e.get("closed") or e["issued"]) >= cutoff]
    state["entries"] = entries
    return state, view(state, bars_by_id, today)


def view(state: dict, bars_by_id: dict[str, pd.DataFrame], today: str) -> dict:
    entries = state.get("entries", [])
    live = [e for e in entries if e["status"] in ("pending", "filled")]
    done = [e for e in entries if e["status"] == "closed" and e.get("r") is not None]
    rs = np.array([e["r"] for e in done], dtype=float)

    def agg(sub: list[dict]) -> dict:
        r = np.array([e["r"] for e in sub], dtype=float)
        return {"closed": len(sub), "win": _r((r > 0).mean(), 3) if len(r) else None, "avg_r": _r(r.mean(), 3) if len(r) else None}

    by_h = {h: agg([e for e in done if e["horizon"] == h]) for h in ("short", "medium", "long")}
    by_m = {m: agg([e for e in done if e["market"] == m]) for m in ("psx", "fx", "crypto")}
    stats = {"since": state.get("since"), "open": len(live), "closed": len(done),
             "win": _r((rs > 0).mean(), 3) if len(rs) else None, "avg_r": _r(rs.mean(), 3) if len(rs) else None,
             "by_horizon": by_h, "by_market": by_m}
    open_rows = [{"id": e["id"], "horizon": e["horizon"], "dir": e["dir"], "issued": e["issued"], "entry": e["entry"], "stop": e["stop"],
                  "target1": e["t1"], "target2": e["t2"], "conviction": e["conviction"], "status": e["status"], "last": e.get("last"), "r_now": e.get("r_now")}
                 for e in sorted(live, key=lambda x: x["issued"], reverse=True)]
    closed_rows = [{"id": e["id"], "horizon": e["horizon"], "dir": e["dir"], "issued": e["issued"], "closed": e.get("closed"), "entry": e.get("fill", e["entry"]),
                    "exit": e.get("exit"), "r": e["r"], "reason": e.get("reason")}
                   for e in sorted(done, key=lambda x: x.get("closed") or "", reverse=True)[:200]]
    return {"stats": stats, "open": open_rows, "closed": closed_rows}
