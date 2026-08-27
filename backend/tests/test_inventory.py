import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


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


def test_agent_websocket_invalid_token_returns_auth_failure(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    with client.websocket_connect("/api/v1/agents/ws") as websocket:
        websocket.send_json({"type": "auth", "payload": {"token": "not-a-real-token"}})
        auth = websocket.receive_json()

        assert auth["type"] == "auth_result"
        assert auth["payload"]["success"] is False
        assert auth["payload"]["license_required"] is False
        assert "invalid agent token" in auth["payload"]["message"]
