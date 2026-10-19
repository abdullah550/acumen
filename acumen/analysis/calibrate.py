"""Pooled calibration: what did signals of a given conviction actually deliver, net of costs?

The simulation produces trades (conviction at entry, result in R, whether target 1 / 2 were reached).
Pooling them across instruments of one market and horizon gives enough trades to estimate

* the probability a trade finishes positive (``win``) and its mean result (``r``),
* the probability of reaching target 1 and target 2 before the stop,
* typical bars to reach each target,

as a monotone function of conviction (isotonic regression: higher conviction is assumed never to be worse,
the data decides how much better). Conviction is grouped in steps of 5 and every group is pulled toward the pool
average by ``PRIOR_TRADES`` pseudo-trades, so a handful of lucky trades at the top cannot read as a certainty.
The result is only *used* when an out-of-sample check passes:
the curve is fitted on the older 60 percent of trades (by entry date) and must rank the newer 40 percent
in the right order. If it does not, the mapping is flattened to the pooled base rates and the status says so.
Conviction then works as a screening rank only, and the app shows that.

A second, separate check decides whether a pool may produce *actionable* ideas at all (:func:`edge_test`): do its trades
beat random entries that use the same stops, targets and costs? Every instrument in a market moves with the market, so a
rising market makes almost any long entry look good; only the difference to random entries measures skill. Each trade is
compared with random entries opened on the same day or just after, in the same direction. The uncertainty of the average
difference allows for what makes trades dependent: trades opened within a trade's own length of each other share the same
price moves (Newey-West over calendar days), and trades in one instrument share that instrument's luck (clusters by
instrument). The result must be clearly above zero, and the newer part of the history must agree.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sst
from scipy.optimize import isotonic_regression

GRID = np.arange(0, 101, 5, dtype=float)
BIN = 5                # conviction is grouped in steps of this size before fitting
PRIOR_TRADES = 20      # pseudo-trades at the pool average added to every group: thin groups barely move the curve
TRAIN_SHARE = 0.6
MIN_OOS = 100
ALPHA = 0.05
EDGE_ALPHA = 0.01      # one-sided; nine pools are examined at once, so the bar is deliberately high
MIN_SPANS = 10         # the history must be at least this many trade-lengths long before the standard error means anything
RECENT_SHARE = 0.4     # the newer share of trades must agree with the whole-history result
MIN_RECENT = 30        # plan trades needed in that newer part
MIN_MATCHED = 3        # random entries (same direction, same day or just after) a plan trade needs before it is compared with them
MATCH_DAYS = 7         # how many days after a plan trade's own day the random entries it is compared with may lie


def wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (float(max(0.0, c - h)), float(min(1.0, c + h)))


def fit_curve(conv: np.ndarray, y: np.ndarray, prior: float = PRIOR_TRADES) -> np.ndarray:
    """Monotone (non-decreasing) curve of ``y`` against conviction, evaluated on ``GRID``.

    Trades are grouped by conviction (steps of ``BIN``). Each group's mean is shrunk toward the pool mean as if it
    held ``prior`` extra trades at that mean, then the groups are made monotone by pooling adjacent violators.
    Without the shrinkage a group holding one winning trade would read as a 100 percent win rate.
    """
    conv = np.asarray(conv, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y) & np.isfinite(conv)
    conv, y = conv[ok], y[ok]
    if len(y) == 0:
        return np.full(len(GRID), np.nan)
    grp = np.clip(np.round(conv / BIN) * BIN, 0, 100)
    xs = np.unique(grp)
    mu = float(y.mean())
    sums = np.array([y[grp == x].sum() for x in xs])
    cnt = np.array([(grp == x).sum() for x in xs], dtype=float)
    est = (sums + prior * mu) / (cnt + prior)
    iso = isotonic_regression(est, weights=cnt + prior, increasing=True).x
    return np.interp(GRID, xs, iso)


def _pairs(curve: np.ndarray, nd: int = 4) -> list[list[float]]:
    return [[float(g), round(float(v), nd)] for g, v in zip(GRID, curve)]


def _interp(pairs: list[list[float]], conv: float) -> float:
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    return float(np.interp(conv, xs, ys))


def _pct(a: np.ndarray, q: float) -> float | None:
    a = a[np.isfinite(a)]
    return float(np.percentile(a, q)) if len(a) else None


def _cluster_boot(conv: np.ndarray, r: np.ndarray, clusters: np.ndarray, boot: int = 400, seed: int = 11) -> dict:
    """Bootstrap the out-of-sample rank correlation by resampling whole calendar months.

    Trades opened in the same month are not independent (instruments move together), so resampling
    trade by trade would make the evidence look stronger than it is.
    """
    rng = np.random.default_rng(seed)
    uniq, inv = np.unique(clusters, return_inverse=True)
    groups = [np.flatnonzero(inv == i) for i in range(len(uniq))]
    if len(groups) < 4:
        return {"p": 1.0, "rho_lo": None, "rho_hi": None}
    rhos = []
    for _ in range(boot):
        pick = rng.integers(0, len(groups), len(groups))
        idx = np.concatenate([groups[i] for i in pick])
        x, y = conv[idx], r[idx]
        if np.ptp(x) == 0 or np.ptp(y) == 0:
            continue
        rho = sst.spearmanr(x, y)[0]
        if np.isfinite(rho):
            rhos.append(rho)
    if len(rhos) < 50:
        return {"p": 1.0, "rho_lo": None, "rho_hi": None}
    rhos = np.asarray(rhos)
    return {"p": float((rhos <= 0).mean()), "rho_lo": float(np.percentile(rhos, 5)), "rho_hi": float(np.percentile(rhos, 95))}


def _edge_blank(n: int, n_base: int, lag_days: int, alpha: float, why: str, match_days: int | None = None) -> dict:
    return {"tested": False, "passed": False, "n": int(n), "n_base": int(n_base), "matched": 0, "instruments": 0, "spans": 0.0, "lag_days": int(lag_days),
            "match_days": None if match_days is None else int(match_days), "df": None, "edge_r": None, "se": None, "ci": None, "p": None, "recent_r": None,
            "plan_r": None, "base_r": None, "alpha": float(alpha), "level": round(1.0 - 2.0 * float(alpha), 4), "why": why}


def _r_column(frame: pd.DataFrame) -> np.ndarray:
    """Result before costs when the trades carry it, else the net result."""
    col = "r_gross" if "r_gross" in frame.columns else "r"
    return frame[col].astype(float).to_numpy()


def _day_index(dates: pd.Series, origin: pd.Timestamp) -> np.ndarray:
    return (pd.to_datetime(dates).dt.normalize() - origin).dt.days.to_numpy().astype(int)


def matched_excess(trades: pd.DataFrame, baseline: pd.DataFrame, window: int, min_matched: int = MIN_MATCHED) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every plan trade: its result minus the mean result of random entries opened in the same direction in ``[entry day, entry day + window]``.

    Returns ``(day index of each trade, excess (NaN where fewer than ``min_matched`` random entries qualify), the comparison mean)``.

    Why the window only looks forward. A plan trade opens on a day when the signals fired, which depends on what the price did
    *before* that day. A random entry made earlier in the same week would have collected whatever the market did in between, and
    the plan, which cannot act before its signal, could not: comparing with entries on both sides of the day therefore penalises a
    trend-following plan whenever trends happen to run (and flatters a contrarian one). Entries on or after the plan's day share
    its future and nothing of its past, so with no skill the expected difference is exactly zero.
    """
    origin = min(pd.to_datetime(trades["entry_date"]).min(), pd.to_datetime(baseline["entry_date"]).min()).normalize()
    si = _day_index(trades["entry_date"], origin)
    bi = _day_index(baseline["entry_date"], origin)
    sr, br = _r_column(trades), _r_column(baseline)
    span = int(max(si.max(), bi.max())) + int(window) + 2
    by_sign = "sign" in trades.columns and "sign" in baseline.columns
    ss = trades["sign"].astype(int).to_numpy() if by_sign else np.zeros(len(trades), dtype=int)
    bs = baseline["sign"].astype(int).to_numpy() if by_sign else np.zeros(len(baseline), dtype=int)
    excess = np.full(len(trades), np.nan)
    ref = np.full(len(trades), np.nan)
    for g in np.unique(ss):
        mb, ms = bs == g, ss == g
        if not mb.any():
            continue
        csum = np.concatenate([[0.0], np.cumsum(np.bincount(bi[mb], weights=br[mb], minlength=span))])
        ccnt = np.concatenate([[0.0], np.cumsum(np.bincount(bi[mb], minlength=span))])
        lo = si[ms]
        hi = np.minimum(lo + int(window), span - 1) + 1
        cnt = ccnt[hi] - ccnt[lo]
        tot = csum[hi] - csum[lo]
        ok = cnt >= min_matched
        mean = np.where(ok, tot / np.maximum(cnt, 1.0), np.nan)
        ref[ms] = mean
        excess[ms] = sr[ms] - mean
    return si, excess, ref


