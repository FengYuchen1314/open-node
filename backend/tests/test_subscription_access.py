from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from open_node.domain.inventory import AgentCommandCreate
from open_node.services.inventory import (
    CommandModel,
    ManagedNodeModel,
    ProductUserModel,
    SubscriptionPlanModel,
)
from open_node.services.subscription_access import ENDPOINT, SubscriptionAccessWorker
from sqlalchemy import select
from test_subscriptions import create_catalog_fixture, make_client


def setup(tmp_path, *, capable=True, queue=True):
    client = make_client(tmp_path)
    token, server_id, node_id, plan_id = create_catalog_fixture(client)
    response = client.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "access-agent",
            "capabilities": {"rpc": True, "native_limiter": True, "subscription_access": capable},
        },
    )
    assert response.status_code == 201, response.text
    response = client.post(
        "/api/v1/users/alice/plan",
        json={
            "plan_id": plan_id,
            "queue_agent_commands": queue,
        },
    )
    assert response.status_code == 200, response.text
    return client, token, server_id, node_id, plan_id, response.json()


def lease(client, token):
    response = client.post(
        "/api/v1/agents/commands/lease", json={"token": token, "max_commands": 10}
    )
    assert response.status_code == 200, response.text
    return response.json()["commands"]


def complete(client, token, command, *, status=200, body=None):
    if body is None:
        entries = command["body"]["entries"]
        body = {
            "success": True,
            "access": {
                "applied": True,
                "revision": command["body"]["revision"],
                "enabled": sum(item["enabled"] for item in entries),
                "disabled": sum(not item["enabled"] for item in entries),
            },
        }
    response = client.post(
        f"/api/v1/agents/commands/{command['id']}/result",
        json={
            "token": token,
            "status": status,
            "body": body,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["command"]


def state(client):
    response = client.get("/api/v1/users/alice/access")
    assert response.status_code == 200, response.text
    return response.json()


def reconcile(client, **kwargs):
    return client.app.state.inventory._subscription_access().run_once(**kwargs)


def current(client, token):
    return next(item for item in lease(client, token) if item["path"] == ENDPOINT)


def test_preview_never_enrolls_or_deploys_credentials(tmp_path):
    client, token, _, _, _, response = setup(tmp_path, queue=False)
    assert response["provisioning_batches"]
    assert response["commands"] == []
    assert not state(client)["managed"]
    assert reconcile(client) == []
    assert all(item["path"] != ENDPOINT for item in lease(client, token))
    assert client.post("/api/v1/users/alice/access/sync").json()["servers"] == []


def test_assignment_tracks_intent_atomically_and_only_confirmation_means_applied(tmp_path):
    client, token, _, _, _, response = setup(tmp_path)
    assert len(response["commands"]) == 1
    assert state(client)["servers"][0]["status"] == "pending"
    command = current(client, token)
    assert command["body"]["entries"][0]["limiter"]["user"]["speed_limit"] == 12500000
    assert complete(client, token, command)["status"] == "succeeded"
    assert state(client)["servers"][0]["status"] == "applied"
    assert reconcile(client) == []
    assert "client" not in state(client)["servers"][0]["entries"][0]


@pytest.mark.parametrize("cause", ["disabled", "expired", "quota_exceeded", "node_not_in_plan"])
def test_automatic_revoke_and_restore_preserve_credentials(tmp_path, cause):
    client, token, _, node_id, plan_id, _ = setup(tmp_path)
    initial = current(client, token)
    complete(client, token, initial)
    store = client.app.state.inventory
    with store._coordinated_session() as session:
        user = session.get(ProductUserModel, "alice")
        plan = session.get(SubscriptionPlanModel, plan_id)
        if cause == "disabled":
            user.is_active = False
        elif cause == "expired":
            user.plan_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif cause == "quota_exceeded":
            plan.traffic_limit_bytes = 1
        else:
            session.get(ManagedNodeModel, node_id).enabled = False
        session.commit()
    if cause == "quota_exceeded":
        email = initial["body"]["entries"][0]["client"]["email"]
        assert (
            client.post(
                "/api/v1/agents/telemetry",
                json={
                    "token": token,
                    "stats": {"user": {email: {"uplink": 100, "downlink": 100}}},
                },
            ).status_code
            == 200
        )
    reconcile(client)
    command = current(client, token)
    assert command["body"]["entries"][0]["enabled"] is False
    assert state(client)["servers"][0]["entries"][0]["reason"] == cause
    complete(client, token, command)
    assert state(client)["servers"][0]["status"] == "applied"
    with store._coordinated_session() as session:
        user = session.get(ProductUserModel, "alice")
        user.is_active = True
        user.plan_expires_at = datetime.now(UTC) + timedelta(days=1)
        session.get(SubscriptionPlanModel, plan_id).traffic_limit_bytes = 0
        session.get(ManagedNodeModel, node_id).enabled = True
        session.commit()
    reconcile(client)
    restored = current(client, token)
    assert restored["body"]["entries"][0]["enabled"] is True
    assert restored["body"]["entries"][0]["client"] == initial["body"]["entries"][0]["client"]
    assert complete(client, token, restored)["status"] == "succeeded"


def test_stale_unsent_restore_cannot_be_leased(tmp_path):
    client, token, _, _, _, response = setup(tmp_path)
    command = response["commands"][0]
    store = client.app.state.inventory
    with store._coordinated_session() as session:
        session.get(ProductUserModel, "alice").is_active = False
        session.commit()
    assert command["id"] not in [item["id"] for item in lease(client, token)]
    assert state(client)["servers"][0]["status"] == "failed"
    reconcile(client)
    assert current(client, token)["body"]["entries"][0]["enabled"] is False


def test_in_flight_provision_is_drained_before_revocation(tmp_path):
    client, token, _, _, _, _ = setup(tmp_path)
    initial = current(client, token)
    assert client.patch("/api/v1/users/alice/active", json={"is_active": False}).status_code == 200
    assert [str(item.id) for item in reconcile(client)] == [initial["id"]]
    complete(client, token, initial)
    assert state(client)["servers"][0]["status"] == "pending"
    reconcile(client)
    assert current(client, token)["body"]["entries"][0]["enabled"] is False


@pytest.mark.parametrize(
    "body",
    [{"success": True}, {"success": True, "access": {"applied": True, "revision": "a" * 64}}],
)
def test_unconfirmed_success_remains_failed_and_retries_with_a_new_id(tmp_path, body):
    client, token, _, _, _, _ = setup(tmp_path)
    initial = current(client, token)
    assert complete(client, token, initial, body=body)["status"] == "failed"
    assert state(client)["servers"][0]["status"] == "failed"
    assert reconcile(client) == []
    retried = reconcile(client, now=datetime.now(UTC) + timedelta(seconds=61))
    assert len(retried) == 1 and str(retried[0].id) != initial["id"]
    assert complete(client, token, current(client, token))["status"] == "succeeded"


def test_legacy_agent_is_not_sent_new_access_payload(tmp_path):
    client, token, _, _, _, response = setup(tmp_path, capable=False)
    assert response["commands"][0]["id"] not in [item["id"] for item in lease(client, token)]
    assert state(client)["servers"][0]["status"] == "failed"
    assert "upgrade" in state(client)["servers"][0]["error"]


def test_concurrent_workers_do_not_create_duplicate_commands(tmp_path):
    client, token, _, _, _, response = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reconcile(client), range(8)))
    assert {str(item[0].id) for item in results} == {response["commands"][0]["id"]}
    assert len([item for item in lease(client, token) if item["path"] == ENDPOINT]) == 1


