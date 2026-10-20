"""The source doctor against the simulated services: what it reports when everything works, when one source is down, and when a page changed shape."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from acumen.fetch import doctor
from acumen.fetch import psx_dps
from tests.fake_psx import yahoo_json
from tests.fake_world import Worlds, config
from tests.fakenet import FakeResponse, ok

SMALL = {"psx_shares": 10, "psx_watch": 10, "tradingview_shares": 10, "exchange_markets": 2, "psx_history_bars": 200, "yahoo_history_bars": 60, "coins": 5}


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """The portal's node search asks DNS; a test must never depend on the machine's network."""
    monkeypatch.setattr(psx_dps, "_resolve", lambda: [])


def cfg():
    c = config()
    c["doctor"] = dict(SMALL)
    return c


def session(w: Worlds, *, yahoo_ka: bool = True):
    s = w.session()
    if yahoo_ka:  # the simulated PSX does not serve Yahoo's ".KA" symbols; give the doctor one (it asks for OGDC when the portal's own list is unavailable)
        days = [w.today - dt.timedelta(days=n) for n in range(300, 0, -1) if (w.today - dt.timedelta(days=n)).weekday() < 5]
        s.routes.insert(0, ("GET", re.compile(r"finance/chart/(TST00|OGDC)\.KA"), ok(yahoo_json(days, [100 + i * 0.1 for i in range(len(days))]))))
    return s


def run(w: Worlds, only=None, **kw):
    return doctor.run_checks(cfg(), w.client(session(w, **kw)), only, now=w.now())


def by_key(results):
    return {r.key: r for r in results}


# ------------------------------------------------------------------------------------------------ everything works
def test_every_source_up_is_all_ok_and_exits_zero():
    res = run(Worlds())
    bad = [(r.key, r.state, r.detail) for r in res if r.state != doctor.OK]
    assert bad == []
    assert len(res) == len(doctor.checks())
    assert {g: v[0] for g, v in doctor.verdicts(res).items()} == {"psx": "ok", "fx": "ok", "crypto": "ok", "macro": "ok"}
    assert doctor.exit_code(res) == 0 and doctor.exit_code(res, strict=True) == 0


def test_details_say_what_was_read():
    r = by_key(run(Worlds()))
    assert re.search(r"TST00: \d+ daily bars, newest 2026-10-09", r["psx.eod"].detail)
    assert "EURUSD=X" in r["fx.yahoo"].detail and "USDPKR=X" in r["fx.pkr"].detail
    assert "BTC" in r["crypto.coingecko"].detail
    assert "7 of 7 series" in r["macro.yahoo"].detail
    assert r["psx.day"].detail.startswith("24 symbols traded on 2026-10-0")


# ------------------------------------------------------------------------------------------------ outages
def test_one_exchange_down_is_a_warning_and_the_market_stays_ok():
    res = run(Worlds().down("binance_vision"))
    r = by_key(res)
    assert r["crypto.binance_vision"].state == doctor.WARN and r["crypto.binance_vision"].fallback
    assert [x.state for x in res if x.group == "crypto" and x.key != "crypto.binance_vision"] == [doctor.OK] * 6
    assert doctor.verdicts(res)["crypto"] == ("ok", "")
    assert doctor.exit_code(res) == 0
    assert doctor.exit_code(res, strict=True) == 1  # strict mode wants every source green


def test_every_exchange_down_leaves_coingecko_closes_and_says_so():
    res = run(Worlds().down("binance_vision", "okx", "coinbase", "kraken", "kucoin"))
    state, why = doctor.verdicts(res)["crypto"]
    assert state == "degraded" and "closing prices only" in why
    assert doctor.exit_code(res) == 0


def test_everything_crypto_down_is_down_and_fails_the_run():
    res = run(Worlds().down("crypto"))
    assert by_key(res)["crypto.coingecko"].state == doctor.FAIL
    assert doctor.verdicts(res)["crypto"][0] == "down"
    assert doctor.exit_code(res) == 1


def test_portal_down_falls_back_to_yahoo_history():
    res = run(Worlds().down("psx"))
    r = by_key(res)
    assert r["psx.portal"].state == doctor.FAIL and r["psx.yahoo"].state == doctor.OK
    state, why = doctor.verdicts(res)["psx"]
    assert state == "degraded" and "Yahoo" in why
    assert doctor.exit_code(res) == 0


def test_portal_and_yahoo_down_is_down():
    res = run(Worlds().down("psx"), yahoo_ka=False)
    assert doctor.verdicts(res)["psx"][0] == "down" and doctor.exit_code(res) == 1


def test_tradingview_down_degrades_psx_and_names_what_is_lost():
    res = run(Worlds().down("tradingview"))
    state, why = doctor.verdicts(res)["psx"]
    assert state == "degraded" and "TradingView" in why and "valuation" in why
    assert doctor.exit_code(res) == 0


def test_yahoo_down_leaves_forex_on_ecb_fixings():
    res = run(Worlds().down("yahoo"))
    state, why = doctor.verdicts(res)["fx"]
    assert state == "degraded" and "ECB" in why
    assert by_key(res)["fx.yahoo"].state == doctor.FAIL
    assert doctor.verdicts(res)["macro"][0] in ("degraded", "ok")


def test_all_forex_sources_down_is_down():
    res = run(Worlds().down("yahoo", "frankfurter", "fawaz"))
    assert doctor.verdicts(res)["fx"][0] == "down" and doctor.exit_code(res) == 1


def test_the_inflation_check_says_why_the_imf_feed_failed():
    r = by_key(run(Worlds().down("imf"), only="macro.inflation"))["macro.inflation"]
    assert r.state == doctor.OK and "World Bank" in r.detail and "the IMF did not answer: HTTP 503" in r.detail


def test_a_fred_that_does_not_answer_is_one_short_warning():
    r = by_key(run(Worlds().down("fred"), only="macro.fred"))["macro.fred"]
    assert r.state == doctor.WARN and "no FRED series answered in time" in r.detail


def test_macro_outage_never_fails_the_run():
    res = run(Worlds().down("yahoo", "fred", "imf", "worldbank"), only="macro")
    assert {r.state for r in res} == {doctor.WARN}
    assert doctor.verdicts(res)["macro"][0] == "degraded" and doctor.exit_code(res) == 0


# ------------------------------------------------------------------------------------------------ a page that changed
def test_a_maintenance_page_instead_of_data_is_reported_not_raised():
    w = Worlds()
    s = session(w)
    s.routes.insert(0, ("GET", re.compile(r"dps\.psx\.com\.pk/symbols"), ok("<html><body>Down for maintenance</body></html>")))
    res = doctor.run_checks(cfg(), w.client(s), "psx", now=w.now())
    r = by_key(res)
    assert r["psx.portal"].state == doctor.FAIL and r["psx.portal"].detail
    assert r["psx.day"].state == doctor.OK  # the checks are independent: a broken symbol list does not stop the rest


def test_an_unexpected_exception_inside_a_check_is_caught(monkeypatch):
    def boom(ctx):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(doctor, "check_fx_pkr", boom)
    res = doctor.run_checks(cfg(), Worlds().client(), "fx.pkr", now=Worlds().now())
    assert res[0].state == doctor.WARN and "unexpected RuntimeError: something nobody planned for" in res[0].detail


def test_a_stale_series_is_a_problem():
    w = Worlds()
    res = doctor.run_checks(cfg(), w.client(session(w)), "fx.yahoo", now=w.now() + dt.timedelta(days=40))
    assert res[0].state == doctor.FAIL and "days old" in res[0].detail


def test_implausible_bitcoin_price_is_caught():
    w = Worlds()
    s = session(w)
    s.routes.insert(0, ("GET", re.compile(r"api/v3/klines"), FakeResponse(200, b"[]")))
    res = doctor.run_checks(cfg(), w.client(s), "crypto.binance_vision", now=w.now())
    assert res[0].state == doctor.WARN


# ------------------------------------------------------------------------------------------------ selection and report
def test_checks_are_selected_by_group_or_key():
    allc = doctor.checks()
    assert {c.group for c in doctor.select(allc, "fx")} == {"fx"}
    assert [c.key for c in doctor.select(allc, "crypto.okx,psx.eod")] == ["psx.eod", "crypto.okx"]
    assert doctor.select(allc, None) == allc
    with pytest.raises(ValueError, match="unknown 'forex'"):
        doctor.select(allc, "forex")


def test_check_keys_are_unique_and_every_failure_has_a_fallback_text():
    cs = doctor.checks()
    assert len({c.key for c in cs}) == len(cs)
    assert all(c.fallback for c in cs if not c.critical or c.key in ("psx.eod",))


def test_report_lists_every_check_and_the_verdicts():
    w = Worlds().down("tradingview")
    c = cfg()
    res = doctor.run_checks(c, w.client(session(w)), now=w.now())
    text = doctor.render(res, c, w.now())
    assert text.startswith("Acumen doctor, 2026-10-09 12:30 UTC")
    for r in res:
        assert r.title in text
    assert "WARN  TradingView: fundamentals" in text and "-> valuation and quality scores are left out" in text
    assert "Verdict" in text and "Pakistan Stock Exchange" in text and "DEGRADED" in text
    assert doctor.Result("a", "psx", "t", "ok", "d", 1.234).as_dict()["seconds"] == 1.2
