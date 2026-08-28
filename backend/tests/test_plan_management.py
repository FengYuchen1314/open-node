from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from open_node.domain.plan_management import PlanRemoval
from open_node.domain.subscriptions import SubscriptionPlanAssignRequest
from open_node.services.inventory import (
    ProductUserModel,
    SubscriptionAccessModel,
    SubscriptionPlanModel,
)
from sqlalchemy import select, text
from test_subscription_access import complete, current, setup, state
from test_subscriptions import make_client

FIELDS = (
    "name",
    "description",
    "traffic_limit_gb",
    "cycle_days",
    "is_reset",
    "reset_day",
    "node_ids",
    "node_multipliers",
    "node_name_overrides",
    "node_name_override_enabled",
    "node_speed_limits",
    "node_device_limits",
    "speed_limit_mbps",
    "device_limit",
    "traffic_mode",
)


@pytest.fixture
def env(tmp_path):
    values = setup(tmp_path)
    client, token = values[:2]
    complete(client, token, current(client, token))
    return values


def base(env):
    return "/api/v1/plans/" + env[4]


def update_payload(env, **changes):
    response = env[0].get(base(env) + "/settings")
    assert response.status_code == 200, response.text
    data = response.json()
    return (
        {key: data["plan"][key] for key in FIELDS}
        | {
            "expected_revision": data["revision"],
            "acknowledge_runtime_restart": True,
        }
        | changes
    )


def removal(env, *, username=None):
    url = f"/api/v1/users/{username}/plan/removal" if username else base(env) + "/settings"
    response = env[0].get(url)
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "expected_revision": data["revision"],
        "confirm_name": username or data["plan"]["name"],
        "acknowledge_runtime_restart": True,
    }


def node(env):
    response = env[0].post(
        "/api/v1/nodes",
        json={
            "server_id": env[2],
            "name": "New node",
            "inbound_tag": "new-inbound",
            "protocol": "vless",
            "config": {"type": "vless", "server": "edge.example", "port": 444},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["node"]


def test_settings_edit_preserves_user_dates_resets_credentials_and_usage(env):
    client = env[0]
    before_user = client.get("/api/v1/users").json()["users"][0]
    credentials = client.get("/api/v1/users/alice/credentials").json()["credentials"]
    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": env[1],
            "stats": {
                "user": {
                    credentials[0]["email"]: {"uplink": 100, "downlink": 200},
                }
            },
        },
    ).raise_for_status()
    payload = update_payload(
        env,
        name="Changed",
        cycle_days=7,
        reset_day=20,
        is_reset=False,
        node_speed_limits={env[3]: 0},
        node_device_limits={env[3]: 0},
    )
    response = client.put(base(env) + "/settings", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["license_required"] is False
    command = current(client, env[1])
    assert command["body"]["entries"][0]["limiter"]["user"]["speed_limit"] == 0
    assert command["body"]["entries"][0]["limiter"]["user"]["device_limit"] == 0
    complete(client, env[1], command)
    assert client.get("/api/v1/users").json()["users"][0] == before_user
    assert client.get("/api/v1/users/alice/credentials").json()["credentials"] == credentials
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 300
    assert (
        client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
        == token
    )


def test_membership_edit_disables_old_enables_new_and_keeps_credential_identity(env):
    fresh = node(env)
    before = env[0].get("/api/v1/users/alice/credentials").json()["credentials"][0]
    response = env[0].put(
        base(env) + "/settings",
        json=update_payload(
            env,
            node_ids=[fresh["id"]],
            node_multipliers={},
            speed_limit_mbps=2,
            device_limit=1,
        ),
    )
    assert response.status_code == 200, response.text
    command = current(env[0], env[1])
    entries = {entry["tag"]: entry for entry in command["body"]["entries"]}
    assert entries["vless-443"]["enabled"] is False
    assert entries["new-inbound"]["enabled"] is True
    assert entries["new-inbound"]["limiter"]["user"]["speed_limit"] == 250000
    complete(env[0], env[1], command)
    credentials = env[0].get("/api/v1/users/alice/credentials").json()["credentials"]
    assert next(item for item in credentials if item["id"] == before["id"]) == before
    reverted = env[0].put(
        base(env) + "/settings",
        json=update_payload(
            env,
            node_ids=[env[3]],
            node_multipliers={},
        ),
    )
    assert reverted.status_code == 200, reverted.text
    command = current(env[0], env[1])
    restored = next(entry for entry in command["body"]["entries"] if entry["tag"] == "vless-443")
    assert restored["enabled"] and restored["client"]["id"] == before["credential"]["id"]


def test_preview_only_subscribers_get_stable_credentials_without_enrollment(tmp_path):
    env = setup(tmp_path, queue=False)
    new = node(env)
    response = env[0].put(
        base(env) + "/settings",
        json=update_payload(
            env,
            node_ids=[env[3], new["id"]],
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["commands"] == [] and response.json()["warnings"]
    assert state(env[0])["managed"] is False
    assert len(env[0].get("/api/v1/users/alice/credentials").json()["credentials"]) == 2


def test_plan_edits_reject_stale_settings_membership_and_duplicate_name(env):
    payload = update_payload(env)
    env[0].put(base(env) + "/settings", json=payload | {"name": "New"}).raise_for_status()
    assert env[0].put(base(env) + "/settings", json=payload).status_code == 409
    payload = update_payload(env)
    env[0].post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    env[0].post("/api/v1/users/bob/plan", json={"plan_id": env[4]}).raise_for_status()
    assert env[0].put(base(env) + "/settings", json=payload).status_code == 409
    env[0].post("/api/v1/plans", json={"name": "Taken", "traffic_limit_gb": 1}).raise_for_status()
    assert (
        env[0].put(base(env) + "/settings", json=update_payload(env, name="Taken")).status_code
        == 409
    )


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"traffic_limit_gb": "nan"},
        {"traffic_limit_gb": 1e100},
        {"traffic_limit_gb": -1},
        {"traffic_limit_gb": 1e-12},
        {"cycle_days": 0},
        {"device_limit": -1},
        {"is_reset": True, "reset_day": 0},
        {"acknowledge_runtime_restart": False},
        {"node_speed_limits": {str(uuid4()): 1}},
        {"unexpected": True},
    ],
)
def test_update_validation(env, change):
    assert (
        env[0].put(base(env) + "/settings", json=update_payload(env, **change)).status_code == 422
    )


