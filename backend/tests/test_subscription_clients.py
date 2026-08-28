import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from open_node.services import subscription_clients
from open_node.services.inventory import InventoryStore
from test_subscriptions import create_catalog_fixture, make_client


def catalog(tmp_path, kinds=("anytls", "snell4", "snell6", "mieru"), names=None):
    client = make_client(tmp_path)
    server = client.post("/api/v1/servers", json={"name": "format-edge"}).json()["server"]
    client.post("/api/v1/users", json={"username": "reader"}).raise_for_status()
    ids = {}
    for index, kind in enumerate(kinds):
        protocol = "snell" if kind.startswith("snell") else kind
        config = {
            "name": names[index] if names else kind,
            "type": protocol,
            "server": "edge.example.com",
            "port": 443,
        }
        template = {"email": "{username}__" + kind}
        if protocol == "snell":
            version = int(kind[-1])
            config["version"] = version
            template["version"] = version
        if protocol == "anytls":
            config.update({"tls": True, "sni": "edge.example.com"})
        if protocol == "mieru":
            config["transport"] = "TCP"
        response = client.post(
            "/api/v1/nodes",
            json={
                "name": kind,
                "server_id": server["id"],
                "protocol": protocol,
                "inbound_tag": kind,
                "client_template": template,
                "config": config,
            },
        )
        response.raise_for_status()
        ids[kind] = response.json()["node"]["id"]
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "formats",
            "traffic_limit_gb": 10,
            "node_ids": list(ids.values()),
        },
    ).json()["plan"]
    assigned = client.post("/api/v1/users/reader/plan", json={"plan_id": plan["id"]}).json()
    clients = {
        item["tag"]: item["client"]
        for item in assigned["provisioning_batches"][0]["body"]["inbound_clients"]
    }
    token = client.post("/api/v1/users/reader/subscription-token").json()["subscription"]["token"]
    return client, token, ids, clients


def test_mixed_formats_filter_unsupported_nodes_and_report_counts(tmp_path):
    client, token, ids, users = catalog(tmp_path)
    clash_response = client.get(f"/api/v1/subscribe/{token}")
    assert clash_response.status_code == 200
    clash = yaml.safe_load(clash_response.text)
    assert [proxy["name"] for proxy in clash["proxies"]] == ["anytls", "snell4", "mieru"]
    assert clash["proxy-groups"][0]["proxies"] == ["anytls", "snell4", "mieru"]
    assert clash_response.headers["x-open-node-included-nodes"] == "3"
    assert clash_response.headers["x-open-node-excluded-nodes"] == "1"
    assert clash_response.headers["cache-control"] == "no-store"
    sing_box = client.get(f"/api/v1/subscribe/{token}?format=sing-box").json()
    assert sing_box["outbounds"][0]["outbounds"] == ["anytls"]
    assert sing_box["inbounds"][0]["listen"] == "127.0.0.1"
    xray_response = client.get(f"/api/v1/subscribe/{token}?format=xray")
    xray = xray_response.json()
    assert [outbound["tag"] for outbound in xray["outbounds"]] == ["anytls", "snell4", "snell6"]
    assert xray["inbounds"][0]["listen"] == "127.0.0.1"
    selected = client.get(f"/api/v1/subscribe/{token}?format=xray&node_id={ids['snell6']}")
    assert selected.status_code == 200
    assert len(selected.json()["outbounds"]) == 1
    assert selected.json()["outbounds"][0]["settings"]["psk"] == users["snell6"]["psk"]
    assert selected.headers["x-open-node-included-nodes"] == "1"
    assert (
        client.get(f"/api/v1/subscribe/{token}?format=xray&node_id={ids['mieru']}").status_code
        == 404
    )
    assert client.get(f"/api/v1/subscribe/{token}?format=xray&node_id={uuid4()}").status_code == 404
    assert client.get(f"/api/v1/subscribe/{token}?format=xray&node_id=invalid").status_code == 422


