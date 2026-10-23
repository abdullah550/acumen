"""A simulated forex landscape (Yahoo, the ECB's fixings, a daily-snapshot feed) served through ``FakeSession``.

One set of "true" daily rates per currency against the dollar generates every pair, so tests can check that the fetcher
picks the right bars, drops the day still in progress, and recovers when a source is down.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote

import numpy as np
import pandas as pd

from tests.fakenet import FakeResponse, FakeSession, ok

# units of each currency per US dollar on the last simulated day; gold and silver in troy ounces per dollar
LEVELS = {"EUR": 0.92, "GBP": 0.78, "AUD": 1.52, "NZD": 1.68, "CAD": 1.37, "CHF": 0.88, "JPY": 148.0, "TRY": 34.0, "ZAR": 18.0, "MXN": 19.5, "SGD": 1.33,
          "NOK": 10.7, "SEK": 10.5, "PLN": 4.0, "HUF": 360.0, "CZK": 23.0, "CNH": 7.2, "INR": 83.0, "PKR": 280.0, "XAU": 1 / 2400.0, "XAG": 1 / 30.0}
VOL = {"PKR": 0.0006, "XAU": 0.010, "XAG": 0.018, "TRY": 0.006, "ZAR": 0.008, "MXN": 0.007, "HUF": 0.007, "JPY": 0.006}
PEGS = {"AED": 3.6725, "SAR": 3.75}
RANGE_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653, "max": 20000}
BST = 3600


def weekdays(first: dt.date, last: dt.date) -> list[dt.date]:
    out, d = [], first
    while d <= last:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class FxWorld:
    def __init__(self, days: int = 1900, last: dt.date = dt.date(2026, 10, 30), seed: int = 5):
        self.today = dt.date(2026, 10, 9)  # a Friday
        self.hour = 0
        self.minute = 30
        self.dates = weekdays(last - dt.timedelta(days=days), last)
        self.down: set[str] = set()  # "yahoo", "frankfurter", "fawaz"
        self.unknown: set[str] = set()  # Yahoo symbols Yahoo has never heard of
        self.scale: dict[str, float] = {}  # Yahoo symbol -> multiplier: a source whose numbers are off
        self.ecb_scale: dict[str, float] = {}  # currency -> multiplier applied to the ECB fixings
        self.cap_days: dict[str, int] = {}  # Yahoo symbol -> the most days of history it will ever return, whatever the range
        self.bad_ticks: dict[tuple[str, dt.date], float] = {}  # (pair, day) -> factor applied to that day's high
        self.stray: dict[str, tuple[float, set[dt.date]]] = {}  # Yahoo symbol -> (factor, days): on those days the open, low and close come from a second quote level
        self.live_lag_hours = 0.0
        self.log: list[str] = []
        rng = np.random.default_rng(seed)
        n = len(self.dates)
        self.leg: dict[str, pd.Series] = {}
        ref = self.dates.index(dt.date(2026, 10, 9))
        for c, lvl in LEVELS.items():
            path = np.exp(np.cumsum(rng.normal(0, VOL.get(c, 0.0045), n)))
            path = path / path[ref] * lvl
            self.leg[c] = pd.Series(path, index=pd.DatetimeIndex(self.dates))
        self.pos = {pd.Timestamp(d): i for i, d in enumerate(self.dates)}
        self.noise = {c: pd.Series(1 + rng.normal(0, 0.0008, n), index=pd.DatetimeIndex(self.dates)) for c in LEVELS}  # a fixing is not a close
        self.wick = rng.uniform(0.001, 0.006, (n, 2))

    # ------------------------------------------------------------------ truth
    def now(self) -> dt.datetime:
        return dt.datetime(self.today.year, self.today.month, self.today.day, self.hour, self.minute, tzinfo=dt.timezone.utc)

    def at(self, day: dt.date, hour: int = 0, minute: int = 30) -> "FxWorld":
        self.today, self.hour, self.minute = day, hour, minute
        return self

    def units(self, c: str) -> pd.Series:
        if c == "USD":
            return pd.Series(1.0, index=pd.DatetimeIndex(self.dates))
        if c in PEGS:
            return pd.Series(PEGS[c], index=pd.DatetimeIndex(self.dates))
        return self.leg[c]

    def close(self, symbol: str) -> pd.Series:
        """True closes of BASE/QUOTE for every simulated weekday up to and including today's (still forming) one."""
        base, quote = symbol[:3], symbol[3:]
        s = self.units(quote) / self.units(base)
        return s[s.index <= pd.Timestamp(self.today)]

    def final(self, symbol: str, last: pd.Timestamp | None = None) -> pd.Series:
        s = self.close(symbol)
        if last is None:
            from acumen.fetch.fx import final_day
            last = final_day(self.now())
        return s[s.index <= last]

    def bars(self, symbol: str, factor: float = 1.0) -> pd.DataFrame:
        c = self.close(symbol) * factor
        o = c.shift(1).fillna(c)
        idx = [self.pos[d] for d in c.index]
        w = self.wick[idx]
        hi = pd.concat([o, c], axis=1).max(axis=1) * (1 + w[:, 0])
        lo = pd.concat([o, c], axis=1).min(axis=1) * (1 - w[:, 1])
        for (sym, day), f in self.bad_ticks.items():
            if sym == symbol and pd.Timestamp(day) in hi.index:
                hi[pd.Timestamp(day)] *= f
        return pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c})

    # ------------------------------------------------------------------ wiring
    def session(self) -> FakeSession:
        s = FakeSession()
        world = self

        def yahoo_symbol(sym: str):
            """Pair (or proxy) a Yahoo symbol stands for, with the multiplier that makes the world's numbers differ from truth."""
            if sym in world.unknown:
                return None
            m = re.fullmatch(r"([A-Z]{3})([A-Z]{3})=X", sym)
            if m:
                return m.group(1) + m.group(2), 1.0
            m = re.fullmatch(r"([A-Z]{3})=X", sym)
            if m:
                return "USD" + m.group(1), 1.0
            if sym == "GC=F":
                return "XAUUSD", 1.004
            if sym == "SI=F":
                return "XAGUSD", 1.004
            return None

        def chart(call):
            sym = unquote(call["url"].rsplit("/", 1)[1])
            world.log.append(f"yahoo {sym} {call['params'].get('range')}")
            if "yahoo" in world.down:
                return FakeResponse(429, b"", headers={"Retry-After": "900"})
            found = yahoo_symbol(sym)
            if found is None:
                return ok({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
            pair, mult = found
            base, quote = pair[:3], pair[3:]
            if base not in world.leg and base not in ("USD", "EUR") and base not in PEGS:
                return ok({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
            try:
                b = world.bars(pair, mult * world.scale.get(sym, 1.0))
            except KeyError:
                return ok({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}})
            first = pd.Timestamp(world.today - dt.timedelta(days=min(RANGE_DAYS[call["params"]["range"]], world.cap_days.get(sym, 10**6))))
            b = b[b.index >= first]
            live_price = float(b["close"].iloc[-1])  # the live tick stays clean: it is the daily bars that carry the second level
            if sym in world.stray:
                factor, days = world.stray[sym]
                hit = b.index.isin([pd.Timestamp(d) for d in days])
                b = b.copy()
                for col in ("open", "low", "close"):
                    b.loc[hit, col] = b.loc[hit, col] * factor
            ts = [int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) - BST for d in b.index]  # midnight in London
            last_day = b.index[-1].date()  # a weekend's "live" price is Friday's last
            live = world.now() if last_day == world.today else dt.datetime(last_day.year, last_day.month, last_day.day, 21, 59, tzinfo=dt.timezone.utc)
            last_t = int(live.timestamp()) - int(world.live_lag_hours * 3600)
            return ok({"chart": {"result": [{"meta": {"gmtoffset": BST, "symbol": sym, "regularMarketPrice": live_price, "regularMarketTime": last_t,
                                                      "exchangeTimezoneName": "Europe/London"},
                                             "timestamp": ts, "indicators": {"quote": [{"open": b["open"].tolist(), "high": b["high"].tolist(), "low": b["low"].tolist(),
                                                                                         "close": b["close"].tolist(), "volume": [0] * len(b)}]}}], "error": None}})

        s.get(r"query[12]\.finance\.yahoo\.com/v8/finance/chart/(?!%5E|\^|DX|EEM|BZ)", chart)

        # ---- ECB fixings
        def frankfurter(call):
            m = re.search(r"/v1/(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})", call["url"])
            world.log.append(f"frankfurter {m.group(1)}..{m.group(2)}")
            if "frankfurter" in world.down:
                return FakeResponse(503, b"down")
            a, b = pd.Timestamp(m.group(1)), pd.Timestamp(m.group(2))
            syms = call["params"]["symbols"].split(",")
            days = [d for d in world.dates if a <= pd.Timestamp(d) <= min(b, pd.Timestamp(world.today)) and not (d.month == 12 and d.day == 25) and not (d.month == 1 and d.day == 1)]
            if (b - a).days > 92:
                days = days[::5]  # a long range is summarised weekly, as the real service does
            rates = {str(d): {c: float(world.leg[c][pd.Timestamp(d)] * world.noise[c][pd.Timestamp(d)] * world.ecb_scale.get(c, 1.0)) for c in syms} for d in days}
            return ok({"amount": 1.0, "base": "USD", "start_date": str(a.date()), "end_date": str(b.date()), "rates": rates})

        s.get(r"api\.frankfurter\.dev/v1/\d{4}-\d{2}-\d{2}\.\.", frankfurter)

        # ---- daily snapshots
        def snapshot(call):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", call["url"])
            d = dt.date.fromisoformat(m.group(1))
            world.log.append(f"fawaz {d}")
            if "fawaz" in world.down or d > world.today or pd.Timestamp(d) not in world.leg["EUR"].index:
                return FakeResponse(404, b"not found")
            usd = {c.lower(): float(world.leg[c][pd.Timestamp(d)]) for c in LEVELS}
            usd.update({c.lower(): v for c, v in PEGS.items()})
            usd["usd"] = 1.0
            return ok({"date": str(d), "usd": usd})

        s.get(r"cdn\.jsdelivr\.net/npm/@fawazahmed0/currency-api@\d{4}-\d{2}-\d{2}/", snapshot)
        s.get(r"\d{4}-\d{2}-\d{2}\.currency-api\.pages\.dev/", snapshot)
        return s
