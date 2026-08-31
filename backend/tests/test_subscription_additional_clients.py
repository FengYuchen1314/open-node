"""Representative, offline coverage of the pinned six additional output schemas."""

import json
from copy import deepcopy

import pytest
import yaml
from fastapi.testclient import TestClient
from open_node.domain.subscriptions import SubscriptionClientFormat
from open_node.services import subscription_clients as clients
from open_node.services.inventory import InventoryStore
from open_node.services.subscription_extra_clients import convert_proxy, render_nodes
from open_node.services.subscription_profiles import SubscriptionProfiles
from open_node.services.template_rendering import DEFAULT_CLASH, TemplateError, render_stash
from test_subscription_clients import catalog

FORMATS = ("loon", "quantumult-x", "shadowrocket", "stash", "surfboard", "egern")
PROXY = {
    "name": "东京节点 🐱",
    "type": "vless",
    "server": "edge.example.test",
    "port": 443,
    "uuid": "00000000-0000-4000-8000-000000000001",
    "tls": True,
    "sni": "tls.example.test",
    "skip-cert-verify": False,
}


@pytest.mark.parametrize("format", FORMATS)
def test_real_subscription_exports_six_distinct_formats_with_honest_preview(tmp_path, format):
    client, token, _, credentials = catalog(tmp_path, kinds=("trojan", "snell6"))
    response = client.get(f"/api/v1/subscribe/{token}", params={"format": format})
    assert response.status_code == 200, response.text
    assert response.headers["x-open-node-included-nodes"] == "1"
    assert response.headers["x-open-node-excluded-nodes"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert "subscription-userinfo" in response.headers
    assert "profile-title" in response.headers
    password = credentials["trojan"]["password"]
    if format == "egern":
        proxy = yaml.safe_load(response.text)["proxies"][0]["trojan"]
        assert proxy["password"] == password and "type" not in proxy
    elif format in {"stash", "shadowrocket"}:
        document = yaml.safe_load(response.text)
        proxy = document["proxies"][0]
        assert proxy["type"] == "trojan" and proxy["password"] == password
        assert "tls" not in proxy
        assert ("rules" in document) is (format == "stash")
    else:
        prefix = (
            "trojan=edge.example.com:443"
            if format == "quantumult-x"
            else "trojan=trojan,edge.example.com,443"
        )
        assert response.text.startswith(prefix)
        assert password in response.text and "[Rule]" not in response.text
    preview_path = "/api/v1/users/reader/subscription-preview"
    preview = client.get(preview_path, params={"format": format}).raise_for_status().json()
    assert [node["available"] for node in preview["nodes"]] == [True, False]
    assert preview["nodes"][1]["reason"]
    assert password not in json.dumps(preview)
    assert TestClient(client.app).get(preview_path, params={"format": format}).status_code == 401


@pytest.mark.parametrize(
    ("format", "agent"),
    [
        ("loon", "Loon/3.3"),
        ("quantumult-x", "Quantumult%20X/1.5"),
        ("shadowrocket", "Shadowrocket/2.2"),
        ("stash", "Stash/2.5 Clash/1.9"),
        ("surfboard", "Surfboard/2.24"),
        ("egern", "Egern/2.0"),
    ],
)
def test_real_ua_selection_and_explicit_format_precedence(tmp_path, format, agent):
    client, token, _, _ = catalog(tmp_path, kinds=("trojan",))
    path = f"/api/v1/subscribe/{token}"
    explicit = client.get(path, params={"format": format}).raise_for_status()
    for params in ({}, {"format": "auto"}):
        automatic = client.get(path, params=params, headers={"User-Agent": agent})
        assert automatic.status_code == 200 and automatic.text == explicit.text
    clash = client.get(path, params={"format": "clash"}, headers={"User-Agent": agent})
    assert clash.status_code == 200 and "proxy-groups" in yaml.safe_load(clash.text)
    assert client.get(path, params={"format": "not-a-client"}).status_code == 422
    assert clients.select_client_format(None, "Mozilla/5.0") == SubscriptionClientFormat.CLASH
    if format == "quantumult-x":
        assert client.get(path, params={"format": "qx"}).text == explicit.text


def test_vless_reality_tls_and_egern_transport_fields_are_preserved():
    proxy = {
        **PROXY,
        "flow": "xtls-rprx-vision",
        "reality-opts": {
            "public-key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
            "short-id": "0123abcd",
        },
    }
    original = deepcopy(proxy)
    for format in FORMATS:
        reason = clients.unsupported_reason(proxy, format)
        if format == "surfboard":
            assert reason == "Protocol is not supported by this client format"
            continue
        assert reason is None, (format, reason)
        converted = convert_proxy(proxy, format)
        assert proxy == original
        if format == "egern":
            output = converted["vless"]
            assert output["user_id"] == proxy["uuid"] and output["flow"] == proxy["flow"]
            tls = output["transport"]["tls"]
            assert tls["skip_tls_verify"] is False and tls["sni"] == PROXY["sni"]
            assert tls["reality"]["public_key"] == proxy["reality-opts"]["public-key"]
        elif format in {"stash", "shadowrocket"}:
            assert converted["reality-opts"] == proxy["reality-opts"]
            assert converted["flow"] == proxy["flow"]
            assert converted["skip-cert-verify"] is False
        else:
            assert "0123abcd" in converted and "xtls-rprx-vision" in converted
            expected = (
                "tls-verification=true" if format == "quantumult-x" else "skip-cert-verify=false"
            )
            assert expected in converted
    grpc = {**PROXY, "network": "grpc", "grpc-opts": {"grpc-service-name": "private-service"}}
    assert clients.unsupported_reason(grpc, "egern") is None
    assert convert_proxy(grpc, "egern")["vless"]["transport"]["grpc"] == {
        "service_name": "private-service",
        "sni": PROXY["sni"],
        "skip_tls_verify": False,
    }


def test_unsupported_extensions_and_native_injection_have_safe_explicit_reasons():
    for format in FORMATS:
        for patch in (
            {"network": "xhttp", "xhttp-opts": {"path": "/private"}},
            {"encryption": "unsupported"},
            {"flow": "unknown-flow"},
            {"dialer-proxy": "private-hop"},
            {"network": "ws", "ws-opts": {"path": "/", "max-early-data": 1024}},
        ):
            reason = clients.unsupported_reason({**PROXY, **patch}, format)
            assert isinstance(reason, str) and "private" not in reason
    for format in clients.TEXT_NODE_FORMATS:
        for password in ("secret,tag=Injected", "secret\n[Rule]\nFINAL,DIRECT", 'secret"'):
            proxy = {
                "name": "node",
                "type": "trojan",
                "server": "edge.test",
                "port": 443,
                "password": password,
            }
            reason = clients.unsupported_reason(proxy, format)
            assert reason == "Client format cannot represent these credentials safely"
            assert password not in reason
    assert clients.unsupported_reason({**PROXY, "client-fingerprint": "custom"}, "loon")
    assert clients.unsupported_reason(
        {**PROXY, "tls": False, "reality-opts": {"public-key": "key"}}, "egern"
    )


def test_stash_uses_compatible_rules_dns_and_hysteria_schema_without_tls_downgrade():
    proxy = {
        "name": "Hysteria node",
        "type": "hysteria2",
        "server": "edge.test",
        "port": 443,
        "password": "test-password",
        "up": 10,
        "down": 20,
        "tfo": True,
        "skip-cert-verify": False,
        "sni": "tls.example.test",
    }
    template = (
        DEFAULT_CLASH
        + "dns:\n  nameserver: [https://dns.example.test/query]\n"
        "  direct-nameserver: [1.1.1.1]\n  nameserver-policy:\n"
        "    '*.example.test': [1.1.1.1]\n"
    )
    rendered, warnings = render_stash(template, [proxy])
    value = yaml.safe_load(rendered)
    assert warnings == [] and value["rules"] == ["MATCH,Proxy"]
    assert value["proxy-groups"][0]["proxies"] == ["Hysteria node"]
    node = value["proxies"][0]
    assert node["auth"] == "test-password" and "password" not in node
    assert node["up-speed"] == "10" and node["down-speed"] == "20"
    assert node["skip-cert-verify"] is False and "tls" not in node
    assert value["dns"]["nameserver"] == ["https://dns.example.test/query", "1.1.1.1"]
    assert value["dns"]["nameserver-policy"]["*.example.test"] == "1.1.1.1"
    assert "skip-cert-verify" not in value["dns"]
    with pytest.raises(TemplateError, match="not compatible with Stash"):
        render_stash(
            DEFAULT_CLASH
            + "rule-providers:\n  private:\n    format: mrs\n    url: https://private.test/a.mrs\n",
            [proxy],
        )
    with pytest.raises(TemplateError, match="not compatible with Stash"):
        render_stash(DEFAULT_CLASH + "tun:\n  enable: true\n", [proxy])
    for providers in ("null", "[]", "false", "invalid"):
        with pytest.raises(TemplateError, match="not compatible with Stash"):
            render_stash(DEFAULT_CLASH + "rule-providers: " + providers + "\n", [proxy])


def test_native_names_are_sanitized_and_unique_in_actual_subscription(tmp_path):
    client, token, _, _ = catalog(tmp_path, kinds=("trojan", "anytls"), names=("A,B", "A=B"))
    for format in ("loon", "quantumult-x", "surfboard"):
        response = client.get(f"/api/v1/subscribe/{token}", params={"format": format})
        assert response.status_code == 200 and response.headers["x-open-node-included-nodes"] == "2"
        lines = response.text.splitlines()
        assert len(lines) == 2
        if format == "quantumult-x":
            assert lines[0].endswith(",tag=A_B") and lines[1].endswith(",tag=A_B (2)")
        else:
            assert lines[0].startswith("A_B=") and lines[1].startswith("A_B (2)=")


def test_temporary_auto_format_preserves_access_limit_privacy_and_legacy_aliases(tmp_path):
    client, _, ids, _ = catalog(tmp_path, kinds=("trojan",))
    response = client.post(
        "/api/v1/temporary-subscriptions",
        json={
            "username": "reader",
            "label": "private-share",
            "node_ids": [ids["trojan"]],
            "max_access": 1,
            "expires_in_seconds": 300,
        },
    )
    share = response.raise_for_status().json()
    rendered = client.get(share["subscription_url"], headers={"User-Agent": "Egern/2.0"})
    assert rendered.status_code == 200 and "trojan" in yaml.safe_load(rendered.text)["proxies"][0]
    assert "subscription-userinfo" not in rendered.headers
    assert rendered.headers["cache-control"] == "no-store"
    assert client.get(share["subscription_url"]).status_code == 404
    for format in FORMATS:
        assert (
            SubscriptionProfiles.legacy_format(format, SubscriptionClientFormat.CLASH).value
            == format
        )
    assert (
        SubscriptionProfiles.legacy_format("qx", SubscriptionClientFormat.CLASH).value
        == "quantumult-x"
    )


def test_all_incompatible_nodes_never_become_empty_success_or_direct_fallback(tmp_path):
    client, token, _, _ = catalog(tmp_path, kinds=("snell6",))
    for format in FORMATS:
        response = client.get(f"/api/v1/subscribe/{token}", params={"format": format})
        assert response.status_code == 404 and "compatible nodes" in response.json()["detail"]
    # Existing serializers remain selected by their own explicit format.
    uri, media, extension = InventoryStore._render_subscription_content(
        [PROXY], SubscriptionClientFormat.URI_LIST
    )
    assert uri.startswith("vless://") and media.startswith("text/plain") and extension == "txt"
    for format in ("shadowrocket", "egern"):
        rendered, _, _ = render_nodes([PROXY], format)
        assert rendered.startswith("proxies:") and not rendered.startswith("vless://")
