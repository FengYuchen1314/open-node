#!/usr/bin/env python3
"""Fail-closed live validation for the versioned REALITY camouflage catalog."""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import ssl
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "backend/app/open_node/resources/camouflage-pools.json"
MAX_RESPONSE = 256 * 1024
USER_AGENT = "open-node-camouflage-validator/1"


def request_text(opener, url: str, timeout: float) -> str:
    request = Request(
        url,
        headers={"Accept": "application/json,text/plain", "User-Agent": USER_AGENT},
    )
    with opener.open(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_RESPONSE:
            raise ValueError("response is too large")
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise ValueError("response is too large")
    return body.decode("utf-8")


def cloudflare_ranges(
    opener, timeout: float
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    result = []
    for url in ("https://www.cloudflare.com/ips-v4/", "https://www.cloudflare.com/ips-v6/"):
        result.extend(
            ipaddress.ip_network(line)
            for line in request_text(opener, url, timeout).splitlines()
            if line.strip()
        )
    return result


def resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses = []
    for result in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("DNS did not return only public addresses")
    return addresses


def validate_tls(host: str, timeout: float) -> dict[str, str]:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    with (
        socket.create_connection((host, 443), timeout=timeout) as connection,
        context.wrap_socket(connection, server_hostname=host) as tls,
    ):
        if tls.version() != "TLSv1.3" or tls.selected_alpn_protocol() != "h2":
            raise ValueError("target did not negotiate TLS 1.3 with h2")
        certificate = tls.getpeercert()
    return {
        "tls_version": "TLSv1.3",
        "alpn": "h2",
        "certificate_not_after": certificate.get("notAfter", ""),
    }


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def validate_gfw(opener, host: str, timeout: float, freshness: timedelta) -> dict[str, object]:
    url = f"https://en.greatfire.org/api/url/https/{host}"
    value = json.loads(request_text(opener, url, timeout))
    last_tested = parse_time(value.get("last_tested") or "")
    if (
        value.get("found") is not True
        or value.get("verdict") != "not blocked"
        or value.get("blocked_percent") != 0
        or datetime.now(UTC) - last_tested > freshness
    ):
        raise ValueError("mainland measurement is blocked, absent, or stale")
    return {
        "gfw_verdict": "not_blocked",
        "gfw_last_tested": last_tested.isoformat().replace("+00:00", "Z"),
        "gfw_blocked_percent": 0,
    }


def validate_pool(opener, pool, ranges, timeout, freshness):
    host = pool["server_name"]
    if pool["target"] != f"{host}:443":
        raise ValueError("catalog target and server name differ")
    addresses = resolve(host)
    if any(any(address in network for network in ranges) for address in addresses):
        raise ValueError("DNS contains an address in Cloudflare's official ranges")
    return {
        "id": pool["id"],
        "region": pool["region"],
        "server_name": host,
        "addresses": [str(address) for address in addresses],
        "cloudflare": False,
        **validate_tls(host, timeout),
        **validate_gfw(opener, host, timeout, freshness),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--region")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--fresh-days", type=int, default=365)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.fresh_days <= 730 or not 1 <= args.timeout <= 60:
        parser.error("timeout and freshness are outside their safe bounds")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    pools = [
        pool for pool in catalog["pools"] if args.region is None or pool["region"] == args.region
    ]
    if not pools:
        parser.error("the selected catalog contains no pools")
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
    try:
        ranges = cloudflare_ranges(opener, args.timeout)
    except (OSError, ValueError, HTTPError, URLError) as exc:
        print(f"FAIL cloudflare-ranges: {type(exc).__name__}", file=sys.stderr, flush=True)
        return 1
    results = []
    failed = False
    for pool in pools:
        try:
            result = validate_pool(
                opener,
                pool,
                ranges,
                args.timeout,
                timedelta(days=args.fresh_days),
            )
            results.append(result)
            if not args.json:
                print(f"PASS {pool['id']} {pool['server_name']}", flush=True)
        except (OSError, ValueError, KeyError, json.JSONDecodeError, HTTPError, URLError) as exc:
            failed = True
            results.append(
                {"id": pool.get("id"), "server_name": pool.get("server_name"), "error": str(exc)}
            )
            if not args.json:
                print(
                    f"FAIL {pool.get('id', '?')} {pool.get('server_name', '?')}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
    if args.json:
        print(json.dumps({"success": not failed, "results": results}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
