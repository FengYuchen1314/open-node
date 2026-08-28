from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.user_limits import UserLimitOverrides
from open_node.services.inventory import (
    AgentModel,
    ManagedNodeModel,
    ProductUserModel,
    SubscriptionPlanModel,
)
from open_node.services.user_limits import effective_limits, node_limits
from pydantic import ValidationError
from sqlalchemy import select, text
from test_subscription_access import complete, current, lease, reconcile, setup, state
from test_subscriptions import make_client
from test_user_management import detail, settings


@pytest.fixture
def env(tmp_path):
    result = setup(tmp_path)
    complete(result[0], result[1], current(result[0], result[1]))
    return result


def save(client, **values):
    result = client.put(
        "/api/v1/users/alice/settings", json=settings(client, limit_overrides=values)
    )
    assert result.status_code == 200, result.text
    return result.json()


def cap(command):
    return command["body"]["entries"][0]["limiter"]["user"]


@pytest.mark.parametrize(
    "level", ["user_node", "user_parent", "user", "plan_node", "plan_parent", "plan", "unlimited"]
)
@pytest.mark.parametrize("value", [0, 7])
def test_exact_precedence_and_explicit_zero(level, value):
    node = SimpleNamespace(id="child", parent_id="parent")
    user = SimpleNamespace(
        node_speed_limit_overrides={},
        node_device_limit_overrides={},
        speed_limit_override_mbps=None,
        device_limit_override=None,
    )
    plan = SimpleNamespace(
        node_speed_limits={}, node_device_limits={}, speed_limit_mbps=90, device_limit=90
    )
    if level.startswith("user_"):
        key = "child" if level == "user_node" else "parent"
        user.node_speed_limit_overrides[key] = value
        user.node_device_limit_overrides[key] = value
        user.speed_limit_override_mbps = user.device_limit_override = 12
        plan.node_speed_limits["child"] = plan.node_device_limits["child"] = 25
        if key == "child":
            user.node_speed_limit_overrides["parent"] = user.node_device_limit_overrides[
                "parent"
            ] = 18
    elif level == "user":
        user.speed_limit_override_mbps = user.device_limit_override = value
        plan.node_speed_limits["child"] = plan.node_device_limits["child"] = 25
    elif level.startswith("plan_"):
        key = "child" if level == "plan_node" else "parent"
        plan.node_speed_limits[key] = plan.node_device_limits[key] = value
        if key == "child":
            plan.node_speed_limits["parent"] = plan.node_device_limits["parent"] = 25
    elif level == "plan":
        plan.speed_limit_mbps = plan.device_limit = value
    else:
        plan, value = None, 0
    limits = effective_limits(user, plan, node)
    assert limits.speed_limit_mbps == limits.device_limit == value
    assert limits.speed_source == limits.device_source == level


def test_speed_and_connections_resolve_independently_and_do_not_walk_grandparents():
    user = SimpleNamespace(
        node_speed_limit_overrides={"child": 0, "grandparent": 1},
        node_device_limit_overrides={"grandparent": 1},
        speed_limit_override_mbps=20,
        device_limit_override=None,
    )
    plan = SimpleNamespace(
        node_speed_limits={}, node_device_limits={"parent": 4}, speed_limit_mbps=90, device_limit=9
    )
    result = effective_limits(user, plan, SimpleNamespace(id="child", parent_id="parent"))
    assert (result.speed_limit_mbps, result.device_limit) == (0, 4)
    assert (result.speed_source, result.device_source) == ("user_node", "plan_parent")


def test_distinct_routed_credentials_share_connections_but_not_bandwidth():
    first, second = str(uuid4()), str(uuid4())
    user = SimpleNamespace(
        node_speed_limit_overrides={first: 2, second: 4},
        node_device_limit_overrides={first: 3, second: 5},
        speed_limit_override_mbps=None,
        device_limit_override=None,
    )
    plan = SimpleNamespace(
        node_ids=[first, second],
        node_speed_limits={},
        node_device_limits={},
        speed_limit_mbps=0,
        device_limit=0,
    )
    nodes = [
        SimpleNamespace(
            id=identifier, name=identifier, parent_id=None, enabled=True, removal_id=None
        )
        for identifier in (first, second)
    ]
    credentials = [
        SimpleNamespace(
            node_id=identifier, server_id="server", inbound_tag="shared", email=identifier
        )
        for identifier in (first, second)
    ]
    resolved = node_limits(user, plan, nodes, credentials)
    assert [item.speed_limit_mbps for item in resolved] == [2, 4]
    assert [item.device_limit for item in resolved] == [3, 3]
    assert resolved[1].device_source == "shared"


