"""Yahoo Finance chart API: daily bars for forex, the macro panel and as a fallback for Pakistan stocks.

This is an unofficial but long-standing public endpoint. It sometimes refuses requests from shared cloud addresses
(HTTP 429), in which case the HTTP client switches the host off for the rest of the run and the caller moves on to
its next source. Only the chart route is used, one request per symbol.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pandas as pd

from .common import to_float
from ..model import normalise_bars
from .http import Client, FetchError, ParseError

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
URL = "https://{host}/v8/finance/chart/{symbol}"


def parse_chart(payload: Any, symbol: str = "") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Bars (normalised) and the ``meta`` block from a chart answer. Raises :class:`ParseError` with Yahoo's own message when it refused."""
    try:
        chart = payload["chart"]
    except (KeyError, TypeError) as exc:
        raise ParseError(f"unexpected Yahoo answer for {symbol}") from exc
    if chart.get("error"):
        raise ParseError(f"Yahoo: {(chart['error'] or {}).get('description') or chart['error']} ({symbol})")
    result = chart.get("result") or []
    if not result:
        raise ParseError(f"Yahoo has no data for {symbol}")
    r0 = result[0]
    stamps = r0.get("timestamp") or []
    quotes = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    meta = r0.get("meta") or {}
    if not stamps:
        raise ParseError(f"Yahoo returned no bars for {symbol}")
    offset = to_float(meta.get("gmtoffset")) or 0.0
    n = len(stamps)
    cols: dict[str, list[float | None]] = {}
    for k in ("open", "high", "low", "close", "volume"):
        raw = quotes.get(k) or []
        cols[k] = [to_float(raw[i]) if i < len(raw) else None for i in range(n)]
    secs = [to_float(t) for t in stamps]
    keep = [i for i in range(n) if secs[i] is not None and cols["close"][i] is not None]
    if not keep:
        raise ParseError(f"Yahoo returned only empty bars for {symbol}")
    days = pd.to_datetime([secs[i] + offset for i in keep], unit="s").normalize()  # the exchange's calendar day, one vectorised conversion
    df = pd.DataFrame({k: [cols[k][i] for i in keep] for k in cols}, index=days)
    return normalise_bars(df), meta


def chart(http: Client, symbol: str, *, range_: str = "5y", interval: str = "1d") -> tuple[pd.DataFrame, dict[str, Any]]:
    """Daily bars for a Yahoo symbol (``EURUSD=X``, ``^GSPC``, ``HBL.KA`` ...). Tries both Yahoo hostnames."""
    last: FetchError | None = None
    for host in HOSTS:
        try:
            reply = http.get(URL.format(host=host, symbol=quote(symbol, safe="")), params={"range": range_, "interval": interval, "includePrePost": "false"}, interval=0.6)
            return parse_chart(reply.json(), symbol)
        except ParseError:
            raise  # Yahoo answered and said no: the other hostname will say the same
        except FetchError as exc:
            last = exc
            if getattr(exc, "status", None) == 404:
                raise
    raise last or FetchError(f"Yahoo unreachable for {symbol}")
