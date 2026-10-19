import pytest
import requests

from acumen.fetch.http import BreakerOpen, Client, FetchError, HttpError, ParseError, PinnedAdapter, RateLimited
from tests.fakenet import FakeClock, FakeSession, ok, FakeResponse


def make(session, **kw):
    clock = FakeClock()
    c = Client("TestAgent/1.0", session=session, sleep=clock.sleep, clock=clock, **kw)
    return c, clock


def test_success_and_user_agent():
    s = FakeSession().get(r"example\.org/a", ok({"x": 1}))
    c, _ = make(s)
    r = c.get("https://example.org/a")
    assert r.status == 200 and r.json() == {"x": 1}
    assert s.headers["User-Agent"] == "TestAgent/1.0"
    assert c.requests_to("example.org") == 1


def test_throttle_spaces_requests_per_host():
    s = FakeSession().get(r"example\.org", ok("{}")).get(r"other\.org", ok("{}"))
    c, clock = make(s, default_interval=0.0)
    c.set_interval("example.org", 2.0)
    c.get("https://example.org/1")
    c.get("https://example.org/2")
    c.get("https://other.org/1")  # a different host is not slowed down
    assert clock.slept == [2.0]


def test_retries_5xx_with_backoff_then_succeeds():
    s = FakeSession().get(r"example\.org", [FakeResponse(503), FakeResponse(502), ok("{}")])
    c, clock = make(s, default_interval=0.0)
    assert c.get("https://example.org/x").status == 200
    assert clock.slept == [1.5, 3.0]


def test_gives_up_after_retries_and_reports_the_status():
    s = FakeSession().get(r"example\.org", FakeResponse(500))
    c, _ = make(s, default_interval=0.0)
    with pytest.raises(HttpError) as e:
        c.get("https://example.org/x")
    assert e.value.status == 500
    assert s.count("example") == 3  # first try + 2 retries


def test_404_is_final_and_does_not_count_against_the_host():
    s = FakeSession().get(r"example\.org", FakeResponse(404))
    c, _ = make(s, default_interval=0.0, breaker_threshold=2)
    for _ in range(5):
        with pytest.raises(HttpError):
            c.get("https://example.org/missing")
    assert not c.is_open("example.org")
    assert s.count("example") == 5  # no retries on 404
    assert c.get("https://example.org/missing", ok_statuses=(404,)).status == 404


def test_429_with_short_pause_is_honoured_once():
    s = FakeSession().get(r"example\.org", [FakeResponse(429, headers={"Retry-After": "3"}), ok("{}")])
    c, clock = make(s, default_interval=0.0)
    assert c.get("https://example.org/x").status == 200
    assert 3.0 in clock.slept


def test_429_twice_switches_the_host_off():
    s = FakeSession().get(r"example\.org", FakeResponse(429, headers={"Retry-After": "1"}))
    c, _ = make(s, default_interval=0.0)
    with pytest.raises(RateLimited):
        c.get("https://example.org/x")
    assert c.is_open("example.org")
    n = s.count("example")
    with pytest.raises(BreakerOpen):
        c.get("https://example.org/y")
    assert s.count("example") == n  # nothing more was sent


def test_429_with_long_pause_does_not_wait():
    s = FakeSession().get(r"example\.org", FakeResponse(429, headers={"Retry-After": "600"}))
    c, clock = make(s, default_interval=0.0)
    with pytest.raises(RateLimited):
        c.get("https://example.org/x")
    assert clock.slept == [] and c.is_open("example.org")


def test_breaker_opens_after_consecutive_failures_and_recovers_on_reset():
    s = FakeSession().get(r"example\.org", requests.ConnectionError("boom"))
    c, _ = make(s, default_interval=0.0, breaker_threshold=4, max_retries=1)
    with pytest.raises(FetchError):
        c.get("https://example.org/1")
    with pytest.raises(BreakerOpen):
        c.get("https://example.org/2")
    assert c.is_open("example.org")
    c.reset("example.org")
    assert not c.is_open("example.org")


def test_a_success_clears_the_failure_count():
    s = FakeSession().get(r"example\.org", [FakeResponse(503)] * 2 + [ok("{}")] + [FakeResponse(503)] * 2 + [ok("{}")])
    c, _ = make(s, default_interval=0.0, breaker_threshold=3)
    assert c.get("https://example.org/1").status == 200
    assert c.get("https://example.org/2").status == 200
    assert not c.is_open("example.org")


def test_403_counts_against_the_host_because_it_usually_means_blocked():
    s = FakeSession().get(r"example\.org", FakeResponse(403))
    c, _ = make(s, default_interval=0.0, breaker_threshold=3)
    for _ in range(3):
        with pytest.raises(HttpError):
            c.get("https://example.org/x")
    assert c.is_open("example.org")


def test_cache_ttl_avoids_repeat_requests():
    s = FakeSession().get(r"example\.org", ok({"n": 1}))
    c, clock = make(s, default_interval=0.0)
    c.get("https://example.org/a", params={"q": 1}, ttl=60)
    c.get("https://example.org/a", params={"q": 1}, ttl=60)
    assert s.count("example") == 1
    c.get("https://example.org/a", params={"q": 2}, ttl=60)
    assert s.count("example") == 2
    clock.now += 61
    c.get("https://example.org/a", params={"q": 1}, ttl=60)
    assert s.count("example") == 3


def test_json_parse_error_names_the_problem():
    s = FakeSession().get(r"example\.org", ok("<html>maintenance</html>"))
    c, _ = make(s, default_interval=0.0)
    with pytest.raises(ParseError):
        c.get("https://example.org/a").json()


def test_stats_are_kept_per_host():
    s = FakeSession().get(r"example\.org", ok("x" * 10))
    c, _ = make(s, default_interval=0.0)
    c.get("https://example.org/a")
    st = c.stats["example.org"]
    assert st["requests"] == 1 and st["bytes"] == 10 and st["errors"] == 0


def test_pinned_adapter_rewrites_url_and_keeps_the_real_host(monkeypatch):
    sent = {}

    def fake_send(self, request, **kw):
        sent["url"], sent["host"] = request.url, request.headers.get("Host")
        sent["kw"] = dict(self.poolmanager.connection_pool_kw)
        return "sent"

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    ad = PinnedAdapter()
    ad.pins["dps.psx.com.pk"] = "52.128.23.16"
    req = requests.Request("GET", "https://dps.psx.com.pk/symbols?x=1").prepare()
    assert ad.send(req) == "sent"
    assert sent["url"] == "https://52.128.23.16/symbols?x=1" and sent["host"] == "dps.psx.com.pk"
    assert sent["kw"]["server_hostname"] == "dps.psx.com.pk" and sent["kw"]["assert_hostname"] == "dps.psx.com.pk"
    # other hosts are untouched and the SNI override is cleared
    req2 = requests.Request("GET", "https://example.org/a").prepare()
    ad.send(req2)
    assert sent["url"] == "https://example.org/a" and "server_hostname" not in sent["kw"]
