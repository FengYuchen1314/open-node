from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from open_node.domain.inventory import AgentCapabilities
from open_node.services.inventory import CommandModel
from sqlalchemy import text, update
from test_inventory import make_client, register_xray_config_workspace


def create_change_set(client, created, steps, *, rollback_on_failure=True):
    response = client.post(
        "/api/v1/change-sets",
        json={
            "name": "Workspace capability coordination",
            "rollback_on_failure": rollback_on_failure,
            "steps": [
                {
                    "server_id": created["server"]["id"],
                    "label": f"Step {index}",
                    **step,
                }
                for index, step in enumerate(steps)
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["change_set"]


def lease_one(client, created):
    response = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": created["agent_token"], "max_commands": 10},
    )
    assert response.status_code == 200, response.text
    commands = response.json()["commands"]
    assert len(commands) == 1
    return commands[0]


def complete(client, created, command, *, success):
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": created["agent_token"],
            "status": 200,
            "body": {"success": success},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["command"]


def register_workspace_and_drain_sync(client, created):
    registration = register_xray_config_workspace(client, created)
    leased = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": created["agent_token"], "max_commands": 10},
    )
    assert leased.status_code == 200, leased.text
    for command in leased.json()["commands"]:
        assert command["path"] == "/api/child/xray/config"
        completed = client.post(
            f"/api/v1/agents/commands/{command['id']}/result",
            json={
                "token": created["agent_token"],
                "status": 503,
                "error": "registration sync drained by workspace test",
            },
        )
        assert completed.status_code == 200, completed.text
    return registration


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("xray/system-config/read", None),
        (
            "xray/system-config/write",
            {
                "log_level": "warning",
                "dns": {},
                "policy": {},
                "metrics_enabled": False,
                "metrics_listen": "127.0.0.1:11111",
                "stats_enabled": True,
                "grpc_enabled": True,
                "grpc_port": 46736,
                "expected_sha256": "a" * 64,
            },
        ),
        ("xray/config-files/list", None),
        ("xray/config-files/read", {"file": "config.json"}),
        (
            "xray/config-files/write",
            {"file": "config.json", "content": {}, "expected_sha256": "b" * 64},
        ),
    ],
)
def test_workspace_operations_reject_an_unregistered_or_legacy_agent(
    tmp_path, operation, payload
):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "legacy-workspace"}).json()
    base = f"/api/v1/servers/{created['server']['id']}"

    kwargs = {} if payload is None else {"json": payload}
    missing = client.post(f"{base}/operations/{operation}", **kwargs)
    assert missing.status_code == 409
    assert "Upgrade the Open Node Agent" in missing.json()["detail"]
    assert "xray_config_workspace" in missing.json()["detail"]

    registration = client.post(
        "/api/v1/agents/register",
        json={
            "token": created["agent_token"],
            "hostname": "legacy-workspace",
            "agent_version": "open-node/0.2.0",
            "capabilities": {"rpc": True},
        },
    )
    assert registration.status_code == 201
    assert registration.json()["agent"]["capabilities"]["xray_config_workspace"] is False

    legacy = client.post(f"{base}/operations/{operation}", **kwargs)
    assert legacy.status_code == 409
    workspace_paths = {
        "/api/child/xray/config-files",
        "/api/child/xray/system-config",
    }
    commands = client.get(f"{base}/commands").json()["commands"]
    assert not any(command["path"] in workspace_paths for command in commands)


def test_raw_workspace_command_uses_the_same_queue_time_capability_gate(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "raw-workspace"}).json()
    base = f"/api/v1/servers/{created['server']['id']}"

    response = client.post(
        f"{base}/commands",
        json={"method": "GET", "path": "/api/child/xray/system-config"},
    )

    assert response.status_code == 409
    assert "Upgrade the Open Node Agent" in response.json()["detail"]


def test_workspace_capability_survives_schema_upgrade_with_a_safe_default(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "workspace-migration"}).json()
    register_workspace_and_drain_sync(client, created)

    with client.app.state.inventory._engine.begin() as connection:
        connection.execute(text("ALTER TABLE agents DROP COLUMN capability_xray_config_workspace"))

    upgraded = make_client(tmp_path)
    agent = upgraded.get("/api/v1/agents").json()[0]
    assert agent["capabilities"]["xray_config_workspace"] is False

    base = f"/api/v1/servers/{created['server']['id']}"
    rejected = upgraded.post(f"{base}/operations/xray/system-config/read")
    assert rejected.status_code == 409
    register_workspace_and_drain_sync(upgraded, created)
    accepted = upgraded.post(f"{base}/operations/xray/system-config/read")
    assert accepted.status_code == 201


