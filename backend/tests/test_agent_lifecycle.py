from uuid import UUID

import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.inventory import AgentCommandCreate
from open_node.main import create_app


@pytest.fixture
def setup(tmp_path):
    client = authenticated_client(
        create_app(Settings(database_url=f"sqlite:///{tmp_path / 'lifecycle.db'}"))
    )
    created = client.post("/api/v1/servers", json={"name": "lifecycle-node"}).json()
    return client, created


def test_upgrade_queues_the_panel_verified_release_without_manual_version(setup):
    from open_node.services.agent_bootstrap_release import release_manifest

    client, created = setup
    response = client.post(
        f"/api/v1/servers/{created['server']['id']}/operations/agent/upgrade",
        json={"confirm": True},
    )
    assert response.status_code == 201
    command = response.json()["command"]
    release = release_manifest()["agent"]
    assert command["body"] == {
        "version": release["version"],
        "sha256": release["wheel"]["sha256"],
    }
    assert command["path"] == "/api/child/agent/upgrade-stream"
    assert command["stream"] and command["timeout_ms"] == 300000


def test_upgrade_rejects_manual_release_fields(setup):
    client, created = setup
    base = f"/api/v1/servers/{created['server']['id']}"
    assert (
        client.post(
            base + "/operations/agent/upgrade",
            json={"version": "0.2.0", "sha256": "a" * 64},
        ).status_code
        == 422
    )
    assert not client.get(base + "/commands").json()["commands"]


@pytest.mark.parametrize("operation", ["upgrade", "rollback", "uninstall"])
def test_lifecycle_writes_require_an_explicit_confirmation(setup, operation):
    client, created = setup
    url = f"/api/v1/servers/{created['server']['id']}/operations/agent/{operation}"
    assert client.post(url).status_code == 422
    assert client.post(url, json={"confirm": False}).status_code == 422


@pytest.mark.parametrize("operation", ["upgrade", "rollback", "uninstall"])
@pytest.mark.parametrize("invalid", [False, 1, "true"])
def test_destructive_operations_require_confirmation_when_supplied(setup, operation, invalid):
    client, created = setup
    url = f"/api/v1/servers/{created['server']['id']}/operations/agent/{operation}"
    assert client.post(url, json={"confirm": invalid}).status_code == 422
    assert client.post(url, json={"confirm": True}).status_code == 201


def test_lifecycle_status_requires_operator_and_uses_a_read_only_child_route(setup):
    client, created = setup
    url = f"/api/v1/servers/{created['server']['id']}/operations/agent/lifecycle"
    anonymous = TestClient(client.app)
    assert anonymous.post(url).status_code == 401
    response = client.post(url)
    assert response.status_code == 201
    assert response.json()["command"]["method"] == "GET"
    assert response.json()["command"]["path"] == "/api/child/agent/lifecycle"


def test_request_id_callback_is_token_scoped_idempotent_and_does_not_skip_dependencies(setup):
    client, created = setup
    base = f"/api/v1/servers/{created['server']['id']}"
    sequence = client.app.state.inventory.create_command_sequence(
        UUID(created["server"]["id"]),
        [
            AgentCommandCreate(
                method="POST",
                path="/api/child/agent/upgrade-stream",
                body={"version": "0.2.0", "sha256": "a" * 64},
            ),
            AgentCommandCreate(method="GET", path="/api/child/system/info"),
        ],
    )
    parent, child = [command.model_dump(mode="json") for command in sequence]
    callback = "/api/v1/agents/commands/by-request/"
    payload = {"token": created["agent_token"], "status": 200, "body": {"success": True}}
    assert client.post(callback + child["request_id"] + "/result", json=payload).status_code == 409
    other = client.post("/api/v1/servers", json={"name": "other-node"}).json()
    url = callback + parent["request_id"] + "/result"
    assert client.post(url, json={**payload, "token": "invalid"}).status_code == 401
    assert client.post(url, json={**payload, "token": other["agent_token"]}).status_code == 404
    response = client.post(url, json=payload)
    assert response.status_code == 200
    assert response.json()["command"]["status"] == "succeeded"
    repeated = client.post(url, json={**payload, "status": 500, "error": "late failure"})
    assert repeated.json()["command"]["status"] == "succeeded"
    rows = client.get(base + "/commands").json()["commands"]
    assert next(row for row in rows if row["id"] == child["id"])["status"] == "pending"


def test_completed_native_uninstall_marks_the_server_offline(setup):
    client, created = setup
    token = created["agent_token"]
    assert (
        client.post(
            "/api/v1/agents/register",
            json={"token": token, "hostname": "node", "agent_version": "open-node/0.1.0"},
        ).status_code
        == 201
    )
    base = f"/api/v1/servers/{created['server']['id']}"
    command = client.post(base + "/operations/agent/uninstall", json={"confirm": True}).json()[
        "command"
    ]
    response = client.post(
        "/api/v1/agents/commands/by-request/" + command["request_id"] + "/result",
        json={
            "token": token,
            "status": 200,
            "body": {"success": True, "action": "uninstall", "installation_status": "removed"},
        },
    )
    assert response.status_code == 200
    servers = client.get("/api/v1/servers").raise_for_status().json()
    server = next(row for row in servers if row["id"] == created["server"]["id"])
    assert server["status"] == "offline"
