"""Market regime from a benchmark series (causal), and per-market summaries."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

LABELS = {
    "bull": "Bull",
    "recovery": "Recovery",
    "range": "Range",
    "correction": "Correction",
    "bear": "Bear",
}


def regime_frame(close: pd.Series) -> pd.DataFrame:
    """Daily regime code and score in [-1, 1] from a benchmark close (needs about 250 bars)."""
    c = close.astype(float)
    s50, s200 = ind.sma(c, 50), ind.sma(c, 200)
    slope200 = s200 / s200.shift(20) - 1.0
    dd = c / c.rolling(252, min_periods=120).max() - 1.0
    above200, above50 = c > s200, c > s50
    score = (
        0.35 * np.sign(c - s200)
        + 0.25 * np.sign(s50 - s200)
        + 0.25 * np.tanh(slope200 * 15.0)
        + 0.15 * np.tanh((dd + 0.05) * 10.0)
    )
    code = pd.Series("range", index=c.index, dtype=object)
    code[(~above200) & (slope200 < 0)] = "bear"
    code[(~above200) & (slope200 >= 0)] = "correction"
    code[above200 & (~above50)] = "correction"
    code[above200 & above50 & (slope200 > 0)] = "bull"
    code[(~above200) & above50] = "recovery"
    code = code.where(s200.notna(), None)
    return pd.DataFrame({"code": code, "score": score.where(s200.notna())})


def regime_tilt_series(close: pd.Series, tilt_cfg: dict[str, float]) -> pd.Series:
    rf = regime_frame(close)
    return rf["code"].map(lambda k: float(tilt_cfg.get(k, 0.0)) if isinstance(k, str) else 0.0).astype(float)


def describe(close: pd.Series, name: str) -> dict:
    """Overview block for one benchmark: label, score and the numbers behind it."""
    rf = regime_frame(close)
    if rf["code"].dropna().empty:
        return {"code": "range", "label": "Not enough history", "score": None, "trend": None, "volatility": None}
    code, score = rf["code"].iloc[-1], float(rf["score"].iloc[-1])
    s50, s200 = ind.sma(close, 50), ind.sma(close, 200)
    dvol = close.pct_change().rolling(14).std() * 100.0  # typical daily move: 14-day standard deviation of daily returns, in percent
    pct = ind.percentile_rank(dvol, 252).iloc[-1] if len(close) >= 300 else np.nan
    vol_word = "" if not np.isfinite(pct) else (", volatile" if pct >= 70 else ", quiet" if pct <= 30 else "")
    slope = (s200.iloc[-1] / s200.iloc[-21] - 1.0) * 100.0 if len(s200.dropna()) > 21 else np.nan
    note = {
        "bull": f"{name} is above a rising 200-day average.",
        "recovery": f"{name} is back above its 50-day average but still below the 200-day.",
        "range": f"{name} has no clear direction against its averages.",
        "correction": f"{name} has slipped below its 50-day average.",
        "bear": f"{name} is below a falling 200-day average.",
    }[code]
    return {
        "code": code,
        "label": LABELS[code] + vol_word,
        "score": round(score, 2),
        "trend": {
            "symbol": name,
            "close": round(float(close.iloc[-1]), 4),
            "sma50": None if not np.isfinite(s50.iloc[-1]) else round(float(s50.iloc[-1]), 4),
            "sma200": None if not np.isfinite(s200.iloc[-1]) else round(float(s200.iloc[-1]), 4),
            "above_200": bool(close.iloc[-1] > s200.iloc[-1]),
            "slope_200_pct": None if not np.isfinite(slope) else round(float(slope), 2),
        },
        "volatility": {"daily_vol_pct": round(float(dvol.iloc[-1]), 2) if np.isfinite(dvol.iloc[-1]) else None, "percentile": None if not np.isfinite(pct) else int(round(pct))},
        "note": note,
    }