def test_limits_edit_preserves_identity_usage_dates_and_other_subscribers(env):
    client, token, _, node_id, plan_id, _ = env
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    client.post("/api/v1/users/bob/plan", json={"plan_id": plan_id}).raise_for_status()
    before = detail(client)["user"]
    credentials = client.get("/api/v1/users/alice/credentials").json()
    subscription = client.post("/api/v1/users/alice/subscription-token").json()
    bob = detail(client, "bob")
    email = credentials["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={"token": token, "stats": {"user": {email: {"uplink": 100, "downlink": 200}}}},
    ).raise_for_status()
    usage = client.get("/api/v1/users/alice/traffic").json()
    values = {
        "traffic_limit_gb": 2,
        "speed_limit_mbps": 6,
        "device_limit": 2,
        "node_speed_limits": {node_id: 4},
        "node_device_limits": {node_id: 1},
    }
    saved = save(client, **values)
    assert saved["user"]["limit_overrides"] == values
    assert saved["limits"]["traffic_limit_bytes"] == 2 * 1024**3
    assert saved["limits"]["nodes"][0]["speed_source"] == "user_node"
    assert cap(saved["commands"][0])["speed_limit"] == 500000
    assert cap(saved["commands"][0])["device_limit"] == 1
    for field in (
        "current_plan_id",
        "plan_started_at",
        "plan_expires_at",
        "reset_day",
        "last_traffic_reset_at",
    ):
        assert saved["user"][field] == before[field]
    assert detail(client)["revision"] == saved["revision"]
    assert client.get("/api/v1/users/alice/credentials").json() == credentials
    assert client.post("/api/v1/users/alice/subscription-token").json() == subscription
    assert client.get("/api/v1/users/alice/traffic").json() == usage
    assert detail(client, "bob") == bob
    complete(client, token, current(client, token))
    assert state(client)["servers"][0]["status"] == "applied"
    old_client_payload = settings(client, remark="Old client metadata update")
    client.put("/api/v1/users/alice/settings", json=old_client_payload).raise_for_status()
    assert detail(client)["user"]["limit_overrides"] == values


def test_explicit_unlimited_and_inheritance_update_real_access_payload(env):
    client, token, _, node_id, *_ = env
    save(
        client,
        speed_limit_mbps=12,
        device_limit=5,
        node_speed_limits={node_id: 0},
        node_device_limits={node_id: 0},
    )
    command = current(client, token)
    assert cap(command)["speed_limit"] == cap(command)["device_limit"] == 0
    complete(client, token, command)
    save(client, speed_limit_mbps=0, device_limit=0)
    assert reconcile(client) == []
    restored = save(client)
    assert cap(restored["commands"][0])["speed_limit"] == 12500000
    assert cap(restored["commands"][0])["device_limit"] == 3


def test_quota_override_header_revocation_unlimited_and_reset_keep_charged_usage(env):
    client, token = env[:2]
    subscription = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]
    email = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={"token": token, "stats": {"user": {email: {"uplink": 100, "downlink": 200}}}},
    ).raise_for_status()
    used = client.get("/api/v1/users/alice/quota").json()["quota"]["charged_usage_bytes"]
    assert used > 0
    save(client, traffic_limit_gb=(used + 100) / 1024**3)
    downloaded = client.get(subscription["subscription_url"])
    assert f"total={used + 100};" in downloaded.headers["subscription-userinfo"]
    saved = save(client, traffic_limit_gb=used / 1024**3)
    assert not saved["commands"][0]["body"]["entries"][0]["enabled"]
    assert client.get(subscription["subscription_url"]).status_code == 404
    complete(client, token, current(client, token))
    save(client, traffic_limit_gb=0)
    assert client.get(subscription["subscription_url"]).status_code == 200
    assert "subscription-userinfo" not in client.get(subscription["subscription_url"]).headers
    quota = client.get("/api/v1/users/alice/quota").json()["quota"]
    assert (
        quota["traffic_limit_bytes"] == 0
        and quota["charged_usage_bytes"] == used
        and not quota["over_quota"]
    )
    complete(client, token, current(client, token))
    save(client, traffic_limit_gb=used / 1024**3)
    complete(client, token, current(client, token))
    reset = client.post("/api/v1/users/alice/traffic/reset").raise_for_status().json()["quota"]
    assert reset["available"] and reset["charged_usage_bytes"] == 0
    reconcile(client)
    assert current(client, token)["body"]["entries"][0]["enabled"]


