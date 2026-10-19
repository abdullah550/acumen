"""Calibration: the conviction -> outcome curve must be monotone, honest about thin data, and only trusted after an
out-of-sample check."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sst

from acumen.analysis import calibrate as cal


def make_trades(n, seed, slope=0.0, base=0.40, span_days=1000):
    """Synthetic trades. P(win) = base + slope * (conviction - 30); wins pay 0.5-2.5R, losses cost 0.5-1R."""
    rng = np.random.default_rng(seed)
    conv = rng.integers(30, 71, n).astype(float)
    p = np.clip(base + slope * (conv - 30), 0.02, 0.98)
    win = rng.random(n) < p
    r = np.where(win, rng.uniform(0.5, 2.5, n), -rng.uniform(0.5, 1.0, n))
    days = np.sort(rng.integers(0, span_days, n))
    return pd.DataFrame({
        "conv": conv, "r": r, "hit1": (r > 0.9).astype(float), "hit2": (r > 1.8).astype(float),
        "bars_t1": np.where(r > 0.9, rng.integers(3, 30, n), np.nan), "bars_t2": np.where(r > 1.8, rng.integers(5, 40, n), np.nan),
        "entry_date": pd.Timestamp("2022-01-03") + pd.to_timedelta(days, unit="D"),
    })


def test_one_lucky_trade_is_not_a_certainty():
    t = make_trades(600, seed=1)
    conv = np.append(t["conv"].to_numpy(), 95.0)
    win = np.append((t["r"] > 0).astype(float).to_numpy(), 1.0)
    curve = cal.fit_curve(conv, win)
    top = curve[list(cal.GRID).index(95.0)]
    assert top < 0.6, "a single winner at the top must not produce a near-certain win rate"
    assert top < (win.mean() + 0.2)


def test_curve_is_monotone_and_a_probability():
    t = make_trades(900, seed=2, slope=0.003)
    curve = cal.fit_curve(t["conv"], (t["r"] > 0).astype(float))
    assert np.all(np.diff(curve) >= -1e-12)
    assert curve.min() >= 0 and curve.max() <= 1


def test_thin_groups_move_less_than_dense_ones():
    rng = np.random.default_rng(5)
    conv = np.concatenate([rng.integers(40, 56, 1000), np.full(3, 90)]).astype(float)
    win = np.concatenate([rng.random(1000) < 0.4, np.ones(3)]).astype(float)
    curve = cal.fit_curve(conv, win)
    # three straight winners add only a small step above the pooled rate
    assert curve[list(cal.GRID).index(90.0)] - win.mean() < 0.12


def test_recovers_a_real_relationship():
    t = make_trades(4000, seed=3, slope=0.005)
    out = cal.calibrate(t, min_trades=200)
    assert out["status"] == "calibrated" and out["calibrated"] is True
    m = dict((int(a), b) for a, b in out["mapping"])
    assert m[65] > m[35] + 0.08, "higher conviction should map to a clearly higher win probability"
    assert out["oos"]["passed"] and out["oos"]["spearman"] > 0


def test_pure_noise_is_not_validated():
    flags = [cal.calibrate(make_trades(3000, seed=s), min_trades=200)["calibrated"] for s in (11, 12, 13, 14, 15)]
    assert sum(flags) <= 1, "noise should pass the out-of-sample check only about 1 time in 20"


def test_noise_mapping_is_flat():
    out = cal.calibrate(make_trades(3000, seed=21), min_trades=200)
    if out["status"] == "unvalidated":
        vals = {round(v, 6) for _, v in out["mapping"]}
        assert len(vals) == 1, "when conviction is not validated every conviction maps to the pooled base rate"
        assert "screening rank" in out["note"]


def test_too_few_trades_stays_flat():
    out = cal.calibrate(make_trades(150, seed=4, slope=0.01), min_trades=200)
    assert out["status"] == "insufficient"
    assert len({round(v, 6) for _, v in out["mapping"]}) == 1
    assert out["oos"]["spearman"] is None


def test_edge_against_random_entries():
    t = make_trades(1500, seed=6)
    rng = np.random.default_rng(9)
    worse = rng.normal(-0.05, 1.0, 2500)
    out = cal.calibrate(t, min_trades=200, baseline=worse)
    assert out["baseline"]["n"] == 2500
    assert out["edge_r"] is not None and out["edge_se"] is not None and out["edge_se"] > 0
    assert abs(out["edge_r"] - (t["r"].mean() - worse.mean())) < 1e-3


def test_no_trades_is_reported_plainly():
    out = cal.calibrate(make_trades(0, seed=1), min_trades=200)
    assert out["status"] == "insufficient" and out["n_trades"] == 0 and out["note"]


# ---------------------------------------------------------------------------------------------- edge against random entries
def dated(n, mean, seed, months=60, sd=1.0, month_sd=0.0, active=None, instruments=0, inst_sd=0.0):
    """Long trades with a known mean result in R, spread over ``months`` blocks of 30 days (``active``: only these blocks).
    ``instruments``: give each trade one of that many instruments, each with its own lasting luck (``inst_sd``)."""
    rng = np.random.default_rng(seed)
    m = rng.integers(0, months, n) if active is None else rng.choice(active, n)
    day = m * 30 + rng.integers(0, 30, n)
    eff = rng.normal(0, month_sd, months)
    r = mean + eff[m] + rng.normal(0, sd, n)
    cols = {"entry_date": pd.Timestamp("2019-01-01") + pd.to_timedelta(day, unit="D"), "sign": 1}
    if instruments:
        inst = rng.integers(0, instruments, n)
        r = r + rng.normal(0, inst_sd, instruments)[inst]
        cols["inst"] = inst
    cols["r"] = r
    cols["r_gross"] = r
    return pd.DataFrame(cols)


def test_matched_excess_uses_only_later_entries_in_the_same_direction():
    trades = pd.DataFrame({"entry_date": pd.to_datetime(["2024-01-10", "2024-01-10"]), "sign": [1, -1], "r_gross": [1.0, 1.0]})
    base = pd.DataFrame({
        "entry_date": pd.to_datetime(["2024-01-08", "2024-01-10", "2024-01-12", "2024-01-16", "2024-01-11", "2024-01-11"]),
        "sign": [1, 1, 1, 1, -1, -1], "r_gross": [10.0, 2.0, 4.0, 100.0, -3.0, -5.0]})
    day, ex, ref = cal.matched_excess(trades, base, window=5, min_matched=2)
    # long: random longs on Jan 10 and 12 count (Jan 8 is before the plan's day, Jan 16 is beyond the window); short: the two shorts on Jan 11
    assert list(ref) == [3.0, -4.0] and list(ex) == [-2.0, 5.0]
    assert list(day) == [2, 2]  # days counted from the earliest date in either frame (Jan 8)
    _, ex3, _ = cal.matched_excess(trades, base, window=5, min_matched=3)
    assert np.isnan(ex3).all(), "a trade with fewer comparisons than the minimum is left out, not compared with a handful"


def test_matched_excess_does_not_penalise_a_trend_following_plan():
    # prices move at random. The plan opens a long only after a rally; a trade earns the next 10 days' moves. Nothing the plan
    # can see predicts them, so its difference to random entries must be zero. (Comparing with entries from *before* the plan's
    # day would charge it for the rally it had already missed.)
    diffs = []
    for seed in range(6):
        rng = np.random.default_rng(seed)
        n = 3000
        m = rng.normal(0, 1, n + 12)
        past = np.array([m[max(t - 3, 0):t].sum() for t in range(n)])
        fwd = np.array([m[t:t + 10].sum() for t in range(n)]) / np.sqrt(10)
        day = pd.Timestamp("2020-01-01") + pd.to_timedelta(np.arange(n), unit="D")
        plan_days = np.flatnonzero(past > 1.5)
        rnd_days = np.flatnonzero(rng.random(n) < 0.3)
        plan = pd.DataFrame({"entry_date": day[plan_days], "sign": 1, "r_gross": fwd[plan_days]})
        base = pd.DataFrame({"entry_date": day[rnd_days], "sign": 1, "r_gross": fwd[rnd_days]})
        _, ex, _ = cal.matched_excess(plan, base, window=7, min_matched=3)
        diffs.append(np.nanmean(ex))
    assert abs(float(np.mean(diffs))) < 0.05 and len(plan) > 300, diffs


def test_excess_variance_is_the_plain_variance_for_independent_trades():
    rng = np.random.default_rng(4)
    n = 4000
    day = rng.integers(0, 1500, n)
    q = rng.normal(0, 1, n)
    inst = rng.integers(0, 40, n)
    v, g = cal.excess_variance(day, q, inst, lag_days=30)
    assert g == 40 and 0.85 * n < v < 1.6 * n * q.var()  # a little above the plain figure: the larger of several estimates is taken
    v_time, g0 = cal.excess_variance(day, q, None, lag_days=30)
    assert g0 == 0 and 0.85 * n < v_time < 1.6 * n * q.var()


def test_edge_test_passes_a_real_edge():
    out = cal.edge_test(dated(1500, 0.25, 1, instruments=40), dated(3000, 0.0, 2), lag_days=30, match_days=7)
    assert out["tested"] and out["passed"], out
    assert 0.15 < out["edge_r"] < 0.35 and out["p"] < 0.001 and out["ci"][0] > 0 and out["recent_r"] > 0
    assert out["level"] == 0.98 and out["spans"] > 50 and out["instruments"] == 40 and out["matched"] == 1500


def test_edge_test_rarely_passes_pure_noise():
    passed = [cal.edge_test(dated(1500, 0.0, 100 + s, instruments=40), dated(3000, 0.0, 200 + s), lag_days=30, match_days=7)["passed"] for s in range(40)]
    assert sum(passed) <= 2, "at a one-sided 1% bar, noise should almost never pass"


def test_edge_test_needs_trades_history_and_a_comparison():
    thin = cal.edge_test(dated(120, 0.5, 1), dated(500, 0.0, 2), lag_days=30, min_trades=200)
    assert not thin["tested"] and "only 120 trades" in thin["why"]
    short = cal.edge_test(dated(1500, 0.5, 1, months=6), dated(3000, 0.0, 2, months=6), lag_days=30)
    assert not short["tested"] and not short["passed"] and "stretches" in short["why"] and short["edge_r"] > 0.3  # the estimate is still shown
    none = cal.edge_test(dated(1500, 0.5, 1), dated(10, 0.0, 2), lag_days=30)
    assert not none["tested"] and "random-entry" in none["why"]
    sparse = cal.edge_test(dated(1500, 0.5, 1), dated(60, 0.0, 2), lag_days=30)  # 60 random entries over five years: no trade has three around it
    assert not sparse["tested"] and "random entries around them" in sparse["why"]


def test_edge_test_treats_trades_of_the_same_months_as_one_piece_of_evidence():
    # the plan's results depend on the month in a way the random entries do not share: its average depends on which months
    # those were, which no amount of extra trades can average away. Trade-by-trade statistics would call this a clear edge.
    sig = dated(3000, 0.25, 5, month_sd=0.8)  # a small real edge, but far inside the month-to-month noise
    base = dated(6000, 0.0, 6)
    iid_se = float(np.sqrt(sig["r"].var() / len(sig) + base["r"].var() / len(base)))
    out = cal.edge_test(sig, base, lag_days=60, match_days=7)
    assert out["tested"] and out["se"] > 3 * iid_se


def test_edge_test_counts_each_instruments_luck_once():
    # the plan's trades sit in 8 instruments, each with its own lasting luck: the standard error must grow once the instruments are known
    sig = dated(2000, 0.0, 5, instruments=8, inst_sd=0.5)
    base = dated(3000, 0.0, 6)
    known = cal.edge_test(sig, base, lag_days=30)
    unknown = cal.edge_test(sig.drop(columns="inst"), base, lag_days=30)
    assert known["instruments"] == 8 and unknown["instruments"] == 0
    assert known["se"] > 2 * unknown["se"]


def test_edge_test_ignores_an_edge_that_has_faded():
    # a strong edge over the first 36 months, a slightly negative one over the last 24: the whole history looks clearly better
    # than random entries, but the newer trades no longer do
    old, new = dated(1200, 0.8, 1, months=36), dated(800, -0.1, 2, months=24)
    new["entry_date"] += pd.Timedelta(days=36 * 30)
    b_old, b_new = dated(2400, 0.0, 3, months=36), dated(1600, 0.0, 4, months=24)
    b_new["entry_date"] += pd.Timedelta(days=36 * 30)
    out = cal.edge_test(pd.concat([old, new], ignore_index=True), pd.concat([b_old, b_new], ignore_index=True), lag_days=30)
    assert out["tested"] and out["p"] < 0.01 and out["recent_r"] < 0
    assert not out["passed"] and "newer" in out["why"]


def test_calibrate_reports_and_uses_the_edge_test():
    t = make_trades(1500, seed=6, span_days=1700)
    base = dated(3000, -0.1, 2, months=57)
    base["entry_date"] += pd.Timestamp("2022-01-03") - pd.Timestamp("2019-01-01")  # the same calendar as make_trades
    t["r"] = t["r"] + 0.3  # the plan clearly beats the random entries
    out = cal.calibrate(t, min_trades=200, baseline=base, lag_days=30)
    e = out["edge"]
    assert e["tested"] and e["passed"] and e["n"] == 1500 and e["n_base"] == 3000
    assert out["edge_r"] == round(e["edge_r"], 4) and out["edge_se"] == round(e["se"], 4)  # the dependence-aware spread is the one published


def test_calibrate_with_undated_random_entries_cannot_pass():
    out = cal.calibrate(make_trades(1500, seed=6), min_trades=200, baseline=np.random.default_rng(1).normal(-0.3, 1.0, 1500))
    assert out["edge"]["tested"] is False and out["edge"]["passed"] is False and "no dates" in out["edge"]["why"]
    assert out["edge_r"] is not None and out["edge_se"] > 0  # the plain comparison is still there for display
    none = cal.calibrate(make_trades(1500, seed=6), min_trades=200)
    assert none["edge"]["passed"] is False and none["edge"]["why"]


# ---------------------------------------------------------------------------------- the pieces of the standard error, one by one
def test_bartlett_sum_matches_a_hand_calculation_and_cannot_go_negative():
    # x = 1, 1, 1, 1 with lag 2: 4 + 2(1 - 1/3)*3 + 2(1 - 2/3)*2 = 28/3
    assert cal._bartlett_sum(np.ones(4), 2) == pytest.approx(28 / 3)
    # a strictly alternating series has strongly negative autocorrelation: flat weights would give a negative variance here (-6)
    alt = np.array([(-1.0) ** i for i in range(10)])
    assert cal._bartlett_sum(alt, 3) == pytest.approx(1.0)
    rng = np.random.default_rng(0)
    for _ in range(50):
        x = np.cumsum(rng.normal(size=30)) * (-1.0) ** np.arange(30)
        assert cal._bartlett_sum(x, 8) >= 0


def test_excess_variance_is_never_below_the_trade_by_trade_figure():
    # two instruments whose differences cancel exactly by day and by instrument: time + instrument - trade would be negative here,
    # but the evidence cannot look better than the plain trade-by-trade figure
    v, g = cal.excess_variance(np.array([0, 0, 1, 1]), np.array([1.0, -1.0, -1.0, 1.0]), np.array([0, 1, 0, 1]), lag_days=1)
    assert g == 2 and v == pytest.approx(4.0)


def test_excess_variance_corrects_for_having_few_instruments():
    # one instrument always ahead of random entries, one always behind: per-instrument sums are +5 and -5, and with two clusters the
    # between-instrument variance is scaled by g/(g-1) = 2
    day = np.tile(np.arange(5), 2)
    q = np.array([1.0] * 5 + [-1.0] * 5)
    inst = np.array([0] * 5 + [1] * 5)
    v, g = cal.excess_variance(day, q, inst, lag_days=1)
    assert g == 2 and v == pytest.approx(100.0)


def test_excess_variance_depends_on_how_much_the_differences_vary_not_on_their_level():
    rng = np.random.default_rng(3)
    day, inst, q = rng.integers(0, 600, 800), rng.integers(0, 20, 800), rng.normal(0, 1, 800)
    a, b = cal.excess_variance(day, q, inst, 20), cal.excess_variance(day, q + 5.0, inst, 20)
    assert a[0] == pytest.approx(b[0]) and a[1] == b[1]
    assert cal.excess_variance(day, np.full(800, 2.0), inst, 20)[0] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------------- what edge_test reports
def test_edge_test_uses_a_t_distribution_with_the_smaller_of_instruments_and_stretches():
    out = cal.edge_test(dated(1500, 0.12, 1, instruments=6, inst_sd=0.2), dated(3000, 0.0, 2), lag_days=30, match_days=7)
    assert out["tested"] and out["instruments"] == 6 and out["df"] == 5 and out["spans"] > 20
    assert out["p"] == pytest.approx(float(sst.t.sf(out["edge_r"] / out["se"], 5)))


def test_edge_test_does_not_pass_on_too_few_newer_trades():
    # a huge edge over only 40 trades (the minimum is lowered for the test): the newest 40% is under 30 trades, so the check that
    # the edge is still there cannot be made, and a pass without it would be a pass on faith
    out = cal.edge_test(dated(40, 3.0, 1, sd=0.5), dated(4000, 0.0, 2), lag_days=30, match_days=7, min_trades=20)
    assert out["tested"] and out["p"] < 0.01 and out["recent_r"] is None
    assert not out["passed"] and "newer" in out["why"]


def test_edge_test_reports_a_plan_worse_than_random_with_a_large_p_value():
    out = cal.edge_test(dated(1500, -0.25, 1, instruments=40), dated(3000, 0.0, 2), lag_days=30, match_days=7)
    assert out["tested"] and out["edge_r"] < -0.1 and out["p"] > 0.99 and out["ci"][1] < 0 and not out["passed"]


def test_edge_test_interval_is_two_sided_at_the_level_the_test_uses():
    out = cal.edge_test(dated(1500, 0.1, 1, instruments=40), dated(3000, 0.0, 2), lag_days=30, match_days=7, alpha=0.02)
    lo, hi = out["ci"]
    assert out["level"] == pytest.approx(0.96)
    assert (lo + hi) / 2 == pytest.approx(out["edge_r"]) and (hi - lo) / 2 == pytest.approx(float(sst.t.ppf(0.98, out["df"])) * out["se"])
    assert (lo > 0) == (out["p"] < 0.02)  # the interval clears zero exactly when the one-sided test rejects


def test_edge_test_compares_results_before_costs():
    sig, base = dated(1500, 0.2, 1, instruments=40), dated(3000, 0.0, 2)
    sig["r"], base["r"] = sig["r"] - 0.5, base["r"] - 0.5  # after costs both look worse; the comparison is made before them
    out = cal.edge_test(sig, base, lag_days=30, match_days=7)
    assert out["matched"] == 1500 and out["plan_r"] == pytest.approx(float(sig["r_gross"].mean()))
    assert out["edge_r"] == pytest.approx(out["plan_r"] - out["base_r"]) and 0.1 < out["edge_r"] < 0.3


def test_calibrate_hands_its_settings_to_the_edge_test():
    t = make_trades(1500, seed=6, span_days=1700)
    base = dated(3000, -0.1, 2, months=57)
    base["entry_date"] += pd.Timestamp("2022-01-03") - pd.Timestamp("2019-01-01")
    e = cal.calibrate(t, min_trades=200, baseline=base, lag_days=45, match_days=10, edge_alpha=0.02)["edge"]
    assert e["tested"] and e["lag_days"] == 45 and e["match_days"] == 10 and e["alpha"] == 0.02 and e["level"] == pytest.approx(0.96)
