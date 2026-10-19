import numpy as np
import pandas as pd
import pytest

from acumen.analysis import htf, indicators as ind, levels, patterns, stats
from tests._synth import make_ohlcv

DF = make_ohlcv(700, seed=3)
H, L, C, O, V = DF["high"], DF["low"], DF["close"], DF["open"], DF["volume"]


def close_tail(a, b, tail=250, rtol=2e-3, atol=1e-6):
    a, b = pd.Series(a).iloc[-tail:], pd.Series(b).iloc[-tail:]
    np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=rtol, atol=atol)


# ------------------------------------------------------------------ cross-checks against `ta`
def test_against_ta_library():
    ta = pytest.importorskip("ta")
    close_tail(ind.rsi(C, 14), ta.momentum.RSIIndicator(C, 14).rsi())
    close_tail(ind.atr(H, L, C, 14), ta.volatility.AverageTrueRange(H, L, C, 14).average_true_range())
    a, pdi, mdi = ind.adx(H, L, C, 14)
    t = ta.trend.ADXIndicator(H, L, C, 14)
    close_tail(a, t.adx(), rtol=5e-3, atol=1e-3)
    close_tail(pdi, t.adx_pos(), rtol=5e-3, atol=1e-3)
    m, s, h = ind.macd(C)
    tm = ta.trend.MACD(C, 26, 12, 9)
    close_tail(m, tm.macd(), rtol=1e-3, atol=1e-4)
    close_tail(s, tm.macd_signal(), rtol=1e-3, atol=1e-4)
    bb = ind.bollinger(C, 20, 2)
    tb = ta.volatility.BollingerBands(C, 20, 2)
    close_tail(bb["upper"], tb.bollinger_hband(), rtol=1e-3)
    close_tail(bb["pct_b"], tb.bollinger_pband(), rtol=1e-3, atol=1e-4)
    k, d = ind.stoch(H, L, C, 14, 3, 1)  # ta's %K is the raw (fast) stochastic; slow %K = smooth 3
    ts = ta.momentum.StochasticOscillator(H, L, C, 14, 3)
    close_tail(k, ts.stoch(), rtol=1e-3, atol=1e-3)
    close_tail(d, ts.stoch_signal(), rtol=1e-3, atol=1e-3)
    close_tail(ind.cci(H, L, C, 20), ta.trend.CCIIndicator(H, L, C, 20).cci(), rtol=1e-3, atol=1e-2)
    close_tail(ind.obv(C, V).diff(), ta.volume.OnBalanceVolumeIndicator(C, V).on_balance_volume().diff(), rtol=1e-6, atol=1.0)  # levels differ by a constant seed
    close_tail(ind.cmf(H, L, C, V, 20), ta.volume.ChaikinMoneyFlowIndicator(H, L, C, V, 20).chaikin_money_flow(), rtol=1e-3, atol=1e-4)
    close_tail(ind.mfi(H, L, C, V, 14), ta.volume.MFIIndicator(H, L, C, V, 14).money_flow_index(), rtol=1e-3, atol=1e-2)


