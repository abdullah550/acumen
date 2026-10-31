"""One-off probe (to be deleted): how well does TradingView's scanner cover each market, the way a web page would ask for it?

A page on github.io can POST to the scanner (it answers any origin, and a text/plain body needs no preflight). This asks it, from a
GitHub runner, for the instruments Acumen follows and reports coverage, sizes, timings and agreement with the portal/CoinGecko.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter

import requests

ORIGIN = "https://abdullah550.github.io"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Origin": ORIGIN, "Referer": ORIGIN + "/", "Content-Type": "text/plain;charset=UTF-8"})
COLS = ["name", "close", "change", "high", "low", "open", "volume"]


def say(*a) -> None:
    print(*a, flush=True)


def scan(path: str, body: dict) -> tuple[list[dict], dict]:
    t = time.time()
    r = S.post(f"https://scanner.tradingview.com/{path}/scan", data=json.dumps(body), timeout=60)
    info = {"status": r.status_code, "secs": round(time.time() - t, 2), "bytes": len(r.content), "wire": r.headers.get("Content-Length"), "enc": r.headers.get("Content-Encoding"),
            "sent": len(json.dumps(body))}
    if r.status_code != 200:
        info["error"] = r.text[:200]
        return [], info
    data = r.json()
    info["total"] = data.get("totalCount")
    rows = [{"s": it["s"], **dict(zip(COLS, it["d"]))} for it in data.get("data", [])]
    return rows, info


def explicit(path: str, tickers: list[str], cols: list[str] = COLS) -> tuple[list[dict], dict]:
    return scan(path, {"symbols": {"tickers": tickers, "query": {"types": []}}, "columns": cols, "options": {"lang": "en"}})


def section(t: str) -> None:
    say(f"\n=== {t}")


# ------------------------------------------------------------------------------------------------------------- PSX
def psx() -> None:
    from acumen.fetch import psx_dps
    from acumen.fetch.http import Client

    section("PSX: portal symbols versus TradingView")
    http = Client("AcumenPersonalAnalytics/1.0", timeout=40)
    dps = psx_dps.Dps(http, interval=1.0, log=say)
    dps.ensure_node()
    rows = dps.market_watch()
    by = {r["symbol"]: r for r in rows}
    say(f"portal market watch: {len(rows)} symbols; sample {rows[0]}")
    syms = sorted(by)
    tickers = ["PSX:KSE100", "PSX:KSE30", "PSX:ALLSHR", "PSX:KMI30"] + [f"PSX:{s}" for s in syms]
    for path in ("pakistan", "global"):
        got, info = explicit(path, tickers)
        say(f"{path}/scan with {len(tickers)} explicit tickers -> {len(got)} rows, {info}")
    got, info = explicit("pakistan", tickers)
    tv = {r["s"].split(":", 1)[1]: r for r in got}
    missing = [s for s in syms if s not in tv]
    say(f"missing from TV: {len(missing)} of {len(syms)}; first {missing[:25]}")
    diffs = []
    for s in syms:
        a, b = by[s].get("close") or by[s].get("ldcp"), (tv.get(s) or {}).get("close")
        if a and b:
            diffs.append(abs(b / a - 1) * 100)
    if diffs:
        say(f"TV close vs portal close: n={len(diffs)}, equal within 0.05%: {sum(d < 0.05 for d in diffs)}, median {statistics.median(diffs):.3f}%, max {max(diffs):.2f}%")
    say("index rows:", [{k: r[k] for k in ("s", "close", "change")} for r in got if r["s"].split(":")[1].startswith(("KSE", "ALL", "KMI"))])
    body = {"filter": [], "options": {"lang": "en"}, "markets": ["pakistan"], "symbols": {"query": {"types": []}, "tickers": []}, "columns": COLS, "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"}, "range": [0, 2000]}
    got2, info2 = scan("pakistan", body)
    say(f"pakistan market-wide scan -> {len(got2)} rows, {info2}; includes KSE100: {any(r['s'] == 'PSX:KSE100' for r in got2)}")


# -------------------------------------------------------------------------------------------------------------- FX
def fx() -> None:
    from acumen.fetch import fx as fxm

    section("FX: universe versus TradingView (FX_IDC, OANDA, FX, and more for metals)")
    pairs = [p for p in fxm.universe({}) if not p.synthetic]
    say(f"{len(pairs)} direct pairs, e.g. {[p.symbol for p in pairs[:8]]}")
    venues = ["FX_IDC", "OANDA", "FX"]
    tickers = [f"{v}:{p.symbol}" for p in pairs for v in venues]
    extra_metal = [f"{v}:{s}" for s in ("XAUUSD", "XAGUSD") for v in ("FOREXCOM", "CAPITALCOM", "SKILLING", "PEPPERSTONE", "IG", "SAXO", "ICE", "FX_IDC")] + ["TVC:GOLD", "TVC:SILVER", "COMEX:GC1!", "COMEX:SI1!"]
    for path in ("global", "forex"):
        got, info = explicit(path, tickers + extra_metal)
        say(f"{path}/scan with {len(tickers) + len(extra_metal)} tickers -> {len(got)} rows, {info}")
    got, _ = explicit("global", tickers + extra_metal)
    found: dict[str, dict[str, float]] = {}
    for r in got:
        v, n = r["s"].split(":", 1)
        found.setdefault(n, {})[v] = r["close"]
    miss = [p.symbol for p in pairs if not any(v in found.get(p.symbol, {}) for v in venues)]
    say(f"pairs with no price from {venues}: {miss}")
    for sym in ("XAUUSD", "XAGUSD", "GOLD", "SILVER", "GC1!", "SI1!", "USDPKR", "USDCNH", "USDINR"):
        say(f"  {sym}: {found.get(sym)}")
    spread = []
    for p in pairs:
        vals = [x for x in found.get(p.symbol, {}).values() if x]
        if len(vals) >= 2:
            spread.append((max(vals) / min(vals) - 1) * 100)
    if spread:
        say(f"venues agree: median spread {statistics.median(spread):.4f}%, max {max(spread):.3f}%")


# ----------------------------------------------------------------------------------------------------------- crypto
def crypto() -> None:
    from acumen.fetch.crypto import CoinGecko, pick_universe
    from acumen.fetch.http import Client

    section("Crypto: the app's universe versus TradingView")
    http = Client("AcumenPersonalAnalytics/1.0", timeout=40)
    rows = CoinGecko(http, None).markets(500)
    coins, skipped = pick_universe(rows, top_n=500, min_volume=2_000_000, exclude_stables=True)
    say(f"universe: {len(coins)} coins (left out {dict(skipped)}); top {[c['symbol'] for c in coins[:6]]}")
    cg = {c["symbol"]: c["price"] for c in coins}
    venues = [("BINANCE", "USDT"), ("COINBASE", "USD"), ("OKX", "USDT"), ("KUCOIN", "USDT"), ("KRAKEN", "USD"), ("BYBIT", "USDT")]
    # A: explicit tickers on three venues
    tickers = [f"{v}:{c['symbol']}{q}" for c in coins for v, q in venues[:3]]
    got, info = explicit("crypto", tickers)
    say(f"A. crypto/scan, {len(tickers)} explicit tickers (3 venues) -> {len(got)} rows, {info}")
    seen: dict[str, dict[str, float]] = {}
    for r in got:
        v, n = r["s"].split(":", 1)
        for c in coins:
            for vv, q in venues[:3]:
                if vv == v and n == c["symbol"] + q:
                    seen.setdefault(c["symbol"], {})[v] = r["close"]
    covered = [c["symbol"] for c in coins if c["symbol"] in seen]
    say(f"   coins with a price on BINANCE/COINBASE/OKX: {len(covered)} of {len(coins)}; first venue wins: {Counter(next(iter(seen[s])) for s in covered)}")
    ratios = [(s, seen[s][next(iter(seen[s]))] / cg[s]) for s in covered if cg.get(s)]
    off = sorted([x for x in ratios if not 0.9 <= x[1] <= 1.1], key=lambda x: abs(x[1] - 1), reverse=True)
    say(f"   TV vs CoinGecko price: {len(ratios)} compared, {len(off)} differ by more than 10%: {[(s, round(r, 3)) for s, r in off[:12]]}")
    say(f"   not covered (first 40): {[c['symbol'] for c in coins if c['symbol'] not in seen][:40]}")
    # B: filters, no ticker list in the request
    total = 0
    union: dict[str, str] = {}
    for v, q in venues:
        body = {"filter": [{"left": "exchange", "operation": "equal", "right": v}, {"left": "currency", "operation": "equal", "right": q}], "options": {"lang": "en"},
                "markets": ["crypto"], "symbols": {"query": {"types": []}, "tickers": []}, "columns": COLS, "sort": {"sortBy": "volume", "sortOrder": "desc"}, "range": [0, 1500]}
        got, info = scan("crypto", body)
        total += len(got)
        for r in got:
            n = r["s"].split(":", 1)[1]
            if n.endswith(q):
                union.setdefault(n[: -len(q)], v)
        say(f"B. crypto/scan filter {v}/{q} -> {len(got)} rows, {info}")
    inb = [c["symbol"] for c in coins if c["symbol"] in union]
    say(f"   coins found by filters over {len(venues)} venues: {len(inb)} of {len(coins)}")
    only3 = [c["symbol"] for c in coins if c["symbol"] in union and union[c["symbol"]] in ("BINANCE", "COINBASE", "OKX")]
    say(f"   of which on BINANCE/COINBASE/OKX: {len(only3)}")


def main() -> int:
    say(f"probe at {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC")
    for fn in (psx, fx, crypto):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - a probe reports everything
            import traceback

            say(f"{fn.__name__} failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
