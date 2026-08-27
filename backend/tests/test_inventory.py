import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.inventory import AgentCapabilities, AgentCommandResultRequest
from open_node.main import create_app
from open_node.services.inventory import CommandModel
from sqlalchemy import Column, MetaData, Table, select, update


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=sqlite_url(tmp_path / "open-node-test.db"))
    return TestClient(create_app(settings))


def scan_result_payload() -> dict[str, object]:
    return {
        "xray_running": True,
        "xray_version": "Xray 1.8.24",
        "api_port": 46736,
        "config_path": "/usr/local/etc/xray/config.json",
        "inbounds": [{"tag": "vless-443", "port": 443, "protocol": "vless"}],
        "device_kicks": {"alice@example.com": 2},
        "config_modified": True,
        "config_added_sections": ["api", "stats"],
        "message": "Xray is running, found 1 inbound(s)",
    }


def queue_recovery(client: TestClient) -> tuple[dict, dict]:
    created = client.post("/api/v1/servers", json={"name": "edge-ordered-recovery"}).json()
    server_id = created["server"]["id"]
    read = client.post(f"/api/v1/servers/{server_id}/operations/xray/config/read").json()["command"]
    result = client.post(
        f"/api/v1/agents/commands/{read['id']}/result",
        json={
            "token": created["agent_token"], "status": 200,
            "body": {"success": True, "config": '{"inbounds":[],"outbounds":[]}'},
        },
    )
    assert result.status_code == 200
    applied = client.post(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery/apply",
        json={"restart_xray": True},
    )
    assert applied.status_code == 201
    return created, applied.json()


def test_server_create_issues_agent_token_without_license_header(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/servers",
        json={
            "name": "edge-1",
            "ip_address": "203.0.113.10",
            "connection_mode": "websocket",
            "xray_mode": "embedded",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["agent_token"]
    assert payload["server"]["status"] == "pending"
    assert "agent_token" not in payload["server"]


def test_server_probe_metadata_round_trips_to_public_probe(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    created = client.post(
        "/api/v1/servers",
        json={
            "name": "edge-meta",
            "ip_address": "203.0.113.10",
            "region": "asia-east",
            "region_country": "JP",
            "region_name": "Japan",
            "region_city": "Tokyo",
            "provider_name": "Example Cloud",
            "provider_url": "https://provider.example",
            "expires_at": "2026-12-31T00:00:00Z",
            "renewal_price": 12.5,
            "renewal_price_cny": 89,
            "renewal_cycle": "month",
            "renewal_currency": "USD",
            "telecom_paid_peer": True,
        },
    )

    assert created.status_code == 201
    server_id = created.json()["server"]["id"]
    assert created.json()["license_required"] is False
    assert created.json()["server"]["provider_name"] == "Example Cloud"
    assert created.json()["server"]["renewal_cycle"] == "month"

    updated = client.patch(
        f"/api/v1/servers/{server_id}/probe-metadata",
        json={
            "region_city": "Osaka",
            "provider_url": None,
            "renewal_price": 10,
            "telecom_paid_peer": False,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["license_required"] is False
    updated_server = updated.json()["server"]
    assert updated_server["region_city"] == "Osaka"
    assert updated_server["provider_url"] is None
    assert updated_server["renewal_price"] == 10
    assert updated_server["telecom_paid_peer"] is False

    probe = client.get("/api/v1/public/probe-servers").json()["servers"][0]
    assert probe["name"] == "edge-meta"
    assert probe["region"] == "asia-east"
    assert probe["region_country"] == "JP"
    assert probe["region_name"] == "Japan"
    assert probe["region_city"] == "Osaka"
    assert probe["provider_name"] == "Example Cloud"
    assert probe["expires_at"] == "2026-12-31"
    assert probe["renewal_price"] == 10
    assert probe["renewal_price_cny"] == 89
    assert probe["renewal_cycle"] == "month"
    assert probe["renewal_currency"] == "USD"
    assert probe["telecom_paid_peer"] is False
    assert "ip_address" not in probe
    assert "agent_token" not in probe


def test_agent_registration_connects_server_without_license_header(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-2"}).json()

    response = client.post(
        "/api/v1/agents/register",
        json={
            "token": created["agent_token"],
            "hostname": "edge-2-host",
            "agent_version": "0.4.7",
            "connection_mode": "websocket",
            "listen_port": 23889,
            "public_ipv4": "198.51.100.22",
            "xray_mode": "embedded",
            "capabilities": {"rpc": True, "stream": True},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["agent"]["hostname"] == "edge-2-host"
    assert payload["agent"]["capabilities"]["rpc"] is True
    assert payload["server"]["status"] == "connected"
    assert payload["server"]["ip_address"] == "198.51.100.22"


def test_agent_registration_syncs_config_and_deduplicates_active_reads(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-register-sync"}).json()
    server_id = created["server"]["id"]
    registration = {"token": created["agent_token"], "hostname": "edge-register-sync"}
    commands_url = f"/api/v1/servers/{server_id}/commands"
    recovery_url = f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery?with_config=true"

    for _ in range(2):
        assert client.post("/api/v1/agents/register", json=registration).status_code == 201
    commands = client.get(commands_url).json()["commands"]
    assert len(commands) == 1
    sync = commands[0]
    assert sync["status"] == "pending"
    assert (sync["method"], sync["path"], sync["query"]) == (
        "GET", "/api/child/xray/config", "",
    )
    assert sync["timeout_ms"] == 60_000

    leased = client.post("/api/v1/agents/commands/lease", json=registration).json()["commands"]
    assert [command["id"] for command in leased] == [sync["id"]]
    assert client.post("/api/v1/agents/register", json=registration).status_code == 201
    commands = client.get(commands_url).json()["commands"]
    assert len(commands) == 1
    assert commands[0]["status"] == "leased"
    assert commands[0]["attempts"] == 1
    assert client.post("/api/v1/agents/commands/lease", json=registration).json()["commands"] == []

    current_config = '{"inbounds":[{"tag":"vless-443"}],"outbounds":[]}'
    drift_config = '{"inbounds":[],"outbounds":[]}'
    for config, has_pending in [
        (current_config, False), (drift_config, True), (current_config, False),
    ]:
        result = client.post(
            f"/api/v1/agents/commands/{sync['id']}/result",
            json={
                "token": created["agent_token"],
                "status": 200,
                "body": {"success": True, "config": config},
            },
        )
        assert result.status_code == 200
        recovery = client.get(recovery_url).json()
        assert recovery["has_current"] is True
        assert recovery["current"]["config"] == current_config
        assert recovery["has_pending"] is has_pending
        if has_pending:
            assert recovery["pending"]["config"] == drift_config
            assert recovery["pending"]["source"] == "agent_report"
            assert recovery["pending"]["source_command_id"] == sync["id"]
        assert client.post("/api/v1/agents/register", json=registration).status_code == 201
        sync = client.post(
            "/api/v1/agents/commands/lease", json=registration,
        ).json()["commands"][0]


def test_agent_heartbeat_updates_speed_without_license_header(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-3"}).json()
    client.post(
        "/api/v1/agents/register",
        json={"token": created["agent_token"], "hostname": "edge-3-host"},
    )

    response = client.post(
        "/api/v1/agents/heartbeat",
        json={
            "token": created["agent_token"],
            "upload_speed": 1234,
            "download_speed": 5678,
        },
    )

    assert response.status_code == 200
    server = response.json()["server"]
    assert server["current_upload_speed"] == 1234
    assert server["current_download_speed"] == 5678


def test_invalid_agent_token_is_rejected_as_auth_not_license(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/agents/register",
        json={"token": "not-a-real-token", "hostname": "unknown"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid agent token"


def test_server_inventory_persists_across_app_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "open-node-test.db"
    first_client = TestClient(create_app(Settings(database_url=sqlite_url(db_path))))

    created = first_client.post(
        "/api/v1/servers",
        json={"name": "edge-persisted", "ip_address": "203.0.113.44"},
    )
    assert created.status_code == 201

    second_client = TestClient(create_app(Settings(database_url=sqlite_url(db_path))))
    response = second_client.get("/api/v1/servers")

    assert response.status_code == 200
    servers = response.json()
    assert len(servers) == 1
    assert servers[0]["name"] == "edge-persisted"
    assert servers[0]["ip_address"] == "203.0.113.44"
    assert "agent_token" not in servers[0]


def test_agent_registration_persists_across_app_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "open-node-test.db"
    first_client = TestClient(create_app(Settings(database_url=sqlite_url(db_path))))
    created = first_client.post("/api/v1/servers", json={"name": "edge-agent"}).json()

    registered = first_client.post(
        "/api/v1/agents/register",
        json={
            "token": created["agent_token"],
            "hostname": "edge-agent-host",
            "connection_mode": "http",
            "listen_port": 28080,
        },
    )
    assert registered.status_code == 201

    second_client = TestClient(create_app(Settings(database_url=sqlite_url(db_path))))
    agents = second_client.get("/api/v1/agents")
    servers = second_client.get("/api/v1/servers")

    assert agents.status_code == 200
    assert agents.json()[0]["hostname"] == "edge-agent-host"
    assert agents.json()[0]["listen_port"] == 28080
    assert servers.json()[0]["status"] == "connected"


def test_duplicate_server_names_are_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    first = client.post("/api/v1/servers", json={"name": "edge-unique"})
    second = client.post("/api/v1/servers", json={"name": "edge-unique"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == "server name already exists: edge-unique"


def test_agent_telemetry_records_xray_and_system_metrics(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-telemetry"}).json()

    response = client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": created["agent_token"],
            "stats": {
                "inbound": {"vmess-in": {"uplink": 100, "downlink": 200}},
                "outbound": {"direct": {"uplink": 40, "downlink": 80}},
                "user": {"alice@example.com": {"uplink": 12, "downlink": 34}},
            },
            "online_users": {"alice@example.com": ["198.51.100.2"]},
            "user_speeds": {"alice@example.com": 4096},
            "conn_counts": {"alice|node-1": 2},
            "system": {"rx_total": 1_000_000, "tx_total": 2_000_000, "boot_time_unix": 42},
            "sysmetrics": {
                "cpu_pct": 12.5,
                "loadavg": "0.12 0.20 0.30",
                "mem_used": 1024,
                "mem_total": 4096,
                "disk_used": 2048,
                "disk_total": 8192,
                "has_cpu": True,
                "has_mem": True,
                "has_disk": True,
            },
            "latency": [{"key": "cmcc", "success": True, "latency_ms": 28, "at": 1798330000}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["server"]["status"] == "connected"
    assert payload["telemetry"]["stats"]["inbound"]["vmess-in"]["uplink"] == 100
    assert payload["telemetry"]["system"]["tx_total"] == 2_000_000
    assert payload["telemetry"]["sysmetrics"]["cpu_pct"] == 12.5
    assert payload["telemetry"]["latency"][0]["key"] == "cmcc"

    latest = client.get(f"/api/v1/servers/{created['server']['id']}/telemetry/latest")
    assert latest.status_code == 200
    assert latest.json()["latest"]["user_speeds"]["alice@example.com"] == 4096
    assert latest.json()["latest"]["conn_counts"]["alice|node-1"] == 2


def test_latest_scan_result_for_unknown_server_returns_404(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/servers/00000000-0000-0000-0000-000000000000/scan/latest")

    assert response.status_code == 404
    assert response.json()["detail"] == "server not found: 00000000-0000-0000-0000-000000000000"


def test_scan_command_result_updates_latest_scan_without_license(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-scan-result"}).json()
    server_id = created["server"]["id"]

    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})

    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={"token": created["agent_token"], "status": 200, "body": scan_result_payload()},
    )

    assert result.status_code == 200
    assert result.json()["command"]["status"] == "succeeded"

    latest = client.get(f"/api/v1/servers/{server_id}/scan/latest")
    payload = latest.json()

    assert latest.status_code == 200
    assert payload["license_required"] is False
    assert payload["scan"]["xray_running"] is True
    assert payload["scan"]["xray_version"] == "Xray 1.8.24"
    assert payload["scan"]["api_port"] == 46736
    assert payload["scan"]["inbounds"][0]["tag"] == "vless-443"
    assert payload["scan"]["device_kicks"] == {"alice@example.com": 2}
    assert payload["scan"]["config_modified"] is True
    assert payload["scan"]["config_added_sections"] == ["api", "stats"]


def test_xray_runtime_inventory_returns_empty_summary_without_scan(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-runtime-empty"}).json()

    response = client.get(f"/api/v1/servers/{created['server']['id']}/xray/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_scan"] is False
    assert payload["inbound_count"] == 0
    assert payload["client_count"] == 0
    assert payload["protocol_counts"] == {}
    assert payload["inbounds"] == []


def test_xray_runtime_inventory_summarizes_scan_inbounds_without_secrets(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-runtime-summary"}).json()
    server_id = created["server"]["id"]

    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [
                    {
                        "tag": "vless-443",
                        "listen": "0.0.0.0",
                        "port": 443,
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {"id": "secret-id-1", "email": "alice@example.com"},
                                {"id": "secret-id-2", "email": "ALICE@example.com"},
                                {"id": "secret-id-3"},
                            ]
                        },
                        "streamSettings": {"network": "tcp", "security": "reality"},
                        "sniffing": {
                            "enabled": True,
                            "destOverride": ["http", "tls", "http"],
                            "excludeDomains": ["Example.com", "example.com", "foo.test"],
                        },
                    },
                    {
                        "port": 8443,
                        "protocol": "anytls",
                        "settings": {
                            "users": [{"password": "hidden-password", "email": "bob@example.com"}]
                        },
                    },
                    {
                        "tag": "socks-in",
                        "protocol": "socks",
                        "settings": {"accounts": [{"user": "root", "pass": "secret-pass"}]},
                    },
                ],
            },
        },
    )

    assert result.status_code == 200
    telemetry = client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": created["agent_token"],
            "stats": {
                "inbound": {"vless-443": {"uplink": 100, "downlink": 200}},
                "user": {
                    "alice@example.com": {"uplink": 12, "downlink": 34},
                    "bob@example.com": {"uplink": 5, "downlink": 6},
                    "root": {"uplink": 9, "downlink": 10},
                },
            },
        },
    )
    assert telemetry.status_code == 200

    response = client.get(f"/api/v1/servers/{server_id}/xray/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_scan"] is True
    assert payload["xray_running"] is True
    assert payload["xray_version"] == "Xray 1.8.24"
    assert payload["api_port"] == 46736
    assert payload["config_modified"] is True
    assert payload["config_added_sections"] == ["api", "stats"]
    assert payload["inbound_count"] == 3
    assert payload["client_count"] == 5
    assert payload["protocol_counts"] == {"anytls": 1, "socks": 1, "vless": 1}
    assert payload["traffic"] == {"uplink": 100, "downlink": 200}
    assert payload["user_traffic"] == {"uplink": 17, "downlink": 40}
    assert payload["traffic_reported_at"]

    vless = payload["inbounds"][0]
    assert vless["tag"] == "vless-443"
    assert vless["display_name"] == "vless-443"
    assert vless["client_container"] == "clients"
    assert vless["client_count"] == 3
    assert vless["user_emails"] == ["alice@example.com"]
    assert vless["network"] == "tcp"
    assert vless["security"] == "reality"
    assert vless["sniffing_enabled"] is True
    assert vless["sniffing_dest_override"] == ["http", "tls"]
    assert vless["sniffing_exclude_domains"] == ["Example.com", "foo.test"]
    assert vless["traffic"] == {"uplink": 100, "downlink": 200}
    assert vless["user_traffic"] == {"uplink": 12, "downlink": 34}
    assert vless["remarks"] == []

    anytls = payload["inbounds"][1]
    assert anytls["tag"] is None
    assert anytls["display_name"] == "anytls-8443"
    assert anytls["client_container"] == "users"
    assert anytls["client_count"] == 1
    assert anytls["user_emails"] == ["bob@example.com"]
    assert anytls["traffic"] == {"uplink": 0, "downlink": 0}
    assert anytls["user_traffic"] == {"uplink": 5, "downlink": 6}
    assert anytls["remarks"] == ["missing_tag"]

    socks = payload["inbounds"][2]
    assert socks["client_container"] == "accounts"
    assert socks["client_count"] == 1
    assert socks["user_emails"] == []
    assert socks["traffic"] == {"uplink": 0, "downlink": 0}
    assert socks["user_traffic"] == {"uplink": 0, "downlink": 0}

    serialized = json.dumps(payload)
    assert "secret-id" not in serialized
    assert "hidden-password" not in serialized
    assert "secret-pass" not in serialized
    assert "root" not in serialized


def test_xray_runtime_inventory_for_unknown_server_returns_404(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/servers/00000000-0000-0000-0000-000000000000/xray/runtime")

    assert response.status_code == 404
    assert response.json()["detail"] == "server not found: 00000000-0000-0000-0000-000000000000"


def test_xray_runtime_node_drafts_create_managed_nodes_without_secrets(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-runtime-node", "domain": "edge.example.com"},
    ).json()
    server_id = created["server"]["id"]
    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [
                    {
                        "tag": "vless-443",
                        "listen": "0.0.0.0",
                        "port": 443,
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {
                                    "id": "secret-runtime-client-id",
                                    "email": "old@example.com",
                                    "flow": "xtls-rprx-vision",
                                }
                            ]
                        },
                        "streamSettings": {
                            "network": "ws",
                            "security": "reality",
                            "wsSettings": {
                                "path": "/edge",
                                "headers": {"Host": "cdn.example.com"},
                            },
                            "realitySettings": {
                                "serverNames": ["www.example.com"],
                                "publicKey": "public-reality-key",
                                "privateKey": "secret-reality-private-key",
                                "shortIds": ["abcd"],
                            },
                        },
                    },
                    {
                        "tag": "ss-2022",
                        "port": 8388,
                        "protocol": "shadowsocks",
                        "settings": {
                            "method": "2022-blake3-aes-256-gcm",
                            "password": "secret-shared-password",
                            "clients": [{"email": "ss@example.com"}],
                        },
                    },
                ],
            },
        },
    )
    assert result.status_code == 200

    drafts_response = client.get(f"/api/v1/servers/{server_id}/xray/runtime/node-drafts")

    assert drafts_response.status_code == 200
    drafts_payload = drafts_response.json()
    assert drafts_payload["license_required"] is False
    assert drafts_payload["has_scan"] is True
    assert len(drafts_payload["drafts"]) == 2
    vless_draft = drafts_payload["drafts"][0]
    assert vless_draft["source_index"] == 0
    assert vless_draft["create_available"] is True
    assert vless_draft["existing_node_id"] is None
    assert vless_draft["draft"]["name"] == "edge-runtime-node vless-443"
    assert vless_draft["draft"]["server_id"] == server_id
    assert vless_draft["draft"]["protocol"] == "vless"
    assert vless_draft["draft"]["inbound_tag"] == "vless-443"
    assert vless_draft["draft"]["tags"] == ["runtime", "vless", "ws", "reality"]
    assert vless_draft["draft"]["client_template"] == {
        "email": "{username}__vless-443",
        "flow": "xtls-rprx-vision",
    }
    assert vless_draft["draft"]["config"] == {
        "name": "edge-runtime-node vless-443",
        "type": "vless",
        "server": "edge.example.com",
        "port": 443,
        "network": "ws",
        "tls": True,
        "sni": "www.example.com",
        "reality-opts": {"public-key": "public-reality-key", "short-id": "abcd"},
        "ws-opts": {"path": "/edge", "headers": {"Host": "cdn.example.com"}},
    }
    assert drafts_payload["drafts"][1]["draft"]["config"]["cipher"] == "2022-blake3-aes-256-gcm"
    serialized_drafts = json.dumps(drafts_payload)
    assert "secret-runtime-client-id" not in serialized_drafts
    assert "secret-reality-private-key" not in serialized_drafts
    assert "secret-shared-password" not in serialized_drafts

    create_response = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes",
        json={"source_index": 0, "host": "public.example.com"},
    )

    assert create_response.status_code == 201
    node = create_response.json()["node"]
    assert node["name"] == "edge-runtime-node vless-443"
    assert node["config"]["server"] == "public.example.com"
    assert node["client_template"]["flow"] == "xtls-rprx-vision"
    assert "secret" not in json.dumps(node)

    repeated = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes",
        json={"source_index": 0, "host": "other.example.com"},
    )

    assert repeated.status_code == 201
    assert repeated.json()["node"]["id"] == node["id"]
    assert repeated.json()["node"]["config"]["server"] == "public.example.com"
    refreshed = client.get(f"/api/v1/servers/{server_id}/xray/runtime/node-drafts").json()
    assert refreshed["drafts"][0]["existing_node_id"] == node["id"]


def test_xray_runtime_node_drafts_handle_missing_scan_and_unavailable_inbound(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-runtime-unavailable"}).json()
    server_id = created["server"]["id"]

    empty = client.get(f"/api/v1/servers/{server_id}/xray/runtime/node-drafts")

    assert empty.status_code == 200
    assert empty.json()["license_required"] is False
    assert empty.json()["has_scan"] is False
    assert empty.json()["drafts"] == []

    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [{"tag": "dokodemo", "protocol": "dokodemo-door"}],
            },
        },
    )

    drafts = client.get(f"/api/v1/servers/{server_id}/xray/runtime/node-drafts").json()["drafts"]
    assert drafts[0]["create_available"] is False
    assert drafts[0]["warnings"] == ["unsupported_protocol", "missing_port"]

    create_response = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes",
        json={"source_index": 0},
    )

    assert create_response.status_code == 400
    assert create_response.json()["detail"] == "unsupported_protocol, missing_port"


def test_xray_runtime_node_import_creates_only_missing_available_nodes(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-runtime-import", "domain": "edge.example.com"},
    ).json()
    server_id = created["server"]["id"]
    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [
                    {
                        "tag": "vless-443",
                        "port": 443,
                        "protocol": "vless",
                        "streamSettings": {"network": "tcp", "security": "tls"},
                    },
                    {
                        "tag": "trojan-443",
                        "port": 8443,
                        "protocol": "trojan",
                        "streamSettings": {"network": "tcp", "security": "tls"},
                    },
                    {"tag": "dokodemo", "protocol": "dokodemo-door"},
                ],
            },
        },
    )
    assert result.status_code == 200
    existing = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes",
        json={"source_index": 1},
    ).json()["node"]

    response = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes/import",
        json={"host": "public.example.com", "extra_tags": ["imported", "runtime"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_scan"] is True
    assert payload["created_count"] == 1
    assert payload["existing_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["created_nodes"][0]["name"] == "edge-runtime-import vless-443"
    assert payload["created_nodes"][0]["config"]["server"] == "public.example.com"
    assert payload["created_nodes"][0]["tags"] == ["runtime", "vless", "tcp", "tls", "imported"]
    assert payload["existing_nodes"][0]["id"] == existing["id"]
    assert payload["skipped"][0] == {
        "source_index": 2,
        "source_tag": "dokodemo",
        "source_display_name": "dokodemo",
        "warnings": ["unsupported_protocol", "missing_port"],
    }
    assert len(client.get("/api/v1/nodes").json()["nodes"]) == 2

    repeated = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes/import",
        json={},
    ).json()

    assert repeated["created_count"] == 0
    assert repeated["existing_count"] == 2
    assert repeated["skipped_count"] == 1


def test_xray_runtime_node_reconciliation_reports_catalog_drift_and_gaps(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-runtime-reconcile", "domain": "edge.example.com"},
    ).json()
    server_id = created["server"]["id"]
    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [
                    {
                        "tag": "vless-443",
                        "port": 443,
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {"email": "old@example.com", "flow": "xtls-rprx-vision"}
                            ]
                        },
                        "streamSettings": {
                            "network": "ws",
                            "security": "tls",
                            "wsSettings": {"path": "/runtime"},
                        },
                    },
                    {"tag": "anytls-8443", "port": 8443, "protocol": "anytls"},
                    {"tag": "dokodemo", "protocol": "dokodemo-door"},
                ],
            },
        },
    )
    assert result.status_code == 200
    stale = client.post(
        "/api/v1/nodes",
        json={
            "name": "Stale vless",
            "server_id": server_id,
            "protocol": "vless",
            "inbound_tag": "vless-443",
            "client_template": {"email": "{username}__vless-443", "flow": "xtls-rprx-vision"},
            "config": {
                "type": "vless",
                "server": "edge.example.com",
                "port": 8443,
                "network": "ws",
                "tls": True,
                "ws-opts": {"path": "/runtime"},
            },
        },
    ).json()["node"]
    missing = client.post(
        "/api/v1/nodes",
        json={
            "name": "Missing trojan",
            "server_id": server_id,
            "protocol": "trojan",
            "inbound_tag": "trojan-443",
            "config": {"type": "trojan", "server": "edge.example.com", "port": 443},
        },
    ).json()["node"]
    routed = client.post(
        "/api/v1/nodes",
        json={
            "name": "Routed catalog",
            "server_id": server_id,
            "protocol": "vless",
            "node_type": "routed",
            "routed_outbound_tag": "proxy-out",
            "routed_rule_marktag": "route-proxy",
            "config": {"type": "vless", "server": "edge.example.com", "port": 443},
        },
    ).json()["node"]

    response = client.get(f"/api/v1/servers/{server_id}/xray/runtime/nodes/reconciliation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_scan"] is True
    assert payload["runtime_count"] == 3
    assert payload["managed_node_count"] == 3
    assert payload["managed_runtime_count"] == 1
    assert payload["unmanaged_runtime_count"] == 1
    assert payload["unavailable_runtime_count"] == 1
    assert payload["in_sync_count"] == 0
    assert payload["stale_count"] == 1
    assert payload["missing_runtime_count"] == 1
    assert payload["catalog_only_count"] == 1
    runtime_entries = {entry["source_display_name"]: entry for entry in payload["runtime_entries"]}
    assert runtime_entries["vless-443"]["status"] == "managed"
    assert runtime_entries["vless-443"]["managed_node_id"] == stale["id"]
    assert runtime_entries["anytls-8443"]["status"] == "unmanaged"
    assert runtime_entries["dokodemo"]["status"] == "unavailable"
    managed_entries = {entry["node_name"]: entry for entry in payload["managed_entries"]}
    assert managed_entries["Stale vless"]["status"] == "stale"
    assert managed_entries["Stale vless"]["node_id"] == stale["id"]
    assert managed_entries["Stale vless"]["runtime_display_name"] == "vless-443"
    assert managed_entries["Stale vless"]["drifts"] == [
        {"field": "config.port", "runtime_value": 443, "managed_value": 8443}
    ]
    assert managed_entries["Missing trojan"]["status"] == "missing_runtime"
    assert managed_entries["Missing trojan"]["node_id"] == missing["id"]
    assert managed_entries["Routed catalog"]["status"] == "catalog_only"
    assert managed_entries["Routed catalog"]["node_id"] == routed["id"]
    assert "old@example.com" not in json.dumps(payload)


