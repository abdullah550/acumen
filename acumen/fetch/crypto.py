"""Crypto: the coin universe and market caps from CoinGecko, daily candles from public exchange APIs, global statistics.

Candles
-------
Each exchange named in ``data.crypto.sources`` is asked once per run for its list of USDT/USD markets, so a coin is only
requested where it exists. The first source that returns enough history wins. The source that served a coin is remembered
and asked first next time, so a coin's series is not stitched together from several venues; when the source does change,
the new bars are only accepted if they agree with what is stored (:func:`acumen.fetch.common.splice`). Coins no
exchange lists fall back to CoinGecko's daily closes (close only, flagged ``approx_hl``).

A UTC day that is still in progress is never stored, so every bar is final.

Everything here is an unofficial or free tier public API. Each parser checks the shape of what it gets and raises
:class:`~acumen.fetch.http.ParseError` instead of guessing; one coin or one exchange failing never stops the run.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from ..config import Config
from ..model import Instrument, MarketData, make_id, normalise_bars
from ..store import HistoryStore
from .common import bars_frame, epoch_to_date, epoch_to_date_ms, iso, positive, splice, status, to_float, utc_now
from .http import Client, FetchError, ParseError

STATE = "crypto_state"
COLS = ["open", "high", "low", "close", "volume"]
SYMBOL_OK = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,19}$")  # becomes part of a file name in the app's data folder
DEFAULT_SOURCES = ["binance_vision", "okx", "coinbase", "kraken", "kucoin", "coingecko"]

# Pegged to a currency or a commodity: no price discovery of their own, nothing to forecast.
PEGGED = {
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "TUSD", "USDD", "PYUSD", "BUSD", "USDP", "GUSD", "LUSD", "FRAX", "USDS", "USD1", "EURC", "EURS", "EURT",
    "AEUR", "CRVUSD", "GHO", "USDB", "SUSD", "USDY", "BUIDL", "USTB", "USDO", "USDG", "RLUSD", "USDX", "XAUT", "PAXG", "USDT0",
}
# Wrapped, staked and bridged forms of another coin: the same exposure twice.
DERIVATIVES = {
    "WBTC", "WETH", "STETH", "WSTETH", "WEETH", "CBBTC", "CBETH", "RETH", "WBETH", "BTCB", "TBTC", "SOLVBTC", "LBTC", "EZETH", "RSETH", "METH", "SFRXETH",
    "SWETH", "OSETH", "ETHX", "WBNB", "WAVAX", "WMATIC", "JITOSOL", "MSOL", "BNSOL", "JUPSOL", "WTRX", "WSOL", "BSC-USD",
}
DERIVATIVE_NAME = re.compile(r"^(wrapped|bridged|staked|lido staked|binance-peg|coinbase wrapped|rocket pool|renzo restaked|kelp dao restaked|mantle staked)\b", re.I)


@dataclass
class Listing:
    """A market an exchange offers for one coin: what the exchange calls it, and its last price and 24-hour value."""

    pair: str
    price: float | None = None
    quote_volume: float | None = None


# ---------------------------------------------------------------------------------------------- classification
def is_derivative(symbol: str, name: str = "") -> bool:
    return symbol.upper() in DERIVATIVES or bool(DERIVATIVE_NAME.match(name or ""))


def looks_stable(symbol: str, price: float | None, chg24: float | None, ath: float | None) -> bool:
    """A listed stablecoin, or a coin that sits on one dollar and has never traded far above it."""
    if symbol.upper() in PEGGED:
        return True
    if price is None or not 0.97 <= price <= 1.03:
        return False
    return (ath is not None and ath <= 1.6) and (chg24 is None or abs(chg24) < 1.0)


def pick_universe(rows: list[dict[str, Any]], *, top_n: int, min_volume: float, exclude_stables: bool, require_mcap: bool = True) -> tuple[list[dict[str, Any]], Counter]:
    """The coins to follow, in market-cap order, and a count of why others were left out."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    skipped: Counter = Counter()
    for r in rows[: max(top_n, 0)]:
        sym = r["symbol"]
        if not SYMBOL_OK.match(sym):
            skipped["unusual ticker"] += 1
        elif sym in seen:
            skipped["ticker already used by a larger coin"] += 1
        elif is_derivative(sym, r.get("name", "")):
            skipped["wrapped or staked copy"] += 1
        elif require_mcap and not (r.get("mcap") or 0) > 0:
            skipped["no market cap"] += 1
        elif (r.get("volume_24h") or 0) < min_volume:
            skipped["thin trading"] += 1
        else:
            stable = looks_stable(sym, r.get("price"), r.get("chg_24h"), r.get("ath"))
            if stable and exclude_stables:
                skipped["stablecoin or pegged token"] += 1
                continue
            seen.add(sym)
            out.append({**r, "stable": stable})
    return out, skipped