def test_preview_is_authenticated_and_never_contains_credentials(tmp_path):
    client, _, ids, users = catalog(tmp_path)
    path = "/api/v1/users/reader/subscription-preview?format=clash"
    response = client.get(path)
    assert response.status_code == 200
    report = response.json()
    assert report["license_required"] is False
    assert report["nodes"][2] == {
        "node_id": ids["snell6"],
        "name": "snell6",
        "protocol": "snell",
        "available": False,
        "reason": "Snell v6 requires the Xray compatibility client",
    }
    for user in users.values():
        for key in ("password", "psk"):
            if key in user:
                assert user[key] not in response.text
    assert "client_template" not in response.text
    assert TestClient(client.app).get(path).status_code == 401


@pytest.mark.parametrize("format", ["clash", "sing-box", "uri-list", "base64"])
def test_all_unsupported_does_not_return_an_empty_or_direct_fallback(tmp_path, format):
    client, token, _, _ = catalog(tmp_path, ("snell6",))
    response = client.get(f"/api/v1/subscribe/{token}?format={format}")
    assert response.status_code == 404
    assert "compatible nodes" in response.json()["detail"]
    report = client.get(f"/api/v1/users/reader/subscription-preview?format={format}").json()
    assert report["nodes"][0]["available"] is False


def test_duplicate_and_reserved_names_get_stable_unique_tags(tmp_path):
    client, token, _, _ = catalog(tmp_path, names=["Proxy", "Proxy", "direct", "Proxy (2)"])
    response = client.get(f"/api/v1/subscribe/{token}")
    clash = yaml.safe_load(response.text)
    names = [proxy["name"] for proxy in clash["proxies"]]
    assert names == ["Proxy (2)", "Proxy (3)", "Proxy (2) (2)"]
    assert len(names) == len(set(names))
    assert client.get(f"/api/v1/subscribe/{token}").text == response.text


@pytest.mark.parametrize(
    "kind", ["vless", "vmess", "trojan", "shadowsocks", "anytls", "hysteria2", "socks", "http"]
)
def test_xray_protocol_credentials_map_to_native_schema(kind):
    proxy = {
        "name": kind,
        "type": kind,
        "server": "example.com",
        "port": 443,
        "uuid": str(uuid4()),
        "username": "reader",
        "password": "fixture-password",
    }
    assert subscription_clients.unsupported_reason(proxy, "xray") is None
    outbound = subscription_clients.xray_outbound(proxy)
    settings = outbound["settings"]
    if kind in {"vless", "vmess"}:
        assert settings["vnext"][0]["users"][0]["id"] == proxy["uuid"]
    elif kind == "hysteria2":
        assert outbound["protocol"] == "hysteria"
        assert outbound["streamSettings"]["hysteriaSettings"]["auth"] == proxy["password"]
    elif kind in {"trojan", "shadowsocks"}:
        assert settings["servers"][0]["password"] == proxy["password"]
    elif kind in {"socks", "http"}:
        assert settings["servers"][0]["users"][0]["pass"] == proxy["password"]
    else:
        assert settings["password"] == proxy["password"]


@pytest.mark.parametrize(
    "transport,options,xray_key,sb_key",
    [
        (
            "ws",
            {"ws-opts": {"path": "/edge", "headers": {"Host": "ws.example.com"}}},
            "wsSettings",
            "ws",
        ),
        ("grpc", {"grpc-opts": {"grpc-service-name": "edge-service"}}, "grpcSettings", "grpc"),
        (
            "httpupgrade",
            {"http-upgrade-opts": {"host": "edge.example.com", "path": "/edge"}},
            "httpupgradeSettings",
            "httpupgrade",
        ),
    ],
)
def test_transport_and_tls_survive_schema_conversion(transport, options, xray_key, sb_key):
    proxy = {
        "type": "vless",
        "name": "TLS node",
        "server": "example.com",
        "port": 443,
        "uuid": str(uuid4()),
        "tls": True,
        "sni": "edge.example.com",
        "alpn": ["h2"],
        "network": transport,
        **options,
    }
    assert subscription_clients.unsupported_reason(proxy, "xray") is None
    xray = subscription_clients.xray_outbound(proxy)["streamSettings"]
    assert xray["tlsSettings"]["serverName"] == "edge.example.com"
    assert xray["tlsSettings"]["alpn"] == ["h2"]
    assert xray_key in xray
    assert subscription_clients.sing_box_transport(proxy)["type"] == sb_key
    assert subscription_clients.sing_box_tls(proxy)["alpn"] == ["h2"]