def test_profile_save_with_unchanged_overrides_does_not_enroll_preview_user(tmp_path):
    client, token, _, _, plan_id, _ = setup(tmp_path, queue=False)
    email = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={"token": token, "stats": {"user": {email: {"uplink": 20, "downlink": 20}}}},
    ).raise_for_status()
    with client.app.state.inventory._coordinated_session() as session:
        session.get(SubscriptionPlanModel, plan_id).traffic_limit_bytes = 1
        session.commit()
    saved = save(client)
    assert not saved["access"]["managed"] and not saved["commands"]


def test_preview_limits_do_not_enroll_but_exhausted_quota_tracks_withdrawal(tmp_path):
    client, token, *_ = setup(tmp_path, queue=False)
    saved = save(client, speed_limit_mbps=2, device_limit=1)
    assert not saved["access"]["managed"] and not saved["commands"]
    assert saved["limits"]["warnings"]
    email = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={"token": token, "stats": {"user": {email: {"uplink": 20, "downlink": 20}}}},
    ).raise_for_status()
    saved = save(client, traffic_limit_gb=1 / 1024**3)
    assert saved["access"]["managed"]
    assert not saved["commands"][0]["body"]["entries"][0]["enabled"]


@pytest.mark.parametrize("reason", ["expired", "disabled"])
def test_unlimited_quota_does_not_bypass_expiry_or_disabled_user(env, reason):
    client, token = env[:2]
    with client.app.state.inventory._coordinated_session() as session:
        user = session.get(ProductUserModel, "alice")
        if reason == "expired":
            user.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            user.is_active = False
        session.commit()
    save(client, traffic_limit_gb=0, speed_limit_mbps=0, device_limit=0)
    command = current(client, token)
    assert not command["body"]["entries"][0]["enabled"]
    assert state(client)["servers"][0]["entries"][0]["reason"] == reason


def test_parent_overrides_apply_to_assignment_and_reconciliation(env):
    client, token, server_id, node_id, plan_id, _ = env
    parent = (
        client.post(
            "/api/v1/nodes",
            json={
                "name": "Physical parent",
                "server_id": server_id,
                "protocol": "vless",
                "inbound_tag": "vless-443",
            },
        )
        .raise_for_status()
        .json()["node"]
    )
    with client.app.state.inventory._coordinated_session() as session:
        session.get(ManagedNodeModel, node_id).parent_id = parent["id"]
        plan = session.get(SubscriptionPlanModel, plan_id)
        plan.node_speed_limits, plan.node_device_limits = {parent["id"]: 8}, {parent["id"]: 2}
        session.commit()
    saved = save(client)
    assert cap(saved["commands"][0])["speed_limit"] == 1000000
    assert saved["limits"]["nodes"][0]["speed_source"] == "plan_parent"
    complete(client, token, current(client, token))
    saved = save(client, node_speed_limits={parent["id"]: 0}, node_device_limits={parent["id"]: 1})
    assert cap(saved["commands"][0])["speed_limit"] == 0
    assert saved["limits"]["nodes"][0]["device_source"] == "user_parent"
    assigned = (
        client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status().json()
    )
    binding = assigned["provisioning_batches"][0]["body"]["limiter_users"][0]["user"]
    assert binding["speed_limit"] == 0 and binding["device_limit"] == 1


def test_shared_credentials_keep_strictest_positive_cap_and_report_it(env):
    client, token, _, node_id, plan_id, _ = env
    node = client.get("/api/v1/nodes").json()["nodes"][0]
    clone = {
        key: value
        for key, value in node.items()
        if key not in {"id", "created_at", "updated_at", "removal_id"}
    }
    clone["name"] = "Alias"
    alias = client.post("/api/v1/nodes", json=clone).raise_for_status().json()["node"]["id"]
    with client.app.state.inventory._coordinated_session() as session:
        session.get(SubscriptionPlanModel, plan_id).node_ids = [node_id, alias]
        session.commit()
    save(
        client,
        speed_limit_mbps=0,
        device_limit=0,
        node_speed_limits={node_id: 5},
        node_device_limits={node_id: 2},
    )
    assigned = (
        client.post(
            "/api/v1/users/alice/plan", json={"plan_id": plan_id, "queue_agent_commands": True}
        )
        .raise_for_status()
        .json()
    )
    binding = assigned["provisioning_batches"][0]["body"]["limiter_users"]
    assert len(binding) == 1 and binding[0]["user"]["speed_limit"] == 625000
    assert binding[0]["user"]["device_limit"] == 2
    nodes = detail(client)["limits"]["nodes"]
    assert [node["speed_limit_mbps"] for node in nodes] == [5, 5]
    assert nodes[1]["speed_source"] == nodes[1]["device_source"] == "shared"
    assert cap(current(client, token))["speed_limit"] == 625000


