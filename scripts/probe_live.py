"""One-off probe (to be deleted): which sources can give live prices from a GitHub runner, and how fresh are they?

Asks, from GitHub's servers, the questions the "live prices" update depends on: TradingView's forex and crypto scanners by
explicit ticker, Yahoo's chart answer (how old is its live price) and its batch quote route, CoinGecko's market list, the
exchanges' price lists, and the PSX market watch (through the app's own client, so the request token is exercised too).
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time

import requests

UA = "AcumenPersonalAnalytics/1.0"
S = requests.Session()
S.headers.update({"User-Agent": UA})
NOW = dt.datetime.now(dt.timezone.utc)


def section(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def timed(fn):
    t = time.time()
    try:
        out = fn()
        return out, time.time() - t, None
    except Exception as exc:  # noqa: BLE001 - a probe reports everything
        return None, time.time() - t, f"{type(exc).__name__}: {str(exc)[:200]}"


def tv(path: str, body: dict) -> tuple[int, str]:
    r = S.post(f"https://scanner.tradingview.com/{path}/scan", json=body, headers={"Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"}, timeout=30)
    return r.status_code, r.text


def show_tv(label: str, path: str, body: dict, cols: list[str], limit: int = 40) -> None:
    (res, secs, err) = timed(lambda: tv(path, body))
    if err:
        print(f"{label}: {err} ({secs:.1f}s)")
        return
    status, text = res
    try:
        data = json.loads(text)
    except ValueError:
        print(f"{label}: HTTP {status}, not JSON: {text[:200]!r} ({secs:.1f}s)")
        return
    rows = data.get("data") if isinstance(data, dict) else None
    if rows is None:
        print(f"{label}: HTTP {status}: {text[:240]!r} ({secs:.1f}s)")
        return
    print(f"{label}: HTTP {status}, {len(rows)} rows (totalCount {data.get('totalCount')}), {len(text) // 1024} KB, {secs:.1f}s")
    for item in rows[:limit]:
        print("   ", item.get("s"), dict(zip(cols, item.get("d", []))))


def tv_probe() -> None:
    cols = ["name", "close", "change", "high", "low", "open", "exchange", "type", "update_mode", "currency"]
    section("TradingView forex scanner, explicit tickers")
    tickers = ["FX_IDC:EURUSD", "OANDA:EURUSD", "FX:EURUSD", "FX:USDJPY", "FX_IDC:USDJPY", "OANDA:XAUUSD", "OANDA:XAGUSD", "FX_IDC:USDPKR", "OANDA:USDPKR", "FX:GBPUSD", "FX_IDC:USDTRY"]
    body = {"symbols": {"tickers": tickers, "query": {"types": []}}, "columns": cols, "options": {"lang": "en"}}
    show_tv("forex/scan by ticker", "forex", body, cols)
    show_tv("global/scan by ticker", "global", body, cols)
    section("TradingView forex scanner, by name (which venues list each pair)")
    names = ["EURUSD", "USDPKR", "XAUUSD", "XAGUSD", "USDJPY", "USDTRY", "EURPKR", "USDAED"]
    body = {"filter": [{"left": "name", "operation": "in_range", "right": names}], "columns": cols, "range": [0, 100], "options": {"lang": "en"}, "sort": {"sortBy": "name", "sortOrder": "asc"}}
    show_tv("forex/scan by name", "forex", body, cols, limit=60)
    section("TradingView crypto scanner, explicit tickers")
    body = {"symbols": {"tickers": ["BINANCE:BTCUSDT", "COINBASE:BTCUSD", "BINANCE:ETHUSDT", "KRAKEN:XBTUSD"], "query": {"types": []}}, "columns": cols, "options": {"lang": "en"}}
    show_tv("crypto/scan by ticker", "crypto", body, cols)
    section("TradingView Pakistan scanner: the index and a stock, with the delay field")
    cols2 = ["name", "close", "change", "high", "low", "open", "volume", "exchange", "type", "update_mode"]
    body = {"symbols": {"tickers": ["PSX:KSE100", "PSX:OGDC", "PSX:HBL"], "query": {"types": []}}, "columns": cols2, "options": {"lang": "en"}}
    show_tv("global/scan PSX", "global", body, cols2)
    show_tv("pakistan/scan PSX", "pakistan", body, cols2)


def yahoo_probe() -> None:
    section("Yahoo chart: how old is the live price, and does a batch quote work?")
    for sym in ("EURUSD=X", "USDPKR=X", "GC=F", "USDJPY=X"):
        def go():
            r = S.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "1d", "interval": "5m"}, timeout=25)
            return r.status_code, r.json() if r.status_code == 200 else r.text[:120]
        (res, secs, err) = timed(go)
        if err or res[0] != 200:
            print(f"{sym}: {err or res} ({secs:.1f}s)")
            continue
        meta = res[1]["chart"]["result"][0]["meta"]
        t = meta.get("regularMarketTime")
        age = (NOW - dt.datetime.fromtimestamp(t, tz=dt.timezone.utc)).total_seconds() / 60 if t else None
        ts = res[1]["chart"]["result"][0].get("timestamp") or []
        print(f"{sym}: price {meta.get('regularMarketPrice')}, previous close {meta.get('chartPreviousClose')}, {age:.0f} min old, {len(ts)} five-minute bars ({secs:.1f}s)")
    (res, secs, err) = timed(lambda: S.get("https://query1.finance.yahoo.com/v7/finance/quote", params={"symbols": "EURUSD=X,GBPUSD=X,USDPKR=X"}, timeout=25))
    print("v7 batch quote:", err or f"HTTP {res.status_code} {res.text[:160]!r}", f"({secs:.1f}s)")


def crypto_probe() -> None:
    section("Crypto price lists")
    (res, secs, err) = timed(lambda: S.get("https://api.coingecko.com/api/v3/coins/markets", params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": 1, "sparkline": "false", "price_change_percentage": "24h"}, timeout=30))
    if err or res.status_code != 200:
        print("coingecko markets:", err or f"HTTP {res.status_code} {res.text[:120]!r}", f"({secs:.1f}s)")
    else:
        rows = res.json()
        ages = sorted((NOW - dt.datetime.fromisoformat(r["last_updated"].replace("Z", "+00:00"))).total_seconds() / 60 for r in rows if r.get("last_updated"))
        print(f"coingecko markets: {len(rows)} coins, {len(res.content) // 1024} KB, {secs:.1f}s; age of the quote in minutes: median {ages[len(ages) // 2]:.1f}, max {ages[-1]:.1f}; BTC {rows[0]['current_price']}")
    (res, secs, err) = timed(lambda: S.get("https://data-api.binance.vision/api/v3/ticker/price", timeout=30))
    if err or res.status_code != 200:
        print("binance ticker/price:", err or f"HTTP {res.status_code} {res.text[:120]!r}", f"({secs:.1f}s)")
    else:
        print(f"binance ticker/price: {len(res.json())} symbols, {len(res.content) // 1024} KB, {secs:.1f}s")
    (res, secs, err) = timed(lambda: S.get("https://data-api.binance.vision/api/v3/ticker/24hr", params={"symbols": json.dumps(["BTCUSDT", "ETHUSDT", "SOLUSDT"], separators=(",", ":"))}, timeout=30))
    print("binance ticker/24hr for three symbols:", err or f"HTTP {res.status_code} {res.text[:300]!r}", f"({secs:.1f}s)")
    (res, secs, err) = timed(lambda: S.get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SPOT"}, timeout=30))
    if err or res.status_code != 200:
        print("okx tickers:", err or f"HTTP {res.status_code} {res.text[:120]!r}", f"({secs:.1f}s)")
    else:
        print(f"okx tickers: {len(res.json().get('data', []))} markets, {len(res.content) // 1024} KB, {secs:.1f}s")


def psx_probe() -> None:
    section("PSX market watch through the app's own client (honest user agent, request token)")
    from acumen.fetch import psx_dps
    from acumen.fetch.http import Client

    http = Client(UA, timeout=40)
    dps = psx_dps.Dps(http, interval=1.0, log=print)
    (ok, secs, err) = timed(dps.ensure_node)
    print(f"ensure_node: {ok} {err or ''} ({secs:.1f}s), token {'read' if dps.token else 'missing'}: {dps.token_note}")
    (rows, secs, err) = timed(dps.market_watch)
    if err:
        print("market_watch:", err)
        return
    traded = [r for r in rows if (r.get("close") or 0) > 0]
    print(f"market_watch: {len(rows)} rows, {len(traded)} with a price, {secs:.1f}s; sample {rows[0]}")
    (idx, secs, err) = timed(dps.indices)
    print("indices:", err or {k: v for k, v in list(idx.items())[:3]}, f"({secs:.1f}s)")
    (eod, secs, err) = timed(lambda: dps.eod("KSE100"))
    print("KSE100 eod:", err or f"{len(eod)} bars, newest {eod.index[-1].date()} close {eod['close'].iloc[-1]}", f"({secs:.1f}s)")


def main() -> int:
    print(f"probe at {NOW:%Y-%m-%d %H:%M} UTC")
    for fn in (tv_probe, yahoo_probe, crypto_probe, psx_probe):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"{fn.__name__} failed: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
