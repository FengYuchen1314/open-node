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