def test_stale_user_and_plan_revisions_are_rejected(env):
    client = env[0]
    payload = settings(client, limit_overrides={"device_limit": 1})
    save(client, device_limit=2)
    assert client.put("/api/v1/users/alice/settings", json=payload).status_code == 409
    payload = settings(client, limit_overrides={"device_limit": 1})
    with client.app.state.inventory._coordinated_session() as session:
        session.get(SubscriptionPlanModel, env[4]).updated_at = datetime.now(UTC)
        session.commit()
    assert client.put("/api/v1/users/alice/settings", json=payload).status_code == 409


@pytest.mark.parametrize(
    "values",
    [
        {"traffic_limit_gb": -1},
        {"traffic_limit_gb": 1e-12},
        {"traffic_limit_gb": 2**53},
        {"traffic_limit_gb": True},
        {"traffic_limit_gb": "2"},
        {"speed_limit_mbps": -1},
        {"speed_limit_mbps": 1e-10},
        {"speed_limit_mbps": 2**50},
        {"device_limit": 0.5},
        {"device_limit": True},
        {"device_limit": 1000001},
        {"node_speed_limits": {"invalid": 2}},
        {"node_speed_limits": {str(uuid4()): -1}},
        {"node_device_limits": {str(uuid4()): None}},
        {"node_device_limits": {str(uuid4()): 1.5}},
        {"extra": 1},
    ],
)
def test_limit_validation_rolls_back_profile(env, values):
    client = env[0]
    before = detail(client)
    response = client.put(
        "/api/v1/users/alice/settings",
        json=settings(client, display_name="Do not save", limit_overrides=values),
    )
    assert response.status_code == 422, response.text
    assert detail(client) == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_limits_are_rejected(value):
    for field in ("traffic_limit_gb", "speed_limit_mbps"):
        with pytest.raises(ValidationError):
            UserLimitOverrides.model_validate({field: value})


def test_unknown_node_rejects_entire_user_update(env):
    client = env[0]
    before = detail(client)
    response = client.put(
        "/api/v1/users/alice/settings",
        json=settings(
            client,
            display_name="Do not save",
            limit_overrides={"node_speed_limits": {str(uuid4()): 1}},
        ),
    )
    assert response.status_code == 404
    assert detail(client) == before


def test_old_unleased_batches_cannot_overwrite_new_caps(env):
    client, token, server_id, _, _, assignment = env
    batch = client.app.state.inventory.create_command(
        UUID(server_id),
        AgentCommandCreate(
            method="POST",
            path="/api/child/batch-apply",
            body=assignment["provisioning_batches"][0]["body"],
        ),
    )
    save(client, speed_limit_mbps=1, device_limit=1)
    commands = lease(client, token)
    assert str(batch.id) not in [item["id"] for item in commands]
    assert (
        cap(next(item for item in commands if item["path"] == "/api/child/subscription-access"))[
            "speed_limit"
        ]
        == 125000
    )


def test_inflight_cap_update_drains_then_converges_to_new_override(env):
    client, token = env[:2]
    save(client, speed_limit_mbps=2)
    previous = current(client, token)
    save(client, speed_limit_mbps=3)
    complete(client, token, previous)
    reconcile(client)
    assert cap(current(client, token))["speed_limit"] == 375000


def test_incapable_agent_cannot_report_limits_as_applied(env):
    client, token, server_id, *_ = env
    with client.app.state.inventory._coordinated_session() as session:
        session.scalar(
            select(AgentModel).where(AgentModel.server_id == server_id)
        ).capability_native_limiter = False
        session.commit()
    saved = save(client, speed_limit_mbps=1)
    assert saved["commands"][0]["id"] not in [item["id"] for item in lease(client, token)]
    assert state(client)["servers"][0]["status"] == "failed"


