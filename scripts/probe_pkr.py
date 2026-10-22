"""One-off probe (to be deleted): which USD/PKR level is real? Yahoo's rupee series mixes two levels since early September.

Prints Yahoo's daily and hourly USD/PKR next to independent sources, so the repair can be built on what they say.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from urllib.parse import quote

import pandas as pd

from acumen.fetch import yahoo as Y
from acumen.fetch.fx import Fawaz
from acumen.fetch.http import Client

http = Client("AcumenPersonalAnalytics/1.0", timeout=25, max_retries=1)


def section(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def attempt(label: str, fn) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never stops
        print(f"{label}: {type(exc).__name__}: {str(exc)[:240]}", flush=True)


def daily(sym: str, rng: str = "3mo", rows: int = 30) -> None:
    df, meta = Y.chart(http, sym, range_=rng)
    print(f"{sym}: {len(df)} bars; regularMarketPrice={meta.get('regularMarketPrice')} at {meta.get('regularMarketTime')}; previousClose={meta.get('previousClose')} chartPreviousClose={meta.get('chartPreviousClose')}")
    print(df.tail(rows)[["open", "high", "low", "close"]].round(3).to_string())


def hourly(sym: str) -> None:
    host = Y.HOSTS[0]
    reply = http.get(Y.URL.format(host=host, symbol=quote(sym, safe="")), params={"range": "5d", "interval": "1h", "includePrePost": "false"}, interval=0.6)
    r0 = reply.json()["chart"]["result"][0]
    stamps = r0["timestamp"]
    q = r0["indicators"]["quote"][0]
    print(f"{sym} hourly: {len(stamps)} bars")
    for t, o, h, lo, c in zip(stamps, q["open"], q["high"], q["low"], q["close"]):
        when = dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).strftime("%m-%d %H:%M")
        print(f"  {when}  o={o} h={h} l={lo} c={c}")


def fawaz_days() -> None:
    fw = Fawaz(http)
    today = pd.Timestamp(dt.datetime.now(dt.timezone.utc).date())
    for k in range(1, 16):
        d = today - pd.Timedelta(days=k)
        try:
            s = fw.day(d)
            print(f"  {d.date()}  PKR={s.get('PKR')}  AED={s.get('AED')}  EUR={s.get('EUR')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {d.date()}  {type(exc).__name__}: {str(exc)[:100]}")


def latest_fawaz() -> None:
    for url in ("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json", "https://latest.currency-api.pages.dev/v1/currencies/usd.json"):
        try:
            j = http.get(url).json()
            print(f"  {url}\n    date={j.get('date')} pkr={j['usd'].get('pkr')} aed={j['usd'].get('aed')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {url}\n    {type(exc).__name__}: {str(exc)[:120]}")


def er_api() -> None:
    j = http.get("https://open.er-api.com/v6/latest/USD").json()
    print(f"  open.er-api.com: PKR={j['rates'].get('PKR')} AED={j['rates'].get('AED')} updated {j.get('time_last_update_utc')}")


def er_api_v4() -> None:
    j = http.get("https://api.exchangerate-api.com/v4/latest/USD").json()
    print(f"  api.exchangerate-api.com v4: PKR={j['rates'].get('PKR')} date={j.get('date')}")


def coingecko_rates() -> None:
    j = http.get("https://api.coingecko.com/api/v3/exchange_rates").json()["rates"]
    print(f"  coingecko exchange_rates: pkr/usd = {j['pkr']['value'] / j['usd']['value']:.3f}  (btc in pkr {j['pkr']['value']}, in usd {j['usd']['value']})")


def kucoin_fiat() -> None:
    j = http.get("https://api.kucoin.com/api/v1/prices", params={"base": "USD", "currencies": "PKR,AED,EUR"}).json()
    print(f"  kucoin prices: {j}")


def binance_p2p_hint() -> None:
    j = http.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "USDTTRY"}).json()
    print(f"  (binance reachable: USDTTRY={j.get('price')})")


def wise_like() -> None:
    j = http.get("https://api.frankfurter.dev/v1/latest", params={"base": "USD", "symbols": "EUR"}).json()
    print(f"  frankfurter (no PKR): {j}")


def main() -> int:
    print(f"USD/PKR probe, {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    section("Yahoo daily USDPKR=X (last 30)")
    attempt("daily USDPKR=X", lambda: daily("USDPKR=X"))
    section("Yahoo daily PKR=X (last 12)")
    attempt("daily PKR=X", lambda: daily("PKR=X", rows=12))
    section("Yahoo dirham, riyal and euro against the rupee (dirham and riyal are pegged to the dollar: 3.6725 and 3.75)")
    for sym, peg in (("AEDPKR=X", 3.6725), ("SARPKR=X", 3.75)):
        def one(sym=sym, peg=peg) -> None:
            df, _ = Y.chart(http, sym, range_="1mo")
            print(sym, "implied USDPKR (last 12 closes):", [round(float(x) * peg, 2) for x in df["close"].tail(12)])
        attempt(sym, one)
    attempt("EURPKR=X", lambda: print("EURPKR=X last 8 closes:", [round(float(x), 2) for x in Y.chart(http, "EURPKR=X", range_="1mo")[0]["close"].tail(8)]))
    section("Yahoo hourly USDPKR=X, last 5 days")
    attempt("hourly", lambda: hourly("USDPKR=X"))
    section("currency-api dated snapshots (the app's own rate snapshot source), last 15 days")
    attempt("fawaz", fawaz_days)
    section("currency-api latest")
    attempt("latest", latest_fawaz)
    section("independent aggregators, right now")
    for label, fn in (("er-api", er_api), ("er-api v4", er_api_v4), ("coingecko", coingecko_rates), ("kucoin", kucoin_fiat)):
        attempt(label, fn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
