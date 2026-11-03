"""Bargains: the conservative fair value, the checks that keep value traps out, the tiers, and the file the app reads."""

from __future__ import annotations

import copy
import json

import pytest

from acumen.analysis import bargains as bg
from acumen.config import load_config
from acumen.outputs import dumps

CFG = load_config("/nonexistent.yml")  # the packaged defaults only
NOW = "2026-10-31T00:00:00Z"


def share(sym, px=100.0, eps=12.5, bvps=80.0, f=None, **item):
    """A Pakistani share that is fairly priced (P/E 8, P/B 1.25) unless told otherwise. ``f`` overrides fundamentals, the rest the item."""
    ff = {"eps": eps, "bvps": bvps, "pe": px / eps if eps else None, "pb": px / bvps, "roe": eps / bvps * 100, "eps_growth_pct": 5.0, "dy": 6.0,
          "payout_pct": 40.0, "mcap": 10e9, "shares": 1e8, "free_float_pct": 40.0}
    ff.update(f or {})
    it = {"id": f"PSX:{sym}", "sym": sym, "name": f"{sym} Ltd", "sector": "Cement", "px": px, "px_asof": "2026-10-30", "bar_close": px, "adv": 5e7,
          "chg1m": 1.0, "chg3m": 3.0, "chg1y": 10.0, "d200": 2.0, "lo52d": 20.0, "hi52d": -10.0, "beta": 1.0, "bars": 1200, "stale_days": 1, "f": ff}
    it.update(item)
    return it


def peers(n=10):
    return [share(f"P{i}") for i in range(n)]


def screen(*extra):
    return bg.screen(peers() + list(extra), CFG, now_iso=NOW, asof="2026-10-30", stale_days=1)


def row(body, sym, where="picks"):
    return next((r for r in body[where] if r["sym"] == sym), None)


# ------------------------------------------------------------------------------------------------ profit and fair value
def test_profit_is_normalised_before_it_is_capitalised():
    assert bg.normalise_eps(10.0, None) == (10.0, "unknown")
    assert bg.normalise_eps(10.0, -20.0) == (10.0, "reported")  # a fall is believed at once
    assert bg.normalise_eps(12.0, 20.0) == (11.0, "averaged")  # a rise is averaged with last year's
    assert bg.normalise_eps(10.0, 150.0) == (5.0, "rebound")  # a rebound of 100% or more may be a recovery from a loss: half counts


def test_fair_value_is_the_middle_of_three_yardsticks_never_an_average():
    f = share("X")["f"]
    stats = bg.fu.sector_stats({f"PSX:P{i}": share(f"P{i}")["f"] for i in range(10)}, {f"PSX:P{i}": "Cement" for i in range(10)})
    val = bg.fair_value(f, 100.0, 1.0, "Cement", stats, CFG["valuation"], CFG["bargains"])
    values = sorted(m["value"] for m in val["models"])
    assert len(values) == 3  # the listed values are rounded to cents, the fair value itself is not
    assert val["fair"] == pytest.approx(values[1], abs=0.01)
    assert val["low"] == pytest.approx(values[0], abs=0.01) and val["high"] == pytest.approx(values[2], abs=0.01)
    # the required return is the risk-free rate plus the full premium, with a beta of at least one
    assert bg.required_return(CFG["valuation"], 0.4) == pytest.approx(0.11 + 0.06)
    assert bg.required_return(CFG["valuation"], 2.5) == pytest.approx(0.11 + 1.6 * 0.06)


@pytest.mark.parametrize("fund", [{"eps": -1.0}, {"eps": 0.0}, {"bvps": None}, {"eps": None}])
def test_no_fair_value_without_positive_profit_and_book_value(fund):
    f = {**share("X")["f"], **fund}
    assert bg.fair_value(f, 100.0, 1.0, "Cement", {}, CFG["valuation"], CFG["bargains"]) is None


# ------------------------------------------------------------------------------------------------ what is listed
def test_a_clean_cheap_share_is_listed_in_the_tier_its_price_reaches():
    body = screen(share("HALF", px=22.0), share("CHEAP", px=40.0), share("FAIR", px=70.0))
    half, cheap = row(body, "HALF"), row(body, "CHEAP")
    assert half["tier"] == 50.0 and half["cents"] <= 50  # "a dollar for fifty cents"
    assert cheap["tier"] == 35.0 and 50 < cheap["cents"] <= 65
    assert row(body, "FAIR") is None and row(body, "FAIR", "excluded") is None  # not cheap enough to be worth reporting at all
    assert [p["sym"] for p in body["picks"]] == ["HALF", "CHEAP"]  # best tier first
    assert body["counts"]["by_tier"] == {"50": 1, "35": 1, "20": 0} and body["counts"]["picks"] == 2
    # the plan on the card: halfway to fair value, fair value, a re-read point below the purchase price, a typical wait
    assert cheap["target2"] == cheap["fair"] and cheap["px"] < cheap["target1"] < cheap["target2"]
    assert cheap["review_below"] == pytest.approx(cheap["px"] * 0.75, abs=0.01)
    assert cheap["horizon_months"] == [12, 36] and cheap["mos_pct"] == pytest.approx(100.0 - cheap["cents"], abs=1.0)
    assert cheap["low"] <= cheap["fair"] <= cheap["high"] and [m["name"] for m in cheap["models"]] == ["Earnings power", "Peer P/E", "Justified P/B"]
    assert cheap["why"].startswith("Priced at ") and "cents on the dollar" in cheap["why"]


