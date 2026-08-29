import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from open_node.domain.inventory import AgentCommandCreate
from open_node.services.inventory import (
    CommandModel,
    ProductUserModel,
    ProductUserRemovalModel,
    SubscriptionCredentialModel,
)
from sqlalchemy import Column, ForeignKey, MetaData, Table, select, text
from test_subscription_access import complete, current, lease, reconcile, setup, state
from test_subscriptions import make_client


@pytest.fixture
def env(tmp_path):
    values = setup(tmp_path)
    complete(values[0], values[1], current(values[0], values[1]))
    return values


def detail(client, username="alice"):
    return client.get(f"/api/v1/users/{username}/settings").raise_for_status().json()


def settings(client, **changes):
    read = detail(client)
    return {
        **{key: read["user"][key] for key in ("display_name", "email", "remark", "is_active")},
        "expected_revision": read["revision"],
        "acknowledge_runtime_restart": True,
        **changes,
    }


def removal(client, username="alice", **changes):
    return {
        "expected_revision": detail(client, username)["revision"],
        "confirm_name": username,
        "acknowledge_runtime_restart": True,
        **changes,
    }


def start(client, username="alice", **changes):
    response = client.post(
        f"/api/v1/users/{username}/remove", json=removal(client, username, **changes)
    )
    assert response.status_code == 202, response.text
    return response.json()


def job(client, identifier):
    return client.get("/api/v1/user-removals/" + identifier).raise_for_status().json()


