"""The forex fetcher against the simulated Yahoo, ECB and daily-snapshot services in ``tests/fake_fx.py``."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from acumen.config import load_config
from acumen.fetch import fx as F
from acumen.fetch.http import Client, ParseError
from acumen.store import HistoryStore
from tests.fake_fx import FxWorld
from tests.fakenet import FakeClock, FakeSession

UTC = dt.timezone.utc
THU, FRI, SAT, SUN, MON, TUE = (dt.date(2026, 10, d) for d in (8, 9, 10, 11, 12, 13))


# ------------------------------------------------------------------------------------------------ helpers
def config(**fx):
    cfg = load_config("/nonexistent.yml")
    cfg["data"]["fx"].update({"history_years": 3, **fx})
    cfg["universe"]["fx"].update({"min_bars": 100})
    return cfg


def new_client(session=None) -> Client:
    clock = FakeClock()
    return Client("TestAgent/1.0", session=session if session is not None else FakeSession(), sleep=clock.sleep, clock=clock, default_interval=0.0)


def go(world: FxWorld, store, *, cfg=None, session=None):
    sess = session or world.session()
    client = new_client(sess)
    md = F.fetch_fx(cfg or config(), store, client, log=lambda m: None, now=world.now())
    return md, sess, client


def sources(md) -> dict[str, bool]:
    return {s.source: s.ok for s in md.status}


def bars(md, sym) -> pd.DataFrame:
    return md.bars[f"FX:{sym}"]


def closes(md, sym) -> pd.Series:
    return bars(md, sym)["close"]


def approx(df: pd.DataFrame) -> pd.Series:
    """The ``approx_hl`` flags; a frame whose bars are all true highs and lows may not carry the column at all."""
    return df["approx_hl"].astype(bool) if "approx_hl" in df.columns else pd.Series(False, index=df.index)


def truth(world, sym) -> pd.Series:
    return world.final(sym)


def yahoo_calls(sess):
    return [c for c in sess.calls if "finance.yahoo.com" in c["url"]]


# ------------------------------------------------------------------------------------------------ the whole pipeline
def test_first_run_builds_every_pair_from_yahoo_and_stores_only_finished_days(tmp_path):
    w = FxWorld().at(FRI)
    store = HistoryStore(tmp_path)
    md, sess, _ = go(w, store)

    syms = {i.symbol for i in md.instruments.values()}
    assert len(syms) == 51 and {"EURUSD", "GBPJPY", "CHFJPY", "USDTRY", "USDCNH", "USDPKR", "XAUUSD", "XAGUSD", "EURPKR", "AEDPKR", "SARPKR"} <= syms
    eur = closes(md, "EURUSD")
    assert eur.index[-1] == pd.Timestamp(THU)  # Friday's bar is still forming at 00:30 UTC and is not stored
    assert eur.index[0] >= pd.Timestamp(dt.date(2023, 10, 1)) and len(eur) > 700
    assert eur.tolist() == pytest.approx(truth(w, "EURUSD").loc[eur.index].tolist())
    assert (bars(md, "EURUSD")["high"] >= bars(md, "EURUSD")[["open", "close"]].max(axis=1)).all()
    assert closes(md, "XAUUSD").iloc[-1] == pytest.approx(truth(w, "XAUUSD").iloc[-1]) and closes(md, "USDPKR").iloc[-1] == pytest.approx(truth(w, "USDPKR").iloc[-1])

    inst = md.instruments
    assert inst["FX:EURUSD"].meta["group"] == "major" and inst["FX:EURUSD"].tradable and inst["FX:EURUSD"].name == "EUR/USD"
    assert inst["FX:EURGBP"].meta["group"] == "minor" and inst["FX:USDTRY"].meta["group"] == "exotic"
    assert inst["FX:XAUUSD"].kind == "metal" and inst["FX:XAUUSD"].name == "Gold / USD" and inst["FX:XAUUSD"].meta["group"] == "metal"
    assert inst["FX:USDPKR"].tradable is False and inst["FX:USDPKR"].meta["group"] == "pkr" and "synthetic" not in inst["FX:USDPKR"].meta
    assert inst["FX:EURPKR"].tradable is False and inst["FX:EURPKR"].meta["synthetic"] is True and inst["FX:AEDPKR"].meta["peg"] == 3.6725

    pkr, usdjpy = closes(md, "USDPKR"), closes(md, "USDJPY")
    assert closes(md, "EURPKR").tolist() == pytest.approx((pkr * closes(md, "EURUSD")).tolist())
    assert closes(md, "AEDPKR").tolist() == pytest.approx((pkr / 3.6725).tolist())
    assert closes(md, "SARPKR").tolist() == pytest.approx((pkr / 3.75).tolist())
    assert closes(md, "JPYPKR").tolist() == pytest.approx((pkr / usdjpy).tolist())  # a dollar pair quoted the other way round
    assert bool(bars(md, "EURPKR")["approx_hl"].all())

    q = md.quotes["FX:EURUSD"]  # the live price against the close of the day before it, not against the forming bar
    assert q["price"] == pytest.approx(w.close("EURUSD").loc[pd.Timestamp(FRI)]) and q["prev_close"] == pytest.approx(truth(w, "EURUSD").iloc[-1])
    assert q["chg_pct"] == pytest.approx((q["price"] / q["prev_close"] - 1) * 100) and q["ts"] == "2026-10-09T00:30:00Z"
    assert "FX:EURPKR" not in md.quotes

    assert sources(md) == {"yahoo_fx": True}
    loaded = store.load_bars("fx")
    assert "FX:EURUSD" in loaded and "FX:EURPKR" not in loaded  # derived pairs are rebuilt from their legs, not stored
    st = store.load_json("fx_state")
    assert st["src"]["EURUSD"] == "yahoo" and st["fallback_from"] == {}
    assert md.extras["history_pct"] > 90 and md.extras["notes"] == []


def test_the_next_day_asks_for_a_month_not_the_history_and_a_second_run_asks_for_nothing(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    md1, _, _ = go(w, store)
    assert closes(md1, "EURUSD").index[-1] == pd.Timestamp(dt.date(2026, 10, 7))

    w.at(FRI)
    md2, s2, _ = go(w, store)
    assert {c["params"]["range"] for c in yahoo_calls(s2)} == {"1mo"}
    eur = closes(md2, "EURUSD")
    assert len(eur) == len(closes(md1, "EURUSD")) + 1 and eur.index[-1] == pd.Timestamp(THU)
    assert eur.tolist() == pytest.approx(truth(w, "EURUSD").loc[eur.index].tolist())
    pd.testing.assert_series_equal(eur.iloc[:-1], closes(md1, "EURUSD"), check_freq=False, check_index_type=False)

    _, s3, _ = go(w, store)  # later the same day
    assert yahoo_calls(s3) == []


def test_a_weekend_run_picks_up_friday_and_the_next_run_has_nothing_to_do(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    go(w, store)
    md, _, _ = go(w.at(SAT), store)
    assert closes(md, "EURUSD").index[-1] == pd.Timestamp(FRI)
    q = md.quotes["FX:EURUSD"]  # the market has been shut since Friday evening: that is still today's price, against Thursday's close
    assert q["ts"].startswith("2026-10-09T21:59") and q["prev_close"] == pytest.approx(truth(w, "EURUSD").loc[pd.Timestamp(THU)])
    _, s2, _ = go(w.at(SUN), store)
    assert yahoo_calls(s2) == []


def test_friday_evening_after_the_new_york_close_the_days_bar_is_final(tmp_path):
    w = FxWorld().at(FRI, hour=22, minute=30)
    md, _, _ = go(w, HistoryStore(tmp_path))
    assert closes(md, "EURUSD").index[-1] == pd.Timestamp(FRI)
    w2 = FxWorld().at(FRI, hour=21, minute=30)
    md2, _, _ = go(w2, HistoryStore(tmp_path / "early"))
    assert closes(md2, "EURUSD").index[-1] == pd.Timestamp(THU)


# ------------------------------------------------------------------------------------------------ outages and fallbacks
def test_without_yahoo_the_ecb_fixings_build_the_g8_and_most_exotics_and_yahoo_replaces_them_when_it_is_back(tmp_path):
    w = FxWorld().at(FRI)
    w.down = {"yahoo"}
    store = HistoryStore(tmp_path)
    md, sess, _ = go(w, store)

    syms = {i.symbol for i in md.instruments.values()}
    assert {"EURUSD", "GBPJPY", "USDTRY", "USDINR", "USDCZK", "CHFJPY"} <= syms
    assert not {"USDPKR", "XAUUSD", "XAGUSD", "USDCNH", "EURPKR", "AEDPKR"} & syms  # not on the ECB's list, and PKR crosses need the rupee
    eur = bars(md, "EURUSD")
    assert bool(approx(eur).all()) and eur.index[-1] == pd.Timestamp(THU)
    assert eur.index.to_series().diff().dropna().max() <= pd.Timedelta(days=4)  # daily, not the weekly summary a long range would get
    assert (eur["close"] / truth(w, "EURUSD").loc[eur.index] - 1).abs().max() < 0.005  # a fixing is not a close, but it is close
    assert sources(md) == {"yahoo_fx": False, "frankfurter": True}
    assert any("ECB's daily fixings" in n for n in md.extras["notes"])
    windows = [(pd.Timestamp(c["url"].split("/v1/")[1].split("..")[0]), pd.Timestamp(c["url"].split("..")[1])) for c in sess.calls if "frankfurter" in c["url"]]
    assert all((b - a).days <= 89 for a, b in windows) and all(windows[i][1] + pd.Timedelta(days=1) == windows[i + 1][0] for i in range(len(windows) - 1))
    assert store.load_json("fx_state")["fallback_from"]["EURUSD"] == str(eur.index[0].date())

    w.down = set()  # Yahoo comes back: its bars replace every bar the fallback supplied
    md2, s2, _ = go(w, store)
    eur2 = bars(md2, "EURUSD")
    assert eur2["close"].tolist() == pytest.approx(truth(w, "EURUSD").loc[eur2.index].tolist())
    assert not bool(approx(eur2).any()) and store.load_json("fx_state")["fallback_from"] == {}
    assert len(md2.instruments) == 51 and sources(md2) == {"yahoo_fx": True}


def test_a_short_outage_is_bridged_by_the_ecb_and_only_those_days_are_replaced_later(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    md1, _, _ = go(w, store)
    old = closes(md1, "EURUSD")

    w.at(TUE)
    w.down = {"yahoo"}
    md2, _, _ = go(w, store)
    eur = bars(md2, "EURUSD")
    new_days = eur.index[eur.index > old.index[-1]]
    assert [d.date() for d in new_days] == [dt.date(2026, 10, 8), dt.date(2026, 10, 9), dt.date(2026, 10, 12)]
    assert approx(eur).tolist() == [False] * len(old) + [True] * 3  # the first Yahoo stretch keeps its true highs and lows
    assert eur["close"].iloc[: len(old)].tolist() == pytest.approx(old.tolist())
    assert store.load_json("fx_state")["fallback_from"]["EURUSD"] == "2026-10-08"
    pkr = bars(md2, "USDPKR")  # the rupee and gold are not on the ECB's list: the daily snapshots bridged those three days
    assert pkr.index[-1] == pd.Timestamp(MON) and approx(pkr).tolist()[-3:] == [True] * 3 and sources(md2)["fawaz"] is True
    assert store.load_json("fx_state")["src"]["USDPKR"] == "fawaz" and store.load_json("fx_state")["src"]["EURUSD"] == "frankfurter"

    w.down = set()
    md3, _, _ = go(w, store)
    eur3 = bars(md3, "EURUSD")
    assert eur3["close"].tolist() == pytest.approx(truth(w, "EURUSD").loc[eur3.index].tolist()) and not bool(approx(eur3).any())
    assert closes(md3, "USDPKR").index[-1] == pd.Timestamp(MON)


def test_daily_snapshots_bridge_a_short_gap_when_yahoo_and_the_ecb_are_down(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(MON)
    w.down = {"yahoo", "frankfurter"}
    md, sess, _ = go(w, store)
    for sym in ("EURUSD", "USDPKR", "XAUUSD", "USDTRY"):
        s = closes(md, sym)
        assert s.index[-1] == pd.Timestamp(FRI) and s.iloc[-2:].tolist() == pytest.approx(truth(w, sym).iloc[-2:].tolist())
    assert bool(approx(bars(md, "USDPKR")).iloc[-1]) and not bool(approx(bars(md, "USDPKR")).iloc[-5])
    assert sources(md)["fawaz"] is True and sources(md)["frankfurter"] is False
    assert closes(md, "EURPKR").index[-1] == pd.Timestamp(FRI)  # the synthetic pairs follow their legs
    asked = sorted({c["url"].split("currency-api@")[1][:10] for c in sess.calls if "jsdelivr" in c["url"]})
    assert asked == ["2026-10-08", "2026-10-09"]  # one snapshot per missing day, shared by every pair


def test_a_gap_longer_than_the_snapshots_can_cover_is_left_alone_and_reported(tmp_path):
    w = FxWorld().at(dt.date(2026, 9, 24))
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(TUE)
    w.down = {"yahoo", "frankfurter"}
    md, _, _ = go(w, store)
    assert closes(md, "EURUSD").index[-1] == pd.Timestamp(dt.date(2026, 9, 23))  # nothing patched in: a hole would be worse than a stale series
    assert any("no bars from the last few days" in n and "EURUSD" in n for n in md.extras["notes"])


def test_a_source_that_disagrees_with_stored_history_is_not_used_and_the_next_source_fills_in(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    md1, _, _ = go(w, store)
    w.at(FRI)
    w.scale = {"GBPUSD=X": 2.0}  # Yahoo's GBP/USD is suddenly twice what it was
    md2, _, _ = go(w, store)
    gbp = bars(md2, "GBPUSD")
    assert gbp["close"].max() < 1.5 * truth(w, "GBPUSD").max()
    assert gbp.index[-1] == pd.Timestamp(THU) and bool(approx(gbp).iloc[-1])  # the ECB's fixing bridged the day instead
    assert store.load_json("fx_state")["src"]["GBPUSD"] == "frankfurter"
    assert closes(md2, "EURUSD").index[-1] == pd.Timestamp(THU)  # the rest were fine
    line = next(s for s in md2.status if s.source == "yahoo_fx")
    assert line.ok and "1 failed" in line.message


def test_a_symbol_whose_numbers_disagree_with_stored_history_falls_through_to_its_short_form(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    go(w, store, cfg=config(sources=["yahoo"]))
    w.at(FRI)
    w.scale = {"USDCNH=X": 2.0}  # the long symbol is wrong; the short form CNH=X is right
    md, sess, _ = go(w, store, cfg=config(sources=["yahoo"]))
    cnh = closes(md, "USDCNH")
    assert cnh.index[-1] == pd.Timestamp(THU) and cnh.iloc[-1] == pytest.approx(truth(w, "USDCNH").iloc[-1])
    assert any("/CNH%3DX" in c["url"] for c in yahoo_calls(sess)) and sources(md) == {"yahoo_fx": True}


def test_a_series_that_would_leave_a_hole_in_the_history_is_refused_and_the_next_source_bridges_it(tmp_path):
    w = FxWorld().at(dt.date(2026, 6, 15))
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(FRI)
    w.cap_days = {"EURUSD=X": 30}  # Yahoo answers, but only with the last month: the 100 days in between would be missing
    md, _, _ = go(w, store)
    eur = bars(md, "EURUSD")
    assert eur.index.to_series().diff().dropna().max() <= pd.Timedelta(days=4) and eur.index[-1] == pd.Timestamp(THU)  # no hole
    assert store.load_json("fx_state")["src"]["EURUSD"] == "frankfurter" and store.load_json("fx_state")["src"]["GBPUSD"] == "yahoo"


def test_ecb_fixings_that_disagree_with_stored_history_are_not_joined_on(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    go(w, store)
    w.at(FRI)
    w.down = {"yahoo"}
    w.ecb_scale = {"GBP": 1.05}  # the ECB's pound is 5% away from what is stored: too far to be a different fixing time
    md, _, _ = go(w, store)
    src = store.load_json("fx_state")["src"]
    assert src["EURUSD"] == "frankfurter" and src["GBPUSD"] == "fawaz"  # the snapshots had the right number
    gbp = closes(md, "GBPUSD")
    assert gbp.index[-1] == pd.Timestamp(THU) and abs(gbp.iloc[-1] / gbp.iloc[-2] - 1) < 0.03  # no 5% cliff


def test_unknown_symbols_are_retried_in_their_short_form_and_metals_fall_back_to_futures(tmp_path):
    w = FxWorld().at(FRI)
    w.unknown = {"USDCNH=X", "XAUUSD=X"}
    md, sess, _ = go(w, HistoryStore(tmp_path))
    assert "USDCNH" in {i.symbol for i in md.instruments.values()} and any("/CNH%3DX" in c["url"] for c in yahoo_calls(sess))
    gold = closes(md, "XAUUSD")
    assert gold.iloc[-1] == pytest.approx(truth(w, "XAUUSD").iloc[-1] * 1.004)  # the futures contract trades a little above spot
    assert any("XAUUSD follows the GC=F futures contract" in n for n in md.extras["notes"])
    assert closes(md, "XAGUSD").iloc[-1] == pytest.approx(truth(w, "XAGUSD").iloc[-1])


def test_a_pair_yahoo_does_not_know_comes_from_the_ecb_in_the_same_run(tmp_path):
    w = FxWorld().at(FRI)
    w.unknown = {"USDHUF=X", "HUF=X"}
    md, _, _ = go(w, HistoryStore(tmp_path))
    huf = bars(md, "USDHUF")
    assert bool(approx(huf).all()) and huf.index[-1] == pd.Timestamp(THU) and md.instruments["FX:USDHUF"].meta["source"] == "frankfurter"
    assert sources(md) == {"yahoo_fx": True, "frankfurter": True}


def test_everything_down_gives_an_empty_market_not_a_crash(tmp_path):
    w = FxWorld().at(FRI)
    w.down = {"yahoo", "frankfurter", "fawaz"}
    md, _, _ = go(w, HistoryStore(tmp_path))
    assert md.instruments == {} and md.bars == {}
    assert sources(md) == {"yahoo_fx": False, "frankfurter": False}
    assert any("no data for" in n for n in md.extras["notes"])
    assert md.extras["history_pct"] == 0


def test_time_limit_keeps_stored_history_instead_of_failing(tmp_path):
    w = FxWorld().at(THU)
    store = HistoryStore(tmp_path)
    go(w, store)
    md, s2, _ = go(w.at(FRI), store, cfg=config(max_seconds=0, sources=["yahoo"]))
    assert yahoo_calls(s2) == [] and any("time limit reached" in n for n in md.extras["notes"])
    assert closes(md, "EURUSD").index[-1] == pd.Timestamp(dt.date(2026, 10, 7))


# ------------------------------------------------------------------------------------------------ bad data
def test_a_cross_that_disagrees_with_its_dollar_legs_is_left_out_but_kept_in_the_store(tmp_path):
    w = FxWorld().at(FRI)
    w.scale = {"EURJPY=X": 1.1}
    store = HistoryStore(tmp_path)
    md, _, _ = go(w, store)
    assert "FX:EURJPY" not in md.instruments and "FX:EURUSD" in md.instruments and "FX:GBPJPY" in md.instruments
    assert any("EURJPY was left out" in n for n in md.extras["notes"])
    assert "FX:EURJPY" in store.load_bars("fx")  # not refetched from scratch next time


def test_a_wick_that_never_traded_is_pulled_in_and_flagged(tmp_path):
    w = FxWorld().at(FRI)
    w.bad_ticks = {("GBPUSD", dt.date(2026, 9, 15)): 10.0}
    md, _, _ = go(w, HistoryStore(tmp_path))
    gbp = bars(md, "GBPUSD")
    day = pd.Timestamp(dt.date(2026, 9, 15))
    assert gbp.loc[day, "high"] <= gbp.loc[day, ["open", "close"]].max() * 1.0001 and bool(approx(gbp).loc[day])
    assert int(approx(gbp).sum()) == 1
    assert closes(md, "GBPUSD").tolist() == pytest.approx(truth(w, "GBPUSD").loc[gbp.index].tolist())


def test_live_quote_is_dropped_when_stale_or_wild(tmp_path):
    w = FxWorld().at(FRI)
    md, _, _ = go(w, HistoryStore(tmp_path / "a"))
    assert "FX:EURUSD" in md.quotes
    w2 = FxWorld().at(FRI)
    w2.live_lag_hours = 24 * 6  # the "live" price is six days old
    md2, _, _ = go(w2, HistoryStore(tmp_path / "b"))
    assert md2.quotes == {} and len(md2.instruments) == 51


# ------------------------------------------------------------------------------------------------ units
@pytest.mark.parametrize("when, final, last_bar", [
    (dt.datetime(2026, 10, 9, 0, 30, tzinfo=UTC), "2026-10-08", "2026-10-08"),
    (dt.datetime(2026, 10, 9, 21, 59, tzinfo=UTC), "2026-10-08", "2026-10-08"),
    (dt.datetime(2026, 10, 9, 22, 0, tzinfo=UTC), "2026-10-09", "2026-10-09"),
    (dt.datetime(2026, 10, 10, 3, 0, tzinfo=UTC), "2026-10-09", "2026-10-09"),
    (dt.datetime(2026, 10, 11, 20, 0, tzinfo=UTC), "2026-10-10", "2026-10-09"),
    (dt.datetime(2026, 10, 12, 0, 30, tzinfo=UTC), "2026-10-11", "2026-10-09"),
    (dt.datetime(2026, 10, 13, 0, 30, tzinfo=UTC), "2026-10-12", "2026-10-12"),
    (dt.datetime(2026, 10, 9, 23, 0, tzinfo=dt.timezone(dt.timedelta(hours=5))), "2026-10-08", "2026-10-08"),  # 18:00 UTC: any time zone is fine
])
def test_final_day_and_the_newest_expected_bar(when, final, last_bar):
    assert F.final_day(when) == pd.Timestamp(final) and F.expected_last(when) == pd.Timestamp(last_bar)


def test_universe_options():
    all_ = F.universe({})
    assert len(all_) == 51 and len({p.symbol for p in all_}) == 51
    assert sum(p.group == "major" for p in all_) == 7 and sum(p.group == "minor" for p in all_) == 21 and sum(p.group == "exotic" for p in all_) == 11
    lean = F.universe({"include_exotics": False, "include_metals": False, "include_pkr_crosses": False})
    assert len(lean) == 29 and "USDPKR" in {p.symbol for p in lean}  # the rupee is always there: everything is priced with it
    assert all(not p.synthetic for p in lean)
    gold = next(p for p in all_ if p.symbol == "XAUUSD")
    assert gold.yahoo == ["XAUUSD=X", "GC=F"] and next(p for p in all_ if p.symbol == "USDJPY").yahoo == ["USDJPY=X", "JPY=X"] and next(p for p in all_ if p.symbol == "EURGBP").yahoo == ["EURGBP=X"]


@pytest.mark.parametrize("days, name", [(10, "1mo"), (25, "1mo"), (26, "3mo"), (120, "6mo"), (300, "1y"), (1105, "5y"), (1835, "5y"), (2000, "10y"), (9999, "max")])
def test_yahoo_range_for_a_number_of_days(days, name):
    assert F.range_for(days) == name


def test_pair_from_legs_and_close_only_bars():
    legs = pd.DataFrame({"EUR": [0.9, 0.8, None, 0.75], "JPY": [150.0, 140.0, 145.0, 150.0]}, index=pd.date_range("2026-10-01", periods=4))
    eurusd = F.pair_from_legs(legs, "EUR", "USD")
    assert eurusd["close"].tolist() == pytest.approx([1 / 0.9, 1 / 0.8, 1 / 0.75]) and len(eurusd) == 3  # a day without a rate is skipped
    eurjpy = F.pair_from_legs(legs, "EUR", "JPY")
    assert eurjpy["close"].iloc[0] == pytest.approx(150 / 0.9)
    assert eurjpy["open"].iloc[1] == pytest.approx(eurjpy["close"].iloc[0]) and eurjpy["high"].iloc[1] >= eurjpy["close"].iloc[0]  # the day-to-day move is the range
    assert bool(eurjpy["approx_hl"].all()) and F.pair_from_legs(legs, "EUR", "CHF").empty


def test_repair_wicks_only_touches_bars_beyond_the_cap():
    idx = pd.date_range("2026-10-01", periods=3)
    df = pd.DataFrame({"open": [1.0, 1.0, 1.0], "high": [1.01, 3.0, 1.02], "low": [0.99, 0.98, 0.1], "close": [1.0, 1.0, 1.0], "volume": 0.0}, index=idx)
    out, n = F.repair_wicks(df, 0.1)
    assert n == 2 and out["high"].tolist() == [1.01, 1.0, 1.02] and out["low"].tolist() == [0.99, 0.98, 1.0] and out["approx_hl"].tolist() == [False, True, True]
    same, none = F.repair_wicks(df.iloc[:1], 0.1)
    assert none == 0 and same is df.iloc[:1] or none == 0


def test_parse_frankfurter_and_fawaz_refuse_what_they_do_not_understand():
    df = F.parse_frankfurter({"base": "USD", "rates": {"2026-10-02": {"EUR": 0.9, "JPY": "bad"}, "2026-10-01": {"EUR": 0.91, "JPY": 150.0}, "oops": {"EUR": 1}}})
    assert df.index.tolist() == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")] and df.loc["2026-10-01", "JPY"] == 150.0 and pd.isna(df.loc["2026-10-02", "JPY"])
    for bad in ({}, {"rates": {}}, {"rates": []}, {"rates": {"x": 1}}, [], None):
        with pytest.raises(ParseError):
            F.parse_frankfurter(bad)
    day = pd.Timestamp("2026-10-08")
    snap = {"date": "2026-10-08", "usd": {f"c{i:02d}": 1.0 + i for i in range(25)}}
    assert len(F.parse_fawaz(snap, day)) == 25 and "C03" in F.parse_fawaz(snap, day)
    for bad, text in (({**snap, "date": "2026-10-07"}, "dated"), ({"date": "2026-10-08", "usd": {"eur": 0.9}}, "empty"), ({"usd": {}}, "unexpected"), ({"date": "2026-10-08", "usd": []}, "unexpected")):
        with pytest.raises(ParseError, match=text):
            F.parse_fawaz(bad, day)


def test_live_quote_rules():
    have = pd.DataFrame({"close": [1.0, 1.1]}, index=pd.to_datetime(["2026-10-07", "2026-10-08"]))
    now = dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    t = now.timestamp()
    ok_ = F._live_quote({"regularMarketPrice": 1.2, "regularMarketTime": t, "gmtoffset": 3600}, have, now)
    assert ok_["prev_close"] == 1.1 and ok_["chg_pct"] == pytest.approx((1.2 / 1.1 - 1) * 100) and ok_["ts"] == "2026-10-09T12:00:00Z"
    assert F._live_quote({"regularMarketPrice": 1.2, "regularMarketTime": t - 5 * 86400}, have, now) is None  # stale
    assert F._live_quote({"regularMarketPrice": 1.2, "regularMarketTime": t + 3600}, have, now) is None  # from the future
    assert F._live_quote({"regularMarketPrice": 1.9, "regularMarketTime": t}, have, now) is None  # a 70% jump in a day is a glitch
    assert F._live_quote({"regularMarketPrice": None, "regularMarketTime": t}, have, now) is None and F._live_quote({}, have, now) is None
    assert F._live_quote({"regularMarketPrice": 1.2, "regularMarketTime": t}, have.iloc[0:0], now) is None
