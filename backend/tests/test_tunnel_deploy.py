import hashlib
import json

import pytest
from test_inventory import make_client, scan_result_payload


def native_server(client, *, capability=1, snapshot=True):
    created = client.post(
        "/api/v1/servers", json={"name": "owned-tunnel", "domain": "localhost"}
    ).json()
    base = f"/api/v1/servers/{created['server']['id']}"
    scan = scan_result_payload()
    scan["nginx"]["tunnel_deploy"] = capability
    assert (
        client.post(
            "/api/v1/agents/scan", json={"token": created["agent_token"], **scan}
        ).status_code
        == 200
    )
    current = {"inbounds": [], "outbounds": [{"tag": "direct", "protocol": "freedom"}]}
    if snapshot:
        read = client.post(base + "/operations/xray/config/read").json()["command"]
        assert (
            client.post(
                f"/api/v1/agents/commands/{read['id']}/result",
                json={
                    "token": created["agent_token"],
                    "status": 200,
                    "body": {"success": True, "config": current},
                },
            ).status_code
            == 200
        )
    return created, base, current


def test_native_tunnel_uses_one_conditional_command_and_refreshes_snapshot(tmp_path):
    client = make_client(tmp_path)
    created, base, current = native_server(client)
    response = client.post(
        base + "/xray/runtime/tunnel-deploy",
        json={
            "listen_address": "127.0.0.1",
            "listen_port": 24443,
            "nginx_port": 28001,
            "forward_port": 26174,
            "api_port": 26736,
            "metrics_port": 28889,
            "queue_agent_commands": True,
            "queue_scan_after_apply": True,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["runtime_profile"] == "open-node" and data["license_required"] is False
    assert data["command_count"] == 1
    command = data["commands"][0]
    assert command["path"] == "/api/child/tunnel/deploy"
    body = command["body"]
    assert (
        body["expected_xray_sha256"]
        == hashlib.sha256(
            json.dumps(current, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
    )
    assert "root /opt/open-node-agent/state/nginx/html;" in data["domain_config"]
    assert "/config/certificates/localhost.pem" in data["domain_config"]
    assert "127.0.0.1:28001 ssl proxy_protocol" in data["domain_config"]
    candidate = body["xray_config"]
    assert candidate["api"]["listen"] == "127.0.0.1:26736"
    assert candidate["metrics"]["listen"] == "127.0.0.1:28889"
    assert candidate["inbounds"][0]["settings"]["port"] == 26174
    assert candidate["inbounds"][0]["listen"] == "127.0.0.1"
    assert candidate["routing"]["rules"][0]["domain"] == ["full:localhost"]
    assert "geoip:" not in json.dumps(candidate)
    assert data["scan_command"]["depends_on_command_id"] == command["id"]
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True},
        },
    )
    assert response.status_code == 200
    commands = client.get(base + "/commands").json()["commands"]
    assert any(
        c["path"] == "/api/child/xray/config"
        and c["query"] == "snapshot_source=master_write"
        and c["status"] == "pending"
        for c in commands
    )


@pytest.mark.parametrize(
    "capability,snapshot,message", [(0, True, "upgrade/configure"), (1, False, "read the current")]
)
def test_native_tunnel_requires_capability_and_snapshot_before_queue(
    tmp_path, capability, snapshot, message
):
    client = make_client(tmp_path)
    _, base, _ = native_server(client, capability=capability, snapshot=snapshot)
    response = client.post(
        base + "/xray/runtime/tunnel-deploy", json={"queue_agent_commands": True}
    )
    assert response.status_code == 400 and message in response.text
    assert client.post(base + "/xray/runtime/tunnel-deploy", json={}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"listen_port": 0},
        {"api_port": 65536},
        {"listen_port": 8001},
        {"listen_address": "example.com"},
        {"site_type": "proxy"},
    ],
)
def test_invalid_tunnel_listener_or_proxy_is_rejected(tmp_path, body):
    client = make_client(tmp_path)
    _, base, _ = native_server(client)
    assert client.post(base + "/xray/runtime/tunnel-deploy", json=body).status_code == 422


def test_native_proxy_config_quotes_values_and_enables_tls_verification(tmp_path):
    client = make_client(tmp_path)
    _, base, _ = native_server(client)
    response = client.post(
        base + "/xray/runtime/tunnel-deploy",
        json={
            "site_type": "proxy",
            "site_value": "https://example.com/",
        },
    )
    assert response.status_code == 200
    config = response.json()["domain_config"]
    assert "proxy_ssl_verify on;" in config and "proxy_ssl_server_name on;" in config
    assert "X-Forwarded-For $proxy_protocol_addr;" in config


def test_legacy_tunnel_rejects_custom_ports_without_native_scan(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "legacy", "domain": "localhost"}).json()
    base = f"/api/v1/servers/{created['server']['id']}"
    assert (
        client.post(base + "/xray/runtime/tunnel-deploy", json={"listen_port": 24443}).status_code
        == 400
    )
    data = client.post(base + "/xray/runtime/tunnel-deploy", json={}).json()
    assert data["runtime_profile"] == "legacy" and data["command_count"] == 4
