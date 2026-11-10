"""Bargains live record: a share is logged once the day it is first listed, followed against the KSE-100 and the typical share with
prices that survive bonus issues, closed when it reaches its fair value or runs out of time, and read only at fixed ages."""

from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from acumen.analysis import bargains_record as br
from acumen.config import load_config

CFG = load_config("/nonexistent.yml")  # the packaged defaults only
N = 480
IDX = pd.bdate_range("2025-01-01", periods=N)
T = np.arange(N, dtype=float)
I0 = 100  # the listing day, as a position in IDX


def day(i: int) -> str:
    return IDX[i].strftime("%Y-%m-%d")


def frame(close) -> pd.DataFrame:
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e6}, index=IDX[: len(c)])


def rising(per_day: float, start=40.0, n=N, i0=I0):
    """Flat until the listing day, then rising by ``per_day`` a day."""
    return np.where(T[:n] <= i0, start, start + per_day * (T[:n] - i0))


def pick(sym="AAA", px=40.0, fair=72.0, i0=I0, **over):
    p = {"id": f"PSX:{sym}", "sym": sym, "name": f"{sym} Ltd", "sector": "Cement", "px": px, "px_asof": day(i0), "fair": fair, "cents": round(px / fair * 100),
         "tier": 35.0, "target1": px + 0.5 * (fair - px), "target2": fair, "review_below": px * 0.75}
    p.update(over)
    return p


def market(n=N, followed=None, bench_drift=0.0002):
    """Forty liquid shares drifting evenly around zero (their median return is about zero), the KSE-100, and the followed shares."""
    bars = {f"PSX:L{k}": frame(100.0 * (1.0 + (k - 19.5) * 1e-5 * T[:n])) for k in range(40)}
    for sym, path in (followed or {}).items():
        bars[f"PSX:{sym}"] = frame(path)
    bench = pd.Series(100000.0 * (1.0 + bench_drift * T[:n]), index=IDX[:n])
    return bars, bench, [f"PSX:L{k}" for k in range(40)]


def run(state, picks, n, followed, cfg=CFG, **kw):
    bars, bench, universe = market(n, followed)
    return br.update(state, picks, bars, kw.get("bench", bench), kw.get("universe", universe), cfg, day(n - 1), day(n - 1))


# ------------------------------------------------------------------------------------------------ logging
def test_a_share_is_logged_once_on_the_day_it_is_first_listed_and_never_twice():
    state, view = run(None, [pick()], I0 + 1, {"AAA": rising(0.0, n=I0 + 1)})
    assert len(state["entries"]) == 1 and state["since"] == day(I0)
    e = state["entries"][0]
    assert e["listed"] == day(I0) and e["px"] == 40.0 and e["entry_close"] == 40.0 and e["target2"] == 72.0 and e["status"] == "open"
    row = view["rows"][0]
    assert row["sym"] == "AAA" and row["ret_pct"] == 0.0 and row["days"] == 0 and row["listed_now"] is True
    assert view["verdict"] == "early" and view["counts"] == {**view["counts"], "tracked": 1, "open": 1, "closed": 0}
    state2, view2 = run(state, [pick()], I0 + 6, {"AAA": rising(0.0, n=I0 + 6)})  # listed again the next week: still the one record
    assert len(state2["entries"]) == 1 and view2["rows"][0]["days"] == 7  # five trading days, seven calendar days


def test_a_share_with_no_price_history_is_not_logged():
    state, view = run(None, [pick("NOPE")], I0 + 1, {})
    assert state["entries"] == [] and view["verdict"] == "none" and view["rows"] == []


