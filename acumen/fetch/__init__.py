"""Collect every market in one call: ``fetch_all(cfg, store)`` -> ``{"psx": MarketData, "fx": MarketData, "crypto": MarketData}``.

Order
-----
Forex runs first (a few hundred small requests), together with the macro series that ride along with it (dollar index,
yields, oil, gold, equities, Pakistan's policy rate and inflation). Crypto comes next and the Pakistan Stock Exchange last:
its portal is paced politely and takes the longest, so a run that is cut short loses the least. Whatever order they ran in,
the result lists the markets in the canonical order ``psx, fx, crypto``.

Failure isolation
-----------------
One market failing must never cost the others. Every provider already survives a source being down (it falls back to the
next source, then to the history stored by earlier runs). This module adds a last safety net for the unexpected: if a
fetcher *raises* (a bug, a full disk, a corrupt stored file), it is run once more against a client that cannot reach the
network, which makes it work from stored history alone, and the failure is reported as a health line that the app shows.
Only when that fails as well is the market returned empty, and the build then reports it as an error.
"""

from __future__ import annotations

import datetime as dt
import time
import traceback
from typing import Any, Callable, Iterable

import requests

from ..config import Config
from ..model import MARKETS, MarketData
from ..store import HistoryStore, StoreLocked
from .common import status, utc_now
from .crypto import fetch_crypto
from .fx import fetch_fx
from .http import Client
from .macro import fetch_macro
from .psx import fetch_psx

# Run order: quick markets first, the slow and politely paced portal last.
RUN_ORDER = ("fx", "crypto", "psx")
FETCHERS: dict[str, Callable[..., MarketData]] = {"psx": fetch_psx, "fx": fetch_fx, "crypto": fetch_crypto}
LABELS = {"psx": "Pakistan Stock Exchange", "fx": "Forex", "crypto": "Crypto"}
Log = Callable[[str], None]


class _NoNetwork:
    """A stand-in for ``requests.Session`` that refuses every request, so a fetcher can only use what is stored."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise requests.ConnectionError("offline: this retry works from stored data only")


def offline_client(cfg: Config) -> Client:
    """A client whose every request fails at once and whose first failure switches the host off, without any waiting."""
    return Client(cfg.user_agent, session=_NoNetwork(), sleep=lambda seconds: None, max_retries=0, breaker_threshold=1)


def parse_markets(markets: Iterable[str] | str | None) -> list[str]:
    """``None`` -> all markets; ``"psx,fx"`` or ``["psx", "fx"]`` -> the listed ones, in canonical order. Unknown names raise ``ValueError``."""
    if markets is None:
        return list(MARKETS)
    if isinstance(markets, str):
        markets = markets.replace(";", ",").split(",")
    wanted = [str(m).strip().lower() for m in markets if str(m).strip()]
    unknown = [m for m in wanted if m not in MARKETS]
    if unknown:
        raise ValueError(f"unknown market {', '.join(repr(m) for m in unknown)}; choose from {', '.join(MARKETS)}")
    if not wanted:
        raise ValueError("no market chosen; choose from " + ", ".join(MARKETS))
    return [m for m in MARKETS if m in wanted]


def _problem(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:160]}" if text else type(exc).__name__


def _trace(exc: BaseException, log: Log) -> None:
    """The traceback goes to the run log (a workflow log is where a failure gets debugged); the app only sees the one-line summary."""
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    log("".join(lines).rstrip())


def _guarded(market: str, fetcher: Callable[..., MarketData], cfg: Config, store: HistoryStore, http: Client, log: Log, now: dt.datetime) -> MarketData:
    """Run one fetcher; on an unexpected exception run it again without network access, then give up with an empty market."""
    try:
        return fetcher(cfg, store, http, log=log, now=now)
    except StoreLocked:
        raise  # a wrong or missing passphrase is not one market's problem: every market would lose its stored history
    except Exception as exc:  # noqa: BLE001 - nothing a fetcher does may stop the other markets
        first = _problem(exc)
        log(f"  {market}: stopped unexpectedly ({first}); trying again from stored history only")
        _trace(exc, log)
    try:
        md = fetcher(cfg, store, offline_client(cfg), log=log, now=now)
        reason = f"stopped unexpectedly ({first}); showing the history stored by earlier runs"
    except StoreLocked:
        raise
    except Exception as exc:  # noqa: BLE001
        log(f"  {market}: no stored history could be used either ({_problem(exc)})")
        _trace(exc, log)
        md = MarketData(market=market)
        reason = f"stopped unexpectedly ({first}) and no stored history could be used"
    md.status.insert(0, status(f"{market}_run", False, reason, now=now))
    return md


def _attach_macro(md: MarketData, cfg: Config, store: HistoryStore, http: Client, log: Log, now: dt.datetime) -> None:
    """Macro series are fetched with forex and travel with it (``md.extras["macro"]``); their health lines join the forex ones."""
    extra: list = []
    try:
        macro, statuses, notes = fetch_macro(cfg, store, http, log=log, now=now)
    except StoreLocked:
        raise
    except Exception as exc:  # noqa: BLE001 - macro is context, never a reason to lose the forex data
        first = _problem(exc)
        log(f"  macro: stopped unexpectedly ({first}); trying again from stored copies only")
        _trace(exc, log)
        try:
            macro, statuses, notes = fetch_macro(cfg, store, offline_client(cfg), log=log, now=now)
            extra = [status("macro_run", False, f"stopped unexpectedly ({first}); showing copies saved by earlier runs", now=now)]
        except StoreLocked:
            raise
        except Exception as exc2:  # noqa: BLE001
            log(f"  macro: no saved copies could be used either ({_problem(exc2)})")
            _trace(exc2, log)
            md.status.append(status("macro_run", False, f"stopped unexpectedly ({first}) and no saved copies could be used", now=now))
            return
    md.extras["macro"] = macro
    md.status.extend([*extra, *statuses])
    if notes:
        md.extras["notes"] = [*md.extras.get("notes", []), *notes]


def summarise(md: MarketData) -> str:
    bad = [s.source for s in md.status if not s.ok]
    n_bars = sum(len(b) for b in md.bars.values())
    return f"{len(md.instruments)} instruments, {n_bars:,} bars, {len(md.status) - len(bad)} of {len(md.status)} sources ok" + (f" (failed: {', '.join(bad)})" if bad else "")


def fetch_all(
    cfg: Config,
    store: HistoryStore,
    markets: Iterable[str] | str | None = None,
    *,
    log: Log = print,
    now: dt.datetime | None = None,
    http: Client | None = None,
) -> dict[str, MarketData]:
    """Fetch the chosen markets (all by default) and return them in the canonical order. Never raises because of one market."""
    wanted = parse_markets(markets)
    now = (now or utc_now()).astimezone(dt.timezone.utc)
    http = http or Client(cfg.user_agent, timeout=float(cfg.get_path("data.request_timeout_s", 30)))
    got: dict[str, MarketData] = {}
    for market in RUN_ORDER:
        if market not in wanted:
            continue
        started = time.time()
        log(f"Fetching {LABELS[market]}")
        md = _guarded(market, FETCHERS[market], cfg, store, http, log, now)
        if market == "fx":
            _attach_macro(md, cfg, store, http, log, now)
        got[market] = md
        log(f"  {market}: {summarise(md)} in {time.time() - started:.0f}s")
    return {m: got[m] for m in MARKETS if m in got}
