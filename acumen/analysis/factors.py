"""Factor scores in [-1, +1] built from the causal feature frame, one set per horizon.

Every factor is signed: positive favours a long position, negative a short. Factors are deliberately
simple, bounded transforms of features (no fitted parameters), so nothing here can be over-fitted to a
particular instrument. Whether the blend has any predictive value is measured by the calibration step.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CONV_SPAN = 0.80
FACTOR_NAMES = ["trend", "momentum", "reversion", "structure", "volume", "pattern", "rs"]
HORIZONS = ("short", "medium", "long")


def _t(x: pd.Series, scale: float = 1.0) -> pd.Series:
    return np.tanh(x / scale)


def _avg(*cols: pd.Series, min_count: int = 1) -> pd.Series:
    m = pd.concat(cols, axis=1)
    return m.mean(axis=1, skipna=True).where(m.notna().sum(axis=1) >= min_count)


def _zret(f: pd.DataFrame, n: int) -> pd.Series:
    """Return over n bars in units of its own volatility."""
    return f[f"roc{n}"] / (f["sigma_d"] * 100.0 * np.sqrt(n))


def _cloud_pos(f: pd.DataFrame) -> pd.Series:
    pos = pd.Series(0.0, index=f.index)
    pos[f["close"] > f["cloud_top"]] = 1.0
    pos[f["close"] < f["cloud_bot"]] = -1.0
    return pos.where(f["cloud_top"].notna())


def _di_score(f: pd.DataFrame) -> pd.Series:
    tot = (f["pdi"] + f["mdi"]).replace(0, np.nan)
    return ((f["pdi"] - f["mdi"]) / tot) * (f["adx"] / 25.0).clip(0, 1)


def trend_factor(f: pd.DataFrame, horizon: str) -> pd.Series:
    atr = f["atr"]
    if horizon == "short":
        return _avg(
            _t((f["close"] - f["ema20"]) / atr, 1.5),
            _t((f["ema20"] - f["sma50"]) / atr, 1.5),
            f["st_dir"],
            _di_score(f),
            min_count=3,
        ).clip(-1, 1)
    if horizon == "medium":
        slope50 = (f["sma50"] - f["sma50"].shift(10)) / atr
        return _avg(
            _t((f["close"] - f["sma50"]) / atr, 2.0),
            _t((f["sma50"] - f["sma200"]) / atr, 3.0),
            _t(slope50, 1.5),
            _cloud_pos(f),
            f["wk_trend"],
            np.sign(f["lr_slope"]) * f["lr_r2"],
            min_count=4,
        ).clip(-1, 1)
    slope200 = (f["sma200"] - f["sma200"].shift(40)) / atr
    return _avg(
        _t((f["close"] - f["sma200"]) / atr, 4.0),
        _t(slope200, 3.0),
        f["wk_trend"],
        (2.0 * f["rp252"] - 1.0),
        min_count=3,
    ).clip(-1, 1)


def momentum_factor(f: pd.DataFrame, horizon: str) -> pd.Series:
    rsi_mom = ((f["rsi14"] - 50.0) / 20.0).clip(-1, 1)
    macd = _t(f["macd_hist"] / f["atr"], 0.4)
    if horizon == "short":
        return _avg(_t(_zret(f, 10), 1.5), macd, rsi_mom, min_count=2).clip(-1, 1)
    if horizon == "medium":
        return _avg(_t(_zret(f, 20), 1.5), _t(_zret(f, 60), 1.5), rsi_mom, min_count=2).clip(-1, 1)
    z121 = f["mom_12_1"] / (f["sigma_d"] * 100.0 * np.sqrt(231.0))
    return _avg(_t(_zret(f, 120), 1.5), _t(z121, 1.5), min_count=1).clip(-1, 1)


def reversion_factor(f: pd.DataFrame, trend_medium: pd.Series) -> pd.Series:
    """Buy-the-dip / sell-the-rally pressure, switched off when the larger trend argues against it."""
    def cube(x: pd.Series) -> pd.Series:
        return np.sign(x) * np.abs(x) ** 1.5

    r_rsi2 = cube(((50.0 - f["rsi2"]) / 50.0).clip(-1, 1))
    r_bb = cube(((0.5 - f["bb_pctb"]) * 2.0).clip(-1, 1))
    r_z = (-f["z20"] / 2.5).clip(-1, 1)
    r_st = ((50.0 - f["stoch_k"]) / 50.0).clip(-1, 1)
    raw = _avg(r_rsi2, r_bb, r_z, r_st, min_count=3)
    gate = pd.Series(np.where(raw > 0, trend_medium > -0.25, trend_medium < 0.25), index=f.index)
    return (raw * gate.astype(float)).clip(-1, 1).where(raw.notna())


def structure_factor(f: pd.DataFrame, horizon: str) -> pd.Series:
    prox = (2.0 * ((f["prox52"] - 0.75) / 0.25).clip(0, 1) - 1.0)
    if horizon == "short":
        b = (f["close"] > f["ref_hi20"]).astype(float) - (f["close"] < f["ref_lo20"]).astype(float)
        return _avg(2.0 * f["pos20"] - 1.0, b.where(f["ref_hi20"].notna()), prox, min_count=2).clip(-1, 1)
    if horizon == "medium":
        b = (f["close"] > f["ref_hi55"]).astype(float) - (f["close"] < f["ref_lo55"]).astype(float)
        return _avg(2.0 * f["pos55"] - 1.0, b.where(f["ref_hi55"].notna()), prox, min_count=2).clip(-1, 1)
    return _avg(2.0 * f["rp252"] - 1.0, prox, min_count=1).clip(-1, 1)


def volume_factor(f: pd.DataFrame) -> pd.Series:
    return _avg(
        (f["cmf20"] * 2.5).clip(-1, 1),
        ((f["mfi14"] - 50.0) / 30.0).clip(-1, 1),
        ((f["updown_vol"] - 0.5) * 4.0).clip(-1, 1),
        _t(f["obv_z"], 2.0),
        min_count=2,
    ).clip(-1, 1)


def rs_factor(f: pd.DataFrame, horizon: str) -> pd.Series:
    roc = f["rs_roc120"] if horizon == "long" else f["rs_roc60"]
    return (0.6 * _t(roc, 0.08) + 0.4 * (2.0 * f["rs_above50"] - 1.0)).clip(-1, 1)


def compute_factors(f: pd.DataFrame, horizon: str) -> pd.DataFrame:
    trend = trend_factor(f, horizon)
    trend_med = trend if horizon == "medium" else trend_factor(f, "medium")
    out = pd.DataFrame(index=f.index)
    out["trend"] = trend
    out["momentum"] = momentum_factor(f, horizon)
    out["reversion"] = reversion_factor(f, trend_med) if horizon == "short" else 0.0
    out["structure"] = structure_factor(f, horizon)
    out["volume"] = volume_factor(f)
    out["pattern"] = f["candle"] if horizon == "short" else np.nan
    out["rs"] = rs_factor(f, horizon)
    return out


def composite(fac: pd.DataFrame, weights: dict[str, float], regime_tilt: pd.Series | None = None, min_factors: int = 3) -> pd.Series:
    """Weighted mean of the available factors (weights renormalised per bar) plus a regime tilt."""
    w = pd.Series({k: float(v) for k, v in weights.items() if float(v) > 0 and k in fac.columns})
    if w.empty:
        return pd.Series(np.nan, index=fac.index)
    m = fac[list(w.index)]
    avail = m.notna()
    wsum = (avail * w).sum(axis=1)
    num = (m.fillna(0.0) * w).sum(axis=1)
    comp = (num / wsum.replace(0, np.nan)).where(avail.sum(axis=1) >= min_factors)
    if regime_tilt is not None:
        comp = comp + regime_tilt.reindex(comp.index).fillna(0.0).where(comp.notna())
    return comp.clip(-1, 1)


def conviction_from_score(score_abs: float) -> float:
    """Map |composite| to a 0-100 conviction score (a score, not a probability): 0 at 0.10, 100 at 0.90."""
    return float(100.0 * np.clip((score_abs - 0.10) / CONV_SPAN, 0.0, 1.0))


def grade_from_conviction(conv: float) -> str:
    return "A" if conv >= 75 else "B" if conv >= 60 else "C" if conv >= 45 else "D"


def classify_setup(f: pd.DataFrame, fac: pd.DataFrame, sign: int, horizon: str) -> pd.Series:
    """Name the setup for every bar assuming a position in direction ``sign`` (+1 long, -1 short)."""
    s = float(sign)
    up20 = (f["close"] > f["ref_hi20"]) if sign > 0 else (f["close"] < f["ref_lo20"])
    up55 = (f["close"] > f["ref_hi55"]) if sign > 0 else (f["close"] < f["ref_lo55"])
    rv = f["relvol"].fillna(1.0)
    rsi_dir = 50.0 + s * (f["rsi14"] - 50.0)
    was_squeezed = f["squeeze"].astype(bool).shift(1, fill_value=False) | f["squeeze"].astype(bool).shift(2, fill_value=False)
    band_break = (f["close"] > f["bb_up"]) if sign > 0 else (f["close"] < f["bb_lo"])
    trend_med = trend_factor(f, "medium") * s
    near_ema = (f["close"] - f["ema20"]).abs() <= 1.0 * f["atr"]
    long_side = "buy" if sign > 0 else "sell"
    names = {
        "breakout": "Breakout" if sign > 0 else "Breakdown",
        "squeeze": "Squeeze release",
        "pullback": "Trend pullback" if sign > 0 else "Trend rally fade",
        "oversold": "Oversold bounce" if sign > 0 else "Overbought fade",
        "momentum": "Momentum continuation",
        "reversal": "Reversal candle at extreme",
        "following": "Trend following" if horizon != "long" else "Long-term trend",
    }
    conds = [
        (up55 | up20) & (rv >= 1.2),
        was_squeezed & band_break,
        (trend_med > 0.3) & (rsi_dir < 55) & (rsi_dir > 35) & near_ema,
        (fac["reversion"] * s > 0.4) if "reversion" in fac else pd.Series(False, index=f.index),
        (fac["momentum"] * s > 0.4) & (trend_med > 0.4),
        (f["candle"] * s > 0.3) & (rsi_dir < 40),
    ]
    choices = [names["breakout"], names["squeeze"], names["pullback"], names["oversold"], names["momentum"], names["reversal"]]
    out = np.select([c.fillna(False).to_numpy(dtype=bool) for c in conds], choices, default=names["following"])
    return pd.Series(out, index=f.index)


LIMIT_SETUPS = {"Trend pullback", "Trend rally fade", "Oversold bounce", "Overbought fade", "Reversal candle at extreme"}
