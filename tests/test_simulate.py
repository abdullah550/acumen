"""Golden scenarios for the trade simulation (hand-built bars, hand-computed answers)."""

import numpy as np
import pytest

from acumen.analysis.simulate import PlanLevels, plan_levels, simulate_trade


def bars(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]  # o h l c


def plan(sign=1, kind="market", ref=100.0, dist=5.0, r=(1.5, 2.5)):
    stop = ref - sign * dist
    return PlanLevels(sign, kind, ref, stop, [ref + sign * x * dist for x in r], dist, "atr")


KW = dict(time_stop=5, wait=3, scale_out=[0.5, 0.5], cost_frac=0.0)


def sim(rows, pl, approx=None, **over):
    o, h, l, c = bars(rows)
    kw = {**KW, **over}
    return simulate_trade(0, pl, o, h, l, c, approx, **kw)


SIG = (100, 100, 100, 100)  # bar 0 is the signal bar


def test_long_target1_then_target2():
    t = sim([SIG, (100, 103, 99, 102), (102, 108, 101, 107), (107, 113, 106, 112), (112, 112, 112, 112), (112, 112, 112, 112), (112, 112, 112, 112)], plan())
    assert t["status"] == "closed" and t["reason"] == "target2" and t["hit1"] and t["hit2"]
    assert t["r"] == pytest.approx(0.5 * 1.5 + 0.5 * 2.5)
    assert t["bars"] == 3 and t["bars_t1"] == 2 and t["bars_t2"] == 3


def test_long_breakeven_after_target1():
    t = sim([SIG, (100, 103, 99, 102), (102, 108, 101, 107), (107, 109, 99.5, 100), (100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100)], plan())
    assert t["reason"] == "target1" and t["hit1"] and not t["hit2"]
    assert t["r"] == pytest.approx(0.75)  # half banked at 1.5R, the rest stopped at entry


def test_long_gap_through_stop_fills_at_open():
    t = sim([SIG, (100, 101, 99.5, 100.5), (93, 94, 92, 93), (93, 93, 93, 93)], plan())
    assert t["reason"] == "stop" and t["exit"] == pytest.approx(93.0)
    assert t["r"] == pytest.approx((93 - 100) / 5)


def test_stop_assumed_first_when_bar_reaches_both():
    t = sim([SIG, (100, 108, 94, 100), (100, 100, 100, 100)], plan())
    assert t["reason"] == "stop" and t["r"] == pytest.approx(-1.0)


def test_time_stop_exit_at_close():
    rows = [SIG] + [(100, 101, 99, 100.5)] * 3 + [(100, 101, 99, 101.0), (100, 101, 99, 103.0), (100, 101, 99, 50.0)]
    t = sim(rows, plan())
    # fill bar 1 counts as bar 1, so bar 5 is the last: close 103
    assert t["reason"] == "time" and t["exit_idx"] == 5
    assert t["r"] == pytest.approx((103 - 100) / 5)


def test_limit_fill_intrabar_and_no_credit_for_fill_bar_upside():
    pl = plan(kind="limit", ref=98.0)  # stop 93, T1 105.5, T2 110.5
    t = sim([SIG, (100, 107, 97.5, 106), (106, 107, 105, 106), (106, 107, 105, 106), (106, 106, 106, 106), (106, 106, 106, 106), (106, 106, 106, 106)], pl)
    assert t["fill"] == pytest.approx(98.0) and t["fill_idx"] == 1
    # the fill bar reached 107 but the fill happened mid-bar: target 1 is only credited from the next bar
    assert t["hit1"] and t["bars_t1"] == 2


def test_limit_cancelled_when_target1_reached_before_fill():
    pl = plan(kind="limit", ref=98.0)
    assert sim([SIG, (101, 106, 100, 105), (105, 105, 105, 105)], pl) is None


def test_limit_expires_after_wait_bars():
    pl = plan(kind="limit", ref=98.0)
    rows = [SIG] + [(100, 101, 99.5, 100)] * 5
    assert sim(rows, pl) is None


def test_limit_fills_at_open_when_it_opens_through_the_limit():
    pl = plan(kind="limit", ref=98.0)
    t = sim([SIG, (96, 97, 95.5, 96.5), (96.5, 97, 96, 96.5), (96.5, 97, 96, 96.5), (96.5, 97, 96, 96.5), (96.5, 97, 96, 96.5), (96.5, 97, 96, 96.5)], pl)
    assert t["fill"] == pytest.approx(96.0 + 0.0) or t["fill"] == pytest.approx(96.0)
    assert t["fill"] == pytest.approx(96.0)