def test_xray_runtime_node_sync_updates_public_fields_without_runtime_secrets(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-runtime-sync", "domain": "edge.example.com"},
    ).json()
    server_id = created["server"]["id"]
    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [
                    {
                        "tag": "vless-443",
                        "port": 443,
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {
                                    "id": "secret-runtime-client-id",
                                    "email": "old@example.com",
                                    "flow": "xtls-rprx-vision",
                                }
                            ]
                        },
                        "streamSettings": {
                            "network": "ws",
                            "security": "tls",
                            "wsSettings": {
                                "path": "/runtime",
                                "headers": {"Host": "runtime.example.com"},
                            },
                        },
                    }
                ],
            },
        },
    )
    assert result.status_code == 200
    stale = client.post(
        "/api/v1/nodes",
        json={
            "name": "Operator named vless",
            "server_id": server_id,
            "protocol": "vless",
            "inbound_tag": "vless-443",
            "tags": ["manual", "keep"],
            "enabled": False,
            "client_template": {
                "email": "{username}__operator",
                "flow": "stale-flow",
                "operator_note": "keep",
            },
            "config": {
                "type": "vless",
                "server": "operator.example.com",
                "port": 8443,
                "network": "ws",
                "tls": True,
                "operator_note": "keep",
                "ws-opts": {
                    "path": "/old",
                    "headers": {"Host": "manual.example.com"},
                },
            },
        },
    ).json()["node"]

    response = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes/{stale['id']}/sync",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["source_index"] == 0
    assert payload["source_display_name"] == "vless-443"
    assert payload["updated_fields"] == [
        "config.port",
        "config.ws_path",
        "client_template.flow",
    ]
    assert payload["drifts_before"] == [
        {"field": "config.port", "runtime_value": 443, "managed_value": 8443},
        {"field": "config.ws_path", "runtime_value": "/runtime", "managed_value": "/old"},
        {
            "field": "client_template.flow",
            "runtime_value": "xtls-rprx-vision",
            "managed_value": "stale-flow",
        },
    ]
    assert payload["drifts_after"] == []
    node = payload["node"]
    assert node["name"] == "Operator named vless"
    assert node["tags"] == ["manual", "keep"]
    assert node["enabled"] is False
    assert node["config"]["server"] == "operator.example.com"
    assert node["config"]["port"] == 443
    assert node["config"]["operator_note"] == "keep"
    assert node["config"]["ws-opts"] == {
        "path": "/runtime",
        "headers": {"Host": "manual.example.com"},
    }
    assert node["client_template"] == {
        "email": "{username}__operator",
        "flow": "xtls-rprx-vision",
        "operator_note": "keep",
    }
    serialized = json.dumps(payload)
    assert "secret-runtime-client-id" not in serialized
    assert "old@example.com" not in serialized

    reconciliation = client.get(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes/reconciliation"
    ).json()
    assert reconciliation["in_sync_count"] == 1
    assert reconciliation["stale_count"] == 0
    assert reconciliation["managed_entries"][0]["status"] == "in_sync"

    routed = client.post(
        "/api/v1/nodes",
        json={
            "name": "Routed catalog",
            "server_id": server_id,
            "protocol": "vless",
            "node_type": "routed",
            "routed_outbound_tag": "proxy-out",
            "routed_rule_marktag": "route-proxy",
            "config": {"type": "vless", "server": "edge.example.com", "port": 443},
        },
    ).json()["node"]
    routed_response = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes/{routed['id']}/sync",
        json={"source_index": 0},
    )
    assert routed_response.status_code == 400
    assert routed_response.json()["detail"] == (
        "runtime sync is only available for physical managed nodes"
    )


