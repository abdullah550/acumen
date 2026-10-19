"""The history store: bars and state between runs, merging that cannot lose a true high or low, and sealing."""

from __future__ import annotations

import gzip

import numpy as np
import pandas as pd
import pytest

from acumen import crypto_box
from acumen.store import SEALED, HistoryStore, StoreLocked

KEY = crypto_box.store_key("correct horse", iterations=1000)  # few iterations: the tests are about the file handling, not the key stretching
OTHER = crypto_box.store_key("battery staple", iterations=1000)


def bars(n=30, start="2026-08-03", price=100.0, approx=False, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, periods=n)
    close = price * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    df = pd.DataFrame({"open": close * 0.998, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1000.0 + np.arange(n)}, index=idx)
    df.index.name = "date"
    df["approx_hl"] = approx
    return df


# ------------------------------------------------------------------------------------------------ plain round trips
def test_bars_round_trip_exactly(tmp_path):
    st = HistoryStore(tmp_path)
    data = {"PSX:OGDC": bars(40, seed=1), "PSX:HUBC": bars(25, seed=2, price=80.0)}
    st.save_bars("psx", data)
    back = st.load_bars("psx")
    assert set(back) == set(data)
    for k, df in data.items():
        pd.testing.assert_frame_equal(back[k], df, check_freq=False, atol=1e-7, rtol=1e-9)
    assert (tmp_path / "bars_psx.csv.gz").exists() and not list(tmp_path.glob("*.tmp"))


def test_missing_and_corrupt_stores_start_from_empty(tmp_path):
    st = HistoryStore(tmp_path)
    assert st.load_bars("crypto") == {}
    (tmp_path / "bars_crypto.csv.gz").write_bytes(b"this is not a gzip file")
    assert st.load_bars("crypto") == {}  # the pipeline must survive a damaged store and rebuild it
    (tmp_path / "bars_fx.csv.gz").write_bytes(gzip.compress(b"id,date\nFX:EURUSD,not-a-date\n"))
    assert st.load_bars("fx") == {}


def test_keep_days_trims_each_series_and_empty_stores_remove_the_file(tmp_path):
    st = HistoryStore(tmp_path)
    st.save_bars("psx", {"PSX:A": bars(50)}, keep_days=20)
    assert len(st.load_bars("psx")["PSX:A"]) == 20
    st.save_bars("psx", {"PSX:A": bars(0).iloc[0:0]})
    assert not (tmp_path / "bars_psx.csv.gz").exists() and st.load_bars("psx") == {}


def test_json_state_round_trip_default_and_numpy_values(tmp_path):
    st = HistoryStore(tmp_path)
    assert st.load_json("ledger", default={"x": 1}) == {"x": 1}
    st.save_json("ledger", {"n": np.int64(3), "when": pd.Timestamp("2026-10-09"), "rows": [1, 2.5, None]})
    assert st.load_json("ledger") == {"n": 3, "when": "2026-10-09T00:00:00", "rows": [1, 2.5, None]}
    (tmp_path / "state" / "broken.json").write_text("{not json")
    assert st.load_json("broken", default=[]) == []


def test_coverage_summarises_what_is_held(tmp_path):
    st = HistoryStore(tmp_path)
    assert st.coverage({})["instruments"] == 0
    cov = st.coverage({"A": bars(10), "B": bars(30, start="2026-07-01")})
    assert cov["instruments"] == 2 and cov["bars"] == 40 and cov["first"] == "2026-07-01" and cov["median_bars"] == 20


# ------------------------------------------------------------------------------------------------ merging
def test_merge_adds_new_days_and_keeps_old_ones(tmp_path):
    st = HistoryStore(tmp_path)
    old = bars(30)
    cur = {"A": old.copy()}
    upd = bars(10, start=str((old.index[-1] + pd.offsets.BDay(1)).date()), seed=5)
    added = st.merge_bars(cur, {"A": upd, "B": bars(5, seed=6)})
    assert added == {"A": 10, "B": 5}
    assert len(cur["A"]) == 40 and cur["A"].index.is_monotonic_increasing and len(cur["B"]) == 5
    pd.testing.assert_frame_equal(cur["A"].iloc[:30][["close"]], old[["close"]], check_freq=False)


def test_merge_lets_the_update_revise_only_the_latest_sessions(tmp_path):
    st = HistoryStore(tmp_path)
    old = bars(30)
    cur = {"A": old.copy()}
    upd = old.copy()
    upd["close"] = upd["close"] * 1.5
    upd["high"] = upd["high"] * 1.5
    upd["open"] = upd["open"] * 1.5
    upd["low"] = upd["low"] * 1.5
    st.merge_bars(cur, {"A": upd}, overwrite_days=5)
    assert np.allclose(cur["A"]["close"].iloc[-5:], upd["close"].iloc[-5:])  # the last five sessions were revised
    assert np.allclose(cur["A"]["close"].iloc[:-5], old["close"].iloc[:-5])  # history before that is left alone


