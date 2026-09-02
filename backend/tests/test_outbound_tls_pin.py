from pathlib import Path
from uuid import UUID

from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    database = (tmp_path / "outbound-tls-pin.db").as_posix()
    return authenticated_client(create_app(Settings(database_url=f"sqlite:///{database}")))


def tls_outbound(pin=None, **tls_updates):
    tls_settings = {"serverName": "proxy.example", **tls_updates}
    if pin is not None:
        tls_settings["pinnedPeerCertSha256"] = pin
    return {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {"vnext": [{"address": "proxy.example", "port": 443}]},
        "streamSettings": {
            "network": "tcp",
            "security": "tls",
            "tlsSettings": tls_settings,
        },
    }


def test_validated_probe_queues_bounded_agent_operation(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "tls-probe"}).json()
    server_id = created["server"]["id"]

    response = client.post(
        f"/api/v1/servers/{server_id}/operations/outbounds/tls-pin/probe",
        json={
            "protocol": "VLESS",
            "address": "EXAMPLE.COM.",
            "port": 443,
            "server_name": "TLS.EXAMPLE.COM",
            "alpn": ["h2"],
            "timeout_ms": 2_000,
            "command_timeout_ms": 7_000,
        },
    )

    assert response.status_code == 201, response.text
    public = response.json()["command"]
    assert public["path"] == "/api/child/outbound-tls-pin/probe"
    assert public["timeout_ms"] == 7_000
    internal = next(
        command
        for command in client.app.state.inventory.list_commands(UUID(server_id))
        if str(command.id) == public["id"]
    )
    assert internal.body == {
        "protocol": "vless",
        "address": "example.com",
        "port": 443,
        "server_name": "tls.example.com",
        "alpn": ["h2"],
        "timeout_ms": 2_000,
    }


def test_outbound_tls_requires_pin_and_forbids_allow_insecure(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "tls-outbound"}).json()
    endpoint = (
        f"/api/v1/servers/{created['server']['id']}/operations/outbounds/manage"
    )

    missing = client.post(endpoint, json={"action": "add", "outbound": tls_outbound()})
    insecure = client.post(
        endpoint,
        json={
            "action": "add",
            "outbound": tls_outbound("ab" * 32, allowInsecure=False),
        },
    )
    valid = client.post(
        endpoint,
        json={"action": "add", "outbound": tls_outbound(":".join(["AB"] * 32))},
    )

    assert missing.status_code == 422
    assert "pinnedPeerCertSha256" in missing.text
    assert insecure.status_code == 422
    assert "allowInsecure" in insecure.text
    assert valid.status_code == 201, valid.text
    internal = client.app.state.inventory.list_commands(UUID(created["server"]["id"]))[-1]
    assert internal.body["outbound"]["streamSettings"]["tlsSettings"][
        "pinnedPeerCertSha256"
    ] == "ab" * 32
    assert valid.json()["command"]["body"] == {"redacted": True}


def test_raw_command_cannot_bypass_probe_schema(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "tls-probe-raw"}).json()
    server_id = created["server"]["id"]

    response = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={
            "method": "POST",
            "path": "/api/child/outbound-tls-pin/probe",
            "body": {"address": "169.254.169.254", "port": 80},
        },
    )

    assert response.status_code == 403
    assert "validated outbound workflow" in response.json()["detail"]
    assert client.app.state.inventory.list_commands(UUID(server_id)) == []
