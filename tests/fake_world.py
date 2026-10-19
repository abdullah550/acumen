"""Every simulated data service at once (PSX portal and TradingView, forex, crypto, macro) behind one ``FakeSession``.

This is what ``fetch_all`` talks to in ``tests/test_fetch_all.py`` and ``tests/test_pipeline.py``. Each service keeps its own
world (its own prices, its own outage switches); this class only lines their clocks up and merges their routes.
"""

from __future__ import annotations

import datetime as dt

from acumen.config import Config, load_config
from acumen.fetch.http import Client
from tests.fake_crypto import CryptoWorld
from tests.fake_fx import FxWorld
from tests.fake_macro import MacroWorld
from tests.fake_psx import World
from tests.fakenet import FakeClock, FakeSession

UTC = dt.timezone.utc
FRI = dt.date(2026, 10, 9)

# Which world a switchable service belongs to.
FX_SERVICES = {"yahoo_fx": ("fx", "yahoo"), "frankfurter": ("fx", "frankfurter"), "fawaz": ("fx", "fawaz")}
MACRO_SERVICES = {"yahoo_macro": ("macro", "yahoo"), "fred": ("macro", "fred"), "imf": ("macro", "imf"), "worldbank": ("macro", "worldbank")}
CRYPTO_SERVICES = ("binance_vision", "okx", "coinbase", "kraken", "kucoin", "coingecko", "fng")


class Worlds:
    def __init__(self, today: dt.date = FRI, hour: int = 12, minute: int = 30, psx_symbols: int = 24, psx_days: int = 330):
        # Friday 17:30 in Pakistan: the PSX session is final; forex and crypto have their last complete day on Thursday.
        self.psx = World(n=psx_symbols, days=psx_days, end=dt.date(2026, 10, 16))
        self.fx = FxWorld()
        self.crypto = CryptoWorld()
        self.macro = MacroWorld()
        self.psx_node_ok = True
        self.psx_tv = True
        self.at(today, hour, minute)

    # ------------------------------------------------------------------ clocks
    def at(self, day: dt.date, hour: int = 12, minute: int = 30) -> "Worlds":
        self.today, self.hour, self.minute = day, hour, minute
        self.psx.cutoff = day
        self.fx.at(day, hour, minute)
        self.crypto.at(day)
        self.macro.at(day, hour, minute)
        return self

    def now(self) -> dt.datetime:
        return dt.datetime(self.today.year, self.today.month, self.today.day, self.hour, self.minute, tzinfo=UTC)

    # ------------------------------------------------------------------ outage switches
    def down(self, *services: str) -> "Worlds":
        """``"yahoo"`` (forex and macro), ``"psx"`` (the portal), ``"tradingview"``, any exchange name, ``"coingecko"``, ``"fred"`` ..."""
        for name in services:
            if name == "yahoo":
                self.fx.down.add("yahoo")
                self.macro.down.add("yahoo")
            elif name == "psx":
                self.psx_node_ok = False
            elif name == "tradingview":
                self.psx_tv = False
            elif name in ("frankfurter", "fawaz"):
                self.fx.down.add(name)
            elif name in ("fred", "imf", "worldbank"):
                self.macro.down.add(name)
            elif name in CRYPTO_SERVICES:
                self.crypto.down.add(name)
            elif name == "crypto":  # every venue, CoinGecko and the sentiment feed
                self.crypto.down.update(CRYPTO_SERVICES)
            else:
                raise KeyError(name)
        return self

    def up(self) -> "Worlds":
        self.psx_node_ok = True
        self.psx_tv = True
        for w in (self.fx, self.crypto, self.macro):
            w.down.clear()
        return self

    # ------------------------------------------------------------------ wiring
    def session(self) -> FakeSession:
        s = FakeSession()
        for world_session in (self.psx.session(node_ok=self.psx_node_ok, tv=self.psx_tv), self.crypto.session(), self.fx.session(), self.macro.session()):
            s.routes.extend(world_session.routes)
        return s

    def client(self, session: FakeSession | None = None) -> Client:
        clock = FakeClock()
        return Client("TestAgent/1.0", session=session if session is not None else self.session(), sleep=clock.sleep, clock=clock, default_interval=0.0)


def config(**data: dict) -> Config:
    """The packaged defaults, shortened to the length of the simulated histories. ``config(fx={...})`` overrides ``data.fx`` keys."""
    cfg = load_config("/nonexistent.yml")
    cfg["data"]["psx"].update({"history_years": 1.2, "ohlc_days": 330})
    cfg["data"]["fx"].update({"history_years": 3})
    cfg["data"]["crypto"].update({"history_days": 400})
    cfg["universe"]["crypto"].update({"top_n": 50, "min_bars": 120})
    cfg["universe"]["fx"].update({"min_bars": 200})
    cfg["universe"]["psx"].update({"min_bars": 120})
    for section, values in data.items():
        cfg["data"][section].update(values)
    return cfg
