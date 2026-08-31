import base64
import json
import traceback
from copy import deepcopy
from dataclasses import fields

import pytest
import yaml
from open_node.services.external_subscription_parser import (
    MAX_BODY_BYTES,
    MAX_NAME_LENGTH,
    MAX_PROXIES,
    MAX_SCALAR_LENGTH,
    MAX_YAML_DEPTH,
    MAX_YAML_NODES,
    ExternalSubscriptionParseError,
    ParsedExternalNode,
    parse_external_subscription,
)

UUID = "4ac23fce-f91b-4e5f-a722-d2a85b8c3324"
SECRET = "upstream-secret-DO-NOT-ECHO"
PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


def proxy(kind="vless", **extra):
    value = {
        "name": "Upstream", "type": kind, "server": "edge.provider.example", "port": 443,
        "udp": kind in {"hysteria2", "hy2"},
    }
    if kind in {"vless", "vmess"}:
        value["uuid"] = UUID
    elif kind in {"trojan", "ss", "shadowsocks", "hysteria2", "hy2", "anytls", "mieru"}:
        value["password"] = SECRET
    elif kind == "snell":
        value["psk"] = SECRET
        value["version"] = 4
    if kind in {"ss", "shadowsocks"}:
        value["cipher"] = "aes-128-gcm"
    if kind == "mieru":
        value["username"] = "upstream-user"
        value["transport"] = "TCP"
    return {**value, **extra}


def document(*nodes, **root):
    return yaml.safe_dump({**root, "proxies": list(nodes)}, allow_unicode=True).encode()


def parsed(value):
    return parse_external_subscription(document(value))[0]


def assert_safe_error(body):
    with pytest.raises(ExternalSubscriptionParseError) as caught:
        parse_external_subscription(body)
    error = caught.value
    assert str(error) == "The external subscription is not a valid YAML proxy document."
    assert error.args == (str(error),)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert SECRET not in repr(error)
    assert SECRET not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize(
    ("kind", "canonical", "clash_type"),
    [
        ("vless", "vless", "vless"), ("vmess", "vmess", "vmess"),
        ("trojan", "trojan", "trojan"), ("ss", "shadowsocks", "ss"),
        ("shadowsocks", "shadowsocks", "ss"), ("hy2", "hysteria2", "hysteria2"),
        ("hysteria2", "hysteria2", "hysteria2"), ("anytls", "anytls", "anytls"),
        ("snell", "snell", "snell"), ("mieru", "mieru", "mieru"),
        ("http", "http", "http"), ("socks5", "socks", "socks5"),
        ("socks", "socks", "socks5"),
    ],
)
def test_supported_protocols_preserve_trusted_clash_configuration(kind, canonical, clash_type):
    source = proxy(kind, udp=kind != "http")
    result = parsed(source)
    assert result.name == "Upstream" and result.protocol == canonical and result.reason is None
    assert result.config == {**source, "type": clash_type}
    assert isinstance(result, ParsedExternalNode)
    assert not next(item for item in fields(result) if item.name == "config").repr


def test_nfc_trim_and_credential_preservation_and_no_secret_repr():
    password = "  e\u0301:" + SECRET + "  "
    source = proxy("trojan", name="  Cafe\u0301  ", password=password)
    result = parsed(source)
    assert result.name == "Caf\u00e9"
    assert result.config == {**source, "name": "Caf\u00e9"}
    assert result.config["password"] == password
    assert SECRET not in repr(result) and password not in repr(result)


def test_returned_nested_configs_are_independent_and_do_not_include_root_sections():
    source = proxy(network="ws", **{"ws-opts": {"path": "/ws", "headers": {"Host": "cdn.test"}}})
    body = document(
        source,
        rules=["MATCH,DIRECT"],
        script={"code": "provider script must never run"},
        **{"proxy-providers": {"another": {"path": "/etc/passwd"}}, "proxy-groups": [
            {"name": "Select", "type": "select", "proxies": ["Upstream"]}
        ]},
    )
    first = parse_external_subscription(body)[0]
    first.config["ws-opts"]["headers"]["Host"] = "modified.test"
    second = parse_external_subscription(body)[0]
    assert second.config == source
    assert set(second.config) == set(source)


@pytest.mark.parametrize("body", [b"proxies: []", b"\xef\xbb\xbfproxies: []\n"])
def test_explicit_empty_list_is_valid_for_confirmed_missing_nodes(body):
    assert parse_external_subscription(body) == []


