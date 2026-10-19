"""Cross-asset and Pakistan macro panel."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from .instrument import _pct_days

LABELS = {"USDPKR": "USD/PKR", "DXY": "US dollar index", "GOLD": "Gold (USD/oz)", "BRENT": "Brent crude (USD/bbl)", "SPX": "S&P 500", "VIX": "VIX",
          "US10Y": "US 10-year yield (%)", "MSCI_EM": "Emerging markets (EEM)", "FEDFUNDS": "Fed funds rate (%)", "DGS2": "US 2-year yield (%)", "T10Y2Y": "US 10y-2y spread (%)"}
ABSOLUTE = {"VIX", "US10Y", "FEDFUNDS", "DGS2", "T10Y2Y"}  # changes are differences in points, not percent


def _r(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def series_block(name: str, s: pd.Series) -> dict | None:
    s = s.dropna()
    if len(s) < 3:
        return None
    last = float(s.iloc[-1])
    def chg(days: int):
        base = s.loc[: s.index[-1] - pd.Timedelta(days=days)]
        if base.empty:
            return None
        b = float(base.iloc[-1])
        if name in ABSOLUTE:
            return _r(last - b, 2)
        return _r((last / b - 1.0) * 100.0, 2) if b else None
    d1 = None
    if len(s) > 1:
        p = float(s.iloc[-2])
        d1 = _r(last - p, 2) if name in ABSOLUTE else (_r((last / p - 1.0) * 100.0, 2) if p else None)
    return {"label": LABELS.get(name, name), "last": _r(last, 4), "asof": s.index[-1].strftime("%Y-%m-%d"), "chg_1d": d1, "chg_1w": chg(7), "chg_1m": chg(30),
            "unit": "points" if name in ABSOLUTE else "percent", "spark": [round(float(v), 4) for v in s.iloc[-30:]]}


def macro_block(extras: dict[str, Any], fx_bars: dict[str, pd.DataFrame], cfg: Any, today: str) -> tuple[dict, dict]:
    """Returns (macro.json body, headline numbers for the overview)."""
    m = (extras or {}).get("macro", {}) or {}
    series: dict[str, pd.Series] = dict(m.get("series", {}))
    usdpkr = fx_bars.get("FX:USDPKR")
    if usdpkr is not None and len(usdpkr) > 5:
        series["USDPKR"] = usdpkr["close"]
    out_series = {}
    for name, s in series.items():
        b = series_block(name, s)
        if b:
            out_series[name] = b
    rates = cfg.get_path("rates", {}) or {}
    as_of = str(rates.get("as_of", ""))
    stale = True
    try:
        stale = (pd.Timestamp(today) - pd.Timestamp(as_of)).days > 120
    except ValueError:
        pass
    pk = dict(m.get("pk", {}) or {})
    policy = pk.get("policy_rate")
    asof = pk.get("asof")
    note = pk.get("note")
    if policy is None:
        policy = (rates.get("policy_rate_pct", {}) or {}).get("PKR")
        if policy is not None:
            asof = as_of or None  # the table's own date: the app warns when it is old
            note = ((note or "") + " Policy rate comes from the configured table, not a live source.").strip()
    cpi = pk.get("cpi_yoy")
    pakistan = {"policy_rate": policy, "cpi_yoy": cpi, "real_rate": _r(policy - cpi, 2) if (policy is not None and cpi is not None) else None,
                "asof": asof, "note": (note or "").strip() or None}
    body = {"series": out_series, "rates": {"as_of": as_of or None, "stale": stale, "policy_pct": rates.get("policy_rate_pct", {})}, "pakistan": pakistan}
    headline = {
        "usd_pkr": out_series.get("USDPKR", {}).get("last"), "policy_rate_pk": policy, "dxy": out_series.get("DXY", {}).get("last"),
        "gold": out_series.get("GOLD", {}).get("last"), "brent": out_series.get("BRENT", {}).get("last"), "vix": out_series.get("VIX", {}).get("last"),
    }
    return body, headline