def test_workspace_command_is_not_leased_after_agent_capability_downgrade(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "workspace-downgrade"}).json()
    register_workspace_and_drain_sync(client, created)
    base = f"/api/v1/servers/{created['server']['id']}"
    command = client.post(f"{base}/operations/xray/config-files/list").json()["command"]

    downgraded = client.post(
        "/api/v1/agents/register",
        json={
            "token": created["agent_token"],
            "hostname": "workspace-downgrade",
            "agent_version": "open-node/0.2.0",
            "capabilities": {"rpc": True},
        },
    )
    assert downgraded.status_code == 201

    leased = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": created["agent_token"], "max_commands": 10},
    ).json()["commands"]
    assert command["id"] not in {item["id"] for item in leased}
    stored = next(
        item for item in client.get(f"{base}/commands").json()["commands"]
        if item["id"] == command["id"]
    )
    assert stored["status"] == "skipped"
    assert stored["result_status"] == 501
    assert stored["result_error"] == "Sensitive Agent command failed"


def test_expired_workspace_lease_becomes_failed_after_agent_capability_downgrade(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "workspace-expired"}).json()
    register_workspace_and_drain_sync(client, created)
    base = f"/api/v1/servers/{created['server']['id']}"
    queued = client.post(f"{base}/operations/xray/config-files/list").json()["command"]
    leased = lease_one(client, created)
    assert leased["id"] == queued["id"]

    with client.app.state.inventory._session() as session:
        session.execute(
            update(CommandModel)
            .where(CommandModel.id == queued["id"])
            .values(leased_at=datetime.now(tz=UTC) - timedelta(minutes=2))
        )
        session.commit()

    downgraded = client.post(
        "/api/v1/agents/register",
        json={
            "token": created["agent_token"],
            "hostname": "workspace-expired",
            "agent_version": "open-node/0.2.0",
            "capabilities": {"rpc": True},
        },
    )
    assert downgraded.status_code == 201

    retried = client.post(
        "/api/v1/agents/commands/lease",
        json={"token": created["agent_token"], "max_commands": 10},
    ).json()["commands"]
    assert queued["id"] not in {command["id"] for command in retried}
    stored = next(
        item for item in client.get(f"{base}/commands").json()["commands"]
        if item["id"] == queued["id"]
    )
    assert stored["status"] == "failed"
    assert stored["attempts"] == 1
    assert stored["result_status"] == 501
    assert stored["leased_at"] is None
    assert stored["completed_at"] is not None
    assert stored["result_error"] == "Sensitive Agent command failed"


@pytest.mark.asyncio
async def test_live_legacy_websocket_terminalizes_expired_persisted_capability_loss(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "workspace-ws-expired"}).json()
    server_id = UUID(created["server"]["id"])
    register_workspace_and_drain_sync(client, created)
    base = f"/api/v1/servers/{server_id}"
    queued = client.post(f"{base}/operations/xray/config-files/list").json()["command"]
    leased = lease_one(client, created)
    assert leased["id"] == queued["id"]

    with client.app.state.inventory._session() as session:
        session.execute(
            update(CommandModel)
            .where(CommandModel.id == queued["id"])
            .values(leased_at=datetime.now(tz=UTC) - timedelta(minutes=2))
        )
        session.commit()

    client.post(
        "/api/v1/agents/register",
        json={
            "token": created["agent_token"],
            "hostname": "workspace-ws-expired",
            "agent_version": "open-node/0.2.0",
            "capabilities": {"rpc": True},
        },
    )
    manager = client.app.state.agent_connections
    websocket = AsyncMock()
    manager.register(server_id, websocket, AgentCapabilities(rpc=True))

    current = next(
        command
        for command in client.app.state.inventory.list_commands(server_id)
        if str(command.id) == queued["id"]
    )
    terminal = await manager.dispatch_command(client.app.state.inventory, current)

    assert terminal.status == "failed"
    assert terminal.attempts == 1
    assert terminal.result_status == 501
    assert "outcome is unknown" in terminal.result_error
    websocket.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_legacy_websocket_cannot_claim_workspace_command_after_http_upgrade(
    tmp_path,
):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "split-capability"}).json()
    server_id = UUID(created["server"]["id"])
    manager = client.app.state.agent_connections
    websocket = AsyncMock()
    manager.register(server_id, websocket, AgentCapabilities(rpc=True))

    register_xray_config_workspace(client, created)
    websocket.reset_mock()
    queued = client.post(
        f"/api/v1/servers/{server_id}/operations/xray/config-files/list"
    )
    assert queued.status_code == 201, queued.text
    queued_command = queued.json()["command"]
    command = next(
        command
        for command in client.app.state.inventory.list_commands(server_id)
        if str(command.id) == queued_command["id"]
    )

    unchanged = await manager.dispatch_command(client.app.state.inventory, command)

    assert unchanged.status == "pending"
    assert unchanged.attempts == 0
    websocket.send_json.assert_not_awaited()