def test_catalog_roundtrip_remaps_nodes_and_old_catalog_preserves_overrides(env, tmp_path):
    client, _, _, node_id, *_ = env
    values = {
        "traffic_limit_gb": 0,
        "speed_limit_mbps": 2,
        "device_limit": None,
        "node_speed_limits": {node_id: 0},
        "node_device_limits": {node_id: 3},
    }
    save(client, **values)
    catalog = client.get("/api/v1/catalog/export").raise_for_status().json()["catalog"]
    exported = catalog["users"][0]["limit_overrides"]
    assert exported["node_speed_limits"] == {"Tokyo vless": 0}
    target = tmp_path / "target"
    target.mkdir()
    imported = make_client(target)
    imported.post("/api/v1/servers", json={"name": "edge-sub"}).raise_for_status()
    imported.post("/api/v1/catalog/import", json={"catalog": catalog}).raise_for_status()
    new_node = imported.get("/api/v1/nodes").json()["nodes"][0]["id"]
    restored = detail(imported)["user"]["limit_overrides"]
    assert restored == {
        **values,
        "node_speed_limits": {new_node: 0},
        "node_device_limits": {new_node: 3},
    }
    old_catalog = deepcopy(catalog)
    old_catalog["users"][0].pop("limit_overrides")
    client.post("/api/v1/catalog/import", json={"catalog": old_catalog}).raise_for_status()
    assert detail(client)["user"]["limit_overrides"] == values
    invalid = deepcopy(catalog)
    invalid["users"][0]["display_name"] = "Do not commit"
    invalid["users"][0]["limit_overrides"]["node_speed_limits"] = {"Missing": 0}
    before = detail(client)
    assert client.post("/api/v1/catalog/import", json={"catalog": invalid}).status_code == 409
    assert detail(client) == before


def test_database_upgrade_adds_inherited_defaults_and_survives_restart(env, tmp_path):
    client = env[0]
    credentials = client.get("/api/v1/users/alice/credentials").json()
    with client.app.state.inventory._engine.begin() as connection:
        for field in (
            "traffic_limit_override_bytes",
            "speed_limit_override_mbps",
            "device_limit_override",
            "node_speed_limit_overrides",
            "node_device_limit_overrides",
        ):
            connection.execute(text(f'ALTER TABLE product_users DROP COLUMN "{field}"'))
    upgraded = make_client(tmp_path)
    assert detail(upgraded)["user"]["limit_overrides"] == UserLimitOverrides().model_dump(
        mode="json"
    )
    save(upgraded, traffic_limit_gb=0, speed_limit_mbps=1, node_device_limits={env[3]: 2})
    restarted = make_client(tmp_path)
    assert detail(restarted)["user"]["limit_overrides"]["traffic_limit_gb"] == 0
    assert detail(restarted)["limits"]["nodes"][0]["device_limit"] == 2
    assert restarted.get("/api/v1/users/alice/credentials").json() == credentials


