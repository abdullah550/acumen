"""Market structure: swing points, support and resistance, volume profile, breakouts.

Swing pivots need ``right`` bars after the pivot to be confirmed. They are therefore exposed at their
CONFIRMATION bar (pivot position + right). ``swing_level_series`` gives, for every bar, the price of the
most recent swing that was already confirmed at that bar, which is what stops and targets may use.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .indicators import atr


@dataclass
class Level:
    px: float
    kind: str  # support | resistance
    touches: int
    strength: float  # 0..1
    last_touch: str | None
    source: str  # swing | poc | value_area

    def to_dict(self) -> dict:
        return {
            "px": float(self.px),
            "kind": self.kind,
            "strength": round(float(self.strength), 2),
            "touches": int(self.touches),
            "label": f"{'Support' if self.kind == 'support' else 'Resistance'} {self.px:.4g}",
        }


def swing_points(high: pd.Series, low: pd.Series, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Confirmed swing highs and lows.

    A swing high at i needs high[i] > max(high[i-left:i]) and high[i] >= max(high[i+1:i+right+1]).
    Swing lows mirror that. Columns: pivot_pos, confirm_pos, kind, price.
    """
    h, l = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
    n = len(h)
    rows = []
    if n >= left + right + 1:
        span = left + right + 1
        wh, wl = sliding_window_view(h, span), sliding_window_view(l, span)
        for j in range(len(wh)):
            i = j + left
            hi_ok = h[i] > wh[j, :left].max() and h[i] >= wh[j, left + 1 :].max()
            lo_ok = l[i] < wl[j, :left].min() and l[i] <= wl[j, left + 1 :].min()
            if hi_ok:
                rows.append((i, i + right, "high", h[i]))
            if lo_ok:
                rows.append((i, i + right, "low", l[i]))
    df = pd.DataFrame(rows, columns=["pivot_pos", "confirm_pos", "kind", "price"])
    return df.sort_values(["confirm_pos", "kind"]).reset_index(drop=True)


def swing_level_series(high: pd.Series, low: pd.Series, left: int = 3, right: int = 3):
    """(last_swing_high, last_swing_low): price of the latest swing confirmed at or before each bar."""
    n = len(high)
    sw = swing_points(high, low, left, right)
    hi = np.full(n, np.nan)
    lo = np.full(n, np.nan)
    for _, r in sw.iterrows():
        c = int(r["confirm_pos"])
        if c < n:
            (hi if r["kind"] == "high" else lo)[c] = r["price"]
    return pd.Series(hi, index=high.index).ffill(), pd.Series(lo, index=high.index).ffill()


def support_resistance(
    df: pd.DataFrame,
    t: int | None = None,
    lookback: int = 250,
    tol_atr: float = 0.6,
    min_touches: int = 2,
    left: int = 3,
    right: int = 3,
    half_life: int = 90,
) -> list[Level]:
    """Cluster swings confirmed at or before bar ``t`` into support/resistance levels."""
    n = len(df)
    if n < 30:
        return []
    t = n - 1 if t is None else int(t)
    sub = df.iloc[: t + 1]
    sw = swing_points(sub["high"], sub["low"], left, right)
    sw = sw[(sw["confirm_pos"] <= t) & (sw["pivot_pos"] >= t - lookback)]
    if sw.empty:
        return []
    a = atr(sub["high"], sub["low"], sub["close"], 14).iloc[-1]
    close = float(sub["close"].iloc[-1])
    tol = float(a * tol_atr) if np.isfinite(a) and a > 0 else close * 0.01
    pts = sw.sort_values("price")
    clusters: list[list[tuple[float, int]]] = []
    for _, r in pts.iterrows():
        p, pos = float(r["price"]), int(r["pivot_pos"])
        if clusters and abs(p - np.mean([x[0] for x in clusters[-1]])) <= tol:
            clusters[-1].append((p, pos))
        else:
            clusters.append([(p, pos)])
    vol = sub["volume"].to_numpy(dtype=float)
    vmean = float(np.mean(vol[-lookback:])) if vol[-lookback:].size and np.mean(vol[-lookback:]) > 0 else 0.0
    levels: list[Level] = []
    for cl in clusters:
        if len(cl) < min_touches:
            continue
        w = np.array([0.5 ** ((t - pos) / half_life) for _, pos in cl])
        px = float(np.average([p for p, _ in cl], weights=w))
        vf = 1.0
        if vmean > 0:
            vf = 1.0 + 0.25 * min(2.0, float(np.mean([vol[pos] for _, pos in cl])) / vmean - 1.0 if vmean else 0.0)
        strength = float(min(1.0, (len(cl) / 5.0) * (0.5 + 0.5 * w.max()) * max(0.6, vf)))
        last = max(pos for _, pos in cl)
        levels.append(
            Level(px, "support" if px < close else "resistance", len(cl), strength, sub.index[last].strftime("%Y-%m-%d"), "swing")
        )
    vp = volume_profile(df, t)
    if vp:
        for key, label in (("poc", "poc"), ("vah", "value_area"), ("val", "value_area")):
            px = vp[key]
            if all(abs(px - lv.px) > tol for lv in levels):
                levels.append(Level(px, "support" if px < close else "resistance", 1, 0.35 if key == "poc" else 0.25, None, label))
    return sorted(levels, key=lambda lv: lv.px)


def nearest_levels(levels: list[Level], price: float, n: int = 2):
    below = sorted([lv for lv in levels if lv.px < price], key=lambda lv: price - lv.px)[:n]
    above = sorted([lv for lv in levels if lv.px > price], key=lambda lv: lv.px - price)[:n]
    return below, above


def volume_profile(df: pd.DataFrame, t: int | None = None, lookback: int = 120, bins: int = 24) -> dict | None:
    """Point of control and 70% value area from bars up to ``t`` (typical price weighted by volume)."""
    n = len(df)
    t = n - 1 if t is None else int(t)
    sub = df.iloc[max(0, t - lookback + 1) : t + 1]
    if len(sub) < 20 or float(sub["volume"].sum()) <= 0:
        return None
    tp = ((sub["high"] + sub["low"] + sub["close"]) / 3.0).to_numpy()
    v = sub["volume"].to_numpy(dtype=float)
    lo, hi = float(sub["low"].min()), float(sub["high"].max())
    if hi <= lo:
        return None
    hist, edges = np.histogram(tp, bins=bins, range=(lo, hi), weights=v)
    centers = (edges[:-1] + edges[1:]) / 2.0
    k = int(np.argmax(hist))
    total, acc, a, b = hist.sum(), hist[k], k, k
    while acc < 0.7 * total and (a > 0 or b < len(hist) - 1):
        left_v = hist[a - 1] if a > 0 else -1.0
        right_v = hist[b + 1] if b < len(hist) - 1 else -1.0
        if left_v >= right_v:
            a -= 1
            acc += hist[a]
        else:
            b += 1
            acc += hist[b]
    return {"poc": float(centers[k]), "val": float(edges[a]), "vah": float(edges[b + 1])}


def breakout_state(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Close beyond the PREVIOUS n-bar high/low. Columns: up, down (bool), and the reference levels."""
    prev_hi = df["high"].rolling(n, min_periods=n).max().shift(1)
    prev_lo = df["low"].rolling(n, min_periods=n).min().shift(1)
    return pd.DataFrame(
        {"up": df["close"] > prev_hi, "down": df["close"] < prev_lo, "ref_high": prev_hi, "ref_low": prev_lo},
        index=df.index,
    )