def test_xray_runtime_credential_reconciliation_reports_client_email_drift(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-runtime-credentials", "domain": "edge.example.com"},
    ).json()
    server_id = created["server"]["id"]
    command = client.post(f"/api/v1/servers/{server_id}/operations/scan").json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                **scan_result_payload(),
                "inbounds": [
                    {
                        "tag": "vless-443",
                        "port": 443,
                        "protocol": "vless",
                        "settings": {
                            "clients": [
                                {
                                    "id": "secret-runtime-client-id",
                                    "email": "alice__vless-443",
                                },
                                {
                                    "id": "secret-orphan-client-id",
                                    "email": "orphan@example.com",
                                },
                            ]
                        },
                        "streamSettings": {"network": "tcp", "security": "tls"},
                    }
                ],
            },
        },
    )
    assert result.status_code == 200
    node = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/nodes",
        json={"source_index": 0},
    ).json()["node"]
    for username in ["alice", "bob"]:
        assert (
            client.post(
                "/api/v1/users",
                json={"username": username, "display_name": username.title()},
            ).status_code
            == 201
        )
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Runtime users",
            "traffic_limit_gb": 64,
            "node_ids": [node["id"]],
        },
    ).json()["plan"]
    alice = client.post("/api/v1/users/alice/plan", json={"plan_id": plan["id"]}).json()
    bob = client.post("/api/v1/users/bob/plan", json={"plan_id": plan["id"]}).json()
    alice_id = alice["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"]["id"]
    bob_id = bob["provisioning_batches"][0]["body"]["inbound_clients"][0]["client"]["id"]

    response = client.get(
        f"/api/v1/servers/{server_id}/xray/runtime/credentials/reconciliation"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_scan"] is True
    assert payload["node_count"] == 1
    assert payload["expected_credential_count"] == 2
    assert payload["matched_runtime_client_count"] == 2
    assert payload["in_sync_count"] == 0
    assert payload["missing_runtime_count"] == 0
    assert payload["out_of_sync_count"] == 1
    assert payload["missing_runtime_client_count"] == 1
    assert payload["extra_runtime_client_count"] == 1
    assert payload["entries"] == [
        {
            "node_id": node["id"],
            "node_name": "edge-runtime-credentials vless-443",
            "protocol": "vless",
            "inbound_tag": "vless-443",
            "enabled": True,
            "runtime_source_index": 0,
            "runtime_display_name": "vless-443",
            "expected_emails": ["alice__vless-443", "bob__vless-443"],
            "runtime_emails": ["alice__vless-443", "orphan@example.com"],
            "missing_runtime_emails": ["bob__vless-443"],
            "extra_runtime_emails": ["orphan@example.com"],
            "status": "drift",
        }
    ]
    serialized = json.dumps(payload)
    assert alice_id not in serialized
    assert bob_id not in serialized
    assert "secret-runtime-client-id" not in serialized
    assert "secret-orphan-client-id" not in serialized

    repair = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/credentials/repair-missing",
        json={},
    )

    assert repair.status_code == 200
    repair_payload = repair.json()
    assert repair_payload["license_required"] is False
    assert repair_payload["has_scan"] is True
    assert repair_payload["planned_client_count"] == 1
    assert repair_payload["batch_count"] == 1
    assert repair_payload["commands"] == []
    assert repair_payload["scan_command"] is None
    assert repair_payload["entries"] == [
        {
            "node_id": node["id"],
            "node_name": "edge-runtime-credentials vless-443",
            "protocol": "vless",
            "inbound_tag": "vless-443",
            "runtime_source_index": 0,
            "runtime_display_name": "vless-443",
            "emails": ["bob__vless-443"],
        }
    ]
    assert repair_payload["provisioning_batches"][0]["body"] == {
        "inbound_clients": [
            {
                "tag": "vless-443",
                "client": {
                    "id": bob_id,
                    "email": "bob__vless-443",
                    "level": 0,
                },
            }
        ],
        "routing_user_additions": [],
        "no_restart": True,
    }
    repair_serialized = json.dumps(repair_payload)
    assert alice_id not in repair_serialized
    assert "secret-runtime-client-id" not in repair_serialized
    assert "secret-orphan-client-id" not in repair_serialized
    assert "orphan@example.com" not in repair_serialized

    cleanup = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/credentials/cleanup-extra",
        json={},
    )

    assert cleanup.status_code == 200
    cleanup_payload = cleanup.json()
    assert cleanup_payload["license_required"] is False
    assert cleanup_payload["has_scan"] is True
    assert cleanup_payload["planned_client_count"] == 1
    assert cleanup_payload["command_count"] == 1
    assert cleanup_payload["commands"] == []
    assert cleanup_payload["scan_command"] is None
    assert cleanup_payload["entries"] == [
        {
            "node_id": node["id"],
            "node_name": "edge-runtime-credentials vless-443",
            "protocol": "vless",
            "inbound_tag": "vless-443",
            "runtime_source_index": 0,
            "runtime_display_name": "vless-443",
            "emails": ["orphan@example.com"],
        }
    ]
    assert cleanup_payload["command_previews"] == [
        {
            "node_id": node["id"],
            "node_name": "edge-runtime-credentials vless-443",
            "body": {
                "action": "remove-client",
                "tag": "vless-443",
                "client": {"email": "orphan@example.com"},
            },
        }
    ]
    cleanup_serialized = json.dumps(cleanup_payload)
    assert alice_id not in cleanup_serialized
    assert bob_id not in cleanup_serialized
    assert "secret-runtime-client-id" not in cleanup_serialized
    assert "secret-orphan-client-id" not in cleanup_serialized

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-runtime-credentials",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"
        queued = client.post(
            f"/api/v1/servers/{server_id}/xray/runtime/credentials/repair-missing",
            json={
                "queue_agent_commands": True,
                "queue_scan_after_apply": True,
                "no_restart": False,
                "command_timeout_ms": 75_000,
            },
        )
        assert queued.status_code == 200
        queued_payload = queued.json()
        assert len(queued_payload["commands"]) == 1
        assert queued_payload["commands"][0]["status"] == "leased"
        assert queued_payload["commands"][0]["path"] == "/api/child/batch-apply"
        assert queued_payload["commands"][0]["body"]["no_restart"] is False
        assert queued_payload["scan_command"]["status"] == "waiting"
        assert queued_payload["scan_command"]["path"] == "/api/child/scan"
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/batch-apply"
        assert rpc_call["payload"]["timeout_ms"] == 75_000
        assert rpc_call["payload"]["body"] == queued_payload["provisioning_batches"][0]["body"]
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": rpc_call["payload"]["request_id"], "status": 200,
            "body": {"success": True},
        }})
        assert websocket.receive_json()["type"] == "rpc_reply_ack"
        scan_rpc = websocket.receive_json()
        assert scan_rpc["type"] == "rpc_call"
        assert scan_rpc["payload"]["path"] == "/api/child/scan"
        assert scan_rpc["payload"]["timeout_ms"] == 75_000
        assert scan_rpc["payload"]["body"] is None
        assert websocket.receive_json()["payload"]["query"] == "snapshot_source=master_write"

        cleanup_queued = client.post(
            f"/api/v1/servers/{server_id}/xray/runtime/credentials/cleanup-extra",
            json={
                "queue_agent_commands": True,
                "queue_scan_after_apply": True,
                "command_timeout_ms": 45_000,
            },
        )
        assert cleanup_queued.status_code == 200
        cleanup_queued_payload = cleanup_queued.json()
        assert len(cleanup_queued_payload["commands"]) == 1
        assert cleanup_queued_payload["commands"][0]["status"] == "leased"
        assert cleanup_queued_payload["commands"][0]["path"] == "/api/child/inbounds"
        assert cleanup_queued_payload["commands"][0]["body"] == {
            "action": "remove-client",
            "tag": "vless-443",
            "client": {"email": "orphan@example.com"},
        }
        assert cleanup_queued_payload["scan_command"]["status"] == "waiting"
        assert cleanup_queued_payload["scan_command"]["path"] == "/api/child/scan"
        cleanup_rpc = websocket.receive_json()
        assert cleanup_rpc["type"] == "rpc_call"
        assert cleanup_rpc["payload"]["path"] == "/api/child/inbounds"
        assert cleanup_rpc["payload"]["timeout_ms"] == 45_000
        assert cleanup_rpc["payload"]["body"] == cleanup_queued_payload["command_previews"][0][
            "body"
        ]
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": cleanup_rpc["payload"]["request_id"], "status": 200,
            "body": {"success": True},
        }})
        assert websocket.receive_json()["type"] == "rpc_reply_ack"
        cleanup_scan_rpc = websocket.receive_json()
        assert cleanup_scan_rpc["type"] == "rpc_call"
        assert cleanup_scan_rpc["payload"]["path"] == "/api/child/scan"
        assert cleanup_scan_rpc["payload"]["timeout_ms"] == 45_000
        assert cleanup_scan_rpc["payload"]["body"] is None


def test_agent_traffic_alias_updates_system_derived_speed(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-traffic"}).json()
    first_at = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
    second_at = first_at + timedelta(seconds=10)

    first = client.post(
        "/api/v1/agents/traffic",
        json={
            "token": created["agent_token"],
            "reported_at": first_at.isoformat(),
            "system": {"rx_total": 1_000, "tx_total": 2_000, "boot_time_unix": 900},
        },
    )
    second = client.post(
        "/api/v1/agents/traffic",
        json={
            "token": created["agent_token"],
            "reported_at": second_at.isoformat(),
            "system": {"rx_total": 1_120, "tx_total": 2_250, "boot_time_unix": 900},
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["server"]["current_download_speed"] == 12
    assert second.json()["server"]["current_upload_speed"] == 25


def test_public_probe_servers_returns_sanitized_mmwx_probe_payload(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={
            "name": "edge-probe",
            "ip_address": "203.0.113.55",
            "traffic_limit": 10_000,
        },
    ).json()
    first_at = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(seconds=20)
    second_at = first_at + timedelta(seconds=20)
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": created["agent_token"],
            "reported_at": first_at.isoformat(),
            "stats": {
                "inbound": {"proxy-in": {"uplink": 40, "downlink": 80}},
                "outbound": {},
                "user": {},
            },
            "system": {"rx_total": 1_000, "tx_total": 2_000, "boot_time_unix": 123},
        },
    )
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": created["agent_token"],
            "reported_at": second_at.isoformat(),
            "stats": {
                "inbound": {"proxy-in": {"uplink": 100, "downlink": 200}},
                "outbound": {},
                "user": {},
            },
            "system": {"rx_total": 1_600, "tx_total": 3_000, "boot_time_unix": 123},
            "sysmetrics": {
                "cpu_pct": 35.5,
                "loadavg": "0.40 0.50 0.60",
                "mem_used": 2048,
                "mem_total": 4096,
                "disk_used": 8192,
                "disk_total": 16384,
                "uptime": 456,
                "cpu_model": "Open Node CPU",
                "cpu_cores": 2,
                "cpu_threads": 4,
                "os": "Debian",
                "kernel": "6.1",
                "arch": "amd64",
                "has_cpu": True,
                "has_mem": True,
                "has_disk": True,
            },
            "latency": [
                {"key": "cmcc", "success": True, "latency_ms": 31, "at": int(second_at.timestamp())}
            ],
        },
    )

    response = client.get("/api/v1/public/probe-servers")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["license_required"] is False
    assert payload["title"] == "Open Node Probe"
    server = payload["servers"][0]
    assert server["name"] == "edge-probe"
    assert server["online"] is True
    assert server["upload_speed"] == 50
    assert server["download_speed"] == 30
    assert server["traffic_limit"] == 10_000
    assert server["traffic_used_up"] == 100
    assert server["traffic_used_down"] == 200
    assert server["traffic_used_total"] == 300
    assert len(server["daily_traffic"]) == 7
    current_day = next(
        item for item in server["daily_traffic"] if item["date"] == second_at.date().isoformat()
    )
    assert current_day == {
        "date": second_at.date().isoformat(),
        "uplink": 60,
        "downlink": 120,
        "total": 180,
    }
    assert server["cumulative_up"] == 3_000
    assert server["cumulative_down"] == 1_600
    assert server["cpu_pct"] == 35.5
    assert server["mem_used"] == 2048
    assert server["disk_total"] == 16384
    assert server["ping"][0]["key"] == "cmcc"
    assert server["ping"][0]["current_ms"] == 31
    assert len(server["ping"][0]["buckets"]) == 12
    assert "id" not in server
    assert "ip_address" not in server
    assert "agent_token" not in server


def test_public_probe_settings_customize_payload_and_disable_servers(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/servers", json={"name": "edge-probe-settings"})

    defaults = client.get("/api/v1/public/probe-settings")
    assert defaults.status_code == 200
    assert defaults.json()["settings"]["title"] == "Open Node Probe"
    assert defaults.json()["license_required"] is False

    updated = client.put(
        "/api/v1/public/probe-settings",
        json={
            "enabled": False,
            "title": "MMWX Public Status",
            "description": "Public node telemetry",
            "logo": "https://example.com/logo.png",
            "show_resource_heatmap": False,
            "show_traffic_quota": False,
            "show_renewal_timeline": True,
            "show_return_route": True,
            "show_health_score": False,
            "refresh_interval_sec": 2,
            "appearance": {
                "theme": "compact",
                "color_mode": "dark",
                "revision": "probe-r2",
            },
        },
    )

    assert updated.status_code == 200
    settings = updated.json()["settings"]
    assert settings["enabled"] is False
    assert settings["title"] == "MMWX Public Status"
    assert settings["description"] == "Public node telemetry"
    assert settings["show_resource_heatmap"] is False
    assert settings["show_traffic_quota"] is False
    assert settings["show_renewal_timeline"] is True
    assert settings["show_return_route"] is True
    assert settings["refresh_interval_sec"] == 2
    assert settings["appearance"] == {
        "theme": "compact",
        "color_mode": "dark",
        "revision": "probe-r2",
    }
    assert settings["updated_at"]
    assert updated.json()["license_required"] is False

    payload = client.get("/api/v1/public/probe-servers").json()
    assert payload["enabled"] is False
    assert payload["title"] == "MMWX Public Status"
    assert payload["description"] == "Public node telemetry"
    assert payload["refresh_interval_sec"] == 2
    assert payload["servers"] == []
    assert payload["license_required"] is False
    assert client.get("/api/v1/public/probe-series?server=0").status_code == 404

    alias = client.get("/api/public/probe-settings")
    assert alias.status_code == 200
    assert alias.json()["settings"]["title"] == "MMWX Public Status"


def test_probe_access_token_gate_allows_worker_header_and_hides_direct_access(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/servers", json={"name": "edge-probe-token"})

    defaults = client.get("/api/v1/public/probe-settings").json()["settings"]
    assert defaults["has_access_token"] is False
    assert defaults["require_access_token"] is False

    created = client.post("/api/v1/probe/access-token")

    assert created.status_code == 200
    created_payload = created.json()
    token = created_payload["token"]
    assert token.startswith("probe_")
    assert created_payload["license_required"] is False
    assert created_payload["settings"]["has_access_token"] is True
    assert created_payload["settings"]["require_access_token"] is True
    assert "access_token_hash" not in created_payload["settings"]

    direct = client.get("/api/v1/public/probe-servers")
    bad_token = client.get(
        "/api/v1/public/probe-servers",
        headers={"X-MMwx-Probe-Token": "probe_wrong"},
    )
    worker = client.get(
        "/api/v1/public/probe-servers",
        headers={"X-MMwx-Probe-Token": token},
    )
    worker_alias = client.get(
        "/api/public/probe-servers",
        headers={"X-MMwx-Probe-Token": token},
    )

    assert direct.status_code == 404
    assert direct.json() == {"success": False, "license_required": False}
    assert bad_token.status_code == 404
    assert worker.status_code == 200
    assert worker.json()["servers"][0]["name"] == "edge-probe-token"
    assert worker.json()["license_required"] is False
    assert worker_alias.status_code == 200

    cleared = client.delete("/api/v1/probe/access-token")
    direct_after_clear = client.get("/api/v1/public/probe-servers")

    assert cleared.status_code == 200
    assert cleared.json()["settings"]["has_access_token"] is False
    assert cleared.json()["settings"]["require_access_token"] is False
    assert direct_after_clear.status_code == 200


def test_probe_access_token_gate_protects_series_targets_and_websocket(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-token-stream"}).json()
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": created["agent_token"],
            "latency": [{"key": "ct-shanghai", "success": True, "latency_ms": 37}],
        },
    )
    token = client.post("/api/v1/probe/access-token").json()["token"]
    worker_headers = {"X-MMwx-Probe-Token": token}

    direct_series = client.get("/api/v1/public/probe-series?server=0&target=ct-shanghai")
    worker_series = client.get(
        "/api/v1/public/probe-series?server=0&target=ct-shanghai",
        headers=worker_headers,
    )
    direct_targets = client.get("/api/v1/public/probe-targets?range=1h")
    worker_targets = client.get("/api/v1/public/probe-targets?range=1h", headers=worker_headers)

    assert direct_series.status_code == 404
    assert direct_series.json() == {"success": False, "license_required": False}
    assert worker_series.status_code == 200
    assert worker_series.json()["series"]["current_ms"] == 37
    assert direct_targets.status_code == 404
    assert worker_targets.status_code == 200
    assert worker_targets.json()["targets"][0]["key"] == "ct-shanghai"

    with client.websocket_connect("/api/v1/public/probe-ws") as websocket:
        denied_payload = websocket.receive_json()

    assert denied_payload == {
        "success": False,
        "error": "probe access denied",
        "license_required": False,
    }

    with client.websocket_connect(
        "/api/v1/public/probe-ws",
        headers=worker_headers,
    ) as websocket:
        payload = websocket.receive_json()

    assert payload["servers"][0]["name"] == "edge-token-stream"
    assert payload["servers"][0]["ping"][0]["current_ms"] == 37
    assert payload["license_required"] is False


def test_public_probe_series_uses_public_index_and_aggregates_latency(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-probe-series"}).json()
    first_at = datetime.now(tz=UTC).replace(microsecond=0) - timedelta(minutes=10)
    second_at = first_at + timedelta(minutes=5)
    for reported_at, latency_ms in [(first_at, 88), (second_at, 42)]:
        response = client.post(
            "/api/v1/agents/telemetry",
            json={
                "token": created["agent_token"],
                "reported_at": reported_at.isoformat(),
                "latency": [
                    {
                        "key": "ct-shanghai",
                        "success": True,
                        "latency_ms": latency_ms,
                        "at": int(reported_at.timestamp()),
                    }
                ],
            },
        )
        assert response.status_code == 200

    response = client.get(
        "/api/v1/public/probe-series?server=0&range=1h&target=ct-shanghai&all=1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["license_required"] is False
    assert payload["bucket_sec"] == 300
    assert payload["generated_at"] > 0
    assert payload["series"]["key"] == "ct-shanghai"
    assert payload["series"]["current_ms"] == 42
    assert len(payload["series"]["buckets"]) == 12
    assert payload["all_series"][0]["key"] == "ct-shanghai"
    assert "server_id" not in payload


def test_probe_tasks_dispatch_due_agent_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-probe-tasks"}).json()
    server_id = created["server"]["id"]

    latency = client.post(
        "/api/v1/probe/tasks",
        json={
            "server_id": server_id,
            "kind": "domain_latency",
            "interval_sec": 60,
            "domains": [" https://example.com/path", "example.com", "[2001:db8::1]"],
            "domain_timeout_ms": 500,
            "allow_icmp": True,
            "command_timeout_ms": 12_000,
        },
    )
    routes = client.post(
        "/api/v1/probe/tasks",
        json={
            "server_id": server_id,
            "kind": "return_route",
            "interval_sec": 3600,
            "return_route_targets": [
                {"carrier": "telecom", "region": "Guangzhou", "host": "ct.example"},
                {"carrier": "unicom", "region": "Shanghai", "host": "cu.example"},
            ],
            "return_route_timeout_seconds": 20,
        },
    )
    system = client.post(
        "/api/v1/probe/tasks",
        json={"server_id": server_id, "kind": "system", "interval_sec": 300},
    )

    assert latency.status_code == 201
    assert routes.status_code == 201
    assert system.status_code == 201
    assert latency.json()["license_required"] is False
    assert latency.json()["task"]["domains"] == ["example.com", "2001:db8::1"]

    listed = client.get("/api/v1/probe/tasks")
    assert listed.status_code == 200
    assert listed.json()["license_required"] is False
    assert len(listed.json()["tasks"]) == 3

    dispatch = client.post("/api/v1/probe/tasks/dispatch-due")

    assert dispatch.status_code == 200
    payload = dispatch.json()
    assert payload["license_required"] is False
    commands = [item["command"] for item in payload["dispatched"]]
    assert [command["path"] for command in commands] == [
        "/api/child/domains/latency",
        "/api/child/network/return-route-test",
        "/api/child/system/info",
    ]
    assert commands[0]["body"] == {
        "domains": ["example.com", "2001:db8::1"],
        "timeout_ms": 500,
        "allow_icmp": True,
    }
    assert commands[1]["body"] == {
        "ip_version": 4,
        "timeout_seconds": 20,
        "targets": [
            {"carrier": "telecom", "region": "Guangzhou", "host": "ct.example", "port": 80},
            {"carrier": "unicom", "region": "Shanghai", "host": "cu.example", "port": 80},
        ],
    }
    assert all(command["status"] == "pending" for command in commands)
    assert all(item["task"]["last_dispatched_at"] for item in payload["dispatched"])

    second_dispatch = client.post("/api/v1/probe/tasks/dispatch-due")
    assert second_dispatch.status_code == 200
    assert second_dispatch.json()["dispatched"] == []

    invalid_task_update = client.patch(
        f"/api/v1/probe/tasks/{latency.json()['task']['id']}",
        json={"domains": []},
    )
    assert invalid_task_update.status_code == 422


def test_domain_latency_command_result_updates_public_probe_series(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-latency-result"}).json()
    server_id = created["server"]["id"]

    command = client.post(
        f"/api/v1/servers/{server_id}/operations/domain-latency",
        json={"domains": ["example.com", "down.example"]},
    ).json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})

    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                "success": True,
                "results": [
                    {
                        "domain": "example.com",
                        "target": "example.com:443",
                        "success": True,
                        "latency_ms": 57,
                    },
                    {
                        "domain": "down.example",
                        "target": "down.example:443",
                        "success": False,
                        "error": "dial timeout",
                    },
                ],
            },
        },
    )

    assert result.status_code == 200
    assert result.json()["command"]["status"] == "succeeded"

    series = client.get(
        "/api/v1/public/probe-series?server=0&range=1h&target=example.com&all=1"
    )

    assert series.status_code == 200
    payload = series.json()
    assert payload["license_required"] is False
    assert payload["series"]["key"] == "example.com"
    assert payload["series"]["current_ms"] == 57
    by_key = {item["key"]: item for item in payload["all_series"]}
    assert by_key["example.com"]["loss_pct"] == 0
    assert by_key["down.example"]["current_ms"] == -1
    assert by_key["down.example"]["loss_pct"] == 100
    assert "server_id" not in payload