@pytest.mark.parametrize(
    "body",
    [
        b"", b"   \n# comment", b"<html>provider error</html>", b"{}", b"null", b"[]",
        b"proxy-providers: {}", b"proxies: null", b"proxies: invalid", b"proxies: {}",
        b"proxies: [false]", b"proxies: [[]]", b"proxies: [null]", b"proxies: [\n",
        b"proxies: []\n---\nproxies: []", b"proxies: []\ninvalid: \xff",
        None, "proxies: []", bytearray(b"proxies: []"),
    ],
)
def test_invalid_documents_fail_whole_preview_with_safe_error(body):
    assert_safe_error(body)


@pytest.mark.parametrize(
    "body",
    [
        b"proxies: []\nproxies: []",
        document(proxy()).replace(b"port: 443", b"port: 443\n  port: 8443"),
        document(proxy()).replace(b"type: vless", b"type: vless\n  'type': trojan"),
        b"proxies: []\nmetadata: {a: 1, a: 2}",
        b"proxies: []\nmetadata: {Host: one, Host: two}",
        b"proxies: []\nmetadata: {? [one, two]: value}",
        b"proxies: []\nmetadata: {true: value}",
        b"proxies: []\nmetadata: {42: value}",
        b"proxies: []\nmetadata: {'<<': {foo: bar}}",
        b"proxies: &nodes []",
        b"proxies: []\nmetadata: &a [*a]",
        b"proxies: []\nmetadata: &a [1,2]\nother: [*a,*a,*a]",
        b"proxies: []\nmetadata: *unknown",
        b"proxies: []\nmetadata: !!python/object/apply:builtins.str [secret]",
        b"proxies: []\nmetadata: !custom secret",
        b"proxies: []\nmetadata: !!binary c2VjcmV0",
        b"proxies: []\nmetadata: !!timestamp 2026-08-31",
        b"proxies: []\nmetadata: !!set {one: null}",
        b"proxies: []\nmetadata: !!bool yes",
        b"proxies: []\nmetadata: !!int 0777",
        b"proxies: []\nmetadata: !!float .nan",
        b"proxies: []\nmetadata: !!float .inf",
        b"proxies: []\nmetadata: 1e99999",
        b"proxies: []\nmetadata: !!null secret",
    ],
)
def test_ambiguous_and_unsafe_yaml_is_rejected_even_in_ignored_root_sections(body):
    assert_safe_error(body)


def test_yaml_errors_do_not_retain_secret_in_exception_context_or_traceback():
    assert_safe_error(("proxies: [\n  password: '" + SECRET).encode())
    assert_safe_error(document(proxy(password=SECRET, port="invalid")))


def test_yaml_12_like_scalars_do_not_turn_credentials_or_names_into_booleans_dates():
    body = b"""proxies:
  - name: on
    type: trojan
    server: edge.test
    port: 443
    password: yes
  - name: 2026-08-31
    type: trojan
    server: edge.test
    port: 443
    password: off
"""
    result = parse_external_subscription(body)
    assert [node.name for node in result] == ["on", "2026-08-31"]
    assert [node.config["password"] for node in result] == ["yes", "off"]
    # No global change to PyYAML's existing SafeLoader behavior.
    assert yaml.safe_load("value: yes") == {"value": True}


def test_body_limit_is_exact_inclusive_and_checked_before_yaml_parse():
    prefix = b"proxies: []\n#"
    body = prefix + b"x" * (MAX_BODY_BYTES - len(prefix))
    assert len(body) == MAX_BODY_BYTES
    assert parse_external_subscription(body) == []
    assert_safe_error(body + b"x")


def test_proxy_limit_accepts_1000_but_not_1001():
    nodes = [proxy(name=f"Node {number}") for number in range(MAX_PROXIES)]
    assert len(parse_external_subscription(document(*nodes))) == MAX_PROXIES
    assert_safe_error(document(*nodes, proxy(name="One too many")))


def test_yaml_object_budget_limits_ignored_metadata():
    body = b"proxies: []\nmetadata: [" + b"0," * MAX_YAML_NODES + b"]"
    assert len(body) < MAX_BODY_BYTES
    assert_safe_error(body)


def test_yaml_depth_budget_limits_ignored_metadata():
    body = b"proxies: []\nmetadata: " + b"[" * MAX_YAML_DEPTH + b"0" + b"]" * MAX_YAML_DEPTH
    assert_safe_error(body)


