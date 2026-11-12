"""Money flows: the buying-pressure measure checked against a plain loop, the sector states, the candidate screen, the weekly report and
archive, and above all the backtest: it must find a relationship planted in a world and report nothing in a world without one."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from acumen.analysis import flows as fl
from acumen.analysis import flows_backtest as fb

DEFAULTS: dict = {}


def get(key, default=None):
    return DEFAULTS.get(key, default)


# ------------------------------------------------------------------------------------------------ worlds
def make_world(seed: int, days: int = 800, sectors: int = 10, per: int = 5, gamma: float = 0.0):
    """Stocks in sectors with random daily moves. With ``gamma`` > 0 a sector whose last week showed more buying pressure than the others
    gets extra drift the next day, so the measure really does predict what happens next."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=days)
    n = sectors * per
    sec_idx = np.repeat(np.arange(sectors), per)
    vol = rng.lognormal(12.0, 0.3, size=(days, n))
    close = np.empty((days, n))
    close[0] = 100.0
    flow = np.zeros((days, n))
    common = rng.normal(0, 0.006, size=days)
    for t in range(1, days):
        lo = max(1, t - 5)
        S, V = flow[lo:t].sum(axis=0), (close[lo:t] * vol[lo:t]).sum(axis=0)
        ps = np.array([S[sec_idx == k].sum() / max(V[sec_idx == k].sum(), 1.0) for k in range(sectors)])
        drift = gamma * (ps - ps.mean())
        r = rng.normal(0, 0.012, n) + drift[sec_idx] + common[t]
        close[t] = close[t - 1] * (1 + r)
        flow[t] = np.sign(r) * close[t] * vol[t]
    ids = [f"PSX:S{i:03d}" for i in range(n)]
    bars = {i: pd.DataFrame({"open": close[:, k], "high": close[:, k], "low": close[:, k], "close": close[:, k], "volume": vol[:, k]}, index=dates) for k, i in enumerate(ids)}
    shares = [{"id": i, "sym": f"S{k:03d}", "name": f"Share {k}", "sector": f"Sector {sec_idx[k]}"} for k, i in enumerate(ids)]
    return bars, shares


def panel_for(bars, shares):
    p = fl.make_panel(bars, shares)
    assert p is not None
    return p


def backtest(bars, shares, **over):
    DEFAULTS.clear()
    DEFAULTS.update(over)
    p = panel_for(bars, shares)
    fr = fl.indicator_frames(p, fl.eligible_sectors(p, 3))
    return fb.run(p, fr, fl.stock_pressure(p, 7), fl.stock_pressure(p, 30), get)


# ------------------------------------------------------------------------------------------------ the measure
def test_buying_pressure_matches_a_plain_loop():
    bars, shares = make_world(1, days=120, sectors=4, per=3)
    p = panel_for(bars, shares)
    fr = fl.indicator_frames(p, fl.eligible_sectors(p, 3))
    day = p.dates[-1]
    for sector in ("Sector 0", "Sector 3"):
        ids = [s["id"] for s in shares if s["sector"] == sector]
        num = den = 0.0
        for d in p.dates[(p.dates > day - pd.Timedelta(days=7)) & (p.dates <= day)]:
            for i in ids:
                prev = p.dates[p.dates.get_loc(d) - 1]
                c, c0, v = bars[i].loc[d, "close"], bars[i].loc[prev, "close"], bars[i].loc[d, "close"] * bars[i].loc[d, "volume"]
                num += math.copysign(v, c - c0) if c != c0 else 0.0
                den += v
        assert fr.pvf[7].loc[day, sector] == pytest.approx(num / den, rel=1e-9)
        assert -1.0 <= fr.pvf[7].loc[day, sector] <= 1.0 and -1.0 <= fr.pvf[30].loc[day, sector] <= 1.0