def test_public_probe_targets_compare_latency_across_public_nodes(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    edge_a = client.post(
        "/api/v1/servers",
        json={"name": "edge-a", "region": "jp-tokyo", "ip_address": "203.0.113.10"},
    ).json()
    edge_b = client.post(
        "/api/v1/servers",
        json={"name": "edge-b", "region_city": "Singapore", "ip_address": "203.0.113.11"},
    ).json()
    reported_at = datetime.now(tz=UTC).replace(microsecond=0)

    for edge, latency_ms, success in [(edge_a, 31, True), (edge_b, 0, False)]:
        response = client.post(
            "/api/v1/agents/telemetry",
            json={
                "token": edge["agent_token"],
                "reported_at": reported_at.isoformat(),
                "latency": [
                    {
                        "key": "ct-shanghai",
                        "success": success,
                        "latency_ms": latency_ms,
                        "at": int(reported_at.timestamp()),
                    }
                ],
            },
        )
        assert response.status_code == 200

    response = client.get("/api/v1/public/probe-targets?range=1h")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["license_required"] is False
    assert payload["bucket_sec"] == 300
    assert payload["generated_at"] > 0
    assert payload["targets"] == [
        {
            "key": "ct-shanghai",
            "label": "ct-shanghai",
            "server_count": 2,
            "healthy_count": 1,
            "average_ms": 31,
            "best_ms": 31,
            "worst_ms": 31,
            "average_loss_pct": 50,
            "servers": [
                {
                    "server_index": 0,
                    "server_name": "edge-a",
                    "region": "jp-tokyo",
                    "current_ms": 31,
                    "loss_pct": 0,
                    "buckets": payload["targets"][0]["servers"][0]["buckets"],
                },
                {
                    "server_index": 1,
                    "server_name": "edge-b",
                    "region": "Singapore",
                    "current_ms": -1,
                    "loss_pct": 100,
                    "buckets": payload["targets"][0]["servers"][1]["buckets"],
                },
            ],
        }
    ]
    assert len(payload["targets"][0]["servers"][0]["buckets"]) == 12
    assert "server_id" not in payload
    assert "ip_address" not in payload["targets"][0]["servers"][0]


def test_public_probe_mmwx_worker_alias_is_available(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/servers", json={"name": "edge-probe-alias"})

    response = client.get("/api/public/probe-servers")

    assert response.status_code == 200
    assert response.json()["servers"][0]["name"] == "edge-probe-alias"


def test_public_probe_websocket_alias_streams_sanitized_payload(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-probe-ws", "ip_address": "203.0.113.44"},
    ).json()
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": created["agent_token"],
            "latency": [{"key": "cmcc", "success": True, "latency_ms": 24}],
        },
    )

    with client.websocket_connect("/api/public/probe-ws") as websocket:
        payload = websocket.receive_json()

    assert payload["enabled"] is True
    assert payload["license_required"] is False
    assert payload["servers"][0]["name"] == "edge-probe-ws"
    assert payload["servers"][0]["ping"][0]["current_ms"] == 24
    assert "ip_address" not in payload["servers"][0]
    assert "agent_token" not in payload["servers"][0]


def test_public_probe_websocket_v1_route_is_available(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/v1/servers", json={"name": "edge-probe-ws-v1"})

    with client.websocket_connect("/api/v1/public/probe-ws") as websocket:
        payload = websocket.receive_json()

    assert payload["servers"][0]["name"] == "edge-probe-ws-v1"
    assert payload["license_required"] is False


def test_public_probe_series_unknown_index_returns_public_404(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/public/probe-series?server=99")

    assert response.status_code == 404
    assert response.json() == {"success": False, "license_required": False}


def test_invalid_telemetry_token_is_rejected_as_auth_not_license(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": "not-a-real-token",
            "system": {"rx_total": 1, "tx_total": 2, "boot_time_unix": 3},
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid agent token"


def test_latest_telemetry_for_unknown_server_returns_404(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/v1/servers/00000000-0000-0000-0000-000000000000/telemetry/latest")

    assert response.status_code == 404
    assert response.json()["detail"] == "server not found: 00000000-0000-0000-0000-000000000000"


def test_agent_command_queue_runs_without_license_header(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-command"}).json()
    server_id = created["server"]["id"]

    command_response = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={
            "method": "post",
            "path": "/api/child/xray/test-config",
            "body": {"config": {"log": {"loglevel": "warning"}}},
            "timeout_ms": 5000,
        },
    )

    assert command_response.status_code == 201
    command = command_response.json()["command"]
    assert command_response.json()["license_required"] is False
    assert command["status"] == "pending"
    assert command["method"] == "POST"
    assert command["attempts"] == 0

    lease_response = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": created["agent_token"], "max_commands": 1},
    )

    assert lease_response.status_code == 200
    leased = lease_response.json()["commands"][0]
    assert leased["id"] == command["id"]
    assert leased["request_id"]
    assert leased["status"] == "leased"
    assert leased["attempts"] == 1

    result_response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"ok": True},
        },
    )

    assert result_response.status_code == 200
    completed = result_response.json()["command"]
    assert result_response.json()["license_required"] is False
    assert completed["status"] == "succeeded"
    assert completed["result_status"] == 200
    assert completed["result_body"] == {"ok": True}

    commands = client.get(f"/api/v1/servers/{server_id}/commands")
    assert commands.status_code == 200
    assert commands.json()["commands"][0]["status"] == "succeeded"


def test_agent_command_result_error_marks_command_failed(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-command-fail"}).json()
    server_id = created["server"]["id"]
    command = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={"method": "GET", "path": "/api/child/system/info"},
    ).json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})

    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 500,
            "body": {"error": "boom"},
            "error": "agent handler panic",
        },
    )

    assert response.status_code == 200
    assert response.json()["command"]["status"] == "failed"
    assert response.json()["command"]["result_error"] == "agent handler panic"


def test_return_route_command_result_updates_public_probe_badges(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-routes", "telecom_paid_peer": True},
    ).json()
    server_id = created["server"]["id"]

    command = client.post(
        f"/api/v1/servers/{server_id}/operations/network/return-route-test",
        json={
            "targets": [
                {"carrier": "telecom", "region": "Guangzhou", "host": "ct.example"},
                {"carrier": "unicom", "region": "Shanghai", "host": "cu.example"},
                {"carrier": "mobile", "region": "Beijing", "host": "cm.example"},
            ],
        },
    ).json()["command"]
    client.post("/api/v1/agents/commands/lease", json={"token": created["agent_token"]})

    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                "success": True,
                "results": [
                    {
                        "carrier": "mobile",
                        "region": "Beijing",
                        "success": True,
                        "route_type": "CMIN",
                        "entry_hop": {"ip": "198.51.100.1", "asn": "58807"},
                        "reason": "private diagnostic detail",
                    },
                    {
                        "carrier": "telecom",
                        "region": "Guangzhou",
                        "success": True,
                        "route_type": "163",
                        "entry_hop": {"ip": "198.51.100.2", "asn": "4134"},
                    },
                    {
                        "carrier": "unicom",
                        "region": "Shanghai",
                        "success": False,
                        "error": "trace timeout",
                    },
                    {"carrier": "unknown", "route_type": "ignored"},
                ],
            },
        },
    )
    assert result.status_code == 200

    client.put("/api/v1/public/probe-settings", json={"show_return_route": True})
    server = client.get("/api/v1/public/probe-servers").json()["servers"][0]

    assert server["return_routes"] == [
        {
            "carrier": "telecom",
            "region": "Guangzhou",
            "route_type": "163",
            "tested_at": server["return_routes"][0]["tested_at"],
        },
        {
            "carrier": "unicom",
            "region": "Shanghai",
            "route_type": "Unknown",
            "tested_at": server["return_routes"][1]["tested_at"],
        },
        {
            "carrier": "mobile",
            "region": "Beijing",
            "route_type": "CMIN",
            "tested_at": server["return_routes"][2]["tested_at"],
        },
    ]
    assert server["return_routes"][0]["tested_at"]
    assert "entry_ip" not in server["return_routes"][0]
    assert "entry_asn" not in server["return_routes"][0]
    assert "reason" not in server["return_routes"][0]