def test_scalar_limit_applies_to_unknown_fields_and_root_sections():
    at_limit = b"proxies: []\nmetadata: '" + b"a" * MAX_SCALAR_LENGTH + b"'"
    assert parse_external_subscription(at_limit) == []
    assert_safe_error(at_limit[:-1] + b"a'")
    assert_safe_error(document(proxy(unknown="x" * (MAX_SCALAR_LENGTH + 1))))


def test_numeric_scalar_resource_limit():
    assert_safe_error(b"proxies: []\nmetadata: " + b"9" * 33)
    assert_safe_error(b"proxies: []\nmetadata: 1." + b"1" * 64)


@pytest.mark.parametrize(
    ("first", "second"), [(" Same ", "Same"), ("Cafe\u0301", "Caf\u00e9"), ("dup", "dup")]
)
def test_duplicate_normalized_names_are_not_silently_replaced(first, second):
    assert_safe_error(document(proxy(name=first), proxy(name=second)))


def test_names_use_nfc_not_nfkc_and_remain_case_sensitive():
    result = parse_external_subscription(
        document(proxy(name="A"), proxy(name="a"), proxy(name="Ａ"))
    )
    assert [node.name for node in result] == ["A", "a", "Ａ"]


def test_name_limit_is_applied_after_nfc_normalization():
    assert parsed(proxy(name="e\u0301" * MAX_NAME_LENGTH)).name == "\u00e9" * MAX_NAME_LENGTH
    assert_safe_error(document(proxy(name="a" * (MAX_NAME_LENGTH + 1))))


@pytest.mark.parametrize(
    "name", [None, 123, True, "", "  ", "a\n", "a\x00", "a\t", "a\u202e", "a\u200b"]
)
def test_invalid_names_reject_whole_preview(name):
    assert_safe_error(document(proxy(), proxy(name=name)))


@pytest.mark.parametrize("port", [None, True, False, "443", "0443", 0, -1, 65536, 1.1])
def test_ports_are_in_range_exact_integers(port):
    assert_safe_error(document(proxy(port=port)))


@pytest.mark.parametrize("port", [1, 443, 65535])
def test_valid_port_boundaries(port):
    assert parsed(proxy(port=port)).config["port"] == port


@pytest.mark.parametrize(
    "host",
    [
        "", "https://edge.test", "user@edge.test", "edge.test/path", "edge.test?secret",
        "edge.test#secret", "edge.test:443", " edge.test", "edge.test ", "edge.test\n",
        "[::1]", "fe80::1%eth0", "1.2.3.999", ".", "_bad.test", "bad..test",
        "-bad.test", "bad-.test", "a" * 64 + ".test", "a/../b", "edge\u200b.test", None, 42,
    ],
)
def test_server_is_a_host_not_url_authority_or_path(host):
    assert_safe_error(document(proxy(server=host)))


