from uuid import UUID, uuid4

import pytest
from open_node.domain.inventory import AgentCommandCreate
from sqlalchemy import text
from test_inventory import make_client

ENDPOINT = "/api/child/node-cleanup"


def setup(tmp_path, capable=True):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "cleanup"}).json()
    base, token = "/api/v1/servers/" + created["server"]["id"], created["agent_token"]
    registration = (
        client.post(
            "/api/v1/agents/register",
            json={
                "token": token,
                "hostname": "cleanup",
                "capabilities": {"rpc": True, "node_cleanup": capable},
            },
        )
        .raise_for_status()
        .json()
    )
    assert registration["agent"]["capabilities"]["node_cleanup"] is capable
    return client, base, token


def queue(client, base, action="apply"):
    payload = {
        "action": action,
        "operation_id": str(uuid4()),
        "expected_revision": "a" * 64,
        "inbound_tags": ["edge"],
        "acknowledge_runtime_restart": True,
    }
    command = client.app.state.inventory.create_command(
        UUID(base.rsplit("/", 1)[-1]),
        AgentCommandCreate(method="POST", path=ENDPOINT, body=payload),
    )
    return command.model_dump(mode="json")


def lease(client, token):
    response = client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10}
    )
    assert response.status_code == 200, response.text
    return response.json()["commands"]


def test_cleanup_capability_is_required_and_survives_schema_upgrade(tmp_path):
    client, base, token = setup(tmp_path, False)
    command = queue(client, base)
    assert command["id"] not in [item["id"] for item in lease(client, token)]
    row = next(
        item
        for item in client.get(base + "/commands").json()["commands"]
        if item["id"] == command["id"]
    )
    assert row["status"] == "skipped" and row["result_status"] == 501
    with client.app.state.inventory._engine.begin() as connection:
        connection.execute(text("ALTER TABLE agents DROP COLUMN capability_node_cleanup"))
    fresh = make_client(tmp_path)
    response = fresh.post("/api/v1/agents/register", json={"token": token, "hostname": "old-agent"})
    assert not response.json()["agent"]["capabilities"]["node_cleanup"]
    response = fresh.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "new-agent",
            "capabilities": {"node_cleanup": True},
        },
    )
    assert response.json()["agent"]["capabilities"]["node_cleanup"]
    assert queue(fresh, base)["id"] in [item["id"] for item in lease(fresh, token)]


@pytest.mark.parametrize(
    "change,leased",
    [
        ({}, True),
        ({"applied": False}, True),
        ({"revision": "b" * 64}, True),
        ({"operation_id": str(uuid4())}, True),
        ({"impact": None}, True),
        ({"exists": False}, True),
        ({}, False),
    ],
)
def test_cleanup_success_requires_an_exact_leased_receipt(tmp_path, change, leased):
    client, base, token = setup(tmp_path)
    command = queue(client, base)
    if leased:
        assert command["id"] in [item["id"] for item in lease(client, token)]
    receipt = {
        "applied": True,
        "revision": "a" * 64,
        "impact": {},
        "operation_id": command["body"]["operation_id"],
        **change,
    }
    response = client.post(
        "/api/v1/agents/commands/" + command["id"] + "/result",
        json={
            "token": token,
            "status": 200,
            "body": {"success": True, "node_cleanup": receipt},
        },
    )
    assert response.status_code == 200, response.text
    expected = "succeeded" if not change and leased else "failed"
    assert response.json()["command"]["status"] == expected


def test_preview_does_not_create_a_mutation_refresh(tmp_path):
    client, base, token = setup(tmp_path)
    command = queue(client, base, "preview")
    lease(client, token)
    before = client.get(base + "/commands").json()["commands"]
    response = client.post(
        "/api/v1/agents/commands/" + command["id"] + "/result",
        json={
            "token": token,
            "status": 200,
            "body": {
                "success": True,
                "node_cleanup": {
                    "applied": False,
                    "revision": "a" * 64,
                    "impact": {},
                },
            },
        },
    )
    assert response.json()["command"]["status"] == "succeeded"
    after = client.get(base + "/commands").json()["commands"]
    assert {row["id"] for row in before} == {row["id"] for row in after}
