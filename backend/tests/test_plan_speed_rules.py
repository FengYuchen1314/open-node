import pytest
from open_node.domain.auto_speed import AutoSpeedRule
from open_node.services.inventory import AgentModel
from pydantic import ValidationError
from sqlalchemy import select, text
from test_plan_management import base, update_payload
from test_subscription_access import complete, current, lease, setup, state

RULE = {
    "type": "sustained",
    "threshold_mbps": 2.0,
    "sustained_seconds": 2,
    "window_seconds": 0,
    "burst_count": 0,
    "limit_mbps": 0.5,
    "limit_duration": 5,
}


@pytest.fixture
def env(tmp_path):
    values = setup(tmp_path)
    complete(values[0], values[1], current(values[0], values[1]))
    register(values, True)
    return values


def register(env, enabled):
    env[0].post(
        "/api/v1/agents/register",
        json={
            "token": env[1],
            "hostname": "rules-agent",
            "capabilities": {
                "rpc": True,
                "native_limiter": True,
                "subscription_access": True,
                "user_auto_speed_rules": enabled,
            },
        },
    ).raise_for_status()


def save(env, rules, **changes):
    response = env[0].put(
        base(env) + "/settings", json=update_payload(env, auto_speed_rules=rules, **changes)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_rules_are_bound_to_own_credentials_with_static_limits_and_clear(env):
    client, token, _, node_id, *_ = env
    credentials = client.get("/api/v1/users/alice/credentials").json()
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    other = client.post(
        "/api/v1/plans", json={"name": "Other", "traffic_limit_gb": 1, "node_ids": [node_id]}
    ).json()["plan"]
    client.post(
        "/api/v1/users/bob/plan", json={"plan_id": other["id"], "queue_agent_commands": True}
    ).raise_for_status()
    complete(client, token, current(client, token))
    stale = update_payload(env)
    saved = save(env, [RULE], speed_limit_mbps=0, device_limit=0)
    assert saved["plan"]["auto_speed_rules"] == [RULE]
    command = current(client, token)
    assert len(command["body"]["entries"]) == 1
    user = command["body"]["entries"][0]["limiter"]["user"]
    assert user["auto_speed_rules"] == [RULE]
    assert user["speed_limit"] == user["device_limit"] == 0
    assert user["email"] == credentials["credentials"][0]["email"]
    complete(client, token, command)
    assert client.put(base(env) + "/settings", json=stale).status_code == 409
    assert client.get("/api/v1/users/bob/access").json()["servers"][0]["status"] == "applied"
    save(env, [])
    command = current(client, token)
    assert not command["body"]["entries"][0]["limiter"]["user"].get("auto_speed_rules")
    complete(client, token, command)
    assert client.get("/api/v1/users/alice/credentials").json() == credentials


@pytest.mark.parametrize(
    "change",
    [
        {"type": "unknown"},
        {"threshold_mbps": 0},
        {"limit_mbps": 0.0000001},
        {"limit_mbps": float("inf")},
        {"limit_mbps": 1e20},
        {"threshold_mbps": "2"},
        {"sustained_seconds": 0},
        {"sustained_seconds": 1.5},
        {"limit_duration": 86401},
        {"burst_count": -1},
        {"window_seconds": -1},
        {"window_seconds": 86401},
        {"type": "burst", "window_seconds": 1, "burst_count": 1},
        {"type": "burst", "window_seconds": 10, "burst_count": 0},
        {"unexpected": True},
    ],
)
def test_rule_validation(change):
    with pytest.raises(ValidationError):
        AutoSpeedRule.model_validate(RULE | change)


def test_rule_count_order_defaults_and_plan_create(env):
    client = env[0]
    second = RULE | {"type": "burst", "window_seconds": 10, "burst_count": 2}
    body = {
        "name": "Automatic",
        "traffic_limit_gb": 1,
        "node_ids": [env[3]],
        "auto_speed_rules": [second, RULE],
    }
    result = client.post("/api/v1/plans", json=body).raise_for_status().json()["plan"]
    assert result["auto_speed_rules"] == [second, RULE]
    assert (
        client.post("/api/v1/plans", json=body | {"auto_speed_rules": [RULE] * 101}).status_code
        == 422
    )
    bad = update_payload(env, auto_speed_rules=[RULE | {"limit_duration": 0}])
    assert client.put(base(env) + "/settings", json=bad).status_code == 422
    minimal = {
        key: value for key, value in RULE.items() if key not in {"window_seconds", "burst_count"}
    }
    assert AutoSpeedRule.model_validate(minimal).model_dump() == RULE


def test_old_agent_never_receives_rules_and_recovery_resends_current_intent(env):
    client, token, *_ = env
    register(env, False)
    save(env, [RULE], speed_limit_mbps=0, device_limit=0)
    assert all(
        command["path"] != "/api/child/subscription-access" for command in lease(client, token)
    )
    assert state(client)["servers"][0]["status"] != "applied"
    register(env, True)
    client.post("/api/v1/users/alice/access/sync").raise_for_status()
    command = current(client, token)
    assert command["body"]["entries"][0]["limiter"]["user"]["auto_speed_rules"] == [RULE]
    complete(client, token, command)
    assert state(client)["servers"][0]["status"] == "applied"


def test_legacy_edits_and_catalog_roundtrip_preserve_rules(env):
    client = env[0]
    save(env, [RULE])
    payload = update_payload(env, description="Older client")
    del payload["auto_speed_rules"]
    client.put(base(env) + "/settings", json=payload).raise_for_status()
    assert client.get(base(env) + "/settings").json()["plan"]["auto_speed_rules"] == [RULE]
    catalog = client.get("/api/v1/catalog/export").raise_for_status().json()["catalog"]
    assert catalog["plans"][0]["auto_speed_rules"] == [RULE]
    catalog["plans"][0]["name"] = "Imported"
    client.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    result = next(
        plan for plan in client.get("/api/v1/plans").json()["plans"] if plan["name"] == "Imported"
    )
    assert result["auto_speed_rules"] == [RULE]
    del catalog["plans"][0]["auto_speed_rules"]
    client.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    result = next(
        plan for plan in client.get("/api/v1/plans").json()["plans"] if plan["name"] == "Imported"
    )
    assert result["auto_speed_rules"] == [RULE]
    catalog["plans"][0]["auto_speed_rules"] = []
    client.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    result = next(
        plan for plan in client.get("/api/v1/plans").json()["plans"] if plan["name"] == "Imported"
    )
    assert result["auto_speed_rules"] == []


def test_legacy_alias_only_edit_keeps_rules_without_runtime_commands(env):
    client, token, _, node_id, *_ = env
    save(env, [RULE])
    complete(client, token, current(client, token))
    payload = update_payload(env, node_name_overrides={node_id: "Renamed"})
    del payload["auto_speed_rules"]
    saved = client.put(base(env) + "/settings", json=payload).raise_for_status().json()
    assert saved["commands"] == []
    assert saved["plan"]["auto_speed_rules"] == [RULE]


def test_schema_upgrade_preserves_plan_and_uses_safe_defaults(env):
    client = env[0]
    original = client.get(base(env) + "/settings").json()["plan"]
    store = client.app.state.inventory
    with store._engine.begin() as db:
        db.execute(text("ALTER TABLE subscription_plans DROP COLUMN auto_speed_rules"))
        db.execute(text("ALTER TABLE agents DROP COLUMN capability_user_auto_speed_rules"))
    store.create_schema()
    store.create_schema()
    assert client.get(base(env) + "/settings").json()["plan"] == original
    assert store.list_subscription_plans()[0].auto_speed_rules == []
    with store._session() as db:
        assert not db.scalar(
            select(AgentModel).where(AgentModel.server_id == env[2])
        ).capability_user_auto_speed_rules


@pytest.mark.parametrize("native,rules_cap", [(True, False), (False, True)])
def test_legacy_batch_and_manual_policy_require_both_capabilities(env, native, rules_cap):
    client, token, server_id, *_ = env
    client.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "legacy",
            "capabilities": {
                "rpc": True,
                "native_limiter": native,
                "user_auto_speed_rules": rules_cap,
            },
        },
    ).raise_for_status()
    user = {"uid": 0, "email": "other", "auto_speed_rules": [RULE]}
    paths = [
        ("batch-apply", {"limiter_users": [{"inbound_tag": "other", "user": user}]}),
        ("limiter", {"inbound_tag": "other", "users": [user]}),
    ]
    for path, body in paths:
        result = (
            client.post(f"/api/v1/servers/{server_id}/operations/{path}", json=body)
            .raise_for_status()
            .json()["command"]
        )
        assert result["id"] not in [command["id"] for command in lease(client, token)]


