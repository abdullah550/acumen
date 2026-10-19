"""Deterministic synthetic OHLCV for tests (business-day index)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(n: int = 600, seed: int = 0, drift: float = 0.0004, vol: float = 0.015, start: str = "2022-01-03", base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = base * np.exp(np.cumsum(rets))
    prev = np.concatenate([[base], close[:-1]])
    gap = rng.normal(0, vol * 0.3, n)
    open_ = prev * (1 + gap)
    span = np.abs(rng.normal(0, vol * 0.8, n)) + 0.002
    high = np.maximum(open_, close) * (1 + span * rng.uniform(0.2, 1.0, n))
    low = np.minimum(open_, close) * (1 - span * rng.uniform(0.2, 1.0, n))
    volume = np.exp(rng.normal(12.0, 0.5, n)) * (1 + 8 * np.abs(rets) / max(vol, 1e-9))
    idx = pd.bdate_range(start, periods=n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    df.index.name = "date"
    return df


def ohlc_from_returns(r: np.ndarray, seed: int = 0, vol: float = 0.015, start: str = "2019-01-02", base: float = 100.0) -> pd.DataFrame:
    """OHLCV bars around a given log-return path (gaps, intrabar ranges and volume derived from it)."""
    rng = np.random.default_rng(seed + 7)
    n = len(r)
    close = base * np.exp(np.cumsum(r))
    prev = np.concatenate([[base], close[:-1]])
    gap = rng.normal(0, vol * 0.3, n)
    open_ = prev * (1 + gap)
    span = np.abs(rng.normal(0, vol * 0.8, n)) + 0.002
    high = np.maximum(open_, close) * (1 + span * rng.uniform(0.2, 1, n))
    low = np.minimum(open_, close) * (1 - span * rng.uniform(0.2, 1, n))
    volume = np.exp(rng.normal(12, 0.5, n)) * (1 + 8 * np.abs(r) / vol)
    idx = pd.bdate_range(start, periods=n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    df.index.name = "date"
    return df


def make_process(n: int, seed: int, kind: str = "rw", vol: float = 0.015, **kw) -> pd.DataFrame:
    """``rw`` random walk (no edge for any rule), ``drift`` slowly varying drift (trend following works),
    ``revert`` fast mean reversion (trend following loses)."""
    rng = np.random.default_rng(seed)
    e = rng.normal(0, vol, n)
    if kind == "rw":
        r = e
    elif kind == "drift":
        phi, sd = 0.98, kw.get("drift_sd", 0.0020)
        mu = np.zeros(n)
        z = rng.normal(0, sd * np.sqrt(1 - phi**2), n)
        for i in range(1, n):
            mu[i] = phi * mu[i - 1] + z[i]
        r = mu + e
    elif kind == "revert":
        x = np.zeros(n)
        z = rng.normal(0, vol * 0.6, n)
        for i in range(1, n):
            x[i] = 0.8 * x[i - 1] + z[i]
        r = e * 0.6 + np.diff(np.concatenate([[0], x]))
    else:
        raise ValueError(kind)
    return ohlc_from_returns(r, seed, vol)