def test_reality_is_not_silently_downgraded_to_tls():
    proxy = {
        "type": "vless",
        "name": "Reality node",
        "server": "example.com",
        "port": 443,
        "uuid": str(uuid4()),
        "tls": True,
        "sni": "edge.example.com",
        "reality-opts": {"public-key": "public-key", "short-id": "0123"},
    }
    xray = subscription_clients.xray_outbound(proxy)["streamSettings"]
    assert xray["security"] == "reality"
    assert xray["realitySettings"]["publicKey"] == "public-key"
    assert subscription_clients.sing_box_tls(proxy)["reality"]["public_key"] == "public-key"
    assert subscription_clients.unsupported_reason(proxy, "uri-list") is None
    assert subscription_clients.uri_options(proxy)["pbk"] == "public-key"
    assert "password" not in json.dumps(xray)


@pytest.mark.parametrize("port", [True, 443.0, 443.5, 0, 65536, "443.0", "-1", "\u00b2"])
def test_invalid_port_is_excluded_without_truncation(port):
    assert (
        subscription_clients.unsupported_reason(
            {"type": "socks", "server": "localhost", "port": port}, "xray"
        )
        == "Server port is invalid"
    )


def test_imported_upgrade_preserves_host_path_and_headers_for_each_client():
    proxy = {
        "type": "vless",
        "name": "upgrade",
        "network": "httpupgrade",
        "server": "127.0.0.1",
        "port": 443,
        "uuid": str(uuid4()),
        "tls": True,
        "sni": "edge.example.com",
    }
    options = {"path": "/edge", "host": "cdn.example.com", "headers": {"X-Edge": "test"}}
    InventoryStore._add_runtime_transport_options(proxy, {"httpupgradeSettings": options})
    assert proxy["http-upgrade-opts"] == options
    clash = subscription_clients.clash_proxy(proxy)
    assert clash["servername"] == "edge.example.com"
    assert clash["network"] == "ws"
    assert clash["ws-opts"] == {
        "path": "/edge",
        "headers": {"Host": "cdn.example.com", "X-Edge": "test"},
        "v2ray-http-upgrade": True,
    }
    for original in (proxy, clash):
        assert subscription_clients.sing_box_transport(original) == {
            "type": "httpupgrade",
            **options,
        }
        assert (
            subscription_clients.xray_outbound(original)["streamSettings"]["httpupgradeSettings"]
            == options
        )


def test_http_camouflage_is_not_converted_to_http2():
    proxy = {
        "type": "vless",
        "name": "HTTP",
        "server": "localhost",
        "port": 443,
        "uuid": str(uuid4()),
        "network": "http",
    }
    assert subscription_clients.unsupported_reason(proxy, "sing-box") is not None
    proxy["network"] = "h2"
    InventoryStore._add_runtime_transport_options(
        proxy, {"httpSettings": {"host": ["cdn.example.com"], "path": "/edge"}}
    )
    assert proxy["h2-opts"] == {"host": ["cdn.example.com"], "path": "/edge"}
    assert subscription_clients.sing_box_transport(proxy) == {
        "type": "http",
        "host": ["cdn.example.com"],
        "path": "/edge",
    }
    assert InventoryStore._runtime_subscription_network("http") == "h2"


def test_hysteria_native_network_is_not_a_v2ray_wrapper():
    proxy = {
        "type": "hysteria2",
        "name": "Hy2",
        "server": "localhost",
        "port": 443,
        "password": "fixture",
        "network": "hysteria",
    }
    for kind in ("clash", "sing-box", "xray"):
        assert subscription_clients.unsupported_reason(proxy, kind) is None
    assert subscription_clients.sing_box_transport(proxy) is None


def test_custom_tls_is_preserved_or_explicitly_excluded():
    proxy = {
        "type": "trojan",
        "name": "TLS",
        "server": "localhost",
        "port": 443,
        "password": "fixture",
        "tls": {"enabled": True, "server_name": "edge.example.com", "certificate": ["fixture-ca"]},
    }
    assert subscription_clients.sing_box_tls(proxy)["certificate"] == ["fixture-ca"]
    for kind in ("xray", "clash", "uri-list", "base64"):
        assert "TLS" in subscription_clients.unsupported_reason(proxy, kind)