def test_rule_only_batch_cannot_claim_unlimited_success(env):
    client, token, server_id, *_ = env
    body = {
        "limiter_users": [
            {
                "inbound_tag": "other",
                "user": {"uid": 0, "email": "other", "auto_speed_rules": [RULE]},
            }
        ]
    }
    result = (
        client.post(f"/api/v1/servers/{server_id}/operations/batch-apply", json=body)
        .raise_for_status()
        .json()["command"]
    )
    assert result["id"] in [command["id"] for command in lease(client, token)]
    response = (
        client.post(
            f"/api/v1/agents/commands/{result['id']}/result",
            json={
                "token": token,
                "status": 200,
                "body": {"success": True, "limiter": {"applied": True, "unlimited": True}},
            },
        )
        .raise_for_status()
        .json()["command"]
    )
    assert response["status"] == "failed"


@pytest.mark.parametrize("users", [None, False, 3, {}])
def test_malformed_manual_policy_cannot_crash_command_leasing(env, users):
    client, token, server_id, *_ = env
    command = (
        client.post(
            f"/api/v1/servers/{server_id}/commands",
            json={"method": "POST", "path": "/api/child/limiter", "body": {"users": users}},
        )
        .raise_for_status()
        .json()["command"]
    )
    assert command["id"] in [item["id"] for item in lease(client, token)]
