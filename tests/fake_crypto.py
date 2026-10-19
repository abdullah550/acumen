"""A simulated crypto data landscape (CoinGecko, five exchanges, fear and greed) served through ``FakeSession``.

Every venue answers in the shape its real API documents, from one set of "true" daily candles per coin, so tests can check
that the fetcher picks the right coins, the right venue, completed days only, and recovers when something is down.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from tests.fakenet import FakeResponse, FakeSession, ok

EXCHANGES = ("binance_vision", "okx", "coinbase", "kraken", "kucoin")


@dataclass
class Coin:
    symbol: str
    id: str
    name: str
    price: float
    mcap: float
    volume_24h: float
    listed: tuple[str, ...] = EXCHANGES
    history: int | None = None  # days of history the coin has (None: the whole simulated span)
    ath_mult: float = 3.0
    factor: dict[str, float] = field(default_factory=dict)  # venue -> price multiplier: a venue whose numbers disagree


def default_coins() -> list[Coin]:
    return [
        Coin("BTC", "bitcoin", "Bitcoin", 62000.0, 1.3e12, 3e10),
        Coin("ETH", "ethereum", "Ethereum", 2400.0, 4.0e11, 1.5e10),
        Coin("USDT", "tether", "Tether", 1.0001, 1.2e11, 6e10, listed=(), ath_mult=1.3),
        Coin("SOL", "solana", "Solana", 150.0, 8e10, 3e9, listed=("binance_vision", "okx", "kucoin")),
        Coin("WBTC", "wrapped-bitcoin", "Wrapped Bitcoin", 61900.0, 1.0e10, 2e8, listed=("binance_vision",)),
        Coin("AAA", "aaa-token", "Alpha Token", 1.7, 2e9, 5e7, listed=("kucoin",)),
        Coin("BBB", "bbb-coin", "Beta Coin", 12.0, 3e9, 8e7, listed=("kraken",)),
        Coin("CCC", "ccc-chain", "Gamma Chain", 0.8, 1.5e9, 5e6, listed=()),  # only CoinGecko knows it
        Coin("YOUNG", "young-coin", "Young Coin", 3.3, 5e8, 2e7, listed=("binance_vision",), history=60),
        Coin("ETH", "ethereum-fake", "Fake Ether", 0.02, 1e8, 3e6, listed=()),  # same ticker as a larger coin
        Coin("THIN", "thin-coin", "Thin Coin", 0.5, 4e8, 1e5, listed=("binance_vision",)),  # trades too little
        Coin("BAD$", "bad-dollar", "Bad Ticker", 0.1, 3e8, 5e6, listed=()),  # not usable as a file name
    ]


class CryptoWorld:
    def __init__(self, coins: list[Coin] | None = None, days: int = 520, last: dt.date = dt.date(2026, 10, 16), seed: int = 11):
        self.coins = coins or default_coins()
        self.today = dt.date(2026, 10, 9)  # the day "now" falls on; its candle is still forming
        self.down: set[str] = set()  # venue names (or "coingecko", "fng") that answer 503/429
        self.down_candles: set[str] = set()  # venues whose market list works but whose candle route answers 503
        self.log: list[str] = []
        rng = np.random.default_rng(seed)
        self.dates = [last - dt.timedelta(days=k) for k in range(days, -1, -1)]
        self.rows: dict[str, dict[dt.date, dict[str, float]]] = {}
        for c in self.coins:
            n = min(c.history or len(self.dates), len(self.dates))
            ds = self.dates[-n:]
            vol = 0.025 if c.symbol != "USDT" else 0.0002
            closes = np.exp(np.cumsum(rng.normal(0, vol, n)))
            closes = closes / closes[ds.index(self.today) if self.today in ds else -1] * c.price
            rows = {}
            prev = float(closes[0]) * (1 + rng.normal(0, 0.003))
            for d, cl in zip(ds, closes):
                cl = float(cl)
                o = prev
                h = max(o, cl) * (1 + abs(rng.normal(0, 0.008)))
                lo = min(o, cl) * (1 - abs(rng.normal(0, 0.008)))
                rows[d] = {"open": o, "high": h, "low": lo, "close": cl, "volume": float(rng.uniform(1e3, 1e5))}
                prev = cl
            self.rows[c.id] = rows

    # ------------------------------------------------------------------ truth
    def now(self, hour: int = 0, minute: int = 30) -> dt.datetime:
        return dt.datetime(self.today.year, self.today.month, self.today.day, hour, minute, tzinfo=dt.timezone.utc)

    def at(self, day: dt.date) -> "CryptoWorld":
        self.today = day
        return self

    def candles(self, coin: Coin, venue: str | None = None) -> list[tuple[dt.date, dict[str, float]]]:
        """Every candle the world has up to and including today's forming one, with the venue's price factor applied."""
        f = coin.factor.get(venue or "", 1.0)
        out = []
        for d in sorted(self.rows[coin.id]):
            if d <= self.today:
                r = self.rows[coin.id][d]
                out.append((d, {"open": r["open"] * f, "high": r["high"] * f, "low": r["low"] * f, "close": r["close"] * f, "volume": r["volume"]}))
        return out

    def true_closes(self, symbol: str) -> dict[dt.date, float]:
        coin = next(c for c in self.coins if c.symbol == symbol)
        return {d: r["close"] for d, r in self.candles(coin) if d < self.today}

    def price(self, coin: Coin) -> float:
        return self.rows[coin.id][self.today]["close"]

    # ------------------------------------------------------------------ wiring
    def session(self) -> FakeSession:
        s = FakeSession()
        by_sym: dict[str, Coin] = {}
        for c in self.coins:
            by_sym.setdefault(c.symbol, c)  # the first coin with a ticker owns it on the exchanges

        def ms(d: dt.date) -> int:
            return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)

        def num(x: float) -> str:
            return f"{x:.10g}"

        def guard(name, make, candles=False):
            def f(call):
                self.log.append(f"{name} {call['url'].split('?')[0]} {call.get('params') or ''}")
                if name in self.down or (candles and name in self.down_candles):
                    return FakeResponse(503, b"down")
                return make(call)
            return f

        def listed(venue):
            return [c for c in self.coins if venue in c.listed and by_sym.get(c.symbol) is c]

        # ---- Binance
        s.get(r"data-api\.binance\.vision/api/v3/ticker/24hr", guard("binance_vision", lambda c: ok(
            [{"symbol": f"{k.symbol}USDT", "lastPrice": num(self.price(k) * k.factor.get("binance_vision", 1.0)), "quoteVolume": num(k.volume_24h)} for k in listed("binance_vision")]
            + [{"symbol": "ETHBTC", "lastPrice": "0.04", "quoteVolume": "1"}, {"symbol": "BTCEUR", "lastPrice": "60000", "quoteVolume": "1"}])))

        def klines(c):
            p = c["params"]
            coin = next((k for k in listed("binance_vision") if f"{k.symbol}USDT" == p["symbol"]), None)
            if coin is None or p.get("interval") != "1d":
                return FakeResponse(400, b'{"code":-1121,"msg":"Invalid symbol."}')
            rows = self.candles(coin, "binance_vision")
            if "endTime" in p:
                rows = [r for r in rows if ms(r[0]) <= int(p["endTime"])]
            rows = rows[-int(p["limit"]):]
            return ok([[ms(d), num(r["open"]), num(r["high"]), num(r["low"]), num(r["close"]), num(r["volume"]), ms(d) + 86399999, "0", 0, "0", "0", "0"] for d, r in rows])

        s.get(r"data-api\.binance\.vision/api/v3/klines", guard("binance_vision", klines, candles=True))

        # ---- OKX
        s.get(r"www\.okx\.com/api/v5/market/tickers", guard("okx", lambda c: ok({"code": "0", "msg": "", "data": [
            {"instId": f"{k.symbol}-USDT", "last": num(self.price(k) * k.factor.get("okx", 1.0)), "volCcy24h": num(k.volume_24h)} for k in listed("okx")] + [{"instId": "BTC-EUR", "last": "1", "volCcy24h": "1"}]})))

        def okx_candles(c):
            p = c["params"]
            coin = next((k for k in listed("okx") if f"{k.symbol}-USDT" == p["instId"]), None)
            if p.get("bar") != "1Dutc":
                return ok({"code": "51000", "msg": "Parameter bar error", "data": []})
            if coin is None:
                return ok({"code": "51001", "msg": "Instrument ID doesn't exist", "data": []})
            rows = self.candles(coin, "okx")
            if "after" in p:
                rows = [r for r in rows if ms(r[0]) < int(p["after"])]
            rows = rows[-int(p.get("limit", 100)):][::-1]
            return ok({"code": "0", "msg": "", "data": [[str(ms(d)), num(r["open"]), num(r["high"]), num(r["low"]), num(r["close"]), num(r["volume"]), "0", "0", "1" if d < self.today else "0"] for d, r in rows]})

        s.get(r"www\.okx\.com/api/v5/market/history-candles", guard("okx", okx_candles, candles=True))

        # ---- Coinbase
        s.get(r"api\.exchange\.coinbase\.com/products$", guard("coinbase", lambda c: ok(
            [{"id": f"{k.symbol}-USD", "base_currency": k.symbol, "quote_currency": "USD", "status": "online", "trading_disabled": False} for k in listed("coinbase")]
            + [{"id": "BTC-EUR", "base_currency": "BTC", "quote_currency": "EUR", "status": "online", "trading_disabled": False},
               {"id": "OLD-USD", "base_currency": "OLD", "quote_currency": "USD", "status": "delisted", "trading_disabled": True}])))

        def cb_candles(c):
            pair = c["url"].split("/products/")[1].split("/")[0]
            coin = next((k for k in listed("coinbase") if f"{k.symbol}-USD" == pair), None)
            p = c["params"]
            if coin is None or int(p.get("granularity", 0)) != 86400:
                return FakeResponse(404, b'{"message":"NotFound"}')
            t0 = dt.datetime.fromisoformat(p["start"].replace("Z", "+00:00")).timestamp()
            t1 = dt.datetime.fromisoformat(p["end"].replace("Z", "+00:00")).timestamp()
            rows = [(d, r) for d, r in self.candles(coin, "coinbase") if t0 <= ms(d) / 1000 <= t1][::-1][:300]
            return ok([[ms(d) // 1000, r["low"], r["high"], r["open"], r["close"], r["volume"]] for d, r in rows])

        s.get(r"api\.exchange\.coinbase\.com/products/[A-Z0-9]+-USD/candles", guard("coinbase", cb_candles, candles=True))

        # ---- Kraken
        def kname(sym):
            return {"BTC": "XBT"}.get(sym, sym)

        s.get(r"api\.kraken\.com/0/public/AssetPairs", guard("kraken", lambda c: ok({"error": [], "result": {
            f"X{kname(k.symbol)}ZUSD": {"altname": f"{kname(k.symbol)}USD", "wsname": f"{kname(k.symbol)}/USD", "base": kname(k.symbol), "quote": "ZUSD", "status": "online"} for k in listed("kraken")}})))

        def kraken_ohlc(c):
            p = c["params"]
            coin = next((k for k in listed("kraken") if f"{kname(k.symbol)}USD" == p["pair"]), None)
            if coin is None or int(p.get("interval", 0)) != 1440:
                return ok({"error": ["EQuery:Unknown asset pair"]})
            since = int(p.get("since", 0))
            rows = [(d, r) for d, r in self.candles(coin, "kraken") if ms(d) // 1000 > since][-720:]
            return ok({"error": [], "result": {f"X{kname(coin.symbol)}ZUSD": [[ms(d) // 1000, num(r["open"]), num(r["high"]), num(r["low"]), num(r["close"]), num(r["close"]), num(r["volume"]), 10] for d, r in rows], "last": 1}})

        s.get(r"api\.kraken\.com/0/public/OHLC", guard("kraken", kraken_ohlc, candles=True))

        # ---- KuCoin
        s.get(r"api\.kucoin\.com/api/v1/market/allTickers", guard("kucoin", lambda c: ok({"code": "200000", "data": {"time": 1, "ticker": [
            {"symbol": f"{k.symbol}-USDT", "last": num(self.price(k) * k.factor.get("kucoin", 1.0)), "volValue": num(k.volume_24h)} for k in listed("kucoin")]}})))

        def kucoin_candles(c):
            p = c["params"]
            coin = next((k for k in listed("kucoin") if f"{k.symbol}-USDT" == p["symbol"]), None)
            if coin is None or p.get("type") != "1day":
                return ok({"code": "400100", "msg": "symbol not exists"})
            t0, t1 = int(p["startAt"]), int(p["endAt"])
            rows = [(d, r) for d, r in self.candles(coin, "kucoin") if t0 <= ms(d) // 1000 <= t1][::-1][:1500]
            return ok({"code": "200000", "data": [[str(ms(d) // 1000), num(r["open"]), num(r["close"]), num(r["high"]), num(r["low"]), num(r["volume"]), "0"] for d, r in rows]})

        s.get(r"api\.kucoin\.com/api/v1/market/candles", guard("kucoin", kucoin_candles, candles=True))

        # ---- CoinGecko
        def cg_guard(make):
            def f(call):
                self.log.append(f"coingecko {call['url'].split('?')[0]} {call.get('params') or ''}")
                if "coingecko" in self.down:
                    return FakeResponse(429, b"", headers={"Retry-After": "900"})
                return make(call)
            return f

        def markets(c):
            p = c["params"]
            ranked = sorted(self.coins, key=lambda k: -k.mcap)
            per, page = int(p["per_page"]), int(p["page"])
            out = []
            for i, k in enumerate(ranked):
                px = self.price(k)
                prev = self.rows[k.id].get(self.today - dt.timedelta(days=1), {"close": px})["close"]
                out.append({"id": k.id, "symbol": k.symbol.lower(), "name": k.name, "current_price": px, "market_cap": k.mcap, "market_cap_rank": i + 1,
                            "total_volume": k.volume_24h, "price_change_percentage_24h": (px / prev - 1) * 100, "circulating_supply": k.mcap / px, "max_supply": None,
                            "ath": px * k.ath_mult, "ath_date": "2024-03-14T07:10:36.635Z"})
            return ok(out[(page - 1) * per: page * per])

        s.get(r"api\.coingecko\.com/api/v3/coins/markets", cg_guard(markets))
        s.get(r"api\.coingecko\.com/api/v3/global", cg_guard(lambda c: ok({"data": {"total_market_cap": {"usd": 3.4e12}, "market_cap_percentage": {"btc": 56.2, "eth": 12.9}, "market_cap_change_percentage_24h_usd": -1.3}})))

        def chart(c):
            cid = c["url"].split("/coins/")[1].split("/")[0]
            coin = next((k for k in self.coins if k.id == cid), None)
            if coin is None:
                return FakeResponse(404, b'{"error":"coin not found"}')
            n = int(c["params"]["days"])
            rows = self.candles(coin)[-(n + 1):]
            prices = [[ms(d), r["close"]] for d, r in rows]
            vols = [[ms(d), r["volume"] * r["close"]] for d, r in rows]
            return ok({"prices": prices, "market_caps": [], "total_volumes": vols})

        s.get(r"api\.coingecko\.com/api/v3/coins/[^/]+/market_chart", cg_guard(chart))

        # ---- fear and greed
        def fng(c):
            if "fng" in self.down:
                return FakeResponse(500, b"oops")
            return ok({"name": "Fear and Greed Index", "data": [{"value": "34", "value_classification": "Fear", "timestamp": "1760000000"}]})

        s.get(r"api\.alternative\.me/fng", fng)
        return s