def test_change_set_dispatch_preflights_workspace_capability(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "dispatch-preflight"}).json()
    change = create_change_set(
        client,
        created,
        [
            {
                "forward": {
                    "method": "GET",
                    "path": "/api/child/xray/system-config",
                },
                "rollback": None,
            }
        ],
    )

    response = client.post(f"/api/v1/change-sets/{change['id']}/dispatch")

    assert response.status_code == 409
    assert "xray_config_workspace" in response.json()["detail"]
    current = client.get(f"/api/v1/change-sets/{change['id']}").json()["change_set"]
    assert current["status"] == "planned"
    assert current["held_server_ids"] == []
    assert current["steps"][0]["forward_command"] is None


def test_manual_rollback_preflights_workspace_capability(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "rollback-preflight"}).json()
    change = create_change_set(
        client,
        created,
        [
            {
                "forward": {
                    "method": "POST",
                    "path": "/api/child/outbounds",
                    "body": {"action": "add", "tag": "workspace-preflight"},
                },
                "rollback": {
                    "method": "GET",
                    "path": "/api/child/xray/system-config",
                },
            }
        ],
    )
    dispatched = client.post(f"/api/v1/change-sets/{change['id']}/dispatch")
    assert dispatched.status_code == 200, dispatched.text
    complete(client, created, lease_one(client, created), success=True)

    response = client.post(f"/api/v1/change-sets/{change['id']}/rollback", json={})

    assert response.status_code == 409
    assert "xray_config_workspace" in response.json()["detail"]
    current = client.get(f"/api/v1/change-sets/{change['id']}").json()["change_set"]
    assert current["status"] == "succeeded"
    assert current["held_server_ids"] == []
    assert current["steps"][0]["rollback_command"] is None


def test_automatic_rollback_persists_capability_failure_without_queued_commands(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "automatic-rollback"}).json()
    change = create_change_set(
        client,
        created,
        [
            {
                "forward": {
                    "method": "POST",
                    "path": "/api/child/outbounds",
                    "body": {"action": "add", "tag": "first"},
                },
                "rollback": {
                    "method": "POST",
                    "path": "/api/child/outbounds",
                    "body": {"action": "remove", "tag": "first"},
                },
            },
            {
                "forward": {
                    "method": "POST",
                    "path": "/api/child/outbounds",
                    "body": {"action": "add", "tag": "second"},
                },
                "rollback": {
                    "method": "GET",
                    "path": "/api/child/xray/system-config",
                },
            },
        ],
    )
    dispatched = client.post(f"/api/v1/change-sets/{change['id']}/dispatch")
    assert dispatched.status_code == 200, dispatched.text
    complete(client, created, lease_one(client, created), success=True)
    failed = complete(client, created, lease_one(client, created), success=False)

    assert failed["status"] == "failed"
    current = client.get(f"/api/v1/change-sets/{change['id']}").json()["change_set"]
    assert current["status"] == "rollback_failed"
    rollbacks = [step["rollback_command"] for step in current["steps"]]
    assert all(command["status"] == "skipped" for command in rollbacks)
    assert all(command["result_status"] == 501 for command in rollbacks)
    errors = {command["result_error"] for command in rollbacks}
    assert "Sensitive Agent command failed" in errors
    assert any("xray_config_workspace" in error for error in errors)
    commands = client.get(
        f"/api/v1/servers/{created['server']['id']}/commands"
    ).json()["commands"]
    queued_paths = {
        command["path"]
        for command in commands
        if command["status"] in {"pending", "waiting"}
    }
    assert queued_paths <= {"/api/child/xray/config"}
