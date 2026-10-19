"""The command line, run in-process against the simulated services (``tests/fake_world.py``)."""

from __future__ import annotations

import json
import types

import pytest

from acumen import cli, crypto_box
from acumen.config import load_config
from acumen.fetch import psx_dps
from tests.fake_world import Worlds

CONFIG = """\
data:
  psx: {history_years: 1.2, ohlc_days: 330}
  fx: {history_years: 3}
  crypto: {history_days: 400}
universe:
  crypto: {top_n: 50, min_bars: 120}
  fx: {min_bars: 200}
  psx: {min_bars: 120}
doctor: {psx_shares: 10, psx_watch: 10, tradingview_shares: 10, exchange_markets: 2, psx_history_bars: 200, yahoo_history_bars: 60, coins: 5}
"""
GOOD = "a long passphrase for the tests"


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr(psx_dps, "_resolve", lambda: [])


def write_config(root, extra: str = ""):
    path = root / "config.yml"
    path.write_text(CONFIG + extra)
    return path


def run(root, *args, w: Worlds | None = None, env=None, cfg=None, quiet=True, http=True):
    w = w or Worlds()
    argv = ["--config", str(cfg or write_config(root))] + (["-q"] if quiet else []) + [str(a) for a in args]
    return cli.main(argv, env={} if env is None else env, http=w.client() if http else None, now=w.now())


def site_meta(site):
    return json.loads((site / "meta.json").read_text())


@pytest.fixture(scope="module")
def refreshed(tmp_path_factory):
    root = tmp_path_factory.mktemp("refreshed")
    code = run(root, "refresh", "--store", root / "store", "--out", root / "site", "--workers", 1)
    return types.SimpleNamespace(root=root, code=code, site=root / "site", store=root / "store")


# ------------------------------------------------------------------------------------------------ refresh
def test_refresh_writes_the_whole_site_and_keeps_the_history(refreshed):
    assert refreshed.code == 0
    meta = site_meta(refreshed.site)
    assert meta["demo"] is False and meta["ext"] == "json" and meta["enc"] is None
    assert set(meta["markets"]) == {"psx", "fx", "crypto"}
    assert all(m["instruments"] > 0 for m in meta["markets"].values())
    assert all((refreshed.site / name).exists() for name in meta["files"].values())
    for market in ("psx", "fx", "crypto"):
        assert (refreshed.store / f"bars_{market}.csv.gz").exists()
    ideas = json.loads((refreshed.site / meta["files"]["ideas"]).read_text())["ideas"]
    assert ideas and all("actionable" in i for i in ideas)


def test_the_site_says_which_sources_worked(refreshed):
    meta = site_meta(refreshed.site)
    assert {s["id"] for s in meta["sources"]} >= {"psx_portal", "yahoo_fx", "coingecko", "fred"}
    assert all(s["ok"] for s in meta["sources"])


def test_a_second_refresh_reuses_the_stored_history(refreshed):
    first = {m: v["asof"] for m, v in site_meta(refreshed.site)["markets"].items()}
    out = refreshed.root / "site2"
    assert run(refreshed.root, "refresh", "--store", refreshed.store, "--out", out, "--workers", 1, "--markets", "crypto") == 0
    meta = site_meta(out)
    assert set(meta["markets"]) == {"crypto"} and meta["markets"]["crypto"]["asof"] == first["crypto"]


def test_refresh_is_loud_when_nothing_could_be_fetched(tmp_path, capsys):
    w = Worlds().down("psx", "tradingview", "yahoo", "frankfurter", "fawaz", "crypto", "fred", "imf", "worldbank")
    code = run(tmp_path, "refresh", "--store", tmp_path / "store", "--out", tmp_path / "site", w=w)
    err = capsys.readouterr().err
    assert code == 1 and "no market returned any data" in err and "doctor" in err
    assert not (tmp_path / "site" / "meta.json").exists()  # a site is never published from nothing


def test_one_market_down_still_publishes_the_others_and_marks_it_an_error(tmp_path):
    w = Worlds().down("psx", "tradingview")
    out = tmp_path / "site"
    assert run(tmp_path, "refresh", "--store", tmp_path / "store", "--out", out, "--workers", 1, "--markets", "psx,crypto", w=w) == 0
    meta = site_meta(out)
    assert meta["markets"]["psx"]["status"] == "error" and meta["markets"]["psx"]["instruments"] == 0
    assert meta["markets"]["crypto"]["status"] in ("ok", "warming") and meta["markets"]["crypto"]["instruments"] > 0
    overview = json.loads((out / meta["files"]["overview"]).read_text())
    assert any("Pakistan Stock Exchange" in a["text"] for a in overview["alerts"])


# ------------------------------------------------------------------------------------------------ fetch and build
def test_fetch_only_collects_history(tmp_path):
    assert run(tmp_path, "fetch", "--store", tmp_path / "store", "--markets", "crypto") == 0
    assert (tmp_path / "store" / "bars_crypto.csv.gz").exists() and not (tmp_path / "site").exists()


