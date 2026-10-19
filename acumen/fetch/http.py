"""A polite HTTP client shared by every provider.

What it guarantees (the project brief's rule 6, "be a good citizen to data providers"):

* an honest ``User-Agent`` (from the configuration), never rotated and never disguised;
* a minimum gap between two requests to the same host (``set_interval`` / the ``interval`` argument);
* a few retries with growing pauses for network errors and 5xx answers, and **no retry storm**: a 429 is
  honoured once when the pause asked for is short, otherwise the host is switched off for the rest of the run;
* a circuit breaker per host: after several failures in a row the host is skipped until the run ends, so a
  provider that is down or blocking us costs seconds, not minutes;
* an in-memory cache with a time to live for requests that would otherwise repeat inside one run.

Nothing here knows about markets. Providers raise :class:`FetchError` (or a subclass) and the orchestration code
turns that into a data-health line and moves on to the next source.
"""

from __future__ import annotations

import json as _json
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

MAX_BYTES = 40 * 1024 * 1024  # a single answer larger than this is almost certainly a mistake


class FetchError(Exception):
    """Base class: the data could not be obtained. ``str(error)`` is safe to show to the owner."""

    def __init__(self, message: str, *, url: str = "", status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


class HttpError(FetchError):
    """The server answered with a status that is not a success (and not worth retrying)."""


class RateLimited(HttpError):
    """429 (or equivalent). The host is switched off for the rest of the run."""


class BreakerOpen(FetchError):
    """The host failed too often in this run and is being skipped."""


class ParseError(FetchError):
    """An answer arrived but is not in the shape the provider expects. Usually a changed website or API."""


@dataclass
class Reply:
    status: int
    content: bytes
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    elapsed: float = 0.0

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return _json.loads(self.content.decode("utf-8-sig", errors="replace"))
        except ValueError as exc:
            raise ParseError(f"not JSON ({_snippet(self.text)})", url=self.url, status=self.status) from exc


def _snippet(text: str, n: int = 80) -> str:
    one = " ".join(text.split())
    return one[:n] + ("…" if len(one) > n else "")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


class Client:
    """One per run. ``session`` and ``sleep`` can be replaced in tests so nothing touches the network or waits."""

    def __init__(
        self,
        user_agent: str = "AcumenPersonalAnalytics/1.0",
        *,
        timeout: float = 30.0,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 2,
        breaker_threshold: int = 6,
        default_interval: float = 0.25,
        max_rate_limit_wait: float = 20.0,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.max_retries = max_retries
        self.breaker_threshold = breaker_threshold
        self.default_interval = default_interval
        self.max_rate_limit_wait = max_rate_limit_wait
        self.intervals: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._fails: dict[str, int] = {}
        self.open_hosts: dict[str, str] = {}  # host -> why it was switched off
        self.pins: dict[str, str] = {}  # host -> IP address to connect to (see PinnedAdapter)
        self._cache: dict[tuple, tuple[float, Reply]] = {}
        self.stats: dict[str, dict[str, float]] = {}
        if hasattr(self.session, "headers"):
            try:
                self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
            except Exception:  # a fake session without a mutable header map
                pass

    # ------------------------------------------------------------------ configuration
    def set_interval(self, host: str, seconds: float) -> None:
        self.intervals[host.lower()] = float(seconds)

    def requests_to(self, host: str) -> int:
        return int(self.stats.get(host.lower(), {}).get("requests", 0))

    def is_open(self, host: str) -> bool:
        return host.lower() in self.open_hosts

    def reset(self, host: str) -> None:
        host = host.lower()
        self.open_hosts.pop(host, None)
        self._fails.pop(host, None)

    # ------------------------------------------------------------------ public verbs
    def get(self, url: str, **kw: Any) -> Reply:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Reply:
        return self.request("POST", url, **kw)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: Any = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        interval: float | None = None,
        timeout: float | None = None,
        ttl: float | None = None,
        retries: int | None = None,
        ok_statuses: tuple[int, ...] = (),
    ) -> Reply:
        """Send one request and return the :class:`Reply`.

        ``ok_statuses`` lists non-2xx statuses the caller wants returned instead of raised (for example 404 for
        "unknown symbol"). ``ttl`` (seconds) caches the reply in memory for the rest of the run.
        """
        host = host_of(url)
        key = None
        if ttl:
            key = (method, url, _freeze(params), _freeze(data), _freeze(json))
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < ttl:
                return hit[1]
        if host in self.open_hosts:
            raise BreakerOpen(f"{host} is being skipped for this run ({self.open_hosts[host]})", url=url)
        tries = (self.max_retries if retries is None else retries) + 1
        last: FetchError | None = None
        waited_429 = False
        for attempt in range(tries):
            self._pace(host, interval)
            started = self.clock()
            try:
                resp = self.session.request(method, url, params=params, data=data, json=json, headers=headers, timeout=timeout or self.timeout)
            except requests.RequestException as exc:  # connection reset, DNS, TLS, timeout...
                self._note(host, started, 0, False)
                last = FetchError(f"{type(exc).__name__}: {_snippet(str(exc), 120)}", url=url)
                self._fail(host, last)
                if host in self.open_hosts:
                    raise BreakerOpen(f"{host} is being skipped for this run ({self.open_hosts[host]})", url=url) from exc
                self._backoff(attempt)
                continue
            body = resp.content or b""
            self._note(host, started, len(body), 200 <= resp.status_code < 300)
            status = int(resp.status_code)
            if len(body) > MAX_BYTES:
                raise FetchError(f"answer is too large ({len(body) // (1024 * 1024)} MB)", url=url, status=status)
            reply = Reply(status, body, str(getattr(resp, "url", url) or url), {str(k).lower(): str(v) for k, v in dict(getattr(resp, "headers", {}) or {}).items()}, self.clock() - started)
            if 200 <= status < 300 or status in ok_statuses:
                self._fails[host] = 0
                if key is not None:
                    self._cache[key] = (self.clock(), reply)
                return reply
            if status == 429:
                wait = _retry_after(reply.headers.get("retry-after"))
                if not waited_429 and wait is not None and wait <= self.max_rate_limit_wait:
                    waited_429 = True
                    self.sleep(max(wait, 1.0))
                    continue
                err = RateLimited("rate limited (HTTP 429)", url=url, status=429)
                self._trip(host, "rate limited")
                raise err
            if status in (500, 502, 503, 504, 408):
                last = HttpError(f"HTTP {status}", url=url, status=status)
                self._fail(host, last)
                if host in self.open_hosts:
                    raise BreakerOpen(f"{host} is being skipped for this run ({self.open_hosts[host]})", url=url)
                self._backoff(attempt)
                continue
            # 4xx: the answer is final. 403 and 451 usually mean "this address is blocked", which counts against the host.
            err = HttpError(f"HTTP {status}", url=url, status=status)
            if status in (401, 403, 451):
                self._fail(host, err)
            raise err
        raise last or FetchError("request failed", url=url)

    # ------------------------------------------------------------------ internals
    def _pace(self, host: str, interval: float | None) -> None:
        gap = self.default_interval if interval is None else interval
        gap = max(gap, self.intervals.get(host, 0.0))
        last = self._last.get(host)
        if last is not None and gap > 0:
            wait = gap - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def _backoff(self, attempt: int) -> None:
        self.sleep(1.5 * (2 ** attempt))

    def _fail(self, host: str, err: FetchError) -> None:
        n = self._fails.get(host, 0) + 1
        self._fails[host] = n
        if n >= self.breaker_threshold:
            self._trip(host, f"{n} failures in a row, last: {err}")

    def _trip(self, host: str, why: str) -> None:
        self.open_hosts[host] = why

    def _note(self, host: str, started: float, size: int, ok: bool) -> None:
        s = self.stats.setdefault(host, {"requests": 0, "errors": 0, "bytes": 0, "seconds": 0.0})
        s["requests"] += 1
        s["errors"] += 0 if ok else 1
        s["bytes"] += size
        s["seconds"] += max(self.clock() - started, 0.0)


def _freeze(x: Any) -> Any:
    if isinstance(x, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in x.items()))
    if isinstance(x, (list, tuple)):
        return tuple(_freeze(v) for v in x)
    return x


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # an HTTP date: treat as "too long", the caller stops instead of waiting