def _bartlett_sum(x: np.ndarray, lag: int) -> float:
    """Variance of the sum of a series whose values are correlated up to ``lag`` steps apart (Newey-West weights, which cannot go negative)."""
    total = float(x @ x)
    lag = int(lag)
    for k in range(1, min(lag, len(x) - 1) + 1):
        total += 2.0 * (1.0 - k / (lag + 1.0)) * float(x[:-k] @ x[k:])
    return total


def excess_variance(day: np.ndarray, excess: np.ndarray, inst: np.ndarray | None, lag_days: int) -> tuple[float, int]:
    """Variance of the *sum* of the per-trade differences, allowing for the two ways trades are not independent.

    * Time: trades opened within ``lag_days`` of each other share the same market moves, and so do trades opened on one
      day. The differences are added up per calendar day and Newey-West weighted over ``lag_days``.
    * Instrument: the trades of one instrument share that instrument's luck over the whole history (a stock that
      happened to rise for five years makes every long in it look good). The differences are added up per instrument.

    The two are combined the way two-way clustering does (time + instrument - trade by trade) and the result is never
    below either one alone or the trade-by-trade figure, so an unlucky draw cannot make the evidence look better than that.
    Returns ``(variance of the sum, number of instruments)``.
    """
    u = excess - excess.mean()
    d = day - day.min()
    daily = np.bincount(d, weights=u, minlength=int(d.max()) + 1)
    v_time = _bartlett_sum(daily, lag_days)
    v_trade = float(u @ u)
    if inst is None:
        return max(v_time, v_trade), 0
    ids, inv = np.unique(inst, return_inverse=True)
    g = len(ids)
    per = np.bincount(inv, weights=u, minlength=g)
    v_inst = float(per @ per) * (g / max(g - 1, 1))
    return max(v_time + v_inst - v_trade, v_time, v_inst, v_trade), g


