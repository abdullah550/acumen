"""Candlestick and range patterns. A pattern at bar t only uses bars <= t.

Candles are weak evidence on their own. ``candle_score`` keeps their weight modest and the calibration
step measures whether they help at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, rel_volume, rsi, sma

# Signed weights (positive = bullish). Reversal patterns are scaled by context in candle_score.
WEIGHTS = {
    "bullish_engulfing": 0.50,
    "bearish_engulfing": -0.50,
    "morning_star": 0.60,
    "evening_star": -0.60,
    "hammer": 0.35,
    "hanging_man": -0.35,
    "inverted_hammer": 0.30,
    "shooting_star": -0.35,
    "pin_bar_bull": 0.35,
    "pin_bar_bear": -0.35,
    "piercing_line": 0.30,
    "dark_cloud_cover": -0.30,
    "three_white_soldiers": 0.50,
    "three_black_crows": -0.50,
    "gap_up": 0.10,
    "gap_down": -0.10,
}
BULL_REVERSAL = {"bullish_engulfing", "morning_star", "hammer", "inverted_hammer", "pin_bar_bull", "piercing_line"}
BEAR_REVERSAL = {"bearish_engulfing", "evening_star", "hanging_man", "shooting_star", "pin_bar_bear", "dark_cloud_cover"}
NAMES = {k: k.replace("_", " ").capitalize() for k in list(WEIGHTS) + ["doji", "inside_bar", "outside_bar", "nr7", "volume_spike"]}


def detect_all(df: pd.DataFrame) -> pd.DataFrame:
    """Boolean DataFrame, one column per pattern, True at the bar where the pattern completes."""
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    a = atr(h, l, c, 14)
    body = (c - o).abs()
    rng = (h - l)
    up_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    lo_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    green, red = c > o, c < o
    prior_down = (c.shift(1) < sma(c, 10).shift(1)) & (c.shift(1) < c.shift(6))
    prior_up = (c.shift(1) > sma(c, 10).shift(1)) & (c.shift(1) > c.shift(6))
    tiny = 0.1 * rng
    p = pd.DataFrame(index=df.index)
    p["doji"] = (rng > 0) & (body <= tiny)
    hammer_shape = (rng > 0) & (lo_wick >= 2 * body) & (up_wick <= 0.15 * rng) & (body > 0)
    star_shape = (rng > 0) & (up_wick >= 2 * body) & (lo_wick <= 0.15 * rng) & (body > 0)
    p["hammer"] = hammer_shape & prior_down
    p["hanging_man"] = hammer_shape & prior_up
    p["inverted_hammer"] = star_shape & prior_down
    p["shooting_star"] = star_shape & prior_up
    pb, pc = body.shift(1), c.shift(1)
    po = o.shift(1)
    p["bullish_engulfing"] = red.shift(1, fill_value=False) & green & (o <= pc) & (c >= po) & (body > pb)
    p["bearish_engulfing"] = green.shift(1, fill_value=False) & red & (o >= pc) & (c <= po) & (body > pb)
    mid_prev = (po + pc) / 2.0
    p["piercing_line"] = red.shift(1, fill_value=False) & green & (o < pc) & (c > mid_prev) & (c < po)
    p["dark_cloud_cover"] = green.shift(1, fill_value=False) & red & (o > pc) & (c < mid_prev) & (c > po)
    big2 = body.shift(2) >= 0.6 * a.shift(2)
    small1 = body.shift(1) <= 0.35 * a.shift(1)
    p["morning_star"] = red.shift(2, fill_value=False) & big2 & small1 & green & (c > (o.shift(2) + c.shift(2)) / 2.0)
    p["evening_star"] = green.shift(2, fill_value=False) & big2 & small1 & red & (c < (o.shift(2) + c.shift(2)) / 2.0)
    g3 = green & green.shift(1, fill_value=False) & green.shift(2, fill_value=False)
    r3 = red & red.shift(1, fill_value=False) & red.shift(2, fill_value=False)
    p["three_white_soldiers"] = g3 & (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (o > o.shift(1)) & (o < c.shift(1))
    p["three_black_crows"] = r3 & (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (o < o.shift(1)) & (o > c.shift(1))
    p["pin_bar_bull"] = (rng > 0) & (lo_wick >= 0.6 * rng) & (c >= l + 0.66 * rng)
    p["pin_bar_bear"] = (rng > 0) & (up_wick >= 0.6 * rng) & (c <= l + 0.34 * rng)
    thr = 0.5 * a
    p["gap_up"] = o > h.shift(1) + thr
    p["gap_down"] = o < l.shift(1) - thr
    p["inside_bar"] = (h <= h.shift(1)) & (l >= l.shift(1))
    p["outside_bar"] = (h > h.shift(1)) & (l < l.shift(1))
    p["nr7"] = rng <= rng.rolling(7, min_periods=7).min()
    p["volume_spike"] = rel_volume(v, 20) > 2.5
    return p.fillna(False).astype(bool)


def candle_score(df: pd.DataFrame, decay: float = 0.7, lookback: int = 5, patterns: pd.DataFrame | None = None) -> pd.Series:
    """Signed evidence in [-1, 1] from recent candle patterns with exponential decay."""
    pat = detect_all(df) if patterns is None else patterns
    r = rsi(df["close"], 14)
    contrib = pd.DataFrame(0.0, index=df.index, columns=list(WEIGHTS))
    for name, w in WEIGHTS.items():
        hit = pat[name].astype(float)
        if name in BULL_REVERSAL:
            hit = hit * np.where(r < 40, 1.0, 0.5)
        elif name in BEAR_REVERSAL:
            hit = hit * np.where(r > 60, 1.0, 0.5)
        contrib[name] = hit * w
    raw = contrib.sum(axis=1)
    out = pd.Series(0.0, index=df.index)
    for k in range(lookback):
        out = out + (decay**k) * raw.shift(k).fillna(0.0)
    return np.tanh(out)


def recent_patterns(df: pd.DataFrame, n_days: int = 10) -> list[dict]:
    """Patterns completed in the last ``n_days`` bars, newest first, for display."""
    pat = detect_all(df).iloc[-n_days:]
    out = []
    for ts, row in pat[::-1].iterrows():
        for name in row.index[row.to_numpy()]:
            if name in ("inside_bar", "outside_bar", "nr7", "volume_spike", "doji") and name not in ("doji",):
                direction = None
            w = WEIGHTS.get(name)
            direction = "long" if (w or 0) > 0 else "short" if (w or 0) < 0 else None
            out.append({"date": ts.strftime("%Y-%m-%d"), "name": NAMES.get(name, name), "dir": direction, "key": name})
    return out
