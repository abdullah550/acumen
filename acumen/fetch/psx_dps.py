"""The PSX data portal (``dps.psx.com.pk``): the primary source for Pakistan equities.

The portal is the site's own back end. It is undocumented and can change without notice (see
``docs/psx-dps-ENDPOINTS.reference.md`` and ``docs/psx-dps-DISCOVERY.reference.md`` for what is known), so every parser
here checks the shape of what it gets and raises :class:`ParseError` instead of guessing.

Three quirks drive the design:

* **The data routes want the page's own request token.** Since 2026-09-24 the portal answers ``403`` to a data request that
  does not carry an ``X-Req-Id`` header equal to ``window.__ps._k``, a value every HTML page embeds (its own script does
  ``$.ajaxSetup({headers: {"X-Req-Id": ps._k}})``). It rotates every few minutes. :meth:`Dps.refresh_token` reads it from
  the front page, :meth:`Dps._call` sends it and, on a 403, reads a new one and tries once more. The token is the portal's
  own, public and unauthenticated: this does what the portal's pages do. If a page ever carries no token the requests go
  out without one (the mechanism may be removed again), and the data health line says so.
* **Some nodes behind the host name do not serve the data routes** (they answer 404, or 403 when a browser-style
  header is sent) while serving ordinary pages, and DNS may advertise only such a node. :meth:`Dps.ensure_node`
  probes a small JSON route and, when the DNS answer is bad, tries other known addresses and pins the first one that
  works. The certificate is still verified for the real host name.
* **Rate limiting is real and unpublished.** The client spaces requests out (``data.psx.min_interval_s``) and stops
  for the run when the host trips its circuit breaker.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import socket
from typing import Any, Callable

import pandas as pd
from bs4 import BeautifulSoup

from .common import bars_frame, epoch_to_date, to_float
from .http import Client, FetchError, HttpError, ParseError, install_pinning

HOST = "dps.psx.com.pk"
BASE = f"https://{HOST}"
# Address that served the data routes when this was written; used only if DNS and the saved list fail.
SEED_NODES = ["52.128.23.16"]
SYMBOL_RX = re.compile(r"^[A-Z0-9]{1,12}$")
MIN_ROWS_FOR_TRADING_DAY = 10
TOKEN_HEADER = "X-Req-Id"
TOKEN_PAGE = "/"  # any HTML page of the portal embeds the token; the front page is the lightest that always exists
TOKEN_TTL_S = 150.0  # the portal rotates it about every five minutes: read a new one well before that
_PS_RX = re.compile(r"window\.__ps\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.DOTALL)
_TOKEN_RX = re.compile(r"^[A-Za-z0-9_-]{16,}$")


def parse_token(html: str) -> str | None:
    """The request token (``window.__ps._k``) embedded in a portal page, or ``None`` when the page carries none."""
    m = _PS_RX.search(html)
    if m is None:
        return None
    try:
        payload = json.loads(m.group(1))
    except ValueError:
        return None
    tok = payload.get("_k") if isinstance(payload, dict) else None
    return tok if isinstance(tok, str) and _TOKEN_RX.fullmatch(tok) else None

_ALIASES = {
    "symbol": "symbol", "sector": "sector", "listedin": "listed", "listed": "listed", "ldcp": "ldcp", "open": "open", "high": "high", "low": "low",
    "current": "close", "close": "close", "change": "change", "change%": "pct", "changepercent": "pct", "percentchange": "pct", "volume": "volume",
    "date": "time", "time": "time",
}


def _header_key(th: Any) -> str:
    name = th.get("data-name")
    if name:
        key = re.sub(r"[^a-z%]", "", str(name).lower())
        return _ALIASES.get(key, key)
    text = re.sub(r"[^a-z%]", "", th.get_text(" ", strip=True).lower())
    return _ALIASES.get(text, text)


def parse_table(html: str, index: int = 0) -> list[dict[str, str]]:
    """Rows of the ``index``-th table as ``{column: value}``.

    The value is the cell's ``data-order`` attribute (the clean number, or an epoch on date columns) when it has one,
    otherwise its text. Pages with several tables (the sector summary) must be read one table at a time.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if len(tables) <= index:
        return []
    table = tables[index]
    head = table.find("thead")
    keys = [_header_key(th) for th in head.find_all("th")] if head else []
    body = table.find("tbody")
    trs = body.find_all("tr") if body else table.find_all("tr")[1:]
    rows: list[dict[str, str]] = []
    for tr in trs:
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        row: dict[str, str] = {}
        for i, td in enumerate(tds):
            key = keys[i] if i < len(keys) else f"c{i}"
            if key in row:
                key = f"{key}{i}"
            order = td.get("data-order")
            row[key] = str(order) if order is not None else td.get_text(" ", strip=True)
        rows.append(row)
    return rows