def edge_test(
    trades: pd.DataFrame,
    baseline: pd.DataFrame | None,
    *,
    lag_days: int = 30,
    match_days: int | None = None,
    min_trades: int = 200,
    alpha: float = EDGE_ALPHA,
    min_spans: float = MIN_SPANS,
    min_matched: int = MIN_MATCHED,
) -> dict:
    """Do the plan's entries beat random entries that use the same exits? (``trades`` and ``baseline`` need ``entry_date``, ``sign`` and ``r_gross``;
    ``trades`` may carry ``inst``, the instrument, which makes the uncertainty allow for instrument-level luck.)

    The comparison is made before costs (``r_gross``, falling back to ``r``): it asks whether the entries were better timed
    than random ones, and a plan whose stops happen to sit closer to the entry would otherwise look worse or better only
    because costs weigh more or less against a small risk. Whether the plan still makes money after costs is a separate
    check on the pooled net result.

    Every plan trade is compared with the random entries opened in the same direction on its own day or in the next
    ``match_days`` days (see :func:`matched_excess`), so the market's moves, which hit both, cancel; a plan trade with fewer
    than ``min_matched`` such entries is left out. The edge is the average of those differences. Its standard error comes from
    :func:`excess_variance`; ``lag_days`` is how long one trade's result stays tied to another's, in practice the longest time a
    trade stays open plus ``match_days``. The one-sided p-value uses a t distribution on the smaller of the number of instruments
    and the number of ``lag_days`` stretches in the history, which is deliberately cautious for short histories. ``passed``
    needs all of: enough trades, a history of at least ``min_spans`` stretches, an estimate above zero with ``p < alpha``, and
    the newer ``RECENT_SHARE`` of the trades also ahead of random entries (an edge that has faded does not count).
    """
    n = int(len(trades))
    window = int(match_days) if match_days else MATCH_DAYS
    lag = max(int(lag_days), window, 1)
    if baseline is None or len(baseline) < 30:
        return _edge_blank(n, 0 if baseline is None else len(baseline), lag, alpha, "too few random-entry trades to compare with", window)
    out = _edge_blank(n, len(baseline), lag, alpha, "", window)
    if n < min_trades:
        out["why"] = f"only {n} trades (need {min_trades})"
        return out
    day, excess, ref = matched_excess(trades, baseline, window, min_matched)
    use = np.isfinite(excess)
    m = int(use.sum())
    out["matched"] = m
    if m < min_trades:
        out["why"] = f"only {m} of {n} trades have {min_matched} random entries around them to compare with (need {min_trades})"
        return out
    d, q = day[use], excess[use]
    out["plan_r"], out["base_r"] = float(_r_column(trades)[use].mean()), float(ref[use].mean())
    spans = float(d.max() - d.min() + 1) / lag
    out["spans"] = spans
    point = float(q.mean())
    out["edge_r"] = point
    if spans < min_spans:
        out["why"] = f"the trades cover {spans:.1f} stretches of {lag} days (need {min_spans:g}); a horizon this long cannot be tested with this much history"
        return out
    inst = trades["inst"].to_numpy()[use] if "inst" in trades.columns else None
    var, groups = excess_variance(d, q, inst, lag)
    out["instruments"] = groups
    se = float(np.sqrt(var)) / m
    df = max(int(min(groups, spans) if groups else spans) - 1, 1)
    z = point / se if se > 0 else (np.inf if point > 0 else -np.inf)
    p = float(sst.t.sf(z, df))
    half = float(sst.t.ppf(1.0 - alpha, df) * se)
    cut = np.quantile(d, 1.0 - RECENT_SHARE)  # the date that leaves the newest share of the trades
    newer = d >= cut
    recent = float(q[newer].mean()) if int(newer.sum()) >= MIN_RECENT else None
    out.update({"tested": True, "se": se, "df": df, "ci": [point - half, point + half], "p": p, "recent_r": recent})
    if point <= 0 or p >= alpha:
        out["why"] = "not clearly better than random entries" if point > 0 else "no better than random entries"
    elif recent is None:
        out["why"] = "too few newer trades to confirm that the edge is still there"
    elif recent <= 0:
        out["why"] = "the newer trades no longer beat random entries"
    else:
        out["passed"] = True
    return out


