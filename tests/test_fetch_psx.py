"""The PSX fetcher against the simulated portal in ``tests/fake_psx.py``. Nothing here touches the network or waits."""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from acumen.config import load_config
from acumen.fetch import psx as P
from acumen.fetch import psx_dps as D
from acumen.fetch.http import Client
from acumen.model import make_id, normalise_bars
from acumen.store import HistoryStore
from tests.fake_psx import World, yahoo_json
from tests.fakenet import FakeClock, FakeResponse, FakeSession, ok

NOW = dt.datetime(2026, 10, 2, 12, 30, tzinfo=dt.timezone.utc)  # Friday 17:30 in Pakistan: the session is final
TODAY = dt.date(2026, 10, 2)
SYM = "TST03"
IID = make_id("psx", SYM)
YEARS = 0.2  # history the fetcher is asked for: 73 days, about 52 trading days
SECTORS = {"Commercial Banks", "Cement", "Fertilizer", "Oil & Gas Exploration Companies"}


# ------------------------------------------------------------------------------------------------ helpers
def at(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, 12, 30, tzinfo=dt.timezone.utc)


def config(years: float = YEARS, **psx):
    cfg = load_config("/nonexistent.yml")
    cfg["data"]["psx"].update({"history_years": years, "ohlc_days": 40, **psx})
    cfg["universe"]["psx"]["min_bars"] = 10  # the simulated histories are short: keep Yahoo out unless a test asks for it
    return cfg


def new_client(session=None) -> Client:
    clock = FakeClock()
    return Client("TestAgent/1.0", session=session if session is not None else FakeSession(), sleep=clock.sleep, clock=clock, default_interval=0.0)


def go(world, store, *, now=NOW, cfg=None, session=None, **session_kw):
    """One pipeline run with a fresh client (request counters start at zero, like a new workflow run)."""
    sess = session or world.session(**session_kw)
    client = new_client(sess)
    md = P.fetch_psx(cfg or config(), store, client, log=lambda m: None, now=now)
    return md, sess, client


def world(**kw) -> World:
    return World(**{"n": 24, "days": 60, **kw})


def state(store):
    return store.load_json("psx_state")


def hist_days(sess) -> list[str]:
    return [c["data"]["date"] for c in sess.calls if c["method"] == "POST" and "/historical" in c["url"]]


def sources(md) -> dict[str, bool]:
    return {s.source: s.ok for s in md.status}


def window(w: World, years: float = YEARS, today: dt.date = TODAY) -> list[dt.date]:
    first = today - dt.timedelta(days=int(years * 365.25))
    return [d for d in w.dates if d >= first and d <= today]


def raw_series(w: World, sym: str, col: str = "close") -> pd.Series:
    return pd.Series([w.rows[d][sym][col] for d in w.dates], index=pd.to_datetime(w.dates))


def stocks(md):
    return [i for i in md.instruments.values() if i.kind != "index"]


