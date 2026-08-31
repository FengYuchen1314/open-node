"""Representative official URI formats, strict decoding and safe preview failures."""

import base64
import json
import traceback
from urllib.parse import quote, urlencode

import pytest
from open_node.services.external_subscription_parser import (
    MAX_BODY_BYTES,
    MAX_PROXIES,
    MAX_SCALAR_LENGTH,
    ExternalSubscriptionParseError,
    parse_external_subscription,
)

UUID = "4ac23fce-f91b-4e5f-a722-d2a85b8c3324"
SECRET = "upstream-secret-NOT-IN-ERRORS"
EDGE = "edge.provider.example:443"
PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


def encoded(value, *, urlsafe=False):
    raw = value.encode() if isinstance(value, str) else value
    return (base64.urlsafe_b64encode if urlsafe else base64.b64encode)(raw).decode()


def node(value):
    return parse_external_subscription(value.encode())[0]


def safe_failure(value):
    body = value.encode() if isinstance(value, str) else value
    with pytest.raises(ExternalSubscriptionParseError) as caught:
        parse_external_subscription(body)
    error = caught.value
    assert error.__context__ is None and error.__cause__ is None
    assert error.args == ("The external subscription is not a valid YAML proxy document.",)
    assert SECRET not in "".join(traceback.format_exception(error))


def vmess(**changes):
    return "vmess://" + encoded(json.dumps({
        "v": "2", "ps": "VMess", "add": "edge.provider.example", "port": "443", "id": UUID,
        "aid": "0", "scy": "auto", "net": "tcp", "type": "none", "tls": "",
        "sni": "", "alpn": "", "host": "", "path": "", "fp": "", "allowInsecure": "0",
        **changes,
    }), urlsafe=True).rstrip("=")


def test_official_native_uri_protocols_have_strict_existing_configs():
    cases = [
        (f"vless://{UUID}@{EDGE}?security=tls#VLESS", "vless", "uuid", UUID),
        (vmess(), "vmess", "uuid", UUID),
        (f"trojan://{SECRET}@{EDGE}#Trojan", "trojan", "password", SECRET),
        (f"ss://{encoded('aes-128-gcm:' + SECRET)}@{EDGE}#SS", "shadowsocks", "password", SECRET),
        (f"hy2://{SECRET}@{EDGE}#HY2", "hysteria2", "password", SECRET),
        (f"anytls://{SECRET}@{EDGE}#AnyTLS", "anytls", "password", SECRET),
        (f"snell://{SECRET}@{EDGE}?version=5#Snell", "snell", "psk", SECRET),
        (f"mieru://alice:{SECRET}@{EDGE}?transport=UDP#Mieru", "mieru", "password", SECRET),
        (f"socks5://alice:{SECRET}@{EDGE}#SOCKS", "socks", "password", SECRET),
        (f"http://alice:{SECRET}@{EDGE}#HTTP", "http", "password", SECRET),
        (f"https://alice:{SECRET}@{EDGE}#HTTPS", "http", "password", SECRET),
    ]
    for value, protocol, key, expected in cases:
        result = node(value)
        assert result.protocol == protocol and result.reason is None
        assert result.config[key] == expected
        assert result.config["server"] == "edge.provider.example"
        assert result.config["port"] == 443
        assert SECRET not in repr(result) and UUID not in repr(result)


def test_uri_transports_reality_tls_and_percent_encoding_are_preserved_once():
    for transport, query, key, expected in [
        ("ws", {"path": "/secret%2Fpath", "host": "front.example"}, "ws-opts",
         {"path": "/secret%2Fpath", "headers": {"Host": "front.example"}}),
        ("grpc", {"serviceName": "service/name"}, "grpc-opts",
         {"grpc-service-name": "service/name"}),
        ("httpupgrade", {"path": "/upgrade", "host": "front.example"}, "http-upgrade-opts",
         {"path": "/upgrade", "host": "front.example"}),
        ("xhttp", {"path": "/x", "mode": "packet-up"}, "xhttp-opts",
         {"path": "/x", "mode": "packet-up"}),
    ]:
        result = node(f"vless://{UUID}@[2001:db8::1]:443?" + urlencode({
            "type": transport, "security": "tls", "sni": "tls.example", **query,
        }) + "#%E4%B8%AD%E6%96%87%20%F0%9F%9A%80")
        assert result.reason is None and result.name == "中文 🚀"
        assert result.config[key] == expected
        assert result.config["servername"] == "tls.example"
        assert result.config["server"] == "2001:db8::1"
    reality = node(f"vless://{UUID}@{EDGE}?" + urlencode({
        "security": "reality", "pbk": PUBLIC_KEY, "sid": "aabb", "fp": "chrome",
        "flow": "xtls-rprx-vision", "alpn": "h2,http/1.1",
    }))
    assert reality.config["reality-opts"] == {"public-key": PUBLIC_KEY, "short-id": "aabb"}
    assert reality.config["flow"] == "xtls-rprx-vision"
    assert reality.config["alpn"] == ["h2", "http/1.1"]
    password = " +:%41@中文 "
    assert node(f"trojan://{quote(password, safe='')}@{EDGE}").config["password"] == password


