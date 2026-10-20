"""The macro fetcher against the simulated Yahoo, FRED, IMF and World Bank in ``tests/fake_macro.py``."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from acumen.analysis import macro as analysis_macro
from acumen.config import load_config
from acumen.fetch import macro as M
from acumen.fetch.http import BreakerOpen, Client, FetchError, HttpError, ParseError, RateLimited
from acumen.store import HistoryStore
from tests.fake_macro import MacroWorld
from tests.fakenet import FakeClock, FakeSession

THU, FRI = dt.date(2026, 10, 8), dt.date(2026, 10, 9)
NAMES = {"DXY", "US10Y", "GOLD", "BRENT", "SPX", "VIX", "MSCI_EM", "FEDFUNDS", "DGS2", "T10Y2Y"}


def config(**macro):
    cfg = load_config("/nonexistent.yml")
    cfg["data"]["macro"].update(macro)
    return cfg


def new_client(session=None, clock=None) -> Client:
    clock = clock or FakeClock()
    return Client("TestAgent/1.0", session=session if session is not None else FakeSession(), sleep=clock.sleep, clock=clock, default_interval=0.0)


def go(world: MacroWorld, store, *, cfg=None, gold=True, clock=None):
    sess = world.session()
    if gold:  # the dollar price of gold is the futures contract Yahoo serves for GC=F
        from tests.fake_fx import FxWorld
        fx = FxWorld().at(world.today, world.hour, world.minute)
        fx.down = {"yahoo"} if "yahoo" in world.down else set()
        sess.routes.extend(fx.session().routes)
    macro, statuses, notes = M.fetch_macro(cfg or config(), store, new_client(sess, clock), log=lambda m: None, now=world.now())
    return macro, {s.source: s for s in statuses}, notes, sess


# ------------------------------------------------------------------------------------------------ the whole pipeline
def test_every_series_pakistan_inflation_and_a_newer_policy_rate(tmp_path):
    w = MacroWorld().at(FRI)
    store = HistoryStore(tmp_path)
    macro, st, notes, sess = go(w, store)
    series = macro["series"]
    assert set(series) == NAMES and notes == []
    for name, sym in (("DXY", "DX-Y.NYB"), ("US10Y", "^TNX"), ("BRENT", "BZ=F"), ("SPX", "^GSPC"), ("VIX", "^VIX"), ("MSCI_EM", "EEM")):
        s = series[name]
        assert s.index[-1] == pd.Timestamp(THU)  # today's bar is still forming
        assert s.tolist() == pytest.approx(w.final(sym).loc[s.index].tolist()), name
    assert len(series["SPX"]) > 400 and series["GOLD"].iloc[-1] > 2000
    assert pd.Timestamp("2026-09-25") not in series["DGS2"].index  # FRED's "." for a day without a value is skipped, not read as zero
    assert series["FEDFUNDS"].iloc[-1] == 4.33 and series["T10Y2Y"].iloc[-1] == 0.4

    pk = macro["pk"]
    assert pk["cpi_yoy"] == 6.1 and "IMF estimate for 2026 (annual average), not the latest monthly reading" in pk["note"]
    assert pk["policy_rate"] == 11.0 and pk["asof"] == "2026-07-01" and "newer than the configured table" in pk["note"]
    assert {k: v.ok for k, v in st.items()} == {"yahoo_macro": True, "fred": True, "imf_inflation": True}
    assert "7 of 7 series" in st["yahoo_macro"].message and "4 series" in st["fred"].message
    assert store.load_json("macro_state")["series"]["DXY"]["saved_at"] == "2026-10-09T00:30:00Z"


@pytest.mark.parametrize("obs, value, as_of, used", [
    (dt.date(2026, 7, 1), 11.0, "2025-12-15", True),
    (dt.date(2025, 11, 1), 12.0, "2025-12-15", False),  # older than the table, though recent enough to be live
    (dt.date(2026, 7, 1), 55.0, "2025-12-15", False),  # not a policy rate
    (dt.date(2025, 3, 1), 12.0, "2024-01-01", False),  # newer than the table, but 19 months old: not something to present as live
])
def test_a_policy_rate_reading_is_only_used_when_it_is_newer_plausible_and_recent(tmp_path, obs, value, as_of, used):
    w = MacroWorld().at(FRI)
    w.policy_rate = (obs, value)
    cfg = config()
    cfg["rates"]["as_of"] = as_of
    macro, _, _, _ = go(w, HistoryStore(tmp_path), cfg=cfg)
    if used:
        assert macro["pk"]["policy_rate"] == value and macro["pk"]["asof"] == str(obs)
    else:
        assert "policy_rate" not in macro["pk"] and "asof" not in macro["pk"]


def test_without_the_optional_series_the_table_is_the_only_source(tmp_path):
    macro, st, _, sess = go(MacroWorld().at(FRI), HistoryStore(tmp_path), cfg=config(pk_policy_fred=""))
    assert "policy_rate" not in macro["pk"] and not any("INTDSRPKM193N" in c["full"] for c in sess.calls)
    w = MacroWorld().at(FRI)
    w.policy_rate = None  # FRED answers with an error page for it
    macro2, st2, _, _ = go(w, HistoryStore(tmp_path / "b"))
    assert "policy_rate" not in macro2["pk"] and st2["fred"].ok is True and "failed" not in st2["fred"].message  # an optional series is not a failure


def test_the_ten_year_yield_is_read_in_percent_even_when_yahoo_quotes_it_times_ten(tmp_path):
    w = MacroWorld().at(FRI)
    w.tnx_x10 = True
    macro, _, _, _ = go(w, HistoryStore(tmp_path))
    assert macro["series"]["US10Y"].tolist() == pytest.approx(w.final("^TNX").loc[macro["series"]["US10Y"].index].tolist())
    assert macro["series"]["US10Y"].max() < 10


# ------------------------------------------------------------------------------------------------ outages
def test_without_yahoo_the_yield_comes_from_fred_and_the_rest_is_reported_missing(tmp_path):
    w = MacroWorld().at(FRI)
    w.down = {"yahoo"}
    macro, st, notes, _ = go(w, HistoryStore(tmp_path))
    assert set(macro["series"]) == {"US10Y", "FEDFUNDS", "DGS2", "T10Y2Y"}
    assert macro["series"]["US10Y"].iloc[-1] == pytest.approx(w.fred_px["DGS10"].loc[macro["series"]["US10Y"].index[-1]], abs=1e-3)
    assert any("US 10-year yield comes from FRED" in n for n in notes) and any("no data for BRENT, DXY, GOLD, MSCI_EM, SPX, VIX" in n for n in notes)
    assert st["yahoo_macro"].ok is False and st["fred"].ok is True


def test_a_recent_copy_fills_in_for_a_source_that_is_down_but_an_old_one_does_not(tmp_path):
    store = HistoryStore(tmp_path)
    w = MacroWorld().at(THU)
    go(w, store)
    w.at(FRI)
    w.down = {"yahoo"}
    macro, _, notes, _ = go(w, store)
    assert set(macro["series"]) == NAMES and macro["series"]["SPX"].index[-1] == pd.Timestamp(dt.date(2026, 10, 7))  # yesterday's copy, labelled as such
    assert any("kept from an earlier run" in n and "SPX (saved 2026-10-08)" in n for n in notes)
    w.at(dt.date(2026, 10, 23))
    macro2b, _, _, _ = go(w, store)  # reusing a copy does not make it any younger: it is still the one saved on the 8th, now 15 days old
    assert "SPX" not in macro2b["series"]
    w.at(dt.date(2026, 10, 30))
    macro3, _, notes3, _ = go(w, store)
    assert "SPX" not in macro3["series"] and any("no data for" in n and "SPX" in n for n in notes3)  # a three-week-old copy is not presented as current


def test_inflation_falls_back_to_the_world_bank_then_to_the_saved_figure_and_then_to_nothing(tmp_path):
    store = HistoryStore(tmp_path)
    w = MacroWorld().at(THU)
    macro, _, _, _ = go(w, store)
    assert macro["pk"]["cpi_yoy"] == 6.1

    w.at(FRI)
    w.down = {"imf"}
    macro2, st2, _, _ = go(w, store)
    assert macro2["pk"]["cpi_yoy"] == 4.5 and "World Bank figure for 2025" in macro2["pk"]["note"]  # the newest year that has a value
    assert st2["imf_inflation"].ok is False and st2["worldbank_inflation"].ok is True

    w.down = {"imf", "worldbank"}
    macro3, _, _, _ = go(w, store)
    assert macro3["pk"]["cpi_yoy"] == 4.5 and "as saved on 2026-10-09" in macro3["pk"]["note"]  # the World Bank figure saved a moment ago

    macro4, _, _, _ = go(w, HistoryStore(tmp_path / "empty"))
    assert "cpi_yoy" not in macro4["pk"] and "Inflation" not in (macro4["pk"]["note"] or "")


def test_a_fred_error_page_is_refused_and_counted_not_read_as_data(tmp_path):
    w = MacroWorld().at(FRI)
    w.fred_missing = {"DGS2"}
    macro, st, _, _ = go(w, HistoryStore(tmp_path))
    assert "DGS2" not in macro["series"] and {"FEDFUNDS", "T10Y2Y", "US10Y"} <= set(macro["series"])
    assert st["fred"].ok is True and "1 failed" in st["fred"].message


def test_a_fred_that_does_not_answer_costs_seconds_not_minutes(tmp_path):
    """From some data centres FRED simply hangs. Waiting 30 s three times for each of five series cost three minutes per run."""
    w = MacroWorld().at(FRI)
    w.down = {"fred"}  # the stand-in answers 503; a real timeout is treated the same way (see test_one_bad_series_is_told_apart_from_an_unreachable_fred)
    clock = FakeClock()
    macro, st, notes, sess = go(w, HistoryStore(tmp_path), clock=clock)
    assert sess.count(r"fredgraph") == 2  # the first series and its single retry: the other series and the Pakistan policy rate are not even asked for
    assert st["fred"].ok is False and "4 failed" in st["fred"].message and "FRED did not answer" in st["fred"].message
    assert clock.now - 1000.0 < 30  # simulated seconds spent waiting
    assert {"DXY", "US10Y", "GOLD", "BRENT", "SPX", "VIX", "MSCI_EM"} <= set(macro["series"])  # Yahoo's series are untouched
    assert {"FEDFUNDS", "DGS2", "T10Y2Y"}.isdisjoint(macro["series"]) and any("no data for" in n and "FEDFUNDS" in n for n in notes)
    assert "policy_rate" not in macro["pk"]  # the configured table is used


def test_a_fred_that_answers_slowly_is_still_read_with_the_short_timeout(tmp_path):
    w = MacroWorld().at(FRI)
    macro, st, _, sess = go(w, HistoryStore(tmp_path))
    assert st["fred"].ok is True and "4 series" in st["fred"].message and sess.count(r"fredgraph") == 5  # four series and the policy rate
    asked = [c for c in sess.calls if "fredgraph" in c["full"]]
    assert asked and all(c["timeout"] == M.FRED_TIMEOUT for c in asked)


@pytest.mark.parametrize("exc, unreachable", [
    (FetchError("ReadTimeout: timed out"), True),  # what a hanging FRED raises: no HTTP status at all
    (HttpError("HTTP 500", status=500), True),
    (HttpError("HTTP 503", status=503), True),
    (HttpError("HTTP 403", status=403), True),
    (RateLimited("rate limited (HTTP 429)", status=429), True),
    (BreakerOpen("fred.stlouisfed.org is being skipped for this run"), True),
    (HttpError("HTTP 400", status=400), False),  # FRED's answer for a series that does not exist
    (HttpError("HTTP 404", status=404), False),
    (ParseError("FRED answered something other than DGS2"), False),
])
def test_one_bad_series_is_told_apart_from_an_unreachable_fred(exc, unreachable):
    assert M.fred_unreachable(exc) is unreachable


def test_everything_down_gives_an_empty_panel_not_a_crash(tmp_path):
    w = MacroWorld().at(FRI)
    w.down = {"yahoo", "fred", "imf", "worldbank"}
    macro, st, notes, _ = go(w, HistoryStore(tmp_path), gold=False)
    assert macro["series"] == {} and "cpi_yoy" not in macro["pk"]
    assert not any(s.ok for s in st.values()) and any("no data for" in n for n in notes)


# ------------------------------------------------------------------------------------------------ how the app reads it
def test_the_pakistan_block_uses_the_live_rate_when_there_is_one_and_the_tables_date_when_not(tmp_path):
    cfg = config()
    w = MacroWorld().at(FRI)
    macro, _, _, _ = go(w, HistoryStore(tmp_path / "a"), cfg=cfg)
    body, head = analysis_macro.macro_block({"macro": macro}, {}, cfg, "2026-10-09")
    pk = body["pakistan"]
    assert pk["policy_rate"] == 11.0 and pk["asof"] == "2026-07-01" and pk["real_rate"] == pytest.approx(11.0 - 6.1) and head["policy_rate_pk"] == 11.0

    w2 = MacroWorld().at(FRI)
    w2.policy_rate = None
    macro2, _, _, _ = go(w2, HistoryStore(tmp_path / "b"), cfg=cfg)
    body2, _ = analysis_macro.macro_block({"macro": macro2}, {}, cfg, "2026-10-09")
    pk2 = body2["pakistan"]
    table = cfg.get_path("rates.policy_rate_pct.PKR")
    assert pk2["policy_rate"] == table and pk2["asof"] == str(cfg.get_path("rates.as_of"))  # the app warns by this date when it is old
    assert "configured table" in pk2["note"] and "IMF estimate" in pk2["note"] and body2["rates"]["stale"] is True
    assert set(body2["series"]) == NAMES and body2["series"]["DXY"]["asof"] == "2026-10-08"


# ------------------------------------------------------------------------------------------------ units
def test_parse_fred_csv():
    ok_ = M.parse_fred_csv('observation_date,DGS2\n2026-10-01,3.9\n2026-10-02,.\n2026-10-05,\n2026-10-06,4.0\n"2026-10-06",4.1\nnot a date,5\n', "DGS2")
    assert ok_.index.tolist() == [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-06")] and ok_.tolist() == [3.9, 4.1]  # a repeated date keeps the later row
    assert M.parse_fred_csv("DATE,DGS2\n2026-10-01,3.9\n", "dgs2").iloc[0] == 3.9
    for bad in ("", "DATE,DGS2", "<html><body>Bad request</body></html>", "DATE,OTHER\n2026-10-01,1\n", "DATE,DGS2\n2026-10-01,.\n"):
        with pytest.raises(ParseError):
            M.parse_fred_csv(bad, "DGS2")


def test_parse_imf_and_world_bank_inflation():
    imf = {"values": {"PCPIPCH": {"PAK": {"2024": 23.4, "2025": 4.5, "2026": 6.1, "2027": 7.0, "x": 1}}}}
    assert M.parse_imf_inflation(imf, 2026) == (6.1, 2026) and M.parse_imf_inflation(imf, 2025) == (4.5, 2025)  # never a year that has not begun
    for bad in ({}, {"values": {}}, {"values": {"PCPIPCH": {"PAK": {"2030": 1.0}}}}, [], None):
        with pytest.raises(ParseError):
            M.parse_imf_inflation(bad, 2026)
    wb = [{"page": 1}, [{"date": "2026", "value": None}, {"date": "2024", "value": 23.4}, {"date": "2025", "value": "4.5"}, "junk"]]
    assert M.parse_worldbank_inflation(wb) == (4.5, 2025)
    for bad in ([], [{}], [{}, None], [{}, [{"date": "2026", "value": None}]], {}):
        with pytest.raises(ParseError):
            M.parse_worldbank_inflation(bad)


def test_fix_yield_units_only_touches_values_no_yield_could_have():
    s = pd.Series([4.2, 42.5, 4.3, 51.0], index=pd.date_range("2026-10-01", periods=4))
    assert M.fix_yield_units(s).tolist() == pytest.approx([4.2, 4.25, 4.3, 5.1])