def test_profile_edit_preserves_credentials_tokens_dates_and_usage(env):
    client = env[0]
    old = detail(client)["user"]
    credentials = client.get("/api/v1/users/alice/credentials").json()
    token = client.post("/api/v1/users/alice/subscription-token").json()
    email = credentials["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": env[1],
            "stats": {"user": {email: {"uplink": 100, "downlink": 200}}},
        },
    ).raise_for_status()
    response = client.put(
        "/api/v1/users/alice/settings",
        json=settings(
            client,
            display_name="Alice edited",
            email="new@example.com",
            remark="Billing note\nSecond line",
        ),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["commands"] == []
    assert updated["user"]["remark"] == "Billing note\nSecond line"
    for field in (
        "username",
        "role",
        "current_plan_id",
        "plan_started_at",
        "plan_expires_at",
        "reset_day",
    ):
        assert updated["user"][field] == old[field]
    assert client.get("/api/v1/users/alice/credentials").json() == credentials
    assert client.post("/api/v1/users/alice/subscription-token").json() == token
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 300
    assert detail(client)["revision"] == updated["revision"]
    payload = settings(client, remark="")
    assert client.put("/api/v1/users/alice/settings", json=payload).status_code == 200
    assert client.put("/api/v1/users/alice/settings", json=payload).status_code == 409
    exported = client.get("/api/v1/catalog/export").json()["catalog"]
    assert exported["users"][0]["remark"] == ""


@pytest.mark.parametrize(
    "changes",
    [
        {"display_name": " "},
        {"display_name": "bad\x00name"},
        {"email": "bad\nmail"},
        {"remark": "x" * 1001},
        {"is_active": 1},
        {"role": "admin"},
        {"username": "bob"},
        {"acknowledge_runtime_restart": False},
    ],
)
def test_profile_validation(env, changes):
    assert (
        env[0].put("/api/v1/users/alice/settings", json=settings(env[0], **changes)).status_code
        == 422
    )


def test_disable_and_restore_respect_quota_and_preserve_identity(env):
    client, token = env[:2]
    original = client.get("/api/v1/users/alice/credentials").json()
    response = client.put("/api/v1/users/alice/settings", json=settings(client, is_active=False))
    assert response.status_code == 200, response.text
    command = current(client, token)
    assert not command["body"]["entries"][0]["enabled"]
    complete(client, token, command)
    with client.app.state.inventory._coordinated_session() as session:
        session.get(ProductUserModel, "alice").plan_expires_at = datetime.now(UTC) - timedelta(
            days=1
        )
        session.commit()
    client.put(
        "/api/v1/users/alice/settings", json=settings(client, is_active=True)
    ).raise_for_status()
    assert state(client)["servers"][0]["entries"][0]["reason"] == "expired"
    assert state(client)["servers"][0]["status"] == "applied"
    assert client.get("/api/v1/users/alice/credentials").json() == original


def test_disabling_preview_credentials_enrolls_only_for_current_access_intent(tmp_path):
    client, token, *_ = setup(tmp_path, queue=False)
    client.patch("/api/v1/users/alice/active", json={"is_active": False}).raise_for_status()
    command = current(client, token)
    assert not command["body"]["entries"][0]["enabled"]
    complete(client, token, command)
    client.patch("/api/v1/users/alice/active", json={"is_active": True}).raise_for_status()
    assert current(client, token)["body"]["entries"][0]["enabled"]


def test_removal_waits_for_confirmation_then_purges_only_user_data(env):
    client, token = env[:2]
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    from open_node.services.subscription_templates import TemplatePreference
    from open_node.services.template_rendering import DEFAULT_CLASH

    template = (
        client.post(
            "/api/v1/subscription-templates",
            json={
                "name": "alice-private.yaml",
                "format": "clash",
                "content": DEFAULT_CLASH,
                "owner_username": "alice",
                "is_public": False,
            },
        )
        .raise_for_status()
        .json()
    )
    template_settings = client.get(
        "/api/v1/subscription-templates/settings", params={"username": "alice"}
    ).json()
    client.put(
        "/api/v1/subscription-templates/settings",
        params={"username": "alice"},
        json={
            "expected_revision": template_settings["revision"],
            "enabled": True,
            "clash_template_id": template["id"],
            "surge_template_id": None,
        },
    ).raise_for_status()
    old_token = client.post("/api/v1/users/alice/subscription-token").json()["subscription"][
        "token"
    ]
    payload = removal(client)
    started = client.post("/api/v1/users/alice/remove", json=payload).json()
    assert started["status"] == "pending"
    assert client.get("/api/v1/subscribe/" + old_token).status_code == 404
    assert detail(client)["user"]["removal_id"] == started["id"]
    assert client.post("/api/v1/users/alice/remove", json=payload).json()["id"] == started["id"]
    assert client.post("/api/v1/users/alice/plan", json={"plan_id": env[4]}).status_code == 409
    assert client.get("/api/v1/users/alice/subscription-token").status_code == 409
    assert client.post("/api/v1/users/alice/subscription-token").status_code == 409
    assert client.post("/api/v1/users/alice/subscription-token/reset").status_code == 409
    assert client.patch("/api/v1/users/alice/active", json={"is_active": True}).status_code == 409
    assert client.put("/api/v1/users/alice/settings", json=settings(client)).status_code == 409
    exported = client.get("/api/v1/catalog/export?include_credentials=true").json()["catalog"]
    assert [user["username"] for user in exported["users"]] == ["bob"]
    assert exported["credentials"] == []
    assert exported["templates"][0]["owner_username"] is None
    assert exported["template_preferences"] == []
    command = current(client, token)
    assert not command["body"]["entries"][0]["enabled"]
    reconcile(client)
    assert job(client, started["id"])["status"] == "pending"
    complete(client, token, command)
    reconcile(client)
    finished = job(client, started["id"])
    assert finished["status"] == "completed" and finished["servers"][0]["status"] == "applied"
    assert client.get("/api/v1/users/alice/settings").status_code == 404
    assert [user["username"] for user in client.get("/api/v1/users").json()["users"]] == ["bob"]
    assert len(client.get("/api/v1/plans").json()["plans"]) == 1
    assert len(client.get("/api/v1/nodes").json()["nodes"]) == 1
    orphan = (
        client.get("/api/v1/subscription-templates/" + template["id"]).raise_for_status().json()
    )
    assert orphan["owner_username"] is None and not orphan["is_public"]
    with client.app.state.inventory._session() as session:
        assert session.get(TemplatePreference, "user:alice") is None
        for table in (
            "subscription_credentials",
            "subscription_access",
            "subscription_traffic_ledger",
            "subscription_archived_traffic",
            "product_user_subscription_tokens",
        ):
            assert (
                session.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE username='alice'")
                ).scalar()
                == 0
            )
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_offline_and_failed_removal_survives_restart_and_retry(env, tmp_path):
    client, token = env[:2]
    started = start(client)
    fresh = make_client(tmp_path)
    command = current(fresh, token)
    complete(fresh, token, command, status=500, body={"error": "offline runtime"})
    reconcile(fresh)
    assert job(fresh, started["id"])["status"] == "failed"
    fresh.post("/api/v1/user-removals/" + started["id"] + "/retry").raise_for_status()
    command = current(fresh, token)
    complete(fresh, token, command)
    reconcile(fresh)
    assert job(fresh, started["id"])["status"] == "completed"