def test_ss_socks_encodings_and_plugin_preserve_opaque_credentials():
    password = "a:%41+中文"
    auth = "aes-128-gcm:" + password
    for uri in (
        f"ss://{encoded(auth, urlsafe=True).rstrip('=')}@{EDGE}",
        f"ss://{quote(auth, safe='')}@{EDGE}",
        "ss://" + encoded(auth + "@" + EDGE),
    ):
        result = node(uri)
        assert result.reason is None and result.config["password"] == password
    socks = node(f"socks://{encoded('alice:' + password)}@{EDGE}")
    assert socks.config["username"] == "alice" and socks.config["password"] == password
    result = node(f"ss://{encoded('aes-128-gcm:' + SECRET)}@{EDGE}?" + urlencode({
        "plugin": "obfs-local;obfs=http;obfs-host=front.example",
    }))
    assert result.config["plugin"] == "obfs"
    assert result.config["plugin-opts"] == {"mode": "http", "host": "front.example"}


def test_vmess_json_fields_and_duplicate_keys_cannot_silently_change_security():
    result = node(vmess(
        net="grpc", path="svc", tls="tls", sni="front.example", allowInsecure=False,
    ))
    assert result.config["grpc-opts"] == {"grpc-service-name": "svc"}
    assert result.config["skip-cert-verify"] is False
    for change in ({"port": True}, {"aid": "no"}, {"tls": None}, {"allowInsecure": "maybe"}):
        safe_failure(vmess(**change))
    safe_failure("vmess://" + encoded('{"id":"' + SECRET + '","id":"other"}'))
    assert node(vmess(extra_security=SECRET)).config is None


def test_valid_unsupported_options_and_protocols_remain_visible_not_silently_dropped():
    values = [
        f"vless://{UUID}@{EDGE}?security=tls&unknown-security=required#Unknown-option",
        f"mieru://alice:{SECRET}@{EDGE}?multiplexing=MULTIPLEXING_HIGH#Mieru-options",
        f"snell://{SECRET}@{EDGE}?version=1#Old-Snell",
        f"hysteria://{SECRET}@{EDGE}#Hysteria-v1",
        f"tuic://{UUID}:{SECRET}@{EDGE}#TUIC",
    ]
    result = parse_external_subscription("\n".join(values).encode())
    assert len(result) == len(values)
    assert all(item.config is None and item.reason for item in result)
    assert "Hysteria v1" in result[3].reason


def test_base64_is_single_layer_bounded_and_canonical_with_no_ignored_uri_lines():
    original = f"vless://{UUID}@{EDGE}?security=tls#节点\r\n".encode()
    for urlsafe in (False, True):
        for padded in (False, True):
            value = encoded(original, urlsafe=urlsafe)
            if not padded:
                value = value.rstrip("=")
            wrapped = "\r\n".join(value[offset:offset + 64] for offset in range(0, len(value), 64))
            assert (
                parse_external_subscription(wrapped.encode())
                == parse_external_subscription(original)
            )
    assert parse_external_subscription(encoded(b"proxies: []").encode()) == []
    for value in (
        encoded(encoded(original)), encoded(original) + "!!!", "Zh==", "Zg=", "Zg===",
        f"vless://{UUID}@{EDGE}?sni=one&sni=two", f"trojan://{SECRET}@{EDGE}?insecure=maybe",
        f"trojan://{SECRET}%ZZ@{EDGE}", f"trojan://{SECRET}%0A@{EDGE}",
        f"vless://{UUID}@{EDGE}?sni=one&peer=two", original + b"NOT-A-NODE",
        f"trojan://{SECRET}@{EDGE}/ignored-path", original + original,
        b"a" * (MAX_BODY_BYTES + 1),
        f"trojan://{'x' * MAX_SCALAR_LENGTH}@{EDGE}",
        "\n".join(f"vless://{UUID}@{EDGE}#n{i}" for i in range(MAX_PROXIES + 1)),
    ):
        safe_failure(value)


def test_yaml_keeps_its_existing_empty_duplicate_and_alias_guards():
    assert parse_external_subscription(b"proxies: []") == []
    for value in (
        b"proxies: []\nproxies: []", b"proxies: &nodes []", b"proxies: null",
        b"proxies: []\n---\nproxies: []", b"proxies: []\nmetadata: !!binary c2VjcmV0",
    ):
        safe_failure(value)
