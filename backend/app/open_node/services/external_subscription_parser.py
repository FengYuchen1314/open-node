"""Bounded, data-only parsing of the external Clash/Mihomo ``proxies`` list.

This is an input trust boundary, not a client-format compatibility converter.
Whole-client rules/providers/scripts are never copied. YAML references (including
merge keys), non-core tags and ambiguous mappings are deliberately not accepted.
Unsupported nodes remain visible, but none of their configuration is importable.
"""

import base64
import binascii
import ipaddress
import math
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_PROXIES = 1000
MAX_YAML_DEPTH = 16
MAX_YAML_NODES = 100_000
MAX_SCALAR_LENGTH = 16_384
MAX_NAME_LENGTH = 160

_ERROR = "The external subscription is not a valid YAML proxy document."
_UNSUPPORTED_PROTOCOL = "This protocol is not supported for external subscription import."
_UNSUPPORTED_OPTIONS = "This node uses options that cannot be safely imported."
_HYSTERIA_V1 = "Hysteria v1 cannot be imported as Hysteria2."
_TAG = "tag:yaml.org,2002:"
_TAGS = {_TAG + kind for kind in ("str", "bool", "int", "float", "null", "map", "seq")}
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z", re.ASCII)
_PROTOCOL = re.compile(r"[a-z][a-z0-9-]{0,63}\Z", re.ASCII)
_INTEGER = re.compile(r"[-+]?(?:0|[1-9][0-9]*)\Z", re.ASCII)
_FLOAT = re.compile(
    r"[-+]?(?:(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
    r"|[0-9]+[eE][-+]?[0-9]+)\Z", re.ASCII
)
_COMMON = {"name", "type", "server", "port", "udp"}
_TLS = {"tls", "servername", "sni", "alpn", "skip-cert-verify", "client-fingerprint"}
# Intersect the supported fields with each Mihomo v1.19.30 Option struct.
# BasicOption adds no TLS fields. Native TLS protocols accept only the redundant
# tls:true annotation; tls:false cannot override their required secure transport.
_TLS_OPTIONS = {
    "vless": _TLS - {"sni"},
    "vmess": _TLS - {"sni"},
    "trojan": _TLS - {"servername"},
    "anytls": _TLS - {"servername"},
    "hysteria2": _TLS - {"servername", "client-fingerprint"},
    "http": {"tls", "sni", "skip-cert-verify"},
    "socks": {"tls", "skip-cert-verify"},
}
# adapter/parser.go does not seed UDP. These constructors pass the zero-valued
# Option.UDP bool to Base; HTTP has no packet support, while Hysteria2 explicitly
# sets Base.UDP=true (and UDPDisabled=false). Snell v6 needs an explicit flag
# because the pinned Mihomo constructor cannot establish any defaults for v6.
_UDP_DEFAULTS = {
    "vless": False, "vmess": False, "trojan": False, "shadowsocks": False,
    "anytls": False, "snell": False, "mieru": False, "socks": False,
    "http": False, "hysteria2": True,
}
_TRANSPORT = {
    "network", "ws-opts", "grpc-opts", "h2-opts", "http-opts", "http-upgrade-opts", "xhttp-opts"
}
_FIELDS = {
    "vless": {"uuid", "flow", "encryption", "reality-opts"},
    "vmess": {"uuid", "alterId", "cipher"},
    "trojan": {"password"},
    "shadowsocks": {"password", "cipher", "plugin", "plugin-opts"},
    "hysteria2": {"password", "obfs", "obfs-password", "up", "down", "ports", "hop-interval"},
    "anytls": {
        "password", "idle-session-check-interval", "idle-session-timeout", "min-idle-session"
    },
    "snell": {"psk", "version", "mode", "obfs-opts"},
    "mieru": {"username", "password", "transport"},
    "http": {"username", "password"},
    "socks": {"username", "password"},
}
_TLS_PROTOCOLS = set(_TLS_OPTIONS)
_TLS_REQUIRED = {"trojan", "hysteria2", "anytls"}
_WRAPPED = {"vless", "vmess", "trojan"}
_FINGERPRINTS = {
    "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq", "random",
    "randomized", "chrome_psk", "chrome_psk_shuffle", "chrome_padding_psk_shuffle",
    "chrome_pq", "chrome_pq_psk",
}
_SS_CIPHERS = {
    "aes-128-gcm", "aes-192-gcm", "aes-256-gcm", "chacha20-ietf-poly1305",
    "xchacha20-ietf-poly1305", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
}


