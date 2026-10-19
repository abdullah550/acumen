"""Causal feature frame for one instrument: every column at bar t uses only bars <= t."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .htf import weekly_trend_state
from .levels import swing_level_series
from .patterns import candle_score


def compute_features(df: pd.DataFrame, bench: pd.Series | None = None) -> pd.DataFrame:
    h, l, c, o, v = df["high"], df["low"], df["close"], df["open"], df["volume"]
    f = pd.DataFrame(index=df.index)
    f["open"], f["high"], f["low"], f["close"], f["volume"] = o, h, l, c, v
    for n in (20, 50, 100, 200):
        f[f"sma{n}"] = ind.sma(c, n)
    f["ema20"], f["ema50"] = ind.ema(c, 20), ind.ema(c, 50)
    f["rsi14"], f["rsi2"] = ind.rsi(c, 14), ind.rsi(c, 2)
    f["stoch_k"], f["stoch_d"] = ind.stoch(h, l, c)
    f["macd"], f["macd_sig"], f["macd_hist"] = ind.macd(c)
    f["adx"], f["pdi"], f["mdi"] = ind.adx(h, l, c)
    f["atr"] = ind.atr(h, l, c, 14)
    f["atrp"] = f["atr"] / c * 100.0
    bb = ind.bollinger(c)
    f["bb_pctb"], f["bb_bw"], f["bb_up"], f["bb_lo"] = bb["pct_b"], bb["bandwidth"], bb["upper"], bb["lower"]
    f["squeeze"] = ind.squeeze(h, l, c)
    for n in (20, 55):
        f[f"ref_hi{n}"] = h.rolling(n, min_periods=n).max().shift(1)
        f[f"ref_lo{n}"] = l.rolling(n, min_periods=n).min().shift(1)
        hi, lo = h.rolling(n, min_periods=n).max(), l.rolling(n, min_periods=n).min()
        f[f"pos{n}"] = ((c - lo) / (hi - lo).replace(0, np.nan)).clip(0, 1)
    f["st_line"], f["st_dir"] = ind.supertrend(h, l, c)
    ich = ind.ichimoku(h, l, c)
    f["cloud_top"] = ich[["cloud_a", "cloud_b"]].max(axis=1, skipna=False)
    f["cloud_bot"] = ich[["cloud_a", "cloud_b"]].min(axis=1, skipna=False)
    lr = ind.linreg(c, 50)
    f["lr_slope"], f["lr_r2"] = lr["slope_pct"], lr["r2"]
    for n in (5, 10, 20, 60, 120, 250):
        f[f"roc{n}"] = ind.roc(c, n)
    f["mom_12_1"] = (c.shift(21) / c.shift(252) - 1.0) * 100.0
    ret = np.log(c).diff()
    f["sigma_d"] = ret.rolling(60, min_periods=30).std(ddof=1).clip(lower=1e-4)
    f["vol20"], f["vol60"] = ind.hist_vol(c, 20), ind.hist_vol(c, 60)
    f["z20"] = ind.zscore(c, 20)
    f["cmf20"] = ind.cmf(h, l, c, v, 20)
    f["mfi14"] = ind.mfi(h, l, c, v, 14)
    obv = ind.obv(c, v)
    f["obv_z"] = ind.zscore(obv, 20)
    f["relvol"] = ind.rel_volume(v, 20)
    up = (c > c.shift(1)).astype(float)
    f["updown_vol"] = (v * up).rolling(10, min_periods=10).sum() / v.rolling(10, min_periods=10).sum().replace(0, np.nan)
    f["hi252"] = h.rolling(252, min_periods=120).max()
    f["lo252"] = l.rolling(252, min_periods=120).min()
    f["prox52"] = c / f["hi252"]
    f["rp252"] = ((c - f["lo252"]) / (f["hi252"] - f["lo252"]).replace(0, np.nan)).clip(0, 1)
    f["candle"] = candle_score(df)
    f["sw_hi"], f["sw_lo"] = swing_level_series(h, l)
    f["wk_trend"] = weekly_trend_state(df)
    if bench is not None and len(bench):
        rs = ind.rs_line(c, bench)
        f["rs_roc60"] = rs / rs.shift(60) - 1.0
        f["rs_roc120"] = rs / rs.shift(120) - 1.0
        f["rs_above50"] = (rs > ind.sma(rs, 50)).astype(float).where(ind.sma(rs, 50).notna())
    else:
        f["rs_roc60"] = f["rs_roc120"] = f["rs_above50"] = np.nan
    return f.replace([np.inf, -np.inf], np.nan)
