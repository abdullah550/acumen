"""Bargains live record: every share the Bargains page lists is logged the day it first passes every check and followed from then on.

The screen cannot be back-tested (free sources have no point-in-time fundamentals), so the only honest evidence that cheap shares
close their gap is a record kept forward in time, the way the ledger follows trade ideas.

* A share is logged once, at the price on its card that day, and stays in the record when it is later dropped from the list: the
  losers are not forgotten. It is followed until its close reaches the fair value it was listed with, or until the longest typical
  wait (``bargains.horizon_months``) has passed.
* Two yardsticks cover the same days: the KSE-100, as asked, and the *typical share* (the median return of every liquid Pakistani
  share, with prices adjusted the same way). The second is the fairer one: stored prices are adjusted for dividends, bonus issues and
  splits, while the index is a price index that leaves dividends out.
* Results are read at fixed ages (``bargains.record_horizons_days``) so that shares listed at different times can be compared, and
  an age is read at all only once ``bargains.record_min_shares`` shares have been followed that long.
* Stored prices are rescaled retroactively when a bonus issue or split happens, so every listing price and target is rescaled by the
  same factor, read off the stored close of the listing day against the close it had when the share was listed.

The state lives in the history store (``state/bargains_record.json``): like the ledger it cannot be recomputed from market data.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

STATE_NAME = "bargains_record"
ROUNDING = 0.002  # a price ratio this close to one is rounding, not a bonus issue or a split
MIN_UNIVERSE = 30  # fewest liquid shares behind a "typical share" figure
DAYS_PER_MONTH = 30.4


def _r(x: Any, nd: int = 1) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _pct(x: float | None) -> float | None:
    return None if x is None else _r(x * 100.0, 1)


def _pos(index: pd.Index, day: Any) -> int:
    """Position of the last bar on or before ``day``; -1 when there is none."""
    return int(index.searchsorted(pd.Timestamp(day), side="right")) - 1


def _iso(ts: Any) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _mean(xs: list[float]) -> float | None:
    return float(np.mean(xs)) if xs else None


class Prices:
    """The closing prices the record is measured with (the followed shares, the KSE-100 and the liquid shares), read once per run."""

    def __init__(self, bars: dict[str, pd.DataFrame], bench: pd.Series | None, universe: list[str]):
        self.bars = bars
        self.bench = self._clean(bench)
        cols = {}
        for iid in universe:
            s = self.close(iid)
            if s is not None:
                cols[iid] = s
        self.frame = pd.concat(cols, axis=1).sort_index().ffill() if cols else None

    @staticmethod
    def _clean(s: pd.Series | None) -> pd.Series | None:
        if s is None or len(s) == 0:
            return None
        s = s.astype(float).dropna()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        return s if len(s) else None

    def close(self, iid: str) -> pd.Series | None:
        b = self.bars.get(iid)
        if b is None or len(b) == 0 or "close" not in b:
            return None
        return self._clean(b["close"])

    def index_return(self, d0: Any, d1: Any) -> float | None:
        s = self.bench
        if s is None:
            return None
        p0, p1 = _pos(s.index, d0), _pos(s.index, d1)
        if p0 < 0 or p1 < p0 or s.iloc[p0] <= 0:
            return None
        return float(s.iloc[p1] / s.iloc[p0] - 1.0)

    def typical_return(self, d0: Any, d1: Any) -> float | None:
        """Median return of the liquid shares over the same days, among those with a price at both ends."""
        fr = self.frame
        if fr is None:
            return None
        p0, p1 = _pos(fr.index, d0), _pos(fr.index, d1)
        if p0 < 0 or p1 < p0:
            return None
        a, b = fr.iloc[p0].to_numpy(float), fr.iloc[p1].to_numpy(float)
        ok = np.isfinite(a) & np.isfinite(b) & (a > 0)
        if int(ok.sum()) < MIN_UNIVERSE:
            return None
        return float(np.median(b[ok] / a[ok] - 1.0))


def _new_entry(pick: dict, prices: Prices, asof: str) -> dict | None:
    """The record of a share the day it is first listed, or ``None`` when it has no price history to follow."""
    s = prices.close(pick["id"])
    listed = min(str(pick.get("px_asof") or asof)[:10], asof)  # never later than the date the record is as of (ISO dates sort as text)
    if s is None:
        return None
    p0 = _pos(s.index, listed)
    if p0 < 0:
        return None
    return {
        "id": pick["id"], "sym": pick["sym"], "name": pick["name"], "sector": pick.get("sector"),
        "listed": listed, "px": float(pick["px"]), "entry_close": float(s.iloc[p0]),
        "fair": float(pick["fair"]), "cents": int(pick["cents"]), "tier": float(pick["tier"]),
        "target1": float(pick["target1"]), "target2": float(pick["target2"]), "review_below": float(pick["review_below"]),
        "status": "open", "reason": None, "closed": None, "listed_now": True,
        "t1_date": None, "t2_date": None, "review_date": None,
    }


def _factor(e: dict, s: pd.Series) -> float:
    """What the stored prices of the listing day have been scaled by since the share was listed (bonus issues, splits, dividends)."""
    p0 = _pos(s.index, e["listed"])
    ref = e.get("entry_close")
    if p0 < 0 or not ref:
        return 1.0
    f = float(s.iloc[p0]) / float(ref)
    return 1.0 if (not math.isfinite(f) or f <= 0 or abs(f - 1.0) < ROUNDING) else f


def _follow(e: dict, prices: Prices, asof: pd.Timestamp, max_days: int, horizons: list[int]) -> dict | None:
    """Bring one entry up to date from the stored prices and return the numbers measured on the way (``None`` without prices)."""
    s = prices.close(e["id"])
    if s is not None:
        s = s.loc[:asof]  # the record is as of the market's date: a bar dated later is not read
    if s is None or len(s) == 0:
        return None
    f = _factor(e, s)
    p0 = _pos(s.index, e["listed"])
    if p0 < 0:
        return None
    px0 = e["px"] * f
    t1, t2, rv = e["target1"] * f, e["target2"] * f, e["review_below"] * f
    after = s.iloc[p0 + 1:]
    for key, mask in (("t1_date", after >= t1), ("t2_date", after >= t2), ("review_date", after <= rv)):
        if e.get(key) is None and len(after) and bool(mask.any()):
            e[key] = _iso(after.index[int(np.argmax(mask.to_numpy()))])  # the first close that did; once seen it is never taken back
    listed = pd.Timestamp(e["listed"])
    if e["status"] == "open":
        if e.get("t2_date"):
            e.update(status="closed", reason="fair_value", closed=e["t2_date"])
        elif (asof - listed).days >= max_days:
            e.update(status="closed", reason="time", closed=_iso(listed + pd.Timedelta(days=max_days)))
    end = pd.Timestamp(e["closed"]) if e.get("closed") else asof
    p1 = _pos(s.index, end)
    if p1 < p0:
        return None
    last = float(s.iloc[p1])
    fair = e["fair"] * f
    out = {
        "f": f, "px0": px0, "last": last, "end": end, "ret": last / px0 - 1.0,
        "index": prices.index_return(listed, end), "typical": prices.typical_return(listed, end),
        "gap": (last - px0) / (fair - px0) if fair > px0 else None,
        "fair": fair, "t1": t1, "t2": t2, "rv": rv, "ages": {},
    }
    for days in horizons:
        at = listed + pd.Timedelta(days=days)
        if at > asof:
            continue
        q = _pos(s.index, at)
        if q < p0:
            continue
        stamp = _iso(at)
        out["ages"][days] = {
            "ret": float(s.iloc[q]) / px0 - 1.0, "index": prices.index_return(listed, at), "typical": prices.typical_return(listed, at),
            "t1": bool(e.get("t1_date") and e["t1_date"] <= stamp), "t2": bool(e.get("t2_date") and e["t2_date"] <= stamp),
            "review": bool(e.get("review_date") and e["review_date"] <= stamp),
        }
    return out


def _horizon_row(days: int, items: list[dict], min_shares: int) -> dict:
    """What the shares followed for at least ``days`` days did in their first ``days`` days."""
    rets = [i["ret"] for i in items]
    idx = [i for i in items if i["index"] is not None]
    typ = [i for i in items if i["typical"] is not None]
    return {
        "days": days, "n": len(items), "ready": len(items) >= min_shares,
        "ret_mean": _pct(_mean(rets)), "ret_median": _pct(float(np.median(rets)) if rets else None),
        "index_mean": _pct(_mean([i["index"] for i in idx])), "typical_mean": _pct(_mean([i["typical"] for i in typ])),
        "excess_index_mean": _pct(_mean([i["ret"] - i["index"] for i in idx])), "excess_typical_mean": _pct(_mean([i["ret"] - i["typical"] for i in typ])),
        "ahead_index": sum(1 for i in idx if i["ret"] > i["index"]), "ahead_index_of": len(idx),
        "ahead_typical": sum(1 for i in typ if i["ret"] > i["typical"]), "ahead_typical_of": len(typ),
        "reached_t1": sum(1 for i in items if i["t1"]), "reached_t2": sum(1 for i in items if i["t2"]), "below_review": sum(1 for i in items if i["review"]),
    }


def update(state: dict | None, picks: list[dict], bars_by_id: dict[str, pd.DataFrame], bench: pd.Series | None, universe: list[str],
           cfg: Any, today: str, asof: str | None) -> tuple[dict, dict]:
    """Advance the record by one run: log the shares that have just passed every check, follow every share, and write the view.
    ``picks`` are the rows of the Bargains page, ``universe`` the liquid Pakistani shares behind the typical-share figure."""
    bcfg = cfg["bargains"]
    asof_s = str(asof or today)[:10]
    asof_ts = pd.Timestamp(asof_s)
    state = state or {"version": 1, "since": asof_s, "entries": []}
    entries: list[dict] = state.setdefault("entries", [])
    prices = Prices(bars_by_id, bench, universe)
    months = int((bcfg.get("horizon_months") or [12, 36])[-1])
    max_days = int(round(months * DAYS_PER_MONTH))
    horizons = sorted(int(d) for d in bcfg["record_horizons_days"])
    min_shares = int(bcfg["record_min_shares"])
    cooldown = int(bcfg["record_cooldown_days"])

    # 1. log the shares that are listed now and are not already followed
    followed = {e["id"] for e in entries if e["status"] == "open"}
    last_closed: dict[str, str] = {}
    for e in entries:
        if e["status"] == "closed" and e.get("closed"):
            last_closed[e["id"]] = max(last_closed.get(e["id"], ""), e["closed"])
    now_listed = {p["id"] for p in picks}
    for p in picks:
        if p["id"] in followed:
            continue
        closed = last_closed.get(p["id"])
        if closed and (asof_ts - pd.Timestamp(closed)).days < cooldown:
            continue
        e = _new_entry(p, prices, asof_s)
        if e is not None:
            entries.append(e)
            followed.add(p["id"])

    # 2. follow every share, open or finished
    rows, by_age = [], {d: [] for d in horizons}
    for e in entries:
        if e["status"] == "open":
            e["listed_now"] = e["id"] in now_listed
        try:
            m = _follow(e, prices, asof_ts, max_days, horizons)
        except Exception as exc:  # one bad entry must not stop the run
            e["error"] = str(exc)[:120]
            m = None
        rows.append((e, m))
        if m:
            for d, a in m["ages"].items():
                by_age[d].append(a)

    out_rows = []
    rows.sort(key=lambda t: t[0]["sym"])
    rows.sort(key=lambda t: t[0]["listed"], reverse=True)  # newest first, symbols in order within a day (the sort is stable)
    for e, m in rows:
        row = {
            "id": e["id"], "sym": e["sym"], "name": e["name"], "sector": e.get("sector"), "listed": e["listed"], "cents": e["cents"],
            "status": e["status"], "reason": e.get("reason"), "closed": e.get("closed"), "listed_now": bool(e.get("listed_now")) if e["status"] == "open" else None,
            "t1_date": e.get("t1_date"), "t2_date": e.get("t2_date"), "review_date": e.get("review_date"),
        }
        if m:
            row.update({
                "days": (m["end"] - pd.Timestamp(e["listed"])).days, "px": _r(m["px0"], 2), "last": _r(m["last"], 2), "fair": _r(m["fair"], 2),
                "target1": _r(m["t1"], 2), "target2": _r(m["t2"], 2), "review_below": _r(m["rv"], 2),
                "ret_pct": _pct(m["ret"]), "index_pct": _pct(m["index"]), "typical_pct": _pct(m["typical"]),
                "excess_index_pct": _pct(None if m["index"] is None else m["ret"] - m["index"]),
                "excess_typical_pct": _pct(None if m["typical"] is None else m["ret"] - m["typical"]),
                "gap_closed_pct": _pct(m["gap"]),
            })
        out_rows.append(row)

    n_open = sum(1 for e in entries if e["status"] == "open")
    horizon_rows = [_horizon_row(d, by_age[d], min_shares) for d in horizons]
    ages = [r["days"] for r in out_rows if r.get("days") is not None]
    verdict = "none" if not entries else ("readable" if any(h["ready"] for h in horizon_rows) else "early")
    view = {
        "since": state.get("since"), "asof": asof_s, "verdict": verdict, "min_shares": min_shares,
        "counts": {"tracked": len(entries), "open": n_open, "closed": len(entries) - n_open, "listed_now": sum(1 for r in out_rows if r["listed_now"]),
                   "reached_fair_value": sum(1 for e in entries if e.get("t2_date")), "below_review": sum(1 for e in entries if e.get("review_date"))},
        "oldest_days": max(ages) if ages else 0,
        "horizons": horizon_rows, "rows": out_rows,
        "caveats": [
            "Each share is logged the day it first passes every check, at the price on its card that day. A buyer could only have traded the next session, and no costs or taxes are deducted.",
            "Prices are adjusted for bonus issues, splits and dividends. The KSE-100 is a price index and leaves dividends out, which flatters the shares, so the typical share (the median of every liquid Pakistani share, adjusted the same way) is the fairer yardstick.",
            f"A share stays in the record when it is later dropped from the list. It is followed until it reaches the fair value it was listed with, or until {months} months have passed, so the losers are not forgotten.",
            "Shares listed in the same weeks share the same market moves: a handful of shares that all agree still says little. An age is read only once enough shares have been followed that long.",
        ],
    }
    return state, view