class ExternalSubscriptionParseError(ValueError):
    """A safe error whose public message cannot contain provider input."""

    def __init__(self) -> None:
        super().__init__(_ERROR)


@dataclass(frozen=True, slots=True)
class ParsedExternalNode:
    name: str
    protocol: str
    config: dict[str, Any] | None = field(repr=False)
    reason: str | None = None


def _reject() -> None:
    raise ExternalSubscriptionParseError()


class _SubscriptionLoader(yaml.SafeLoader):
    # A separate resolver table avoids changing any other application YAML loader.
    yaml_implicit_resolvers: dict = {}

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0
        self._nodes = 0

    def compose_node(self, parent, index):
        event = self.peek_event()
        # Zero-reference policy also prevents exponential expansion and cycles.
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
            _reject()
        self._nodes += 1
        self._depth += 1
        if self._nodes > MAX_YAML_NODES or self._depth > MAX_YAML_DEPTH:
            _reject()
        try:
            node = super().compose_node(parent, index)
        finally:
            self._depth -= 1
        if node.tag not in _TAGS:
            _reject()
        if isinstance(node, ScalarNode) and len(node.value) > MAX_SCALAR_LENGTH:
            _reject()
        return node

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            _reject()
        result = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.tag != _TAG + "str":
                _reject()
            key = self.construct_object(key_node, deep=True)
            if key == "<<" or key in result:
                _reject()
            result[key] = self.construct_object(value_node, deep=True)
        return result


def _yaml_int(loader, node):
    value = loader.construct_scalar(node)
    if len(value) > 32 or not _INTEGER.fullmatch(value):
        _reject()
    return int(value, 10)


def _yaml_float(loader, node):
    value = loader.construct_scalar(node)
    if len(value) > 64 or not _FLOAT.fullmatch(value):
        _reject()
    number = float(value)
    if not math.isfinite(number):
        _reject()
    return number


def _yaml_bool(loader, node):
    value = loader.construct_scalar(node)
    if value.lower() not in {"true", "false"}:
        _reject()
    return value.lower() == "true"


def _yaml_null(loader, node):
    if loader.construct_scalar(node) not in {"", "~", "null", "Null", "NULL"}:
        _reject()
    return None


_SubscriptionLoader.add_implicit_resolver(
    _TAG + "bool", re.compile(r"(?:true|True|TRUE|false|False|FALSE)\Z"), list("tTfF")
)
_SubscriptionLoader.add_implicit_resolver(
    _TAG + "null", re.compile(r"(?:~|null|Null|NULL|)\Z"), ["~", "n", "N", ""]
)
_SubscriptionLoader.add_implicit_resolver(_TAG + "int", _INTEGER, list("-+0123456789"))
_SubscriptionLoader.add_implicit_resolver(_TAG + "float", _FLOAT, list("-+.0123456789"))
_SubscriptionLoader.add_constructor(_TAG + "int", _yaml_int)
_SubscriptionLoader.add_constructor(_TAG + "float", _yaml_float)
_SubscriptionLoader.add_constructor(_TAG + "bool", _yaml_bool)
_SubscriptionLoader.add_constructor(_TAG + "null", _yaml_null)


def _text(value: Any, *, limit: int = 4096, empty: bool = False) -> str:
    if type(value) is not str or len(value) > limit or (not value and not empty):
        _reject()
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in value):
        _reject()
    return value


