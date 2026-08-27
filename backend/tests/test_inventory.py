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
