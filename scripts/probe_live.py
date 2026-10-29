"""One-off probe (to be deleted): can a web page on github.io call these price sources directly (CORS), from where?

For each source: one real request carrying ``Origin: https://abdullah550.github.io`` and one preflight, printing the status and the
``Access-Control-Allow-*`` answers. A page can read a response only when the allowed origin is ``*`` or its own, and a preflight
(sent for JSON bodies and custom headers) is answered with 2xx and lists the headers the page wants to send.
"""

from __future__ import annotations

import json
import sys
import time

import requests

ORIGIN = "https://abdullah550.github.io"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
S = requests.Session()

TV_BODY = {"symbols": {"tickers": ["FX_IDC:EURUSD", "FX_IDC:USDPKR", "OANDA:XAUUSD"], "query": {"types": []}}, "columns": ["name", "close", "change"], "options": {"lang": "en"}}

# (label, method, url, json body, extra request headers the page would send)
CASES = [
    ("tradingview forex scan", "POST", "https://scanner.tradingview.com/forex/scan", TV_BODY, {"Content-Type": "application/json"}),
    ("tradingview forex scan, text/plain body", "POST", "https://scanner.tradingview.com/forex/scan", TV_BODY, {"Content-Type": "text/plain;charset=UTF-8"}),
    ("tradingview crypto scan", "POST", "https://scanner.tradingview.com/crypto/scan", {**TV_BODY, "symbols": {"tickers": ["BINANCE:BTCUSDT"], "query": {"types": []}}}, {"Content-Type": "application/json"}),
    ("psx portal home page", "GET", "https://dps.psx.com.pk/", None, {}),
    ("psx portal market watch", "GET", "https://dps.psx.com.pk/market-watch", None, {"X-Req-Id": "x"}),
    ("yahoo chart (query1)", "GET", "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?range=1d&interval=5m", None, {}),
    ("yahoo chart (query2)", "GET", "https://query2.finance.yahoo.com/v8/finance/chart/EURUSD=X?range=1d&interval=5m", None, {}),
    ("coingecko markets", "GET", "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&per_page=5&page=1", None, {}),
    ("binance vision prices", "GET", "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT", None, {}),
    ("binance 24h tickers", "GET", "https://data-api.binance.vision/api/v3/ticker/24hr?symbols=%5B%22BTCUSDT%22%2C%22ETHUSDT%22%5D", None, {}),
    ("okx tickers", "GET", "https://www.okx.com/api/v5/market/tickers?instType=SPOT", None, {}),
    ("coinbase ticker", "GET", "https://api.exchange.coinbase.com/products/BTC-USD/ticker", None, {}),
    ("frankfurter fx", "GET", "https://api.frankfurter.app/latest?from=USD&to=PKR", None, {}),
    ("jsdelivr currency-api", "GET", "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json", None, {}),
    ("er-api", "GET", "https://open.er-api.com/v6/latest/USD", None, {}),
    ("github raw", "GET", "https://raw.githubusercontent.com/abdullah550/acumen/main/README.md", None, {}),
    ("github contents api", "GET", "https://api.github.com/repos/abdullah550/acumen/contents/README.md?ref=main", None, {}),
]
KEYS = ("access-control-allow-origin", "access-control-allow-headers", "access-control-allow-methods", "access-control-allow-credentials")


def cors(headers) -> str:
    got = {k: headers.get(k) for k in KEYS if headers.get(k)}
    return json.dumps(got) if got else "no CORS headers"


def main() -> int:
    print(f"CORS probe at {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC, origin {ORIGIN}")
    for label, method, url, body, extra in CASES:
        print(f"\n{label}  [{method} {url[:80]}]")
        headers = {"User-Agent": UA, "Origin": ORIGIN, "Referer": ORIGIN + "/", **extra}
        try:
            r = S.request(method, url, headers=headers, data=json.dumps(body) if body is not None else None, timeout=30)
            snippet = r.text[:90].replace("\n", " ")
            print(f"  request   : HTTP {r.status_code}, {len(r.content)} bytes, {cors(r.headers)}\n              body starts: {snippet!r}")
        except Exception as exc:  # noqa: BLE001 - a probe reports everything
            print(f"  request   : {type(exc).__name__}: {str(exc)[:150]}")
        ask = {"User-Agent": UA, "Origin": ORIGIN, "Access-Control-Request-Method": method,
               "Access-Control-Request-Headers": ",".join(k.lower() for k in extra) or "accept"}
        try:
            p = S.options(url, headers=ask, timeout=30)
            print(f"  preflight : HTTP {p.status_code}, {cors(p.headers)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  preflight : {type(exc).__name__}: {str(exc)[:150]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