def _integer(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()
    return value


def _boolean(value: Any) -> None:
    if type(value) is not bool:
        _reject()


def _mapping(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _reject()
    return value


def _list(value: Any, *, maximum: int, empty: bool = True) -> list:
    if type(value) is not list or len(value) > maximum or (not value and not empty):
        _reject()
    return value


def _host(value: Any) -> str:
    host = _text(value, limit=253)
    if (
        host != host.strip() or any(char in host for char in "/\\@?#%[]")
        or any(unicodedata.category(char) == "Cf" for char in host)
    ):
        _reject()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Validate syntax only; importing a proxy never resolves or connects to it.
        try:
            encoded = host.encode("idna").decode("ascii").rstrip(".")
        except UnicodeError:
            _reject()
        if not encoded or len(encoded) > 253:
            _reject()
        if any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in encoded.split(".")
        ):
            _reject()
        if all(char.isdigit() or char == "." for char in encoded):
            _reject()
    return host


def _authority(value: Any) -> None:
    authority = _text(value, limit=320)
    if any(char in authority for char in "/\\@?#%"):
        _reject()
    try:
        parsed = urlsplit("//" + authority)
        port = parsed.port
    except ValueError:
        _reject()
    if (
        not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.query or parsed.fragment
        or authority != authority.strip() or authority.endswith(":")
    ):
        _reject()
    _host(parsed.hostname)
    if port is not None:
        _integer(port, 1, 65535)


def _path(value: Any) -> None:
    path = _text(value)
    if not path.startswith("/") or path.startswith("//") or "#" in path or "\\" in path:
        _reject()
    if any(char.isspace() for char in path):
        _reject()


def _headers(value: Any, *, lists: bool = False) -> None:
    headers = _mapping(value)
    if len(headers) > 32:
        _reject()
    names: set[str] = set()
    for key, item in headers.items():
        _text(key, limit=128)
        if not _TOKEN.fullmatch(key) or key.lower() in names:
            _reject()
        names.add(key.lower())
        values = _list(item, maximum=16, empty=False) if lists else [item]
        for entry in values:
            _text(entry, empty=True)
            if key.lower() == "host":
                _authority(entry)


def _uuid(value: Any) -> None:
    text = _text(value, limit=36)
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", text):
        _reject()
    try:
        UUID(text)
    except ValueError:
        _reject()


def _base64_key(value: Any, length: int, *, urlsafe: bool = False) -> None:
    key = _text(value, limit=128)
    alphabet = r"[A-Za-z0-9_-]+={0,2}" if urlsafe else r"[A-Za-z0-9+/]+={0,2}"
    if not re.fullmatch(alphabet, key):
        _reject()
    try:
        raw = base64.b64decode(
            key + "=" * (-len(key) % 4), altchars=b"-_" if urlsafe else None, validate=True
        )
    except (ValueError, binascii.Error):
        _reject()
    if len(raw) != length:
        _reject()


class _Validator:
    def __init__(self) -> None:
        self.unsupported = False

    def fields(self, value: Any, allowed: set[str]) -> dict[str, Any]:
        mapping = _mapping(value)
        if mapping.keys() - allowed:
            self.unsupported = True
        return mapping

    def choice(self, value: Any, choices: set[str], *, empty: bool = False) -> str:
        text = _text(value, limit=128, empty=empty)
        if text not in choices:
            self.unsupported = True
        return text


def _validate_tls(proxy: dict, kind: str, check: _Validator) -> None:
    allowed = _TLS_OPTIONS[kind]
    for key in ("tls", "skip-cert-verify"):
        if key in proxy and key in allowed:
            _boolean(proxy[key])
    for key in ("servername", "sni"):
        if key in proxy and key in allowed:
            _host(proxy[key])
    if "alpn" in proxy and "alpn" in allowed:
        values = _list(proxy["alpn"], maximum=16, empty=False)
        for value in values:
            text = _text(value, limit=255)
            if not text.isascii() or any(char.isspace() or char == "," for char in text):
                _reject()
        if len(set(values)) != len(values):
            _reject()
    if "client-fingerprint" in proxy and "client-fingerprint" in allowed:
        check.choice(proxy["client-fingerprint"], _FINGERPRINTS)
    enabled = proxy.get("tls", kind in _TLS_REQUIRED) or "reality-opts" in proxy
    if kind in _TLS_REQUIRED and proxy.get("tls") is False:
        check.unsupported = True
    if not enabled and proxy.keys() & (allowed - {"tls"}):
        check.unsupported = True


def _validate_transport(proxy: dict, kind: str, check: _Validator) -> str:
    transport = _text(proxy.get("network", "tcp"), limit=32)
    transport = {"raw": "tcp", "http-upgrade": "httpupgrade"}.get(transport, transport)
    if transport not in {"tcp", "ws", "grpc", "http", "h2", "httpupgrade", "xhttp"}:
        check.unsupported = True
    if kind not in _WRAPPED and transport != "tcp":
        check.unsupported = True
    if kind == "trojan" and transport in {"http", "h2"}:
        check.unsupported = True
    schemas = {
        "ws-opts": {"path", "headers", "max-early-data", "early-data-header-name",
                    "v2ray-http-upgrade"},
        "grpc-opts": {"grpc-service-name"},
        "h2-opts": {"host", "path"},
        "http-opts": {"method", "path", "headers"},
        "http-upgrade-opts": {"host", "path", "headers"},
        "xhttp-opts": {"host", "path", "mode"},
    }
    expected = {
        "ws": "ws-opts", "grpc": "grpc-opts", "h2": "h2-opts", "http": "http-opts",
        "httpupgrade": "http-upgrade-opts", "xhttp": "xhttp-opts",
    }.get(transport)
    for key, allowed in schemas.items():
        if key not in proxy:
            continue
        options = check.fields(proxy[key], allowed)
        if key != expected:
            check.unsupported = True
        if "path" in options:
            paths = (
                _list(options["path"], maximum=32, empty=False)
                if key == "http-opts" else [options["path"]]
            )
            for path in paths:
                _path(path)
        if "headers" in options:
            _headers(options["headers"], lists=key == "http-opts")
        if "host" in options:
            hosts = (
                _list(options["host"], maximum=16, empty=False)
                if key == "h2-opts" else [options["host"]]
            )
            for host in hosts:
                _authority(host)
            if key == "http-upgrade-opts":
                for name, value in options.get("headers", {}).items():
                    if name.lower() == "host" and value != options["host"]:
                        _reject()
        if "method" in options:
            method = _text(options["method"], limit=32)
            if not _TOKEN.fullmatch(method):
                _reject()
        if "grpc-service-name" in options:
            _text(options["grpc-service-name"], limit=1024, empty=True)
        if "max-early-data" in options:
            _integer(options["max-early-data"], 0, 65535)
        if "early-data-header-name" in options:
            name = _text(options["early-data-header-name"], limit=128)
            if not _TOKEN.fullmatch(name):
                _reject()
        if "v2ray-http-upgrade" in options:
            _boolean(options["v2ray-http-upgrade"])
            if options["v2ray-http-upgrade"] and options.get("max-early-data", 0):
                # upgrade_options cannot preserve WS early-data negotiation.
                check.unsupported = True
        if "mode" in options:
            check.choice(options["mode"], {"auto", "packet-up", "stream-up", "stream-one"})
    return transport


def _validate_vless(proxy: dict, transport: str, check: _Validator) -> None:
    if "encryption" in proxy:
        check.choice(proxy["encryption"], {"", "none"}, empty=True)
    if "flow" in proxy:
        flow = check.choice(proxy["flow"], {"", "xtls-rprx-vision"}, empty=True)
        if flow and (transport != "tcp" or not (proxy.get("tls") or proxy.get("reality-opts"))):
            check.unsupported = True
    if "reality-opts" not in proxy:
        return
    options = check.fields(proxy["reality-opts"], {"public-key", "short-id"})
    _base64_key(options.get("public-key"), 32, urlsafe=True)
    if "short-id" in options:
        short_id = _text(options["short-id"], limit=16, empty=True)
        if len(short_id) % 2 or not re.fullmatch(r"[0-9A-Fa-f]*", short_id):
            _reject()
    if proxy.get("tls") is False:
        _reject()


def _validate_shadowsocks(proxy: dict, check: _Validator) -> None:
    cipher = check.choice(proxy.get("cipher"), _SS_CIPHERS)
    if cipher.startswith("2022-") and cipher in _SS_CIPHERS:
        keys = proxy["password"].split(":")
        if len(keys) > 8:
            _reject()
        if cipher == "2022-blake3-chacha20-poly1305" and len(keys) != 1:
            check.unsupported = True
        for key in keys:
            _base64_key(key, 16 if cipher == "2022-blake3-aes-128-gcm" else 32)
    if "plugin" not in proxy:
        if "plugin-opts" in proxy:
            check.unsupported = True
        return
    plugin = check.choice(proxy["plugin"], {"obfs", "v2ray-plugin"})
    allowed = {"mode", "host"} if plugin == "obfs" else {"mode", "host", "path", "tls", "mux"}
    options = check.fields(proxy.get("plugin-opts", {}), allowed)
    if "mode" in options:
        check.choice(options["mode"], {"http", "tls"} if plugin == "obfs" else {"websocket"})
    elif plugin == "obfs":
        _reject()
    if "host" in options:
        _authority(options["host"])
    if "path" in options:
        _path(options["path"])
    for key in ("tls", "mux"):
        if key in options:
            _boolean(options[key])


def _validate_hysteria2(proxy: dict, check: _Validator) -> None:
    if "obfs" in proxy:
        check.choice(proxy["obfs"], {"salamander"})
        _text(proxy.get("obfs-password"))
    elif "obfs-password" in proxy:
        _text(proxy["obfs-password"])
        check.unsupported = True
    for key in ("up", "down"):
        if key in proxy:
            value = proxy[key]
            if type(value) is str:
                # Native unit strings cannot pass the existing integer-Mbps converters.
                text = _text(value, limit=32)
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?\s*(?:[KMG]bps|[KMG]Bps|Mbps)", text):
                    _reject()
                check.unsupported = True
            else:
                _integer(value, 0, 1_000_000)
    if "hop-interval" in proxy:
        _integer(proxy["hop-interval"], 1, 86400)
        if "ports" not in proxy:
            check.unsupported = True
    if "ports" in proxy:
        ports = _text(proxy["ports"], limit=2048).split(",")
        if len(ports) > 128:
            _reject()
        for part in ports:
            if not re.fullmatch(r"[0-9]{1,5}(?:-[0-9]{1,5})?", part):
                _reject()
            values = [int(value) for value in part.split("-")]
            for value in values:
                _integer(value, 1, 65535)
            if values[0] > values[-1]:
                _reject()


def _validate_snell(proxy: dict, check: _Validator) -> None:
    # Mihomo v1.19.30 defaults to v1; the managed-output converter defaults to v4.
    # Never infer the provider's protocol version from that output-only default.
    version = _integer(proxy["version"], 0, 6) if "version" in proxy else None
    if version not in {4, 5, 6}:
        check.unsupported = True
    if "mode" in proxy:
        check.choice(proxy["mode"], {"default", "unshaped"})
        if version != 6:
            check.unsupported = True
    if "obfs-opts" in proxy:
        options = check.fields(proxy["obfs-opts"], {"mode", "host"})
        mode = check.choice(options.get("mode", "none"), {"none", "http", "tls"})
        if "host" in options:
            _authority(options["host"])
            if mode == "none":
                check.unsupported = True
        if version == 6 and (mode != "none" or "host" in options):
            check.unsupported = True


def _validated_node(proxy: Any) -> ParsedExternalNode:
    proxy = _mapping(proxy)
    raw_name = _text(proxy.get("name"), limit=MAX_SCALAR_LENGTH)
    if any(unicodedata.category(char) == "Cf" for char in raw_name):
        _reject()
    name = unicodedata.normalize("NFC", raw_name).strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        _reject()
    kind = _text(proxy.get("type"), limit=64).strip().lower()
    if not _PROTOCOL.fullmatch(kind):
        _reject()
    kind = {"ss": "shadowsocks", "socks5": "socks", "hy2": "hysteria2"}.get(kind, kind)
    # Check supplied common fields even for unavailable/unknown protocols.
    if "server" in proxy:
        _host(proxy["server"])
    if "port" in proxy:
        _integer(proxy["port"], 1, 65535)
    if "udp" in proxy:
        _boolean(proxy["udp"])
    if kind in {"hysteria", "hysteria1", "hy1"}:
        return ParsedExternalNode(name, kind, None, _HYSTERIA_V1)
    if kind not in _FIELDS:
        return ParsedExternalNode(name, kind, None, _UNSUPPORTED_PROTOCOL)
    _host(proxy.get("server"))
    _integer(proxy.get("port"), 1, 65535)
    check = _Validator()
    allowed = _COMMON | _FIELDS[kind] | {"network"}
    if kind in _TLS_PROTOCOLS:
        allowed |= _TLS_OPTIONS[kind]
    if kind in _WRAPPED:
        allowed |= _TRANSPORT
    check.fields(proxy, allowed)
    udp = proxy.get("udp", _UDP_DEFAULTS[kind])
    if kind in {"http", "hysteria2"} and udp is not _UDP_DEFAULTS[kind]:
        check.unsupported = True
    if kind == "snell" and proxy.get("version") == 6 and "udp" not in proxy:
        check.unsupported = True
    transport = _validate_transport(proxy, kind, check)
    if kind in _TLS_PROTOCOLS:
        _validate_tls(proxy, kind, check)
    if kind in {"vless", "vmess"}:
        _uuid(proxy.get("uuid"))
    if kind in {"trojan", "shadowsocks", "hysteria2", "anytls", "mieru"}:
        _text(proxy.get("password"))
    if kind == "vless":
        _validate_vless(proxy, transport, check)
    elif kind == "vmess":
        if "alterId" in proxy:
            _integer(proxy["alterId"], 0, 65535)
        if "cipher" in proxy:
            check.choice(proxy["cipher"], {
                "auto", "aes-128-gcm", "chacha20-ietf-poly1305", "none", "zero"
            })
    elif kind == "shadowsocks":
        _validate_shadowsocks(proxy, check)
    elif kind == "hysteria2":
        _validate_hysteria2(proxy, check)
    elif kind == "anytls":
        for key in ("idle-session-check-interval", "idle-session-timeout", "min-idle-session"):
            if key in proxy:
                _integer(proxy[key], 0, 86400 if key != "min-idle-session" else 10000)
    elif kind == "snell":
        _text(proxy.get("psk"))
        _validate_snell(proxy, check)
    elif kind == "mieru":
        _text(proxy.get("username"), limit=255)
        if "transport" in proxy:
            check.choice(proxy["transport"], {"TCP", "UDP"})
        else:
            # Mihomo requires transport; the output converter's TCP fallback is
            # not permission to repair an incomplete external configuration.
            check.unsupported = True
    elif kind in {"http", "socks"}:
        if "username" in proxy:
            username = _text(proxy["username"], limit=255)
            if kind == "http" and ":" in username:
                _reject()
            if kind == "socks" and len(username.encode("utf-8")) > 255:
                _reject()
        if "password" in proxy:
            password = _text(
                proxy["password"], limit=255 if kind == "socks" else 4096, empty=True
            )
            if kind == "socks" and len(password.encode("utf-8")) > 255:
                _reject()
            if "username" not in proxy:
                _reject()
        if kind == "http" and "username" in proxy and not proxy.get("password"):
            # Mihomo sends Basic auth only when BOTH fields are nonempty; other
            # formats can authenticate with just a username and an empty password.
            check.unsupported = True
    if check.unsupported:
        return ParsedExternalNode(name, kind, None, _UNSUPPORTED_OPTIONS)
    config = deepcopy(proxy)
    config["name"] = name
    config["type"] = {"shadowsocks": "ss", "socks": "socks5"}.get(kind, kind)
    config["udp"] = udp
    if "network" in config:
        config["network"] = transport
    return ParsedExternalNode(name, kind, config)


def parse_external_subscription(body: bytes) -> list[ParsedExternalNode]:
    """Parse one bounded YAML document, or raise an input-free public error.

    An explicit ``proxies: []`` is a valid empty preview. Missing/null ``proxies``
    is not. Names are NFC-normalized and trimmed before duplicate detection.
    Credentials are never trimmed, normalized, logged or exposed in ``repr``.
    Verified source UDP defaults are explicit, so output defaults cannot alter them.
    """
    try:
        if type(body) is not bytes or not body or len(body) > MAX_BODY_BYTES:
            _reject()
        loader = _SubscriptionLoader(body.decode("utf-8-sig"))
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
        document = _mapping(document)
        proxies = _list(document.get("proxies"), maximum=MAX_PROXIES)
        result: list[ParsedExternalNode] = []
        names: set[str] = set()
        for proxy in proxies:
            node = _validated_node(proxy)
            if node.name in names:
                _reject()
            names.add(node.name)
            result.append(node)
        return result
    except (ExternalSubscriptionParseError, yaml.YAMLError, UnicodeError, RecursionError):
        # Raise outside the handler: even exception.__context__ must not retain a
        # scanner error containing a credential-bearing line from the document.
        pass
    raise ExternalSubscriptionParseError() from None