def _num_row(row: dict[str, str]) -> dict[str, Any]:
    sym = str(row.get("symbol", "")).strip().upper()
    return {
        "symbol": sym, "sector": row.get("sector"), "listed": [x for x in str(row.get("listed", "")).split(",") if x],
        "ldcp": to_float(row.get("ldcp")), "open": to_float(row.get("open")), "high": to_float(row.get("high")), "low": to_float(row.get("low")),
        "close": to_float(row.get("close")), "change": to_float(row.get("change")), "pct": to_float(row.get("pct")), "volume": to_float(row.get("volume")),
    }


def parse_market_watch(html: str) -> list[dict[str, Any]]:
    """Every symbol trading today with its session numbers. Raises :class:`ParseError` when the table is not there."""
    rows = [_num_row(r) for r in parse_table(html)]
    rows = [r for r in rows if SYMBOL_RX.match(r["symbol"])]
    if not rows:
        raise ParseError("market watch table not found or empty")
    return rows


def parse_day(html: str) -> list[dict[str, Any]]:
    """All symbols for one past trading day (``POST /historical``), or ``[]`` for a weekend or holiday."""
    rows = [_num_row(r) for r in parse_table(html)]
    rows = [r for r in rows if SYMBOL_RX.match(r["symbol"]) and r["close"] is not None and r["close"] > 0]
    return rows if len(rows) >= MIN_ROWS_FOR_TRADING_DAY else []


