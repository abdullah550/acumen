"""Technical indicators.

Pure pandas/numpy. Every function is causal: the value at bar ``t`` only uses bars ``<= t``, so appending
future bars never changes earlier values (tests/test_ind_causality.py enforces this).

Undefined values are NaN, never inf. Inputs are never mutated. Smoothing follows Wilder (RMA) for RSI,
ATR and ADX; RMA/EMA recursions start at the first observation, so values converge to the textbook
(SMA-seeded) versions after roughly 5 periods.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def _clean(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan)


# ----------------------------------------------------------------------------- moving averages
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (alpha = 1/n)."""
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    half = max(n // 2, 1)
    root = max(int(round(np.sqrt(n))), 1)
    return wma(2 * wma(s, half) - wma(s, n), root)


def kama(s: pd.Series, n: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    """Kaufman adaptive moving average."""
    x = s.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return pd.Series(out, index=s.index)
    change = np.abs(x[n:] - x[:-n])
    vol = pd.Series(np.abs(np.diff(x, prepend=x[0]))).rolling(n).sum().to_numpy()[n:]
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.where(vol > 0, change / vol, 0.0)
    fsc, ssc = 2.0 / (fast + 1), 2.0 / (slow + 1)
    sc = (er * (fsc - ssc) + ssc) ** 2
    prev = x[n - 1]
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = prev + sc[i - n] * (x[i] - prev)
        out[i] = prev
    return pd.Series(out, index=s.index)


def vwma(close: pd.Series, volume: pd.Series, n: int) -> pd.Series:
    return _clean((close * volume).rolling(n).sum() / volume.rolling(n).sum().replace(0, np.nan))


# ----------------------------------------------------------------------------- momentum
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru, rd = rma(up, n), rma(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + ru / rd)
    out = out.where(rd != 0, 100.0)
    out = out.where(~((ru == 0) & (rd == 0)), 50.0)
    return out.where(ru.notna() & rd.notna())


def stoch(high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3, smooth: int = 3):
    ll, hh = low.rolling(k, min_periods=k).min(), high.rolling(k, min_periods=k).max()
    raw = _clean(100.0 * (close - ll) / (hh - ll).replace(0, np.nan))
    kk = raw.rolling(smooth, min_periods=smooth).mean() if smooth > 1 else raw
    return kk, kk.rolling(d, min_periods=d).mean()


def stoch_rsi(close: pd.Series, n: int = 14, k: int = 14, smooth: int = 3) -> pd.Series:
    r = rsi(close, n)
    ll, hh = r.rolling(k, min_periods=k).min(), r.rolling(k, min_periods=k).max()
    raw = _clean(100.0 * (r - ll) / (hh - ll).replace(0, np.nan))
    return raw.rolling(smooth, min_periods=smooth).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return line, sig, line - sig


def roc(close: pd.Series, n: int) -> pd.Series:
    """Percent change over n bars."""
    return _clean(close.pct_change(n) * 100.0)


def cci(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: float(np.abs(x - x.mean()).mean()), raw=True)
    return _clean((tp - ma) / (0.015 * md.replace(0, np.nan)))


def willr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    hh, ll = high.rolling(n, min_periods=n).max(), low.rolling(n, min_periods=n).min()
    return _clean(-100.0 * (hh - close) / (hh - ll).replace(0, np.nan))


def mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int = 14) -> pd.Series:
    tp = (high + low + close) / 3.0
    flow = tp * volume
    pos = flow.where(tp.diff() > 0, 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(tp.diff() < 0, 0.0).rolling(n, min_periods=n).sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + pos / neg)
    out = out.where(neg != 0, 100.0)
    out = out.where(~((pos == 0) & (neg == 0)), 50.0)
    return out.where(pos.notna())


def aroon(high: pd.Series, low: pd.Series, n: int = 25):
    up = high.rolling(n + 1, min_periods=n + 1).apply(lambda x: 100.0 * float(np.argmax(x)) / n, raw=True)
    dn = low.rolling(n + 1, min_periods=n + 1).apply(lambda x: 100.0 * float(np.argmin(x)) / n, raw=True)
    return up, dn, up - dn


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    pc = close.shift(1)
    return pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return rma(true_range(high, low, close), n)


def natr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    return _clean(atr(high, low, close, n) / close * 100.0)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14):
    up, dn = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=close.index).where(up.notna())
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=close.index).where(dn.notna())
    a = atr(high, low, close, n)
    plus_di = _clean(100.0 * rma(plus_dm, n) / a.replace(0, np.nan))
    minus_di = _clean(100.0 * rma(minus_dm, n) / a.replace(0, np.nan))
    dx = _clean(100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return rma(dx, n), plus_di, minus_di


# ----------------------------------------------------------------------------- volatility
def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame(
        {
            "mid": mid,
            "upper": upper,
            "lower": lower,
            "pct_b": _clean((close - lower) / (upper - lower).replace(0, np.nan)),
            "bandwidth": _clean((upper - lower) / mid.replace(0, np.nan) * 100.0),
        }
    )


def keltner(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20, mult: float = 1.5, atr_n: int = 10) -> pd.DataFrame:
    mid = ema(close, n)
    a = atr(high, low, close, atr_n)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a})