def test_build_works_from_the_store_without_the_network(tmp_path):
    store, out = tmp_path / "store", tmp_path / "site"
    assert run(tmp_path, "fetch", "--store", store, "--markets", "crypto") == 0
    # no http client is passed and the world is not reachable: build must not need either
    assert run(tmp_path, "build", "--store", store, "--out", out, "--workers", 1, "--markets", "crypto", http=False) == 0
    meta = site_meta(out)
    assert meta["markets"]["crypto"]["instruments"] > 0
    assert all(not s["ok"] for s in meta["sources"])  # every source was skipped, and the health lines say so


def test_build_needs_a_store(tmp_path, capsys):
    assert run(tmp_path, "build", "--store", tmp_path / "nothing", "--out", tmp_path / "site") == 1
    assert "run `python -m acumen refresh` first" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ encryption
def test_a_passphrase_encrypts_every_data_file_and_the_app_can_open_them(tmp_path):
    out = tmp_path / "site"
    assert run(tmp_path, "refresh", "--store", tmp_path / "store", "--out", out, "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": GOOD}) == 0
    meta = site_meta(out)  # meta.json stays readable: it carries the salt the app needs to derive the key
    assert meta["ext"] == "enc" and meta["enc"] is not None
    assert [p.name for p in out.glob("*.json")] == ["meta.json"]
    key = crypto_box.key_from_meta(meta["enc"], GOOD)
    assert key is not None
    ideas = crypto_box.decrypt_json(key, (out / meta["files"]["ideas"]).read_bytes())
    assert "ideas" in ideas
    assert crypto_box.key_from_meta(meta["enc"], "the wrong passphrase") is None


def test_a_short_passphrase_is_refused_before_anything_is_written(tmp_path, capsys):
    out = tmp_path / "site"
    assert run(tmp_path, "refresh", "--store", tmp_path / "store", "--out", out, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": "short"}) == 2
    assert "at least 8" in capsys.readouterr().err and not out.exists()


def test_a_modest_passphrase_works_but_is_warned_about(tmp_path, capsys):
    out = tmp_path / "site"
    code = run(tmp_path, "refresh", "--store", tmp_path / "store", "--out", out, "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": "twelve chars"}, quiet=False)
    assert code == 0 and "WARNING: the passphrase in ACUMEN_PASSPHRASE is short" in capsys.readouterr().out


def test_resolve_passphrase_modes():
    cfg = load_config("/nonexistent.yml")
    assert cli.resolve_passphrase(cfg, {}) == (None, None)  # auto, nothing set: plain files
    assert cli.resolve_passphrase(cfg, {"ACUMEN_PASSPHRASE": GOOD}) == (GOOD, None)
    cfg["output"]["encrypt"] = True
    with pytest.raises(cli.CliError, match="ACUMEN_PASSPHRASE is empty"):
        cli.resolve_passphrase(cfg, {})
    assert cli.resolve_passphrase(cfg, {"ACUMEN_PASSPHRASE": GOOD})[0] == GOOD
    cfg["output"]["encrypt"] = False
    pw, warn = cli.resolve_passphrase(cfg, {"ACUMEN_PASSPHRASE": GOOD})
    assert pw is None and "stay readable by anyone" in warn
    cfg["output"]["encrypt"] = "sometimes"
    with pytest.raises(cli.CliError, match="auto, true or false"):
        cli.resolve_passphrase(cfg, {})
    cfg["output"]["encrypt"] = "TRUE"
    cfg["output"]["passphrase_env"] = "MY_SECRET"
    assert cli.resolve_passphrase(cfg, {"MY_SECRET": GOOD})[0] == GOOD


def test_encrypt_true_without_a_passphrase_is_an_error(tmp_path, capsys):
    cfg = write_config(tmp_path, "output: {encrypt: true}\n")
    assert run(tmp_path, "refresh", "--store", tmp_path / "store", "--out", tmp_path / "site", "--markets", "crypto", cfg=cfg) == 2
    assert "output.encrypt is true" in capsys.readouterr().err


def stored(store):
    return sorted(q.relative_to(store).as_posix() for q in store.rglob("*") if q.is_file())


def test_the_history_store_is_sealed_with_the_passphrase(tmp_path):
    store = tmp_path / "store"
    assert run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site", "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": GOOD}) == 0
    names = stored(store)
    assert "bars_crypto.csv.gz.enc" in names and "state/ledger.json.enc" in names
    assert not [n for n in names if not n.endswith(".enc")], "a sealed store holds no plain file"
    blob = (store / "bars_crypto.csv.gz.enc").read_bytes()
    assert b"BTC" not in blob and b"close" not in blob


def test_a_refresh_without_the_passphrase_leaves_a_sealed_store_alone(tmp_path, capsys):
    store = tmp_path / "store"
    assert run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site", "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": GOOD}) == 0
    before = {n: (store / n).read_bytes() for n in stored(store)}
    code = run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site2", "--workers", 1, "--markets", "crypto")
    err = capsys.readouterr().err
    assert code == 2 and "encrypted" in err and "ACUMEN_PASSPHRASE" in err
    assert {n: (store / n).read_bytes() for n in stored(store)} == before and not (tmp_path / "site2").exists()