@pytest.mark.parametrize("username", ["alice/plan", "a/b+subscriber", "utf8/\u4e2d\u6587"])
def test_valid_slash_username_can_edit_limits_without_ambiguous_routes(env, username):
    client = env[0]
    params = {"username": username}
    client.post("/api/v1/users", json={"username": username}).raise_for_status()
    client.post("/api/v1/user-plan", params=params, json={"plan_id": env[4]}).raise_for_status()
    before = client.get("/api/v1/user-settings", params=params).raise_for_status().json()
    assert before["user"]["username"] == username
    payload = {key: before["user"][key] for key in ("display_name", "email", "remark", "is_active")}
    client.put(
        "/api/v1/user-settings",
        params=params,
        json={
            **payload,
            "expected_revision": before["revision"],
            "acknowledge_runtime_restart": True,
            "limit_overrides": {"speed_limit_mbps": 0},
        },
    ).raise_for_status()
    for resource in (
        "quota",
        "traffic",
        "access",
        "credentials",
        "subscription-token",
        "plan/removal",
    ):
        client.get("/api/v1/user-" + resource, params=params).raise_for_status()
    saved = client.get("/api/v1/user-settings", params=params).raise_for_status().json()
    assert saved["limits"]["speed_limit_mbps"] == 0
    response = client.post(
        "/api/v1/user-remove",
        params=params,
        json={
            "expected_revision": saved["revision"],
            "confirm_name": username,
            "acknowledge_runtime_restart": True,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["username"] == username
    assert detail(client)["user"]["current_plan_id"] == env[4]
    assert detail(client)["user"]["removal_id"] is None


def test_plan_edits_preserve_overrides_and_assignment_dates(env):
    from open_node.domain.subscriptions import SubscriptionPlanCreate

    client, token, _, node_id, plan_id, _ = env
    save(client, traffic_limit_gb=4, speed_limit_mbps=2, device_limit=0)
    complete(client, token, current(client, token))
    before = detail(client)["user"]
    plan = client.get(f"/api/v1/plans/{plan_id}/settings").raise_for_status().json()
    payload = {key: plan["plan"][key] for key in SubscriptionPlanCreate.model_fields}
    payload.update(
        speed_limit_mbps=80,
        device_limit=9,
        node_speed_limits={node_id: 40},
        expected_revision=plan["revision"],
        acknowledge_runtime_restart=True,
    )
    client.put(f"/api/v1/plans/{plan_id}/settings", json=payload).raise_for_status()
    after = detail(client)["user"]
    for field in (
        "limit_overrides",
        "plan_started_at",
        "plan_expires_at",
        "reset_day",
        "last_traffic_reset_at",
    ):
        assert after[field] == before[field]
    assert detail(client)["limits"]["nodes"][0]["speed_limit_mbps"] == 2
    inherited = save(client)
    assert inherited["limits"]["nodes"][0]["speed_limit_mbps"] == 40
    assert cap(current(client, token))["device_limit"] == 9


def test_node_removal_prunes_only_its_overrides(env):
    from test_node_management import create, drain, remove

    client, token, server_id, node_id, *_ = env
    client.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "limits-agent",
            "capabilities": {
                "rpc": True,
                "native_limiter": True,
                "subscription_access": True,
                "node_cleanup": True,
            },
        },
    ).raise_for_status()
    other = create(client, server_id)
    save(
        client,
        traffic_limit_gb=3,
        speed_limit_mbps=2,
        node_speed_limits={node_id: 1, other["id"]: 0},
        node_device_limits={node_id: 2, other["id"]: 4},
    )
    complete(client, token, current(client, token))
    response = remove(client, node_id)
    assert response.status_code == 202, response.text
    overrides = detail(client)["user"]["limit_overrides"]
    assert overrides["node_speed_limits"] == {other["id"]: 0}
    assert overrides["node_device_limits"] == {other["id"]: 4}
    assert overrides["traffic_limit_gb"] == 3 and overrides["speed_limit_mbps"] == 2
    client.get("/api/v1/catalog/export").raise_for_status()
    drain(client, token, response.json()["id"])


def test_catalog_export_rejects_ambiguous_limit_node_names(env):
    client, _, server_id, node_id, *_ = env
    save(client, node_speed_limits={node_id: 2})
    other = (
        client.post(
            "/api/v1/nodes",
            json={
                "name": "Other node",
                "protocol": "vless",
                "server_id": server_id,
                "inbound_tag": "other",
            },
        )
        .raise_for_status()
        .json()["node"]
    )
    with client.app.state.inventory._coordinated_session() as session:
        session.get(ManagedNodeModel, other["id"]).name = "Tokyo vless"
        session.commit()
    assert client.get("/api/v1/catalog/export").status_code == 409


def test_subscriber_sees_own_effective_caps_but_cannot_change_them(tmp_path):
    from test_subscriber_auth import login, make

    _, operator, client = make(tmp_path, catalog=True)
    node_id = operator.get("/api/v1/nodes").json()["nodes"][0]["id"]
    save(
        operator,
        traffic_limit_gb=2,
        speed_limit_mbps=0,
        device_limit=2,
        node_speed_limits={node_id: 1},
    )
    operator.post(
        "/api/v1/users", json={"username": "bob", "remark": "private note"}
    ).raise_for_status()
    login(client).raise_for_status()
    profile = client.get("/api/v1/account/me?username=bob").raise_for_status().json()
    assert profile["username"] == "alice"
    assert profile["quota"]["traffic_limit_bytes"] == 2 * 1024**3
    assert profile["speed_limit_mbps"] == 0 and profile["device_limit"] == 2
    assert profile["node_limits"][0]["speed_limit_mbps"] == 1
    assert all(
        key not in str(profile)
        for key in ("private note", "inbound_tag", "credential", "server_id")
    )
    assert (
        client.put(
            "/api/v1/users/alice/settings", json=settings(operator, limit_overrides={})
        ).status_code
        == 401
    )
    assert client.put("/api/v1/account/me", json={"limit_overrides": {}}).status_code == 405