def test_websocket_early_data_preserves_query_and_requires_supported_header():
    proxy = {
        "type": "vless",
        "name": "WS",
        "server": "localhost",
        "port": 443,
        "uuid": str(uuid4()),
        "network": "ws",
        "ws-opts": {
            "path": "/edge?key=value",
            "max-early-data": 2048,
            "early-data-header-name": "Sec-WebSocket-Protocol",
        },
    }
    assert subscription_clients.unsupported_reason(proxy, "xray") is None
    assert (
        subscription_clients.xray_outbound(proxy)["streamSettings"]["wsSettings"]["path"]
        == "/edge?key=value&ed=2048"
    )
    proxy["ws-opts"]["early-data-header-name"] = "X-Early"
    assert "early data" in subscription_clients.unsupported_reason(proxy, "xray")


def test_shadowsocks_import_preserves_cipher_and_2022_server_key():
    proxy = {}
    InventoryStore._add_protocol_runtime_options(
        proxy,
        {
            "clients": [
                {"email": "first", "method": "aes-256-gcm", "password": "existing-user-secret"}
            ]
        },
        "shadowsocks",
    )
    assert proxy == {"cipher": "aes-256-gcm"}
    InventoryStore._add_protocol_runtime_options(
        proxy,
        {
            "method": "2022-blake3-aes-128-gcm",
            "password": "server-key",
            "clients": [{"password": "existing-user-key"}],
        },
        "shadowsocks",
    )
    assert proxy == {"cipher": "2022-blake3-aes-128-gcm", "server-key-source": "runtime"}
    node = SimpleNamespace(inbound_tag="ss", config=proxy)
    inbound = {
        "tag": "ss",
        "protocol": "shadowsocks",
        "settings": {"method": proxy["cipher"], "password": "server-key"},
    }
    scan = SimpleNamespace(inbounds=[inbound])
    proxy["password"] = InventoryStore._runtime_shadowsocks_server_key(scan, node)
    assert proxy["password"] == "server-key"
    InventoryStore._apply_credential_to_proxy(proxy, "shadowsocks", {"password": "new-user-key"})
    assert proxy["password"] == "server-key:new-user-key"
    assert InventoryStore._runtime_shadowsocks_server_key(None, node) is None
    scan.inbounds.append(inbound)
    assert InventoryStore._runtime_shadowsocks_server_key(scan, node) is None


@pytest.mark.parametrize("kind", ["vless", "trojan", "vmess"])
@pytest.mark.parametrize("transport", ["ws", "grpc", "httpupgrade"])
def test_uri_transport_tls_and_ipv6_survive_export(kind, transport):
    proxy = {
        "name": "reader",
        "type": kind,
        "server": "::1",
        "port": 443,
        "uuid": str(uuid4()),
        "password": "pass:@word",
        "network": transport,
        "tls": True,
        "sni": "edge.example.com",
        "alpn": ["h2", "http/1.1"],
        "skip-cert-verify": True,
    }
    if transport == "grpc":
        proxy["grpc-opts"] = {"grpc-service-name": "edge/service"}
    else:
        proxy["ws-opts" if transport == "ws" else "http-upgrade-opts"] = {
            "path": "/edge?a=b",
            "headers": {"Host": "cdn.example.com"},
        }
    uri = InventoryStore._proxy_uri(proxy)
    if kind == "vmess":
        encoded = uri.removeprefix("vmess://")
        result = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        assert result["add"] == "::1"
        assert result["net"] == transport
        assert result["path"] == ("edge/service" if transport == "grpc" else "/edge?a=b")
        assert result["sni"] == "edge.example.com"
        assert result["allowInsecure"] == "1"
    else:
        parsed = urlsplit(uri)
        assert parsed.hostname == "::1" and parsed.port == 443
        query = parse_qs(parsed.query)
        assert query["type"] == [transport]
        assert query["serviceName" if transport == "grpc" else "path"] == [
            "edge/service" if transport == "grpc" else "/edge?a=b"
        ]
        assert query["sni"] == ["edge.example.com"]
        assert query["alpn"] == ["h2,http/1.1"]
        assert query["allowInsecure"] == ["1"]


