"""One-off probe (to be deleted): does the PSX data portal answer when a request carries the page's own request token?

Since 2026-10-23 no known address of dps.psx.com.pk serves the data routes to GitHub's servers. Published libraries report that the
portal began to ask, on 2026-09-24, for an ``X-Req-Id`` header equal to ``window.__ps._k``, a value every HTML page embeds. This
checks that, node by node, and looks for other nodes in the portal's address block.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile

HOST = "dps.psx.com.pk"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
PS_RX = re.compile(r"window\.__ps\s*=\s*(\{.*?\})\s*(?:;|</script>)", re.DOTALL)
TMP = tempfile.mkdtemp()


def section(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def curl(ip: str | None, path: str, *, method: str = "GET", data: str | None = None, headers: tuple[str, ...] = (), timeout: int = 25, url: str | None = None) -> tuple[str, str]:
    """(one-line summary, body text)."""
    out_file = os.path.join(TMP, f"b{abs(hash((ip, path, data, headers))) % 10**9}")
    cmd = ["curl", "-sS", "-m", str(timeout), "-A", UA, "-o", out_file, "-w", "%{http_code} %{content_type} %{size_download}b ip=%{remote_ip} %{time_total}s"]
    if ip:
        cmd += ["--resolve", f"{HOST}:443:{ip}"]
    for hdr in headers:
        cmd += ["-H", hdr]
    if method == "POST":
        cmd += ["-X", "POST", "--data", data or ""]
    cmd.append(url or f"https://{HOST}{path}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    summary = (proc.stdout or proc.stderr or "").strip()[:200]
    try:
        body = open(out_file, "rb").read().decode("utf-8", "replace")
    except OSError:
        body = ""
    return summary, body


def token_from(html: str) -> tuple[str | None, str]:
    m = PS_RX.search(html)
    if not m:
        i = html.find("__ps")
        return None, f"no window.__ps block; first '__ps' mention at {i}: {html[max(i - 80, 0):i + 200]!r}" if i >= 0 else "no '__ps' anywhere in the page"
    try:
        payload = json.loads(m.group(1))
    except ValueError:
        return None, f"window.__ps is not JSON: {m.group(1)[:200]!r}"
    keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
    tok = payload.get("_k") if isinstance(payload, dict) else None
    return (tok if isinstance(tok, str) else None), f"window.__ps keys={keys}, _k length={len(tok) if isinstance(tok, str) else None}, starts {str(tok)[:6]!r}"


JSON_ROUTES = ["/data/symbol-position", "/symbols", "/timeseries/eod/OGDC", "/timeseries/int/OGDC", "/timeseries/eod/KSE100"]
HTML_ROUTES = ["/market-watch", "/trading-board/REG/main", "/indices"]


def head(body: str, n: int = 110) -> str:
    return " ".join(body[:n].split())


def try_node(ip: str | None, label: str) -> str | None:
    section(f"node {label}")
    summary, html = curl(ip, "/")
    print(f"GET /  ->  {summary}")
    token, note = token_from(html)
    print(f"  token: {note}")
    base = ["X-Requested-With: XMLHttpRequest", f"Referer: https://{HOST}/", "Accept: application/json, text/javascript, */*; q=0.01"]
    for token_on in (False, True):
        if token_on and not token:
            continue
        hdrs = tuple(base + ([f"X-Req-Id: {token}"] if token_on else []))
        tag = "with token   " if token_on else "without token"
        for path in JSON_ROUTES + HTML_ROUTES:
            s, b = curl(ip, path, headers=hdrs)
            print(f"  {tag} GET {path:26s} {s}  | {head(b)}", flush=True)
        for what, post in (("one day", "date=2026-10-27"), ("one symbol-month", "month=10&year=2026&symbol=OGDC")):
            s, b = curl(ip, "/historical", method="POST", data=post, headers=tuple(list(hdrs) + ["Content-Type: application/x-www-form-urlencoded"]))
            print(f"  {tag} POST /historical {what:16s} {s}  | {head(b)}", flush=True)
    return token


def scan_block() -> list[str]:
    section("address block 52.128.23.0/24: which addresses answer for the portal's name")

    def one(i: int) -> tuple[str, str]:
        ip = f"52.128.23.{i}"
        s, b = curl(ip, "/", timeout=6)
        return ip, s

    found = []
    with cf.ThreadPoolExecutor(32) as pool:
        for ip, s in sorted(pool.map(one, range(256)), key=lambda x: tuple(int(p) for p in x[0].split("."))):
            if s and not s.startswith(("000", "curl", "timeout")):
                print(f"  {ip:14s} {s}")
                if s.startswith("200"):
                    found.append(ip)
    print(f"  answering with 200: {found}")
    return found


def main() -> int:
    section("DNS (system resolver, and two public resolvers over HTTPS)")
    import socket

    try:
        print("system:", sorted({str(i[4][0]) for i in socket.getaddrinfo(HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)}))
    except OSError as exc:
        print("system: failed", exc)
    for name, url, hdr in (("google", f"https://dns.google/resolve?name={HOST}&type=A", ()), ("cloudflare", f"https://cloudflare-dns.com/dns-query?name={HOST}&type=A", ("accept: application/dns-json",))):
        s, b = curl(None, "", headers=hdr, url=url, timeout=15)
        print(f"{name}: {s} | {head(b, 300)}")

    token = try_node(None, "DNS default")
    for ip in ("52.128.23.6", "52.128.23.16"):
        try_node(ip, ip)

    found = scan_block()
    for ip in found:
        if ip not in ("52.128.23.6", "52.128.23.16"):
            try_node(ip, f"{ip} (found by the scan)")

    section("how the front page's own script uses the token")
    _, html = curl(None, "/")
    srcs = re.findall(r'src="(/static/[^"]+\.js[^"]*)"', html)
    print("scripts:", srcs)
    for src in srcs:
        _, js = curl(None, src)
        print(f"{src}: {len(js)} chars")
        for key in ("X-Req-Id", "__ps", "timeseries", "market-watch", "trading-board", "symbol-position", "beforeSend", "ajaxSetup"):
            hits = [m.start() for m in re.finditer(re.escape(key), js)]
            print(f"  '{key}': {len(hits)} hit(s)")
            for pos in hits[:3]:
                print("     ..." + " ".join(js[max(pos - 120, 0):pos + 220].split()) + "...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