def test_inflight_enable_drains_before_removal_and_old_commands_cannot_restore(tmp_path):
    client, token, server_id, *_ = setup(tmp_path)
    old = current(client, token)
    started = start(client)
    with client.app.state.inventory._coordinated_session() as session:
        assert client.app.state.inventory._subscription_access().can_lease(
            session, session.get(CommandModel, old["id"]), datetime.now(UTC)
        )
    reconcile(client)
    assert job(client, started["id"])["status"] == "pending"
    complete(client, token, old)
    reconcile(client)
    withdrawn = current(client, token)
    assert not withdrawn["body"]["entries"][0]["enabled"]
    complete(client, token, withdrawn)
    reconcile(client)
    assert job(client, started["id"])["status"] == "completed"
    store = client.app.state.inventory
    obsolete = store.create_command(
        UUID(server_id),
        AgentCommandCreate(
            method="POST",
            path=old["path"],
            body=old["body"],
        ),
    )
    assert not any(item["id"] == str(obsolete.id) for item in lease(client, token))
    with store._session() as session:
        assert session.get(CommandModel, str(obsolete.id)).status == "skipped"
        record = session.get(ProductUserRemovalModel, started["id"])
        assert old["body"]["entries"][0]["client"]["id"] not in json.dumps(record.fingerprints)


def test_recreated_username_gets_new_credentials_labels_and_zero_usage(env):
    client, token = env[:2]
    old = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": token,
            "stats": {"user": {old["email"]: {"uplink": 1000, "downlink": 2000}}},
        },
    ).raise_for_status()
    started = start(client)
    complete(client, token, current(client, token))
    reconcile(client)
    assert job(client, started["id"])["status"] == "completed"
    client.post("/api/v1/users", json={"username": "alice"}).raise_for_status()
    client.post("/api/v1/users/alice/plan", json={"plan_id": env[4]}).raise_for_status()
    fresh = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]
    assert fresh["email"] != old["email"] and fresh["credential"] != old["credential"]
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 0
    assert job(client, started["id"])["status"] == "completed"


def test_unmanaged_credentials_require_explicit_acknowledgment(tmp_path):
    client, _, _, node_id, *_ = setup(tmp_path, queue=False)
    store = client.app.state.inventory
    with store._coordinated_session() as session:
        credential = session.scalar(select(SubscriptionCredentialModel))
        credential.inbound_tag = None
        session.commit()
    assert detail(client)["warnings"]
    assert client.post("/api/v1/users/alice/remove", json=removal(client)).status_code == 409
    started = start(client, acknowledge_unmanaged_credentials=True)
    assert started["status"] == "completed" and started["warnings"]


def test_pending_user_removal_blocks_server_removal(env):
    client = env[0]
    start(client)
    preview = client.get(f"/api/v1/servers/{env[2]}/removal").json()
    assert any("user removals" in item for item in preview["blockers"])


def test_admin_users_cannot_be_disabled_or_removed(env):
    client = env[0]
    with client.app.state.inventory._coordinated_session() as session:
        session.get(ProductUserModel, "alice").role = "admin"
        session.commit()
    assert client.patch("/api/v1/users/alice/active", json={"is_active": False}).status_code == 409
    assert (
        client.put(
            "/api/v1/users/alice/settings", json=settings(client, is_active=False)
        ).status_code
        == 409
    )
    assert client.post("/api/v1/users/alice/remove", json=removal(client)).status_code == 409
    assert (
        client.put(
            "/api/v1/users/alice/settings", json=settings(client, remark="admin note")
        ).status_code
        == 200
    )


def test_auth_csrf_and_missing_records(env):
    client = env[0]
    anonymous = TestClient(client.app, base_url="https://testserver")
    assert anonymous.get("/api/v1/users/alice/settings").status_code == 401
    assert anonymous.post("/api/v1/users/alice/remove", json=removal(client)).status_code == 401
    assert client.get(f"/api/v1/user-removals/{uuid4()}").status_code == 404
    assert client.get("/api/v1/users/unknown/settings").status_code == 404
    payload = settings(client)
    del client.headers["X-CSRF-Token"]
    assert client.put("/api/v1/users/alice/settings", json=payload).status_code == 403


def test_pending_and_retired_credentials_cannot_be_reimported(env):
    client = env[0]
    catalog = client.get("/api/v1/catalog/export?include_credentials=true").json()["catalog"]
    started = start(client)
    request = {"catalog": catalog, "import_credentials": True}
    assert client.post("/api/v1/catalog/import", json=request).status_code == 409
    complete(client, env[1], current(client, env[1]))
    reconcile(client)
    assert job(client, started["id"])["status"] == "completed"
    assert client.post("/api/v1/catalog/import", json=request).status_code == 409
    assert client.get("/api/v1/users").json()["users"] == []


