"""``fetch_all`` against every simulated service at once: the order it runs in, failures that stay inside one market,
history reused from earlier runs, and a sealed store that must stop the whole run."""

from __future__ import annotations

import datetime as dt

import pytest

from acumen import crypto_box, fetch
from acumen.fetch import psx_dps
from acumen.model import MarketData
from acumen.store import HistoryStore, StoreLocked
from tests.fake_world import Worlds, config

KEY = crypto_box.store_key("a long passphrase for the tests", iterations=1000)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr(psx_dps, "_resolve", lambda: [])


def run(w: Worlds, store: HistoryStore, markets=None, logs=None):
    return fetch.fetch_all(config(), store, markets, log=(logs.append if logs is not None else (lambda line: None)), now=w.now(), http=w.client())


# ------------------------------------------------------------------------------------------------ choosing markets
def test_parse_markets():
    assert fetch.parse_markets(None) == ["psx", "fx", "crypto"]
    assert fetch.parse_markets("crypto, psx") == ["psx", "crypto"]  # always in the canonical order
    assert fetch.parse_markets(["FX"]) == ["fx"]
    with pytest.raises(ValueError, match="unknown market 'forex'"):
        fetch.parse_markets("fx,forex")
    with pytest.raises(ValueError, match="no market chosen"):
        fetch.parse_markets(" , ")


# ------------------------------------------------------------------------------------------------ the happy path
def test_every_market_comes_back_in_canonical_order_and_the_history_is_stored(tmp_path):
    w, logs = Worlds(), []
    store = HistoryStore(tmp_path)
    got = run(w, store, logs=logs)
    assert list(got) == ["psx", "fx", "crypto"]
    for market, md in got.items():
        assert md.instruments and md.bars and all(s.ok for s in md.status), (market, [s for s in md.status if not s.ok])
        assert store.load_bars(market), f"{market} history was not stored"
    assert "macro" in got["fx"].extras and got["fx"].extras["macro"]  # the macro series travel with forex
    heads = [line for line in logs if line.startswith("Fetching")]
    assert heads == ["Fetching Forex", "Fetching Crypto", "Fetching Pakistan Stock Exchange"]  # quick markets first, the slow portal last


def test_a_subset_touches_only_those_markets(tmp_path):
    store = HistoryStore(tmp_path)
    got = run(Worlds(), store, "crypto")
    assert list(got) == ["crypto"]
    assert (tmp_path / "bars_crypto.csv.gz").exists()
    assert not (tmp_path / "bars_psx.csv.gz").exists() and not (tmp_path / "bars_fx.csv.gz").exists()


def test_a_later_run_extends_the_stored_history(tmp_path):
    store = HistoryStore(tmp_path)
    first = run(Worlds(), store, "crypto")["crypto"]
    last_first = max(b.index[-1] for b in first.bars.values())
    w = Worlds().at(dt.date(2026, 10, 12))
    again = run(w, HistoryStore(tmp_path), "crypto")["crypto"]
    last_again = max(b.index[-1] for b in again.bars.values())
    assert last_again > last_first
    for inst_id, bars in first.bars.items():
        assert inst_id in again.bars and again.bars[inst_id].index[0] <= bars.index[0]  # nothing that was held is dropped


# ------------------------------------------------------------------------------------------------ failures stay inside one market
def test_a_market_whose_sources_are_all_down_keeps_its_stored_history(tmp_path):
    store = HistoryStore(tmp_path)
    before = run(Worlds(), store, "crypto")["crypto"]
    after = run(Worlds().down("crypto"), HistoryStore(tmp_path), "crypto")["crypto"]
    assert set(after.bars) == set(before.bars)  # history from earlier runs is still there
    assert all(len(after.bars[k]) == len(v) for k, v in before.bars.items())
    assert any(not s.ok for s in after.status), "the outage must show up as a health line"


def test_a_market_that_is_down_with_no_history_is_empty_and_says_so(tmp_path):
    got = run(Worlds().down("crypto"), HistoryStore(tmp_path))
    assert not got["crypto"].bars and any(not s.ok for s in got["crypto"].status)
    assert got["psx"].bars and got["fx"].bars  # the others are untouched


def test_a_fetcher_that_crashes_is_retried_from_stored_history(tmp_path, monkeypatch):
    calls = []

    def flaky(cfg, store, http, *, log, now):
        calls.append(http)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        return MarketData(market="fx")

    monkeypatch.setitem(fetch.FETCHERS, "fx", flaky)
    logs = []
    got = run(Worlds(), HistoryStore(tmp_path), logs=logs)
    assert list(got) == ["psx", "fx", "crypto"] and got["psx"].bars and got["crypto"].bars
    first = got["fx"].status[0]
    assert first.source == "fx_run" and not first.ok
    assert "stopped unexpectedly (RuntimeError: disk full)" in first.message and "stored by earlier runs" in first.message
    assert calls[1] is not calls[0]  # the retry runs against a client that cannot reach the network
    assert any("Traceback" in line and "disk full" in line for line in logs), "the traceback goes to the run log"


def test_a_fetcher_that_crashes_twice_leaves_an_empty_market(tmp_path, monkeypatch):
    def broken(cfg, store, http, *, log, now):
        raise RuntimeError("corrupt file")

    monkeypatch.setitem(fetch.FETCHERS, "psx", broken)
    got = run(Worlds(), HistoryStore(tmp_path))
    assert not got["psx"].instruments and not got["psx"].bars
    assert "no stored history could be used" in got["psx"].status[0].message and "corrupt file" in got["psx"].status[0].message
    assert got["fx"].bars and got["crypto"].bars


def test_a_macro_failure_never_costs_the_forex_data(tmp_path, monkeypatch):
    def broken(cfg, store, http, *, log, now):
        raise RuntimeError("macro parser bug")

    monkeypatch.setattr(fetch, "fetch_macro", broken)
    got = run(Worlds(), HistoryStore(tmp_path), "fx")
    assert got["fx"].bars and "macro" not in got["fx"].extras
    assert any(s.source == "macro_run" and not s.ok and "macro parser bug" in s.message for s in got["fx"].status)


# ------------------------------------------------------------------------------------------------ a sealed store stops everything
def test_a_sealed_store_without_its_key_stops_the_whole_run(tmp_path):
    w = Worlds()
    sealed = HistoryStore(tmp_path, key=KEY)
    got = run(w, sealed, "crypto")
    assert got["crypto"].bars
    assert (tmp_path / "bars_crypto.csv.gz.enc").exists()
    with pytest.raises(StoreLocked):
        run(w, HistoryStore(tmp_path), "crypto")  # no key: a quiet fresh start would throw the stored years away
    with pytest.raises(StoreLocked):
        run(w, HistoryStore(tmp_path, key=crypto_box.store_key("another passphrase", iterations=1000)), "crypto")
    again = run(w, HistoryStore(tmp_path, key=KEY), "crypto")
    assert again["crypto"].bars


def test_a_locked_macro_state_is_not_swallowed_either(tmp_path, monkeypatch):
    def locked(cfg, store, http, *, log, now):
        raise StoreLocked("macro cache is sealed")

    monkeypatch.setattr(fetch, "fetch_macro", locked)
    with pytest.raises(StoreLocked):
        run(Worlds(), HistoryStore(tmp_path), "fx")
