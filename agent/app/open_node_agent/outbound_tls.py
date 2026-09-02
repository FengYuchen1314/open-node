"""Safe TLS certificate pinning for manually managed Xray outbounds."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
import ssl
from typing import Any

from open_node_agent.runtime import RuntimeFailure

ENDPOINT = "/api/child/outbound-tls-pin/probe"
SUPPORTED_PROTOCOLS = frozenset(
    {"vless", "vmess", "trojan", "shadowsocks", "socks", "http", "anytls"}
)
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_ALPN = re.compile(r"[A-Za-z0-9._/-]+")
_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_MANAGED_EGRESS_OUTBOUND_PREFIX = "managed-egress:"


def _public_unicast_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return one numeric public-unicast address, including transition checks.

    ``is_global`` alone is not an SSRF boundary: Python classifies multicast as
    global, and an IPv6 transition address can embed a private IPv4 destination.
    """

    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise RuntimeFailure("TLS certificate probe returned an invalid address set") from exc
    if (
        not address.is_global
        or address.is_multicast
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None)
    ):
        raise RuntimeFailure("TLS certificate probe only permits publicly routable peers")

    embedded: list[ipaddress.IPv4Address] = []
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            embedded.append(address.ipv4_mapped)
        if address.sixtofour is not None:
            embedded.append(address.sixtofour)
        if address.teredo is not None:
            embedded.extend(address.teredo)
        if address in _NAT64_WELL_KNOWN:
            embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
    if any(not item.is_global or item.is_multicast for item in embedded):
        raise RuntimeFailure("TLS certificate probe only permits publicly routable peers")
    return address


def normalize_certificate_pins(value: Any) -> str:
    """Return the exact comma-separated hex format consumed by Xray-core-mmwx."""

    if not isinstance(value, str):
        raise RuntimeFailure("TLS outbound requires pinnedPeerCertSha256")
    raw_pins = [item.strip() for item in value.split(",") if item.strip()]
    if not raw_pins or len(raw_pins) > 8:
        raise RuntimeFailure("pinnedPeerCertSha256 must contain between 1 and 8 hashes")
    normalized: list[str] = []
    for raw in raw_pins:
        pin = raw.replace(":", "").lower()
        if len(pin) != 64 or any(character not in "0123456789abcdef" for character in pin):
            raise RuntimeFailure(
                "Each pinnedPeerCertSha256 value must be a 32-byte SHA-256 hash"
            )
        if pin not in normalized:
            normalized.append(pin)
    return ",".join(normalized)


def validate_manual_outbound_tls(outbound: dict) -> None:
    """Enforce fork-compatible TLS policy at the Agent trust boundary.

    This is deliberately called by ``/api/child/outbounds`` itself, so a raw
    command cannot bypass the control-plane form validation.
    """

    stream = outbound.get("streamSettings")
    if not isinstance(stream, dict):
        return
    tls_settings = stream.get("tlsSettings")
    if isinstance(tls_settings, dict) and "allowInsecure" in tls_settings:
        raise RuntimeFailure(
            "TLS outbound must not use allowInsecure; use pinnedPeerCertSha256"
        )
    if str(stream.get("security") or "").strip().lower() != "tls":
        return
    protocol = str(outbound.get("protocol") or "").strip().lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise RuntimeFailure("TLS pinning is not supported for this outbound protocol")
    if not isinstance(tls_settings, dict):
        raise RuntimeFailure("TLS outbound requires tlsSettings")
    pin = tls_settings.get("pinnedPeerCertSha256")
    network = str(stream.get("network") or "tcp").strip().lower()
    if network == "hysteria" or "hysteriaSettings" in stream:
        if pin not in (None, ""):
            tls_settings["pinnedPeerCertSha256"] = normalize_certificate_pins(pin)
        return
    tls_settings["pinnedPeerCertSha256"] = normalize_certificate_pins(pin)


def validate_changed_managed_outbound_tls(expected: dict, candidate: dict) -> None:
    """Validate only managed TLS outbounds introduced or changed by egress apply.

    Existing unrelated TLS outbounds belong to the operator and are deliberately
    outside this endpoint's migration boundary.  Comparing the complete outbound
    also prevents a caller from keeping the tag while changing TLS material.
    """

    expected_outbounds = expected.get("outbounds", [])
    candidate_outbounds = candidate.get("outbounds", [])
    if not isinstance(expected_outbounds, list) or not isinstance(candidate_outbounds, list):
        return
    previous_by_tag: dict[str, list[dict]] = {}
    for item in expected_outbounds:
        if isinstance(item, dict):
            previous_by_tag.setdefault(str(item.get("tag") or ""), []).append(item)
    for item in candidate_outbounds:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "")
        if not tag.startswith(_MANAGED_EGRESS_OUTBOUND_PREFIX):
            continue
        if any(item == previous for previous in previous_by_tag.get(tag, [])):
            continue
        validate_manual_outbound_tls(item)


