"""A small simulated PSX data portal + TradingView + Yahoo, served through ``FakeSession``.

``World`` owns the truth (raw, unadjusted prices with optional bonus issues and holidays) and answers the same routes
the real portal does, using the real page structure (``data-order`` attributes, ``data-name`` headers).
"""

from __future__ import annotations

import datetime as dt
import json
from html import escape

import numpy as np

from tests.fakenet import FakeResponse, FakeSession, ok

SECTORS = ["COMMERCIAL BANKS", "CEMENT", "FERTILIZER", "OIL & GAS EXPLORATION COMPANIES"]


def _fmt(v: float) -> str:
    return f"{v:,.2f}"


def day_html(rows: list[dict]) -> str:
    head = "".join(f'<th data-name="{n}">{n.upper()}</th>' for n in ("symbol", "ldcp", "open", "high", "low", "close", "change", "percentChange", "volume"))
    body = []
    for r in rows:
        chg = r["close"] - r["ldcp"]
        cells = [f'<td data-search="{r["symbol"]}" data-order="{r["symbol"]}"><a class="tbl__symbol"><strong>{r["symbol"]}</strong></a></td>']
        for k in ("ldcp", "open", "high", "low", "close"):
            cells.append(f'<td class="right" data-order="{r[k]}">{_fmt(r[k])}</td>')
        cells.append(f'<td class="right" data-order="{chg}">{chg:.2f}</td>')
        cells.append(f'<td class="right" data-order="{chg / r["ldcp"] * 100 if r["ldcp"] else 0}">{chg / r["ldcp"] * 100 if r["ldcp"] else 0:.2f}%</td>')
        cells.append(f'<td class="right" data-order="{int(r["volume"])}">{int(r["volume"]):,}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="tbl" id="historicalTable"><thead class="tbl__head"><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def watch_html(rows: list[dict]) -> str:
    names = ("symbol", "sector", "listed", "ldcp", "open", "high", "low", "close", "change", "percentChange", "volume")
    head = "".join(f'<th data-name="{n}">{n.upper()}</th>' for n in names)
    body = []
    for r in rows:
        chg = r["close"] - r["ldcp"]
        cells = [f'<td data-search="{r["symbol"]}" data-order="{r["symbol"]}"><a class="tbl__symbol"><strong>{r["symbol"]}</strong></a></td>',
                 f'<td>{r.get("sector", "0801")}</td>', f'<td>{escape("ALLSHR,KSE100")}</td>']
        for k in ("ldcp", "open", "high", "low", "close"):
            cells.append(f'<td class="right" data-order="{r[k]}">{_fmt(r[k])}</td>')
        cells.append(f'<td class="right" data-order="{chg}">{chg:.2f}</td>')
        cells.append(f'<td class="right" data-order="{chg / r["ldcp"] * 100 if r["ldcp"] else 0}">x</td>')
        cells.append(f'<td class="right" data-order="{int(r["volume"])}">{int(r["volume"]):,}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="tbl"><thead class="tbl__head"><tr>{head}</tr></thead><tbody class="tbl__body">{"".join(body)}</tbody></table>'


class World:
    """Raw market history for ``n`` fake symbols over the weekdays ending ``end``."""

    def __init__(self, n: int = 26, days: int = 80, end: dt.date = dt.date(2026, 10, 2), seed: int = 3, holidays: tuple[dt.date, ...] = (), bonus: dict | None = None):
        self.rng = np.random.default_rng(seed)
        self.end = end
        self.holidays = set(holidays)
        self.symbols = [f"TST{i:02d}" for i in range(n)]
        self.dates = [d for d in (end - dt.timedelta(days=k) for k in range(int(days * 7 / 5) + 10)) if d.weekday() < 5 and d not in self.holidays][:days][::-1]
        self.bonus = bonus or {}  # {(symbol, date): factor}  e.g. 1/1.2 for a 20% bonus issue
        self.rows: dict[dt.date, dict[str, dict]] = {}
        self.missing_days: set[dt.date] = set()  # days the history route wrongly returns empty (outage-like)
        self.lagging: set[dt.date] = set()  # days the history route has not published yet although the live market watch has them
        self.cutoff: dt.date | None = None  # the portal does not know days after this one (lets a test reveal data day by day)
        self.token: str | None = None  # when set, the data routes want this as X-Req-Id (403 otherwise) and every page embeds it, as the real portal does
        px = {s: float(self.rng.uniform(20, 400)) for s in self.symbols}
        for d in self.dates:
            day = {}
            for s in self.symbols:
                prev = px[s]
                f = self.bonus.get((s, d), 1.0)
                ldcp = round(prev * f, 2)
                o = round(ldcp * (1 + self.rng.normal(0, 0.004)), 2)
                c = round(ldcp * (1 + float(np.clip(self.rng.normal(0, 0.015), -0.07, 0.07))), 2)
                h = round(max(o, c) * (1 + abs(self.rng.normal(0, 0.004))), 2)
                lo = round(min(o, c) * (1 - abs(self.rng.normal(0, 0.004))), 2)
                v = float(self.rng.uniform(5e4, 5e6))
                day[s] = {"symbol": s, "ldcp": ldcp, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                px[s] = c
            self.rows[d] = day

    def visible(self) -> list[dt.date]:
        return [d for d in self.dates if self.cutoff is None or d <= self.cutoff]

    def day_rows(self, d: dt.date) -> list[dict]:
        if d in self.missing_days or d in self.lagging or d not in self.rows or (self.cutoff is not None and d > self.cutoff):
            return []
        return list(self.rows[d].values())

    def watch_rows(self) -> list[dict]:
        """The live market watch: the newest visible session, whatever the history route says about it."""
        return list(self.rows[self.visible()[-1]].values())

    def symbols_json(self) -> list[dict]:
        out = [{"symbol": s, "name": f"Test Company {s}", "sectorName": SECTORS[i % len(SECTORS)], "isETF": False, "isDebt": False} for i, s in enumerate(self.symbols)]
        out.append({"symbol": "TSTSUKUK", "name": "A Sukuk", "sectorName": "SUKUK", "isETF": False, "isDebt": True})
        out.append({"symbol": "TST00-OCT", "name": "A future", "sectorName": "FUTURES", "isETF": False, "isDebt": False})
        return out

    def eod_json(self, code: str) -> dict:
        data = [[int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()), 100000 + i * 10.0, 1000, 100000 + i * 10.0 - 5] for i, d in enumerate(self.visible())]
        return {"status": 1, "message": "", "data": data[::-1]}

    def tv_json(self, columns: list[str]) -> dict:
        data = []
        last = self.rows[self.visible()[-1]]
        for s in self.symbols:
            r = last[s]
            vals = {"name": s, "description": f"Test Company {s}", "close": r["close"], "change": (r["close"] / r["ldcp"] - 1) * 100, "volume": r["volume"],
                    "market_cap_basic": r["close"] * 1e8, "price_earnings_ttm": 8.5, "earnings_per_share_diluted_ttm": r["close"] / 8.5, "dividends_yield": 4.2,
                    "price_book_fq": 1.3, "return_on_equity": 16.0, "book_value_per_share_fq": r["close"] / 1.3, "total_shares_outstanding_fundamental": 1e8,
                    "float_shares_outstanding": 4e7, "sector": "Finance", "type": "stock"}
            data.append({"s": f"PSX:{s}", "d": [vals.get(c) for c in columns]})
        return {"totalCount": len(data), "data": data}

    # ------------------------------------------------------------------ wiring
    def session(self, *, node_ok: bool = True, tv: bool = True, good_ip: str | None = None, http=None) -> FakeSession:
        """``good_ip``: only requests pinned to this address are served (unpinned ones get the bad node's 404)."""
        s = FakeSession()

        def served() -> bool:
            if not node_ok:
                return False
            if good_ip:
                return bool(http is not None and http.pins.get("dps.psx.com.pk") == good_ip)
            return True

        def guard(make):
            def f(call):
                if not served():
                    return FakeResponse(404, b"<html>not found</html>")
                if self.token is not None and (call.get("headers") or {}).get("X-Req-Id") != self.token:
                    return FakeResponse(403, b"")
                return make(call)
            return f

        def front(call):  # every node serves the pages, whether or not it serves the data routes
            block = '<script>window.__ps = {"_k": "%s", "lc": "en", "rv": 3, "tz": "Asia/Karachi"};</script>' % self.token if self.token else ""
            return ok(f"<!DOCTYPE html><html><head>{block}</head><body>data portal</body></html>")

        s.get(r"dps\.psx\.com\.pk/$", front)

        s.get(r"dps\.psx\.com\.pk/data/symbol-position", guard(lambda c: ok([{"name": "ADV", "value": 0.4}, {"name": "DEC", "value": 0.5}])))
        s.get(r"dps\.psx\.com\.pk/symbols", guard(lambda c: ok(self.symbols_json())))
        s.get(r"dps\.psx\.com\.pk/market-watch", guard(lambda c: ok(watch_html(self.watch_rows()))))
        s.get(r"dps\.psx\.com\.pk/timeseries/eod/", guard(lambda c: ok(self.eod_json(c["url"].rsplit("/", 1)[1]))))

        def hist(call):
            d = dt.date.fromisoformat(call["data"]["date"])
            rows = self.day_rows(d)
            return ok(day_html(rows) if rows else "<table class='tbl'><thead></thead><tbody></tbody></table>")

        s.post(r"dps\.psx\.com\.pk/historical", guard(hist))
        if tv:
            s.post(r"scanner\.tradingview\.com/pakistan/scan", lambda c: ok(self.tv_json(c["json"]["columns"])))
        else:
            s.post(r"scanner\.tradingview\.com", FakeResponse(403, b"forbidden"))
        return s


def yahoo_json(dates: list[dt.date], closes: list[float]) -> dict:
    ts = [int(dt.datetime(d.year, d.month, d.day, 5, 0, tzinfo=dt.timezone.utc).timestamp()) for d in dates]
    return {"chart": {"result": [{"meta": {"gmtoffset": 18000, "symbol": "X"}, "timestamp": ts, "indicators": {"quote": [{
        "open": [c * 0.999 for c in closes], "high": [c * 1.01 for c in closes], "low": [c * 0.99 for c in closes], "close": closes, "volume": [1000] * len(closes)}]}}], "error": None}}


def no_route(session: FakeSession) -> FakeSession:
    return session