@pytest.mark.parametrize(
    "host", ["edge.test", "edge.test.", "192.0.2.1", "2001:db8::1", "localhost"]
)
def test_import_does_not_resolve_proxy_server_hosts(host, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        pytest.fail("Parsing must not resolve or connect to a proxy")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    assert parsed(proxy(server=host)).config["server"] == host


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", [], {}])
@pytest.mark.parametrize("key", ["udp", "tls", "skip-cert-verify"])
def test_boolean_fields_are_not_coerced(key, value):
    assert_safe_error(document(proxy(**{key: value})))


@pytest.mark.parametrize("value", [None, "", "not-a-uuid", True, " " + UUID, "{" + UUID + "}"])
def test_uuid_credentials_require_exact_uuid_text(value):
    assert_safe_error(document(proxy(uuid=value)))


def test_uppercase_uuid_is_preserved_not_reissued_or_rewritten():
    assert parsed(proxy(uuid=UUID.upper())).config["uuid"] == UUID.upper()


@pytest.mark.parametrize("kind", ["trojan", "ss", "hysteria2", "anytls", "mieru"])
@pytest.mark.parametrize("password", [None, "", True, 123, "secret\nline", "a" * 4097])
def test_required_password_types_lengths_and_controls(kind, password):
    assert_safe_error(document(proxy(kind, password=password)))


@pytest.mark.parametrize("kind", ["hysteria", "hysteria1", "hy1"])
def test_hysteria_v1_is_never_passed_to_v2_output_alias(kind):
    result = parsed(proxy(kind, password=SECRET, auth=SECRET, up=100))
    assert result.protocol == kind
    assert result.config is None
    assert result.reason == "Hysteria v1 cannot be imported as Hysteria2."
    assert SECRET not in repr(result)


@pytest.mark.parametrize("kind", ["wireguard", "tuic", "ssr", "direct", "unknown-protocol"])
def test_unknown_protocols_remain_visible_without_importable_config(kind):
    result = parsed(proxy(kind, private=SECRET))
    assert result.protocol == kind and result.config is None
    assert result.reason == "This protocol is not supported for external subscription import."
    assert SECRET not in repr(result)


@pytest.mark.parametrize("kind", [None, 123, "", "a" * 65, "trojan/password", "https://secret.test"])
def test_protocol_metadata_has_a_bounded_safe_shape(kind):
    assert_safe_error(document(proxy(type=kind)))


@pytest.mark.parametrize(
    "extra",
    [
        {"dialer-proxy": "managed node"}, {"proxy-provider": "other-source"},
        {"interface-name": "eth0"}, {"routing-mark": 123}, {"tfo": True}, {"mptcp": True},
        {"ip-version": "ipv4"}, {"smux": {"enabled": True}}, {"packet-encoding": "xudp"},
        {"certificate": "/etc/passwd"}, {"private-key": "/etc/key"},
        {"ca": "/etc/ca"}, {"ca-str": "certificate data"}, {"file": "/tmp/local"},
        {"script": "not executable"}, {"rules": ["MATCH,DIRECT"]},
        {"proxy-providers": {"other": {"path": "/etc/passwd"}}},
        {"plugin": "/usr/bin/not-allowed"}, {"password": SECRET}, {"arbitrary": SECRET},
    ],
)
def test_unknown_and_cross_source_or_local_options_are_not_silently_dropped(extra):
    result = parsed(proxy(**extra))
    assert result.config is None
    assert result.reason == "This node uses options that cannot be safely imported."
    assert SECRET not in repr(result)


def test_unknown_options_do_not_make_invalid_recognized_fields_acceptable():
    assert_safe_error(document(proxy(**{"dialer-proxy": "other", "port": 0})))
    assert_safe_error(document(proxy(**{"dialer-proxy": "other", "uuid": SECRET})))


@pytest.mark.parametrize(
    ("network", "options"),
    [
        ("tcp", {}), ("raw", {}),
        ("ws", {"ws-opts": {"path": "/ws?token=" + SECRET, "headers": {
            "Host": "cdn.test:8443", "Authorization": "Bearer " + SECRET
        }, "max-early-data": 2048, "early-data-header-name": "Sec-WebSocket-Protocol"}}),
        ("ws", {"ws-opts": {"path": "/up", "v2ray-http-upgrade": True}}),
        ("grpc", {"grpc-opts": {"grpc-service-name": "proxy/" + SECRET}}),
        ("h2", {"h2-opts": {"host": ["cdn.test"], "path": "/h2"}}),
        ("http", {"http-opts": {"method": "GET", "path": ["/first", "/second"],
                                  "headers": {"Host": ["cdn.test"], "User-Agent": ["agent"]}}}),
        ("httpupgrade", {"http-upgrade-opts": {"host": "cdn.test", "path": "/up"}}),
        ("http-upgrade", {"http-upgrade-opts": {"host": "cdn.test", "path": "/up"}}),
        ("xhttp", {"xhttp-opts": {"host": "cdn.test", "path": "/xhttp", "mode": "auto"}}),
    ],
)
def test_supported_transports_retain_credentials_and_headers(network, options):
    source = proxy(network=network, **options)
    result = parsed(source)
    expected_network = {"raw": "tcp", "http-upgrade": "httpupgrade"}.get(network, network)
    assert result.config == {**source, "network": expected_network}
    assert result.reason is None
    assert SECRET not in repr(result)


@pytest.mark.parametrize(
    "extra",
    [
        {"network": "kcp"}, {"network": "quic"},
        {"ws-opts": {"path": "/unused"}},
        {"network": "ws", "grpc-opts": {"grpc-service-name": "unused"}},
        {"network": "ws", "ws-opts": {"unknown": SECRET}},
        {"network": "grpc", "grpc-opts": {"grpc-mode": "gun"}},
        {"network": "h2", "h2-opts": {"headers": {"Authorization": SECRET}}},
        {"network": "xhttp", "xhttp-opts": {"extra": {"downloadSettings": {"address": "other"}}}},
        {"network": "xhttp", "xhttp-opts": {"mode": "other"}},
        {"network": "ws", "ws-opts": {"v2ray-http-upgrade": True, "max-early-data": 1}},
    ],
)
def test_unsupported_transport_options_are_visible_but_unavailable(extra):
    assert parsed(proxy(**extra)).config is None


@pytest.mark.parametrize(
    "extra",
    [
        {"network": False}, {"network": "ws", "ws-opts": []},
        {"network": "ws", "ws-opts": {"path": "relative"}},
        {"network": "ws", "ws-opts": {"path": "//other/route"}},
        {"network": "ws", "ws-opts": {"path": "/bad#fragment"}},
        {"network": "ws", "ws-opts": {"path": "/bad path"}},
        {"network": "ws", "ws-opts": {"headers": {"bad:name": "value"}}},
        {"network": "ws", "ws-opts": {"headers": {"Host": "one.test", "host": "two.test"}}},
        {"network": "ws", "ws-opts": {"headers": {"Host": "user@host.test"}}},
        {"network": "ws", "ws-opts": {"headers": {"Host": "host.test:65536"}}},
        {"network": "ws", "ws-opts": {"headers": {"Host": "host.test?"}}},
        {"network": "ws", "ws-opts": {"headers": {"Host": "host.test#"}}},
        {"network": "ws", "ws-opts": {"headers": {"X-Test": "x\r\ninjected: x"}}},
        {"network": "ws", "ws-opts": {"headers": {"X-Test": True}}},
        {"network": "ws", "ws-opts": {"max-early-data": True}},
        {"network": "ws", "ws-opts": {"max-early-data": -1}},
        {"network": "ws", "ws-opts": {"max-early-data": 65536}},
        {"network": "ws", "ws-opts": {"early-data-header-name": "bad:name"}},
        {"network": "ws", "ws-opts": {"v2ray-http-upgrade": "false"}},
        {"network": "grpc", "grpc-opts": {"grpc-service-name": 1}},
        {"network": "http", "http-opts": {"path": "/not-a-list"}},
        {"network": "http", "http-opts": {"headers": {"Host": "not-a-list"}}},
        {"network": "http", "http-opts": {"method": "GET\n"}},
        {"network": "h2", "h2-opts": {"host": "not-a-list"}},
        {"network": "httpupgrade", "http-upgrade-opts": {
            "host": "one.test", "headers": {"Host": "two.test"}
        }},
    ],
)
def test_malformed_transport_fields_fail_whole_preview(extra):
    assert_safe_error(document(proxy(**extra)))


def test_transport_collection_bounds():
    assert_safe_error(document(proxy(network="ws", **{"ws-opts": {
        "headers": {f"X-{index}": "value" for index in range(33)}
    }})))
    assert_safe_error(document(proxy(network="h2", **{"h2-opts": {"host": ["host.test"] * 17}})))
    assert_safe_error(document(proxy(network="http", **{"http-opts": {"path": ["/"] * 33}})))


def test_tls_and_reality_fields_preserved():
    source = proxy(tls=True, flow="xtls-rprx-vision", **{
        "servername": "tls.provider.example",
        "skip-cert-verify": False, "alpn": ["h2", "http/1.1"], "client-fingerprint": "chrome",
        "reality-opts": {"public-key": PUBLIC_KEY, "short-id": "0123456789abcdef"},
    })
    assert parsed(source).config == source


@pytest.mark.parametrize(
    "extra",
    [
        {"tls": {}}, {"tls": True, "alpn": "h2"}, {"tls": True, "alpn": []},
        {"tls": True, "alpn": ["h2", "h2"]}, {"tls": True, "alpn": ["h2,http/1.1"]},
        {"tls": True, "alpn": [True]}, {"tls": True, "alpn": ["x"] * 17},
        {"reality-opts": {}}, {"reality-opts": {"public-key": "invalid"}},
        {"reality-opts": {"public-key": PUBLIC_KEY, "short-id": "123"}},
        {"reality-opts": {"public-key": PUBLIC_KEY, "short-id": 1234}},
        {"reality-opts": {"public-key": PUBLIC_KEY, "short-id": "zz"}},
        {"tls": False, "reality-opts": {"public-key": PUBLIC_KEY}},
    ],
)
def test_invalid_tls_or_reality_is_not_coerced(extra):
    assert_safe_error(document(proxy(**extra)))


@pytest.mark.parametrize(
    "extra",
    [
        {"tls": False, "sni": "unused.test"},
        {"tls": True, "client-fingerprint": "not-supported"},
        {"tls": True, "reality-opts": {"public-key": PUBLIC_KEY, "extra": SECRET}},
        {"flow": "xtls-rprx-vision"}, {"tls": True, "flow": "unknown"},
        {"tls": True, "flow": "xtls-rprx-vision", "network": "ws"},
        {"servername": "one.test", "sni": "two.test"},
        {"encryption": "post-quantum-profile"},
    ],
)
def test_tls_and_flow_options_that_cannot_be_preserved_are_unavailable(extra):
    assert parsed(proxy(**extra)).config is None


@pytest.mark.parametrize("kind", ["trojan", "hysteria2", "anytls"])
def test_required_tls_cannot_be_silently_disabled(kind):
    assert parsed(proxy(kind, tls=False)).config is None


@pytest.mark.parametrize("value", [True, -1, 65536, 1.5, "0"])
def test_vmess_alter_id_is_exact_bounded_integer(value):
    assert_safe_error(document(proxy("vmess", alterId=value)))


def test_vmess_cipher_unsupported_is_not_replaced_by_default():
    assert parsed(proxy("vmess", cipher="unsupported-cipher")).config is None


@pytest.mark.parametrize(
    "cipher",
    ["2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm", "2022-blake3-chacha20-poly1305"],
)
def test_shadowsocks_2022_keys_are_validated_and_preserved(cipher):
    length = 16 if cipher == "2022-blake3-aes-128-gcm" else 32
    password = base64.b64encode(bytes(range(length))).decode()
    source = proxy("ss", cipher=cipher, password=password)
    assert parsed(source).config == source
    assert_safe_error(document({**source, "password": SECRET}))
    assert_safe_error(document({**source, "password": base64.b64encode(b"short").decode()}))


@pytest.mark.parametrize(
    ("plugin", "options"),
    [
        ("obfs", {"mode": "tls", "host": "cover.test"}),
        ("obfs", {"mode": "http", "host": "cover.test"}),
        ("v2ray-plugin", {
            "mode": "websocket", "host": "cover.test", "path": "/ws", "tls": True, "mux": False
        }),
    ],
)
def test_known_in_process_shadowsocks_plugins_have_bounded_options(plugin, options):
    source = proxy("ss", plugin=plugin, **{"plugin-opts": options})
    assert parsed(source).config == source
    assert parsed({**source, "plugin-opts": {**options, "args": SECRET}}).config is None


def test_shadowsocks_unsupported_plugin_cipher_and_orphan_options():
    assert parsed(proxy("ss", plugin="/usr/bin/plugin")).config is None
    assert parsed(proxy("ss", cipher="rc4-md5")).config is None
    assert parsed(proxy("ss", **{"plugin-opts": {"mode": "http"}})).config is None
    assert_safe_error(document(proxy("ss", plugin="obfs")))


def test_hysteria2_obfs_bandwidth_and_hopping_are_preserved():
    source = proxy("hysteria2", sni="tls.test", **{
        "obfs": "salamander", "obfs-password": SECRET, "ports": "443,5000-6000",
        "hop-interval": 30, "up": 100, "down": 200, "skip-cert-verify": False,
    })
    assert parsed(source).config == source


@pytest.mark.parametrize(
    "extra",
    [
        {"up": True}, {"down": -1}, {"up": 1000001}, {"up": 1.5},
        {"up": "invalid"}, {"ports": [443]}, {"ports": "0"}, {"ports": "65536"},
        {"ports": "5000-1000"}, {"ports": "443,"}, {"ports": "443;other"},
        {"ports": ",".join(["443"] * 129)}, {"hop-interval": True},
        {"hop-interval": 0}, {"hop-interval": 86401}, {"obfs": "salamander"},
        {"obfs": "salamander", "obfs-password": False},
    ],
)
def test_hysteria2_invalid_fields_reject_preview(extra):
    assert_safe_error(document(proxy("hysteria2", **extra)))


@pytest.mark.parametrize(
    "extra", [{"up": "100 Mbps"}, {"hop-interval": 30}, {"obfs-password": SECRET}]
)
def test_hysteria2_unrepresentable_native_options_remain_visible(extra):
    assert parsed(proxy("hysteria2", **extra)).config is None


@pytest.mark.parametrize(
    "key", ["idle-session-check-interval", "idle-session-timeout", "min-idle-session"]
)
def test_anytls_idle_fields_are_preserved_and_never_coerced(key):
    source = proxy("anytls", **{key: 10})
    assert parsed(source).config == source
    for value in (True, -1, "10", 1000000):
        assert_safe_error(document({**source, key: value}))


@pytest.mark.parametrize("version", [4, 5, 6])
def test_snell_versions_and_modes_are_not_mistranslated(version):
    extra = (
        {"mode": "unshaped"} if version == 6
        else {"obfs-opts": {"mode": "http", "host": "cover.test"}}
    )
    source = proxy("snell", version=version, **extra)
    assert parsed(source).config == source
    assert parsed(proxy("snell", version=3)).config is None
    assert parsed(proxy("snell", version=6, mode="insecure")).config is None
    unused = proxy("snell", **{"obfs-opts": {"mode": "none", "host": "unused.test"}})
    assert parsed(unused).config is None
    assert_safe_error(document(proxy("snell", version=True)))


def test_snell_without_version_is_unavailable_not_silently_upgraded_from_v1_to_v4():
    from open_node.services.subscription_clients import clash_proxy

    source = proxy("snell")
    source.pop("version")
    # The trusted managed-output default must never define untrusted input semantics.
    assert clash_proxy(source)["version"] == 4
    result = parsed(source)
    assert result.name == source["name"] and result.protocol == "snell"
    assert result.config is None and result.reason is not None


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_unsupported_explicit_snell_versions_stay_unavailable(version):
    assert parsed(proxy("snell", version=version)).config is None


@pytest.mark.parametrize("transport", ["TCP", "UDP"])
def test_mieru_preserves_original_authentication_and_transport(transport):
    source = proxy("mieru", transport=transport)
    assert parsed(source).config == source
    assert parsed(proxy("mieru", transport="unknown")).config is None
    assert_safe_error(document(proxy("mieru", username="")))


def test_mieru_without_transport_is_unavailable_not_repaired_to_tcp():
    from open_node.services.subscription_clients import clash_proxy

    source = proxy("mieru")
    source.pop("transport")
    assert clash_proxy(source)["transport"] == "TCP"
    result = parsed(source)
    assert result.name == source["name"] and result.protocol == "mieru"
    assert result.config is None and result.reason is not None


@pytest.mark.parametrize("kind", ["http", "socks5"])
def test_http_and_socks_optional_authentication_is_explicit_and_preserved(kind):
    source = proxy(kind, username="subscriber", password=SECRET)
    assert parsed(source).config == source
    assert_safe_error(document(proxy(kind, password=SECRET)))
    assert_safe_error(document(proxy(kind, username=True)))


@pytest.mark.parametrize("password", [None, ""])
def test_http_partial_authentication_is_unavailable_not_changed_to_authenticated(password):
    from open_node.services.subscription_clients import xray_outbound

    source = proxy("http", username="subscriber")
    if password is not None:
        source["password"] = password
    # Mihomo sends no Basic header for these shapes; the generic Xray converter
    # would request username/empty-password authentication instead.
    users = xray_outbound(source)["settings"]["servers"][0]["users"]
    assert users == [{"user": "subscriber", "pass": ""}]
    assert parsed(source).config is None


@pytest.mark.parametrize("password", [None, ""])
def test_socks_username_and_empty_password_preserve_official_authentication(password):
    from open_node.services.subscription_clients import xray_outbound

    source = proxy("socks5", username="subscriber")
    if password is not None:
        source["password"] = password
    result = parsed(source)
    assert result.config == source
    assert xray_outbound(result.config)["settings"]["servers"][0]["users"] == [
        {"user": "subscriber", "pass": ""}
    ]


@pytest.mark.parametrize("kind", ["http", "socks5"])
def test_http_and_socks_without_credentials_remain_anonymous(kind):
    from open_node.services.subscription_clients import xray_outbound

    source = proxy(kind)
    result = parsed(source)
    assert result.config == source
    assert "users" not in xray_outbound(result.config)["settings"]["servers"][0]


@pytest.mark.parametrize("field_name", ["username", "password"])
@pytest.mark.parametrize("credential", ["a" * 255, "é" * 127 + "a", "密" * 85])
def test_socks_authentication_limit_is_255_bytes_not_255_characters(field_name, credential):
    assert len(credential.encode("utf-8")) == 255
    source = proxy("socks5", username="subscriber", password=SECRET)
    source[field_name] = credential
    assert parsed(source).config == source
    source[field_name] = credential + "a"
    assert_safe_error(document(source))


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("vless", False), ("vmess", False), ("trojan", False), ("ss", False),
        ("shadowsocks", False), ("anytls", False), ("snell", False), ("mieru", False),
        ("http", False), ("socks5", False), ("socks", False),
        ("hysteria2", True), ("hy2", True),
    ],
)
def test_source_udp_defaults_are_explicit_before_the_managed_output_converter(kind, expected):
    from open_node.services.subscription_clients import clash_proxy

    source = proxy(kind)
    source.pop("udp")
    result = parsed(source)
    expected_type = {"shadowsocks": "ss", "socks": "socks5", "hy2": "hysteria2"}.get(kind, kind)
    assert result.config == {**source, "type": expected_type, "udp": expected}
    assert clash_proxy(result.config)["udp"] is expected
    for credential in ("uuid", "password", "psk", "username"):
        if credential in source:
            assert result.config[credential] == source[credential]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    "kind", ["vless", "vmess", "trojan", "ss", "anytls", "snell", "mieru", "socks5"]
)
def test_source_udp_explicit_flags_are_not_overridden(kind, enabled):
    from open_node.services.subscription_clients import clash_proxy

    source = proxy(kind, udp=enabled)
    result = parsed(source)
    assert result.config == source
    assert clash_proxy(result.config)["udp"] is enabled