# ---------------------------------------------------------------------------------------------- parsers
def parse_markets(payload: Any) -> list[dict[str, Any]]:
    """CoinGecko ``/coins/markets`` rows, in the order given (market-cap rank)."""
    if not isinstance(payload, list):
        raise ParseError("unexpected CoinGecko markets answer")
    out = []
    for r in payload:
        if not isinstance(r, dict) or not r.get("id") or not r.get("symbol"):
            continue
        ath_date = str(r.get("ath_date") or "")[:10] or None
        out.append({
            "id": str(r["id"]), "symbol": str(r["symbol"]).strip().upper(), "name": str(r.get("name") or r["symbol"]).strip(),
            "price": positive(r.get("current_price")), "mcap": positive(r.get("market_cap")), "rank": int(r["market_cap_rank"]) if to_float(r.get("market_cap_rank")) else None,
            "volume_24h": to_float(r.get("total_volume")), "chg_24h": to_float(r.get("price_change_percentage_24h")),
            "circ_supply": positive(r.get("circulating_supply")), "max_supply": positive(r.get("max_supply")), "ath": positive(r.get("ath")), "ath_date": ath_date,
        })
    if not out:
        raise ParseError("CoinGecko returned no coins")
    return out


def parse_global(payload: Any) -> dict[str, float | None]:
    try:
        d = payload["data"]
        return {
            "total_mcap": to_float((d.get("total_market_cap") or {}).get("usd")),
            "btc_dominance": to_float((d.get("market_cap_percentage") or {}).get("btc")),
            "eth_dominance": to_float((d.get("market_cap_percentage") or {}).get("eth")),
            "mcap_chg_24h": to_float(d.get("market_cap_change_percentage_24h_usd")),
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise ParseError("unexpected CoinGecko global answer") from exc


def parse_fear_greed(payload: Any) -> float | None:
    try:
        v = to_float(payload["data"][0]["value"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ParseError("unexpected fear and greed answer") from exc
    return v if v is not None and 0 <= v <= 100 else None


def parse_cg_chart(payload: Any) -> pd.DataFrame:
    """CoinGecko ``market_chart`` (daily closes and USD volumes) as close-only bars flagged ``approx_hl``."""
    try:
        prices = payload["prices"]
        vols = payload.get("total_volumes") or []
    except (KeyError, TypeError, AttributeError) as exc:
        raise ParseError("unexpected CoinGecko chart answer") from exc
    vol_by_day = {epoch_to_date_ms(v[0]): to_float(v[1]) for v in vols if isinstance(v, (list, tuple)) and len(v) >= 2 and to_float(v[0]) is not None}
    rows, prev = [], None
    for p in prices:
        if not isinstance(p, (list, tuple)) or len(p) < 2 or to_float(p[0]) is None:
            continue
        close = positive(p[1])
        if close is None:
            continue
        day = epoch_to_date_ms(p[0])
        opn = prev if prev is not None else close
        vol_usd = vol_by_day.get(day)
        rows.append((day, opn, max(opn, close), min(opn, close), close, (vol_usd / close) if vol_usd else None))
        prev = close
    df = bars_frame(rows, COLS)
    if len(df):
        df["approx_hl"] = True
    return df


def _combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return bars_frame([], COLS)
    return normalise_bars(pd.concat(frames).sort_index())


# --- Binance (the public market-data host, reachable from places where api.binance.com is blocked)
def parse_binance_tickers(payload: Any) -> dict[str, Listing]:
    if not isinstance(payload, list):
        raise ParseError("unexpected Binance ticker answer")
    out: dict[str, Listing] = {}
    for t in payload:
        sym = str(t.get("symbol", "")) if isinstance(t, dict) else ""
        price = positive(t.get("lastPrice")) if isinstance(t, dict) else None
        if sym.endswith("USDT") and len(sym) > 4 and price is not None:
            out[sym[:-4]] = Listing(sym, price, to_float(t.get("quoteVolume")))
    if not out:
        raise ParseError("Binance listed no USDT markets")
    return out


def parse_binance_klines(payload: Any) -> pd.DataFrame:
    if not isinstance(payload, list):
        raise ParseError("unexpected Binance candle answer")
    rows = []
    for k in payload:
        if isinstance(k, (list, tuple)) and len(k) >= 6 and to_float(k[0]) is not None:
            rows.append((epoch_to_date_ms(to_float(k[0])), k[1], k[2], k[3], k[4], k[5]))
    return bars_frame(rows, COLS)


# --- OKX
def _okx_data(payload: Any) -> list:
    if not isinstance(payload, dict) or "code" not in payload:
        raise ParseError("unexpected OKX answer")
    if str(payload.get("code")) != "0":
        raise ParseError(f"OKX: {payload.get('msg') or payload.get('code')}")
    if "data" not in payload:
        raise ParseError("unexpected OKX answer")
    return payload["data"] or []


def parse_okx_tickers(payload: Any) -> dict[str, Listing]:
    out: dict[str, Listing] = {}
    for t in _okx_data(payload):
        inst = str(t.get("instId", "")) if isinstance(t, dict) else ""
        base, _, quote = inst.partition("-")
        price = positive(t.get("last"))
        if quote == "USDT" and base and price is not None:
            out[base] = Listing(inst, price, to_float(t.get("volCcy24h")))
    if not out:
        raise ParseError("OKX listed no USDT markets")
    return out


def parse_okx_candles(payload: Any) -> tuple[pd.DataFrame, float | None]:
    """Completed daily candles and the timestamp (ms) of the oldest one returned, for paging backwards."""
    rows, oldest = [], None
    for c in _okx_data(payload):
        if not isinstance(c, (list, tuple)) or len(c) < 6 or to_float(c[0]) is None:
            continue
        ts = float(to_float(c[0]))
        oldest = ts if oldest is None else min(oldest, ts)
        if len(c) > 8 and str(c[8]) == "0":
            continue  # the candle is still forming
        rows.append((epoch_to_date_ms(ts), c[1], c[2], c[3], c[4], c[5]))
    return bars_frame(rows, COLS), oldest


# --- Coinbase
def parse_coinbase_products(payload: Any) -> dict[str, Listing]:
    if not isinstance(payload, list):
        raise ParseError("unexpected Coinbase products answer")
    rank = {"USD": 0, "USDT": 1, "USDC": 2}
    best: dict[str, tuple[int, str]] = {}
    for p in payload:
        if not isinstance(p, dict) or p.get("status") != "online" or p.get("trading_disabled"):
            continue
        base, quote = str(p.get("base_currency") or ""), str(p.get("quote_currency") or "")
        if base and quote in rank and (base not in best or rank[quote] < best[base][0]):
            best[base] = (rank[quote], str(p.get("id") or f"{base}-{quote}"))
    if not best:
        raise ParseError("Coinbase listed no markets")
    return {b: Listing(pair) for b, (_, pair) in best.items()}


def parse_coinbase_candles(payload: Any) -> pd.DataFrame:
    """``[time, low, high, open, close, volume]`` rows, newest first."""
    if not isinstance(payload, list):
        raise ParseError(f"Coinbase: {payload.get('message') if isinstance(payload, dict) else 'unexpected candle answer'}")
    rows = []
    for c in payload:
        if isinstance(c, (list, tuple)) and len(c) >= 6 and to_float(c[0]) is not None:
            rows.append((epoch_to_date(to_float(c[0])), c[3], c[2], c[1], c[4], c[5]))
    return bars_frame(rows, COLS)


# --- Kraken
KRAKEN_NAMES = {"XBT": "BTC", "XDG": "DOGE"}


def _kraken_result(payload: Any, what: str) -> dict:
    if not isinstance(payload, dict):
        raise ParseError(f"unexpected Kraken {what} answer")
    if payload.get("error"):
        raise ParseError(f"Kraken: {payload['error'][0]}")
    if "result" not in payload:
        raise ParseError(f"unexpected Kraken {what} answer")
    return payload["result"] or {}


def parse_kraken_pairs(payload: Any) -> dict[str, Listing]:
    result = _kraken_result(payload, "pairs")
    out: dict[str, Listing] = {}
    quoted_usd: set[str] = set()
    for key, p in result.items():
        if not isinstance(p, dict) or p.get("status") not in (None, "online"):
            continue
        base, _, quote = str(p.get("wsname") or "").partition("/")
        if quote not in ("USD", "USDT") or not base:
            continue
        base = KRAKEN_NAMES.get(base, base)
        if base in quoted_usd and quote != "USD":
            continue
        out[base] = Listing(str(p.get("altname") or key))
        if quote == "USD":
            quoted_usd.add(base)
    if not out:
        raise ParseError("Kraken listed no USD markets")
    return out


def parse_kraken_ohlc(payload: Any) -> pd.DataFrame:
    res = _kraken_result(payload, "candle")
    key = next((k for k in res if k != "last"), None)
    rows = []
    for r in (res.get(key) or []) if key else []:
        if isinstance(r, (list, tuple)) and len(r) >= 7 and to_float(r[0]) is not None:
            rows.append((epoch_to_date(to_float(r[0])), r[1], r[2], r[3], r[4], r[6]))
    return bars_frame(rows, COLS)


# --- KuCoin
def _kucoin_data(payload: Any) -> Any:
    if not isinstance(payload, dict) or "code" not in payload:
        raise ParseError("unexpected KuCoin answer")
    if str(payload.get("code")) != "200000":
        raise ParseError(f"KuCoin: {payload.get('msg') or payload.get('code')}")
    if "data" not in payload:
        raise ParseError("unexpected KuCoin answer")
    return payload["data"]


def parse_kucoin_tickers(payload: Any) -> dict[str, Listing]:
    data = _kucoin_data(payload)
    out: dict[str, Listing] = {}
    for t in (data or {}).get("ticker", []) if isinstance(data, dict) else []:
        base, _, quote = str(t.get("symbol", "")).partition("-") if isinstance(t, dict) else ("", "", "")
        price = positive(t.get("last")) if isinstance(t, dict) else None
        if quote == "USDT" and base and price is not None:
            out[base] = Listing(f"{base}-USDT", price, to_float(t.get("volValue")))
    if not out:
        raise ParseError("KuCoin listed no USDT markets")
    return out


def parse_kucoin_candles(payload: Any) -> pd.DataFrame:
    """``[time, open, close, high, low, volume, turnover]`` rows (note the order), newest first."""
    data = _kucoin_data(payload)
    rows = []
    for c in data or []:
        if isinstance(c, (list, tuple)) and len(c) >= 6 and to_float(c[0]) is not None:
            rows.append((epoch_to_date(to_float(c[0])), c[1], c[3], c[4], c[2], c[5]))
    return bars_frame(rows, COLS)


# ---------------------------------------------------------------------------------------------- exchange adapters
class Exchange:
    """One venue's public API: which coins it lists, and a coin's daily candles."""

    name = ""
    label = ""
    host = ""
    interval = 0.25

    def __init__(self, http: Client):
        self.http = http
        http.set_interval(self.host, self.interval)

    def listing(self) -> dict[str, Listing]:
        raise NotImplementedError

    def daily(self, pair: str, days: int, now: dt.datetime) -> pd.DataFrame:
        raise NotImplementedError

    def url(self, path: str) -> str:
        return f"https://{self.host}{path}"


class BinanceVision(Exchange):
    name, label, host, interval = "binance_vision", "Binance", "data-api.binance.vision", 0.12

    def listing(self) -> dict[str, Listing]:
        return parse_binance_tickers(self.http.get(self.url("/api/v3/ticker/24hr"), params={"type": "MINI"}, ttl=900).json())

    def daily(self, pair: str, days: int, now: dt.datetime) -> pd.DataFrame:
        frames, end, need = [], None, max(days, 2)
        for _ in range(3):
            limit = min(1000, need)
            params: dict[str, Any] = {"symbol": pair, "interval": "1d", "limit": limit}
            if end is not None:
                params["endTime"] = end
            payload = self.http.get(self.url("/api/v3/klines"), params=params).json()
            df = parse_binance_klines(payload)
            if len(df) == 0:
                break
            frames.append(df)
            need -= len(df)
            if need <= 0 or len(payload) < limit:
                break
            end = int(float(payload[0][0])) - 1
        return _combine(frames)


class Okx(Exchange):
    name, label, host, interval = "okx", "OKX", "www.okx.com", 0.25

    def listing(self) -> dict[str, Listing]:
        return parse_okx_tickers(self.http.get(self.url("/api/v5/market/tickers"), params={"instType": "SPOT"}, ttl=900).json())

    def daily(self, pair: str, days: int, now: dt.datetime) -> pd.DataFrame:
        frames, after, need = [], None, max(days, 2)
        for _ in range(max(1, math.ceil(need / 100)) + 1):
            params: dict[str, Any] = {"instId": pair, "bar": "1Dutc", "limit": 100}  # "1D" would open at 00:00 Hong Kong time
            if after is not None:
                params["after"] = int(after)
            df, oldest = parse_okx_candles(self.http.get(self.url("/api/v5/market/history-candles"), params=params).json())
            if oldest is None:
                break
            frames.append(df)
            need -= len(df)
            if need <= 0:
                break
            after = oldest
        return _combine(frames)


class Coinbase(Exchange):
    name, label, host, interval = "coinbase", "Coinbase", "api.exchange.coinbase.com", 0.4

    def listing(self) -> dict[str, Listing]:
        return parse_coinbase_products(self.http.get(self.url("/products"), ttl=900).json())

    def daily(self, pair: str, days: int, now: dt.datetime) -> pd.DataFrame:
        frames, end, need = [], now, max(days, 2)
        for _ in range(5):
            span = min(need, 300)
            start = end - dt.timedelta(days=span)
            params = {"granularity": 86400, "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
            df = parse_coinbase_candles(self.http.get(self.url(f"/products/{pair}/candles"), params=params).json())
            if len(df) == 0:
                break
            frames.append(df)
            need -= span
            if need <= 0:
                break
            end = start
        return _combine(frames)


class Kraken(Exchange):
    name, label, host, interval = "kraken", "Kraken", "api.kraken.com", 1.1

    def listing(self) -> dict[str, Listing]:
        return parse_kraken_pairs(self.http.get(self.url("/0/public/AssetPairs"), ttl=900).json())

    def daily(self, pair: str, days: int, now: dt.datetime) -> pd.DataFrame:
        since = int((now - dt.timedelta(days=min(max(days, 2), 700))).timestamp())  # the route returns at most 720 candles
        return parse_kraken_ohlc(self.http.get(self.url("/0/public/OHLC"), params={"pair": pair, "interval": 1440, "since": since}).json())


class Kucoin(Exchange):
    name, label, host, interval = "kucoin", "KuCoin", "api.kucoin.com", 0.3

    def listing(self) -> dict[str, Listing]:
        return parse_kucoin_tickers(self.http.get(self.url("/api/v1/market/allTickers"), ttl=900).json())

    def daily(self, pair: str, days: int, now: dt.datetime) -> pd.DataFrame:
        end = int(now.timestamp())
        start = end - min(max(days, 2), 1500) * 86400
        params = {"type": "1day", "symbol": pair, "startAt": start, "endAt": end}
        return parse_kucoin_candles(self.http.get(self.url("/api/v1/market/candles"), params=params).json())


EXCHANGES: dict[str, type[Exchange]] = {c.name: c for c in (BinanceVision, Okx, Coinbase, Kraken, Kucoin)}


class CoinGecko:
    """Universe, market caps, global figures, and a close-only history fallback."""

    host = "api.coingecko.com"

    def __init__(self, http: Client, api_key: str | None = None):
        self.http = http
        self.headers = {"x-cg-demo-api-key": api_key} if api_key else {}
        http.set_interval(self.host, 1.2 if api_key else 2.6)  # the free tier allows a few dozen calls a minute

    def _get(self, path: str, **kw: Any):
        return self.http.get(f"https://{self.host}{path}", headers=self.headers or None, **kw)

    def markets(self, top_n: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, math.ceil(max(top_n, 1) / 250) + 1):
            params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page, "sparkline": "false", "price_change_percentage": "24h"}
            got = parse_markets(self._get("/api/v3/coins/markets", params=params, ttl=600).json())
            rows += got
            if len(got) < 250:
                break
        return rows

    def global_stats(self) -> dict[str, float | None]:
        return parse_global(self._get("/api/v3/global", ttl=600).json())

    def chart(self, cg_id: str, days: int) -> pd.DataFrame:
        params = {"vs_currency": "usd", "days": min(max(days, 2), 365), "interval": "daily"}  # the free tier serves one year
        return parse_cg_chart(self._get(f"/api/v3/coins/{cg_id}/market_chart", params=params).json())


def fear_greed(http: Client) -> float | None:
    http.set_interval("api.alternative.me", 1.0)
    return parse_fear_greed(http.get("https://api.alternative.me/fng/", params={"limit": 1}, ttl=600).json())


# ---------------------------------------------------------------------------------------------- orchestration
def _plausible(df: pd.DataFrame, live_price: float | None) -> bool:
    """A different coin sharing a ticker shows up as a price that is nowhere near the market's."""
    if live_price is None or len(df) == 0:
        return True
    last = float(df["close"].iloc[-1])
    return last > 0 and 0.4 <= last / live_price <= 2.5


def _fundamentals(r: dict[str, Any]) -> dict[str, Any]:
    return {"mcap": r.get("mcap"), "rank": r.get("rank"), "volume_24h": r.get("volume_24h"), "circ_supply": r.get("circ_supply"), "max_supply": r.get("max_supply"),
            "ath": r.get("ath"), "ath_date": r.get("ath_date"), "cg_id": r.get("id")}


def _quote(r: dict[str, Any], bars: pd.DataFrame, ts: str) -> dict[str, Any] | None:
    price = r.get("price")
    if not price:
        return None
    chg = r.get("chg_24h")
    prev = price / (1 + chg / 100.0) if chg is not None and chg > -99 else None
    vol = (r.get("volume_24h") or 0.0) / price
    return {"price": price, "prev_close": prev, "chg_pct": chg, "volume": vol, "ts": ts}


def fetch_crypto(
    cfg: Config,
    store: HistoryStore,
    http: Client,
    *,
    log: Callable[[str], None] = print,
    now: dt.datetime | None = None,
    exchanges: list[Exchange] | None = None,
) -> MarketData:
    t_run = time.time()
    now = (now or utc_now()).astimezone(dt.timezone.utc)
    ccfg = cfg.get_path("data.crypto", {}) or {}
    ucfg = cfg.get_path("universe.crypto", {}) or {}
    top_n = int(ucfg.get("top_n", 500))
    min_volume = float(ucfg.get("min_24h_volume_usd", 2_000_000))
    min_bars = int(ucfg.get("min_bars", 120))
    exclude_stables = bool(ucfg.get("exclude_stables", True))
    history_days = int(ccfg.get("history_days", 1000))
    keep = int(cfg.get_path("backtest.max_history_bars", 1500))
    max_seconds = float(ccfg.get("max_seconds", 1500))
    cg_fallback_max = int(ccfg.get("coingecko_fallback_max", 40))
    today = now.date()
    today_ts = pd.Timestamp(today)
    complete_to = today_ts - pd.Timedelta(days=1)  # the newest UTC day that is over

    md = MarketData("crypto")
    state = store.load_json(STATE, {}) or {}
    bars = store.load_bars("crypto")
    statuses = []
    notes: list[str] = []

    # ---- universe and market caps
    key = os.environ.get(str(ccfg.get("coingecko_key_env", "COINGECKO_API_KEY"))) or None
    cg = CoinGecko(http, key)
    rows: list[dict[str, Any]] = []
    cg_ok = False
    t0 = time.time()
    try:
        rows = cg.markets(top_n)
        cg_ok = True
        statuses.append(status("coingecko", True, f"{len(rows)} coins", items=len(rows), started=t0, now=now))
        state["universe"] = {"saved_at": iso(now), "rows": rows}
    except FetchError as exc:
        statuses.append(status("coingecko", False, str(exc), started=t0, now=now))
        saved = state.get("universe") or {}
        rows = list(saved.get("rows") or [])
        if rows:
            notes.append(f"coin list from the copy saved on {str(saved.get('saved_at', ''))[:10]}")
        log(f"  crypto: coingecko unavailable: {exc}")

    # ---- exchanges and what each lists
    chain = exchanges if exchanges is not None else [EXCHANGES[n](http) for n in (ccfg.get("sources") or DEFAULT_SOURCES) if n in EXCHANGES]
    use_cg_fallback = "coingecko" in (ccfg.get("sources") or DEFAULT_SOURCES)
    listings: dict[str, dict[str, Listing]] = {}
    for ex in chain:
        t0 = time.time()
        try:
            listings[ex.name] = ex.listing()
        except FetchError as exc:
            listings[ex.name] = {}
            statuses.append(status(ex.name, False, f"market list unavailable: {exc}", started=t0, now=now))
            log(f"  crypto: {ex.name} unavailable: {exc}")
    from_exchange = False
    if not rows:
        # Neither CoinGecko nor a saved list: follow the busiest markets of the first exchange that answered.
        first = next((ex for ex in chain if listings.get(ex.name)), None)
        if first is not None:
            ordered = sorted(listings[first.name].items(), key=lambda kv: -(kv[1].quote_volume or 0.0))[:top_n]
            rows = [{"id": None, "symbol": s_, "name": s_, "price": l.price, "mcap": None, "rank": None, "volume_24h": l.quote_volume, "chg_24h": None,
                     "circ_supply": None, "max_supply": None, "ath": None, "ath_date": None} for s_, l in ordered]
            from_exchange = True
            notes.append(f"coin list taken from {first.label}'s busiest markets: market caps unavailable")
    coins, skipped = pick_universe(rows, top_n=top_n, min_volume=min_volume, exclude_stables=exclude_stables, require_mcap=not from_exchange)
    if skipped:
        log("  crypto: left out " + ", ".join(f"{n} {why}" for why, n in skipped.most_common()))
    live = cg_ok

    # ---- candles
    served: Counter = Counter()
    failed: Counter = Counter()
    stale = fresh = missing = cg_used = 0
    src_state: dict[str, str] = dict(state.get("src") or {})
    timed_out = False
    cg_sources = {"coingecko"}
    for r in coins:
        sym = r["symbol"]
        iid = make_id("crypto", sym)
        have = bars.get(iid)
        last_have = have.index[-1] if have is not None and len(have) else None
        if last_have is not None and last_have >= complete_to and len(have) >= min_bars:
            fresh += 1
            continue
        if time.time() - t_run >= max_seconds:
            timed_out = True
            stale += 1
            continue
        short = have is None or len(have) < min_bars
        need = history_days + 1 if short else max((today_ts - last_have).days + 3, 5)  # +1: today's forming candle comes back too and is dropped
        preferred = src_state.get(sym)
        order = sorted((ex for ex in chain if sym in listings.get(ex.name, {})), key=lambda ex: ex.name != preferred)
        got = False
        for ex in order:
            if http.is_open(ex.host):
                continue
            try:
                df = ex.daily(listings[ex.name][sym].pair, need, now)
            except FetchError:
                failed[ex.name] += 1
                continue
            df = df[df.index < today_ts]
            if short:
                df = df.iloc[-history_days:]
            if len(df) == 0 or not _plausible(df, r.get("price")):
                failed[ex.name] += 1
                continue
            merged, why = splice(have, df)
            if why and why != "nothing to add":
                failed[ex.name] += 1
                continue
            if merged is not None and len(merged):
                bars[iid] = merged
            src_state[sym] = ex.name
            served[ex.name] += 1
            got = True
            break
        if not got and use_cg_fallback and r.get("id") and cg_used < cg_fallback_max and not http.is_open(cg.host) and (have is None or short or last_have < complete_to):
            try:
                df = cg.chart(r["id"], need)
                df = df[df.index < today_ts]
                if short:
                    df = df.iloc[-history_days:]
                merged, why = splice(have, df)
                if len(df) and not why:
                    bars[iid] = merged  # type: ignore[assignment]
                    src_state[sym] = "coingecko"
                    served["coingecko"] += 1
                    cg_used += 1
                    got = True
            except FetchError:
                failed["coingecko"] += 1
        if not got:
            if have is None or len(have) == 0:
                missing += 1
            else:
                stale += 1
    for ex in chain:
        if served[ex.name] or failed[ex.name]:
            tripped = http.is_open(ex.host)  # switched off for the rest of the run after repeated failures
            ok = not tripped and (served[ex.name] > 0 or failed[ex.name] < 3)  # a venue used only as a fallback for a few coins is not an outage
            statuses.append(status(ex.name, ok, f"candles for {served[ex.name]} coin(s)" + (f", {failed[ex.name]} request(s) failed" if failed[ex.name] else ""), items=served[ex.name], now=now))
    if served["coingecko"] or failed["coingecko"]:
        statuses.append(status("coingecko_history", served["coingecko"] > 0, f"closes for {served['coingecko']} coin(s) no exchange lists", items=served["coingecko"], now=now))
    if timed_out:
        notes.append(f"time limit reached: {stale} coin(s) kept their stored history")
    if missing:
        notes.append(f"{missing} coin(s) had no candle source")

    # ---- instruments, quotes, fundamentals
    for r in coins:
        sym = r["symbol"]
        iid = make_id("crypto", sym)
        df = bars.get(iid)
        if df is None or len(df) == 0:
            continue
        md.instruments[iid] = Instrument(iid, "crypto", sym, r["name"], None, "crypto", True,
                                         {"exchange": next((ex.label for ex in chain if ex.name == src_state.get(sym)), "CoinGecko" if src_state.get(sym) == "coingecko" else None),
                                          "stable": bool(r.get("stable")), "cg_id": r.get("id")})
        md.bars[iid] = df
        if live:
            md.fundamentals[iid] = _fundamentals(r)
            q = _quote(r, df, iso(now))
            if q:
                md.quotes[iid] = q
        elif r.get("mcap") and r.get("id"):
            md.fundamentals[iid] = _fundamentals(r)  # from the saved copy: stale, and the market note says so
    if make_id("crypto", "BTC") in md.instruments:
        md.extras["benchmark"] = {"symbol": "BTC", "name": "Bitcoin"}

    # ---- global figures
    g: dict[str, Any] = dict(state.get("global") or {})
    if live:
        try:
            g.update(cg.global_stats())
            state["global"] = g
        except FetchError as exc:
            notes.append(f"global market figures: {exc}")
    try:
        fg = fear_greed(http)
        if fg is not None:
            g["fear_greed"] = fg
    except FetchError as exc:
        notes.append(f"fear and greed index: {exc}")
        g.pop("fear_greed", None)
    md.extras["global"] = {"total_mcap": g.get("total_mcap"), "btc_dominance": g.get("btc_dominance"), "eth_dominance": g.get("eth_dominance"),
                           "mcap_chg_24h": g.get("mcap_chg_24h"), "fear_greed": g.get("fear_greed")}

    # ---- progress and persistence
    lengths = sorted(len(md.bars[i]) for i in md.bars)
    median_len = lengths[len(lengths) // 2] if lengths else 0
    md.extras["history_pct"] = int(round(100 * min(1.0, median_len / float(min(history_days, 400))))) if lengths else 0
    md.extras["notes"] = notes
    md.status = statuses
    state["src"] = src_state
    store.save_json(STATE, state)
    if bars:
        keep_ids = [i for i, df in bars.items() if len(df) and (today_ts - df.index[-1]).days <= 45]
        store.save_bars("crypto", {i: bars[i] for i in keep_ids}, keep_days=keep)
    for n in notes:
        log(f"  crypto: {n}")
    log(f"  crypto: {len(md.instruments)} instruments ({fresh} already current), {sum(served.values())} updated, {stale} stale, {missing} without data, {round(time.time() - t_run)}s")
    return md