def test_the_wrong_passphrase_stops_the_run_before_it_can_lose_the_history(tmp_path, capsys):
    store = tmp_path / "store"
    assert run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site", "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": GOOD}) == 0
    before = {n: (store / n).read_bytes() for n in stored(store)}
    code = run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site2", "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": "another long passphrase"})
    assert code == 2 and "not the one the store was written with" in capsys.readouterr().err
    assert {n: (store / n).read_bytes() for n in stored(store)} == before


def test_adding_a_passphrase_seals_the_history_that_was_stored_plain(tmp_path):
    store = tmp_path / "store"
    assert run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site", "--workers", 1, "--markets", "crypto") == 0
    assert "bars_crypto.csv.gz" in stored(store)
    assert run(tmp_path, "refresh", "--store", store, "--out", tmp_path / "site2", "--workers", 1, "--markets", "crypto", env={"ACUMEN_PASSPHRASE": GOOD}) == 0
    names = stored(store)
    assert "bars_crypto.csv.gz.enc" in names and not [n for n in names if not n.endswith(".enc")]


# ------------------------------------------------------------------------------------------------ demo
def test_demo_writes_marked_synthetic_data(tmp_path):
    out = tmp_path / "site"
    assert run(tmp_path, "demo", "--out", out, "--days", 420, "--end", "2026-10-09", "--workers", 1) == 0
    meta = site_meta(out)
    assert meta["demo"] is True and meta["generated_at"].startswith("2026-10-09T12:30")
    overview = json.loads((out / meta["files"]["overview"]).read_text())
    assert any(a["text"].startswith("DEMO DATA") for a in overview["alerts"])


# ------------------------------------------------------------------------------------------------ doctor
def test_doctor_prints_a_verdict_and_exits_zero_when_everything_works(tmp_path, capsys):
    assert run(tmp_path, "doctor", quiet=False) == 0
    text = capsys.readouterr().out
    assert "Acumen doctor," in text and "Verdict" in text and "FAIL" not in text


def test_doctor_json_is_machine_readable_and_strict_fails_on_a_warning(tmp_path, capsys):
    w = Worlds().down("tradingview")
    assert run(tmp_path, "doctor", "--json", w=w) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdicts"]["psx"]["state"] == "degraded"
    assert any(r["key"] == "psx.tradingview" and r["state"] == "warn" for r in data["results"])
    assert run(tmp_path, "doctor", "--strict", "--only", "psx", w=Worlds().down("tradingview")) == 1


def test_doctor_exits_one_when_a_market_is_down(tmp_path):
    assert run(tmp_path, "doctor", "--only", "crypto", w=Worlds().down("crypto")) == 1


# ------------------------------------------------------------------------------------------------ mistakes
@pytest.mark.parametrize("argv,message", [
    (["fetch", "--markets", "forex"], "unknown market 'forex'"),
    (["refresh", "--workers", "0"], "--workers must be at least 1"),
    (["demo", "--end", "yesterday"], "--end must be a date"),
    (["demo", "--days", "100"], "--days must be at least 300"),
    (["demo", "--end", "2999-01-01"], "cannot be in the future"),
    (["doctor", "--only", "forex"], "unknown 'forex'"),
])
def test_mistakes_are_one_line_errors_with_status_two(tmp_path, capsys, argv, message):
    assert run(tmp_path, *argv) == 2
    err = capsys.readouterr().err
    assert message in err and "Traceback" not in err and err.count("\n") == 1


def test_a_missing_config_file_is_an_error(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.yml"), "version"], env={}) == 2
    assert "does not exist" in capsys.readouterr().err


def test_a_broken_config_file_is_an_error(tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("- just\n- a list\n")
    assert cli.main(["--config", str(bad), "version"], env={}) == 2
    assert "could not read the configuration" in capsys.readouterr().err


def test_the_example_settings_file_says_what_the_defaults_say():
    """config.example.yml is meant to be copied and trimmed. As shipped it must match the packaged defaults exactly, so
    copying it unchanged can never quietly move a threshold (and the central-bank `rates` stay commented out until someone
    looks them up and dates them)."""
    import yaml
    from pathlib import Path

    from acumen import config as config_mod

    example = Path(__file__).resolve().parent.parent / "config.example.yml"
    with open(config_mod._DEFAULT_PATH, encoding="utf-8") as fh:
        defaults = yaml.safe_load(fh)
    with open(example, encoding="utf-8") as fh:
        assert "rates" not in yaml.safe_load(fh)
    assert load_config(example) == defaults


def test_no_command_prints_help_and_exits_two(capsys):
    assert cli.main([], env={}) == 2
    assert "COMMAND" in capsys.readouterr().out


def test_version(capsys):
    assert cli.main(["version"], env={}) == 0
    assert capsys.readouterr().out.startswith("Acumen ")