def test_native_hysteria_options_are_not_lost():
    proxy = {
        "type": "hysteria2",
        "name": "Hy2",
        "server": "localhost",
        "port": 443,
        "password": "fixture",
        "obfs": "salamander",
        "obfs-password": "obfs-fixture",
        "ports": "443,8443-8450",
        "up": 10,
        "down": 20,
        "hop-interval": 15,
    }
    assert subscription_clients.unsupported_reason(proxy, "sing-box") is None
    outbound = InventoryStore._sing_box_outbound(proxy)
    assert outbound["obfs"] == {"type": "salamander", "password": "obfs-fixture"}
    assert outbound["server_ports"] == ["443", "8443:8450"]
    assert outbound["hop_interval"] == "15s"
    assert outbound["up_mbps"] == 10 and outbound["down_mbps"] == 20
    assert subscription_clients.unsupported_reason(proxy, "xray") is not None


def test_pinned_uri_importer_limitation_keeps_native_upgrade_exports_available():
    proxy = {
        "name": "upgrade",
        "type": "vless",
        "server": "localhost",
        "port": 443,
        "uuid": str(uuid4()),
        "network": "httpupgrade",
    }
    for target in ("clash", "sing-box", "xray"):
        assert subscription_clients.unsupported_reason(proxy, target) is None
    for target in ("uri-list", "base64"):
        assert "Mihomo v1.19.30" in subscription_clients.unsupported_reason(proxy, target)


def test_certificate_pinning_and_reality_cannot_be_silently_dropped():
    proxy = {
        "name": "TLS",
        "type": "vless",
        "server": "localhost",
        "port": 443,
        "uuid": str(uuid4()),
        "tls": True,
        "fingerprint": "fixture-cert-pin",
    }
    assert subscription_clients.unsupported_reason(proxy, "clash") is None
    for target in ("sing-box", "xray", "uri-list", "base64"):
        assert "certificate verification" in subscription_clients.unsupported_reason(proxy, target)
    proxy.pop("fingerprint")
    proxy.update({"tls": False, "reality-opts": {"public-key": "fixture"}})
    for target in ("clash", "sing-box", "xray", "uri-list", "base64"):
        assert "REALITY" in subscription_clients.unsupported_reason(proxy, target)


def test_provisioned_vision_flow_survives_every_subscription_format(tmp_path):
    client = make_client(tmp_path)
    _, _, _, plan_id = create_catalog_fixture(client)
    assigned = client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).json()
    flow = assigned["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"]["flow"]
    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
    path = f"/api/v1/subscribe/{token}"
    assert yaml.safe_load(client.get(path).text)["proxies"][0]["flow"] == flow
    assert client.get(path + "?format=sing-box").json()["outbounds"][2]["flow"] == flow
    assert (
        client.get(path + "?format=xray").json()["outbounds"][0]["settings"]["vnext"][0]["users"][
            0
        ]["flow"]
        == flow
    )
    for target in ("uri-list", "base64"):
        body = client.get(path + "?format=" + target).text
        if target == "base64":
            body = base64.b64decode(body).decode()
        assert parse_qs(urlsplit(body.strip()).query)["flow"] == [flow]


@pytest.mark.parametrize("flag", ["false", 0, None])
def test_invalid_verification_flags_cannot_weaken_tls(flag):
    proxy = {
        "type": "trojan",
        "name": "TLS",
        "server": "localhost",
        "port": 443,
        "password": "fixture",
        "skip-cert-verify": flag,
    }
    for target in ("clash", "sing-box", "xray", "uri-list", "base64"):
        assert "boolean" in subscription_clients.unsupported_reason(proxy, target)


def test_snell_default_version_is_explicit_and_invalid_versions_are_excluded():
    proxy = {"type": "snell", "name": "Snell", "server": "localhost", "port": 443, "psk": "fixture"}
    assert subscription_clients.clash_proxy(proxy)["version"] == 4
    for version in (0, False, 4.0, "4", None):
        assert (
            subscription_clients.unsupported_reason({**proxy, "version": version}, "clash")
            is not None
        )