def test_quota_accepts_one_byte_without_rounding_to_zero(env):
    response = env[0].put(
        base(env) + "/settings", json=update_payload(env, traffic_limit_gb=1 / (1024**3))
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["traffic_limit_bytes"] == 1


def test_node_validation(env):
    assert (
        env[0]
        .put(base(env) + "/settings", json=update_payload(env, node_ids=[env[3], env[3]]))
        .status_code
        == 422
    )
    assert (
        env[0]
        .put(base(env) + "/settings", json=update_payload(env, node_multipliers={env[3]: -1}))
        .status_code
        == 422
    )
    assert (
        env[0]
        .put(
            base(env) + "/settings",
            json=update_payload(env, node_ids=[str(uuid4())], node_multipliers={}),
        )
        .status_code
        == 404
    )


def test_unassign_preserves_user_usage_credentials_and_token_and_retries(env):
    client = env[0]
    credentials = client.get("/api/v1/users/alice/credentials").json()["credentials"]
    token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": env[1],
            "stats": {
                "user": {
                    credentials[0]["email"]: {"uplink": 100, "downlink": 200},
                }
            },
        },
    ).raise_for_status()
    response = client.post("/api/v1/users/alice/plan/remove", json=removal(env, username="alice"))
    assert response.status_code == 200, response.text
    user = client.get("/api/v1/users").json()["users"][0]
    assert (
        user["current_plan_id"] is None and user["plan_expires_at"] is None and not user["is_reset"]
    )
    assert len(client.get("/api/v1/plans").json()["plans"]) == 1
    assert client.get("/api/v1/subscribe/" + token).status_code == 404
    command = current(client, env[1])
    assert not any(item["enabled"] for item in command["body"]["entries"])
    complete(client, env[1], command, status=500, body={"error": "runtime failed"})
    assert state(client)["servers"][0]["status"] == "failed"
    client.post("/api/v1/users/alice/access/sync").raise_for_status()
    command = current(client, env[1])
    complete(client, env[1], command)
    assert state(client)["servers"][0]["status"] == "applied"
    assert client.get("/api/v1/users/alice/credentials").json()["credentials"] == credentials
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 300
    assert (
        client.post("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
        == token
    )
    client.post(
        "/api/v1/users/alice/plan", json={"plan_id": env[4], "queue_agent_commands": True}
    ).raise_for_status()
    restored = current(client, env[1])
    assert restored["body"]["entries"][0]["client"]["id"] == credentials[0]["credential"]["id"]


def test_unassign_tracks_preview_credentials_for_revocation(tmp_path):
    env = setup(tmp_path, queue=False)
    response = env[0].post("/api/v1/users/alice/plan/remove", json=removal(env, username="alice"))
    assert response.status_code == 200, response.text
    assert state(env[0])["managed"]
    command = current(env[0], env[1])
    assert not command["body"]["entries"][0]["enabled"]


def test_delete_plan_unbinds_all_users_but_not_other_plan(env):
    client = env[0]
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    client.post("/api/v1/users/bob/plan", json={"plan_id": env[4]}).raise_for_status()
    other = client.post("/api/v1/plans", json={"name": "Other", "traffic_limit_gb": 1}).json()[
        "plan"
    ]
    response = client.post(base(env) + "/remove", json=removal(env))
    assert response.status_code == 200, response.text
    assert response.json()["affected_users"] == ["alice", "bob"]
    assert client.get("/api/v1/plans").json()["plans"] == [other]
    assert all(
        item["current_plan_id"] is None for item in client.get("/api/v1/users").json()["users"]
    )
    assert client.get(base(env) + "/settings").status_code == 404
    with client.app.state.inventory._session() as session:
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert session.scalars(select(SubscriptionAccessModel)).all()


def test_unassign_stale_assignment_and_missing_confirmation(env):
    payload = removal(env, username="alice")
    assert (
        env[0]
        .post("/api/v1/users/alice/plan/remove", json=payload | {"confirm_name": "wrong"})
        .status_code
        == 409
    )
    assert (
        env[0]
        .post(
            "/api/v1/users/alice/plan/remove", json=payload | {"acknowledge_runtime_restart": False}
        )
        .status_code
        == 422
    )
    other = (
        env[0].post("/api/v1/plans", json={"name": "Other", "traffic_limit_gb": 1}).json()["plan"]
    )
    env[0].post("/api/v1/users/alice/plan", json={"plan_id": other["id"]}).raise_for_status()
    assert env[0].post("/api/v1/users/alice/plan/remove", json=payload).status_code == 409
    assert env[0].get("/api/v1/users").json()["users"][0]["current_plan_id"] == other["id"]


def test_in_flight_enable_is_drained_before_plan_removal(tmp_path):
    env = setup(tmp_path)
    initial = current(env[0], env[1])
    response = env[0].post(base(env) + "/remove", json=removal(env))
    assert response.status_code == 200, response.text
    assert response.json()["commands"][0]["id"] == initial["id"]
    complete(env[0], env[1], initial)
    env[0].post("/api/v1/users/alice/access/sync").raise_for_status()
    revoked = current(env[0], env[1])
    assert not any(item["enabled"] for item in revoked["body"]["entries"])


def test_offline_revocation_intent_survives_backend_restart(env, tmp_path):
    env[0].post(base(env) + "/remove", json=removal(env)).raise_for_status()
    fresh = make_client(tmp_path)
    assert state(fresh)["servers"][0]["status"] == "pending"
    command = current(fresh, env[1])
    assert not command["body"]["entries"][0]["enabled"]
    complete(fresh, env[1], command)
    assert state(fresh)["servers"][0]["status"] == "applied"


def test_quota_reduction_revokes_without_resetting_traffic(env):
    email = env[0].get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
    env[0].post(
        "/api/v1/agents/telemetry",
        json={
            "token": env[1],
            "stats": {
                "user": {
                    email: {"uplink": 2 * 1024**3, "downlink": 2 * 1024**3},
                }
            },
        },
    ).raise_for_status()
    response = env[0].put(base(env) + "/settings", json=update_payload(env, traffic_limit_gb=1))
    assert response.status_code == 200, response.text
    assert not current(env[0], env[1])["body"]["entries"][0]["enabled"]
    assert env[0].get("/api/v1/users/alice/traffic").json()["total"] == 4 * 1024**3


def test_failed_update_rolls_back_settings_and_new_credentials(env):
    bad = (
        env[0]
        .post(
            "/api/v1/nodes", json={"name": "No inbound", "server_id": env[2], "protocol": "vless"}
        )
        .json()["node"]
    )
    before = env[0].get(base(env) + "/settings").json()
    credentials = env[0].get("/api/v1/users/alice/credentials").json()
    response = env[0].put(
        base(env) + "/settings", json=update_payload(env, node_ids=[bad["id"]], node_multipliers={})
    )
    assert response.status_code == 409, response.text
    assert env[0].get(base(env) + "/settings").json() == before
    assert env[0].get("/api/v1/users/alice/credentials").json() == credentials


def test_concurrent_assignment_and_plan_removal_cannot_create_dangling_plan(env):
    env[0].post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    payload = PlanRemoval.model_validate(removal(env))
    store = env[0].app.state.inventory
    barrier = Barrier(2)

    def run(action):
        barrier.wait()
        try:
            if action == "remove":
                store._plan_management().remove(env[4], payload)
            else:
                store.assign_subscription_plan(
                    "bob", SubscriptionPlanAssignRequest(plan_id=UUID(env[4]))
                )
        except ValueError:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ("remove", "assign")))
    with store._session() as session:
        for user in session.scalars(select(ProductUserModel)):
            assert not user.current_plan_id or session.get(
                SubscriptionPlanModel, user.current_plan_id
            )


def test_authentication_csrf_and_missing_records(env):
    anonymous = TestClient(env[0].app, base_url="https://testserver")
    assert anonymous.get(base(env) + "/settings").status_code == 401
    assert anonymous.post(base(env) + "/remove", json=removal(env)).status_code == 401
    assert env[0].get(f"/api/v1/plans/{uuid4()}/settings").status_code == 404
    assert env[0].get("/api/v1/users/missing/plan/removal").status_code == 404
    payload = update_payload(env)
    del env[0].headers["X-CSRF-Token"]
    assert env[0].put(base(env) + "/settings", json=payload).status_code == 403


def test_write_revision_remains_valid_across_sqlite_reads_and_new_credentials(env):
    fresh = node(env)
    payload = update_payload(env, node_ids=[env[3], fresh["id"]])
    response = env[0].put(base(env) + "/settings", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert env[0].get(base(env) + "/settings").json()["revision"] == result["revision"]
    second = {key: result["plan"][key] for key in FIELDS} | {
        "name": "Second edit",
        "expected_revision": result["revision"],
        "acknowledge_runtime_restart": True,
    }
    assert env[0].put(base(env) + "/settings", json=second).status_code == 200