TRAPS = [
    ("ILLIQ", {"adv": 1e6}, "a day"),
    ("TINY", {"f": {"mcap": 1e9}}, "too small"),
    ("DROP", {"chg1m": -30.0}, "fell 30% in a month"),
    ("SLIDE", {"chg3m": -40.0}, "fell 40% in three months"),
    ("ONEOFF", {"px": 12.0, "f": {"eps_growth_pct": 400.0}}, "one-off gain"),  # half of a rebound counts, so only a very low price still looks cheap
    ("SPLIT", {"f": {"pe": 8.0}}, "do not agree"),  # the ratios imply a price of 100, the quote says 40
    ("STALE", {"stale_days": 30}, "No trade for 30 days"),
    ("JUMP", {"bar_close": 80.0}, "away from the last stored close"),
    ("FLOAT", {"f": {"free_float_pct": 5.0}}, "free to trade"),
    ("DEBT", {"f": {"de_ratio": 4.0}}, "Debt is 4.0 times equity"),
]


@pytest.mark.parametrize("sym,over,reason", TRAPS)
def test_a_cheap_share_that_fails_a_check_is_reported_with_the_reason_not_listed(sym, over, reason):
    body = screen(share(sym, **{"px": 40.0, **over}))
    assert row(body, sym) is None
    excluded = row(body, sym, "excluded")
    assert excluded is not None and any(reason in r for r in excluded["reasons"]), excluded and excluded["reasons"]
    assert body["counts"]["excluded"] == len(body["excluded"])


def test_a_price_that_looks_too_good_to_be_true_is_not_believed():
    body = screen(share("PE", px=10.0))  # P/E 0.8 on profit of 12.5
    assert row(body, "PE") is None and any("too low to believe" in r for r in row(body, "PE", "excluded")["reasons"])


def test_the_excluded_cards_are_kept_small():
    ex = row(screen(share("ILLIQ", px=40.0, adv=1e6)), "ILLIQ", "excluded")
    assert ex["reasons"] and not {"models", "target1", "target2", "review_below", "why", "risks"} & set(ex)


def test_the_counts_cover_everything_that_qualified_and_the_lists_are_cut_to_their_caps():
    cfg = copy.deepcopy(CFG)
    cfg["bargains"]["max_list"] = 2
    cfg["bargains"]["max_excluded"] = 1
    extra = [share(f"C{i}", px=30.0 + i) for i in range(4)] + [share(f"I{i}", px=40.0 + i, adv=1e6) for i in range(3)]
    body = bg.screen(peers() + extra, cfg, now_iso=NOW, asof="2026-10-30", stale_days=1)
    assert body["counts"]["picks"] == 4 and len(body["picks"]) == 2
    assert sum(body["counts"]["by_tier"].values()) == 4
    assert body["counts"]["excluded"] == 3 and len(body["excluded"]) == 1
    assert [p["sym"] for p in body["picks"]] == ["C0", "C1"]  # the deepest discounts are the ones kept
    assert any("Only the 2 deepest discounts of the 4 shares that pass every check" in n for n in body["notes"])
    assert any("Only the 1 deepest discounts of the 3 cheap shares that fail a check" in n for n in body["notes"])
    quiet = screen(share("C", px=30.0))
    assert not any(n.startswith("Only the") for n in quiet["notes"])


def test_funds_are_not_valued_and_the_file_says_so():
    body = screen(share("FUND", px=20.0, sector="Open End Mutual Fund"))
    assert row(body, "FUND") is None and row(body, "FUND", "excluded") is None
    assert any("funds and real estate trusts were not valued" in n for n in body["notes"])


def test_old_prices_are_flagged_on_the_file():
    body = bg.screen(peers(), CFG, now_iso=NOW, asof="2026-10-20", stale_days=11)
    assert any("11 days old" in n for n in body["notes"]) and body["stale_days"] == 11


# ------------------------------------------------------------------------------------------------ the file
def test_tiers_describe_themselves_in_cents_on_the_dollar():
    tiers = bg._tier_defs([20, 50, 35])
    assert [t["min_mos"] for t in tiers] == [50.0, 35.0, 20.0]
    assert [t["label"] for t in tiers] == ["50 cents on the dollar or less", "51 to 65 cents on the dollar", "66 to 80 cents on the dollar"]
    assert tiers[0]["name"] == "A dollar for fifty cents"


def test_the_file_is_strict_json_and_says_what_it_does_not_cover():
    body = screen(share("HALF", px=22.0), share("ILLIQ", px=40.0, adv=1e6))
    text = dumps(body).decode()

    def refuse(token):
        raise ValueError(f"{token} is not valid JSON")

    again = json.loads(text, parse_constant=refuse)
    assert again["picks"][0]["sym"] == "HALF" and set(again["scope"]) == {"psx", "fx", "crypto"}
    assert again["caveats"] and again["method"]["required_return_pct"] == pytest.approx(17.0)
    assert [m["name"] for m in again["method"]["models"]] == ["Earnings power", "Peer P/E", "Justified P/B"]
    assert all(p["id"].startswith("PSX:") for p in again["picks"] + again["excluded"])


def test_the_home_tile_is_a_short_form_of_the_file():
    body = screen(*[share(f"C{i}", px=30.0 + i) for i in range(5)])
    tile = bg.summary(body)
    assert tile["counts"] == body["counts"] and len(tile["top"]) == 3
    assert [t["sym"] for t in tile["top"]] == [p["sym"] for p in body["picks"][:3]]
    assert set(tile["top"][0]) == {"id", "sym", "name", "px", "fair", "mos_pct", "cents", "tier"}