def test_a_flat_day_and_a_day_without_trading_count_as_neither_buying_nor_selling():
    dates = pd.bdate_range("2024-01-01", periods=40)
    close = pd.Series(100.0, index=dates)
    close.iloc[20:] = 101.0  # one up day (day 20), flat otherwise
    vol = pd.Series(10.0, index=dates)
    vol.iloc[10] = 0.0  # a day with no trading
    bars = {f"PSX:A{k}": pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": vol}) for k in range(3)}
    shares = [{"id": i, "sym": i[4:], "name": i, "sector": "X"} for i in bars]
    p = panel_for(bars, shares)
    assert p.flow.iloc[20].sum() == pytest.approx(3 * 101.0 * 10.0)  # the one up day
    assert p.flow.iloc[19].sum() == 0.0 and p.flow.iloc[21].sum() == 0.0  # flat days
    assert p.value.iloc[10].isna().all()  # no volume, no value


def test_too_few_shares_or_history_gives_no_panel():
    bars, shares = make_world(2, days=120, sectors=1, per=2)
    assert fl.make_panel(bars, shares) is None
    bars, shares = make_world(2, days=20, sectors=2, per=3)
    assert fl.make_panel(bars, shares) is None  # fewer than 30 bars each


def test_sector_states():
    cut = 0.5
    assert fl.classify(1.0, 0.9, cut) == "entering"
    assert fl.classify(-1.0, -0.6, cut) == "leaving"
    assert fl.classify(1.2, 0.1, cut) == "turning_in"
    assert fl.classify(-1.2, 0.3, cut) == "fading"
    assert fl.classify(0.2, 0.9, cut) == "neutral"
    assert fl.classify(None, 1.0, cut) == "neutral" and fl.classify(float("nan"), 1.0, cut) == "neutral"


def test_a_sector_is_judged_against_the_market_not_in_isolation():
    # every share rises every day: pressure is +100% everywhere, so no sector stands out, whatever its absolute level
    dates = pd.bdate_range("2024-01-01", periods=200)
    bars, shares = {}, []
    for s in range(4):
        for k in range(3):
            c = pd.Series(100.0 * (1.001 + 0.0001 * s) ** np.arange(200), index=dates)
            iid = f"PSX:Q{s}{k}"
            bars[iid] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1000.0})
            shares.append({"id": iid, "sym": iid[4:], "name": iid, "sector": f"Sector {s}"})
    p = panel_for(bars, shares)
    fr = fl.indicator_frames(p, fl.eligible_sectors(p, 3))
    day = p.dates[-1]
    assert np.allclose(fr.pvf[7].loc[day].values, 1.0) and np.allclose(fr.rel[7].loc[day].values, 0.0, atol=1e-9)
    rows = fl.sector_table(p, fr, day, {i: 1e7 for i in bars}, get)
    assert {r["state"] for r in rows} == {"neutral"}  # z-scores of nothing are nothing


# ------------------------------------------------------------------------------------------------ the table, the screen and the report
def full_build(seed=3, **kw):
    DEFAULTS.clear()
    bars, shares = make_world(seed, days=500, sectors=8, per=5, gamma=0.0)
    funds = {s["id"]: {"eps": 5.0, "eps_growth_pct": 25.0, "net_margin_pct": 10.0, "roe": 15.0, "pe": 8.0} for s in shares}
    args = dict(bars=bars, shares=shares, funds=funds, asof=str(bars[shares[0]["id"]].index[-1].date()), adv={s["id"]: 2e7 for s in shares}, bargain_ids=set(), regime=None,
                macro={"usd_pkr": 280.0}, archive=None, now_iso="2026-01-01T00:00:00Z")
    args.update(kw)
    return fl.build(get, **args), args


def test_the_flows_file_has_what_the_page_needs():
    (out, archive), args = full_build()
    assert out["ready"] and out["windows"] == {"fast": 7, "slow": 30} and out["investor"]["available"] is False
    names = [r["name"] for r in out["sectors"]]
    assert len(names) == 8 == out["counts"]["sectors"]
    scores = [r["score"] for r in out["sectors"] if r["score"] is not None]
    assert scores == sorted(scores, reverse=True)  # best first
    for r in out["sectors"]:
        assert r["state"] in fl.STATES and r["n"] == 5 and r["thin"] is False and len(r["top"]) == 3
        assert -100 <= r["pressure7"] <= 100 and -100 <= r["pressure30"] <= 100
    rep = out["report"]
    assert rep["week"] == fl.iso_week(out["asof"]) and rep["asof"] == out["asof"] and rep["lines"]
    assert archive["weeks"][-1]["week"] == rep["week"] and set(archive["weeks"][-1]["score"]) <= set(names)
    assert out["backtest"]["verdict"] in ("supported", "partial", "reversed", "not_shown", "too_short") and out["backtest"]["tests"]
    assert out["macro"] == {"usd_pkr": 280.0}