# ------------------------------------------------------------------------------------------------ following
def test_the_return_is_measured_against_the_index_and_the_typical_share_over_the_same_days():
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.1, n=I0 + 1)})
    n = I0 + 61  # sixty trading days later; the share is no longer on the list
    state, view = run(state, [], n, {"AAA": rising(0.1, n=n)})
    row = view["rows"][0]
    price = 40.0 + 0.1 * 60
    b0, b1 = 1 + 0.0002 * I0, 1 + 0.0002 * (I0 + 60)
    assert row["listed_now"] is False and row["status"] == "open"
    assert row["last"] == pytest.approx(price, abs=0.01) and row["ret_pct"] == pytest.approx((price / 40.0 - 1) * 100, abs=0.06)
    assert row["index_pct"] == pytest.approx((b1 / b0 - 1) * 100, abs=0.06)
    assert row["typical_pct"] == pytest.approx(0.0, abs=0.1)  # the forty liquid shares drift evenly around zero
    assert row["excess_index_pct"] == pytest.approx(row["ret_pct"] - row["index_pct"], abs=0.11)
    assert row["gap_closed_pct"] == pytest.approx((price - 40.0) / (72.0 - 40.0) * 100, abs=0.06)  # a fifth of the way to fair value


def test_milestones_are_dated_by_the_first_close_and_never_taken_back():
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.2, n=I0 + 1)})
    n = I0 + 91
    state, view = run(state, [], n, {"AAA": rising(0.2, n=n)})  # +0.2 a day: halfway (56) after 80 days, fair value (72) after 160
    e = state["entries"][0]
    assert e["t1_date"] == day(I0 + 80) and e["t2_date"] is None and e["review_date"] is None
    n = I0 + 121  # the price drops back under the first target, the milestone stays
    drop = np.concatenate([rising(0.2, n=I0 + 91), np.full(30, 45.0)])
    state, view = run(state, [], n, {"AAA": drop})
    assert state["entries"][0]["t1_date"] == day(I0 + 80) and view["counts"]["reached_fair_value"] == 0


def test_a_share_closes_when_it_reaches_the_fair_value_it_was_listed_with_and_the_result_is_frozen():
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.2, n=I0 + 1)})
    state, view = run(state, [], N, {"AAA": rising(0.2)})  # 72 is reached 160 days after the listing day
    e = state["entries"][0]
    assert e["status"] == "closed" and e["reason"] == "fair_value" and e["closed"] == day(I0 + 160) == e["t2_date"]
    row = view["rows"][0]
    assert row["ret_pct"] == pytest.approx(80.0, abs=0.05) and row["gap_closed_pct"] == pytest.approx(100.0, abs=0.05) and row["days"] == 160 * 7 // 5
    assert view["counts"]["closed"] == 1 and view["counts"]["reached_fair_value"] == 1


def test_a_closed_share_is_logged_again_only_after_the_cooldown():
    cfg = copy.deepcopy(CFG)
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.2, n=I0 + 1)}, cfg)
    n = I0 + 162  # closed two days ago
    state, _ = run(state, [pick(i0=I0 + 161, px=70.0)], n, {"AAA": rising(0.2, n=n)}, cfg)
    assert len(state["entries"]) == 1  # inside the 90-day cooldown
    n = I0 + 160 + 130
    flat = np.concatenate([rising(0.2, n=I0 + 161), np.full(n - I0 - 161, 72.0)])
    state, view = run(state, [pick(i0=n - 1, px=60.0, fair=100.0)], n, {"AAA": flat}, cfg)
    assert len(state["entries"]) == 2 and [e["status"] for e in state["entries"]] == ["closed", "open"]
    assert [r["status"] for r in view["rows"]] == ["open", "closed"]  # newest first


def test_a_share_that_runs_out_of_time_closes_at_the_end_of_the_longest_typical_wait():
    cfg = copy.deepcopy(CFG)
    cfg["bargains"]["horizon_months"] = [1, 2]  # 61 days
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.0, n=I0 + 1)}, cfg)
    n = I0 + 91
    state, view = run(state, [], n, {"AAA": rising(0.0, n=n)}, cfg)
    e = state["entries"][0]
    assert e["status"] == "closed" and e["reason"] == "time" and e["closed"] == (pd.Timestamp(day(I0)) + pd.Timedelta(days=61)).strftime("%Y-%m-%d")
    assert view["rows"][0]["days"] == 61


def test_bars_dated_after_the_as_of_date_are_not_read():
    state, view = run(None, [pick()], I0 + 1, {"AAA": rising(0.5)})  # the stored series runs on past the date the record is as of
    e = state["entries"][0]
    assert e["t1_date"] is None and e["t2_date"] is None and view["rows"][0]["ret_pct"] == 0.0 and view["rows"][0]["days"] == 0


