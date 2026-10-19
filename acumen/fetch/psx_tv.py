"""TradingView's public screener endpoint, used for Pakistan stock fundamentals (and as a last-resort price snapshot).

One request returns every listed Pakistan stock with the requested columns: market cap, P/E, EPS, dividend yield,
P/B, ROE and more. The endpoint is unofficial. Column names have changed over the years, so the request asks for a
long list and, when the server names a column it no longer knows, drops that column and asks again. A missing
column only means a missing field, never a failed run. TradingView is credited in the app's About page.
"""

from __future__ import annotations

import re
from typing import Any

from .common import positive, to_float
from .http import Client, FetchError, ParseError

URL = "https://scanner.tradingview.com/pakistan/scan"

COLUMNS = [
    "name", "description", "close", "change", "volume", "market_cap_basic",
    "price_earnings_ttm", "earnings_per_share_diluted_ttm", "earnings_per_share_basic_ttm",
    "dividends_yield", "dividend_yield_recent", "price_book_fq", "return_on_equity", "return_on_equity_fq",
    "book_value_per_share_fq", "total_shares_outstanding_fundamental", "total_shares_outstanding", "float_shares_outstanding",
    "dividend_payout_ratio_ttm", "earnings_per_share_diluted_yoy_growth_ttm", "earnings_per_share_basic_yoy_growth_ttm",
    "net_margin", "net_margin_ttm", "sector", "industry", "type",
]


def _first(row: dict[str, Any], *names: str) -> float | None:
    for n in names:
        v = to_float(row.get(n))
        if v is not None:
            return v
    return None


def _within(v: float | None, lo: float, hi: float) -> float | None:
    return v if v is not None and lo <= v <= hi else None


def fundamentals_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map one screener row to the fundamentals the engine reads. Fields that are absent or implausible are ``None``."""
    shares = positive(_first(row, "total_shares_outstanding_fundamental", "total_shares_outstanding"))
    float_sh = positive(row.get("float_shares_outstanding"))
    ff = None
    if shares and float_sh:
        ratio = float_sh / shares
        if 0 < ratio <= 1.05:
            ff = round(min(ratio, 1.0) * 100.0, 1)
    pe = _within(positive(row.get("price_earnings_ttm")), 0.0, 1000.0)
    return {
        "pe": pe,
        "pb": _within(positive(row.get("price_book_fq")), 0.0, 100.0),
        "eps": _first(row, "earnings_per_share_diluted_ttm", "earnings_per_share_basic_ttm"),
        "dy": _within(_first(row, "dividends_yield", "dividend_yield_recent"), 0.0, 100.0),
        "roe": _within(_first(row, "return_on_equity", "return_on_equity_fq"), -300.0, 300.0),
        "bvps": positive(row.get("book_value_per_share_fq")),
        "mcap": positive(row.get("market_cap_basic")),
        "shares": shares,
        "free_float_pct": ff,
        "payout_pct": _within(_first(row, "dividend_payout_ratio_ttm"), 0.0, 1000.0),
        "eps_growth_pct": _within(_first(row, "earnings_per_share_diluted_yoy_growth_ttm", "earnings_per_share_basic_yoy_growth_ttm"), -1000.0, 10000.0),
        "net_margin_pct": _within(_first(row, "net_margin", "net_margin_ttm"), -1000.0, 1000.0),
        # TradingView has no payout history: None means "unknown", which the engine leaves out instead of scoring as zero
        "dividends": None,
        "eps_hist": None,
    }


def parse_scan(payload: Any, columns: list[str]) -> dict[str, dict[str, Any]]:
    """``{symbol: {column: value}}`` from a scan answer."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ParseError("unexpected TradingView answer")
    out: dict[str, dict[str, Any]] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("s", ""))
        sym = ticker.split(":", 1)[-1].upper()
        vals = item.get("d")
        if not sym or not isinstance(vals, list):
            continue
        out[sym] = {c: (vals[i] if i < len(vals) else None) for i, c in enumerate(columns)}
    if not out:
        raise ParseError("TradingView returned no symbols")
    return out


def scan(http: Client, columns: list[str] | None = None, *, max_drops: int = 12) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch every Pakistan stock. Returns ``(rows, dropped_columns)``."""
    cols = list(columns or COLUMNS)
    dropped: list[str] = []
    headers = {"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}
    for _ in range(max_drops + 1):
        body = {
            "filter": [], "options": {"lang": "en"}, "markets": ["pakistan"], "symbols": {"query": {"types": []}, "tickers": []},
            "columns": cols, "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 2000],
        }
        reply = http.post(URL, json=body, headers=headers, ok_statuses=(400,), retries=1, interval=1.0)
        if reply.status == 400:
            text = reply.text
            bad = [c for c in cols if re.search(rf'(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])', text)]
            if not bad or len(cols) - len(bad) < 3:
                raise FetchError(f"TradingView refused the request: {' '.join(text.split())[:120]}", url=URL, status=400)
            dropped.extend(bad)
            cols = [c for c in cols if c not in bad]
            continue
        return parse_scan(reply.json(), cols), dropped
    raise FetchError("TradingView kept refusing column names", url=URL)


def quote_from_row(row: dict[str, Any], ts: str) -> dict[str, Any] | None:
    price = positive(row.get("close"))
    if price is None:
        return None
    chg = to_float(row.get("change"))
    prev = price / (1 + chg / 100.0) if chg is not None and chg > -99 else None
    return {"price": price, "prev_close": prev, "chg_pct": chg, "volume": to_float(row.get("volume")) or 0.0, "ts": ts}
