"""Did the 7- and 30-day flow measures rank what happened next?

The question
------------
At every fifth trading day ("anchor"), using only data up to that day: rank the sectors by the flow measure, then look at what each
sector did over the *following* 5, 10 and 21 trading days relative to the whole liquid market. A measure that tells where money is going
should line up with that: the sectors it ranks high should do better than the ones it ranks low.

How it is scored
----------------
* Rank correlation (Spearman) between the measure and the later relative return, one number per anchor; the verdict reads the average
  and its t-statistic. Anchors every five days with 10- and 21-day looks ahead overlap, so the t-statistic uses Newey-West standard
  errors with a lag that covers the overlap.
* The gap between the best third and the worst third of sectors (a fifth of shares for single shares), as an average return per period.
* Two tests were fixed before looking: the 7-day measure against the next 5 days and the 30-day measure against the next 21 days. They need
  |t| >= ``primary_t`` (2.5). Every other combination is shown too, but with eleven tests some will look good by luck, so they need
  |t| >= ``other_t`` (3.0) to be called out.
* Fewer than ``min_anchors`` anchors: no verdict.

What it cannot do
-----------------
Sectors and shares are today's liquid names, so a share that was delisted is not in the past; prices are adjusted for dividends,
bonus and splits (as stored); trading costs are not charged. A positive result would still need an out-of-sample record: the weekly archive
in the store starts one now.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

HORIZONS = (5, 10, 21)  # trading days
STEP = 5  # trading days between anchors
WARMUP_DAYS = 100  # calendar days of history before the first anchor
PRIMARY = (("fast", 5), ("slow", 21))
SIGNAL_LABEL = {"fast": "7-day", "slow": "30-day", "both": "7- and 30-day together"}


def newey_west(x: np.ndarray | pd.Series, lag: int) -> tuple[float, float]:
    """Mean and t-statistic of a series whose neighbours are correlated (Bartlett weights up to ``lag``). ``(nan, nan)`` when too short."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = len(a)
    if n < 8:
        return float("nan"), float("nan")
    mu = float(a.mean())
    e = a - mu
    var = float((e * e).sum() / n)
    for k in range(1, min(lag, n - 1) + 1):
        var += 2.0 * (1.0 - k / (lag + 1.0)) * float((e[k:] * e[:-k]).sum() / n)
    se = math.sqrt(max(var, 1e-18) / n)
    return mu, mu / se


def _rank_ic(a: pd.Series, b: pd.Series, min_n: int) -> float:
    m = a.notna() & b.notna()
    if int(m.sum()) < min_n:
        return float("nan")
    ra, rb = a[m].rank(), b[m].rank()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(ra.corr(rb))


def _spread(a: pd.Series, b: pd.Series, frac: float, min_n: int) -> float:
    m = a.notna() & b.notna()
    if int(m.sum()) < min_n:
        return float("nan")
    q = a[m].rank(pct=True)
    top, bottom = b[m][q >= 1.0 - frac], b[m][q <= frac]
    if not len(top) or not len(bottom):
        return float("nan")
    return float(top.mean() - bottom.mean())


def _forward(panel: Any, h: int) -> pd.DataFrame:
    return panel.close.shift(-h) / panel.close - 1.0


def _sector_forward(panel: Any, sectors: list[str], h: int) -> pd.DataFrame:
    r = _forward(panel, h)
    by = pd.DataFrame({s: r[list(panel.sector[panel.sector == s].index)].mean(axis=1) for s in sectors}, index=r.index)
    return by.sub(r.mean(axis=1), axis=0)


def _series(ind: pd.DataFrame, fwd: pd.DataFrame, anchors: pd.DatetimeIndex, min_n: int, frac: float) -> tuple[pd.Series, pd.Series]:
    ics, sps = {}, {}
    for d in anchors:
        a, b = ind.loc[d], fwd.loc[d]
        ic = _rank_ic(a, b, min_n)
        if math.isfinite(ic):
            ics[d] = ic
            sps[d] = _spread(a, b, frac, min_n)
    return pd.Series(ics, dtype=float), pd.Series(sps, dtype=float)


def _judge(ic_t: float, ic: float, thr: float) -> str:
    if not math.isfinite(ic_t):
        return "none"
    if ic_t >= thr and ic > 0:
        return "supported"
    if ic_t <= -thr and ic < 0:
        return "reversed"
    return "none"


def _test(tid: str, level: str, signal: str, h: int, ind: pd.DataFrame, fwd: pd.DataFrame, anchors: pd.DatetimeIndex, min_n: int, frac: float, primary: bool, thr: float) -> dict[str, Any]:
    ics, sps = _series(ind, fwd, anchors, min_n, frac)
    lag = int(math.ceil(h / STEP))
    ic, t = newey_west(ics.values, lag)
    sp, sp_t = newey_west(sps.values, lag)
    return {
        "id": tid, "level": level, "signal": signal, "label": SIGNAL_LABEL.get(signal, signal), "horizon": h, "primary": primary, "n": int(len(ics)),
        "ic": None if not math.isfinite(ic) else round(ic, 3), "t": None if not math.isfinite(t) else round(t, 2),
        "spread": None if not math.isfinite(sp) else round(sp * 100.0, 2), "spread_t": None if not math.isfinite(sp_t) else round(sp_t, 2),
        "hit": None if not len(sps.dropna()) else round(float((sps.dropna() > 0).mean()) * 100.0, 0),
        "verdict": _judge(t, ic, thr),
    }