def test_system_info_operation_queues_mmwx_child_command(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-operation"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/system-info"
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["license_required"] is False
    command = payload["command"]
    assert command["method"] == "GET"
    assert command["path"] == "/api/child/system/info"
    assert command["stream"] is False


def test_pull_data_operations_queue_mmwx_child_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-pull-ops"}).json()
    server_id = created["server"]["id"]

    traffic = client.post(f"/api/v1/servers/{server_id}/operations/traffic")
    speed = client.post(f"/api/v1/servers/{server_id}/operations/speed")

    assert traffic.status_code == 201
    assert speed.status_code == 201
    assert traffic.json()["command"]["path"] == "/api/child/traffic"
    assert speed.json()["command"]["path"] == "/api/child/speed"
    commands = client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
    assert [command["path"] for command in commands] == [
        "/api/child/speed",
        "/api/child/traffic",
    ]


def test_domain_latency_operation_normalizes_probe_targets(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-domain-op"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/domain-latency",
        json={
            "domains": [
                " https://example.com/path",
                "example.com",
                "[2001:db8::1]",
            ],
            "timeout_ms": 500,
            "allow_icmp": True,
            "command_timeout_ms": 12_000,
        },
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/domains/latency"
    assert command["timeout_ms"] == 12_000
    assert command["body"] == {
        "domains": ["example.com", "2001:db8::1"],
        "timeout_ms": 500,
        "allow_icmp": True,
    }


def test_diagnostic_operations_queue_mmwx_child_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-diagnostics"}).json()
    server_id = created["server"]["id"]

    operations = [
        ("services/status", "GET", "/api/child/services/status"),
        ("system/nics", "GET", "/api/child/system/nics"),
        ("logs/files/list", "GET", "/api/child/logs/files"),
        ("scan", "POST", "/api/child/scan"),
    ]
    responses = [
        client.post(f"/api/v1/servers/{server_id}/operations/{operation}")
        for operation, _method, _path in operations
    ]

    assert all(response.status_code == 201 for response in responses)
    for response, (_operation, expected_method, expected_path) in zip(
        responses,
        operations,
        strict=True,
    ):
        command = response.json()["command"]
        assert response.json()["license_required"] is False
        assert command["method"] == expected_method
        assert command["path"] == expected_path
        assert command["stream"] is False


def test_service_control_operation_queues_valid_body(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-service-control"}).json()
    server_id = created["server"]["id"]

    response = client.post(
        f"/api/v1/servers/{server_id}/operations/services/control",
        json={"service": "nginx", "action": "restart"},
    )
    invalid = client.post(
        f"/api/v1/servers/{server_id}/operations/services/control",
        json={"service": "agent", "action": "restart"},
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/services/control"
    assert command["body"] == {"service": "nginx", "action": "restart"}
    assert invalid.status_code == 422


def test_logs_operation_builds_safe_query(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-logs"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/logs",
        json={"service": "xray", "lines": 500},
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "GET"
    assert command["path"] == "/api/child/logs"
    assert command["query"] == "service=xray&lines=500"


def test_log_file_delete_operation_builds_safe_agent_query(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-log-files"}).json()
    server_id = created["server"]["id"]

    single = client.post(
        f"/api/v1/servers/{server_id}/operations/logs/files/delete",
        json={"name": "mmw-agent.log.1", "command_timeout_ms": 15_000},
    )
    all_files = client.post(
        f"/api/v1/servers/{server_id}/operations/logs/files/delete",
        json={"all": True},
    )
    missing_target = client.post(
        f"/api/v1/servers/{server_id}/operations/logs/files/delete",
        json={},
    )
    invalid_name = client.post(
        f"/api/v1/servers/{server_id}/operations/logs/files/delete",
        json={"name": "../mmw-agent.log"},
    )

    assert single.status_code == 201
    command = single.json()["command"]
    assert command["method"] == "DELETE"
    assert command["path"] == "/api/child/logs/files"
    assert command["query"] == "name=mmw-agent.log.1"
    assert command["timeout_ms"] == 15_000
    assert all_files.status_code == 201
    assert all_files.json()["command"]["query"] == "all=1"
    assert missing_target.status_code == 422
    assert invalid_name.status_code == 422


def test_xray_test_config_operation_serializes_structured_config(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-test-config"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/xray/test-config",
        json={
            "config": {"log": {"loglevel": "warning"}},
            "command_timeout_ms": 15_000,
        },
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/xray/test-config"
    assert command["timeout_ms"] == 15_000
    assert json.loads(command["body"]["config"]) == {"log": {"loglevel": "warning"}}


def test_config_read_operations_queue_mmwx_child_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-config-read"}).json()
    server_id = created["server"]["id"]

    operations = [
        ("xray/config/read", "/api/child/xray/config"),
        ("xray/system-config/read", "/api/child/xray/system-config"),
        ("xray/config-files/list", "/api/child/xray/config-files"),
        ("inbounds/list", "/api/child/inbounds"),
        ("outbounds/list", "/api/child/outbounds"),
        ("routing/read", "/api/child/routing"),
        ("nginx/config/read", "/api/child/nginx/config"),
        ("nginx/config-files/list", "/api/child/nginx/config-files"),
        ("nginx/servers-list", "/api/child/nginx/servers-list"),
        ("nginx/websites/list", "/api/child/nginx/websites"),
    ]
    responses = [
        client.post(f"/api/v1/servers/{server_id}/operations/{operation}")
        for operation, _path in operations
    ]

    assert all(response.status_code == 201 for response in responses)
    for response, (_operation, expected_path) in zip(responses, operations, strict=True):
        command = response.json()["command"]
        assert response.json()["license_required"] is False
        assert command["method"] == "GET"
        assert command["path"] == expected_path


def test_xray_runtime_manage_operations_queue_agent_schemas(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-runtime-manage"}).json()
    server_id = created["server"]["id"]

    inbound = client.post(
        f"/api/v1/servers/{server_id}/operations/inbounds/manage",
        json={
            "action": "ADD-CLIENT",
            "tag": "vless-443",
            "client": {"id": "uuid-1", "email": "user@example.com"},
            "domains": ["https://Example.COM/path"],
            "command_timeout_ms": 40_000,
        },
    )
    outbound = client.post(
        f"/api/v1/servers/{server_id}/operations/outbounds/manage",
        json={"action": "reorder", "tags": ["direct", "proxy"]},
    )
    routing = client.post(
        f"/api/v1/servers/{server_id}/operations/routing/manage",
        json={
            "action": "set",
            "routing": {"rules": []},
            "observatory": None,
            "burst_observatory": {"subjectSelector": ["proxy"]},
            "no_restart": True,
            "command_timeout_ms": 45_000,
        },
    )

    assert inbound.status_code == 201
    assert outbound.status_code == 201
    assert routing.status_code == 201
    assert inbound.json()["command"]["path"] == "/api/child/inbounds"
    assert inbound.json()["command"]["body"] == {
        "action": "add-client",
        "tag": "vless-443",
        "client": {"id": "uuid-1", "email": "user@example.com"},
        "domains": ["example.com"],
    }
    assert inbound.json()["command"]["timeout_ms"] == 40_000
    assert outbound.json()["command"]["path"] == "/api/child/outbounds"
    assert outbound.json()["command"]["body"] == {
        "action": "reorder",
        "tags": ["direct", "proxy"],
    }
    assert routing.json()["command"]["path"] == "/api/child/routing"
    assert routing.json()["command"]["body"] == {
        "action": "set",
        "routing": {"rules": []},
        "index": 0,
        "no_restart": True,
        "observatory": None,
        "burstObservatory": {"subjectSelector": ["proxy"]},
    }
    assert routing.json()["command"]["timeout_ms"] == 45_000


def test_batch_cert_site_route_limiter_operations_queue_agent_schemas(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-high-level"}).json()
    server_id = created["server"]["id"]

    batch = client.post(
        f"/api/v1/servers/{server_id}/operations/batch-apply",
        json={
            "inbound_clients": [
                {"tag": "vless-443", "client": {"id": "uuid-1", "email": "u@example.com"}}
            ],
            "routing_user_additions": [
                {"marktag": "route-proxy", "outbound_tag": "proxy", "user_email": "u@example.com"}
            ],
            "no_restart": True,
        },
    )
    cert = client.post(
        f"/api/v1/servers/{server_id}/operations/cert/deploy",
        json={
            "domain": "example.com",
            "cert_pem": "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
            "key_pem": "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----",
            "cert_path": "/etc/nginx/cert/example.com.pem",
            "key_path": "/etc/nginx/cert/example.com.key",
            "reload": "nginx",
            "command_timeout_ms": 70_000,
        },
    )
    setup_ssl = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/setup-ssl",
        json={"domain": "EXAMPLE.com", "domain_config": "server { listen 443 ssl; }"},
    )
    delete_site = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/websites/delete",
        json={"domain": "https://example.com/path"},
    )
    return_route = client.post(
        f"/api/v1/servers/{server_id}/operations/network/return-route-test",
        json={
            "ip_version": 4,
            "timeout_seconds": 20,
            "targets": [{"carrier": "TELECOM", "region": "gz", "host": "1.1.1.1"}],
        },
    )
    validate_site = client.post(
        f"/api/v1/servers/{server_id}/operations/validate-site",
        json={"site_type": "proxy", "site_value": "https://example.com"},
    )
    limiter = client.post(
        f"/api/v1/servers/{server_id}/operations/limiter",
        json={
            "inbound_tag": "vless-443",
            "node_limit": 1048576,
            "users": [
                {
                    "uid": 1001,
                    "email": "u@example.com",
                    "speed_limit": 1024,
                    "device_limit": 2,
                }
            ],
            "auto_speed_rules": [{"threshold": 10}],
        },
    )

    assert batch.status_code == 201
    assert cert.status_code == 201
    assert setup_ssl.status_code == 201
    assert delete_site.status_code == 201
    assert return_route.status_code == 201
    assert validate_site.status_code == 201
    assert limiter.status_code == 201
    assert batch.json()["command"]["path"] == "/api/child/batch-apply"
    assert batch.json()["command"]["body"]["no_restart"] is True
    assert cert.json()["command"]["path"] == "/api/child/cert/deploy"
    assert cert.json()["command"]["body"]["reload"] == "nginx"
    assert cert.json()["command"]["timeout_ms"] == 70_000
    assert setup_ssl.json()["command"]["path"] == "/api/child/nginx/setup-ssl"
    assert setup_ssl.json()["command"]["body"] == {
        "domain": "example.com",
        "domain_config": "server { listen 443 ssl; }",
    }
    assert delete_site.json()["command"]["method"] == "DELETE"
    assert delete_site.json()["command"]["path"] == "/api/child/nginx/websites"
    assert delete_site.json()["command"]["body"] == {"domain": "example.com"}
    assert return_route.json()["command"]["path"] == "/api/child/network/return-route-test"
    assert return_route.json()["command"]["body"]["targets"][0]["carrier"] == "telecom"
    assert validate_site.json()["command"]["path"] == "/api/child/validate-site"
    assert validate_site.json()["command"]["body"] == {
        "site_type": "proxy",
        "site_value": "https://example.com",
    }
    assert limiter.json()["command"]["path"] == "/api/child/limiter"
    assert limiter.json()["command"]["body"]["users"][0]["device_limit"] == 2


def test_xray_config_write_operation_serializes_structured_config(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-config"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/xray/config/write",
        json={
            "config": {"inbounds": [], "outbounds": []},
            "path": "/usr/local/etc/xray/config.json",
            "force": True,
            "command_timeout_ms": 20_000,
        },
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/xray/config"
    assert command["body"]["path"] == "/usr/local/etc/xray/config.json"
    assert command["body"]["force"] is True
    assert json.loads(command["body"]["config"]) == {"inbounds": [], "outbounds": []}
    assert command["timeout_ms"] == 20_000


def test_xray_config_command_results_record_snapshots_and_restore(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-snapshots"}).json()
    server_id = created["server"]["id"]

    config_text = '{\n  "inbounds": [{"tag": "vless-443"}],\n  "outbounds": []\n}'
    read_command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    read_result = client.post(
        f"/api/v1/agents/commands/{read_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                "success": True,
                "path": "/usr/local/etc/xray/config.json",
                "config": config_text,
            },
        },
    )

    assert read_result.status_code == 200
    snapshot_response = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots?with_config=true",
    )
    assert snapshot_response.status_code == 200
    snapshot_payload = snapshot_response.json()
    assert snapshot_payload["license_required"] is False
    assert len(snapshot_payload["snapshots"]) == 1
    current = snapshot_payload["snapshots"][0]
    assert current["status"] == "current"
    assert current["source"] == "agent_report"
    assert current["source_command_id"] == read_command["id"]
    assert current["config"] == config_text
    assert current["config_hash"] == hashlib.sha256(config_text.encode()).hexdigest()
    assert current["size_bytes"] == len(config_text.encode())

    write_config = '{"inbounds":[],"outbounds":[{"tag":"direct","protocol":"freedom"}]}'
    write_command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/write",
        json={"config": write_config},
    ).json()["command"]
    write_result = client.post(
        f"/api/v1/agents/commands/{write_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "message": "Config saved successfully"},
        },
    )

    assert write_result.status_code == 200
    snapshots = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots?with_config=true",
    ).json()["snapshots"]
    assert [item["status"] for item in snapshots] == ["current", "old"]
    assert snapshots[0]["source"] == "master_write"
    assert snapshots[0]["source_command_id"] == write_command["id"]
    assert snapshots[0]["config"] == write_config

    old_snapshot = snapshots[1]
    restore = client.post(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/{old_snapshot['id']}/restore",
    )

    assert restore.status_code == 201
    restore_payload = restore.json()
    assert restore_payload["license_required"] is False
    restore_command = restore_payload["command"]
    assert restore_command["method"] == "POST"
    assert restore_command["path"] == "/api/child/xray/config"
    assert restore_command["body"] == {"config": config_text}
    assert restore_command["timeout_ms"] == 60_000


def test_xray_config_agent_drift_creates_pending_recovery_and_accepts(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-pending"}).json()
    server_id = created["server"]["id"]
    current_config = '{"inbounds":[{"tag":"vless-443"}],"outbounds":[]}'
    drift_config = '{"inbounds":[{"tag":"vless-443"},{"tag":"trojan-8443"}],"outbounds":[]}'

    current_command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    current_result = client.post(
        f"/api/v1/agents/commands/{current_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "config": current_config},
        },
    )
    assert current_result.status_code == 200

    drift_command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    drift_result = client.post(
        f"/api/v1/agents/commands/{drift_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "config": drift_config},
        },
    )
    assert drift_result.status_code == 200

    recovery = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery?with_config=true",
    )
    assert recovery.status_code == 200
    recovery_payload = recovery.json()
    assert recovery_payload["license_required"] is False
    assert recovery_payload["has_current"] is True
    assert recovery_payload["has_pending"] is True
    assert recovery_payload["current"]["status"] == "current"
    assert recovery_payload["current"]["config"] == current_config
    assert recovery_payload["pending"]["status"] == "pending_recovery"
    assert recovery_payload["pending"]["source"] == "agent_report"
    assert recovery_payload["pending"]["source_command_id"] == drift_command["id"]
    assert recovery_payload["pending"]["config"] == drift_config

    listed = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots?with_config=true",
    ).json()["snapshots"]
    assert {snapshot["status"] for snapshot in listed} == {"current", "pending_recovery"}

    accepted = client.post(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery/accept",
    )
    assert accepted.status_code == 200
    accepted_payload = accepted.json()
    assert accepted_payload["license_required"] is False
    assert accepted_payload["current"]["status"] == "current"
    assert accepted_payload["current"]["source"] == "manual_accept"
    assert accepted_payload["current"]["config_hash"] == hashlib.sha256(
        drift_config.encode()
    ).hexdigest()
    assert {snapshot["status"] for snapshot in accepted_payload["snapshots"]} == {
        "current",
        "old",
    }

    recovery_after_accept = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery",
    ).json()
    assert recovery_after_accept["has_pending"] is False
    assert recovery_after_accept["current"]["source"] == "manual_accept"


def test_xray_config_recovery_apply_queues_current_and_discards_pending_on_success(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-apply"}).json()
    server_id = created["server"]["id"]
    current_config = json.dumps(
        {
            "inbounds": [{"tag": "vless-443", "port": 443}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            "routing": {"rules": [{"outboundTag": "direct"}]},
        },
        separators=(",", ":"),
    )
    drift_config = json.dumps(
        {
            "inbounds": [
                {"tag": "vless-443", "port": 443},
                {"tag": "agent-only-inbound", "port": 8443},
            ],
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "agent-only-outbound", "protocol": "freedom"},
            ],
            "routing": {
                "rules": [
                    {"outboundTag": "direct"},
                    {"outboundTag": "agent-only-outbound"},
                ]
            },
        },
        separators=(",", ":"),
    )

    for config_text in (current_config, drift_config):
        command = client.post(
            f"/api/v1/servers/{server_id}/operations/xray/config/read",
        ).json()["command"]
        result = client.post(
            f"/api/v1/agents/commands/{command['id']}/result",
            json={
                "token": created["agent_token"],
                "status": 200,
                "body": {"success": True, "config": config_text},
            },
        )
        assert result.status_code == 200

    recovery_before_apply = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery",
    ).json()
    assert recovery_before_apply["has_pending"] is True

    applied = client.post(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery/apply",
        json={"restart_xray": True, "command_timeout_ms": 45_000},
    )
    assert applied.status_code == 201
    applied_payload = applied.json()
    assert applied_payload["license_required"] is False
    assert applied_payload["snapshot"]["status"] == "current"
    assert applied_payload["snapshot"]["config"] is None
    assert applied_payload["command_count"] == 3
    assert applied_payload["merged_agent_only_count"] == 2
    assert applied_payload["warnings"] == []
    assert [command["path"] for command in applied_payload["commands"]] == [
        "/api/child/xray/test-config",
        "/api/child/xray/config",
        "/api/child/services/control",
    ]
    assert all(command["timeout_ms"] == 45_000 for command in applied_payload["commands"])
    merged_config = json.loads(applied_payload["commands"][0]["body"]["config"])
    assert [inbound["tag"] for inbound in merged_config["inbounds"]] == [
        "vless-443",
        "agent-only-inbound",
    ]
    assert [outbound["tag"] for outbound in merged_config["outbounds"]] == [
        "direct",
        "agent-only-outbound",
    ]
    assert merged_config["routing"]["rules"] == [{"outboundTag": "direct"}]
    assert applied_payload["commands"][1]["body"] == {
        "config": applied_payload["commands"][0]["body"]["config"],
        "force": True,
    }
    assert applied_payload["commands"][2]["body"] == {
        "service": "xray",
        "action": "restart",
    }

    config_command = applied_payload["commands"][1]
    validation = applied_payload["commands"][0]
    assert client.post(
        f"/api/v1/agents/commands/{validation['id']}/result",
        json={"token": created["agent_token"], "status": 200, "body": {"ok": True}},
    ).status_code == 200
    config_result = client.post(
        f"/api/v1/agents/commands/{config_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "message": "Config saved successfully"},
        },
    )
    assert config_result.status_code == 200

    recovery_after_apply = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery",
    ).json()
    assert recovery_after_apply["has_pending"] is False
    assert recovery_after_apply["current"]["config_hash"] == hashlib.sha256(
        applied_payload["commands"][0]["body"]["config"].encode()
    ).hexdigest()


@pytest.mark.parametrize("status, body", [
    (200, {"ok": False}), (200, {"ok": "true"}), (200, {"success": True}),
    (500, {"ok": True}), (200, {"ok": True, "success": False}),
])
def test_recovery_validation_failure_skips_writes_and_rejects_early_results(
    tmp_path: Path, status: int, body: dict,
) -> None:
    client = make_client(tmp_path)
    created, applied = queue_recovery(client)
    validation, write, restart = applied["commands"]
    assert [command["status"] for command in applied["commands"]] == [
        "pending", "waiting", "waiting",
    ]
    assert write["depends_on_command_id"] == validation["id"]
    assert restart["depends_on_command_id"] == write["id"]
    assert client.app.state.inventory.lease_command_for_push(UUID(write["id"])) is None
    token = created["agent_token"]
    early = client.post(
        f"/api/v1/agents/commands/{write['id']}/result",
        json={"token": token, "status": 200, "body": {"success": True}},
    )
    assert early.status_code == 409
    leased = client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10},
    ).json()["commands"]
    assert [command["id"] for command in leased] == [validation["id"]]
    failed = client.post(
        f"/api/v1/agents/commands/{validation['id']}/result",
        json={"token": token, "status": status, "body": body},
    )
    assert failed.json()["command"]["status"] == "failed"
    late = client.post(
        f"/api/v1/agents/commands/{validation['id']}/result",
        json={"token": token, "status": 200, "body": {"ok": True}},
    )
    assert late.json()["command"]["status"] == "failed"
    commands = client.get(f"/api/v1/servers/{created['server']['id']}/commands").json()["commands"]
    for command in commands:
        if command["id"] in {write["id"], restart["id"]}:
            assert command["status"] == "skipped"
            assert command["attempts"] == 0
            assert command["completed_at"]
            assert "prerequisite" in command["result_error"]
    assert client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10},
    ).json()["commands"] == []
    snapshots = client.get(
        f"/api/v1/servers/{created['server']['id']}/xray/config-snapshots",
    ).json()["snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["source"] == "agent_report"


def test_recovery_dependencies_persist_across_restart_and_release_one_step_at_a_time(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created, applied = queue_recovery(client)
    token = created["agent_token"]
    validation, write, restart = applied["commands"]
    client = make_client(tmp_path)
    assert [command["id"] for command in client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10},
    ).json()["commands"]] == [validation["id"]]
    assert client.post(
        f"/api/v1/agents/commands/{validation['id']}/result",
        json={"token": token, "status": 200, "body": {"ok": True}},
    ).status_code == 200
    client = make_client(tmp_path)
    assert [command["id"] for command in client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10},
    ).json()["commands"]] == [write["id"]]
    assert client.post(
        f"/api/v1/agents/commands/{write['id']}/result",
        json={"token": token, "status": 200, "body": {"success": True}},
    ).status_code == 200
    client = make_client(tmp_path)
    leased = client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10},
    ).json()["commands"]
    assert restart["id"] in {command["id"] for command in leased}
    assert all(command["id"] not in {validation["id"], write["id"]} for command in leased)


@pytest.mark.parametrize("write_succeeds", [True, False])
def test_websocket_recovery_waits_for_validation_and_write_results(
    tmp_path: Path, write_succeeds: bool,
) -> None:
    client = make_client(tmp_path)
    created, applied = queue_recovery(client)
    validation, write, restart = applied["commands"]
    with client.websocket_connect("/api/remote/ws") as websocket:
        websocket.send_json({"type": "auth", "payload": {
            "token": created["agent_token"], "capabilities": {"rpc": True},
        }})
        assert websocket.receive_json()["type"] == "auth_result"
        assert websocket.receive_json()["payload"]["request_id"] == validation["request_id"]
        startup = websocket.receive_json()["payload"]
        assert startup["path"] == "/api/child/xray/config"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": write["request_id"], "status": 200, "body": {"success": True},
        }})
        assert websocket.receive_json()["type"] == "error"
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": validation["request_id"], "status": 200, "body": {"ok": True},
        }})
        assert websocket.receive_json()["type"] == "rpc_reply_ack"
        assert websocket.receive_json()["payload"]["request_id"] == write["request_id"]
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": write["request_id"], "status": 200,
            "body": {"success": write_succeeds},
        }})
        ack = websocket.receive_json()
        assert ack["payload"]["status"] == ("succeeded" if write_succeeds else "failed")
        if write_succeeds:
            assert websocket.receive_json()["payload"]["request_id"] == restart["request_id"]
            assert websocket.receive_json()["payload"]["query"] == "snapshot_source=master_write"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"
    commands = client.get(
        f"/api/v1/servers/{created['server']['id']}/commands",
    ).json()["commands"]
    final_restart = next(command for command in commands if command["id"] == restart["id"])
    assert final_restart["status"] == ("leased" if write_succeeds else "skipped")
    assert final_restart["attempts"] == (1 if write_succeeds else 0)
    if not write_succeeds:
        assert all(command["query"] != "snapshot_source=master_write" for command in commands)