# ------------------------------------------------------------------------------------------------ first and later runs
def test_first_run_builds_history_instruments_and_state(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    md, sess, _ = go(w, store)
    days = window(w)

    syms = {i.symbol: i for i in stocks(md)}
    assert len(syms) == 24
    assert "TSTSUKUK" not in syms and "TST00-OCT" not in syms  # debt and futures are not stocks
    assert {i.sector for i in syms.values()} == SECTORS
    assert {i.symbol for i in md.instruments.values() if i.kind == "index"} == {"KSE100", "KMI30"}
    assert md.extras["benchmark"]["symbol"] == "KSE100"

    bars = md.bars[IID]
    assert bars.index.tolist() == [pd.Timestamp(d) for d in days]
    for col in ("open", "high", "low", "close"):  # true high and low, straight from the portal
        assert bars[col].tolist() == pytest.approx([w.rows[d][SYM][col] for d in days])
    assert bars["volume"].tolist() == pytest.approx([w.rows[d][SYM]["volume"] for d in days], abs=1.0)  # the portal shows whole shares
    assert "approx_hl" not in bars or not bars["approx_hl"].any()

    last = w.rows[TODAY][SYM]
    q = md.quotes[IID]
    assert q["price"] == last["close"] and q["prev_close"] == last["ldcp"]
    assert q["chg_pct"] == pytest.approx((last["close"] / last["ldcp"] - 1) * 100, abs=1e-6)
    f = md.fundamentals[IID]
    assert f["pe"] == 8.5 and f["roe"] == 16.0 and f["free_float_pct"] == 40.0
    assert f["dividends"] is None and f["eps_hist"] is None  # "unknown", never scored as zero

    st = state(store)
    assert (st["first_date"], st["last_date"]) == (days[0].isoformat(), TODAY.isoformat())
    assert st["adjustments"] == [] and st["empty"] == []
    assert md.extras["history_pct"] == 100
    assert sources(md) == {"psx_tradingview": True, "psx_portal": True}
    assert sess.count("symbol-position") == 1  # DNS pointed at a working node: one probe, no pinning

    saved = store.load_bars("psx")
    assert len(saved[IID]) == len(days) and make_id("psx", "KSE100") in saved
    assert store.load_json("psx_universe")["symbols"][SYM]["sector"] in {"COMMERCIAL BANKS", "CEMENT", "FERTILIZER", "OIL & GAS EXPLORATION COMPANIES"}


def test_second_run_fetches_only_the_new_day(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    w.cutoff = w.dates[-2]
    md1, s1, _ = go(w, store, now=at(w.dates[-2]))
    assert md1.bars[IID].index[-1] == pd.Timestamp(w.dates[-2])
    first_run_days = len(hist_days(s1))
    assert first_run_days > 40

    w.cutoff = None
    md2, s2, _ = go(w, store)
    assert hist_days(s2) == [TODAY.isoformat()]
    assert len(md2.bars[IID]) == len(md1.bars[IID]) + 1
    cols = ["open", "high", "low", "close", "volume"]
    pd.testing.assert_frame_equal(md2.bars[IID][cols].iloc[:-1], md1.bars[IID][cols], check_freq=False, check_index_type=False)  # the stored past is not rewritten
    assert md2.bars[IID].index[-1] == pd.Timestamp(TODAY)
    assert "1 new day(s)" in next(s for s in md2.status if s.source == "psx_portal").message
    assert state(store)["last_date"] == TODAY.isoformat()

    md3, s3, _ = go(w, store)  # nothing new: no history request at all
    assert hist_days(s3) == []
    assert len(md3.bars[IID]) == len(md2.bars[IID])


# ------------------------------------------------------------------------------------------------ corporate actions
def test_bonus_issue_inside_a_forward_block_is_adjusted_everywhere(tmp_path):
    event_day = world().dates[-4]
    w = world(bonus={(SYM, event_day): 1 / 1.2})
    store = HistoryStore(tmp_path)
    w.cutoff = w.dates[-8]
    go(w, store, now=at(w.cutoff))
    w.cutoff = None
    md, _, _ = go(w, store)

    bars = md.bars[IID]
    before = w.dates[w.dates.index(event_day) - 1]
    assert bars.loc[pd.Timestamp(before), "close"] == pytest.approx(w.rows[event_day][SYM]["ldcp"], rel=1e-9)
    assert bars["close"].pct_change().abs().max() < 0.10  # no cliff anywhere ...
    assert raw_series(w, SYM).pct_change().abs().max() > 0.12  # ... although the raw series has one
    assert bars.loc[pd.Timestamp(before), "volume"] == pytest.approx(w.rows[before][SYM]["volume"] * 1.2, rel=3e-3)  # bonus shares: older volume restated

    other = md.bars[make_id("psx", "TST04")]
    assert other["close"].tolist() == pytest.approx(raw_series(w, "TST04").loc[other.index].tolist())  # untouched

    adj = state(store)["adjustments"]
    assert [(a["symbol"], a["date"]) for a in adj] == [(SYM, event_day.isoformat())]
    assert adj[0]["factor"] == pytest.approx(1 / 1.2, abs=3e-3)
    assert "1 price adjustment(s)" in next(s for s in md.status if s.source == "psx_portal").message


def test_bonus_issue_inside_the_first_run_is_adjusted(tmp_path):
    w0 = world()
    event_day = w0.dates[-25]
    w = world(bonus={("TST07", event_day): 0.8})
    md, _, _ = go(w, HistoryStore(tmp_path))
    bars = md.bars[make_id("psx", "TST07")]
    assert bars["close"].pct_change().abs().max() < 0.10
    assert pd.Timestamp(event_day) in bars.index
    assert raw_series(w, "TST07").pct_change().abs().max() > 0.15
    adj = state(HistoryStore(tmp_path))["adjustments"]
    assert [(a["symbol"], a["date"]) for a in adj] == [("TST07", event_day.isoformat())]
    assert adj[0]["factor"] == pytest.approx(0.8, abs=3e-3)


def test_bonus_issue_exactly_at_the_backward_junction_is_adjusted(tmp_path):
    base = world(days=90)
    junction_day = base.dates[-30]  # the oldest day the first (budget-limited) run reaches
    w = world(days=90, bonus={("TST05", junction_day): 1 / 1.25})
    store = HistoryStore(tmp_path)
    cfg = config(years=0.5, max_requests_per_run=34)  # 30 days, the node probe, the symbol list, the market watch and one read of the front page (the request token)

    md1, s1, _ = go(w, store, cfg=cfg)
    assert len(hist_days(s1)) == 30 and state(store)["first_date"] == junction_day.isoformat()
    assert md1.extras["history_pct"] == 75  # 30 of the 40 days the engine wants true high/low for
    assert state(store)["adjustments"] == []  # the first day of a block has nothing to compare with yet
    assert md1.bars[make_id("psx", "TST05")]["close"].pct_change().abs().max() < 0.10  # (only the newest 30 days so far)

    md2, s2, _ = go(w, store, cfg=cfg)  # extends 30 days further back and meets the stored history
    assert len(hist_days(s2)) == 30
    bars = md2.bars[make_id("psx", "TST05")]
    assert len(bars) == 60
    assert bars["close"].pct_change().abs().max() < 0.10
    assert raw_series(w, "TST05").pct_change().abs().max() > 0.15
    adj = state(store)["adjustments"]
    assert [(a["symbol"], a["date"]) for a in adj] == [("TST05", junction_day.isoformat())]
    assert md2.extras["history_pct"] == 100
    other = md2.bars[make_id("psx", "TST06")]
    assert other["close"].tolist() == pytest.approx(raw_series(w, "TST06").loc[other.index].tolist())


def test_a_skipped_trading_day_is_not_mistaken_for_corporate_actions(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    missed = w.dates[-3]
    w.missing_days = {missed}  # the portal answers with an empty table for a day that did trade
    w.cutoff = w.dates[-5]
    go(w, store, now=at(w.cutoff))
    w.cutoff = None
    md, _, _ = go(w, store)

    assert state(store)["adjustments"] == []
    assert any("missed day" in n for n in md.extras["notes"])
    bars = md.bars[IID]
    assert pd.Timestamp(missed) not in bars.index
    assert bars["close"].tolist() == pytest.approx(raw_series(w, SYM).loc[bars.index].tolist())  # nothing rescaled
    assert missed.isoformat() in state(store)["empty"]  # a blank with data after it is remembered as a non-trading day


# ------------------------------------------------------------------------------------------------ holidays and "not published yet"
def test_a_real_holiday_is_asked_once_and_remembered(tmp_path):
    holiday = dt.date(2026, 9, 14)
    w = world(holidays=(holiday,))
    store = HistoryStore(tmp_path)
    md, s1, _ = go(w, store)
    assert hist_days(s1).count(holiday.isoformat()) == 1
    assert state(store)["empty"] == [holiday.isoformat()]
    assert pd.Timestamp(holiday) not in md.bars[IID].index
    assert len(md.bars[IID]) == len(window(w))

    _, s2, _ = go(w, store)
    assert hist_days(s2) == []


def test_a_blank_today_is_not_remembered_as_a_holiday(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    w.cutoff = w.dates[-2]  # the portal has not published today's table yet
    md1, _, _ = go(w, store)
    assert state(store)["empty"] == [] and state(store)["last_date"] == w.dates[-2].isoformat()

    md2, s2, _ = go(w, store)  # still not published: one request for the missing day, nothing remembered
    assert hist_days(s2) == [TODAY.isoformat()]
    assert state(store)["empty"] == []
    assert md2.bars[IID].index[-1] == pd.Timestamp(w.dates[-2])  # yesterday's market-watch numbers are not taken for today's

    w.cutoff = None
    md3, s3, _ = go(w, store)
    assert hist_days(s3) == [TODAY.isoformat()]
    assert md3.bars[IID].index[-1] == pd.Timestamp(TODAY)


def test_market_watch_supplies_today_when_the_day_table_lags(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    w.cutoff = w.dates[-2]
    go(w, store, now=at(w.cutoff))
    w.cutoff, w.lagging = None, {TODAY}
    md, _, _ = go(w, store)
    last = w.rows[TODAY][SYM]
    bar = md.bars[IID].iloc[-1]
    assert md.bars[IID].index[-1] == pd.Timestamp(TODAY)
    assert [bar[k] for k in ("open", "high", "low", "close")] == pytest.approx([last[k] for k in ("open", "high", "low", "close")])
    assert state(store)["last_date"] == TODAY.isoformat() and state(store)["adjustments"] == []

    w.lagging = set()
    _, s3, _ = go(w, store)
    assert hist_days(s3) == []  # published later: nothing to fetch again


# ------------------------------------------------------------------------------------------------ outages
def test_portal_outage_uses_the_saved_symbols_and_the_tradingview_snapshot(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    w.cutoff = w.dates[-2]
    go(w, store, now=at(w.cutoff))
    w.cutoff = None

    md, _, _ = go(w, store, node_ok=False)
    assert sources(md)["psx_portal"] is False and sources(md)["psx_tradingview"] is True
    s = stocks(md)
    assert len(s) == 24 and {i.sector for i in s} == SECTORS  # the portal's sector names, not TradingView's coarser ones
    bars = md.bars[IID]
    assert bars.index[-1] == pd.Timestamp(TODAY)
    assert bool(bars["approx_hl"].iloc[-1]) and not bool(bars["approx_hl"].iloc[-2])  # today's high and low are approximations
    assert bars["close"].iloc[-1] == w.rows[TODAY][SYM]["close"]
    assert any("symbol list from the copy saved" in n for n in md.extras["notes"])
    assert any("TradingView snapshot" in n for n in md.extras["notes"])
    assert md.fundamentals[IID]["pe"] == 8.5
    assert md.quotes[IID]["price"] == w.rows[TODAY][SYM]["close"]
    assert make_id("psx", "KSE100") in md.instruments and any("stored history" in n for n in md.extras["notes"])  # benchmark kept from the store
    assert state(store)["last_date"] == w.dates[-2].isoformat()  # only the portal confirms a day

    md3, _, _ = go(w, store)  # the portal is back: its table replaces the snapshot row; nothing looks like a corporate action
    bars3 = md3.bars[IID]
    assert len(bars3) == len(bars) and not bool(bars3["approx_hl"].iloc[-1])
    assert bars3["open"].iloc[-1] == w.rows[TODAY][SYM]["open"] and bars3["high"].iloc[-1] == w.rows[TODAY][SYM]["high"]
    assert state(store)["adjustments"] == [] and not any("missed day" in n for n in md3.extras["notes"])
    assert state(store)["last_date"] == TODAY.isoformat()


def test_everything_down_still_returns_the_stored_market(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    go(w, store)
    md, _, _ = go(w, store, node_ok=False, tv=False)
    assert sources(md) == {"psx_tradingview": False, "psx_portal": False}
    assert len(stocks(md)) == 24 and md.quotes == {}
    assert md.fundamentals[IID]["pe"] == 8.5
    assert any("fundamentals from the copy saved" in n for n in md.extras["notes"])
    assert md.bars[IID].index[-1] == pd.Timestamp(TODAY)  # stale, and the app shows it as stale


def test_everything_down_on_a_first_run_gives_an_empty_market_not_a_crash(tmp_path):
    md, _, _ = go(world(), HistoryStore(tmp_path), node_ok=False, tv=False)
    assert md.instruments == {} and md.bars == {}
    assert sources(md) == {"psx_tradingview": False, "psx_portal": False}
    assert md.extras["history_pct"] == 0
    assert HistoryStore(tmp_path).load_bars("psx") == {}


def test_a_bad_dns_node_is_pinned_around_and_remembered(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_resolve", lambda: ["10.9.9.9"])
    good = D.SEED_NODES[0]
    w = world()
    store = HistoryStore(tmp_path)

    client = new_client()
    client.session = sess = w.session(good_ip=good, http=SimpleNamespace(pins=client.pins))
    md = P.fetch_psx(config(), store, client, log=lambda m: None, now=NOW)
    assert len(md.instruments) == 26 and sources(md)["psx_portal"] is True
    assert client.pins["dps.psx.com.pk"] == good
    assert sess.count("symbol-position") == 3  # the DNS node, the other DNS answer, the known good node
    nodes = store.load_json("psx_nodes")
    assert nodes["good"] == [good] and "10.9.9.9" in nodes["seen"]

    client2 = new_client()
    client2.session = sess2 = w.session(good_ip=good, http=SimpleNamespace(pins=client2.pins))
    P.fetch_psx(config(), store, client2, log=lambda m: None, now=NOW)
    assert sess2.count("symbol-position") == 2  # the DNS node fails once, then the remembered one is tried first


def test_when_no_node_serves_data_the_portal_is_reported_down_and_nothing_stays_pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_resolve", lambda: ["10.9.9.9"])
    md, _, client = go(world(), HistoryStore(tmp_path), node_ok=False)
    portal = next(s for s in md.status if s.source == "psx_portal")
    assert not portal.ok and "no node" in portal.message
    assert client.pins == {} and not client.is_open("dps.psx.com.pk")


# ------------------------------------------------------------------------------------------------ other sources
def test_kse100_falls_back_to_yahoo_when_the_portal_has_no_index_history(tmp_path):
    w = world()
    sess = w.session()
    sess.routes.insert(0, ("GET", re.compile(r"timeseries/eod/KSE100"), FakeResponse(404, b"{}")))
    dates = w.dates[-60:]
    sess.get(r"finance/chart/%5EKSE", ok(yahoo_json(dates, [40000.0 + 10 * i for i in range(60)])))
    md, _, _ = go(w, HistoryStore(tmp_path), session=sess)
    kse = md.bars[make_id("psx", "KSE100")]
    assert len(kse) == 60 and kse["close"].iloc[-1] == 40590.0
    assert make_id("psx", "KMI30") in md.instruments  # the other index still came from the portal
    assert any(n.startswith("KSE100 from the portal") for n in md.extras["notes"])


def test_yahoo_adds_older_history_for_a_short_listing_without_touching_portal_bars(tmp_path):
    w = world()
    cfg = config()
    cfg["universe"]["psx"]["min_bars"] = 55  # the portal gives 52 days: every stock counts as "short"
    sess = w.session()

    def yahoo(call):
        sym = re.search(r"chart/(TST\d\d)\.KA", call["url"]).group(1)
        if sym == "TST00":
            return ok(yahoo_json(w.dates, [w.rows[d][sym]["close"] for d in w.dates]))
        if sym == "TST01":  # a source that adjusts differently: it must be ignored, not spliced in as a cliff
            return ok(yahoo_json(w.dates, [2 * w.rows[d][sym]["close"] for d in w.dates]))
        return FakeResponse(404, b"{}")

    sess.get(r"finance/chart/TST\d\d\.KA", yahoo)
    md, _, _ = go(w, HistoryStore(tmp_path), cfg=cfg, session=sess)
    bars0 = md.bars[make_id("psx", "TST00")]
    assert len(bars0) == len(w.dates)  # the portal's days plus the older days only Yahoo had
    portal_part = bars0.loc[pd.Timestamp(window(w)[0]):]
    assert portal_part["high"].tolist() == pytest.approx([w.rows[d]["TST00"]["high"] for d in window(w)])  # still the portal's own highs
    assert len(md.bars[make_id("psx", "TST01")]) == len(window(w))


# ------------------------------------------------------------------------------------------------ units
def row(sym, close, ldcp=None, volume=1000.0):
    return {"symbol": sym, "ldcp": ldcp, "open": close, "high": close, "low": close, "close": close, "volume": volume}


@pytest.mark.parametrize("prev, ldcp, event", [
    (100.0, 100.0, False), (100.0, 100.3, False), (100.0, 100.6, True),
    (1000.0, 1003.0, False), (1000.0, 1006.0, True),
    (1.0, 1.005, False), (1.0, 1.02, True),
    (100.0, 1000.0, False), (100.0, 5.0, False),  # absurd ratios are data errors, not corporate actions
])
def test_block_event_tolerance(prev, ldcp, event):
    blk = P.Block({"AAA"}, {"AAA": prev})
    blk.add_day(dt.date(2026, 9, 1), [row("AAA", prev, ldcp=ldcp)])
    assert bool(blk.events) is event


@pytest.mark.parametrize("prev, ldcp, close, event", [
    (71.41, 7.14, 8.14, True),  # a 10-for-1 split: the two-decimal LDCP lands just under a tenth of the old close (this one was missed once)
    (17.70, 1.77, 1.65, True),  # exactly a tenth
    (4.86, 97.20, 83.91, True),  # a 1-for-20 consolidation
    (100.0, 5.0, 5.2, True),  # 20-for-1
    (100.0, 1000.0, 990.0, True),  # 1-for-10
    (100.0, 10.0, 100.0, False),  # LDCP says "split" but the day traded at the old price: one bad row, not an event
    (100.0, 10.0, None, False),
    (100.0, 0.5, 0.5, False),  # a factor of 1/200 is outside anything a company does
    (100.0, 90.0, 55.0, True),  # a modest factor needs no corroboration (a dividend or a small bonus)
])
def test_a_big_corporate_action_is_believed_only_when_the_days_close_agrees(prev, ldcp, close, event):
    assert (P.corporate_factor(prev, ldcp, close) is not None) is event
    if event:
        blk = P.Block({"AAA"}, {"AAA": prev})
        blk.add_day(dt.date(2026, 9, 1), [{**row("AAA", close), "ldcp": ldcp}])
        assert set(blk.events) == {"AAA"} and blk.events["AAA"][0][1] == pytest.approx(ldcp / prev)


def test_block_junction_believes_a_split_only_with_a_close_to_back_it():
    syms = [f"S{i:02d}" for i in range(24)]

    def block():
        blk = P.Block(set(syms))
        for day, px in ((dt.date(2026, 9, 1), 100.0), (dt.date(2026, 9, 2), 101.0)):
            blk.add_day(day, [row(s, px, ldcp=px) for s in syms])
        return blk

    def edges(split_close):
        e = {s: {"day": "2026-09-03", "ldcp": 101.0, "close": 101.0, "volume": 1000.0} for s in syms}
        e["S05"] = {"day": "2026-09-03", "ldcp": 10.1, "close": split_close, "volume": 1000.0}  # LDCP of the first stored day: a tenth of the older block's last close
        return e

    frames, recs = block().build(edges=edges(10.6), junction_date=dt.date(2026, 9, 3))
    assert recs == [{"date": "2026-09-03", "symbol": "S05", "factor": 0.1}]
    assert frames[make_id("psx", "S05")]["close"].tolist() == pytest.approx([10.0, 10.1])
    _, none = block().build(edges={s: {**e, "close": None} for s, e in edges(10.6).items()}, junction_date=dt.date(2026, 9, 3))  # nothing to corroborate it with
    assert none == []
    _, bad = block().build(edges=edges(101.0), junction_date=dt.date(2026, 9, 3))  # the first stored day traded at the old price
    assert bad == []


def test_a_ten_for_one_split_is_adjusted_whichever_way_the_portal_rounds_its_last_day_price(tmp_path):
    base = world()
    syms = [f"TST{i:02d}" for i in range(2, 14)]
    days = {s: base.dates[-6 + 2 * (i % 3)] for i, s in enumerate(syms)}  # four splits a day: fewer than a quarter of the symbols, so it is not mistaken for a missed day
    w = world(bonus={(s, d): 0.1 for s, d in days.items()})
    ratios = [w.rows[d][s]["ldcp"] / w.rows[w.dates[w.dates.index(d) - 1]][s]["close"] for s, d in days.items()]
    assert min(ratios) <= 0.1 < max(ratios)  # both sides of the boundary are in play

    store = HistoryStore(tmp_path)
    w.cutoff = w.dates[-8]
    go(w, store, now=at(w.cutoff))
    w.cutoff = None
    md, _, _ = go(w, store)
    for s in syms:
        bars = md.bars[make_id("psx", s)]
        assert bars["close"].pct_change().abs().max() < 0.10, s
        assert raw_series(w, s).pct_change().abs().max() > 0.8
    assert sorted(a["symbol"] for a in state(store)["adjustments"]) == sorted(syms)


def test_block_storm_guard_needs_a_big_enough_crowd():
    syms = [f"S{i:02d}" for i in range(30)]
    blk = P.Block(set(syms), {s: 100.0 for s in syms})
    blk.add_day(dt.date(2026, 9, 1), [row(s, 102.0, ldcp=102.0) for s in syms])  # all 30 differ from the stored close
    assert blk.events == {} and "missed day" in blk.notes[0]

    few = syms[:3]
    blk = P.Block(set(syms), {s: 100.0 for s in syms})
    blk.add_day(dt.date(2026, 9, 1), [row(s, 102.0, ldcp=(102.0 if s in few else 100.0)) for s in syms])
    assert set(blk.events) == set(few) and blk.notes == []

    small = syms[:10]  # with fewer than 20 symbols there is no crowd to tell a missed day from real events
    blk = P.Block(set(small), {s: 100.0 for s in small})
    blk.add_day(dt.date(2026, 9, 1), [row(s, 102.0, ldcp=102.0) for s in small])
    assert len(blk.events) == 10


def test_block_junction_finds_an_event_between_two_blocks():
    syms = [f"S{i:02d}" for i in range(24)]
    blk = P.Block(set(syms))
    for day, px in ((dt.date(2026, 9, 1), 100.0), (dt.date(2026, 9, 2), 101.0)):
        blk.add_day(day, [row(s, px, ldcp=px) for s in syms])
    edge = {s: {"day": "2026-09-03", "ldcp": 101.0, "close": 101.0, "volume": 1000.0} for s in syms}
    edge["S05"]["ldcp"] = 80.8
    frames, recs = blk.build(edges=edge, junction_date=dt.date(2026, 9, 3))
    assert recs == [{"date": "2026-09-03", "symbol": "S05", "factor": 0.8}]
    assert frames[make_id("psx", "S05")]["close"].tolist() == pytest.approx([80.0, 80.8])
    assert frames[make_id("psx", "S00")]["close"].tolist() == [100.0, 101.0]

    blk2 = P.Block(set(syms))
    for day, px in ((dt.date(2026, 9, 1), 100.0), (dt.date(2026, 9, 2), 101.0)):
        blk2.add_day(day, [row(s, px, ldcp=px) for s in syms])
    frames2, recs2 = blk2.build(edges={s: {"day": "2026-09-03", "ldcp": 90.9, "close": 90.9, "volume": 1000.0} for s in syms}, junction_date=dt.date(2026, 9, 3))  # every symbol differs: a missed day
    assert recs2 == [] and any("across the join" in n for n in blk2.notes)
    assert frames2[make_id("psx", "S05")]["close"].tolist() == [100.0, 101.0]


def test_block_ignores_symbols_outside_the_universe_and_bad_rows():
    blk = P.Block({"AAA"})
    blk.add_day(dt.date(2026, 9, 1), [row("AAA", 10.0, ldcp=10.0), row("ZZZ", 5.0), row("AAA2", 0.0), {"symbol": "AAA", "close": None}])
    frames, _ = blk.build()
    assert list(frames) == [make_id("psx", "AAA")] and len(frames[make_id("psx", "AAA")]) == 1


def test_apply_events_scales_prices_and_bonus_volume_only_before_the_event():
    idx = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"])
    df = pd.DataFrame({"open": [10, 10, 8], "high": [11, 11, 9], "low": [9, 9, 7], "close": [10, 10, 8], "volume": [100, 100, 120.0]}, index=idx)
    out = P.apply_events(df, [(pd.Timestamp("2026-09-03"), 0.8)])
    assert out["close"].tolist() == pytest.approx([8, 8, 8]) and out["high"].tolist() == pytest.approx([8.8, 8.8, 9])
    assert out["volume"].tolist() == pytest.approx([125, 125, 120])
    cash = P.apply_events(df, [(pd.Timestamp("2026-09-03"), 0.97)])  # an ordinary dividend changes prices, not share volume
    assert cash["close"].tolist() == pytest.approx([9.7, 9.7, 8]) and cash["volume"].tolist() == [100, 100, 120]
    cons = P.apply_events(df, [(pd.Timestamp("2026-09-03"), 20.0)])  # a 1-for-20 consolidation: dearer shares, fewer of them
    assert cons["close"].tolist() == pytest.approx([200, 200, 8]) and cons["volume"].tolist() == pytest.approx([5, 5, 120])
    assert P.apply_events(df, []) is df and P.apply_events(None, [(pd.Timestamp("2026-09-03"), 0.8)]) is None
    assert df["close"].tolist() == [10, 10, 8]  # the input is not modified


def _stock(closes, volume=100.0):
    n = len(closes)
    return normalise_bars(pd.DataFrame({"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes], "close": closes, "volume": [volume] * n}, index=pd.bdate_range("2026-01-05", periods=n)))


def test_heal_splits_rescales_the_history_before_an_unadjusted_ten_for_one():
    px = [100.0 + (i % 5) for i in range(40)]
    px = [p / 10 if i >= 25 else p for i, p in enumerate(px)]  # the ratio is 10/104: a tenth, with the day's own move on top
    df = _stock(px)
    bars = {"PSX:AAA": df, "PSX:BBB": _stock([50.0 + (i % 3) for i in range(40)])}
    other = bars["PSX:BBB"].copy()
    recs = P.heal_splits(bars)
    assert recs == [{"date": df.index[25].strftime("%Y-%m-%d"), "symbol": "AAA", "factor": 0.1, "inferred": True}]
    fixed = bars["PSX:AAA"]
    assert fixed["close"].iloc[:25].tolist() == pytest.approx([p / 10 for p in px[:25]]) and fixed["close"].iloc[25:].tolist() == px[25:]
    assert fixed["volume"].iloc[:25].tolist() == pytest.approx([1000.0] * 25) and fixed["volume"].iloc[25:].tolist() == [100.0] * 15  # a split means ten times the shares
    assert fixed["close"].pct_change().abs().max() < 0.05
    pd.testing.assert_frame_equal(bars["PSX:BBB"], other)  # nothing else is touched
    assert P.heal_splits(bars) == []  # idempotent: the history is consistent now


def test_heal_splits_handles_a_consolidation_and_an_odd_ratio():
    px = [4.8 + 0.01 * (i % 4) for i in range(40)]
    px = [p * 17.3 if i >= 20 else p for i, p in enumerate(px)]  # 1-for-20, with the day's own move: 17.3x
    bars = {"PSX:AAA": _stock(px, volume=1000.0)}
    recs = P.heal_splits(bars)
    assert [(r["symbol"], r["factor"]) for r in recs] == [("AAA", 20.0)]
    assert bars["PSX:AAA"]["close"].iloc[:20].tolist() == pytest.approx([p * 20 for p in px[:20]])
    assert bars["PSX:AAA"]["volume"].iloc[:20].tolist() == pytest.approx([50.0] * 20) and bars["PSX:AAA"]["volume"].iloc[20] == 1000.0

    odd = [100.0] * 20 + [15.0] * 20  # not near any round ratio: keep the series continuous instead of guessing
    bars = {"PSX:AAA": _stock(odd)}
    recs = P.heal_splits(bars)
    assert [r["factor"] for r in recs] == [0.15]
    assert bars["PSX:AAA"]["close"].tolist() == pytest.approx([15.0] * 40)


def test_heal_splits_leaves_bad_prints_big_real_moves_and_unconfirmed_edges_alone():
    spike = [100.0] * 40
    spike[20] = 10.0  # one bad print that comes straight back
    pop = [100.0] * 20 + [150.0] * 20  # +50% in a day: wild, but a market can do it
    crash = [100.0] * 20 + [30.0] * 20  # -70%: still not a split
    late = [100.0] * 38 + [10.0, 10.0]  # looks like a split, but only two bars show it: wait for the next run
    short = [100.0, 10.0, 10.0]
    bars = {f"PSX:{n}": _stock(c) for n, c in {"SPK": spike, "POP": pop, "CRS": crash, "LATE": late, "SHORT": short}.items()}
    bars["PSX:KSE100"] = _stock([100.0] * 20 + [10.0] * 20)  # indices are never rescaled
    before = {k: v.copy() for k, v in bars.items()}
    assert P.heal_splits(bars) == []
    for k, v in before.items():
        pd.testing.assert_frame_equal(bars[k], v)


def test_snap_factor_prefers_the_round_ratio_a_company_would_use():
    assert P.snap_factor(0.114) == 0.1 and P.snap_factor(0.0917) == 0.1 and P.snap_factor(0.19) == 0.2
    assert P.snap_factor(17.27) == 20.0 and P.snap_factor(2.1) == 2.0
    assert P.snap_factor(0.15) == 0.15 and P.snap_factor(0.012) == pytest.approx(0.01)


def test_a_split_the_old_rule_missed_is_repaired_in_stored_history_on_the_next_run(tmp_path):
    w = world()
    store = HistoryStore(tmp_path)
    md1, _, _ = go(w, store)
    clean = md1.bars[IID]
    cut = clean.index[30]
    broken = clean.copy()
    broken.loc[broken.index < cut, ["open", "high", "low", "close"]] *= 10.0  # what an unadjusted 10-for-1 split leaves behind
    broken.loc[broken.index < cut, "volume"] /= 10.0
    saved = store.load_bars("psx")
    saved[IID] = broken
    store.save_bars("psx", saved)
    assert store.load_bars("psx")[IID]["close"].pct_change().abs().max() > 0.8

    md2, sess, _ = go(w, store)
    assert hist_days(sess) == []  # nothing new to fetch: this is purely the repair
    assert md2.bars[IID]["close"].tolist() == pytest.approx(clean["close"].tolist(), rel=1e-6)
    assert md2.bars[IID]["volume"].tolist() == pytest.approx(clean["volume"].tolist(), rel=1e-6)
    assert store.load_bars("psx")[IID]["close"].pct_change().abs().max() < 0.10  # and it was saved
    adj = state(store)["adjustments"]
    assert [(a["symbol"], a["date"], a["factor"], a.get("inferred")) for a in adj] == [(SYM, cut.strftime("%Y-%m-%d"), 0.1, True)]
    assert any("1 stock split(s)" in n and SYM in n for n in md2.extras["notes"])

    md3, _, _ = go(w, store)
    assert state(store)["adjustments"] == adj and not any("stock split" in n for n in md3.extras["notes"])  # once is enough


# ------------------------------------------------------------------------------------------------ joining blocks of days
OHLCV = ("open", "high", "low", "close", "volume")


def same_history(a, b, syms):
    for sym in syms:
        x, y = a.bars[make_id("psx", sym)], b.bars[make_id("psx", sym)]
        assert x.index.equals(y.index), sym
        for col in OHLCV:
            assert x[col].tolist() == pytest.approx(y[col].tolist(), rel=1e-8), (sym, col)


def test_older_days_are_scaled_by_the_events_that_followed_them(tmp_path):
    d = world(days=90).dates
    events = {
        ("TST05", d[-10]): 0.1,  # a 10-for-1 split in the newest block
        ("TST06", d[-15]): 1 / 1.25,  # a bonus issue there
        ("TST07", d[-12]): 0.97, ("TST07", d[-20]): 0.95,  # two dividends: together 0.92, so nearly a "bonus" by size, yet not one share more
        ("TST08", d[-50]): 0.5,  # an event inside the older block itself
        ("TST09", d[-60]): 0.8,  # one exactly at the second seam
    }
    w = world(days=90, bonus=events)
    cfg = config(years=0.5, max_requests_per_run=34)  # 30 days a run (plus the portal's list, market watch, probe and token page): three runs reach the first day
    store = HistoryStore(tmp_path / "steps")
    md = None
    for _ in range(3):
        md, _, _ = go(w, store, cfg=cfg)
    one_shot, _, _ = go(w, HistoryStore(tmp_path / "once"), cfg=config(years=0.5))

    syms = [x.symbol for x in stocks(one_shot)]
    assert len(md.bars[IID]) == 90
    same_history(md, one_shot, syms)  # however the days arrive, the history is the same
    for sym in ("TST05", "TST06", "TST07", "TST08", "TST09"):
        bars = md.bars[make_id("psx", sym)]
        assert bars["close"].pct_change().abs().max() < 0.10, sym  # no cliff at a seam or at an event
    assert raw_series(w, "TST05").pct_change().abs().max() > 0.8
    other = md.bars[make_id("psx", "TST04")]
    assert other["close"].tolist() == pytest.approx(raw_series(w, "TST04").loc[other.index].tolist())  # nothing to adjust, nothing rescaled
    dividends = md.bars[make_id("psx", "TST07")]
    assert dividends["volume"].iloc[0] == pytest.approx(w.rows[d[0]]["TST07"]["volume"], rel=3e-3)  # dividends never restate share volume, however many pile up

    st = state(store)
    assert st["history_version"] == P.HISTORY_VERSION and "edge_ldcp" not in st
    assert st["edges"]["TST03"]["day"] == d[0].isoformat() == st["first_date"]
    assert st["edges"]["TST03"]["close"] == w.rows[d[0]]["TST03"]["close"]


def test_a_walk_the_portal_gives_up_on_keeps_the_days_it_did_give(tmp_path):
    w = world(days=90)
    d = w.dates
    cut = d[-40]
    real = w.day_rows

    def flaky(day):
        if day < cut:
            raise requests.ConnectionError("the portal went away")
        return real(day)

    w.day_rows = flaky
    store = HistoryStore(tmp_path)
    md1, _, _ = go(w, store, cfg=config(years=0.5))
    assert len(md1.bars[IID]) == 40 and state(store)["first_date"] == cut.isoformat()  # the 40 days before the failure were kept
    portal = next(s for s in md1.status if s.source == "psx_portal")
    assert not portal.ok and "history update stopped" in portal.message and "kept 40 day(s)" in portal.message

    w.day_rows = real
    md2, _, _ = go(w, store, cfg=config(years=0.5))
    one_shot, _, _ = go(w, HistoryStore(tmp_path / "once"), cfg=config(years=0.5))
    same_history(md2, one_shot, [x.symbol for x in stocks(one_shot)])


def test_a_store_from_before_the_seam_rule_is_rebuilt_from_the_portal(tmp_path):
    d = world(days=90).dates
    w = world(days=90, bonus={("TST05", d[-10]): 0.2})
    store = HistoryStore(tmp_path)
    cfg = config(years=0.34)  # the whole simulated world is the target, so one run completes the rebuild
    clean, _, _ = go(w, store, cfg=cfg)
    old = state(store)
    for k in ("history_version", "edges"):
        old.pop(k)
    store.save_json("psx_state", old)  # what a store built by the earlier code looks like ...
    saved = store.load_bars("psx")
    broken = saved[IID].copy()
    broken.loc[broken.index < pd.Timestamp(d[-40]), ["open", "high", "low", "close"]] *= 5.0  # ... with its older block at the wrong scale
    saved[IID] = broken
    store.save_bars("psx", saved)

    md, sess, _ = go(w, store, cfg=cfg)
    assert len(hist_days(sess)) >= 80  # every day is asked for again
    same_history(md, clean, [x.symbol for x in stocks(clean)])
    assert state(store)["history_version"] == P.HISTORY_VERSION and "edges" in state(store)
    assert any("being rebuilt" in n for n in md.extras["notes"])
    assert md.bars[make_id("psx", "KSE100")].equals(clean.bars[make_id("psx", "KSE100")])  # the indices are not part of it

    again, s2, _ = go(w, store, cfg=cfg)
    assert hist_days(s2) == [] and not any("rebuilt" in n for n in again.extras["notes"])  # once is enough


def test_a_rebuild_leaves_the_old_bars_alone_when_the_portal_hands_over_no_days(tmp_path):
    w = world(days=90)
    store = HistoryStore(tmp_path)
    go(w, store, cfg=config(years=0.5))
    old = state(store)
    for k in ("history_version", "edges"):
        old.pop(k)
    store.save_json("psx_state", old)
    before = store.load_bars("psx")[IID].copy()

    w.missing_days = set(w.dates)  # every day table comes back empty: an outage, not a market
    md, _, _ = go(w, store, cfg=config(years=0.5))
    pd.testing.assert_frame_equal(md.bars[IID][list(OHLCV)], before[list(OHLCV)], check_freq=False)
    st = state(store)
    assert "history_version" not in st and st["first_date"] == w.dates[0].isoformat()  # still waiting for a run that can rebuild
    assert any("too few days" in n for n in md.extras["notes"])


def test_carry_factors_read_the_scale_off_the_first_stored_bar():
    idx = pd.bdate_range("2026-09-01", periods=4)
    bars = {
        "PSX:AAA": normalise_bars(pd.DataFrame({"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 2000.0}, index=idx)),  # dividends: price scaled, shares not
        "PSX:BBB": normalise_bars(pd.DataFrame({"open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 20000.0}, index=idx)),  # a 10-for-1 split since: price a tenth, ten times the shares
        "PSX:CCC": normalise_bars(pd.DataFrame({"open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 1000.0}, index=idx)),
        "PSX:DDD": normalise_bars(pd.DataFrame({"open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 1000.0}, index=idx)),
        "PSX:KSE100": normalise_bars(pd.DataFrame({"open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 1000.0}, index=idx)),
    }
    day = idx[0].strftime("%Y-%m-%d")
    edges = {
        "AAA": {"day": day, "close": 12.0, "volume": 2000.0, "ldcp": 12.1},
        "BBB": {"day": day, "close": 50.0, "volume": 2000.0, "ldcp": 50.5},
        "CCC": {"day": idx[1].strftime("%Y-%m-%d"), "close": 5.0, "volume": 1000.0, "ldcp": 5.0},  # remembered a different bar than the first stored one
        "KSE100": {"day": day, "close": 50.0, "volume": 1000.0, "ldcp": 50.0},
    }
    got = P.carry_factors(bars, edges)
    assert set(got) == {"AAA", "BBB"}  # no remembered edge (DDD), a different bar (CCC) and an index (KSE100) get nothing
    assert got["AAA"] == pytest.approx((10.0 / 12.0, 1.0))
    assert got["BBB"] == pytest.approx((0.1, 0.1))


def test_audit_jumps_lists_the_biggest_one_day_move_of_each_stock_that_has_one():
    idx = pd.bdate_range("2026-09-01", periods=6)

    def frame(closes):
        return normalise_bars(pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0}, index=idx))

    bars = {"PSX:OK": frame([10, 10.5, 10, 10.2, 10, 10.1]), "PSX:CUT": frame([100, 101, 10, 10.2, 10.1, 10]), "PSX:UP": frame([1, 1, 1, 20, 21, 20]), "PSX:KSE100": frame([100, 100, 1, 1, 1, 1])}
    got = P.audit_jumps(bars)
    assert [(s, d, round(r, 2)) for s, d, r in got] == [("UP", idx[3].strftime("%Y-%m-%d"), 20.0), ("CUT", idx[2].strftime("%Y-%m-%d"), 0.1)]


def test_title_sector():
    assert P.title_sector("COMMERCIAL BANKS") == "Commercial Banks"
    assert P.title_sector("OIL & GAS EXPLORATION COMPANIES") == "Oil & Gas Exploration Companies"
    assert P.title_sector("LEATHER AND TANNERIES") == "Leather and Tanneries"
    assert P.title_sector("PAPER, BOARD & PACKAGING") == "Paper, Board & Packaging"
    assert P.title_sector("  ") == "" and P.title_sector("") == ""


def _bars(dates, closes):
    return normalise_bars(pd.DataFrame({"open": closes, "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes], "close": closes, "volume": [100.0] * len(closes)}, index=pd.to_datetime(dates)))


def test_bootstrap_state_rebuilds_the_date_range_from_stored_bars():
    d = pd.bdate_range("2026-08-03", periods=10)
    bars = {make_id("psx", "AAA"): _bars(d, [10.0] * 10), make_id("psx", "BBB"): _bars(d[2:], [5.0] * 8), make_id("psx", "KSE100"): _bars(pd.bdate_range("2026-01-01", periods=300), [40000.0] * 300)}
    st: dict = {}
    P.bootstrap_state(st, bars)
    assert st == {"last_date": "2026-08-14", "first_date": "2026-08-03"}  # the index's longer history does not count
    keep = {"last_date": "2026-08-10"}
    P.bootstrap_state(keep, bars)
    assert keep == {"last_date": "2026-08-10"}
    empty: dict = {}
    P.bootstrap_state(empty, {})
    assert empty == {}


def test_splice_extends_both_ends_and_never_rewrites_a_stored_day():
    d = pd.bdate_range("2026-01-05", periods=30)
    old = _bars(d[10:20], [100.0] * 10)
    extra = _bars(d, [100.5] * 30)
    out, why = P.splice(old, extra)
    assert why == "" and len(out) == 30
    assert out.loc[d[10:20], "close"].tolist() == [100.0] * 10  # shared days keep the stored value
    assert out.loc[d[0], "close"] == 100.5 and out.loc[d[-1], "close"] == 100.5


def test_splice_refuses_a_source_that_disagrees_or_does_not_join():
    d = pd.bdate_range("2026-01-05", periods=30)
    old = _bars(d[10:20], [100.0] * 10)
    out, why = P.splice(old, _bars(d, [200.0] * 30))
    assert out is old and "disagree" in why

    few = _bars(d[10:13], [100.0] * 3)  # too few shared days to compare: the two ends must at least join
    out, why = P.splice(few, _bars(d[:10], [50.0] * 10))
    assert out is few and "do not join" in why
    out, why = P.splice(few, _bars(d[:5], [100.0] * 5)[0:0])
    assert out is few and why == "no data"
    out, why = P.splice(few, _bars(d[10:13], [100.0] * 3))  # the same days again: genuinely nothing new
    assert out is few and why == "nothing to add"
    out, why = P.splice(few, _bars(d[:10], [95.0] * 10))
    assert why == "" and len(out) == 13

    assert P.splice(old, None) == (old, "no data")
    assert P.splice(None, _bars(d, [1.0] * 30))[1] == ""


def test_looks_unchanged_spots_a_repeated_session():
    d = pd.bdate_range("2026-08-03", periods=3)
    syms = [f"S{i:02d}" for i in range(25)]
    bars = {make_id("psx", s): _bars(d, [10.0, 10.0, 10.0]) for s in syms}
    rows = [{"symbol": s, "close": 10.0, "volume": 100.0} for s in syms]
    assert P.looks_unchanged(rows, bars) is True
    moved = [{**r, "close": 10.5} if i < 6 else r for i, r in enumerate(rows)]  # 6 of 25 changed: a real session
    assert P.looks_unchanged(moved, bars) is False
    assert P.looks_unchanged(rows[:5], bars) is False  # too few to judge