# ------------------------------------------------------------------------------------------------ prices that change
def test_a_split_after_the_listing_does_not_look_like_a_loss():
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.0, n=I0 + 1)})  # listed at 40 with a first target of 56 and fair value 72
    n = I0 + 61
    adjusted = np.full(n, 20.0)  # a 2-for-1 split since: the stored history, the listing day included, is now in the new units
    state, view = run(state, [], n, {"AAA": adjusted})
    row = view["rows"][0]
    assert row["px"] == 20.0 and row["last"] == 20.0 and row["ret_pct"] == 0.0 and row["gap_closed_pct"] == 0.0
    assert row["target1"] == 28.0 and row["target2"] == 36.0 and row["review_below"] == 15.0 and row["fair"] == 36.0
    assert state["entries"][0]["px"] == 40.0  # what was logged stays as it was; the rescaling is read afresh every run


def test_rounding_noise_in_the_stored_close_is_not_a_corporate_action():
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.0, n=I0 + 1)})
    n = I0 + 11
    state, view = run(state, [], n, {"AAA": np.full(n, 40.0 * 1.0005)})
    assert view["rows"][0]["px"] == 40.0


# ------------------------------------------------------------------------------------------------ reading the record
def test_an_age_is_read_only_once_enough_shares_have_been_followed_that_long():
    cfg = copy.deepcopy(CFG)
    cfg["bargains"]["record_min_shares"] = 3
    syms = {"UP": 0.1, "FLAT": 0.0, "DOWN": -0.05, "BIG": 0.3}
    picks = [pick(s) for s in syms]
    state, _ = run(None, picks, I0 + 1, {s: rising(p, n=I0 + 1) for s, p in syms.items()}, cfg)
    n = int(np.searchsorted(IDX, IDX[I0] + pd.Timedelta(days=90), side="right"))  # the first trading day after the 90-day age has passed
    state, view = run(state, [], n + 5, {s: rising(p, n=n + 5) for s, p in syms.items()}, cfg)
    h90, h180 = view["horizons"][0], view["horizons"][1]
    assert h90["days"] == 90 and h90["n"] == 4 and h90["ready"] is True and view["verdict"] == "readable"
    assert h180["n"] == 0 and h180["ready"] is False and h180["ret_mean"] is None
    assert h90["ahead_index_of"] == 4 and 0 <= h90["ahead_index"] <= 4 and h90["ahead_typical_of"] == 4
    assert h90["ret_median"] is not None and h90["index_mean"] is not None and h90["typical_mean"] == pytest.approx(0.0, abs=0.1)
    # the same record under the default of 20 shares is not read at all
    _, view20 = run(state, [], n + 5, {s: rising(p, n=n + 5) for s, p in syms.items()})
    assert view20["horizons"][0]["n"] == 4 and view20["horizons"][0]["ready"] is False and view20["verdict"] == "early"


def test_the_record_is_strict_json_and_survives_a_round_trip_through_the_store():
    state, view = run(None, [pick()], I0 + 41, {"AAA": rising(0.1, n=I0 + 41)})
    for obj in (state, view):
        again = json.loads(json.dumps(obj, allow_nan=False))
        assert again == obj
    state2, view2 = run(json.loads(json.dumps(state)), [pick()], I0 + 41, {"AAA": rising(0.1, n=I0 + 41)})
    assert len(state2["entries"]) == 1 and view2["rows"] == view["rows"]
    assert view["caveats"] and "KSE-100" in view["caveats"][1] and "typical share" in view["caveats"][1]


def test_without_an_index_or_enough_liquid_shares_the_yardsticks_are_left_out_not_made_up():
    state, _ = run(None, [pick()], I0 + 1, {"AAA": rising(0.1, n=I0 + 1)})
    n = I0 + 41
    state, view = run(state, [], n, {"AAA": rising(0.1, n=n)}, bench=None, universe=[f"PSX:L{k}" for k in range(5)])
    row = view["rows"][0]
    assert row["ret_pct"] is not None and row["index_pct"] is None and row["typical_pct"] is None
    assert row["excess_index_pct"] is None and row["excess_typical_pct"] is None