# ------------------------------------------------------------------ golden values computed by explicit loops
def test_rsi_golden_loop():
    close = pd.Series(np.cumsum(np.random.default_rng(1).normal(0, 1, 120)) + 100)
    n = 14
    gains = np.maximum(close.diff().to_numpy(), 0)
    losses = np.maximum(-close.diff().to_numpy(), 0)
    avg_g = np.nanmean(gains[1 : n + 1])
    avg_l = np.nanmean(losses[1 : n + 1])
    for i in range(n + 1, len(close)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    expected = 100 - 100 / (1 + avg_g / avg_l)
    assert abs(ind.rsi(close, n).iloc[-1] - expected) < 0.05  # recursion seed differs, converged


def test_sma_ema_exact():
    s = pd.Series([1.0, 2, 3, 4, 5, 6])
    assert ind.sma(s, 3).iloc[-1] == pytest.approx(5.0)
    alpha = 2 / (3 + 1)
    e = s.iloc[0]
    for x in s.iloc[1:]:
        e = alpha * x + (1 - alpha) * e
    assert ind.ema(s, 3).iloc[-1] == pytest.approx(e)


def test_true_range_and_bounds():
    tr = ind.true_range(H, L, C)
    assert (tr.dropna() >= (H - L).dropna().reindex(tr.dropna().index) - 1e-12).all()
    r = ind.rsi(C)
    assert r.dropna().between(0, 100).all()
    s, d = ind.stoch(H, L, C)
    assert s.dropna().between(-1e-9, 100 + 1e-9).all()


def test_degenerate_inputs_do_not_raise_or_inf():
    flat = pd.DataFrame({k: [10.0] * 80 for k in ("open", "high", "low", "close")})
    flat["volume"] = 0.0
    fh, fl, fc, fv = flat["high"], flat["low"], flat["close"], flat["volume"]
    outs = [ind.rsi(fc), ind.stoch(fh, fl, fc)[0], ind.adx(fh, fl, fc)[0], ind.bollinger(fc)["pct_b"], ind.cmf(fh, fl, fc, fv), ind.mfi(fh, fl, fc, fv), ind.efficiency_ratio(fc), ind.zscore(fc), ind.supertrend(fh, fl, fc)[0], ind.linreg(fc)["r2"], ind.hurst(fc), ind.willr(fh, fl, fc), ind.rel_volume(fv)]
    for o in outs:
        assert not np.isinf(pd.Series(o).to_numpy(dtype=float)).any()
    short = C.iloc[:5]
    assert ind.sma(short, 20).isna().all()
    assert len(ind.linreg(short, 50)) == 5


def test_supertrend_direction_follows_trend():
    up = make_ohlcv(300, seed=1, drift=0.004, vol=0.006)
    line, d = ind.supertrend(up["high"], up["low"], up["close"])
    assert d.iloc[-1] == 1 and line.iloc[-1] < up["close"].iloc[-1]
    dn = make_ohlcv(300, seed=1, drift=-0.004, vol=0.006)
    line, d = ind.supertrend(dn["high"], dn["low"], dn["close"])
    assert d.iloc[-1] == -1 and line.iloc[-1] > dn["close"].iloc[-1]


def test_linreg_recovers_slope_and_r2():
    y = pd.Series(100 + 0.5 * np.arange(200.0))
    lr = ind.linreg(y, 50)
    assert lr["r2"].iloc[-1] == pytest.approx(1.0)
    assert lr["slope_pct"].iloc[-1] == pytest.approx(0.5 / y.iloc[-1] * 100)


def _ar1_prices(phi, n=3000, seed=8, vol=0.01):
    rng = np.random.default_rng(seed)
    e = rng.normal(0, vol, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = phi * r[i - 1] + e[i]
    return pd.Series(100 * np.exp(np.cumsum(r)))


def test_hurst_random_walk_persistent_and_mean_reverting():
    rw = _ar1_prices(0.0, seed=7)
    persistent = _ar1_prices(0.5)
    reverting = _ar1_prices(-0.5)
    h_rw, h_p, h_m = (ind.hurst(x, 250).iloc[-1] for x in (rw, persistent, reverting))
    assert 0.4 < h_rw < 0.6
    assert h_p > h_rw + 0.05 and h_m < h_rw - 0.05


def test_ichimoku_cloud_is_shifted_forward():
    ich = ind.ichimoku(H, L, C)
    assert ich["cloud_a"].iloc[:26 + 26].isna().any()
    raw_a = ((H.rolling(9).max() + L.rolling(9).min()) / 2 + (H.rolling(26).max() + L.rolling(26).min()) / 2) / 2
    assert ich["cloud_a"].iloc[-1] == pytest.approx(raw_a.iloc[-27])


# ------------------------------------------------------------------ higher time frame
def test_htf_no_lookahead_on_wednesday():
    df = make_ohlcv(120, seed=4)
    wk = htf.resample_ohlcv(df, "W-FRI", closed_only=True)
    aligned = htf.align_htf_to_daily(wk["close"], df.index)
    for ts in df.index:
        seen = aligned.loc[ts]
        if pd.isna(seen):
            continue
        # the value visible on ts must belong to a week that ended strictly before ts
        ended = wk.index[wk["close"] == seen]
        assert (ended < ts).any()
    wed = df.index[df.index.dayofweek == 2][10]
    week_end = wed + pd.Timedelta(days=2)  # that week's Friday
    assert aligned.loc[wed] != wk["close"].get(week_end, -1)


def test_resample_drops_incomplete_last_week():
    df = make_ohlcv(43, seed=5)  # ends mid-week for most seeds
    wk = htf.resample_ohlcv(df, "W-FRI", closed_only=True)
    assert wk.index[-1] <= df.index[-1]


# ------------------------------------------------------------------ levels
def test_swings_are_confirmed_late():
    sw = levels.swing_points(H, L, 3, 3)
    assert len(sw) > 10
    assert ((sw["confirm_pos"] - sw["pivot_pos"]) == 3).all()
    hi, lo = levels.swing_level_series(H, L)
    first_confirm = int(sw["confirm_pos"].min())
    assert hi.iloc[:first_confirm].isna().all() or lo.iloc[:first_confirm].isna().all()


def test_support_resistance_finds_planted_level():
    n = 300
    base = np.full(n, 100.0)
    x = np.arange(n)
    price = 100 + 5 * np.sin(x / 6.0)  # oscillates between 95 and 105
    df = pd.DataFrame({"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 1000.0}, index=pd.bdate_range("2022-01-03", periods=n))
    lv = levels.support_resistance(df, min_touches=3)
    pxs = [round(l.px) for l in lv]
    assert any(abs(p - 105) <= 1 for p in pxs) and any(abs(p - 95) <= 1 for p in pxs)


def test_volume_profile_poc_inside_range():
    vp = levels.volume_profile(DF)
    assert vp and DF["low"].iloc[-120:].min() <= vp["poc"] <= DF["high"].iloc[-120:].max()
    assert vp["val"] <= vp["poc"] <= vp["vah"]


# ------------------------------------------------------------------ patterns
def test_bullish_engulfing_detected():
    idx = pd.bdate_range("2023-01-02", periods=30)
    close = np.linspace(120, 100, 30)
    df = pd.DataFrame({"open": close + 0.5, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0}, index=idx)
    df.loc[idx[-2], ["open", "close", "high", "low"]] = [101.0, 100.0, 101.2, 99.8]
    df.loc[idx[-1], ["open", "close", "high", "low"]] = [99.8, 101.6, 101.8, 99.6]
    p = patterns.detect_all(df)
    assert bool(p["bullish_engulfing"].iloc[-1])
    assert patterns.candle_score(df).iloc[-1] > 0


def test_candle_score_bounded():
    s = patterns.candle_score(DF)
    assert s.between(-1, 1).all()


# ------------------------------------------------------------------ stats
def test_stats_basics():
    r = stats.simple_returns(C)
    assert stats.max_drawdown(C)[0] <= 0
    dd = stats.drawdown_series(C)
    assert dd.max() <= 0 and dd.min() == pytest.approx(stats.max_drawdown(C)[0])
    assert stats.var_hist(r, 0.95) < 0 and stats.cvar_hist(r, 0.95) <= stats.var_hist(r, 0.95)
    assert stats.beta(r, r) == pytest.approx(1.0)


# ------------------------------------------------------------------ causality: appending the future changes nothing
CUTS = [350, 500, 613]


def _series_outputs(df):
    h, l, c, o, v = df["high"], df["low"], df["close"], df["open"], df["volume"]
    out = {
        "sma": ind.sma(c, 20), "ema": ind.ema(c, 20), "wma": ind.wma(c, 10), "hma": ind.hma(c, 16), "kama": ind.kama(c), "vwma": ind.vwma(c, v, 20),
        "rsi": ind.rsi(c), "stoch_k": ind.stoch(h, l, c)[0], "stoch_d": ind.stoch(h, l, c)[1], "stochrsi": ind.stoch_rsi(c),
        "macd": ind.macd(c)[0], "macd_h": ind.macd(c)[2], "roc": ind.roc(c, 10), "cci": ind.cci(h, l, c), "willr": ind.willr(h, l, c),
        "mfi": ind.mfi(h, l, c, v), "aroon": ind.aroon(h, l)[2], "atr": ind.atr(h, l, c), "natr": ind.natr(h, l, c),
        "adx": ind.adx(h, l, c)[0], "pdi": ind.adx(h, l, c)[1], "bb_pct": ind.bollinger(c)["pct_b"], "bb_bw": ind.bollinger(c)["bandwidth"],
        "kc_up": ind.keltner(h, l, c)["upper"], "don": ind.donchian(h, l)[0], "hv": ind.hist_vol(c), "pk": ind.parkinson_vol(h, l),
        "sq": ind.squeeze(h, l, c).astype(float), "st_line": ind.supertrend(h, l, c)[0], "st_dir": ind.supertrend(h, l, c)[1], "psar": ind.psar(h, l),
        "ich_a": ind.ichimoku(h, l, c)["cloud_a"], "ich_b": ind.ichimoku(h, l, c)["cloud_b"], "ich_ch": ind.ichimoku(h, l, c)["chikou_bull"].astype(float),
        "lr_slope": ind.linreg(c)["slope_pct"], "lr_r2": ind.linreg(c)["r2"], "er": ind.efficiency_ratio(c), "hurst": ind.hurst(c, 100), "z": ind.zscore(c),
        "obv": ind.obv(c, v), "ad": ind.ad_line(h, l, c, v), "cmf": ind.cmf(h, l, c, v), "vwap": ind.vwap_rolling(h, l, c, v), "rv": ind.rel_volume(v),
        "force": ind.force_index(c, v), "pr": ind.percentile_rank(c, 100), "piv": ind.pivots_classic(h, l, c)["p"],
        "candle": patterns.candle_score(df), "wk_trend": htf.weekly_trend_state(df),
        "sw_hi": levels.swing_level_series(h, l)[0], "sw_lo": levels.swing_level_series(h, l)[1],
        "bo_up": levels.breakout_state(df)["up"].astype(float), "dd": stats.drawdown_series(c), "rvol": stats.simple_returns(c),
    }
    for name, col in patterns.detect_all(df).items():
        out[f"pat_{name}"] = col.astype(float)
    return out


def test_causality_all_series():
    full = _series_outputs(DF)
    for cut in CUTS:
        part = _series_outputs(DF.iloc[:cut])
        for name, s in part.items():
            a = s.to_numpy(dtype=float)
            b = full[name].iloc[:cut].to_numpy(dtype=float)
            np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True, err_msg=f"{name} changes when future bars are appended (cut={cut})")


def test_causality_support_resistance_and_profile():
    for cut in CUTS:
        a = levels.support_resistance(DF.iloc[:cut])
        b = levels.support_resistance(DF, t=cut - 1)
        assert [(round(x.px, 6), x.kind) for x in a] == [(round(x.px, 6), x.kind) for x in b]
        assert levels.volume_profile(DF.iloc[:cut]) == levels.volume_profile(DF, t=cut - 1)