def donchian(high: pd.Series, low: pd.Series, n: int = 20):
    up, lo = high.rolling(n, min_periods=n).max(), low.rolling(n, min_periods=n).min()
    return up, lo, (up + lo) / 2.0


def hist_vol(close: pd.Series, n: int = 20, annualise: int = 252) -> pd.Series:
    """Close-to-close volatility in percent, annualised."""
    r = np.log(close).diff()
    return _clean(r.rolling(n, min_periods=n).std(ddof=1) * np.sqrt(annualise) * 100.0)


def parkinson_vol(high: pd.Series, low: pd.Series, n: int = 20, annualise: int = 252) -> pd.Series:
    x = (np.log(high / low) ** 2) / (4.0 * np.log(2.0))
    return _clean(np.sqrt(x.rolling(n, min_periods=n).mean() * annualise) * 100.0)


def squeeze(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 20) -> pd.Series:
    """True when the Bollinger bands sit inside the Keltner channel (volatility compression)."""
    bb, kc = bollinger(close, n, 2.0), keltner(high, low, close, n, 1.5, n)
    return ((bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])).where(bb["upper"].notna() & kc["upper"].notna(), False)


# ----------------------------------------------------------------------------- trend
def supertrend(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 10, mult: float = 3.0):
    """Returns (line, direction) with direction +1 (line is the rising lower band) or -1."""
    a = atr(high, low, close, n).to_numpy()
    hl2 = ((high + low) / 2.0).to_numpy()
    c = close.to_numpy()
    m = len(c)
    line = np.full(m, np.nan)
    direction = np.full(m, np.nan)
    valid = np.flatnonzero(~np.isnan(a))
    if len(valid) == 0:
        return pd.Series(line, index=close.index), pd.Series(direction, index=close.index)
    s = valid[0]
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    d = 1 if c[s] >= hl2[s] else -1
    direction[s] = d
    line[s] = flb[s] if d == 1 else fub[s]
    for i in range(s + 1, m):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if d == 1 and c[i] < flb[i]:
            d = -1
        elif d == -1 and c[i] > fub[i]:
            d = 1
        direction[i] = d
        line[i] = flb[i] if d == 1 else fub[i]
    return pd.Series(line, index=close.index), pd.Series(direction, index=close.index)


def psar(high: pd.Series, low: pd.Series, af: float = 0.02, max_af: float = 0.2) -> pd.Series:
    h, l = high.to_numpy(), low.to_numpy()
    n = len(h)
    out = np.full(n, np.nan)
    if n < 3:
        return pd.Series(out, index=high.index)
    up = h[1] >= h[0]
    sar = l[0] if up else h[0]
    ep = h[0] if up else l[0]
    a = af
    out[0] = sar
    for i in range(1, n):
        sar = sar + a * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < sar:
                up, sar, ep, a = False, ep, l[i], af
            elif h[i] > ep:
                ep, a = h[i], min(a + af, max_af)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, a = True, ep, h[i], af
            elif l[i] < ep:
                ep, a = l[i], min(a + af, max_af)
        out[i] = sar
    return pd.Series(out, index=high.index)


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series, conv: int = 9, base: int = 26, span_b: int = 52) -> pd.DataFrame:
    """Cloud values are those that plot AT each bar (computed from bars <= t - base), so they are causal."""
    tenkan = (high.rolling(conv).max() + low.rolling(conv).min()) / 2.0
    kijun = (high.rolling(base).max() + low.rolling(base).min()) / 2.0
    a = ((tenkan + kijun) / 2.0).shift(base)
    b = ((high.rolling(span_b).max() + low.rolling(span_b).min()) / 2.0).shift(base)
    return pd.DataFrame(
        {"tenkan": tenkan, "kijun": kijun, "cloud_a": a, "cloud_b": b, "chikou_bull": close > close.shift(base)}
    )


def linreg(close: pd.Series, n: int = 50) -> pd.DataFrame:
    """Rolling least squares of price on time. slope_pct = slope per bar as percent of price."""
    y = close.to_numpy(dtype=float)
    out_slope = np.full(len(y), np.nan)
    out_r2 = np.full(len(y), np.nan)
    out_int = np.full(len(y), np.nan)
    if len(y) >= n:
        w = sliding_window_view(y, n)
        x = np.arange(n, dtype=float)
        xc = x - x.mean()
        yc = w - w.mean(axis=1, keepdims=True)
        sxx = float((xc**2).sum())
        slope = (yc @ xc) / sxx
        syy = (yc**2).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            r2 = np.where(syy > 0, (slope**2 * sxx) / syy, 0.0)
        out_slope[n - 1 :] = slope / w[:, -1] * 100.0
        out_r2[n - 1 :] = r2
        out_int[n - 1 :] = w.mean(axis=1) + slope * (n - 1 - x.mean())
    idx = close.index
    return pd.DataFrame({"slope_pct": out_slope, "r2": out_r2, "endpoint": out_int}, index=idx)