def run(panel: Any, fr: Any, stock_fast: pd.DataFrame, stock_slow: pd.DataFrame, cfg_get) -> dict[str, Any]:
    """Every test, the overall verdict and a plain-language summary. ``panel`` and ``fr`` are the objects built by ``flows``; the stock frames hold
    each share's own 7- and 30-day buying pressure."""
    primary_t = float(cfg_get("flows.primary_t", 2.5))
    other_t = float(cfg_get("flows.other_t", 3.0))
    min_anchors = int(cfg_get("flows.min_anchors", 52))
    min_sectors = int(cfg_get("flows.backtest_min_sectors", 8))
    min_shares = int(cfg_get("flows.backtest_min_shares", 40))
    dates = panel.dates
    first = dates[0] + pd.Timedelta(days=WARMUP_DAYS)
    idx = [i for i in range(0, len(dates), STEP) if dates[i] >= first]
    anchors = dates[idx]
    out: dict[str, Any] = {
        "step_days": STEP, "horizons": list(HORIZONS), "primary_t": primary_t, "other_t": other_t, "min_anchors": min_anchors,
        "from": None, "to": None, "anchors": 0, "tests": [], "verdict": "too_short", "headline": "", "sector_line": "", "stock_lines": [], "notes": [],
    }
    if len(anchors) < 2:
        out["headline"] = "There is not enough price history to test the flow measures yet."
        return out
    last_ok = dates[-1 - min(HORIZONS)] if len(dates) > min(HORIZONS) else dates[-1]
    anchors = anchors[anchors <= last_ok]
    out["from"], out["to"], out["anchors"] = anchors[0].strftime("%Y-%m-%d"), anchors[-1].strftime("%Y-%m-%d"), int(len(anchors))
    sectors = fr.sectors
    sig = {"fast": fr.z[7], "slow": fr.z[30], "both": fr.score}
    tests: list[dict[str, Any]] = []
    for h in HORIZONS:
        fwd = _sector_forward(panel, sectors, h)
        for s in ("fast", "slow", "both"):
            prim = (s, h) in PRIMARY
            tests.append(_test(f"sector_{s}_{h}", "sector", s, h, sig[s], fwd, anchors, min_sectors, 1.0 / 3.0, prim, primary_t if prim else other_t))
    for s, h, ind in (("fast", 5, stock_fast), ("slow", 21, stock_slow)):
        fwd = _forward(panel, h)
        fwd = fwd.sub(fwd.mean(axis=1), axis=0)
        tests.append(_test(f"stock_{s}_{h}", "stock", s, h, ind, fwd, anchors, min_shares, 0.2, False, other_t))
    out["tests"] = tests

    prim = [t for t in tests if t["primary"]]
    sup = [t for t in prim if t["verdict"] == "supported"]
    rev = [t for t in prim if t["verdict"] == "reversed"]
    if out["anchors"] < min_anchors:
        out["verdict"] = "too_short"
    elif len(sup) == len(prim):
        out["verdict"] = "supported"
    elif sup:
        out["verdict"] = "partial"
    elif rev:
        out["verdict"] = "reversed"
    else:
        out["verdict"] = "not_shown"

    def phrase(t: dict[str, Any], what: str) -> str:
        if t["ic"] is None:
            return f"{what}: too little data"
        return f"{what}: rank correlation {t['ic']:+.2f} (t = {t['t']:+.1f})"
    p7 = next(t for t in tests if t["id"] == "sector_fast_5")
    p30 = next(t for t in tests if t["id"] == "sector_slow_21")
    out["sector_line"] = (f"Sector level, {out['anchors']} weekly checks from {out['from']}: " + phrase(p7, "the 7-day measure against the next 5 days") + "; "
                          + phrase(p30, "the 30-day measure against the next 21 days") + f". A real signal needs |t| of at least {primary_t:g}.")
    for k in ("fast", "slow"):
        t = next(x for x in tests if x["id"] == f"stock_{k}_{5 if k == 'fast' else 21}")
        if t["ic"] is None:
            continue
        gap = "" if t["spread"] is None else f"; best fifth minus worst fifth {t['spread']:+.2f}% (t = {t['spread_t']:+.1f}), before any costs"
        meaning = {"supported": ", so higher pressure went with higher returns", "reversed": ", so higher pressure went with slightly lower returns"}.get(t["verdict"], "")
        out["stock_lines"].append(f"Single shares, the {t['label']} measure against the next {t['horizon']} days: rank correlation {t['ic']:+.3f} (t = {t['t']:+.1f}){meaning}{gap}.")
    out["headline"] = {
        "supported": "Both fixed tests passed: the flow measures ranked later sector performance. Treat it as a lead worth following, not as proof: see the cautions.",
        "partial": "One of the two fixed tests passed and the other did not. That is not enough to call the measure a signal.",
        "reversed": "The measure ranked later sector performance the wrong way round: sectors with the strongest recent buying pressure did worse afterwards.",
        "not_shown": "The flow measures describe where money has been going. Over the stored history they did not rank which sectors did better afterwards.",
        "too_short": f"Only {out['anchors']} weekly checks are possible so far; at least {min_anchors} are needed before the result means anything.",
    }[out["verdict"]]
    out["notes"] = [
        "Sectors and shares are today's liquid names, so shares that left the market are missing from the past.",
        "Prices are as stored: adjusted for dividends, bonus shares and splits. Trading costs are not charged.",
        f"There are {len(tests)} tests here. With that many, a few will look good by chance; only the two fixed in advance carry the verdict.",
        "Overlapping look-aheads are handled with Newey-West standard errors; the weekly archive on this page starts an out-of-sample record.",
    ]
    return out
