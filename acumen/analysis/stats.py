"""Return and risk statistics. Percent figures are returned in percent units."""

from __future__ import annotations

import numpy as np
import pandas as pd


def simple_returns(close: pd.Series) -> pd.Series:
    return close.pct_change().replace([np.inf, -np.inf], np.nan)


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close).diff().replace([np.inf, -np.inf], np.nan)


def drawdown_series(close: pd.Series) -> pd.Series:
    """Drawdown from the running peak in percent (0 or negative)."""
    peak = close.cummax()
    return (close / peak - 1.0) * 100.0


def max_drawdown(close: pd.Series):
    """Returns (max_drawdown_pct (negative), peak_date, trough_date)."""
    if len(close) == 0:
        return float("nan"), None, None
    dd = drawdown_series(close)
    trough = dd.idxmin()
    peak = close.loc[:trough].idxmax()
    return float(dd.min()), peak, trough


def annualised_vol(returns: pd.Series, periods: int = 252) -> float:
    r = returns.dropna()
    return float(r.std(ddof=1) * np.sqrt(periods) * 100.0) if len(r) > 2 else float("nan")


def downside_deviation(returns: pd.Series, periods: int = 252, mar: float = 0.0) -> float:
    r = returns.dropna()
    if len(r) < 3:
        return float("nan")
    d = np.minimum(r - mar, 0.0)
    return float(np.sqrt((d**2).mean()) * np.sqrt(periods) * 100.0)


def sharpe(returns: pd.Series, rf: float = 0.0, periods: int = 252) -> float:
    r = returns.dropna() - rf / periods
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(periods)) if len(r) > 2 and sd > 0 else float("nan")


def sortino(returns: pd.Series, rf: float = 0.0, periods: int = 252) -> float:
    r = returns.dropna() - rf / periods
    dd = np.sqrt((np.minimum(r, 0.0) ** 2).mean())
    return float(r.mean() / dd * np.sqrt(periods)) if len(r) > 2 and dd > 0 else float("nan")


def cagr(close: pd.Series, periods: int = 252) -> float:
    if len(close) < 2 or close.iloc[0] <= 0:
        return float("nan")
    years = (len(close) - 1) / periods
    return float(((close.iloc[-1] / close.iloc[0]) ** (1.0 / years) - 1.0) * 100.0) if years > 0 else float("nan")


def calmar(close: pd.Series, periods: int = 252) -> float:
    mdd, _, _ = max_drawdown(close)
    c = cagr(close, periods)
    return float(c / abs(mdd)) if mdd and mdd < 0 and np.isfinite(c) else float("nan")


def var_hist(returns: pd.Series, conf: float = 0.95) -> float:
    """Historical value at risk in percent (negative number)."""
    r = returns.dropna()
    return float(np.percentile(r, (1.0 - conf) * 100.0) * 100.0) if len(r) >= 30 else float("nan")


def cvar_hist(returns: pd.Series, conf: float = 0.95) -> float:
    r = returns.dropna()
    if len(r) < 30:
        return float("nan")
    cut = np.percentile(r, (1.0 - conf) * 100.0)
    tail = r[r <= cut]
    return float(tail.mean() * 100.0) if len(tail) else float("nan")


def beta(y: pd.Series, x: pd.Series, min_obs: int = 60) -> float:
    df = pd.concat([y, x], axis=1, join="inner").dropna()
    if len(df) < min_obs:
        return float("nan")
    var = df.iloc[:, 1].var(ddof=1)
    return float(df.iloc[:, 0].cov(df.iloc[:, 1]) / var) if var > 0 else float("nan")


def rolling_beta(y: pd.Series, x: pd.Series, n: int = 120) -> pd.Series:
    df = pd.concat([y, x], axis=1, join="inner")
    cov = df.iloc[:, 0].rolling(n, min_periods=n).cov(df.iloc[:, 1])
    var = df.iloc[:, 1].rolling(n, min_periods=n).var()
    return (cov / var.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def rolling_corr(y: pd.Series, x: pd.Series, n: int = 60) -> pd.Series:
    df = pd.concat([y, x], axis=1, join="inner")
    return df.iloc[:, 0].rolling(n, min_periods=n).corr(df.iloc[:, 1])


def correlation_matrix(returns: pd.DataFrame, min_periods: int = 60) -> pd.DataFrame:
    return returns.corr(min_periods=min_periods)


def gap_frequency(open_: pd.Series, close: pd.Series, threshold_pct: float = 2.0) -> float:
    """Share of sessions (percent) that opened more than threshold_pct away from the previous close."""
    gap = (open_ / close.shift(1) - 1.0).abs() * 100.0
    g = gap.dropna()
    return float((g > threshold_pct).mean() * 100.0) if len(g) else float("nan")