def test_nothing_is_built_when_there_is_nothing_to_compare():
    bars, shares = make_world(4, days=300, sectors=3, per=3)  # three sectors: fewer than four
    out, archive = fl.build(get, bars=bars, shares=shares, funds={}, asof="2022-03-01", adv={}, bargain_ids=set(), regime=None, macro=None, archive=None, now_iso="x")
    assert out["ready"] is False and "note" in out and archive is None and out["investor"]["available"] is False
    out, archive = fl.build(get, bars={}, shares=[], funds={}, asof=None, adv={}, bargain_ids=set(), regime=None, macro=None, archive=None, now_iso="x")
    assert out["ready"] is False and archive is None
    assert fl.summary(out) == {"ready": False}


def screen_panel():
    """Twelve quiet shares, three that do something: UP (steady buying, rising turnover), CHASED (already up 40% in a month), LOSS (loss-making)."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2025-01-01", periods=200)
    bars, shares = {}, []

    def add(name, close, vol, sector="A"):
        iid = f"PSX:{name}"
        bars[iid] = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": vol}, index=dates)
        shares.append({"id": iid, "sym": name, "name": name, "sector": sector})

    for k in range(12):
        c = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 200)), index=dates)
        add(f"Q{k}", c, pd.Series(rng.uniform(900, 1100, 200), index=dates), "A" if k < 6 else "B")
    base = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.003, 200)), index=dates)
    up = base.copy()
    up.iloc[-22:] = up.iloc[-23] * np.cumprod(np.full(22, 1.004))  # a steady rise, +9% in the month
    vol = pd.Series(1000.0, index=dates)
    vol.iloc[-6:] = 2500.0
    add("UP", up, vol)
    chased = base.copy()
    chased.iloc[-22:] = chased.iloc[-23] * np.cumprod(np.full(22, 1.017))  # +45%
    add("CHASED", chased, vol)
    add("LOSS", up.copy(), vol)
    return bars, shares, dates


def test_the_candidate_screen_needs_all_its_reasons_and_says_them():
    DEFAULTS.clear()
    bars, shares, dates = screen_panel()
    p = panel_for(bars, shares)
    funds = {s["id"]: {"eps": 1.0, "eps_growth_pct": 2.0} for s in shares}
    funds["PSX:UP"] = {"eps": 8.0, "eps_growth_pct": 40.0, "net_margin_pct": 12.0, "roe": 18.0, "pe": 9.0}
    funds["PSX:CHASED"] = {"eps": 8.0, "eps_growth_pct": 40.0, "net_margin_pct": 12.0}
    funds["PSX:LOSS"] = {"eps": -2.0, "eps_growth_pct": 50.0}
    out = fl.candidates(p, dates[-1], {"A": "entering", "B": "leaving"}, funds, {s["id"]: 3e7 for s in shares}, {"PSX:UP"}, get)
    by = {c["sym"]: c for c in out}
    up = by["UP"]
    assert up["pillars"] == 3 and up["accumulation"] and up["fundamentals"] and up["interest"] and up["bargain"] is True
    assert up["sector_state"] == "entering" and len(up["why"]) == 3 and "up 40%" in up["why"][1] and "turnover" in up["why"][2]
    assert "CHASED" not in by or not by["CHASED"]["accumulation"]  # already up far too much in a month: not accumulation
    assert "LOSS" not in by or not by["LOSS"]["fundamentals"]  # a loss is not improving fundamentals
    assert all(c["pillars"] >= 2 for c in out) and out[0]["sym"] == "UP"


def rows_for(states: dict[str, str]):
    return [{"name": n, "state": s, "score": float(i)} for i, (n, s) in enumerate(states.items())]


def test_the_weekly_report_names_where_money_is_going_and_what_changed():
    rows = rows_for({"Cement": "entering", "Banks": "entering", "Textile": "leaving", "Power": "turning_in", "Autos": "fading", "Sugar": "neutral"})
    cands = [{"id": "PSX:LUCK", "sym": "LUCK", "sector": "Cement", "sector_state": "entering", "pillars": 3},
             {"id": "PSX:XYZ", "sym": "XYZ", "sector": "Textile", "sector_state": "leaving", "pillars": 3}]
    market = {"pressure7": 12.0, "pressure30": 3.5, "turnover": 0.9}
    prev = {"entering": ["Textile"], "leaving": ["Cement"], "turning_in": []}
    rep = fl.weekly_report(rows, cands, market, prev, "2026-10-30")
    assert rep["entering"] == ["Cement", "Banks"] and rep["leaving"] == ["Textile"] and rep["turning_in"] == ["Power"] and rep["fading"] == ["Autos"]
    assert rep["beneficiaries"] == ["PSX:LUCK"]  # a share in a leaving sector is not a beneficiary of the rotation
    text = " ".join(rep["lines"])
    assert "Money is entering: Cement and Banks." in text and "Money is leaving: Textile." in text and "LUCK" in text and "XYZ" not in text
    moved = {(c["sector"], c["from"], c["to"]) for c in rep["changes"]}
    assert ("Cement", "leaving", "entering") in moved and ("Textile", "entering", "leaving") in moved and ("Power", "neutral", "turning_in") in moved
    assert not any(c["sector"] == "Sugar" for c in rep["changes"])  # neutral to neutral is not a change
    empty = fl.weekly_report(rows_for({"A": "neutral", "B": "neutral"}), [], {}, None, "2026-10-30")
    assert "No sector shows buying pressure" in empty["lines"][0] and empty["changes"] == []


def test_the_archive_keeps_one_entry_per_week_and_a_limited_history():
    def entry(week, asof):
        return {"week": week, "asof": asof, "entering": ["A"], "leaving": ["B"], "turning_in": [], "fading": [], "beneficiaries": [], "score": {}, "market": {}}
    state = fl.update_archive(None, entry("2026-W40", "2026-10-02"))
    state = fl.update_archive(state, entry("2026-W41", "2026-10-09"))
    again = entry("2026-W41", "2026-10-10")
    again["entering"] = ["C"]
    state = fl.update_archive(state, again)  # the same week built again: replaced, not added
    assert [w["week"] for w in state["weeks"]] == ["2026-W40", "2026-W41"] and state["weeks"][1]["entering"] == ["C"]
    for k in range(150):
        state = fl.update_archive(state, entry(f"2027-W{k:03d}", f"2027-01-01") | {"asof": f"2027-{1 + k // 28:02d}-{1 + k % 28:02d}"})
    assert len(state["weeks"]) == fl.KEEP_WEEKS and state["weeks"] == sorted(state["weeks"], key=lambda w: w["asof"])
    assert fl.iso_week("2026-10-30") == "2026-W44" and fl.iso_week("2027-01-01") == "2026-W53"


def test_the_live_check_scores_each_call_only_after_enough_days_have_passed():
    dates = pd.bdate_range("2025-01-01", periods=120)
    bars, shares = {}, []
    for s, drift in (("A", 0.004), ("B", -0.004), ("C", 0.0), ("D", 0.0)):
        for k in range(3):
            c = pd.Series(100 * np.cumprod(np.full(120, 1 + drift)), index=dates)
            iid = f"PSX:{s}{k}"
            bars[iid] = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1000.0})
            shares.append({"id": iid, "sym": iid[4:], "name": iid, "sector": s})
    p = panel_for(bars, shares)
    old, recent = dates[60], dates[-3]
    state = {"v": 1, "weeks": [
        {"week": "w1", "asof": str(old.date()), "entering": ["A"], "leaving": ["B"]},
        {"week": "w2", "asof": str(recent.date()), "entering": ["A"], "leaving": ["B"]},  # only 3 days old: cannot be scored at 5 or 21 days
    ]}
    out = fl.live_check(state, p, dates[-1])
    assert out["weeks"] == 2 and out["horizons"]["5"]["n"] == 1 and out["horizons"]["21"]["n"] == 1
    assert out["horizons"]["5"]["hit"] == 100.0 and out["horizons"]["5"]["mean_spread"] > 3.0  # entering sector A beat leaving sector B
    assert [r["week"] for r in out["rows"]] == ["w1"]
    assert fl.live_check({"v": 1, "weeks": []}, p, dates[-1])["horizons"]["5"] == {"n": 0, "mean_spread": None, "hit": None}


# ------------------------------------------------------------------------------------------------ the backtest
def test_newey_west_agrees_with_the_plain_t_for_independent_data_and_widens_for_overlapping_data():
    rng = np.random.default_rng(0)
    x = rng.normal(0.2, 1.0, 400)
    mu, t0 = fb.newey_west(x, 0)
    assert mu == pytest.approx(x.mean()) and t0 == pytest.approx(x.mean() / (x.std() / math.sqrt(len(x))), rel=1e-6)
    smooth = np.convolve(rng.normal(0, 1.0, 410), np.ones(5) / 5, mode="valid")[:400] + 0.05  # neighbours share 4/5 of their noise
    assert abs(fb.newey_west(smooth, 5)[1]) < abs(fb.newey_west(smooth, 0)[1])
    assert all(math.isnan(v) for v in fb.newey_west([1.0, 2.0, 3.0], 2))  # too short to say anything


def test_a_planted_relationship_is_found():
    bars, shares = make_world(11, days=900, gamma=0.02)
    bt = backtest(bars, shares)
    prim = {t["id"]: t for t in bt["tests"] if t["primary"]}
    assert set(prim) == {"sector_fast_5", "sector_slow_21"}
    assert prim["sector_fast_5"]["verdict"] == "supported" and prim["sector_fast_5"]["t"] >= 2.5 and prim["sector_fast_5"]["ic"] > 0 and prim["sector_fast_5"]["spread"] > 0
    assert bt["verdict"] in ("supported", "partial") and bt["anchors"] >= 100 and bt["from"] < bt["to"]


def test_a_world_without_one_is_not_called_a_signal():
    verdicts = []
    for seed in (21, 22, 23, 24):
        bars, shares = make_world(seed, days=900, gamma=0.0)
        bt = backtest(bars, shares)
        verdicts.append(bt["verdict"])
        assert all(t["verdict"] == "none" for t in bt["tests"] if t["primary"]), (seed, [(t["id"], t["t"]) for t in bt["tests"] if t["primary"]])
    assert set(verdicts) == {"not_shown"}


def test_the_wrong_way_round_is_reported_as_reversed():
    bars, shares = make_world(31, days=900, gamma=-0.02)
    bt = backtest(bars, shares)
    assert bt["verdict"] == "reversed" and "wrong way round" in bt["headline"]
    assert next(t for t in bt["tests"] if t["id"] == "sector_fast_5")["verdict"] == "reversed"


def test_a_short_history_gets_no_verdict():
    bars, shares = make_world(41, days=260, gamma=0.02)
    bt = backtest(bars, shares)
    assert bt["verdict"] == "too_short" and bt["anchors"] < 52 and "at least 52" in bt["headline"]
    bars, shares = make_world(42, days=60)
    bt = backtest(bars, shares)
    assert bt["verdict"] == "too_short" and bt["tests"] == []


def test_the_backtest_only_uses_what_was_known_on_the_day():
    """Changing prices after an anchor must not change that anchor's measure."""
    bars, shares = make_world(51, days=300, sectors=5, per=4)
    p1 = panel_for(bars, shares)
    cut = p1.dates[200]
    changed = {i: b.copy() for i, b in bars.items()}
    for b in changed.values():
        b.loc[b.index > cut, ["close", "volume"]] *= 3.0  # a different future
    p2 = panel_for(changed, shares)
    f1, f2 = fl.indicator_frames(p1, fl.eligible_sectors(p1, 3)), fl.indicator_frames(p2, fl.eligible_sectors(p2, 3))
    assert f1.score.loc[:cut].equals(f2.score.loc[:cut]) or np.allclose(f1.score.loc[:cut].fillna(0).values, f2.score.loc[:cut].fillna(0).values)
    for w in (7, 30):
        assert np.allclose(f1.pvf[w].loc[:cut].fillna(0).values, f2.pvf[w].loc[:cut].fillna(0).values)


def test_the_stock_level_tests_are_reported_with_their_spread_and_their_own_bar():
    bars, shares = make_world(61, days=900, gamma=0.0)
    bt = backtest(bars, shares)
    stock = [t for t in bt["tests"] if t["level"] == "stock"]
    assert {t["id"] for t in stock} == {"stock_fast_5", "stock_slow_21"} and all(not t["primary"] for t in stock)
    assert all(t["n"] > 100 and t["spread"] is not None and t["hit"] is not None for t in stock)
    assert len(bt["stock_lines"]) == 2 and "before any costs" in bt["stock_lines"][0] and len(bt["notes"]) >= 4
