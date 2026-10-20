"""A scripted stand-in for the network, so every fetch test runs offline and instantly."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@dataclass
class FakeResponse:
    status_code: int = 200
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def ok(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> FakeResponse:
    if isinstance(body, (dict, list)):
        raw = json.dumps(body).encode()
    elif isinstance(body, str):
        raw = body.encode()
    else:
        raw = bytes(body)
    return FakeResponse(status, raw, headers or {})


class FakeSession:
    """Routes ``(method, url)`` to scripted answers.

    ``routes`` is a list of ``(method, regex, answer)``; ``answer`` is a ``FakeResponse``, an exception, a callable
    taking the call details and returning one of those, or a list that is consumed one item per call (the last item
    repeats). The first route whose regex matches the full URL (with the query string) wins.
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}

    def add(self, method: str, pattern: str, answer: Any) -> "FakeSession":
        self.routes.append((method.upper(), re.compile(pattern), answer))
        return self

    def get(self, pattern: str, answer: Any) -> "FakeSession":
        return self.add("GET", pattern, answer)

    def post(self, pattern: str, answer: Any) -> "FakeSession":
        return self.add("POST", pattern, answer)

    def request(self, method: str, url: str, params: dict | None = None, data: Any = None, json: Any = None, headers: dict | None = None, timeout: float | None = None, **_: Any):
        full = url
        if params:
            full += ("&" if "?" in url else "?") + "&".join(f"{k}={v}" for k, v in params.items())
        call = {"method": method.upper(), "url": url, "full": full, "params": params, "data": data, "json": json, "headers": headers, "timeout": timeout}
        self.calls.append(call)
        for m, rx, answer in self.routes:
            if m == method.upper() and rx.search(full):
                if isinstance(answer, list):
                    item = answer[0] if len(answer) == 1 else answer.pop(0)
                else:
                    item = answer
                if callable(item):
                    item = item(call)
                if isinstance(item, BaseException):
                    raise item
                item.url = item.url or url
                return item
        raise requests.ConnectionError(f"no route for {method} {full}")

    def count(self, pattern: str) -> int:
        rx = re.compile(pattern)
        return sum(1 for c in self.calls if rx.search(c["full"]))
