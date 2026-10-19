"""The crypto fetcher against the simulated exchanges and CoinGecko in ``tests/fake_crypto.py``."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from acumen.config import load_config
from acumen.fetch import crypto as C
from acumen.fetch.http import Client, ParseError
from acumen.model import make_id
from acumen.store import HistoryStore
from tests.fake_crypto import Coin, CryptoWorld
from tests.fakenet import FakeClock, FakeSession

D8, D9 = dt.date(2026, 10, 8), dt.date(2026, 10, 9)


# ------------------------------------------------------------------------------------------------ helpers
def config(**crypto):
    cfg = load_config("/nonexistent.yml")
    cfg["data"]["crypto"].update({"history_days": 300, **crypto})
    cfg["universe"]["crypto"].update({"top_n": 50, "min_24h_volume_usd": 2_000_000, "min_bars": 100})
    return cfg


def new_client(session=None) -> Client:
    clock = FakeClock()
    return Client("TestAgent/1.0", session=session if session is not None else FakeSession(), sleep=clock.sleep, clock=clock, default_interval=0.0)


def go(world: CryptoWorld, store, *, cfg=None, session=None):
    sess = session or world.session()
    client = new_client(sess)
    md = C.fetch_crypto(cfg or config(), store, client, log=lambda m: None, now=world.now())
    return md, sess, client


def sources(md) -> dict[str, bool]:
    return {s.source: s.ok for s in md.status}


def closes(md, sym) -> pd.Series:
    return md.bars[make_id("crypto", sym)]["close"]


def truth(world, sym, n=None) -> pd.Series:
    t = world.true_closes(sym)
    s = pd.Series(list(t.values()), index=pd.to_datetime(list(t.keys())))
    return s.iloc[-n:] if n else s


def exchange_of(md, sym):
    return md.instruments[make_id("crypto", sym)].meta["exchange"]


# ------------------------------------------------------------------------------------------------ the whole pipeline
def test_first_run_chooses_coins_venues_and_stores_only_finished_days(tmp_path):
    w = CryptoWorld().at(D9)
    store = HistoryStore(tmp_path)
    md, sess, _ = go(w, store)

    assert sorted(i.symbol for i in md.instruments.values()) == ["AAA", "BBB", "BTC", "CCC", "ETH", "SOL", "YOUNG"]
    # left out: USDT (stablecoin), WBTC (wrapped copy), a second "ETH", THIN (too little trading), "BAD$" (not a usable ticker)
    assert {s: exchange_of(md, s) for s in ("BTC", "ETH", "SOL", "AAA", "BBB", "CCC", "YOUNG")} == {
        "BTC": "Binance", "ETH": "Binance", "SOL": "Binance", "AAA": "KuCoin", "BBB": "Kraken", "CCC": "CoinGecko", "YOUNG": "Binance"}

    btc = md.bars[make_id("crypto", "BTC")]
    assert len(btc) == 300 and btc.index[-1] == pd.Timestamp(D8)  # today's candle is still forming: not stored
    assert btc["close"].tolist() == pytest.approx(truth(w, "BTC", 300).tolist())
    assert (btc["high"] >= btc[["open", "close"]].max(axis=1)).all() and "approx_hl" not in btc or not btc["approx_hl"].any()
    young = md.bars[make_id("crypto", "YOUNG")]
    assert young["close"].tolist() == pytest.approx(truth(w, "YOUNG").tolist())  # a short history is all there is
    ccc = md.bars[make_id("crypto", "CCC")]
    assert bool(ccc["approx_hl"].all()) and ccc.index[-1] == pd.Timestamp(D8)  # close-only, flagged as such

    q = md.quotes[make_id("crypto", "BTC")]
    assert q["price"] == pytest.approx(62000.0) and q["prev_close"] == pytest.approx(62000.0 / (1 + q["chg_pct"] / 100))
    f = md.fundamentals[make_id("crypto", "BTC")]
    assert (f["rank"], f["mcap"], f["cg_id"]) == (1, 1.3e12, "bitcoin") and f["ath"] == pytest.approx(62000.0 * 3)
    assert f["ath_date"] == "2024-03-14"
    assert md.extras["global"] == {"total_mcap": 3.4e12, "btc_dominance": 56.2, "eth_dominance": 12.9, "mcap_chg_24h": -1.3, "fear_greed": 34.0}
    assert md.extras["benchmark"] == {"symbol": "BTC", "name": "Bitcoin"} and md.extras["history_pct"] == 100
    assert md.instruments[make_id("crypto", "BTC")].meta["stable"] is False

    st = store.load_json("crypto_state")
    assert st["src"] == {"BTC": "binance_vision", "ETH": "binance_vision", "SOL": "binance_vision", "AAA": "kucoin", "BBB": "kraken", "CCC": "coingecko", "YOUNG": "binance_vision"}
    assert sources(md) == {"coingecko": True, "binance_vision": True, "kraken": True, "kucoin": True, "coingecko_history": True}
    assert make_id("crypto", "BTC") in store.load_bars("crypto")


def test_the_next_day_asks_for_a_few_candles_not_the_history(tmp_path):
    w = CryptoWorld().at(D8)
    store = HistoryStore(tmp_path)
    md1, _, _ = go(w, store)
    assert md1.bars[make_id("crypto", "BTC")].index[-1] == pd.Timestamp(dt.date(2026, 10, 7))

    w.at(D9)
    md2, s2, _ = go(w, store)
    assert all(int(c["params"]["limit"]) <= 6 for c in s2.calls if "klines" in c["url"] and c["params"]["symbol"] != "YOUNGUSDT")
    btc = md2.bars[make_id("crypto", "BTC")]
    assert len(btc) == 301 and btc.index[-1] == pd.Timestamp(D8)
    assert btc["close"].tolist() == pytest.approx(truth(w, "BTC", 301).tolist())
    cols = ["open", "high", "low", "close", "volume"]
    pd.testing.assert_frame_equal(btc[cols].iloc[:-1], md1.bars[make_id("crypto", "BTC")][cols], check_freq=False, check_index_type=False)
    assert len(md2.bars[make_id("crypto", "YOUNG")]) == len(md1.bars[make_id("crypto", "YOUNG")]) + 1  # a short history is re-read in full and extended
    assert sorted(i.symbol for i in md2.instruments.values()) == sorted(i.symbol for i in md1.instruments.values())

    _, s3, _ = go(w, store)  # later the same day: everything is current, no candle is requested at all ...
    asked = [c for c in s3.calls if any(k in c["url"] for k in ("klines", "candles", "OHLC", "market_chart"))]
    assert [c["params"]["symbol"] for c in asked] == ["YOUNGUSDT"]  # ... except a coin with too little history, which is always re-read (it may have grown)


def test_the_forming_day_is_never_stored_even_late_in_the_evening(tmp_path):
    w = CryptoWorld().at(D9)
    md, _, _ = go(w, HistoryStore(tmp_path), cfg=config())
    late = C.fetch_crypto(config(), HistoryStore(tmp_path / "late"), new_client(w.session()), log=lambda m: None, now=w.now(hour=23, minute=55))
    for m in (md, late):
        assert all(df.index[-1] <= pd.Timestamp(D8) for df in m.bars.values())


def test_when_the_first_venue_is_down_the_next_one_serves(tmp_path):
    w = CryptoWorld().at(D9)
    w.down = {"binance_vision"}
    md, _, _ = go(w, HistoryStore(tmp_path))
    assert sources(md)["binance_vision"] is False
    assert {exchange_of(md, s) for s in ("BTC", "ETH", "SOL")} == {"OKX"}
    assert exchange_of(md, "YOUNG") == "CoinGecko"  # Binance-only coin: CoinGecko's closes are better than nothing
    assert closes(md, "BTC").tolist() == pytest.approx(truth(w, "BTC", 300).tolist())  # three pages of OKX candles, UTC-aligned


def test_a_venue_that_disagrees_with_stored_history_is_not_spliced_in(tmp_path):
    w = CryptoWorld().at(D8)
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(D9)
    w.down = {"binance_vision"}
    next(c for c in w.coins if c.symbol == "BTC").factor["okx"] = 1.5  # OKX's numbers are 50% off
    md, _, _ = go(w, store)
    btc = closes(md, "BTC")
    assert btc.iloc[-1] == pytest.approx(w.true_closes("BTC")[D8])  # the day was filled from the next venue, Coinbase, which agrees
    assert btc.max() < 1.3 * truth(w, "BTC").max()
    assert store.load_json("crypto_state")["src"]["BTC"] == "coinbase"
    assert exchange_of(md, "ETH") == "OKX"


def test_a_coin_stays_with_the_venue_that_served_it_even_when_the_first_venue_returns(tmp_path):
    w = CryptoWorld().at(D8)
    store = HistoryStore(tmp_path)
    w.down = {"binance_vision"}
    go(w, store)  # BTC, ETH and SOL come from OKX
    w.down = set()
    w.at(D9)
    md, s2, _ = go(w, store)
    assert {exchange_of(md, s) for s in ("BTC", "ETH", "SOL")} == {"OKX"}  # one continuous series, not one stitched from two venues
    assert not [c for c in s2.calls if "klines" in c["url"] and c["params"]["symbol"] in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]


def test_a_venue_whose_candle_route_keeps_failing_is_dropped_for_the_run_and_reported_down(tmp_path):
    w = CryptoWorld().at(D9)
    w.down_candles = {"binance_vision"}  # its market list answers, its candles do not
    md, sess, _ = go(w, HistoryStore(tmp_path))
    assert len([c for c in sess.calls if "klines" in c["url"]]) == 6  # six failures in a row switch the host off for the run
    line = next(s for s in md.status if s.source == "binance_vision")
    assert line.ok is False and "2 request(s) failed" in line.message  # coins that never asked are not counted as failures
    assert {exchange_of(md, s) for s in ("BTC", "ETH", "SOL")} == {"OKX"}


def test_a_price_that_is_nowhere_near_the_market_is_a_different_coin(tmp_path):
    w = CryptoWorld().at(D9)
    next(c for c in w.coins if c.symbol == "BTC").factor["binance_vision"] = 100.0  # same ticker, a different (or broken) market
    md, _, _ = go(w, HistoryStore(tmp_path))
    assert exchange_of(md, "BTC") == "OKX" and closes(md, "BTC").iloc[-1] == pytest.approx(w.true_closes("BTC")[D8])
    assert exchange_of(md, "ETH") == "Binance" and sources(md)["binance_vision"] is True


def test_coingecko_down_reuses_the_saved_coin_list_and_says_so(tmp_path):
    w = CryptoWorld().at(D8)
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(D9)
    w.down = {"coingecko"}
    md, _, _ = go(w, store)
    assert sources(md)["coingecko"] is False
    assert any("coin list from the copy saved on 2026-10-08" in n for n in md.extras["notes"])
    assert sorted(i.symbol for i in md.instruments.values()) == ["AAA", "BBB", "BTC", "CCC", "ETH", "SOL", "YOUNG"]
    assert md.quotes == {} and md.fundamentals[make_id("crypto", "BTC")]["rank"] == 1  # no live prices; the saved market caps remain
    assert closes(md, "BTC").index[-1] == pd.Timestamp(D8)  # exchanges still updated the candles
    assert md.extras["global"]["total_mcap"] == 3.4e12  # the saved global figures


def test_without_coingecko_or_a_saved_list_the_busiest_exchange_markets_are_followed(tmp_path):
    w = CryptoWorld().at(D9)
    w.down = {"coingecko"}
    md, _, _ = go(w, HistoryStore(tmp_path))
    assert sorted(i.symbol for i in md.instruments.values()) == ["BTC", "ETH", "SOL", "YOUNG"]  # THIN trades too little, WBTC is a copy
    assert any("busiest markets" in n for n in md.extras["notes"])
    assert md.fundamentals == {} and md.quotes == {}
    assert md.instruments[make_id("crypto", "BTC")].name == "BTC"


def test_time_limit_keeps_stored_history_instead_of_failing(tmp_path):
    w = CryptoWorld().at(D8)
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(D9)
    md, s2, _ = go(w, store, cfg=config(max_seconds=0))
    assert [c for c in s2.calls if any(k in c["url"] for k in ("klines", "candles", "OHLC", "market_chart"))] == []
    assert any("time limit reached" in n for n in md.extras["notes"])
    assert closes(md, "BTC").index[-1] == pd.Timestamp(dt.date(2026, 10, 7))  # stale, but present


def test_stablecoins_are_followed_only_when_asked_for(tmp_path):
    w = CryptoWorld().at(D9)
    cfg = config()
    cfg["universe"]["crypto"]["exclude_stables"] = False
    md, _, _ = go(w, HistoryStore(tmp_path), cfg=cfg)
    usdt = md.instruments[make_id("crypto", "USDT")]
    assert usdt.meta["stable"] is True and "USDT" in {i.symbol for i in md.instruments.values()}


@pytest.mark.parametrize("venue, days", [("binance_vision", 1100), ("okx", 420), ("coinbase", 650), ("kraken", 500), ("kucoin", 700)])
def test_every_venue_pages_through_a_long_history(tmp_path, venue, days):
    w = CryptoWorld(coins=[Coin("ZZZ", "zzz", "Zed", 5.0, 1e9, 1e7, listed=(venue,))], days=1300).at(D9)
    cfg = config(history_days=days, sources=[venue])
    md, _, _ = go(w, HistoryStore(tmp_path), cfg=cfg)
    s = closes(md, "ZZZ")
    assert len(s) == days and s.index[-1] == pd.Timestamp(D8)
    assert s.index.to_series().diff().dropna().eq(pd.Timedelta(days=1)).all()  # no gaps between the pages
    assert s.tolist() == pytest.approx(truth(w, "ZZZ").loc[s.index].tolist())
    assert exchange_of(md, "ZZZ") == C.EXCHANGES[venue].label


def test_empty_everything_gives_an_empty_market_not_a_crash(tmp_path):
    w = CryptoWorld().at(D9)
    w.down = {"coingecko", "fng", *C.EXCHANGES}
    md, _, _ = go(w, HistoryStore(tmp_path))
    assert md.instruments == {} and md.bars == {}
    assert sources(md)["coingecko"] is False and all(not ok for k, ok in sources(md).items())
    assert md.extras["global"]["total_mcap"] is None and md.extras["history_pct"] == 0


# ------------------------------------------------------------------------------------------------ units
def test_classification_helpers():
    assert C.looks_stable("USDC", 1.0, 0.0, 1.17) and C.looks_stable("FOO", 0.999, 0.1, 1.4) and not C.looks_stable("XRP", 1.01, 0.5, 3.84)
    assert not C.looks_stable("FOO", 1.0, 5.0, 1.1) and not C.looks_stable("BAR", 50.0, 0.1, 60.0) and C.looks_stable("PAXG", 2600.0, 0.2, 2700.0)
    assert C.is_derivative("WBTC") and C.is_derivative("XYZ", "Wrapped Something") and C.is_derivative("STETH", "Lido Staked Ether")
    assert not C.is_derivative("BTC", "Bitcoin") and not C.is_derivative("WLD", "Worldcoin")


def row(sym, mcap=1e9, vol=1e7, name=None, price=10.0, chg=1.0, ath=20.0):
    return {"id": sym.lower(), "symbol": sym, "name": name or sym, "price": price, "mcap": mcap, "volume_24h": vol, "chg_24h": chg, "ath": ath}


def test_pick_universe_reports_why_coins_were_left_out():
    rows = [row("AAA"), row("AAA"), row("USDC", price=1.0, ath=1.1, chg=0.0), row("WBTC", name="Wrapped Bitcoin"), row("BBB", vol=1e5), row("CCC", mcap=0), row("d d"), row("EEE"), row("FFF")]
    coins, why = C.pick_universe(rows, top_n=8, min_volume=2e6, exclude_stables=True)
    assert [c["symbol"] for c in coins] == ["AAA", "EEE"]  # FFF is the 9th: beyond top_n
    assert why == {"ticker already used by a larger coin": 1, "stablecoin or pegged token": 1, "wrapped or staked copy": 1, "thin trading": 1, "no market cap": 1, "unusual ticker": 1}
    kept, _ = C.pick_universe(rows, top_n=8, min_volume=2e6, exclude_stables=False)
    assert [c["symbol"] for c in kept] == ["AAA", "USDC", "EEE"] and kept[1]["stable"] is True
    nocap, _ = C.pick_universe([row("AAA", mcap=None)], top_n=5, min_volume=1.0, exclude_stables=True, require_mcap=False)
    assert len(nocap) == 1


def test_plausibility_check_catches_a_different_market():
    df = pd.DataFrame({"close": [100.0, 101.0]}, index=pd.to_datetime(["2026-10-07", "2026-10-08"]))
    assert C._plausible(df, 105.0) and C._plausible(df, None) and C._plausible(df.iloc[0:0], 105.0)
    assert not C._plausible(df, 1.0) and not C._plausible(df, 100000.0)


# --- samples in the shapes the real APIs document
def test_binance_samples():
    kl = [[1727740800000, "62000.01", "63000.00", "61500.5", "62500.25", "1234.5", 1727827199999, "76000000", 100, "1", "2", "0"],
          [1727827200000, "62500.25", "64000", "62000", "63900.1", "2000", 1727913599999, "1", 1, "1", "1", "0"]]
    df = C.parse_binance_klines(kl)
    assert df.index.tolist() == [pd.Timestamp("2024-10-01"), pd.Timestamp("2024-10-02")]
    assert df.iloc[0].tolist()[:5] == [62000.01, 63000.0, 61500.5, 62500.25, 1234.5]
    with pytest.raises(ParseError):
        C.parse_binance_klines({"code": -1121, "msg": "Invalid symbol."})
    t = C.parse_binance_tickers([{"symbol": "BTCUSDT", "lastPrice": "62000.0", "quoteVolume": "1e9"}, {"symbol": "ETHBTC", "lastPrice": "0.04"}, {"symbol": "BADUSDT", "lastPrice": "0"}])
    assert list(t) == ["BTC"] and t["BTC"].pair == "BTCUSDT"


def test_okx_samples_skip_the_forming_candle_and_report_errors():
    payload = {"code": "0", "msg": "", "data": [["1727827200000", "2", "3", "1", "2.5", "10", "0", "0", "0"], ["1727740800000", "1", "2", "0.5", "1.5", "9", "0", "0", "1"]]}
    df, oldest = C.parse_okx_candles(payload)
    assert df.index.tolist() == [pd.Timestamp("2024-10-01")] and oldest == 1727740800000.0
    with pytest.raises(ParseError, match="Instrument"):
        C.parse_okx_candles({"code": "51001", "msg": "Instrument ID doesn't exist", "data": []})
    assert list(C.parse_okx_tickers({"code": "0", "data": [{"instId": "BTC-USDT", "last": "1", "volCcy24h": "5"}, {"instId": "BTC-EUR", "last": "1"}]})) == ["BTC"]


def test_coinbase_samples_put_the_columns_in_open_high_low_close_order():
    df = C.parse_coinbase_candles([[1727827200, 61000.0, 64000.0, 62500.25, 63900.1, 2000.0], [1727740800, 61500.5, 63000.0, 62000.01, 62500.25, 1234.5]])
    assert df.loc["2024-10-01"].tolist()[:5] == [62000.01, 63000.0, 61500.5, 62500.25, 1234.5]
    with pytest.raises(ParseError, match="NotFound"):
        C.parse_coinbase_candles({"message": "NotFound"})
    prods = C.parse_coinbase_products([{"id": "BTC-USDC", "base_currency": "BTC", "quote_currency": "USDC", "status": "online"}, {"id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD", "status": "online"},
                                       {"id": "X-USD", "base_currency": "X", "quote_currency": "USD", "status": "online", "trading_disabled": True}])
    assert list(prods) == ["BTC"] and prods["BTC"].pair == "BTC-USD"


def test_kraken_samples_use_kraken_names():
    pairs = C.parse_kraken_pairs({"error": [], "result": {"XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "status": "online"}, "XDGUSD": {"altname": "XDGUSD", "wsname": "XDG/USD"},
                                                          "ETHEUR": {"altname": "ETHEUR", "wsname": "ETH/EUR"}, "XBTUSDT": {"altname": "XBTUSDT", "wsname": "XBT/USDT"}}})
    assert set(pairs) == {"BTC", "DOGE"} and pairs["BTC"].pair == "XBTUSD"  # USD wins over USDT
    df = C.parse_kraken_ohlc({"error": [], "result": {"XXBTZUSD": [[1727740800, "62000.0", "63000", "61500", "62500", "62400", "12.5", 99]], "last": 1727740800}})
    assert df.iloc[0].tolist()[:5] == [62000.0, 63000.0, 61500.0, 62500.0, 12.5]
    with pytest.raises(ParseError, match="Unknown asset pair"):
        C.parse_kraken_ohlc({"error": ["EQuery:Unknown asset pair"]})


def test_kucoin_samples_use_open_close_high_low_order():
    df = C.parse_kucoin_candles({"code": "200000", "data": [["1727740800", "62000", "62500", "63000", "61500", "12.5", "800000"]]})
    assert df.iloc[0].tolist()[:5] == [62000.0, 63000.0, 61500.0, 62500.0, 12.5]
    with pytest.raises(ParseError, match="symbol not exists"):
        C.parse_kucoin_candles({"code": "400100", "msg": "symbol not exists"})
    assert list(C.parse_kucoin_tickers({"code": "200000", "data": {"ticker": [{"symbol": "BTC-USDT", "last": "62000", "volValue": "1"}, {"symbol": "BTC-BTC", "last": "1"}]}})) == ["BTC"]


def test_coingecko_samples():
    rows = C.parse_markets([{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 62000, "market_cap": 1.2e12, "market_cap_rank": 1, "total_volume": 3e10,
                             "price_change_percentage_24h": -1.5, "circulating_supply": 19.7e6, "max_supply": 21e6, "ath": 73738, "ath_date": "2024-03-14T07:10:36.635Z"},
                            {"id": "odd", "symbol": "odd", "name": None, "current_price": None, "market_cap": None, "market_cap_rank": None}, "junk", {"symbol": "x"}])
    assert rows[0]["symbol"] == "BTC" and rows[0]["rank"] == 1 and rows[0]["ath_date"] == "2024-03-14" and rows[1]["name"] == "odd" and rows[1]["rank"] is None and len(rows) == 2
    with pytest.raises(ParseError):
        C.parse_markets([])
    with pytest.raises(ParseError):
        C.parse_markets({"status": {"error_code": 429}})
    assert C.parse_global({"data": {"total_market_cap": {"usd": 3e12}, "market_cap_percentage": {"btc": 55.5}}}) == {"total_mcap": 3e12, "btc_dominance": 55.5, "eth_dominance": None, "mcap_chg_24h": None}
    with pytest.raises(ParseError):
        C.parse_global({})
    assert C.parse_fear_greed({"data": [{"value": "72"}]}) == 72.0 and C.parse_fear_greed({"data": [{"value": "300"}]}) is None
    with pytest.raises(ParseError):
        C.parse_fear_greed({"data": []})
    chart = C.parse_cg_chart({"prices": [[1727740800000, 100.0], [1727827200000, 110.0], [1727913600000, 99.0]], "total_volumes": [[1727740800000, 1000.0], [1727827200000, 2200.0]]})
    assert chart["close"].tolist() == [100.0, 110.0, 99.0] and chart["open"].tolist() == [100.0, 100.0, 110.0] and chart["high"].tolist() == [100.0, 110.0, 110.0]
    assert chart["volume"].iloc[1] == pytest.approx(20.0) and bool(chart["approx_hl"].all())