def test_concurrent_validation_results_have_one_terminal_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(tmp_path)
    created, applied = queue_recovery(client)
    store = client.app.state.inventory
    validation, write, restart = applied["commands"]
    original_apply = store._apply_command_result
    barrier = Barrier(2)

    def concurrent_apply(session, server, command, payload):
        barrier.wait(timeout=5)
        original_apply(session, server, command, payload)

    monkeypatch.setattr(store, "_apply_command_result", concurrent_apply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(
            store.complete_command,
            UUID(validation["id"]),
            AgentCommandResultRequest(token=created["agent_token"], status=200, body={"ok": ok}),
        ) for ok in (True, False)]
        outcomes = [result.result(timeout=10).status for result in results]
    assert len(set(outcomes)) == 1
    commands = {
        str(command.id): command
        for command in store.list_commands(UUID(created["server"]["id"]))
    }
    assert commands[validation["id"]].status == outcomes[0]
    if outcomes[0] == "succeeded":
        assert commands[write["id"]].status == "pending"
        assert commands[restart["id"]].status == "waiting"
    else:
        assert commands[write["id"]].status == "skipped"
        assert commands[restart["id"]].status == "skipped"


def test_existing_sqlite_commands_migrate_without_losing_history(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-schema"}).json()
    command = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/system-info",
    ).json()["command"]
    legacy = Table(
        "agent_commands", MetaData(),
        *[
            Column(
                column.name, column.type, primary_key=column.primary_key, nullable=column.nullable,
            )
            for column in CommandModel.__table__.columns
            if column.name != "depends_on_command_id"
        ],
    )
    with client.app.state.inventory._engine.begin() as connection:
        row = connection.execute(select(CommandModel.__table__)).mappings().one()
        CommandModel.__table__.drop(connection)
        legacy.create(connection)
        connection.execute(legacy.insert().values(
            {column.name: row[column.name] for column in legacy.columns}
        ))
    upgraded = make_client(tmp_path)
    migrated = upgraded.get(
        f"/api/v1/servers/{created['server']['id']}/commands",
    ).json()["commands"]
    assert len(migrated) == 1
    assert migrated[0]["id"] == command["id"]
    assert migrated[0]["depends_on_command_id"] is None
    assert migrated[0]["status"] == "pending"
    _, recovery = queue_recovery(upgraded)
    assert recovery["commands"][1]["depends_on_command_id"] == recovery["commands"][0]["id"]


def test_xray_mutating_commands_queue_deduped_master_snapshot_refresh(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-refresh"}).json()
    server_id = created["server"]["id"]
    current_config = '{"inbounds":[{"tag":"vless-443"}],"outbounds":[]}'
    drift_config = '{"inbounds":[],"outbounds":[]}'
    refreshed_config = (
        '{"inbounds":[{"tag":"vless-443"},{"tag":"trojan-8443"}],"outbounds":[]}'
    )

    for config_text in (current_config, drift_config):
        command = client.post(
            f"/api/v1/servers/{server_id}/operations/xray/config/read",
        ).json()["command"]
        result = client.post(
            f"/api/v1/agents/commands/{command['id']}/result",
            json={
                "token": created["agent_token"],
                "status": 200,
                "body": {"success": True, "config": config_text},
            },
        )
        assert result.status_code == 200

    assert client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery",
    ).json()["has_pending"] is True

    mutation = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={
            "method": "POST",
            "path": "/api/child/inbounds",
            "body": {"action": "add-client", "tag": "vless-443"},
        },
    ).json()["command"]
    mutation_result = client.post(
        f"/api/v1/agents/commands/{mutation['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True},
        },
    )
    assert mutation_result.status_code == 200

    second_mutation = client.post(
        f"/api/v1/servers/{server_id}/commands",
        json={
            "method": "POST",
            "path": "/api/child/routing",
            "body": {"action": "add-rule"},
        },
    ).json()["command"]
    second_mutation_result = client.post(
        f"/api/v1/agents/commands/{second_mutation['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True},
        },
    )
    assert second_mutation_result.status_code == 200

    commands = client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
    refresh_commands = [
        command
        for command in commands
        if command["method"] == "GET"
        and command["path"] == "/api/child/xray/config"
        and command["query"] == "snapshot_source=master_write"
    ]
    assert len(refresh_commands) == 1
    refresh_command = refresh_commands[0]
    assert refresh_command["status"] == "pending"
    assert refresh_command["timeout_ms"] == 60_000

    refresh_result = client.post(
        f"/api/v1/agents/commands/{refresh_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "config": refreshed_config},
        },
    )
    assert refresh_result.status_code == 200

    recovery_after_refresh = client.get(
        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery",
    ).json()
    assert recovery_after_refresh["has_pending"] is False
    assert recovery_after_refresh["current"]["source"] == "master_write"
    assert recovery_after_refresh["current"]["config_hash"] == hashlib.sha256(
        refreshed_config.encode()
    ).hexdigest()


def test_xray_runtime_tunnel_inventory_reads_current_config_snapshot(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-tunnels"}).json()
    server_id = created["server"]["id"]
    config_text = json.dumps(
        {
            "inbounds": [
                {
                    "tag": "tunnel-in",
                    "protocol": "tunnel",
                    "port": 1000,
                    "settings": {"address": "127.0.0.1", "port": 1001},
                },
                {
                    "tag": "api",
                    "protocol": "tunnel",
                    "port": 1002,
                    "settings": {"address": "127.0.0.1", "port": 1003},
                },
                {
                    "tag": "tunnel-web",
                    "protocol": "tunnel",
                    "port": 18080,
                    "settings": {"address": "10.0.0.2", "port": 80, "network": "tcp"},
                },
                {
                    "tag": "tunnel-relay-h1",
                    "protocol": "tunnel",
                    "port": 19001,
                    "settings": {"address": "10.0.0.3", "port": 9001},
                },
                {
                    "tag": "tunnel-relay-h0",
                    "protocol": "tunnel",
                    "port": 19000,
                    "settings": {"address": "10.0.0.2", "port": 9000},
                },
                {"tag": "vless-443", "protocol": "vless", "port": 443},
            ],
            "outbounds": [
                {
                    "tag": "tunnel-routed",
                    "protocol": "freedom",
                    "settings": {"redirect": "[2001:db8::10]:443"},
                },
                {
                    "tag": "direct",
                    "protocol": "freedom",
                    "settings": {"password": "secret-should-not-leak"},
                },
            ],
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": ["vless-443"],
                        "outboundTag": "tunnel-routed",
                        "domain": ["example.com"],
                        "ip": ["1.1.1.1"],
                    },
                    {"type": "field", "outboundTag": "direct", "domain": ["ignored.test"]},
                ]
            },
        }
    )
    read_command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    result = client.post(
        f"/api/v1/agents/commands/{read_command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "config": config_text},
        },
    )

    assert result.status_code == 200
    response = client.get(f"/api/v1/servers/{server_id}/xray/runtime/tunnels")
    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_config"] is True
    assert payload["source_snapshot_id"]
    assert payload["tunnel_count"] == 2
    assert payload["chain_count"] == 1
    assert payload["warnings"] == []

    inbound = next(item for item in payload["tunnels"] if item["kind"] == "inbound")
    routed = next(item for item in payload["tunnels"] if item["kind"] == "routed")
    assert inbound == {
        "kind": "inbound",
        "tag": "tunnel-web",
        "listen_port": 18080,
        "target_address": "10.0.0.2",
        "target_port": 80,
        "network": "tcp",
        "inbound_tag": None,
        "match_domains": [],
        "match_ips": [],
        "rule_index": None,
    }
    assert routed["tag"] == "tunnel-routed"
    assert routed["listen_port"] == 443
    assert routed["target_address"] == "2001:db8::10"
    assert routed["target_port"] == 443
    assert routed["inbound_tag"] == "vless-443"
    assert routed["match_domains"] == ["example.com"]
    assert routed["match_ips"] == ["1.1.1.1"]
    assert routed["rule_index"] == 0

    chain = payload["chains"][0]
    assert chain["label"] == "relay"
    assert chain["entry_port"] == 19000
    assert chain["final_target"] == "10.0.0.3:9001"
    assert [hop["tag"] for hop in chain["hops"]] == ["tunnel-relay-h0", "tunnel-relay-h1"]
    assert "secret-should-not-leak" not in json.dumps(payload)

    routed_preview = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnels/delete",
        json={"kind": "routed", "tag": "tunnel-routed", "rule_index": 0},
    )
    assert routed_preview.status_code == 200
    routed_payload = routed_preview.json()
    assert routed_payload["license_required"] is False
    assert routed_payload["has_config"] is True
    assert routed_payload["command_count"] == 2
    assert routed_payload["commands"] == []
    assert routed_payload["scan_command"] is None
    assert routed_payload["command_previews"] == [
        {
            "method": "POST",
            "path": "/api/child/routing",
            "body": {"action": "remove_rule", "index": 0},
        },
        {
            "method": "POST",
            "path": "/api/child/outbounds",
            "body": {"action": "remove", "tag": "tunnel-routed"},
        },
    ]

    stale_index = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnels/delete",
        json={"kind": "routed", "tag": "tunnel-routed", "rule_index": 3},
    )
    assert stale_index.status_code == 404
    assert "rule index changed" in stale_index.json()["detail"]

    queued_chain = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnels/delete",
        json={
            "kind": "chain",
            "label": "relay",
            "queue_agent_commands": True,
            "queue_scan_after_apply": True,
            "command_timeout_ms": 45_000,
        },
    )
    assert queued_chain.status_code == 200
    queued_payload = queued_chain.json()
    assert queued_payload["target_kind"] == "chain"
    assert queued_payload["target_label"] == "relay"
    assert queued_payload["command_count"] == 2
    assert [command["path"] for command in queued_payload["commands"]] == [
        "/api/child/inbounds",
        "/api/child/inbounds",
    ]
    assert [command["body"]["tag"] for command in queued_payload["commands"]] == [
        "tunnel-relay-h0",
        "tunnel-relay-h1",
    ]
    assert all(command["timeout_ms"] == 45_000 for command in queued_payload["commands"])
    assert queued_payload["scan_command"]["path"] == "/api/child/scan"
    assert queued_payload["scan_command"]["timeout_ms"] == 45_000


def test_xray_runtime_tunnel_chain_create_plans_and_queues_hops(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    entry = client.post(
        "/api/v1/servers",
        json={"name": "chain-entry", "ip_address": "198.51.100.10"},
    ).json()
    middle = client.post(
        "/api/v1/servers",
        json={"name": "chain-middle", "domain": "middle.example.com"},
    ).json()
    exit_server = client.post(
        "/api/v1/servers",
        json={
            "name": "chain-exit",
            "ip_address": "2001:db8::20",
            "domain": "exit.example.com",
        },
    ).json()

    def record_config(server: dict[str, object], config: dict[str, object]) -> None:
        server_id = server["server"]["id"]
        command = client.post(
            f"/api/v1/servers/{server_id}/operations/xray/config/read",
        ).json()["command"]
        result = client.post(
            f"/api/v1/agents/commands/{command['id']}/result",
            json={
                "token": server["agent_token"],
                "status": 200,
                "body": {"success": True, "config": json.dumps(config)},
            },
        )
        assert result.status_code == 200

    record_config(entry, {"inbounds": [{"tag": "vless-443", "port": 443}]})
    record_config(middle, {"inbounds": [{"tag": "occupied", "port": 19000}]})
    record_config(exit_server, {"inbounds": []})

    request_body = {
        "label": "Relay-1",
        "server_ids": [
            entry["server"]["id"],
            middle["server"]["id"],
            exit_server["server"]["id"],
        ],
        "entry_port": 19000,
        "target_address": "service.internal",
        "target_port": 443,
    }
    preview = client.post("/api/v1/servers/xray/runtime/tunnel-chains", json=request_body)

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["license_required"] is False
    assert payload["label"] == "relay-1"
    assert payload["entry_server_id"] == entry["server"]["id"]
    assert payload["entry_host"] == "198.51.100.10"
    assert payload["entry_port"] == 19000
    assert payload["final_target"] == "service.internal:443"
    assert payload["command_count"] == 3
    assert payload["commands"] == []
    assert payload["scan_commands"] == []
    assert payload["warnings"] == ["chain-middle:port_19000_in_use"]
    assert payload["hops"] == [
        {
            "server_id": entry["server"]["id"],
            "server_name": "chain-entry",
            "tag": "tunnel-relay-1-h0",
            "listen_port": 19000,
            "target_address": "middle.example.com",
            "target_port": 20000,
        },
        {
            "server_id": middle["server"]["id"],
            "server_name": "chain-middle",
            "tag": "tunnel-relay-1-h1",
            "listen_port": 20000,
            "target_address": "2001:db8::20",
            "target_port": 19000,
        },
        {
            "server_id": exit_server["server"]["id"],
            "server_name": "chain-exit",
            "tag": "tunnel-relay-1-h2",
            "listen_port": 19000,
            "target_address": "service.internal",
            "target_port": 443,
        },
    ]
    assert [
        preview["body"]["inbound"]["settings"]
        for preview in payload["command_previews"]
    ] == [
        {"address": "middle.example.com", "port": 20000, "network": "tcp,udp"},
        {"address": "2001:db8::20", "port": 19000, "network": "tcp,udp"},
        {"address": "service.internal", "port": 443, "network": "tcp,udp"},
    ]

    queued = client.post(
        "/api/v1/servers/xray/runtime/tunnel-chains",
        json={
            **request_body,
            "queue_agent_commands": True,
            "queue_scan_after_apply": True,
            "command_timeout_ms": 45_000,
        },
    )

    assert queued.status_code == 200
    queued_payload = queued.json()
    assert queued_payload["command_count"] == 3
    assert [command["server_id"] for command in queued_payload["commands"]] == [
        entry["server"]["id"],
        middle["server"]["id"],
        exit_server["server"]["id"],
    ]
    assert [command["path"] for command in queued_payload["commands"]] == [
        "/api/child/inbounds",
        "/api/child/inbounds",
        "/api/child/inbounds",
    ]
    assert [command["body"]["inbound"]["tag"] for command in queued_payload["commands"]] == [
        "tunnel-relay-1-h0",
        "tunnel-relay-1-h1",
        "tunnel-relay-1-h2",
    ]
    assert all(command["timeout_ms"] == 45_000 for command in queued_payload["commands"])
    assert [command["server_id"] for command in queued_payload["scan_commands"]] == [
        entry["server"]["id"],
        middle["server"]["id"],
        exit_server["server"]["id"],
    ]
    assert all(
        command["path"] == "/api/child/scan"
        for command in queued_payload["scan_commands"]
    )
    assert all(command["timeout_ms"] == 45_000 for command in queued_payload["scan_commands"])
    for command, scan in zip(
        queued_payload["commands"], queued_payload["scan_commands"], strict=True,
    ):
        assert scan["status"] == "waiting"
        assert scan["depends_on_command_id"] == command["id"]


def test_xray_runtime_tunnel_deploy_plans_template_and_requires_force(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={
            "name": "edge-tunnel-deploy",
            "domain": "Gateway.EXAMPLE.com",
            "pull_address": "gateway-proxy.example.com",
        },
    ).json()
    server_id = created["server"]["id"]
    command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                "success": True,
                "config": json.dumps(
                    {
                        "inbounds": [{"tag": "vless-443", "port": 443}],
                        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
                    }
                ),
            },
        },
    )
    assert result.status_code == 200

    request_body = {
        "site_type": "proxy",
        "site_value": "http://127.0.0.1:12889",
        "cert_name": "*.example.com",
        "queue_scan_after_apply": True,
    }
    preview = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnel-deploy",
        json=request_body,
    )

    assert preview.status_code == 200
    payload = preview.json()
    assert payload["license_required"] is False
    assert payload["domain"] == "gateway.example.com"
    assert payload["proxy_domain"] == "gateway-proxy.example.com"
    assert payload["cert_name"] == "_.example.com"
    assert payload["command_count"] == 4
    assert payload["commands"] == []
    assert payload["scan_command_preview"]["path"] == "/api/child/scan"
    assert payload["warnings"] == ["current_config_has_user_content"]
    assert [command["path"] for command in payload["command_previews"]] == [
        "/api/child/nginx/clear-stream-port",
        "/api/child/nginx/setup-ssl",
        "/api/child/xray/config",
        "/api/child/services/control",
    ]
    setup_body = payload["command_previews"][1]["body"]
    assert setup_body["domain"] == "gateway.example.com"
    assert "include stream_servers/*.conf" in setup_body["nginx_config"]
    assert "server_name                gateway.example.com;" in setup_body["domain_config"]
    assert "proxy_pass              http://127.0.0.1:12889;" in setup_body["domain_config"]
    assert "cert/_.example.com.pem" in setup_body["domain_config"]

    config_body = payload["command_previews"][2]["body"]
    xray_config = json.loads(config_body["config"])
    assert xray_config["inbounds"][0]["tag"] == "tunnel-in"
    assert xray_config["inbounds"][0]["port"] == 443
    assert xray_config["inbounds"][0]["settings"]["port"] == 46_174
    assert xray_config["routing"]["rules"][0] == {
        "inboundTag": ["tunnel-in"],
        "domain": ["gateway.example.com"],
        "outboundTag": "nginx",
    }
    assert xray_config["routing"]["rules"][1] == {
        "inboundTag": ["tunnel-in"],
        "outboundTag": "direct",
    }

    blocked = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnel-deploy",
        json={**request_body, "queue_agent_commands": True},
    )
    assert blocked.status_code == 400

    queued = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnel-deploy",
        json={
            **request_body,
            "queue_agent_commands": True,
            "force": True,
            "command_timeout_ms": 45_000,
        },
    )

    assert queued.status_code == 200
    queued_payload = queued.json()
    assert [command["path"] for command in queued_payload["commands"]] == [
        "/api/child/nginx/clear-stream-port",
        "/api/child/nginx/setup-ssl",
        "/api/child/xray/config",
        "/api/child/services/control",
    ]
    assert all(command["timeout_ms"] == 45_000 for command in queued_payload["commands"])
    assert queued_payload["scan_command"]["path"] == "/api/child/scan"
    assert queued_payload["scan_command"]["timeout_ms"] == 45_000
    sequence = [*queued_payload["commands"], queued_payload["scan_command"]]
    assert [command["status"] for command in sequence] == ["pending", *(["waiting"] * 4)]
    for previous, following in zip(sequence[:-1], sequence[1:], strict=True):
        assert following["depends_on_command_id"] == previous["id"]
    failed = client.post(
        f"/api/v1/agents/commands/{sequence[0]['id']}/result",
        json={"token": created["agent_token"], "status": 200, "body": {"success": False}},
    )
    assert failed.json()["command"]["status"] == "failed"
    commands = client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
    for command in commands:
        if command["id"] in {item["id"] for item in sequence[1:]}:
            assert command["status"] == "skipped"
            assert command["attempts"] == 0