@pytest.mark.parametrize(("kind", "enabled"), [("http", True), ("hysteria2", False)])
def test_source_udp_cannot_claim_to_override_a_fixed_protocol_capability(kind, enabled):
    result = parsed(proxy(kind, udp=enabled))
    assert result.config is None and result.reason is not None


def test_source_udp_snell_v6_has_no_invented_mihomo_default():
    source = proxy("snell", version=6)
    source.pop("udp")
    assert parsed(source).config is None
    for enabled in (True, False):
        explicit = {**source, "udp": enabled}
        assert parsed(explicit).config == explicit


@pytest.mark.parametrize(
    ("kind", "key", "value"),
    [
        ("http", "servername", "ignored.test"), ("http", "alpn", ["h2"]),
        ("http", "client-fingerprint", "chrome"), ("socks5", "sni", "ignored.test"),
        ("socks5", "servername", "ignored.test"), ("socks5", "alpn", ["h2"]),
        ("socks5", "client-fingerprint", "chrome"),
        ("vless", "sni", "ignored.test"), ("vmess", "sni", "ignored.test"),
        ("trojan", "servername", "ignored.test"), ("anytls", "servername", "ignored.test"),
        ("hysteria2", "servername", "ignored.test"),
        ("hysteria2", "client-fingerprint", "chrome"),
    ],
)
def test_source_tls_ignored_fields_do_not_gain_meaning_in_another_client(kind, key, value):
    result = parsed(proxy(kind, tls=True, **{key: value}))
    assert result.config is None and result.reason is not None


