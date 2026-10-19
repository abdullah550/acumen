"""Shared data model used by the fetch layer, the analysis layer and the builder.

Conventions (every module relies on these):

* Instrument ids are ``"<MARKET>:<SYMBOL>"`` in upper case: ``PSX:HBL``, ``FX:EURUSD``, ``CRYPTO:BTC``.
* ``market`` is one of ``psx``, ``fx``, ``crypto``.
* Bars are daily, one DataFrame per instrument, indexed by a tz-naive ``DatetimeIndex`` normalised to
  midnight (the exchange session date, Pakistan date for PSX, UTC date for crypto and forex),
  sorted ascending, no duplicate dates, columns ``open high low close volume`` as float64.
  An optional boolean column ``approx_hl`` marks bars whose high/low were synthesised from open/close
  because the source did not publish them.
* Volume is in shares (PSX), base-currency units (crypto) or 0 (forex, where it is not meaningful).
* Prices are in the instrument's quote currency: PKR for PSX, USD (usually USDT) for crypto,
  the pair's quote currency for forex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

MARKETS = ("psx", "fx", "crypto")
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


def make_id(market: str, symbol: str) -> str:
    return f"{market.upper()}:{symbol.upper()}"


def split_id(inst_id: str) -> tuple[str, str]:
    market, _, symbol = inst_id.partition(":")
    return market.lower(), symbol


@dataclass
class Instrument:
    id: str
    market: str
    symbol: str
    name: str
    sector: str | None = None
    # equity | etf | index | fx | metal | crypto
    kind: str = "equity"
    # False for indices, PKR crosses that retail brokers do not offer, etc. Still analysed and shown.
    tradable: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceStatus:
    """One line of the data-health report."""

    source: str
    ok: bool
    message: str = ""
    items: int = 0
    duration_s: float = 0.0
    at: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketData:
    market: str
    instruments: dict[str, Instrument] = field(default_factory=dict)
    bars: dict[str, pd.DataFrame] = field(default_factory=dict)
    # id -> flat dict of numbers/strings. PSX: pe, pb, eps, dividend_yield, roe, bvps, market_cap,
    # shares, free_float, dividends(list). Crypto: market_cap, rank, circulating_supply, max_supply, ath...
    fundamentals: dict[str, dict[str, Any]] = field(default_factory=dict)
    # id -> {price, prev_close, chg_pct, volume, ts}
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: list[SourceStatus] = field(default_factory=list)
    # market specific extras: psx breadth / sector summary / index weights, crypto global stats, fx rates
    extras: dict[str, Any] = field(default_factory=dict)

    def ids(self) -> list[str]:
        return list(self.instruments)


def empty_bars() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in BAR_COLUMNS})
    df.index = pd.DatetimeIndex([], name="date")
    return df


def normalise_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce any OHLCV frame to the conventions above. Drops bars without a positive close."""
    if df is None or len(df) == 0:
        return empty_bars()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out.index.name = "date"
    for col in BAR_COLUMNS:
        if col not in out.columns:
            out[col] = float("nan")
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")  # a column of whole numbers must not stay int64
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out[out["close"] > 0]
    # Fill missing open/high/low from what is known so downstream maths never sees NaN.
    approx = out["high"].isna() | out["low"].isna()
    out["open"] = out["open"].fillna(out["close"])
    hi_fallback = out[["open", "close"]].max(axis=1)
    lo_fallback = out[["open", "close"]].min(axis=1)
    out["high"] = out["high"].fillna(hi_fallback)
    out["low"] = out["low"].fillna(lo_fallback)
    out["high"] = out[["high", "open", "close"]].max(axis=1)
    out["low"] = out[["low", "open", "close"]].min(axis=1)
    out["volume"] = out["volume"].fillna(0.0).clip(lower=0.0)
    if "approx_hl" in out.columns:
        out["approx_hl"] = out["approx_hl"].fillna(False).astype(bool) | approx
    elif approx.any():
        out["approx_hl"] = approx
    return out
