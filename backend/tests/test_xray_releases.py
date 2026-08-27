import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app


@pytest.fixture
def client_and_server(tmp_path):
    client = authenticated_client(
        create_app(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    )
    server = client.post("/api/v1/servers", json={"name": "release-node"}).json()["server"]
    return client, server["id"]


def test_pinned_xray_install_reaches_the_native_agent(client_and_server):
    client, server = client_and_server
    body = {"version": "v26.2.6", "sha256": "a" * 64, "start": False}
    response = client.post(f"/api/v1/servers/{server}/operations/xray/install", json=body)
    assert response.status_code == 201
    command = response.json()["command"]
    assert command["body"] == body
    assert command["path"] == "/api/child/xray/install-stream"
    assert command["stream"] and command["timeout_ms"] == 300000


@pytest.mark.parametrize("path", ["install", "install-stream", "rollback"])
def test_successful_release_change_refreshes_the_configuration(client_and_server, path):
    client, _ = client_and_server
    created = client.post("/api/v1/servers", json={"name": "fresh-runtime"}).json()
    base = f"/api/v1/servers/{created['server']['id']}/commands"
    command = client.post(base, json={"method": "POST", "path": "/api/child/xray/" + path}).json()[
        "command"
    ]
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": True},
        },
    )
    assert response.status_code == 200
    rows = client.get(base).json()["commands"]
    assert any(
        row["path"] == "/api/child/xray/config" and row["query"] == "snapshot_source=master_write"
        for row in rows
    )


@pytest.mark.parametrize(
    "body",
    [
        {"version": "../../other"},
        {"version": "latest"},
        {"sha256": "invalid"},
        {"url": "https://example.invalid/untrusted.zip"},
    ],
)
def test_invalid_release_request_never_enters_the_queue(client_and_server, body):
    client, server = client_and_server
    response = client.post(f"/api/v1/servers/{server}/operations/xray/install", json=body)
    assert response.status_code == 422
    assert client.get(f"/api/v1/servers/{server}/commands").json()["commands"] == []


@pytest.mark.parametrize("operation,method", [("release", "GET"), ("rollback", "POST")])
def test_xray_release_operations_require_operator_and_queue_real_routes(
    client_and_server, operation, method
):
    client, server = client_and_server
    url = f"/api/v1/servers/{server}/operations/xray/{operation}"
    anonymous = TestClient(client.app, base_url="https://testserver")
    assert anonymous.post(url).status_code == 401
    response = client.post(url)
    assert response.status_code == 201
    assert response.json()["command"]["method"] == method
    assert response.json()["command"]["path"] == f"/api/child/xray/{operation}"