@pytest.mark.parametrize(
    ("kind", "options"),
    [
        ("vless", {"servername": "verified.test", "alpn": ["h2"], "client-fingerprint": "chrome"}),
        ("vmess", {"servername": "verified.test", "alpn": ["h2"], "client-fingerprint": "chrome"}),
        ("trojan", {"sni": "verified.test", "alpn": ["h2"], "client-fingerprint": "chrome"}),
        ("anytls", {"sni": "verified.test", "alpn": ["h2"], "client-fingerprint": "chrome"}),
        ("hysteria2", {"sni": "verified.test", "alpn": ["h3"]}),
        ("http", {"sni": "verified.test"}), ("socks5", {}),
    ],
)
def test_source_tls_confirmed_option_subset_preserves_original_values(kind, options):
    from open_node.services.subscription_clients import clash_proxy, sing_box_tls

    source = proxy(kind, tls=True, **{"skip-cert-verify": False, **options})
    result = parsed(source)
    assert result.config == source
    assert clash_proxy(result.config)["skip-cert-verify"] is False
    tls = sing_box_tls(result.config)
    assert tls["insecure"] is False
    if "sni" in source or "servername" in source:
        assert tls["server_name"] == "verified.test"
    else:
        assert "server_name" not in tls


def test_every_selected_basic_node_remains_convertible_by_existing_clash_output():
    from open_node.services.subscription_clients import clash_proxy, unsupported_reason

    kinds = [
        "vless", "vmess", "trojan", "ss", "hysteria2", "anytls", "snell", "mieru", "http", "socks5"
    ]
    nodes = parse_external_subscription(document(*(proxy(kind, name=kind) for kind in kinds)))
    for node in nodes:
        original = deepcopy(node.config)
        assert unsupported_reason(node.config, "clash") is None
        converted = clash_proxy(node.config)
        assert node.config == original
        assert converted["server"] == original["server"]
        for field_name in ("password", "uuid", "psk", "username"):
            if field_name in original:
                assert converted[field_name] == original[field_name]
        assert len(json.dumps(converted)) < MAX_BODY_BYTES