def test_short_mirrors_long():
    pl = plan(sign=-1)  # stop 105, T1 92.5, T2 87.5
    t = sim([SIG, (100, 101, 97, 98), (98, 99, 91, 92), (92, 93, 86, 88), (88, 88, 88, 88), (88, 88, 88, 88), (88, 88, 88, 88)], pl)
    assert t["reason"] == "target2" and t["r"] == pytest.approx(2.0)


def test_short_gap_up_through_stop():
    t = sim([SIG, (100, 100.5, 99, 99.5), (108, 110, 107, 109), (109, 109, 109, 109)], plan(sign=-1))
    assert t["reason"] == "stop" and t["exit"] == pytest.approx(108.0)
    assert t["r"] == pytest.approx(-(108 - 100) / 5)


def test_costs_in_r():
    rows = [SIG, (100, 103, 99, 102), (102, 108, 101, 107), (107, 113, 106, 112), (112, 112, 112, 112), (112, 112, 112, 112), (112, 112, 112, 112)]
    t = sim(rows, plan(), cost_frac=0.01)
    assert t["cost_r"] == pytest.approx(0.01 * 100 / 5)
    assert t["r"] == pytest.approx(2.0 - 0.2)


def test_gap_beside_stop_is_skipped():
    # opens 1 above the stop: R distance would be 1, less than a quarter of the plan: not tradable
    assert sim([SIG, (96, 97, 95.5, 96), (96, 96, 96, 96)], plan()) is None


def test_open_trade_when_data_ends():
    t = sim([SIG, (100, 101, 99.5, 100.5), (100.5, 101, 100, 101)], plan())
    assert t["status"] == "open" and t["reason"] == "open"
    assert t["r"] == pytest.approx((101 - 100) / 5)


def test_approx_bars_use_close_only():
    # bar 1 has an approximated range that would touch the stop, but closes are what count there
    rows = [SIG, (100, 100.5, 94, 100.2), (100.2, 101, 100, 100.6), (100.6, 101, 100, 100.6), (100.6, 101, 100, 100.6), (100.6, 101, 100, 100.6)]
    approx = np.array([False, True, True, True, True, True])
    t = sim(rows, plan(), approx=approx)
    assert t["reason"] == "time" and t["r"] == pytest.approx(0.6 / 5)


def test_plan_levels_snaps_to_nearby_swing_only():
    pcfg = {"stop_atr": 2.0, "max_stop_atr": 4.0, "targets_r": [1.5, 2.5]}
    # swing 2.2 ATR below + 0.25 buffer = 2.45 ATR vs base 2.0 -> inside the 0.75..1.35 window (1.5..2.7): use it
    pl = plan_levels(100.0, 1.0, 97.8, 1, "Trend following", pcfg)
    assert pl.stop_kind == "swing" and pl.dist == pytest.approx(2.45)
    # far swing is ignored
    pl = plan_levels(100.0, 1.0, 92.0, 1, "Trend following", pcfg)
    assert pl.stop_kind == "atr" and pl.dist == pytest.approx(2.0)
    # limit setups place the reference below the close for longs, above for shorts
    assert plan_levels(100.0, 1.0, None, 1, "Trend pullback", pcfg).ref == pytest.approx(99.5)
    assert plan_levels(100.0, 1.0, None, -1, "Trend rally fade", pcfg).ref == pytest.approx(100.5)


def test_no_lookahead_in_fills():
    """Appending bars after the exit must not change a closed trade."""
    rows = [SIG, (100, 103, 99, 102), (102, 108, 101, 107), (107, 113, 106, 112), (112, 112, 112, 112)]
    a = sim(rows, plan())
    b = sim(rows + [(50, 60, 40, 55)] * 10, plan())
    assert a["r"] == b["r"] and a["exit_idx"] == b["exit_idx"]


def test_entry_that_opens_beyond_the_stop_is_not_taken():
    assert sim([SIG, (93, 94, 92, 93), (93, 93, 93, 93)], plan()) is None


def test_short_targets_never_go_below_zero():
    pcfg = {"stop_atr": 4.0, "max_stop_atr": 5.0, "targets_r": [3.0, 6.0], "scale_out": [0.4, 0.6]}
    # ATR 9% of price, stop 4 ATR => 36% above entry: 3R target would be -8%: no plan at all
    assert plan_levels(100.0, 9.0, None, -1, "Breakout", pcfg) is None
    # ATR 5% => stop 20% above; T1 at -60% is fine, T2 would be -120% so it is clamped to the floor
    p = plan_levels(100.0, 5.0, None, -1, "Breakout", pcfg)
    assert p is not None
    assert all(x >= 10.0 - 1e-9 for x in p.targets)
    assert p.targets[0] > p.targets[1] >= 10.0 - 1e-9
    # longs are unaffected
    q = plan_levels(100.0, 9.0, None, 1, "Breakout", pcfg)
    assert q is not None and q.targets[1] > q.targets[0] > 100.0