def _peer_name(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise RuntimeFailure(f"{field} must be a DNS name or IP address")
    normalized = value.strip().rstrip(".").lower()
    if not normalized or len(normalized) > 253 or any(ord(char) < 33 for char in normalized):
        raise RuntimeFailure(f"{field} must be a DNS name or IP address")
    try:
        return str(ipaddress.ip_address(normalized))
    except ValueError:
        pass
    try:
        ascii_name = normalized.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RuntimeFailure(f"{field} must be a DNS name or IP address") from exc
    labels = ascii_name.split(".")
    if len(labels) < 2 or any(
        not label or len(label) > 63 or _DNS_LABEL.fullmatch(label) is None
        for label in labels
    ):
        raise RuntimeFailure(f"{field} must be a DNS name or IP address")
    return ascii_name


def validate_probe_request(body: dict) -> dict:
    allowed = {"protocol", "address", "port", "server_name", "alpn", "timeout_ms"}
    if set(body) - allowed:
        raise RuntimeFailure("TLS certificate probe contains unsupported fields")
    protocol = str(body.get("protocol") or "").strip().lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        raise RuntimeFailure("TLS certificate probe does not support this protocol")
    address = _peer_name(body.get("address"), "address")
    server_name = _peer_name(body.get("server_name") or address, "server_name")
    port = body.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise RuntimeFailure("TLS certificate probe port must be between 1 and 65535")
    timeout_ms = body.get("timeout_ms", 8_000)
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or not 1_000 <= timeout_ms <= 12_000
    ):
        raise RuntimeFailure("TLS certificate probe timeout must be between 1000 and 12000 ms")
    alpn = body.get("alpn", [])
    if (
        not isinstance(alpn, list)
        or len(alpn) > 8
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > 32
            or _ALPN.fullmatch(item) is None
            for item in alpn
        )
    ):
        raise RuntimeFailure("TLS certificate probe ALPN list is invalid")
    return {
        "protocol": protocol,
        "address": address,
        "port": port,
        "server_name": server_name,
        "alpn": list(dict.fromkeys(alpn)),
        "timeout_ms": timeout_ms,
    }


async def _resolve_public(address: str, port: int, timeout: float) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        records = await asyncio.wait_for(
            loop.getaddrinfo(
                address,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            ),
            timeout=timeout,
        )
    except (TimeoutError, OSError) as exc:
        raise RuntimeFailure("TLS certificate probe could not resolve the peer") from exc
    addresses = list(dict.fromkeys(str(record[4][0]) for record in records))
    if not addresses or len(addresses) > 16:
        raise RuntimeFailure("TLS certificate probe returned an invalid address set")
    parsed = [_public_unicast_address(item) for item in addresses]
    return [str(item) for item in parsed]


async def _leaf_sha256(
    address: str,
    port: int,
    server_name: str,
    alpn: list[str],
    timeout: float,
) -> str:
    # Verification is intentionally disabled only in this one-shot fingerprint
    # collector. The resulting runtime outbound is pinned and never receives
    # allowInsecure.
    peer = _public_unicast_address(address)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    if alpn:
        context.set_alpn_protocols(alpn)
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                str(peer),
                port,
                family=socket.AF_INET if peer.version == 4 else socket.AF_INET6,
                flags=socket.AI_NUMERICHOST,
                ssl=context,
                server_hostname=server_name,
            ),
            timeout=timeout,
        )
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
        if not certificate or len(certificate) > 1024 * 1024:
            raise RuntimeFailure("TLS certificate probe received no valid leaf certificate")
        return hashlib.sha256(certificate).hexdigest()
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        raise RuntimeFailure("TLS certificate probe handshake failed") from exc
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (TimeoutError, OSError, ssl.SSLError):
                pass


async def probe_tls_certificate(body: dict) -> dict:
    request = validate_probe_request(body)
    timeout = request["timeout_ms"] / 1000
    deadline = asyncio.get_running_loop().time() + timeout
    addresses = await _resolve_public(request["address"], request["port"], timeout)
    last_error: RuntimeFailure | None = None
    for address in addresses[:8]:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            fingerprint = await _leaf_sha256(
                address,
                request["port"],
                request["server_name"],
                request["alpn"],
                remaining,
            )
            return {"success": True, "pinned_peer_cert_sha256": fingerprint}
        except RuntimeFailure as exc:
            last_error = exc
    raise RuntimeFailure("TLS certificate probe handshake failed") from last_error