def parse_indices(html: str) -> dict[str, dict[str, float | None]]:
    """``{code: {current, high, low, change, pct}}`` from ``/indices``."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict[str, float | None]] = {}
    for tr in soup.find_all("tr"):
        a = tr.find("a", attrs={"data-code": True})
        if not a:
            continue
        tds = tr.find_all("td", recursive=False)
        vals = [to_float(td.get("data-order") if td.get("data-order") is not None else td.get_text(strip=True)) for td in tds[1:]]
        if len(vals) >= 5:
            out[str(a["data-code"])] = {"high": vals[0], "low": vals[1], "current": vals[2], "change": vals[3], "pct": vals[4]}
    return out


def parse_eod(payload: Any, symbol: str = "") -> pd.DataFrame:
    """``/timeseries/eod/{SYM}``: ``{status, data: [[epoch, close, volume, open], ...]}`` newest first.

    High and low are not published on this route, so the bars carry ``approx_hl = True``.
    """
    if not isinstance(payload, dict) or "data" not in payload:
        raise ParseError(f"unexpected EOD payload for {symbol}")
    data = payload.get("data") or []
    rows = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        ep, close = to_float(item[0]), to_float(item[1])
        if ep is None or close is None:
            continue
        vol = to_float(item[2])
        opn = to_float(item[3]) if len(item) > 3 else None
        rows.append((epoch_to_date(ep), opn, None, None, close, vol))
    return bars_frame(rows, ["open", "high", "low", "close", "volume"])


def parse_symbols(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ParseError("symbols list is not a list")
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol", "")).strip().upper()
        if not SYMBOL_RX.match(sym):
            continue
        out.append({"symbol": sym, "name": str(item.get("name") or sym).strip(), "sector": str(item.get("sectorName") or "").strip(),
                    "is_etf": bool(item.get("isETF")), "is_debt": bool(item.get("isDebt"))})
    if not out:
        raise ParseError("symbols list is empty")
    return out


class Dps:
    """Calls to the portal, with node probing. ``state`` is a small dict persisted by the caller between runs."""

    def __init__(self, http: Client, *, interval: float = 1.2, state: dict | None = None, log: Callable[[str], None] = print, resolver: Callable[[], list[str]] | None = None):
        self.http = http
        self.state = state if state is not None else {}
        self.log = log
        self.resolver = resolver or _resolve
        self.http.set_interval(HOST, interval)
        install_pinning(http)
        self.reprobes = 0
        self.node: str | None = None  # None = whatever DNS says
        self.token: str | None = None  # the portal's request token (see the module note); None = none read, or the pages carry none
        self.token_at: float | None = None  # when it was read, on the http client's clock; None = never
        self.token_note: str | None = None  # why there is no token, for the data health line
        self.token_reads = 0

    # ------------------------------------------------------------------ the request token
    def refresh_token(self) -> str | None:
        """Read the request token from the portal's front page and remember it.

        Keeps the token it already had when the page cannot be read (a network error says nothing about the token), and
        forgets it when the page loads but carries none: the portal may have dropped the mechanism, and requests then go
        out the way they did before it existed.
        """
        self.token_reads += 1
        self.token_at = self.http.clock()  # one try per interval: a 403 on a data route forces the next one
        try:
            html = self.http.get(BASE + TOKEN_PAGE, retries=1, interval=0.5).text
        except FetchError as exc:
            self.token_note = f"the front page could not be read for the request token ({exc})"
            return self.token
        token = parse_token(html)
        self.token = token
        self.token_note = None if token else "the portal's pages carry no request token (window.__ps._k)"
        return token

    def auth_headers(self) -> dict[str, str]:
        """The headers the portal's own scripts send on a data request, with a token no older than ``TOKEN_TTL_S``."""
        if self.token_at is None or self.http.clock() - self.token_at > TOKEN_TTL_S:
            self.refresh_token()
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"}
        if self.token:
            headers[TOKEN_HEADER] = self.token
        return headers

    # ------------------------------------------------------------------ node handling
    def _probe(self) -> bool:
        try:
            data = self.http.get(f"{BASE}/data/symbol-position", headers=self.auth_headers(), retries=0, interval=0.5).json()
        except FetchError:
            return False
        return isinstance(data, list) and bool(data) and isinstance(data[0], dict) and "name" in data[0]

    def ensure_node(self) -> bool:
        """True when the portal's data routes answer. Pins an address when DNS hands out a node that does not serve them."""
        self.http.pins.pop(HOST, None)
        self.http.reset(HOST)
        self.refresh_token()  # a data route answers 403 without it, which would look like a node that serves no data
        if self._probe():
            self.node = None
            return True
        seen = [str(x) for x in self.state.get("seen", [])]
        good = [str(x) for x in self.state.get("good", [])]
        dns = [ip for ip in self.resolver() if ip]
        candidates: list[str] = []
        for ip in [*good, *dns, *seen, *SEED_NODES]:
            if ip not in candidates:
                candidates.append(ip)
        self.state["seen"] = sorted(set(seen) | set(dns))[:20]
        for ip in candidates[:8]:
            self.http.pins[HOST] = ip
            self.http.reset(HOST)
            if self._probe():
                self.node = ip
                self.state["good"] = [ip, *[g for g in good if g != ip]][:5]
                self.log(f"  psx: the portal's DNS answer does not serve data; using node {ip}")
                return True
        self.http.pins.pop(HOST, None)
        self.http.reset(HOST)
        return False

    def _call(self, method: str, path: str, **kw: Any):
        extra = dict(kw.pop("headers", None) or {})

        def send():
            return self.http.request(method, BASE + path, headers={**self.auth_headers(), **extra}, **kw)

        new_token_tried = False
        while True:
            try:
                return send()
            except HttpError as exc:
                if exc.status == 403 and not new_token_tried:
                    # most likely the token has rotated (or was never read): read a new one and ask once more
                    new_token_tried = True
                    self.http.reset(HOST)  # a 403 counts against the host; this one is being dealt with
                    if self.refresh_token():
                        continue
                # 404/403 on a data route is the bad-node signature: look for a working node once or twice, then retry.
                if exc.status in (403, 404) and self.reprobes < 2:
                    self.reprobes += 1
                    if self.ensure_node():
                        return send()
                raise

    # ------------------------------------------------------------------ routes
    def symbols(self) -> list[dict[str, Any]]:
        return parse_symbols(self._call("GET", "/symbols", ttl=3600).json())

    def market_watch(self) -> list[dict[str, Any]]:
        return parse_market_watch(self._call("GET", "/market-watch", ttl=300).text)

    def indices(self) -> dict[str, dict[str, float | None]]:
        return parse_indices(self._call("GET", "/indices", ttl=300).text)

    def day(self, day: dt.date) -> list[dict[str, Any]]:
        """Every symbol's session for ``day``; ``[]`` for a weekend or public holiday."""
        html = self._call("POST", "/historical", data={"date": day.isoformat()}).text
        return parse_day(html)

    def eod(self, symbol: str) -> pd.DataFrame:
        payload = self._call("GET", f"/timeseries/eod/{symbol}").json()
        df = parse_eod(payload, symbol)
        if len(df) == 0:
            raise ParseError(f"no end-of-day data for {symbol}")
        return df


def _resolve() -> list[str]:
    try:
        infos = socket.getaddrinfo(HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({str(i[4][0]) for i in infos})
