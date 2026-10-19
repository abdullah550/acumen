"""Higher time frame helpers. The classic trap is letting a daily bar see a weekly value that had not
completed yet; everything here only exposes a weekly value once its period has fully ended."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import sma


def resample_ohlcv(df: pd.DataFrame, rule: str = "W-FRI", closed_only: bool = True) -> pd.DataFrame:
    """Aggregate daily bars. With ``closed_only`` the trailing period is dropped unless it has ended."""
    if len(df) == 0:
        return df.copy()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df[["open", "high", "low", "close", "volume"]].resample(rule, label="right", closed="right").agg(agg).dropna(subset=["close"])
    if closed_only and len(out):
        last_daily = df.index[-1]
        period_end = out.index[-1]
        if period_end > last_daily:  # the period label lies in the future: still forming
            out = out.iloc[:-1]
    return out


def align_htf_to_daily(htf: pd.Series, daily_index: pd.DatetimeIndex) -> pd.Series:
    """For each daily date take the last HTF value whose period ended strictly before that date.

    ``htf`` is indexed by period end dates (as produced by ``resample_ohlcv``).
    """
    if len(htf) == 0:
        return pd.Series(np.nan, index=daily_index)
    shifted = htf.copy()
    shifted.index = shifted.index + pd.Timedelta(days=1)  # available from the day after the period ends
    return shifted.reindex(daily_index.union(shifted.index)).ffill().reindex(daily_index)


def weekly_trend_state(df: pd.DataFrame, fast: int = 10, slow: int = 30) -> pd.Series:
    """+1 / 0 / -1 weekly trend (close vs fast and slow weekly averages), causal on the daily index."""
    wk = resample_ohlcv(df, "W-FRI", closed_only=True)
    if len(wk) < slow + 2:
        return pd.Series(np.nan, index=df.index)
    f, s = sma(wk["close"], fast), sma(wk["close"], slow)
    state = pd.Series(0.0, index=wk.index)
    state[(wk["close"] > f) & (f > s)] = 1.0
    state[(wk["close"] < f) & (f < s)] = -1.0
    state[f.isna() | s.isna()] = np.nan
    return align_htf_to_daily(state, df.index)