def efficiency_ratio(close: pd.Series, n: int = 10) -> pd.Series:
    change = close.diff(n).abs()
    path = close.diff().abs().rolling(n, min_periods=n).sum()
    return _clean(change / path.replace(0, np.nan))


def hurst(close: pd.Series, window: int = 100, max_lag: int = 16) -> pd.Series:
    """Rolling Hurst exponent from the scaling of k-lag log-price dispersion: std(x_t - x_{t-k}) ~ k^H.

    About 0.5 for a random walk, above 0.5 for persistent (trending) series, below for mean reverting.
    """
    lp = np.log(close)
    lags = np.arange(2, max_lag + 1)
    cols = [lp.diff(int(k)).rolling(window, min_periods=window).std(ddof=0) for k in lags]
    m = np.column_stack([c.to_numpy() for c in cols])
    with np.errstate(divide="ignore", invalid="ignore"):
        ly = np.log(m)
    lx = np.log(lags.astype(float))
    lxc = lx - lx.mean()
    ok = np.isfinite(ly).all(axis=1)
    h = np.full(len(close), np.nan)
    if ok.any():
        yc = ly[ok] - ly[ok].mean(axis=1, keepdims=True)
        h[ok] = (yc @ lxc) / float((lxc**2).sum())
    return pd.Series(h, index=close.index)


def zscore(s: pd.Series, n: int = 20) -> pd.Series:
    mu = s.rolling(n, min_periods=n).mean()
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return _clean((s - mu) / sd.replace(0, np.nan))


# ----------------------------------------------------------------------------- volume
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return (np.sign(close.diff()).fillna(0.0) * volume).cumsum()


def ad_line(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    rng = (high - low).replace(0, np.nan)
    clv = ((close - low) - (high - close)) / rng
    return (clv.fillna(0.0) * volume).cumsum()


def cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    rng = (high - low).replace(0, np.nan)
    clv = (((close - low) - (high - close)) / rng).fillna(0.0)
    return _clean((clv * volume).rolling(n, min_periods=n).sum() / volume.rolling(n, min_periods=n).sum().replace(0, np.nan))


def vwap_rolling(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    tp = (high + low + close) / 3.0
    return _clean((tp * volume).rolling(n, min_periods=n).sum() / volume.rolling(n, min_periods=n).sum().replace(0, np.nan))


def rel_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Today's volume over the average of the previous n bars (excludes today)."""
    return _clean(volume / volume.rolling(n, min_periods=n).mean().shift(1).replace(0, np.nan))


def force_index(close: pd.Series, volume: pd.Series, n: int = 13) -> pd.Series:
    return (close.diff() * volume).ewm(span=n, adjust=False, min_periods=n).mean()


# ----------------------------------------------------------------------------- helpers
def percentile_rank(s: pd.Series, window: int = 252) -> pd.Series:
    """Percentile (0-100) of the current value inside its own trailing window (inclusive of today)."""
    x = s.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) >= window:
        w = sliding_window_view(x, window)
        cur = w[:, -1:]
        with np.errstate(invalid="ignore"):
            out[window - 1 :] = np.where(np.isnan(cur[:, 0]), np.nan, ((w < cur).sum(axis=1) + 0.5 * (w == cur).sum(axis=1)) / window * 100.0)
    return pd.Series(out, index=s.index)


def rs_line(close: pd.Series, bench: pd.Series) -> pd.Series:
    """Relative strength line close / bench aligned on the close index (bench forward filled)."""
    b = bench.reindex(close.index).ffill()
    return _clean(close / b.replace(0, np.nan))


def pivots_classic(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    """Classic floor pivots computed from the PREVIOUS bar, so they are known at the open of each bar."""
    h, l, c = high.shift(1), low.shift(1), close.shift(1)
    p = (h + l + c) / 3.0
    return pd.DataFrame({"p": p, "r1": 2 * p - l, "s1": 2 * p - h, "r2": p + (h - l), "s2": p - (h - l)})


def fib_levels(low: float, high: float, up: bool = True) -> dict[str, float]:
    """Retracement and extension levels for a swing. ``up=True``: swing from low to high (retrace down)."""
    rng = high - low
    base = high if up else low
    sign = -1.0 if up else 1.0
    out = {f"ret_{int(r * 1000)}": base + sign * rng * r for r in (0.236, 0.382, 0.5, 0.618, 0.786)}
    ext_base = low if up else high
    ext_sign = 1.0 if up else -1.0
    out["ext_1272"] = ext_base + ext_sign * rng * 1.272
    out["ext_1618"] = ext_base + ext_sign * rng * 1.618
    return out
