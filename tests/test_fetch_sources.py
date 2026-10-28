"""Parsers and small clients for the PSX portal, TradingView and Yahoo, against hand-written samples of what they send."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
import requests

from acumen.fetch import psx_dps as D
from acumen.fetch import psx_tv as TV
from acumen.fetch import yahoo as Y
from acumen.fetch.http import Client, FetchError, HttpError, ParseError
from tests.fake_psx import World, day_html, watch_html, yahoo_json
from tests.fakenet import FakeClock, FakeResponse, FakeSession, ok


def client(session=None, **kw) -> Client:
    clock = FakeClock()
    return Client("T/1", session=session or FakeSession(), sleep=clock.sleep, clock=clock, default_interval=0.0, **kw)


def ep(day: dt.date, hour: int = 0) -> int:
    """Pakistan wall-clock seconds, as the portal publishes them (read as UTC)."""
    return int(dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc).timestamp())


# ------------------------------------------------------------------------------------------------ portal tables
def test_parse_table_prefers_the_clean_data_order_value_and_survives_odd_headers():
    html = """<table><thead><tr><th data-name="symbol">SYMBOL</th><th data-name="ldcp">LDCP</th><th>Current</th><th>Change %</th><th data-name="volume">VOLUME</th></tr></thead>
    <tbody><tr><td data-order="HBL"><a><strong>HBL</strong></a></td><td data-order="250.5">250.50</td><td>251.25</td><td data-order="-0.23">▼ -0.23%</td><td data-order="1234567">1,234,567</td></tr></tbody></table>"""
    rows = D.parse_table(html)
    assert rows == [{"symbol": "HBL", "ldcp": "250.5", "close": "251.25", "pct": "-0.23", "volume": "1234567"}]
    assert D.parse_table(html, index=3) == [] and D.parse_table("<p>nothing</p>") == []


def test_parse_market_watch_keeps_only_real_symbols_and_numbers():
    html = watch_html([
        {"symbol": "hbl", "ldcp": 100.0, "open": 101.0, "high": 103.0, "low": 99.5, "close": 102.0, "volume": 5000},
        {"symbol": "ABC-OCT", "ldcp": 10.0, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1},  # a futures contract
        {"symbol": "TOOLONGSYMBOL1", "ldcp": 1.0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1},
    ])
    rows = D.parse_market_watch(html)
    assert [r["symbol"] for r in rows] == ["HBL"]
    r = rows[0]
    assert (r["ldcp"], r["open"], r["high"], r["low"], r["close"], r["volume"]) == (100.0, 101.0, 103.0, 99.5, 102.0, 5000.0)
    assert r["listed"] == ["ALLSHR", "KSE100"] and r["pct"] == pytest.approx(2.0)
    with pytest.raises(D.ParseError):
        D.parse_market_watch("<html><body>maintenance</body></html>")


def test_parse_day_returns_nothing_for_a_holiday_page():
    w = World(n=24, days=3)
    rows = D.parse_day(day_html(w.day_rows(w.dates[-1])))
    assert len(rows) == 24 and rows[0]["close"] > 0 and rows[0]["ldcp"] > 0
    assert D.parse_day("<table class='tbl'><thead></thead><tbody></tbody></table>") == []
    assert D.parse_day("") == [] and D.parse_day("<html></html>") == []
    few = day_html(w.day_rows(w.dates[-1])[:5])  # a handful of rows is a partial page, not a trading day
    assert D.parse_day(few) == []
    broken = w.day_rows(w.dates[-1])
    for r in broken[:20]:
        r["close"] = 0.0  # zero closes are not prices
    assert D.parse_day(day_html(broken)) == []


def test_parse_indices():
    html = """<table><tbody>
    <tr><td><a data-code="KSE100">KSE-100 Index</a></td><td data-order="170500.5">170,500.50</td><td data-order="168900.1">168,900.10</td><td data-order="169800.2">169,800.20</td><td data-order="-120.5">▼ -120.50</td><td data-order="-0.07">▼ -0.07%</td></tr>
    <tr><td><a data-code="KMI30">KMI-30 Index</a></td><td>260,000.00</td><td>258,000.00</td><td>259,000.00</td><td>1,000.00</td><td>0.39%</td></tr>
    <tr><td>no code here</td><td>1</td></tr></tbody></table>"""
    out = D.parse_indices(html)
    assert out["KSE100"] == {"high": 170500.5, "low": 168900.1, "current": 169800.2, "change": -120.5, "pct": -0.07}
    assert out["KMI30"]["current"] == 259000.0 and set(out) == {"KSE100", "KMI30"}


def test_parse_eod_is_newest_first_close_volume_open_and_marks_high_low_as_approximate():
    d1, d2, d3 = dt.date(2026, 9, 28), dt.date(2026, 9, 29), dt.date(2026, 9, 30)
    payload = {"status": 1, "message": "", "data": [[ep(d3, 15), 103.0, 300, 102.0], [ep(d2, 15), 102.0, 200, 101.0], [ep(d1, 0), 101.0, 100, 100.0], [None, 1, 1, 1], "junk", [ep(d1), "x", 1, 1]]}
    df = D.parse_eod(payload, "ABC")
    assert df.index.tolist() == [pd.Timestamp(d1), pd.Timestamp(d2), pd.Timestamp(d3)]  # oldest first once parsed
    assert df["close"].tolist() == [101.0, 102.0, 103.0] and df["open"].tolist() == [100.0, 101.0, 102.0] and df["volume"].tolist() == [100.0, 200.0, 300.0]
    assert df["approx_hl"].all()
    assert len(D.parse_eod({"status": 1, "data": []}, "NOPE")) == 0
    with pytest.raises(ParseError):
        D.parse_eod([1, 2, 3], "ABC")


def test_parse_symbols():
    payload = [
        {"symbol": "hbl", "name": " Habib Bank ", "sectorName": "COMMERCIAL BANKS", "isETF": False, "isDebt": False},
        {"symbol": "HBLTFC1", "name": "HBL TFC", "sectorName": "TFC", "isETF": False, "isDebt": True},
        {"symbol": "NIT", "name": "", "sectorName": None, "isETF": True},
        {"symbol": "bad symbol"}, "junk", {"name": "no symbol"},
    ]
    out = {s["symbol"]: s for s in D.parse_symbols(payload)}
    assert set(out) == {"HBL", "HBLTFC1", "NIT"}
    assert out["HBL"]["name"] == "Habib Bank" and out["HBL"]["sector"] == "COMMERCIAL BANKS"
    assert out["HBLTFC1"]["is_debt"] and out["NIT"]["is_etf"] and out["NIT"]["name"] == "NIT" and out["NIT"]["sector"] == ""
    with pytest.raises(ParseError):
        D.parse_symbols([])
    with pytest.raises(ParseError):
        D.parse_symbols({"not": "a list"})


# ------------------------------------------------------------------------------------------------ the portal client
def test_dps_caches_slow_changing_routes_for_the_run():
    w = World(n=24, days=3)
    sess = w.session()
    dps = D.Dps(client(sess), interval=0)
    assert len(dps.symbols()) == 25 and len(dps.symbols()) == 25  # 24 stocks and a sukuk: the futures contract has a hyphen
    assert sess.count(r"dps\.psx\.com\.pk/symbols") == 1


def test_dps_looks_for_a_working_node_when_a_data_route_starts_answering_404():
    w = World(n=24, days=3)
    sess = w.session()
    day = w.dates[-1]
    seen = {"n": 0}
    real = [r for r in sess.routes if r[0] == "POST" and "historical" in r[1].pattern][0][2]

    def flaky(call):  # the first request lands on a node that does not serve data
        seen["n"] += 1
        return FakeResponse(404, b"nope") if seen["n"] == 1 else real(call)

    sess.routes = [(m, rx, flaky if (m == "POST" and "historical" in rx.pattern) else a) for m, rx, a in sess.routes]
    dps = D.Dps(client(sess), interval=0, resolver=lambda: [])
    assert len(dps.day(day)) == 24
    assert sess.count("symbol-position") == 1  # one probe, which found the unpinned node fine, then the request was repeated
    assert seen["n"] == 2


def test_dps_gives_up_re_probing_after_two_tries():
    w = World(n=24, days=3)
    sess = w.session()
    sess.routes.insert(0, ("POST", __import__("re").compile(r"historical"), FakeResponse(404, b"nope")))
    dps = D.Dps(client(sess), interval=0, resolver=lambda: [])
    for _ in range(3):
        with pytest.raises(HttpError):
            dps.day(w.dates[-1])
    assert sess.count("symbol-position") <= 2 * (1 + len(D.SEED_NODES))  # bounded, not one probe round per failure


# ------------------------------------------------------------------------------------------------ the portal's request token
TOKEN_PAGE = '<html><head><script>window.__ps = {"_k": "%s", "lc": "en", "rv": 3, "tz": "Asia/Karachi"};</script></head><body></body></html>'
TOKEN_A, TOKEN_B = "ovC1yAQm9-8H2Zf3kLx0RtPwUe5JdGhNsB7aVcXy1M_", "Zq4mN8xW2cV7bB1nM5kL9jH3gF6dS0aP-tR_yUeIoQw"


def test_parse_token_reads_the_value_the_pages_embed_and_nothing_else():
    assert D.parse_token(TOKEN_PAGE % TOKEN_A) == TOKEN_A
    assert D.parse_token('<script>window.__ps={"_k":"%s"}</script>' % TOKEN_A) == TOKEN_A  # no spaces, no semicolon
    assert D.parse_token("<html>no block</html>") is None
    assert D.parse_token("<script>window.__ps = {not json};</script>") is None
    assert D.parse_token('<script>window.__ps = {"lc": "en"};</script>') is None
    assert D.parse_token(TOKEN_PAGE % "short") is None  # too short to be a token
    assert D.parse_token(TOKEN_PAGE % "has spaces in it, so it is not a token") is None


def _front_page_reads(sess) -> int:
    return sess.count(r"dps\.psx\.com\.pk/$")


def test_dps_sends_the_front_page_token_on_every_data_route():
    w = World(n=24, days=3)
    w.token = TOKEN_A
    sess = w.session()
    dps = D.Dps(client(sess), interval=0)
    assert len(dps.symbols()) == 25
    assert len(dps.market_watch()) == 24
    assert len(dps.day(w.dates[-1])) == 24
    assert len(dps.eod("TST00")) == 3
    data_calls = [c for c in sess.calls if not c["url"].endswith("psx.com.pk/")]
    assert len(data_calls) == 4
    assert all(c["headers"]["X-Req-Id"] == TOKEN_A and c["headers"]["X-Requested-With"] == "XMLHttpRequest" for c in data_calls)
    assert _front_page_reads(sess) == 1  # one read of the front page served all four


def test_dps_reads_a_new_token_when_the_portal_refuses_the_old_one():
    w = World(n=24, days=3)
    w.token = TOKEN_A
    sess = w.session()
    dps = D.Dps(client(sess), interval=0)
    assert len(dps.symbols()) == 25
    w.token = TOKEN_B  # the portal rotated it
    assert len(dps.day(w.dates[-1])) == 24  # refused once, a new token read, asked again
    assert dps.token == TOKEN_B and sess.count("historical") == 2 and _front_page_reads(sess) == 2
    assert len(dps.market_watch()) == 24 and _front_page_reads(sess) == 2  # and the new one is kept


def test_dps_reads_a_fresh_token_before_the_old_one_can_run_out():
    w = World(n=24, days=3)
    w.token = TOKEN_A
    sess = w.session()
    clock = FakeClock()
    dps = D.Dps(Client("T/1", session=sess, sleep=clock.sleep, clock=clock, default_interval=0.0), interval=0)
    dps.symbols()
    clock.now += D.TOKEN_TTL_S + 1
    w.token = TOKEN_B
    assert len(dps.day(w.dates[-1])) == 24
    assert sess.count("historical") == 1 and _front_page_reads(sess) == 2  # no refusal needed: the token was renewed on age


def test_dps_still_works_when_the_pages_carry_no_token_and_says_so():
    w = World(n=24, days=3)  # no token: the pages embed none and the routes do not ask for one
    sess = w.session()
    dps = D.Dps(client(sess), interval=0)
    assert len(dps.symbols()) == 25 and len(dps.day(w.dates[-1])) == 24
    assert all("X-Req-Id" not in (c["headers"] or {}) for c in sess.calls)
    assert dps.token is None and "no request token" in dps.token_note


def test_dps_carries_on_when_the_front_page_cannot_be_read_and_keeps_the_token_it_has():
    w = World(n=24, days=3)
    sess = w.session()
    dps = D.Dps(client(sess), interval=0)
    sess.routes.insert(0, ("GET", __import__("re").compile(r"dps\.psx\.com\.pk/$"), requests.ConnectionError("boom")))
    assert len(dps.symbols()) == 25  # the routes do not ask for a token in this world, so the failed page read costs nothing
    assert dps.token is None and "could not be read" in dps.token_note
    dps.token, dps.token_at = TOKEN_A, None  # a token from an earlier read survives a failed renewal
    dps.refresh_token()
    assert dps.token == TOKEN_A


def test_dps_stops_asking_when_even_a_fresh_token_is_refused():
    w = World(n=24, days=3)
    w.token = TOKEN_A
    sess = w.session()
    sess.routes.insert(0, ("GET", __import__("re").compile(r"dps\.psx\.com\.pk/symbols"), FakeResponse(403, b"")))
    dps = D.Dps(client(sess), interval=0, resolver=lambda: [])
    with pytest.raises(HttpError) as err:
        dps.symbols()
    assert err.value.status == 403
    assert sess.count(r"dps\.psx\.com\.pk/symbols") <= 4 and _front_page_reads(sess) <= 4  # bounded: a new token once, a node search twice at most


# ------------------------------------------------------------------------------------------------ TradingView
def test_tradingview_drops_columns_it_does_not_know_and_asks_again():
    w = World(n=24, days=3)
    sess = FakeSession()

    def scan(call):
        cols = call["json"]["columns"]
        bad = [c for c in ("dividend_yield_recent", "net_margin") if c in cols]
        if bad:
            return FakeResponse(400, ('{"error":"Unknown field \\"%s\\""}' % bad[0]).encode())
        return ok(w.tv_json(cols))

    sess.post(r"scanner\.tradingview\.com/pakistan/scan", scan)
    rows, dropped = TV.scan(client(sess))
    assert dropped == ["dividend_yield_recent", "net_margin"] and len(rows) == 24
    assert len(sess.calls) == 3
    assert all("net_margin" not in c["json"]["columns"] for c in sess.calls[-1:])
    f = TV.fundamentals_from_row(rows["TST00"])
    assert f["pe"] == 8.5 and f["dy"] == 4.2 and f["net_margin_pct"] is None  # a dropped column means a missing field only


def test_tradingview_refusing_the_request_without_naming_a_column_is_an_error():
    sess = FakeSession().post(r"scanner\.tradingview\.com", FakeResponse(400, b'{"error":"bad request"}'))
    with pytest.raises(FetchError, match="refused"):
        TV.scan(client(sess))
    sess = FakeSession().post(r"scanner\.tradingview\.com", FakeResponse(403, b"forbidden"))
    with pytest.raises(HttpError):
        TV.scan(client(sess))
    sess = FakeSession().post(r"scanner\.tradingview\.com", ok({"totalCount": 0, "data": []}))
    with pytest.raises(ParseError):
        TV.scan(client(sess))
    sess = FakeSession().post(r"scanner\.tradingview\.com", ok("<html>"))
    with pytest.raises(FetchError):
        TV.scan(client(sess))


def test_fundamentals_from_row_rejects_implausible_numbers_and_leaves_gaps_unknown():
    good = {"price_earnings_ttm": 8.5, "price_book_fq": 1.3, "earnings_per_share_diluted_ttm": 12.0, "dividends_yield": 6.5, "return_on_equity": 21.0,
            "book_value_per_share_fq": 80.0, "market_cap_basic": 1.0e11, "total_shares_outstanding_fundamental": 1.0e9, "float_shares_outstanding": 4.0e8}
    f = TV.fundamentals_from_row(good)
    assert (f["pe"], f["pb"], f["eps"], f["dy"], f["roe"], f["bvps"], f["free_float_pct"]) == (8.5, 1.3, 12.0, 6.5, 21.0, 80.0, 40.0)
    assert f["payout_pct"] is None and f["dividends"] is None and f["eps_hist"] is None
    bad = TV.fundamentals_from_row({"price_earnings_ttm": -4.0, "price_book_fq": 0, "dividends_yield": 400, "return_on_equity": -900, "market_cap_basic": -1,
                                    "total_shares_outstanding_fundamental": 100, "float_shares_outstanding": 500})
    assert bad["pe"] is None and bad["pb"] is None and bad["dy"] is None and bad["roe"] is None and bad["mcap"] is None and bad["free_float_pct"] is None
    assert TV.fundamentals_from_row({})["pe"] is None
    fallback = TV.fundamentals_from_row({"earnings_per_share_basic_ttm": 3.0, "dividend_yield_recent": 2.0, "return_on_equity_fq": 9.0, "total_shares_outstanding": 5.0})
    assert (fallback["eps"], fallback["dy"], fallback["roe"], fallback["shares"]) == (3.0, 2.0, 9.0, 5.0)


def test_quote_from_row_derives_the_previous_close_from_the_change():
    q = TV.quote_from_row({"close": 110.0, "change": 10.0, "volume": 5000}, "2026-10-02T12:30:00Z")
    assert q["price"] == 110.0 and q["prev_close"] == pytest.approx(100.0) and q["chg_pct"] == 10.0 and q["volume"] == 5000.0
    assert TV.quote_from_row({"close": None}, "x") is None and TV.quote_from_row({"close": 0}, "x") is None
    assert TV.quote_from_row({"close": 5.0, "change": -100.0}, "x")["prev_close"] is None


# ------------------------------------------------------------------------------------------------ Yahoo
def test_yahoo_parse_chart_reads_bars_in_the_exchange_calendar_day():
    dates = [dt.date(2026, 9, 28), dt.date(2026, 9, 29), dt.date(2026, 9, 30)]
    df, meta = Y.parse_chart(yahoo_json(dates, [100.0, 101.0, 102.0]), "X")
    assert df.index.tolist() == [pd.Timestamp(d) for d in dates] and df["close"].tolist() == [100.0, 101.0, 102.0]
    assert meta["gmtoffset"] == 18000
    payload = yahoo_json(dates, [100.0, 101.0, 102.0])
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None  # a missing close is skipped, not zero
    df, _ = Y.parse_chart(payload, "X")
    assert len(df) == 2
    # a timestamp late in the previous UTC day belongs to the next local day once the offset is applied
    late = {"chart": {"result": [{"meta": {"gmtoffset": 18000}, "timestamp": [int(dt.datetime(2026, 9, 29, 20, tzinfo=dt.timezone.utc).timestamp())],
                                  "indicators": {"quote": [{"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]}]}}], "error": None}}
    assert Y.parse_chart(late, "X")[0].index[0] == pd.Timestamp("2026-09-30")


@pytest.mark.parametrize("payload, text", [
    ({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}, "No data found"),
    ({"chart": {"result": [], "error": None}}, "no data"),
    ({"chart": {"result": [{"meta": {}, "timestamp": [], "indicators": {"quote": [{}]}}], "error": None}}, "no bars"),
    ({"chart": {"result": [{"meta": {}, "timestamp": [1], "indicators": {"quote": [{"close": [None]}]}}], "error": None}}, "only empty"),
    ({"unexpected": 1}, "unexpected"),
    ([], "unexpected"),
])
def test_yahoo_parse_chart_failures_say_why(payload, text):
    with pytest.raises(ParseError, match=f"(?i){text}"):
        Y.parse_chart(payload, "X")


def test_yahoo_chart_tries_the_second_host_on_a_network_error_but_not_after_an_answer():
    dates = [dt.date(2026, 9, 29), dt.date(2026, 9, 30)]
    sess = FakeSession()
    sess.get(r"query1\.finance\.yahoo\.com", requests.ConnectionError("reset"))
    sess.get(r"query2\.finance\.yahoo\.com", ok(yahoo_json(dates, [1.0, 2.0])))
    df, _ = Y.chart(client(sess), "EURUSD=X")
    assert len(df) == 2 and sess.count("query2") == 1
    assert "EURUSD%3DX" in sess.calls[0]["url"]  # the symbol is URL-quoted

    sess = FakeSession()
    sess.get(r"query1\.finance\.yahoo\.com", ok({"chart": {"result": None, "error": {"description": "Not found"}}}))
    sess.get(r"query2\.finance\.yahoo\.com", ok(yahoo_json(dates, [1.0, 2.0])))
    with pytest.raises(ParseError):
        Y.chart(client(sess), "NOPE")
    assert sess.count("query2") == 0  # Yahoo answered; the other host would say the same

    sess = FakeSession().get(r"finance\.yahoo\.com", FakeResponse(404, b"{}"))
    with pytest.raises(HttpError):
        Y.chart(client(sess), "NOPE")
    assert sess.count("query2") == 0

    sess = FakeSession().get(r"finance\.yahoo\.com", FakeResponse(429, b"", headers={"Retry-After": "900"}))
    c = client(sess)
    with pytest.raises(FetchError):
        Y.chart(c, "AAA")
    assert c.is_open("query1.finance.yahoo.com")