@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/api/child/inbounds",
            lambda client: {"action": "add-client", "tag": "edge", "client": client},
        ),
        (
            "/api/child/batch-apply",
            lambda client: {"inbound_clients": [{"tag": "edge", "client": client}]},
        ),
        (
            "/api/child/xray/config",
            lambda client: {"config": {"inbounds": [{"settings": {"clients": [client]}}]}},
        ),
        (
            "/api/child/xray/config",
            lambda client: {
                "content": json.dumps({"inbounds": [{"settings": {"clients": [client]}}]})
            },
        ),
    ],
)
def test_retirement_detects_structured_restores_without_matching_new_credentials(env, path, body):
    client = env[0]
    old = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["credential"]
    started = start(client)
    store = client.app.state.inventory
    with store._session() as session:
        retired = session.get(ProductUserRemovalModel, started["id"])
        command = CommandModel(server_id=env[2], method="POST", path=path, body=body(old))
        assert store._user_management().restores(command, retired.fingerprints)
        command.body = body({**old, "id": str(uuid4())})
        assert not store._user_management().restores(command, retired.fingerprints)


def test_shared_credential_label_blocks_removal_and_disabling(env):
    client = env[0]
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    client.post("/api/v1/users/bob/plan", json={"plan_id": env[4]}).raise_for_status()
    store = client.app.state.inventory
    with store._coordinated_session() as session:
        rows = session.scalars(
            select(SubscriptionCredentialModel).order_by(SubscriptionCredentialModel.username)
        ).all()
        rows[1].email = rows[0].email
        session.commit()
    assert client.post("/api/v1/users/alice/remove", json=removal(client)).status_code == 409
    assert client.patch("/api/v1/users/alice/active", json={"is_active": False}).status_code == 409
    assert detail(client)["user"]["is_active"]


def test_existing_database_adds_user_fields_without_changing_credentials(env, tmp_path):
    client = env[0]
    credentials = client.get("/api/v1/users/alice/credentials").json()
    original = ProductUserModel.__table__
    metadata = MetaData()
    from open_node.services.inventory import SubscriptionPlanModel

    SubscriptionPlanModel.__table__.to_metadata(metadata)
    legacy = Table(
        "legacy_product_users",
        metadata,
        *[
            Column(
                column.name,
                column.type,
                *(ForeignKey(key.target_fullname) for key in column.foreign_keys),
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in original.columns
            if column.name not in {"removal_id", "remark"}
        ],
    )
    with client.app.state.inventory._engine.begin() as connection:
        legacy.create(connection)
        names = list(legacy.columns.keys())
        connection.execute(
            legacy.insert().from_select(names, select(*(original.c[name] for name in names)))
        )
        original.drop(connection)
        connection.execute(text("ALTER TABLE legacy_product_users RENAME TO product_users"))
    fresh = make_client(tmp_path)
    assert detail(fresh)["user"]["remark"] == ""
    assert detail(fresh)["user"]["removal_id"] is None
    assert fresh.get("/api/v1/users/alice/credentials").json() == credentials


def test_removal_waits_for_opaque_runtime_work_and_reconfirms_withdrawal(env):
    client, token = env[:2]
    store = client.app.state.inventory
    opaque = store.create_command(
        UUID(env[2]),
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/rollback",
            body={},
        ),
    )
    started = start(client)
    leased = lease(client, token)
    old = next(command for command in leased if command["id"] == str(opaque.id))
    withdrawal = next(
        command for command in leased if command["path"].endswith("subscription-access")
    )
    complete(client, token, withdrawal)
    reconcile(client)
    assert job(client, started["id"])["status"] == "pending"
    complete(client, token, old, body={"success": True})
    reconcile(client)
    repeated = current(client, token)
    assert not repeated["body"]["entries"][0]["enabled"]
    complete(client, token, repeated)
    reconcile(client)
    assert job(client, started["id"])["status"] == "completed"


def test_removal_marker_prevents_access_even_if_old_writer_sets_active_and_plan(env):
    client = env[0]
    start(client)
    with client.app.state.inventory._coordinated_session() as session:
        user = session.get(ProductUserModel, "alice")
        user.is_active, user.current_plan_id = True, env[4]
        session.commit()
    assert state(client)["servers"][0]["entries"][0]["reason"] == "removing"
    assert not state(client)["servers"][0]["entries"][0]["enabled"]
    from open_node.services.inventory import SubscriptionUnavailableError

    store = client.app.state.inventory
    with store._session() as session, pytest.raises(SubscriptionUnavailableError):
        store._available_subscription_plan(session, session.get(ProductUserModel, "alice"))
