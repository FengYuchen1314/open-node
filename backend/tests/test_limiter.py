import hashlib
import json
from uuid import UUID, uuid4

import pytest
from open_node.domain.subscriptions import SubscriptionCatalogPlanEntry, SubscriptionPlanCreate
from test_subscriptions import create_catalog_fixture, make_client


@pytest.mark.parametrize("model", [SubscriptionPlanCreate, SubscriptionCatalogPlanEntry])
@pytest.mark.parametrize("value", [-1, 0.0000001, float("inf"), float("nan"), 1e15])
def test_plan_limits_reject_unrepresentable_rates(model, value):
    required = {"node_ids": [uuid4()]} if model is SubscriptionPlanCreate else {}
    with pytest.raises(ValueError):
        model(name="limited", traffic_limit_gb=10, speed_limit_mbps=value, **required)
    with pytest.raises(ValueError):
        model(
            name="limited",
            traffic_limit_gb=10,
            node_speed_limits={str(uuid4()): value},
            **required,
        )


@pytest.mark.parametrize("model", [SubscriptionPlanCreate, SubscriptionCatalogPlanEntry])
def test_plan_allows_zero_and_smallest_native_rate(model):
    required = {"node_ids": [uuid4()]} if model is SubscriptionPlanCreate else {}
    assert (
        model(
            name="unlimited", traffic_limit_gb=10, speed_limit_mbps=0, **required
        ).speed_limit_mbps
        == 0
    )
    assert (
        model(
            name="limited",
            traffic_limit_gb=10,
            speed_limit_mbps=0.000008,
            **required,
        ).speed_limit_mbps
        * 125000
        == 1
    )
    with pytest.raises(ValueError):
        model(
            name="invalid",
            traffic_limit_gb=10,
            node_device_limits={str(uuid4()): -1},
            **required,
        )


def register(client, token, native):
    result = client.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "limiter-agent",
            "capabilities": {"rpc": True, "native_limiter": native},
        },
    )
    assert result.status_code == 201
    assert result.json()["agent"]["capabilities"]["native_limiter"] is native


def limited_batch(client, server_id, rate=100):
    response = client.post(
        f"/api/v1/servers/{server_id}/operations/batch-apply",
        json={
            "limiter_users": [
                {"inbound_tag": "edge", "user": {"uid": 0, "email": "alice", "speed_limit": rate}}
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["command"]


def test_limited_credentials_are_never_leased_to_legacy_agents(tmp_path):
    client = make_client(tmp_path)
    server = client.post("/api/v1/servers", json={"name": "legacy"}).json()
    token, server_id = server["agent_token"], server["server"]["id"]
    register(client, token, False)
    command = limited_batch(client, server_id)
    leased = client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10}
    ).json()["commands"]
    assert command["id"] not in [item["id"] for item in leased]
    result = next(
        item
        for item in client.get(f"/api/v1/servers/{server_id}/commands").json()["commands"]
        if item["id"] == command["id"]
    )
    assert result["status"] == "skipped"
    assert result["attempts"] == 0
    assert result["result_error"] == "Sensitive Agent command failed"
    internal = next(
        item
        for item in client.app.state.inventory.list_commands(UUID(server["server"]["id"]))
        if str(item.id) == command["id"]
    )
    assert "native limiter" in internal.result_error


@pytest.mark.parametrize(
    "rate,confirmation,expected",
    [
        (100, None, "failed"),
        (100, {"applied": True, "unlimited": True}, "failed"),
        (100, {"applied": True, "revision": "invalid"}, "failed"),
        (100, {"applied": True, "revision": "a" * 64}, "succeeded"),
        (0, {"applied": True, "unlimited": True}, "succeeded"),
    ],
)
def test_batch_success_requires_native_enforcement_confirmation(
    tmp_path, rate, confirmation, expected
):
    client = make_client(tmp_path)
    server = client.post("/api/v1/servers", json={"name": "native"}).json()
    token, server_id = server["agent_token"], server["server"]["id"]
    register(client, token, True)
    command = limited_batch(client, server_id, rate)
    leased = client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10}
    ).json()["commands"]
    assert command["id"] in [item["id"] for item in leased]
    result = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": token,
            "status": 200,
            "body": {"success": True, "limiter": confirmation},
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["command"]["status"] == expected


def test_plan_node_overrides_are_bound_to_actual_credentials(tmp_path):
    client = make_client(tmp_path)
    token, server_id, node_id, _ = create_catalog_fixture(client)
    register(client, token, True)
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "per-node",
            "traffic_limit_gb": 10,
            "node_ids": [node_id],
            "speed_limit_mbps": 100,
            "device_limit": 9,
            "node_speed_limits": {node_id: 0.5},
            "node_device_limits": {node_id: 2},
        },
    ).json()["plan"]
    response = client.post("/api/v1/users/alice/plan", json={"plan_id": plan["id"]}).json()
    body = response["provisioning_batches"][0]["body"]
    binding = body["limiter_users"][0]
    assert binding["inbound_tag"] == "vless-443"
    assert binding["user"]["email"] == body["inbound_clients"][0]["client"]["email"]
    assert binding["user"]["speed_limit"] == 62500
    assert binding["user"]["device_limit"] == 2
    assert (
        binding["user"]["conn_group"]
        == "account-"
        + hashlib.sha256(
            json.dumps(["alice", server_id, "vless-443"], separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_limiter_status_and_removal_commands_preserve_revision(tmp_path):
    client = make_client(tmp_path)
    server_id = client.post("/api/v1/servers", json={"name": "limits"}).json()["server"]["id"]
    status = client.post(f"/api/v1/servers/{server_id}/operations/limiter/status").json()["command"]
    assert status["method"] == "GET"
    assert status["path"] == "/api/child/limiter"
    command = client.post(
        f"/api/v1/servers/{server_id}/operations/limiter",
        json={
            "inbound_tag": "edge",
            "action": "remove",
            "expected_revision": "b" * 64,
        },
    ).json()["command"]
    assert command["body"] == {
        "inbound_tag": "edge",
        "action": "remove",
        "expected_revision": "b" * 64,
    }
