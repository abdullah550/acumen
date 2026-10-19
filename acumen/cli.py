"""Command line: ``python -m acumen <command>``.

``refresh``  fetch every market, analyse it and write the site's data files (what the scheduled workflow runs)
``fetch``    only collect data into the history store
``build``    analyse the history already in the store, without the network (for example after editing ``config.yml``)
``demo``     write clearly labelled synthetic data, to look at the app without touching any source
``doctor``   check that every data source still answers and still looks the way the parsers expect
``version``  print the version

Exit status: 0 when it worked, 1 when the run could not do its job (no market returned data; a market is down in ``doctor``),
2 for a mistake in the command, the configuration or the passphrase.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import __version__, crypto_box
from .config import Config, load_config
from .fetch import fetch_all, offline_client, parse_markets, summarise
from .fetch import doctor as doctor_mod
from .fetch.common import utc_now
from .fetch.http import Client
from .model import MARKETS, MarketData
from .store import HistoryStore, StoreLocked

DEFAULT_STORE = "store"
DEFAULT_OUT = "site/data"
MIN_PASSPHRASE = 8
COMFORTABLE_PASSPHRASE = 16
TRUE_WORDS = {"true", "yes", "on", "1"}
FALSE_WORDS = {"false", "no", "off", "0", "none"}


class CliError(Exception):
    """A mistake the person can fix: printed as one line, exit status 2."""


class RunError(Exception):
    """The run could not do its job: printed as one line, exit status 1."""


# ---------------------------------------------------------------------------------------------- passphrase
def resolve_passphrase(cfg: Config, env: Mapping[str, str]) -> tuple[str | None, str | None]:
    """``(passphrase or None, warning or None)`` from ``output.encrypt`` (auto / true / false) and the environment variable.

    ``auto`` encrypts exactly when the variable is set. ``true`` insists on it. ``false`` never encrypts. The encrypted files are
    public on a public site, so anyone can try passphrases against them offline: a short one is refused and a modest one is warned about.
    """
    mode = cfg.get_path("output.encrypt", "auto")
    word = str(mode).strip().lower() if not isinstance(mode, bool) else ("true" if mode else "false")
    name = str(cfg.get_path("output.passphrase_env", "ACUMEN_PASSPHRASE"))
    value = env.get(name, "")
    if word in FALSE_WORDS:
        return None, (f"{name} is set but output.encrypt is false: the data files stay readable by anyone" if value else None)
    if word not in TRUE_WORDS and word != "auto":
        raise CliError(f"output.encrypt must be auto, true or false (found {mode!r})")
    if not value:
        if word in TRUE_WORDS:
            raise CliError(f"output.encrypt is true but the environment variable {name} is empty; set it to your passphrase")
        return None, None
    if len(value) < MIN_PASSPHRASE:
        raise CliError(f"the passphrase in {name} has {len(value)} characters: use at least {MIN_PASSPHRASE}, ideally a long random phrase")
    if len(value) < COMFORTABLE_PASSPHRASE:
        return value, f"the passphrase in {name} is short ({len(value)} characters). The encrypted files are public, so it can be guessed offline: 16 or more characters is safer"
    return value, None


# ---------------------------------------------------------------------------------------------- helpers
def _log(quiet: bool) -> Callable[[str], None]:
    if quiet:
        return lambda message: None
    return lambda message: print(message, flush=True)


def _workers(value: int | None) -> int:
    if value is not None:
        if value < 1:
            raise CliError("--workers must be at least 1")
        return value
    return max(1, min(4, os.cpu_count() or 1))


def _markets(value: str | None) -> list[str]:
    try:
        return parse_markets(value)
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _last_weekday(day: dt.date) -> dt.date:
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def _date(text: str, flag: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise CliError(f"{flag} must be a date like 2026-10-09 (found {text!r})") from exc


def _open_store(path: str | Path, cfg: Config, env: Mapping[str, str], log: Callable[[str], None]) -> tuple[HistoryStore, str | None]:
    """The history store and the passphrase (``None`` for plain files). The store is sealed with a key of its own, derived from the passphrase."""
    passphrase, warning = resolve_passphrase(cfg, env)
    if warning:
        log(f"WARNING: {warning}")
    return HistoryStore(path, key=crypto_box.store_key(passphrase) if passphrase else None), passphrase


def _write_site(cfg: Config, data: dict[str, MarketData], store: HistoryStore | None, args: argparse.Namespace, passphrase: str | None,
                log: Callable[[str], None], now: dt.datetime, *, demo: bool) -> dict[str, Any]:
    from . import build  # imported late: it pulls in the whole analysis engine, which the quick commands do not need

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = build.run(cfg, data, out, store=store, passphrase=passphrase, demo=demo, now=now, workers=_workers(args.workers), log=log)
    state = "encrypted" if passphrase else "not encrypted"
    log(f"Done: {summary['ideas']} ideas ({summary['actionable']} actionable), {summary['files']:,} files, {summary['bytes'] / 1e6:.1f} MB ({state}) in {summary['seconds']:.0f}s -> {out}")
    if summary.get("failed"):
        log(f"  {summary['failed']} instrument(s) could not be analysed; meta.json lists examples")
    return summary


def _require_data(data: dict[str, MarketData]) -> None:
    usable = [m for m, md in data.items() if md.instruments and any(len(b) for b in md.bars.values())]
    if not usable:
        raise RunError("no market returned any data, so nothing was written. Run `python -m acumen doctor` to see which sources are failing")


# ---------------------------------------------------------------------------------------------- commands
def cmd_fetch(args: argparse.Namespace, cfg: Config, env: Mapping[str, str], http: Client | None, now: dt.datetime | None, log: Callable[[str], None]) -> int:
    store, _ = _open_store(args.store, cfg, env, log)
    data = fetch_all(cfg, store, _markets(args.markets), log=log, now=now, http=http)
    for market, md in data.items():
        log(f"{market}: {summarise(md)}")
    _require_data(data)
    return 0


def cmd_refresh(args: argparse.Namespace, cfg: Config, env: Mapping[str, str], http: Client | None, now: dt.datetime | None, log: Callable[[str], None]) -> int:
    store, passphrase = _open_store(args.store, cfg, env, log)
    stamp = now or utc_now()
    data = fetch_all(cfg, store, _markets(args.markets), log=log, now=stamp, http=http)
    _require_data(data)
    _write_site(cfg, data, store, args, passphrase, log, stamp, demo=False)
    return 0


def cmd_build(args: argparse.Namespace, cfg: Config, env: Mapping[str, str], http: Client | None, now: dt.datetime | None, log: Callable[[str], None]) -> int:
    root = Path(args.store)
    if not root.exists() or not any(root.iterdir()):
        raise RunError(f"the history store {root} is empty or missing: run `python -m acumen refresh` first")
    store, passphrase = _open_store(root, cfg, env, log)
    stamp = now or utc_now()
    log("Analysing the stored history (no network)")
    data = fetch_all(cfg, store, _markets(args.markets), log=log, now=stamp, http=offline_client(cfg))
    _require_data(data)
    _write_site(cfg, data, store, args, passphrase, log, stamp, demo=False)
    return 0


def cmd_demo(args: argparse.Namespace, cfg: Config, env: Mapping[str, str], http: Client | None, now: dt.datetime | None, log: Callable[[str], None]) -> int:
    from .demo import make_demo

    stamp = now or utc_now()
    end = _date(args.end, "--end") if args.end else _last_weekday(stamp.date())
    if end > stamp.date():
        raise CliError("--end cannot be in the future")
    if args.days < 300:
        raise CliError("--days must be at least 300: the analysis needs a year or more of history to say anything")
    log(f"Generating synthetic markets: seed {args.seed}, {args.days} days ending {end}" + (" (pure noise)" if args.noise else ""))
    data = make_demo(seed=args.seed, days=args.days, end=end.isoformat(), structure=not args.noise)
    if end < stamp.date():
        stamp = dt.datetime(end.year, end.month, end.day, 12, 0, tzinfo=dt.timezone.utc)
    passphrase, warning = resolve_passphrase(cfg, env)
    if warning:
        log(f"WARNING: {warning}")
    _write_site(cfg, data, None, args, passphrase, log, stamp, demo=True)
    log("Every price, company and result in this data is synthetic: the app shows a DEMO banner.")
    return 0


def cmd_doctor(args: argparse.Namespace, cfg: Config, env: Mapping[str, str], http: Client | None, now: dt.datetime | None, log: Callable[[str], None]) -> int:
    stamp = (now or utc_now()).astimezone(dt.timezone.utc)
    try:
        doctor_mod.select(doctor_mod.checks(), args.only)  # fail on a typo before any request is made
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    if not args.json:
        print(f"Acumen doctor, {stamp.strftime('%Y-%m-%d %H:%M UTC')}\nuser agent: {cfg.user_agent}", flush=True)
    shown: list[str] = []

    def stream(result: doctor_mod.Result) -> None:
        if args.json:
            return
        if result.group not in shown:
            shown.append(result.group)
            print(f"\n{doctor_mod.GROUP_LABEL[result.group]}", flush=True)
        print(doctor_mod.format_result(result), flush=True)

    results = doctor_mod.run_checks(cfg, http, args.only, now=stamp, log=stream)
    verdicts = doctor_mod.verdicts(results)
    if args.json:
        print(json.dumps({"generated_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"), "results": [r.as_dict() for r in results],
                          "verdicts": {g: {"state": s, "why": w} for g, (s, w) in verdicts.items()}}, indent=2))
    else:
        print("\nVerdict", flush=True)
        for group, (state, why) in verdicts.items():
            print(f"  {doctor_mod.GROUP_LABEL[group]:<26} {state.upper():<9} {why}".rstrip(), flush=True)
        whole = doctor_mod.complete_groups(results)
        partial = [doctor_mod.GROUP_LABEL[g] for g in doctor_mod.GROUPS if any(r.group == g for r in results) and g not in whole]
        if partial:
            print(f"  (only some checks were run for: {', '.join(partial)}; no verdict for those)", flush=True)
    return doctor_mod.exit_code(results, strict=args.strict)


def cmd_version(args: argparse.Namespace, cfg: Config, env: Mapping[str, str], http: Client | None, now: dt.datetime | None, log: Callable[[str], None]) -> int:
    print(f"Acumen {__version__}")
    return 0


# ---------------------------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m acumen", description="Acumen: a self-updating market analysis engine for PSX shares, forex and crypto.",
                                epilog="Run `python -m acumen doctor` first on a new machine: it shows which data sources answer from where you are.")
    p.add_argument("--config", metavar="FILE", help="settings that override the packaged defaults (default: config.yml in the current folder, if it exists)")
    p.add_argument("-q", "--quiet", action="store_true", help="print only warnings and errors")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def command(name: str, fn: Callable[..., int], help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    def store_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--store", default=DEFAULT_STORE, metavar="DIR", help=f"history store: price history and small state files kept between runs (default: {DEFAULT_STORE})")
        sp.add_argument("--markets", metavar="LIST", help=f"comma-separated subset of {', '.join(MARKETS)} (default: all). The app shows only the markets that were built")

    def out_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--out", default=DEFAULT_OUT, metavar="DIR", help=f"where the app's data files are written (default: {DEFAULT_OUT})")
        sp.add_argument("--workers", type=int, metavar="N", help="processes used by the analysis (default: the number of cores, at most 4)")

    sp = command("refresh", cmd_refresh, "Fetch every market, analyse it and write the app's data files.")
    store_opts(sp)
    out_opts(sp)
    store_opts(command("fetch", cmd_fetch, "Collect data into the history store and stop."))
    sp = command("build", cmd_build, "Analyse the history already in the store, without any network access, and write the app's data files.")
    store_opts(sp)
    out_opts(sp)
    sp = command("demo", cmd_demo, "Write synthetic demo data (marked DEMO everywhere) so the app can be looked at without any data source.")
    out_opts(sp)
    sp.add_argument("--seed", type=int, default=7, help="random seed of the synthetic markets (default: 7)")
    sp.add_argument("--days", type=int, default=1100, help="length of the synthetic history in calendar days (default: 1100)")
    sp.add_argument("--end", metavar="YYYY-MM-DD", help="last day of the history (default: the latest weekday)")
    sp.add_argument("--noise", action="store_true", help="markets with nothing to predict: a check that the app then calls nothing actionable")
    sp = command("doctor", cmd_doctor, "Ask every data source one small question and report whether the answer still makes sense.")
    sp.add_argument("--only", metavar="LIST", help="comma-separated groups (psx, fx, crypto, macro) or single checks (crypto.okx)")
    sp.add_argument("--strict", action="store_true", help="exit with status 1 when anything at all is not ok, not only when a market is down")
    sp.add_argument("--json", action="store_true", help="print the report as JSON")
    command("version", cmd_version, "Print the version.")
    return p


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, http: Client | None = None, now: dt.datetime | None = None) -> int:
    """Run one command and return the exit status. ``env``, ``http`` and ``now`` exist so tests can run without the machine's surroundings."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 2
    env = os.environ if env is None else env
    log = _log(args.quiet)
    try:
        explicit = args.config
        if explicit and not Path(explicit).exists():
            raise CliError(f"the configuration file {explicit} does not exist")
        try:
            cfg = load_config(explicit)
        except (ValueError, OSError) as exc:
            raise CliError(f"could not read the configuration: {exc}") from exc
        return int(args.fn(args, cfg, env, http, now, log))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except StoreLocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