def test_an_approximate_high_low_never_replaces_a_true_one(tmp_path):
    st = HistoryStore(tmp_path)
    old = bars(10, approx=False)
    cur = {"A": old.copy()}
    last = old.index[-1]
    upd = old.loc[[last]].copy()
    upd["approx_hl"] = True
    upd["high"] = upd["close"]
    upd["low"] = upd["close"]
    upd["close"] = upd["close"] * 1.001
    st.merge_bars(cur, {"A": upd})
    row = cur["A"].loc[last]
    assert row["high"] >= old.loc[last, "high"] - 1e-9 and row["low"] <= old.loc[last, "low"] + 1e-9
    assert bool(row["approx_hl"]) is False and row["close"] == pytest.approx(upd["close"].iloc[0])


def test_a_true_high_low_upgrades_an_approximated_old_row(tmp_path):
    st = HistoryStore(tmp_path)
    old = bars(20, approx=True)
    cur = {"A": old.copy()}
    upd = bars(20, approx=False, seed=1).iloc[:3]  # the first three days, far older than the revision window
    st.merge_bars(cur, {"A": upd})
    assert not cur["A"]["approx_hl"].iloc[:3].any() and cur["A"]["approx_hl"].iloc[3:].all()


# ------------------------------------------------------------------------------------------------ sealing
def test_a_sealed_store_holds_only_ciphertext(tmp_path):
    st = HistoryStore(tmp_path, key=KEY)
    st.save_bars("psx", {"PSX:OGDC": bars(30)})
    st.save_json("ledger", {"issued": ["PSX:OGDC secret idea"]})
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == [f"bars_psx.csv.gz{SEALED}", f"state/ledger.json{SEALED}"]
    for f in files:
        blob = (tmp_path / f).read_bytes()
        assert b"OGDC" not in blob and b"secret" not in blob and b"close" not in blob
    again = HistoryStore(tmp_path, key=KEY)
    assert set(again.load_bars("psx")) == {"PSX:OGDC"} and again.load_json("ledger") == {"issued": ["PSX:OGDC secret idea"]}


def test_a_sealed_store_will_not_open_without_the_key_or_with_the_wrong_one(tmp_path):
    HistoryStore(tmp_path, key=KEY).save_bars("psx", {"PSX:A": bars(10)})
    HistoryStore(tmp_path, key=KEY).save_json("ledger", {"a": 1})
    for bad in (None, OTHER):
        st = HistoryStore(tmp_path, key=bad)
        with pytest.raises(StoreLocked):
            st.load_bars("psx")
        with pytest.raises(StoreLocked):
            st.load_json("ledger")
    assert "passphrase" in str(pytest.raises(StoreLocked, HistoryStore(tmp_path, key=OTHER).load_bars, "psx").value)


def test_a_locked_store_is_never_overwritten_with_a_plain_copy(tmp_path):
    HistoryStore(tmp_path, key=KEY).save_json("ledger", {"a": 1})
    with pytest.raises(StoreLocked, match="refusing to write a plain copy"):
        HistoryStore(tmp_path).save_json("ledger", {"a": 2})
    assert not (tmp_path / "state" / "ledger.json").exists()
    assert HistoryStore(tmp_path, key=KEY).load_json("ledger") == {"a": 1}


def test_a_plain_store_is_sealed_in_place_and_the_plain_files_go(tmp_path):
    plain = HistoryStore(tmp_path)
    plain.save_bars("psx", {"PSX:A": bars(12)})
    plain.save_json("ledger", {"a": 1})
    sealed = HistoryStore(tmp_path, key=KEY)
    sealed.save_bars("psx", sealed.load_bars("psx"))  # a plain store opens with a key: the next save seals it
    sealed.save_json("ledger", sealed.load_json("ledger"))
    names = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    assert names == [f"bars_psx.csv.gz{SEALED}", f"ledger.json{SEALED}"]
    assert HistoryStore(tmp_path, key=KEY).load_json("ledger") == {"a": 1}


def test_emptying_a_sealed_store_removes_its_sealed_file_but_never_without_the_key(tmp_path):
    HistoryStore(tmp_path, key=KEY).save_bars("psx", {"PSX:A": bars(10)})
    with pytest.raises(StoreLocked, match="refusing to delete"):
        HistoryStore(tmp_path).save_bars("psx", {})
    assert (tmp_path / f"bars_psx.csv.gz{SEALED}").exists()
    HistoryStore(tmp_path, key=KEY).save_bars("psx", {})
    assert not (tmp_path / f"bars_psx.csv.gz{SEALED}").exists()
    assert HistoryStore(tmp_path, key=KEY).load_bars("psx") == {}
