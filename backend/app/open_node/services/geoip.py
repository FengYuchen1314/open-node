"""Bounded GeoIP country lookup for subscription Provider filtering.

MMWX resolves a node hostname and asks IPinfo for its country.  Open Node keeps
that behavior optional, moves DNS into a killable isolated process, rejects
non-public addresses, and never embeds a third-party token in the source tree.
"""

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import quote

MAX_SERVER_BYTES = 512
_DNS_TIMEOUT = 2.0
_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class GeoIPLookupError(ValueError):
    pass


def _public_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise GeoIPLookupError("GeoIP server address is invalid") from None
    if (
        not address.is_global
        or address.is_private
        or address.is_reserved
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or getattr(address, "ipv4_mapped", None) is not None
        or getattr(address, "sixtofour", None) is not None
        or getattr(address, "teredo", None) is not None
    ):
        raise GeoIPLookupError("GeoIP only accepts public node addresses")
    return str(address)


def _hostname(value: str) -> str:
    try:
        result = value.removesuffix(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise GeoIPLookupError("GeoIP node hostname is invalid") from None
    labels = result.split(".")
    if (
        len(result) > 253
        or len(labels) < 2
        or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
        or labels[-1] in {"localhost", "local", "internal", "home", "lan"}
    ):
        raise GeoIPLookupError("GeoIP node hostname is invalid")
    return result


def _resolve_worker() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_SERVER_BYTES + 1)
        if len(raw) > MAX_SERVER_BYTES:
            raise GeoIPLookupError()
        value = json.loads(raw)
        if not isinstance(value, str):
            raise GeoIPLookupError()
        host = _hostname(value)
        answers = socket.getaddrinfo(
            host, 443, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP
        )
        approved = []
        for family, kind, protocol, _canonical, sockaddr in answers:
            if (
                family not in {socket.AF_INET, socket.AF_INET6}
                or kind != socket.SOCK_STREAM
                or protocol not in {0, socket.IPPROTO_TCP}
                or not isinstance(sockaddr, tuple)
            ):
                raise GeoIPLookupError()
            address = _public_ip(sockaddr[0])
            if address not in approved:
                approved.append(address)
        if not approved or len(approved) > 64:
            raise GeoIPLookupError()
        sys.stdout.write(approved[0])
        return 0
    except (GeoIPLookupError, OSError, ValueError, TypeError):
        return 2


def _resolve(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        literal = ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        return _public_ip(str(literal))
    encoded = json.dumps(_hostname(candidate), ensure_ascii=True).encode("ascii")
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR")
        if key in os.environ
    }
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--resolve-worker"],
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_DNS_TIMEOUT,
            check=False,
            env=environment,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise GeoIPLookupError("GeoIP node hostname could not be resolved") from None
    if result.returncode != 0:
        raise GeoIPLookupError("GeoIP node hostname could not be resolved")
    try:
        return _public_ip(result.stdout.decode("ascii"))
    except (UnicodeError, GeoIPLookupError):
        raise GeoIPLookupError("GeoIP node hostname could not be resolved") from None


class IPInfoCountryLookup:
    def __init__(self, token: str | None):
        self._token = token.strip() if isinstance(token, str) else ""
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def lookup(self, server: str) -> str:
        if not self.configured:
            raise GeoIPLookupError("GeoIP filtering requires an operator IPinfo token")
        with self._lock:
            cached = self._cache.get(server)
        if cached is not None:
            return cached
        from open_node.services.external_fetch import (
            ExternalFetchError,
            fetch_external_subscription,
        )

        address = _resolve(server)
        url = (
            "https://api.ipinfo.io/lite/"
            f"{quote(address, safe=':')}?token={quote(self._token, safe='')}"
        )
        try:
            result = fetch_external_subscription(
                url, user_agent="OpenNode/0.1 GeoIP", timeout=4, max_bytes=4096
            )
            value = json.loads(result.body)
        except (ExternalFetchError, UnicodeError, ValueError, TypeError):
            raise GeoIPLookupError("GeoIP country lookup failed") from None
        country = value.get("country_code") if isinstance(value, dict) else None
        country = country.upper() if isinstance(country, str) else ""
        if _COUNTRY.fullmatch(country) is None:
            raise GeoIPLookupError("GeoIP country lookup failed")
        with self._lock:
            if len(self._cache) >= 4096:
                self._cache.pop(next(iter(self._cache)))
            self._cache[server] = country
        return country


if __name__ == "__main__":
    raise SystemExit(_resolve_worker() if sys.argv[1:] == ["--resolve-worker"] else 2)