def test_xray_runtime_tunnel_deploy_protects_existing_tunnel_inbounds(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post(
        "/api/v1/servers",
        json={"name": "edge-existing-tunnel", "domain": "edge.example.com"},
    ).json()
    server_id = created["server"]["id"]
    command = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {
                "success": True,
                "config": json.dumps(
                    {
                        "inbounds": [
                            {
                                "tag": "runtime-chain-hop",
                                "protocol": "tunnel",
                                "port": 20_001,
                            }
                        ],
                        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
                    }
                ),
            },
        },
    )
    assert result.status_code == 200

    preview = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnel-deploy",
        json={},
    )
    assert preview.status_code == 200
    assert preview.json()["warnings"] == ["current_config_has_user_content"]

    blocked = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnel-deploy",
        json={"queue_agent_commands": True},
    )
    assert blocked.status_code == 400


def test_xray_runtime_tunnel_inventory_handles_missing_config_snapshot(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-no-tunnels"}).json()
    server_id = created["server"]["id"]

    response = client.get(f"/api/v1/servers/{server_id}/xray/runtime/tunnels")

    assert response.status_code == 200
    payload = response.json()
    assert payload["license_required"] is False
    assert payload["has_config"] is False
    assert payload["tunnels"] == []
    assert payload["chains"] == []

    delete_response = client.post(
        f"/api/v1/servers/{server_id}/xray/runtime/tunnels/delete",
        json={"kind": "inbound", "tag": "tunnel-web"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["warnings"] == ["current_config_snapshot_not_found"]
    assert delete_response.json()["command_previews"] == []


def test_xray_config_snapshots_ignore_empty_and_failed_results(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-snapshot-empty"}).json()
    server_id = created["server"]["id"]

    empty_read = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/read",
    ).json()["command"]
    failed_write = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config/write",
        json={"config": {"inbounds": []}},
    ).json()["command"]

    empty_result = client.post(
        f"/api/v1/agents/commands/{empty_read['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True, "config": "   "},
        },
    )
    failed_result = client.post(
        f"/api/v1/agents/commands/{failed_write['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 400,
            "body": {"success": False},
            "error": "invalid config",
        },
    )

    assert empty_result.status_code == 200
    assert failed_result.status_code == 200
    snapshots = client.get(f"/api/v1/servers/{server_id}/xray/config-snapshots")
    assert snapshots.status_code == 200
    assert snapshots.json()["snapshots"] == []


def test_xray_system_config_write_queues_agent_schema(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-xray-system"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/xray/system-config/write",
        json={
            "metrics_enabled": True,
            "metrics_listen": "127.0.0.1:11111",
            "stats_enabled": True,
            "grpc_enabled": True,
            "grpc_port": 46736,
        },
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/xray/system-config"
    assert command["body"] == {
        "metrics_enabled": True,
        "metrics_listen": "127.0.0.1:11111",
        "stats_enabled": True,
        "grpc_enabled": True,
        "grpc_port": 46736,
    }


def test_config_file_operations_build_queries_and_bodies(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-config-files"}).json()
    server_id = created["server"]["id"]

    xray_read = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config-files/read",
        json={"file": "routing.json"},
    )
    xray_write = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config-files/write",
        json={"file": "routing.json", "content": {"routing": {"rules": []}}},
    )
    nginx_read = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/config-files/read",
        json={"file": "/etc/nginx/conf.d/site.conf"},
    )
    nginx_write = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/config-files/write",
        json={"path": "/etc/nginx/conf.d/site.conf", "content": "server { listen 80; }"},
    )
    invalid_xray_file = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config-files/read",
        json={"file": "../config.json"},
    )

    assert xray_read.status_code == 201
    assert xray_write.status_code == 201
    assert nginx_read.status_code == 201
    assert nginx_write.status_code == 201
    assert invalid_xray_file.status_code == 422
    assert xray_read.json()["command"]["query"] == "file=routing.json"
    assert xray_write.json()["command"]["body"]["file"] == "routing.json"
    assert json.loads(xray_write.json()["command"]["body"]["content"]) == {
        "routing": {"rules": []}
    }
    assert (
        nginx_read.json()["command"]["query"]
        == "file=%2Fetc%2Fnginx%2Fconf.d%2Fsite.conf"
    )
    assert nginx_write.json()["command"]["path"] == "/api/child/nginx/config-files"
    assert nginx_write.json()["command"]["body"] == {
        "path": "/etc/nginx/conf.d/site.conf",
        "content": "server { listen 80; }",
    }


def test_xray_takeover_external_operation_queues_agent_schema(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-takeover-xray"}).json()

    default_response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/xray/takeover-external",
    )
    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/xray/takeover-external",
        json={"command_timeout_ms": 180_000},
    )

    assert default_response.status_code == 201
    assert default_response.json()["command"]["timeout_ms"] == 120_000
    assert response.status_code == 201
    payload = response.json()
    command = payload["command"]
    assert payload["license_required"] is False
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/external-xray/takeover"
    assert command["stream"] is False
    assert command["body"] is None
    assert command["timeout_ms"] == 180_000