def test_no_restart_is_rejected_before_any_assignment(tmp_path):
    client, _, _, _, plan_id, _ = setup(tmp_path, queue=False)
    response = client.post(
        "/api/v1/users/alice/plan",
        json={
            "plan_id": plan_id,
            "queue_agent_commands": True,
            "no_restart": True,
        },
    )
    assert response.status_code == 422
    assert not state(client)["managed"]


def test_unleased_confirmation_does_not_mark_access_applied(tmp_path):
    client, token, _, _, _, assignment = setup(tmp_path)
    assert complete(client, token, assignment["commands"][0])["status"] == "failed"
    assert state(client)["servers"][0]["status"] == "failed"


def test_explicit_sync_waits_for_a_fresh_confirmation(tmp_path):
    client, token, _, _, _, _ = setup(tmp_path)
    complete(client, token, current(client, token))
    assert state(client)["servers"][0]["status"] == "applied"
    synced = client.post("/api/v1/users/alice/access/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["servers"][0]["status"] == "pending"
    complete(client, token, current(client, token))
    assert state(client)["servers"][0]["status"] == "applied"


def test_unrelated_user_edits_do_not_reapply_managed_plan_limits(tmp_path):
    client, token, server_id, _, _, _ = setup(tmp_path)
    complete(client, token, current(client, token))
    queued = client.app.state.inventory.create_command(
        UUID(server_id),
        AgentCommandCreate(
            method="POST",
            path="/api/child/batch-apply",
            body={
                "inbound_clients": [
                    {"tag": "vless-443", "client": {"email": "unrelated", "id": "other"}}
                ]
            },
        ),
    )
    assert str(queued.id) in [item["id"] for item in lease(client, token)]
    complete(client, token, {"id": str(queued.id)}, body={"success": True})
    assert state(client)["servers"][0]["status"] == "applied"
    assert reconcile(client) == []


@pytest.mark.parametrize("body", [None, [], {}, {"entries": [None]}])
def test_invalid_raw_access_commands_do_not_break_the_lease_queue(tmp_path, body):
    client, token, server_id, _, _, _ = setup(tmp_path, queue=False)
    store = client.app.state.inventory
    queued = store.create_command(
        UUID(server_id), AgentCommandCreate(method="POST", path=ENDPOINT, body=body)
    )
    assert str(queued.id) not in [item["id"] for item in lease(client, token)]
    with store._session() as session:
        assert session.get(CommandModel, str(queued.id)).status == "skipped"


@pytest.mark.asyncio
async def test_worker_resets_due_traffic_and_dispatches_pending_access(tmp_path):
    client, _, _, _, _, _ = setup(tmp_path)
    store = client.app.state.inventory
    with store._coordinated_session() as session:
        user = session.get(ProductUserModel, "alice")
        user.plan_started_at = datetime.now(UTC) - timedelta(days=40)
        user.reset_day = 1
        session.commit()
    connections = type("Connections", (), {"dispatch_command": AsyncMock()})()
    worker = SubscriptionAccessWorker(store, connections)
    await worker.tick()
    assert connections.dispatch_command.await_count == 1
    with store._session() as session:
        reset_at = session.get(ProductUserModel, "alice").last_traffic_reset_at
        assert reset_at is not None
    await worker.tick()
    with store._session() as session:
        assert session.get(ProductUserModel, "alice").last_traffic_reset_at == reset_at
        assert (
            len(list(session.scalars(select(CommandModel).where(CommandModel.path == ENDPOINT))))
            == 1
        )


def test_old_queued_batch_is_adopted_and_cannot_restore_an_expired_user(tmp_path):
    client, token, server_id, _, _, assignment = setup(tmp_path, queue=False)
    store = client.app.state.inventory
    batch = store.create_command(
        UUID(server_id),
        AgentCommandCreate(
            method="POST",
            path="/api/child/batch-apply",
            body=assignment["provisioning_batches"][0]["body"],
        ),
    )
    store._subscription_access().backfill()
    assert state(client)["managed"]
    with store._coordinated_session() as session:
        session.get(ProductUserModel, "alice").plan_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
        session.commit()
    assert str(batch.id) not in [item["id"] for item in lease(client, token)]
    reconcile(client)
    assert current(client, token)["body"]["entries"][0]["enabled"] is False


def test_access_commands_wait_for_existing_change_set_reservations(tmp_path):
    from test_change_coordination import dispatch, plan

    client, token, server_id, _, _, assignment = setup(tmp_path)
    identifier = plan(client, [{"server": {"id": server_id}}])
    change_commands = dispatch(client, identifier)
    leased = lease(client, token)
    assert assignment["commands"][0]["id"] not in [item["id"] for item in leased]
    response = client.post(
        f"/api/v1/agents/commands/{change_commands[0]['id']}/result",
        json={
            "token": token,
            "status": 200,
            "body": {"success": True},
        },
    )
    assert response.status_code == 200, response.text
    assert current(client, token)["id"] == assignment["commands"][0]["id"]
