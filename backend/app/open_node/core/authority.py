import re
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv6Address, ip_address

from starlette.types import ASGIApp, Receive, Scope, Send


class InvalidAuthority(ValueError):
    pass


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


def normalize_authority(value: str) -> str:
    """Validate an HTTP authority and normalize ASCII case only.

    Deliberately do not collapse equivalent spellings, default ports, trailing dots, or
    IPv6 forms.  The configured authority remains an exact trust boundary after case
    normalization.
    """
    if not value or not value.isascii() or value != value.strip():
        raise InvalidAuthority("Authority must be non-empty visible ASCII without whitespace")
    if any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise InvalidAuthority("Authority contains whitespace or control characters")

    normalized = value.lower()
    if normalized.startswith("["):
        closing = normalized.find("]")
        if closing < 0 or "%" in normalized[: closing + 1]:
            raise InvalidAuthority("IPv6 authorities require an unscoped bracketed address")
        host = normalized[1:closing]
        suffix = normalized[closing + 1 :]
        try:
            parsed = ip_address(host)
        except ValueError:
            raise InvalidAuthority("Bracketed authority is not a valid IPv6 address") from None
        if not isinstance(parsed, IPv6Address):
            raise InvalidAuthority("Only IPv6 addresses may be bracketed")
        _validate_port_suffix(suffix)
        return normalized

    if "[" in normalized or "]" in normalized or normalized.count(":") > 1:
        raise InvalidAuthority("IPv6 authorities must use brackets")
    host, separator, port = normalized.rpartition(":")
    if not separator:
        host, port = normalized, None
    else:
        _validate_port(port)
    if not host or len(host) > 253 or host.endswith("."):
        raise InvalidAuthority("Authority host is invalid")

    try:
        parsed = ip_address(host)
    except ValueError:
        parsed = None
    if parsed is not None:
        if not isinstance(parsed, IPv4Address):
            raise InvalidAuthority("IPv6 authorities must use brackets")
    else:
        if all(char.isdigit() or char == "." for char in host):
            raise InvalidAuthority("Numeric authority is not a valid IPv4 address")
        if any(not _DNS_LABEL.fullmatch(label) for label in host.split(".")):
            raise InvalidAuthority("Authority host is not a valid DNS name")
    return normalized


def normalize_authorities(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        authority = normalize_authority(value)
        if authority in seen:
            raise InvalidAuthority("Trusted authorities must be unique after case normalization")
        seen.add(authority)
        normalized.append(authority)
    return normalized


def _validate_port_suffix(suffix: str) -> None:
    if not suffix:
        return
    if not suffix.startswith(":"):
        raise InvalidAuthority("Unexpected data follows the bracketed IPv6 address")
    _validate_port(suffix[1:])


def _validate_port(port: str) -> None:
    if (
        not port
        or not port.isascii()
        or not port.isdigit()
        or len(port) > 5
        or (len(port) > 1 and port.startswith("0"))
        or not 1 <= int(port) <= 65535
    ):
        raise InvalidAuthority("Authority port must be a canonical integer from 1 to 65535")


def _scope_authority(scope: Scope) -> str:
    values: list[bytes] = []
    for name, value in scope.get("headers", []):
        if name.lower() in {b"host", b":authority"}:
            values.append(value)
    if len(values) != 1:
        raise InvalidAuthority("Exactly one Host or :authority header is required")
    try:
        decoded = values[0].decode("ascii")
    except UnicodeDecodeError:
        raise InvalidAuthority("Authority must be ASCII") from None
    return normalize_authority(decoded)


class TrustedAuthorityMiddleware:
    """Reject ambiguous or untrusted HTTP authorities before routing."""

    def __init__(self, app: ASGIApp, authorities: Iterable[str]) -> None:
        self.app = app
        self.authorities = frozenset(normalize_authorities(authorities))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not self.authorities:
            await self.app(scope, receive, send)
            return
        try:
            trusted = _scope_authority(scope) in self.authorities
        except InvalidAuthority:
            trusted = False
        if trusted:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Untrusted authority"})
        else:
            body = b"Invalid Host header"
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