class PinnedAdapter(HTTPAdapter):
    """Connect to a chosen IP address but still send the real host name (SNI and ``Host`` header) and verify the
    certificate against it. Used for the PSX data portal, whose DNS sometimes advertises a node that does not serve
    the data routes. Pinning costs nothing in security because the certificate is still checked for the host name."""

    def __init__(self, *args: Any, **kw: Any):
        self.pins: dict[str, str] = {}
        super().__init__(*args, **kw)

    def send(self, request, **kw):  # type: ignore[override]
        parts = urlsplit(request.url)
        host = (parts.hostname or "").lower()
        ip = self.pins.get(host)
        pool_kw = self.poolmanager.connection_pool_kw
        if ip and parts.scheme == "https":
            netloc = ip + (f":{parts.port}" if parts.port else "")
            request.url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
            request.headers["Host"] = host
            pool_kw["server_hostname"] = host
            pool_kw["assert_hostname"] = host
        else:
            pool_kw.pop("server_hostname", None)
            pool_kw.pop("assert_hostname", None)
        return super().send(request, **kw)


def install_pinning(client: Client) -> PinnedAdapter | None:
    """Mount a :class:`PinnedAdapter` on the client's real ``requests`` session (no-op for fakes)."""
    sess = client.session
    if not isinstance(sess, requests.Session):
        return None
    ad = PinnedAdapter()
    ad.pins = client.pins  # one shared table: client.pins[host] = ip takes effect on the next request
    sess.mount("https://", ad)
    return ad
