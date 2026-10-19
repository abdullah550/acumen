"""Simulated macro sources (Yahoo's index and futures symbols, FRED CSV, the IMF and the World Bank) for ``tests/test_fetch_macro.py``."""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote

import numpy as np
import pandas as pd

from tests.fakenet import FakeResponse, FakeSession, ok

LEVELS = {"DX-Y.NYB": (101.0, 0.004), "^TNX": (4.2, 0.012), "BZ=F": (78.0, 0.014), "^GSPC": (5200.0, 0.010), "^VIX": (17.0, 0.05), "EEM": (42.0, 0.011)}
RANGE_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": 20000}
EDT = -4 * 3600


class MacroWorld:
    def __init__(self, days: int = 900, last: dt.date = dt.date(2026, 10, 30), seed: int = 21):
        self.today = dt.date(2026, 10, 9)
        self.hour = 0
        self.minute = 30
        self.down: set[str] = set()  # "yahoo", "fred", "imf", "worldbank"
        self.unknown: set[str] = set()  # Yahoo symbols Yahoo has never heard of
        self.tnx_x10 = False  # Yahoo quotes the 10-year yield index at ten times the yield
        self.fred_missing: set[str] = set()  # FRED ids that answer with an error page
        self.policy_rate = (dt.date(2026, 7, 1), 11.0)  # (observation month, percent) for the IMF-reported series on FRED; None for no series
        self.imf = {"2024": 23.4, "2025": 4.5, "2026": 6.1, "2027": 7.0}
        self.wb = [("2026", None), ("2025", 4.5), ("2024", 23.4)]
        self.log: list[str] = []
        rng = np.random.default_rng(seed)
        self.dates = [d for d in (last - dt.timedelta(days=k) for k in range(days, -1, -1)) if d.weekday() < 5]
        n = len(self.dates)
        ref = self.dates.index(dt.date(2026, 10, 9))
        self.px: dict[str, pd.Series] = {}
        for sym, (lvl, vol) in LEVELS.items():
            path = np.exp(np.cumsum(rng.normal(0, vol, n)))
            self.px[sym] = pd.Series(path / path[ref] * lvl, index=pd.DatetimeIndex(self.dates))
        self.fred_px = {"DGS10": self.px["^TNX"] * 1.002, "DGS2": self.px["^TNX"] - 0.4, "T10Y2Y": pd.Series(0.4, index=pd.DatetimeIndex(self.dates))}

    def now(self) -> dt.datetime:
        return dt.datetime(self.today.year, self.today.month, self.today.day, self.hour, self.minute, tzinfo=dt.timezone.utc)

    def at(self, day: dt.date, hour: int = 0, minute: int = 30) -> "MacroWorld":
        self.today, self.hour, self.minute = day, hour, minute
        return self

    def final(self, sym: str) -> pd.Series:
        """What the app should end up with: closes up to the day before today."""
        s = self.px[sym]
        return s[s.index < pd.Timestamp(self.today)]

    def session(self, s: FakeSession | None = None) -> FakeSession:
        s = s or FakeSession()
        world = self

        def chart(call):
            sym = unquote(call["url"].rsplit("/", 1)[1])
            world.log.append(f"yahoo {sym}")
            if "yahoo" in world.down:
                return FakeResponse(429, b"", headers={"Retry-After": "900"})
            if sym in world.unknown or sym not in world.px:
                return ok({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
            first = pd.Timestamp(world.today - dt.timedelta(days=RANGE_DAYS[call["params"]["range"]]))
            px = world.px[sym]
            px = px[(px.index >= first) & (px.index <= pd.Timestamp(world.today))] * (10.0 if (sym == "^TNX" and world.tnx_x10) else 1.0)
            ts = [int(dt.datetime(d.year, d.month, d.day, 13, 30, tzinfo=dt.timezone.utc).timestamp()) for d in px.index]
            rows = px.tolist()
            return ok({"chart": {"result": [{"meta": {"gmtoffset": EDT, "symbol": sym, "regularMarketPrice": rows[-1], "regularMarketTime": int(world.now().timestamp())},
                                             "timestamp": ts, "indicators": {"quote": [{"open": rows, "high": [v * 1.004 for v in rows], "low": [v * 0.996 for v in rows], "close": rows,
                                                                                         "volume": [1000] * len(rows)}]}}], "error": None}})

        s.get(r"query[12]\.finance\.yahoo\.com/v8/finance/chart/(%5E|DX-Y|EEM|BZ)", chart)

        def fred(call):
            sid = call["params"]["id"]
            world.log.append(f"fred {sid}")
            if "fred" in world.down:
                return FakeResponse(503, b"down")
            if sid in world.fred_missing or (sid == "INTDSRPKM193N" and world.policy_rate is None):
                return FakeResponse(200, b"<html><body>Bad request. The series does not exist.</body></html>")
            cosd = pd.Timestamp(call["params"]["cosd"])
            lines = [f"observation_date,{sid}"]
            if sid == "FEDFUNDS":
                for m in pd.date_range(cosd.replace(day=1), pd.Timestamp(world.today), freq="MS"):
                    lines.append(f"{m.date()},4.33")
            elif sid == "INTDSRPKM193N":
                d0, v0 = world.policy_rate
                for m in pd.date_range(pd.Timestamp(d0) - pd.DateOffset(months=5), pd.Timestamp(d0), freq="MS"):
                    lines.append(f"{m.date()},{v0 if m >= pd.Timestamp(d0) else 12.0}")
            else:
                ser = world.fred_px[sid]
                for d, v in ser[(ser.index >= cosd) & (ser.index < pd.Timestamp(world.today))].items():
                    lines.append(f"{d.date()},{'.' if d.day == 25 else f'{v:.3f}'}")  # a holiday is a dot
            return FakeResponse(200, ("\n".join(lines) + "\n").encode())

        s.get(r"fred\.stlouisfed\.org/graph/fredgraph\.csv", fred)

        def imf(call):
            world.log.append("imf")
            if "imf" in world.down:
                return FakeResponse(503, b"down")
            return ok({"values": {"PCPIPCH": {"PAK": world.imf}}, "api": {"version": "1"}})

        s.get(r"imf\.org/external/datamapper/api/v1/PCPIPCH/PAK", imf)

        def wb(call):
            world.log.append("worldbank")
            if "worldbank" in world.down:
                return FakeResponse(503, b"down")
            return ok([{"page": 1, "pages": 1, "per_page": 8, "total": len(world.wb)}, [{"indicator": {"id": "FP.CPI.TOTL.ZG"}, "country": {"id": "PK"}, "date": y, "value": v} for y, v in world.wb]])

        s.get(r"api\.worldbank\.org/v2/country/PAK/indicator/FP\.CPI\.TOTL\.ZG", wb)
        return s
