"""One-off probe (to be deleted): what does the PSX data portal answer to GitHub's servers?

Since the morning of 2026-10-23 no known address of dps.psx.com.pk serves the data routes. This prints, for every address DNS and
the saved list know, the status, type and start of the answer to each route, with and without the app's own headers.
"""

from __future__ import annotations

import socket
import subprocess
import sys

HOST = "dps.psx.com.pk"
UA_APP = "AcumenPersonalAnalytics/1.0"
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
GET = ["/", "/data/symbol-position", "/symbols", "/market-watch", "/indices", "/timeseries/eod/OGDC", "/timeseries/int/OGDC", "/company/OGDC"]


def section(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def curl(ip: str | None, path: str, *, ua: str, post: str | None = None, head: int = 0) -> str:
    cmd = ["curl", "-sS", "-m", "25", "-A", ua, "-D", "-", "-o", "/tmp/psx_body", "-w", "\nSTATUS %{http_code} bytes=%{size_download} type=%{content_type} ip=%{remote_ip} time=%{time_total}s\n"]
    if ip:
        cmd += ["--resolve", f"{HOST}:443:{ip}"]
    if post is not None:
        cmd += ["-X", "POST", "--data", post]
    cmd.append(f"https://{HOST}{path}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        return "timeout"
    text = (out.stdout or "") + (out.stderr or "")
    status = [ln for ln in text.splitlines() if ln.startswith("STATUS")]
    headers = [ln.strip() for ln in text.splitlines() if ln.lower().startswith(("server:", "via:", "location:", "set-cookie:", "cf-ray:", "x-", "content-type:"))]
    try:
        body = open("/tmp/psx_body", "rb").read(head or 160).decode("utf-8", "replace").replace("\n", " ")
    except OSError:
        body = ""
    return f"{status[-1] if status else text[:200]}\n      headers: {' | '.join(headers[:6])}\n      body: {body[:head or 160]}"


def main() -> int:
    section("DNS")
    try:
        ips = sorted({str(i[4][0]) for i in socket.getaddrinfo(HOST, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)})
    except OSError as exc:
        ips = []
        print(f"DNS failed: {exc}")
    print("DNS answer:", ips)
    try:
        v6 = sorted({str(i[4][0]) for i in socket.getaddrinfo(HOST, 443, family=socket.AF_INET6, type=socket.SOCK_STREAM)})
        print("IPv6:", v6)
    except OSError as exc:
        print("IPv6: none", exc)
    candidates = [None, *ips, *[ip for ip in ("52.128.23.6", "52.128.23.16") if ip not in ips]]
    for ip in candidates:
        for ua_name, ua in (("app", UA_APP), ("browser", UA_BROWSER)):
            section(f"node {ip or 'DNS default'}, user agent {ua_name}")
            for path in GET:
                print(f"GET {path}\n  {curl(ip, path, ua=ua)}", flush=True)
            print(f"POST /historical date=2026-10-22\n  {curl(ip, '/historical', ua=ua, post='date=2026-10-22')}", flush=True)
    section("the site's front page, links to its data routes")
    subprocess.run(f"curl -sS -m 25 -A '{UA_BROWSER}' https://{HOST}/ | grep -o -E '(src|href)=\"[^\"]+\"' | head -40", shell=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