def calibrate(
    trades: pd.DataFrame,
    *,
    min_trades: int = 200,
    default_eta: tuple[int, int] = (5, 20),
    baseline: pd.DataFrame | np.ndarray | None = None,
    lag_days: int = 30,
    match_days: int | None = None,
    edge_alpha: float = EDGE_ALPHA,
    min_spans: float = MIN_SPANS,
    min_matched: int = MIN_MATCHED,
) -> dict:
    """Calibration block for one (market, horizon) pool. See the module docstring.

    ``trades`` needs columns ``conv, r, hit1, hit2, bars_t1, bars_t2, entry_date`` (``approx_share`` optional).
    With fewer than ``min_trades`` rows, or no usable out-of-sample check, the mapping is flat.
    ``baseline`` holds the random-entry trades (a frame with ``r`` and ``entry_date``, or just an array of R values, which can
    be compared but not tested for significance because it carries no dates); ``lag_days`` is how long a trade's result stays tied
    to its neighbours' in the edge test (the longest time a trade stays open at this horizon, in calendar days, plus ``match_days``);
    ``match_days`` is how far ahead of a plan trade's day the random entries it is compared with may lie.
    """
    n = int(len(trades))
    base = {"win": None, "avg_r": None, "hit1": None, "hit2": None}
    out: dict = {
        "status": "insufficient", "calibrated": False, "n_trades": n, **base,
        "buckets": [], "mapping": [], "mapping_r": [], "mapping_t1": [], "mapping_t2": [],
        "oos": {"n": 0, "spearman": None, "p": None, "top_half_avg_r": None, "bottom_half_avg_r": None, "passed": False},
        "eta_bars": {"t1": [default_eta[0], default_eta[1]], "t2": [default_eta[0] * 2, default_eta[1] * 2], "empirical": False},
        "note": "", "baseline": None, "edge_r": None, "edge_se": None,
        "edge": _edge_blank(n, 0, lag_days, edge_alpha, "No trades in the backtest window." if n == 0 else "not tested", match_days),
    }
    base_frame = baseline if hasattr(baseline, "columns") else None
    base_r = None if baseline is None else (baseline["r"].astype(float).to_numpy() if base_frame is not None else np.asarray(baseline, dtype=float))
    if base_r is not None and len(base_r) >= 30:
        out["baseline"] = {"n": int(len(base_r)), "win": round(float((base_r > 0).mean()), 4), "avg_r": round(float(base_r.mean()), 4)}
    if n == 0:
        out["note"] = "No trades in the backtest window."
        flat = np.full(len(GRID), np.nan)
        for key in ("mapping", "mapping_r", "mapping_t1", "mapping_t2"):
            out[key] = _pairs(flat)
        return out
    t = trades.copy()
    t["r"] = t["r"].astype(float)
    conv = t["conv"].astype(float).to_numpy()
    r = t["r"].to_numpy()
    win = (r > 0).astype(float)
    h1 = t["hit1"].astype(float).to_numpy()
    h2 = t["hit2"].astype(float).to_numpy()
    out.update({"win": round(float(win.mean()), 4), "avg_r": round(float(r.mean()), 4),
                "hit1": round(float(h1.mean()), 4), "hit2": round(float(h2.mean()), 4)})
    if "approx_share" in t:
        out["close_basis_share"] = round(float((t["approx_share"].astype(float) > 0.5).mean()), 3)

    # buckets for display (always, even when not validated)
    edges = list(range(30, 101, 10))
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conv >= lo) & (conv < hi) if hi < 100 else (conv >= lo)
        k = int(m.sum())
        if k == 0:
            continue
        ci = wilson(float(win[m].sum()), k)
        out["buckets"].append({"lo": lo, "hi": hi, "n": k, "win": round(float(win[m].mean()), 3),
                               "avg_r": round(float(r[m].mean()), 3), "ci": [round(ci[0], 3), round(ci[1], 3)]})

    # eta: bars to reach each target, from trades that got there
    e1 = t["bars_t1"].astype(float).to_numpy()
    e2 = t["bars_t2"].astype(float).to_numpy()
    if np.isfinite(e1).sum() >= 15:
        out["eta_bars"]["t1"] = [int(round(_pct(e1, 25))), int(round(_pct(e1, 75)))]
        out["eta_bars"]["empirical"] = True
    if np.isfinite(e2).sum() >= 10:
        out["eta_bars"]["t2"] = [int(round(_pct(e2, 25))), int(round(_pct(e2, 75)))]

    if out["baseline"] is not None:
        b = base_r
        out["edge_r"] = round(float(r.mean() - b.mean()), 4)
        out["edge_se"] = round(float(np.sqrt(r.var(ddof=1) / len(r) + b.var(ddof=1) / len(b))), 4) if len(r) > 1 and len(b) > 1 else None
    if baseline is not None and base_frame is None:
        edge = _edge_blank(n, len(base_r), lag_days, edge_alpha, "the random-entry trades carry no dates, so their spread cannot be measured", match_days)
    else:
        edge = edge_test(t, base_frame, lag_days=lag_days, match_days=match_days, min_trades=min_trades, alpha=edge_alpha, min_spans=min_spans, min_matched=min_matched)
    out["edge"] = {k: (round(v, 4) if isinstance(v, float) else [round(x, 4) for x in v] if isinstance(v, list) else v) for k, v in edge.items()}
    if edge["tested"]:  # the spread that allows for dependence replaces the one that treats every trade as independent
        out["edge_r"], out["edge_se"] = round(edge["edge_r"], 4), round(edge["se"], 4)
    flat = {"win": np.full(len(GRID), win.mean()), "r": np.full(len(GRID), r.mean()),
            "t1": np.full(len(GRID), h1.mean()), "t2": np.full(len(GRID), h2.mean())}
    if n < min_trades:
        out["note"] = f"Only {n} trades (need {min_trades}). Probabilities shown are the pooled base rates, not a function of conviction."
        curves = flat
    else:
        t = t.sort_values("entry_date")
        k = int(n * TRAIN_SHARE)
        tr, te = t.iloc[:k], t.iloc[k:]
        oos = out["oos"]
        oos["n"] = int(len(te))
        passed = False
        if len(te) >= MIN_OOS:
            rho, p = sst.spearmanr(te["conv"].astype(float), te["r"].astype(float))
            med = float(te["conv"].astype(float).median())
            top = te[te["conv"].astype(float) >= med]["r"].astype(float)
            bot = te[te["conv"].astype(float) < med]["r"].astype(float)
            rho = float(rho) if np.isfinite(rho) else 0.0
            months = pd.to_datetime(te["entry_date"]).dt.to_period("M").astype(str).to_numpy()
            cb = _cluster_boot(te["conv"].astype(float).to_numpy(), te["r"].astype(float).to_numpy(), months)
            oos.update({"spearman": round(rho, 3), "p": round(cb["p"], 3),
                        "rho_ci": None if cb["rho_lo"] is None else [round(cb["rho_lo"], 3), round(cb["rho_hi"], 3)],
                        "top_half_avg_r": round(float(top.mean()), 3) if len(top) else None,
                        "bottom_half_avg_r": round(float(bot.mean()), 3) if len(bot) else None})
            passed = bool(rho > 0 and cb["p"] < ALPHA and len(top) and len(bot) and top.mean() > bot.mean())
            oos["passed"] = passed
        if passed:
            out["status"], out["calibrated"] = "calibrated", True
            out["note"] = ("Conviction ranked newer trades correctly out of sample. Trades are not independent "
                           "(many instruments moved together), so treat the intervals by conviction band as optimistic.")
            curves = {"win": fit_curve(conv, win), "r": fit_curve(conv, r), "t1": fit_curve(conv, h1), "t2": fit_curve(conv, h2)}
        else:
            out["status"] = "unvalidated"
            why = "too few out-of-sample trades" if len(te) < MIN_OOS else "conviction did not rank newer trades reliably"
            out["note"] = f"Not validated ({why}). Probabilities shown are pooled base rates; use conviction only as a screening rank."
            curves = flat
    out["mapping"] = _pairs(curves["win"])
    out["mapping_r"] = _pairs(curves["r"])
    out["mapping_t1"] = _pairs(curves["t1"])
    out["mapping_t2"] = _pairs(curves["t2"])
    return out


def predict(cal: dict | None, conv: float) -> dict:
    """Look up modelled outcomes for a conviction score. Returns ``None`` values when nothing is known."""
    if not cal or not cal.get("mapping") or not np.isfinite(cal["mapping"][0][1]):
        return {"p_win": None, "exp_r": None, "p_t1": None, "p_t2": None, "calibrated": False, "status": (cal or {}).get("status", "insufficient")}
    return {
        "p_win": _interp(cal["mapping"], conv),
        "exp_r": _interp(cal["mapping_r"], conv),
        "p_t1": _interp(cal["mapping_t1"], conv),
        "p_t2": _interp(cal["mapping_t2"], conv),
        "calibrated": bool(cal.get("calibrated")),
        "status": cal.get("status", "insufficient"),
    }


def blend_with_own(pool_value: float | None, own_value: float | None, n_own: int, k: float, cap: float = 0.4) -> float | None:
    """Shrink an instrument's own historical result toward the pooled value (own weight at most ``cap``)."""
    if pool_value is None:
        return None
    if own_value is None or n_own <= 0:
        return pool_value
    w = min(cap, n_own / (n_own + float(k)))
    return float((1 - w) * pool_value + w * own_value)