def test_nginx_config_write_operation_queues_text_config(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-nginx-config"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/nginx/config/write",
        json={
            "config": "events {}\nhttp {}",
            "path": "/etc/nginx/nginx.conf",
            "command_timeout_ms": 25_000,
        },
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/nginx/config"
    assert command["body"] == {
        "config": "events {}\nhttp {}",
        "path": "/etc/nginx/nginx.conf",
    }
    assert command["timeout_ms"] == 25_000


def test_warp_license_and_agent_settings_operations_queue_agent_commands(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-agent-settings"}).json()
    server_id = created["server"]["id"]

    warp = client.post(
        f"/api/v1/servers/{server_id}/operations/warp/license",
        json={"license": "warp-plus-key", "command_timeout_ms": 45_000},
    )
    xray_mode = client.post(
        f"/api/v1/servers/{server_id}/operations/agent/switch-xray-mode",
        json={"xray_mode": "embedded"},
    )
    listen_port = client.post(
        f"/api/v1/servers/{server_id}/operations/agent/switch-listen-port",
        json={"listen_port": 24889},
    )
    probe_master = client.post(
        f"/api/v1/servers/{server_id}/operations/agent/probe-master-url",
        json={"master_url": "https://panel.example.com/"},
    )
    update_master = client.post(
        f"/api/v1/servers/{server_id}/operations/agent/update-master-url",
        json={"master_url": "https://panel.example.com/", "only_if_recovery": True},
    )
    invalid_port = client.post(
        f"/api/v1/servers/{server_id}/operations/agent/switch-listen-port",
        json={"listen_port": 80},
    )
    invalid_master = client.post(
        f"/api/v1/servers/{server_id}/operations/agent/probe-master-url",
        json={"master_url": "file:///etc/passwd"},
    )

    assert warp.status_code == 201
    assert xray_mode.status_code == 201
    assert listen_port.status_code == 201
    assert probe_master.status_code == 201
    assert update_master.status_code == 201
    assert invalid_port.status_code == 422
    assert invalid_master.status_code == 422
    assert warp.json()["command"]["path"] == "/api/child/warp/license"
    assert warp.json()["command"]["body"] == {"license": "warp-plus-key"}
    assert warp.json()["command"]["timeout_ms"] == 45_000
    assert xray_mode.json()["command"]["body"] == {"xray_mode": "embedded"}
    assert listen_port.json()["command"]["body"] == {"listen_port": 24889}
    assert probe_master.json()["command"]["body"] == {
        "master_url": "https://panel.example.com"
    }
    assert update_master.json()["command"]["body"] == {
        "master_url": "https://panel.example.com",
        "only_if_recovery": True,
    }


def test_stream_maintenance_operations_queue_mmwx_child_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-maintenance"}).json()
    server_id = created["server"]["id"]

    operations = [
        ("xray/install", "/api/child/xray/install-stream"),
        ("xray/remove", "/api/child/xray/remove-stream"),
        ("nginx/remove", "/api/child/nginx/remove-stream"),
        ("agent/upgrade", "/api/child/agent/upgrade-stream"),
        ("agent/uninstall", "/api/child/agent/uninstall-stream"),
    ]
    responses = [
        client.post(f"/api/v1/servers/{server_id}/operations/{operation}")
        for operation, _path in operations
    ]

    assert all(response.status_code == 201 for response in responses)
    for response, (_operation, expected_path) in zip(responses, operations, strict=True):
        command = response.json()["command"]
        assert response.json()["license_required"] is False
        assert command["method"] == "POST"
        assert command["path"] == expected_path
        assert command["stream"] is True
        assert command["timeout_ms"] == 300_000


def test_legacy_maintenance_operations_queue_active_agent_paths(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-legacy-maintenance"}).json()
    server_id = created["server"]["id"]

    xray_install = client.post(f"/api/v1/servers/{server_id}/operations/xray/install-legacy")
    xray_remove = client.post(f"/api/v1/servers/{server_id}/operations/xray/remove-legacy")
    nginx_install = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/install-legacy",
        json={
            "domain": "https://panel.example.com/path",
            "command_timeout_ms": 180_000,
        },
    )
    nginx_remove = client.post(f"/api/v1/servers/{server_id}/operations/nginx/remove-legacy")

    assert xray_install.status_code == 201
    assert xray_remove.status_code == 201
    assert nginx_install.status_code == 201
    assert nginx_remove.status_code == 201
    assert xray_install.json()["command"]["path"] == "/api/child/xray/install"
    assert xray_remove.json()["command"]["path"] == "/api/child/xray/remove"
    assert nginx_install.json()["command"]["path"] == "/api/child/nginx/install"
    assert nginx_install.json()["command"]["body"] == {"domain": "panel.example.com"}
    assert nginx_install.json()["command"]["timeout_ms"] == 180_000
    assert nginx_remove.json()["command"]["path"] == "/api/child/nginx/remove"
    for response in [xray_install, xray_remove, nginx_install, nginx_remove]:
        command = response.json()["command"]
        assert response.json()["license_required"] is False
        assert command["method"] == "POST"
        assert command["stream"] is False


def test_nginx_install_operation_accepts_optional_domain_query(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-nginx-install"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/nginx/install",
        json={
            "domain": "https://panel.example.com/path",
            "command_timeout_ms": 180_000,
        },
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert command["path"] == "/api/child/nginx/install-stream"
    assert command["query"] == "domain=panel.example.com"
    assert command["stream"] is True
    assert command["timeout_ms"] == 180_000


def test_nginx_clear_stream_port_operation_queues_agent_schema(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-nginx-stream"}).json()
    server_id = created["server"]["id"]

    response = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/clear-stream-port",
        json={"port": 443, "command_timeout_ms": 20_000},
    )
    invalid = client.post(
        f"/api/v1/servers/{server_id}/operations/nginx/clear-stream-port",
        json={"port": 0},
    )

    assert response.status_code == 201
    command = response.json()["command"]
    assert response.json()["license_required"] is False
    assert command["method"] == "POST"
    assert command["path"] == "/api/child/nginx/clear-stream-port"
    assert command["body"] == {"port": 443}
    assert command["timeout_ms"] == 20_000
    assert invalid.status_code == 422


def test_warp_operations_queue_mmwx_child_commands(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-warp-ops"}).json()
    server_id = created["server"]["id"]

    install = client.post(f"/api/v1/servers/{server_id}/operations/warp/install")
    status = client.post(f"/api/v1/servers/{server_id}/operations/warp/status")
    remove = client.post(f"/api/v1/servers/{server_id}/operations/warp/remove")

    assert install.status_code == 201
    assert status.status_code == 201
    assert remove.status_code == 201
    assert install.json()["command"]["method"] == "POST"
    assert install.json()["command"]["path"] == "/api/child/warp/install"
    assert status.json()["command"]["method"] == "GET"
    assert status.json()["command"]["path"] == "/api/child/warp/status"
    assert remove.json()["command"]["method"] == "POST"
    assert remove.json()["command"]["path"] == "/api/child/warp/remove"
    assert install.json()["command"]["stream"] is False
    assert status.json()["license_required"] is False


def test_agent_command_rejects_non_child_paths(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-command-path"}).json()

    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/commands",
        json={"method": "GET", "path": "/etc/passwd"},
    )

    assert response.status_code == 422


def test_invalid_command_token_is_rejected_as_auth_not_license(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": "not-a-real-token"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid agent token"


def test_create_command_for_unknown_server_returns_404(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/v1/servers/00000000-0000-0000-0000-000000000000/commands",
        json={"method": "GET", "path": "/api/child/system/info"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "server not found: 00000000-0000-0000-0000-000000000000"


def test_agent_websocket_auth_registers_agent_and_acks_heartbeat(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws"}).json()

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-host",
                    "agent_version": "0.5.0",
                    "public_ipv4": "198.51.100.88",
                    "capabilities": {"rpc": True, "stream": True},
                    "xray_mode": "embedded",
                },
            }
        )
        auth = websocket.receive_json()

        assert auth["type"] == "auth_result"
        assert auth["payload"]["success"] is True
        assert auth["payload"]["license_required"] is False
        assert auth["payload"]["server_id"] == created["server"]["id"]
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        websocket.send_json(
            {
                "type": "heartbeat",
                "payload": {"listen_port": 28888, "public_ipv4": "198.51.100.89"},
            }
        )
        heartbeat = websocket.receive_json()

        assert heartbeat["type"] == "heartbeat_ack"
        assert heartbeat["payload"]["server_time"] > 0

    agents = client.get("/api/v1/agents").json()
    servers = client.get("/api/v1/servers").json()
    assert agents[0]["hostname"] == "edge-ws-host"
    assert agents[0]["capabilities"]["rpc"] is True
    assert servers[0]["status"] == "connected"
    assert servers[0]["ip_address"] == "198.51.100.89"


def test_agent_websocket_scan_result_updates_latest_without_license(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-scan"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-scan-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        websocket.send_json({"type": "scan_result", "payload": scan_result_payload()})
        ack = websocket.receive_json()

        assert ack["type"] == "scan_result_ack"
        assert ack["payload"]["server_id"] == server_id
        assert ack["payload"]["license_required"] is False
        assert ack["payload"]["reported_at"]

    latest = client.get(f"/api/v1/servers/{server_id}/scan/latest")
    payload = latest.json()

    assert latest.status_code == 200
    assert payload["license_required"] is False
    assert payload["scan"]["server_id"] == server_id
    assert payload["scan"]["xray_running"] is True
    assert payload["scan"]["config_path"] == "/usr/local/etc/xray/config.json"
    assert payload["scan"]["message"] == "Xray is running, found 1 inbound(s)"


def test_online_agent_websocket_receives_rpc_call_and_completes_command(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-rpc"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-rpc-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(
            f"/api/v1/servers/{server_id}/commands",
            json={"method": "GET", "path": "/api/child/system/info", "timeout_ms": 5000},
        )
        assert queued.status_code == 201
        command = queued.json()["command"]
        assert command["status"] == "leased"
        assert command["attempts"] == 1

        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["request_id"] == command["request_id"]
        assert rpc_call["payload"]["path"] == "/api/child/system/info"

        websocket.send_json(
            {
                "type": "rpc_reply",
                "payload": {
                    "request_id": command["request_id"],
                    "status": 200,
                    "body": {"hostname": "edge-ws-rpc-host"},
                },
            }
        )
        reply_ack = websocket.receive_json()
        assert reply_ack["type"] == "rpc_reply_ack"
        assert reply_ack["payload"]["status"] == "succeeded"

    commands = client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
    assert commands[0]["status"] == "succeeded"
    assert commands[0]["result_body"] == {"hostname": "edge-ws-rpc-host"}


def test_online_agent_websocket_receives_specialized_operation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-operation"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-operation-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(f"/api/v1/servers/{server_id}/operations/speed")

        assert queued.status_code == 201
        assert queued.json()["command"]["status"] == "leased"
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/speed"
        assert rpc_call["payload"]["method"] == "GET"


def test_online_agent_websocket_receives_diagnostic_operation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-diagnostics"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-diagnostics-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(f"/api/v1/servers/{server_id}/operations/services/status")

        assert queued.status_code == 201
        assert queued.json()["command"]["status"] == "leased"
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/services/status"
        assert rpc_call["payload"]["method"] == "GET"


def test_online_agent_websocket_receives_agent_setting_operation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-agent-setting"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-agent-setting-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(
            f"/api/v1/servers/{server_id}/operations/agent/update-master-url",
            json={"master_url": "https://panel.example.com", "only_if_recovery": True},
        )

        assert queued.status_code == 201
        assert queued.json()["command"]["status"] == "leased"
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/agent/update-master-url"
        assert rpc_call["payload"]["method"] == "POST"
        assert rpc_call["payload"]["body"] == {
            "master_url": "https://panel.example.com",
            "only_if_recovery": True,
        }


def test_online_agent_websocket_receives_high_level_operation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-high-level"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-high-level-host",
                    "capabilities": {"rpc": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(
            f"/api/v1/servers/{server_id}/operations/routing/manage",
            json={
                "action": "add_user_to_rule",
                "marktag": "route-proxy",
                "user_email": "user@example.com",
                "no_restart": True,
            },
        )

        assert queued.status_code == 201
        assert queued.json()["command"]["status"] == "leased"
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/routing"
        assert rpc_call["payload"]["method"] == "POST"
        assert rpc_call["payload"]["body"] == {
            "action": "add_user_to_rule",
            "index": 0,
            "marktag": "route-proxy",
            "user_email": "user@example.com",
            "no_restart": True,
        }


def test_online_agent_websocket_receives_stream_maintenance_operation(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-maintenance"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-maintenance-host",
                    "capabilities": {"rpc": True, "stream": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(f"/api/v1/servers/{server_id}/operations/xray/install")

        assert queued.status_code == 201
        assert queued.json()["command"]["status"] == "leased"
        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["path"] == "/api/child/xray/install-stream"
        assert rpc_call["payload"]["method"] == "POST"
        assert rpc_call["payload"]["stream"] is True


def test_online_agent_websocket_persists_stream_data_until_reply(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-stream"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-stream-host",
                    "capabilities": {"rpc": True, "stream": True},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(
            f"/api/v1/servers/{server_id}/commands",
            json={
                "method": "POST",
                "path": "/api/child/xray/install-stream",
                "timeout_ms": 5000,
                "stream": True,
            },
        )
        assert queued.status_code == 201
        command = queued.json()["command"]
        assert command["status"] == "leased"
        assert command["stream"] is True

        rpc_call = websocket.receive_json()
        assert rpc_call["type"] == "rpc_call"
        assert rpc_call["payload"]["request_id"] == command["request_id"]
        assert rpc_call["payload"]["stream"] is True

        websocket.send_json(
            {
                "type": "rpc_stream_data",
                "payload": {
                    "request_id": command["request_id"],
                    "data": "data: installing xray\n\n",
                },
            }
        )
        websocket.send_json(
            {
                "type": "rpc_stream_data",
                "payload": {
                    "request_id": command["request_id"],
                    "data": "data: xray started\n\n",
                },
            }
        )
        websocket.send_json(
            {
                "type": "rpc_reply",
                "payload": {"request_id": command["request_id"], "status": 200},
            }
        )
        assert websocket.receive_json()["type"] == "rpc_reply_ack"

    stream_response = client.get(
        f"/api/v1/servers/{server_id}/commands/{command['id']}/stream"
    )
    assert stream_response.status_code == 200
    payload = stream_response.json()
    assert payload["license_required"] is False
    assert payload["server_id"] == server_id
    assert payload["command_id"] == command["id"]
    assert [frame["sequence"] for frame in payload["frames"]] == [1, 2]
    assert [frame["data"] for frame in payload["frames"]] == [
        "data: installing xray\n\n",
        "data: xray started\n\n",
    ]
    assert all(frame["request_id"] == command["request_id"] for frame in payload["frames"])

    commands = client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
    assert commands[0]["status"] == "succeeded"


def test_stream_command_stays_queued_when_agent_lacks_stream_capability(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-no-stream"}).json()
    server_id = created["server"]["id"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json(
            {
                "type": "auth",
                "payload": {
                    "token": created["agent_token"],
                    "hostname": "edge-ws-no-stream-host",
                    "capabilities": {"rpc": True, "stream": False},
                },
            }
        )
        assert websocket.receive_json()["payload"]["success"] is True
        assert websocket.receive_json()["payload"]["path"] == "/api/child/xray/config"

        queued = client.post(
            f"/api/v1/servers/{server_id}/commands",
            json={
                "method": "POST",
                "path": "/api/child/xray/install-stream",
                "stream": True,
            },
        )

    assert queued.status_code == 201
    command = queued.json()["command"]
    assert command["status"] == "pending"
    assert command["attempts"] == 0


@pytest.mark.parametrize("ws_path", ["/api/v1/agents/ws", "/api/remote/ws"])
@pytest.mark.parametrize("result_transport", ["websocket", "http"])
def test_websocket_sync_refresh_and_reconnect_recovery(
    tmp_path: Path, result_transport: str, ws_path: str,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-sync"}).json()
    server_id = created["server"]["id"]
    recovery_url = f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery?with_config=true"
    auth = {"type": "auth", "payload": {
        "token": created["agent_token"], "capabilities": {"rpc": True},
    }}
    current_config = '{"inbounds":[{"tag":"vless-443"}],"outbounds":[]}'
    changed_config = '{"inbounds":[{"tag":"trojan-8443"}],"outbounds":[]}'

    with client.websocket_connect(ws_path) as websocket:
        websocket.send_json(auth)
        assert websocket.receive_json()["type"] == "auth_result"
        sync = websocket.receive_json()
        assert sync["type"] == "rpc_call"
        assert sync["payload"]["path"] == "/api/child/xray/config"
        assert sync["payload"]["query"] == ""
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": sync["payload"]["request_id"], "status": 200,
            "body": {"success": True, "config": current_config},
        }})
        assert websocket.receive_json()["type"] == "rpc_reply_ack"
        recovery = client.get(recovery_url).json()
        assert recovery["current"]["config"] == current_config
        assert recovery["has_pending"] is False

        mutation = client.post(
            f"/api/v1/servers/{server_id}/commands",
            json={"method": "POST", "path": "/api/child/inbounds", "body": {"action": "add"}},
        ).json()["command"]
        assert websocket.receive_json()["payload"]["request_id"] == mutation["request_id"]
        if result_transport == "http":
            result = client.post(
                f"/api/v1/agents/commands/{mutation['id']}/result",
                json={"token": created["agent_token"], "status": 200, "body": {"success": True}},
            )
            assert result.status_code == 200
        else:
            websocket.send_json({"type": "rpc_reply", "payload": {
                "request_id": mutation["request_id"], "status": 200, "body": {"success": True},
            }})
            assert websocket.receive_json()["type"] == "rpc_reply_ack"

        refresh = websocket.receive_json()
        assert refresh["type"] == "rpc_call"
        assert refresh["payload"]["path"] == "/api/child/xray/config"
        assert refresh["payload"]["query"] == "snapshot_source=master_write"
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": refresh["payload"]["request_id"], "status": 200,
            "body": {"success": True, "config": changed_config},
        }})
        assert websocket.receive_json()["type"] == "rpc_reply_ack"
        recovery = client.get(recovery_url).json()
        assert recovery["current"]["config"] == changed_config
        assert recovery["current"]["source"] == "master_write"
        assert recovery["has_pending"] is False

    with client.websocket_connect(ws_path) as websocket:
        websocket.send_json(auth)
        assert websocket.receive_json()["type"] == "auth_result"
        sync = websocket.receive_json()
        assert sync["payload"]["query"] == ""
        websocket.send_json({"type": "rpc_reply", "payload": {
            "request_id": sync["payload"]["request_id"], "status": 200,
            "body": {"success": True, "config": current_config},
        }})
        assert websocket.receive_json()["type"] == "rpc_reply_ack"

    recovery = client.get(recovery_url).json()
    assert recovery["current"]["config"] == changed_config
    assert recovery["pending"]["config"] == current_config
    assert recovery["pending"]["status"] == "pending_recovery"
    assert recovery["pending"]["source"] == "agent_report"


@pytest.mark.parametrize("status, body", [
    (404, {"success": False, "error": "Xray config not found"}),
    (200, {"success": False, "config": '{"inbounds":[]}'}),
    (200, {"success": True, "config": ""}),
])
def test_websocket_sync_failure_keeps_connection_and_retries_on_reconnect(
    tmp_path: Path, status: int, body: dict,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-no-xray"}).json()
    server_id = created["server"]["id"]
    auth = {"type": "auth", "payload": {
        "token": created["agent_token"], "capabilities": {"rpc": True},
    }}
    request_ids = []
    for _ in range(2):
        with client.websocket_connect("/api/v1/agents/ws") as websocket:
            websocket.send_json(auth)
            assert websocket.receive_json()["payload"]["success"] is True
            sync = websocket.receive_json()["payload"]
            request_ids.append(sync["request_id"])
            websocket.send_json({"type": "rpc_reply", "payload": {
                "request_id": sync["request_id"], "status": status, "body": body,
            }})
            assert websocket.receive_json()["type"] == "rpc_reply_ack"
            websocket.send_json({"type": "heartbeat", "payload": {}})
            assert websocket.receive_json()["type"] == "heartbeat_ack"
    assert len(set(request_ids)) == 2
    snapshots = client.get(f"/api/v1/servers/{server_id}/xray/config-snapshots").json()
    assert snapshots["snapshots"] == []


def test_websocket_dispatches_backlog_and_expired_leases_without_duplicate_pushes(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-backlog"}).json()
    server_id = created["server"]["id"]
    first = client.post(f"/api/v1/servers/{server_id}/operations/system-info").json()["command"]
    stream = client.post(f"/api/v1/servers/{server_id}/operations/xray/install").json()["command"]

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json({"type": "auth", "payload": {
            "token": created["agent_token"], "capabilities": {"rpc": True, "stream": False},
        }})
        assert websocket.receive_json()["type"] == "auth_result"
        assert websocket.receive_json()["payload"]["request_id"] == first["request_id"]
        sync = websocket.receive_json()["payload"]
        assert sync["path"] == "/api/child/xray/config"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"

        http_leases = client.post(
            "/api/v1/agents/commands/lease", json={"token": created["agent_token"]},
        ).json()["commands"]
        assert [command["id"] for command in http_leases] == [stream["id"]]
        with client.app.state.inventory._session() as session:
            session.execute(update(CommandModel).where(
                CommandModel.request_id == sync["request_id"],
            ).values(leased_at=datetime.now(tz=UTC) - timedelta(minutes=2)))
            session.commit()

        websocket.send_json({"type": "heartbeat", "payload": {}})
        assert websocket.receive_json()["type"] == "heartbeat_ack"
        retry = websocket.receive_json()
        assert retry["payload"]["request_id"] == sync["request_id"]
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"

    commands = client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
    synced = next(command for command in commands if command["request_id"] == sync["request_id"])
    assert synced["attempts"] == 2


def test_non_rpc_websocket_keeps_sync_available_for_http_lease(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-no-rpc"}).json()
    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json({"type": "auth", "payload": {"token": created["agent_token"]}})
        assert websocket.receive_json()["type"] == "auth_result"
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json()["type"] == "pong"
        commands = client.post(
            "/api/v1/agents/commands/lease", json={"token": created["agent_token"]},
        ).json()["commands"]
        assert len(commands) == 1
        assert commands[0]["path"] == "/api/child/xray/config"
        assert commands[0]["attempts"] == 1


def test_http_and_websocket_lease_race_has_one_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-lease-race"}).json()
    server_id = created["server"]["id"]
    command = client.post(f"/api/v1/servers/{server_id}/operations/system-info").json()["command"]
    store = client.app.state.inventory
    original_claim = store._claim_command_lease
    barrier = Barrier(2)

    def concurrent_claim(session, candidate, now):
        barrier.wait(timeout=5)
        return original_claim(session, candidate, now)

    monkeypatch.setattr(store, "_claim_command_lease", concurrent_claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        http = pool.submit(store.lease_commands, created["agent_token"], 1)
        push = pool.submit(store.lease_command_for_push, UUID(command["id"]))
        http_commands = http.result(timeout=10)[1]
        push_command = push.result(timeout=10)
    assert len(http_commands) + int(push_command is not None) == 1
    persisted = store.list_commands(UUID(server_id))[0]
    assert persisted.status == "leased"
    assert persisted.attempts == 1


@pytest.mark.asyncio
async def test_failed_websocket_send_releases_lease_without_replaying_completed_commands(
    tmp_path: Path,
) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-send-failed"}).json()
    server_id = UUID(created["server"]["id"])
    queued = client.post(f"/api/v1/servers/{server_id}/operations/system-info").json()["command"]
    store = client.app.state.inventory
    manager = client.app.state.agent_connections
    command = store.list_commands(server_id)[0]
    websocket = AsyncMock()
    websocket.send_json.side_effect = WebSocketDisconnect()
    manager.register(server_id, websocket, AgentCapabilities(rpc=True))
    failed = await manager.dispatch_command(store, command)
    assert failed.status == "pending"
    assert failed.attempts == 1
    assert manager.is_connected(server_id) is False

    websocket.send_json.side_effect = None
    manager.register(server_id, websocket, AgentCapabilities(rpc=True))
    leased = await manager.dispatch_command(store, failed)
    assert leased.status == "leased"
    assert leased.attempts == 2
    assert store.release_command_lease(leased.id, 1).status == "leased"
    assert store.lease_commands(created["agent_token"], 1)[1] == []
    await manager.dispatch_command(store, command)
    assert websocket.send_json.await_count == 2

    assert client.post(
        f"/api/v1/agents/commands/{queued['id']}/result",
        json={"token": created["agent_token"], "status": 200},
    ).status_code == 200
    await manager.dispatch_command(store, command)
    assert websocket.send_json.await_count == 2


def test_websocket_auth_probe_has_no_inventory_or_connection_side_effects(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "edge-ws-probe"}).json()
    server_id = created["server"]["id"]
    probe = {"type": "auth", "payload": {
        "token": created["agent_token"], "probe": True, "capabilities": {"rpc": True},
    }}
    with client.websocket_connect("/api/remote/ws") as websocket:
        websocket.send_json(probe)
        auth = websocket.receive_json()
        assert auth["type"] == "auth_result"
        assert auth["payload"]["success"] is True
        assert websocket.receive()["type"] == "websocket.close"
    assert client.get("/api/v1/agents").json() == []
    assert client.get("/api/v1/servers").json()[0]["status"] == "pending"
    assert client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"] == []

    with client.websocket_connect("/api/remote/ws") as live:
        live.send_json({"type": "auth", "payload": {**probe["payload"], "probe": False}})
        assert live.receive_json()["type"] == "auth_result"
        assert live.receive_json()["payload"]["path"] == "/api/child/xray/config"
        with client.websocket_connect("/api/remote/ws") as websocket:
            websocket.send_json(probe)
            assert websocket.receive_json()["payload"]["success"] is True
        command = client.post(
            f"/api/v1/servers/{server_id}/operations/system-info",
        ).json()["command"]
        assert live.receive_json()["payload"]["request_id"] == command["request_id"]
        assert len(client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]) == 2


@pytest.mark.parametrize("probe", [True, False])
@pytest.mark.parametrize("ws_path", ["/api/v1/agents/ws", "/api/remote/ws"])
def test_agent_websocket_invalid_token_returns_auth_failure(
    tmp_path: Path, probe: bool, ws_path: str,
) -> None:
    client = make_client(tmp_path)

    with client.websocket_connect(ws_path) as websocket:
        websocket.send_json({"type": "auth", "payload": {
            "token": "not-a-real-token", "probe": probe,
        }})
        auth = websocket.receive_json()

        assert auth["type"] == "auth_result"
        assert auth["payload"]["success"] is False
        assert auth["payload"]["license_required"] is False
        assert "invalid agent token" in auth["payload"]["message"]
