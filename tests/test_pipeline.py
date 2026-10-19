"""The whole analysis on synthetic markets: what the app is told, and above all that nothing is called actionable unless the
evidence behind it says so. One world has trends planted in stocks and forex (the coins are noise), the other has nothing to find."""

from __future__ import annotations

import datetime as dt
import json
import types

import pytest

from acumen import cli

END = "2026-10-09"
NOW = dt.datetime(2026, 10, 12, 12, 0, tzinfo=dt.timezone.utc)


def build(root, *extra):
    code = cli.main(["-q", "demo", "--out", str(root / "site"), "--days", "1500", "--end", END, "--workers", "2", *extra], env={}, now=NOW)
    assert code == 0
    return load(root / "site")


def strict(text: str):
    def refuse(token):
        raise ValueError(f"{token} is not valid JSON")

    return json.loads(text, parse_constant=refuse)


def load(site):
    meta = strict((site / "meta.json").read_text())
    files = {name: strict((site / path).read_text()) for name, path in meta["files"].items()}
    return types.SimpleNamespace(site=site, meta=meta, files=files, ideas=files["ideas"]["ideas"], pools=files["calibration"]["markets"], overview=files["overview"])


@pytest.fixture(scope="module")
def structured(tmp_path_factory):
    return build(tmp_path_factory.mktemp("structured"))


@pytest.fixture(scope="module")
def noise(tmp_path_factory):
    return build(tmp_path_factory.mktemp("noise"), "--noise")


def pool(world, market, horizon):
    return world.pools[market]["horizons"][horizon]


# ------------------------------------------------------------------------------------------------ what is written
def test_every_file_is_strict_json_and_present(structured):
    meta = structured.meta
    assert meta["demo"] is True and set(meta["markets"]) == {"psx", "fx", "crypto"}
    assert all((structured.site / path).exists() for path in meta["files"].values())
    detail = list((structured.site / "detail").rglob("*.json"))
    assert len(detail) >= 100
    for path in detail[:25]:
        strict(path.read_text())
    assert any("DEMO DATA" in a["text"] for a in structured.overview["alerts"])


def test_the_published_ideas_agree_with_the_evidence_behind_them(structured):
    assert structured.ideas
    for idea in structured.ideas:
        edge = pool(structured, idea["market"], idea["horizon"])["edge"]
        assert idea["actionable"] == (not idea["blockers"]), idea["id"]
        if idea["actionable"]:
            assert edge["tested"] and edge["passed"], f"{idea['id']} {idea['horizon']} is actionable but its pool did not beat random entries"
            assert idea["evidence"]["skill"] is True and idea["exp_r"] > 0
        elif not edge["passed"] and idea["action"] != "AVOID":
            assert "no_edge" in idea["blockers"], f"{idea['id']} {idea['horizon']}: the pool has no proven edge, so the idea must say so"


def test_a_pool_that_cannot_be_tested_says_why_and_is_not_actionable(structured):
    for market in ("psx", "fx", "crypto"):
        long = pool(structured, market, "long")["edge"]
        assert long["tested"] is False and long["passed"] is False and "stretches" in long["why"]
        assert not [i for i in structured.ideas if i["market"] == market and i["horizon"] == "long" and i["actionable"]]


def test_the_edge_test_reports_what_it_compared(structured):
    e = pool(structured, "psx", "medium")["edge"]
    assert e["tested"] and e["matched"] >= 0.9 * e["n"] and e["instruments"] >= 30 and e["spans"] >= 10
    assert e["lag_days"] >= 60 and e["match_days"] == 14 and e["alpha"] == 0.01 and e["level"] == 0.98
    assert e["ci"][0] < e["edge_r"] < e["ci"][1] and 0 <= e["p"] <= 1 and e["df"] >= 2


# ------------------------------------------------------------------------------------------------ the planted world
def test_trends_planted_in_stocks_and_forex_are_found_and_pure_noise_is_not(structured):
    assert pool(structured, "psx", "medium")["edge"]["passed"] and pool(structured, "fx", "medium")["edge"]["passed"]
    for horizon in ("short", "medium"):
        e = pool(structured, "crypto", horizon)["edge"]
        assert e["tested"] and not e["passed"], (horizon, e)
    actionable = [i for i in structured.ideas if i["actionable"]]
    assert actionable and {i["market"] for i in actionable} <= {"psx", "fx"} and not [i for i in actionable if i["horizon"] == "long"]
    assert any("not clearly better than random" in a["text"] or "no better" in a["text"] or "Positive past results" in a["text"] for a in structured.overview["alerts"])


def test_beating_random_entries_is_not_enough_when_costs_eat_the_difference(structured):
    # swing trades on the demo exchange beat random entries before costs but lose money after them: research only
    e = pool(structured, "psx", "short")
    assert e["edge"]["passed"] and e["avg_r"] < 0.02
    swing = [i for i in structured.ideas if i["market"] == "psx" and i["horizon"] == "short" and i["action"] != "AVOID"]
    assert swing and not [i for i in swing if i["actionable"]]
    assert all("negative_expectancy" in i["blockers"] for i in swing)


# ------------------------------------------------------------------------------------------------ the world without structure
def test_markets_with_nothing_to_find_produce_nothing_actionable(noise):
    assert noise.ideas, "ideas are still listed, as research candidates"
    assert not [i for i in noise.ideas if i["actionable"]]
    for market in noise.pools:
        for horizon, p in noise.pools[market]["horizons"].items():
            assert not p["edge"]["passed"], (market, horizon, p["edge"])
    assert all(i["blockers"] for i in noise.ideas)
    assert any("research" in a["text"].lower() or "random entries" in a["text"] for a in noise.overview["alerts"])
