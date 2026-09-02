import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from open_node.domain.inventory import (
    AgentCommandCreate,
    ServerCreate,
    XrayConfigSnapshotSource,
)
from open_node.services.inventory import (
    AgentChangeSetModel,
    ChangeSetServerLockModel,
    CommandModel,
    ManagedNodeModel,
    ManagedNodeRemovalModel,
    ServerModel,
    SubscriptionCredentialModel,
)
from open_node.services.node_cleanup import ENDPOINT
from open_node.services.server_egress import ServerEgress
from open_node.services.subscription_access import ENDPOINT as ACCESS
from sqlalchemy import Column, ForeignKey, MetaData, Table, select, text
from test_subscription_access import complete, current, lease, reconcile, setup
from test_subscriptions import make_client


@pytest.fixture
def env(tmp_path):
    values = setup(tmp_path)
    client, token = values[:2]
    complete(client, token, current(client, token))
    client.post(
        "/api/v1/agents/register",
        json={
            "token": token,
            "hostname": "nodes",
            "capabilities": {
                "rpc": True,
                "native_limiter": True,
                "subscription_access": True,
                "node_cleanup": True,
            },
        },
    ).raise_for_status()
    return values


def detail(client, identifier):
    return client.get(f"/api/v1/nodes/{identifier}/settings").raise_for_status().json()


def settings(client, identifier, **changes):
    view = detail(client, identifier)
    return {
        **{
            field: view["node"][field]
            for field in (
                "name",
                "tag",
                "tags",
                "enabled",
                "parent_id",
                "target_node_id",
                "client_template",
                "config",
            )
        },
        "expected_revision": view["revision"],
        "acknowledge_runtime_restart": True,
        **changes,
    }


def remove(client, identifier, **changes):
    view = detail(client, identifier)
    return client.post(
        f"/api/v1/nodes/{identifier}/remove",
        json={
            "confirm_name": view["node"]["name"],
            "expected_revision": view["revision"],
            "acknowledge_runtime_restart": True,
            **changes,
        },
    )


def job(client, identifier):
    return client.get("/api/v1/node-removals/" + identifier).raise_for_status().json()


def cleanup_result(client, token, command, *, impact=None, status=200):
    body = command["body"]
    receipt = {
        "applied": body["action"] == "apply",
        "revision": body.get("expected_revision", "a" * 64),
        "impact": impact
        or {
            "inbound_tags": body.get("inbound_tags", []),
            "outbound_tags": body.get("outbound_tags", []),
            "suspended_tags": [],
            "removed_rules": 0,
            "changed_rules": 0,
            "removed_limiter_policies": 0,
            "default_outbound_changed": False,
        },
    }
    if body.get("operation_id"):
        receipt["operation_id"] = body["operation_id"]
    return complete(
        client,
        token,
        command,
        status=status,
        body={"success": status < 400, "node_cleanup": receipt},
    )


def drain(client, token, identifier):
    actions = []
    for _ in range(12):
        reconcile(client)
        for command in lease(client, token):
            if command["path"] == ACCESS:
                complete(client, token, command)
            elif command["path"] == ENDPOINT:
                actions.append(command["body"])
                cleanup_result(client, token, command)
            else:
                complete(client, token, command, body={})
        if job(client, identifier)["status"] == "completed":
            return actions
    raise AssertionError(job(client, identifier))


def create(client, server_id, **changes):
    response = client.post(
        "/api/v1/nodes",
        json={
            "name": "Extra node",
            "server_id": server_id,
            "protocol": "vless",
            "inbound_tag": "extra",
            "config": {"type": "vless", "server": "example.com", "port": 443},
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["node"]


def test_edit_preserves_identity_usage_links_and_plan_dates(env):
    client, token, _, identifier, *_ = env
    old = client.get("/api/v1/users/alice/credentials").json()
    link = client.post("/api/v1/users/alice/subscription-token").json()
    user = client.get("/api/v1/users/alice/settings").json()["user"]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": token,
            "stats": {"user": {old["credentials"][0]["email"]: {"uplink": 100, "downlink": 200}}},
        },
    ).raise_for_status()
    body = settings(client, identifier, name="Renamed", tags=["alpha", "alpha", "beta"])
    response = client.put(f"/api/v1/nodes/{identifier}/settings", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["node"]["tags"] == ["alpha", "beta"]
    assert response.json()["revision"] == detail(client, identifier)["revision"]
    assert client.put(f"/api/v1/nodes/{identifier}/settings", json=body).status_code == 409
    assert client.get("/api/v1/users/alice/credentials").json() == old
    assert client.post("/api/v1/users/alice/subscription-token").json() == link
    assert client.get("/api/v1/users/alice/settings").json()["user"] == user
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 300


def test_managed_egress_must_be_disconnected_before_node_identity_change_or_removal(env):
    client, _, _, identifier, *_ = env
    store = client.app.state.inventory
    node_view = detail(client, identifier)["node"]
    target_server_id = node_view["server_id"]
    source_server_id = str(
        store.create_server(ServerCreate(name="egress source", ip_address="198.51.100.88")).id
    )
    _, _, email = ServerEgress._identity(source_server_id, str(identifier))
    now = datetime.now(UTC)
    source_config = {
        "inbounds": [],
        # Exercise the orphan case: the source records were removed manually,
        # but the dedicated credential still exists on the target server.
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": []},
    }
    target_config = {
        "inbounds": [
            {
                "tag": node_view["inbound_tag"],
                "protocol": node_view["protocol"],
                "settings": {"clients": [{"email": email, "id": "secret"}]},
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    with store._session() as session:
        for server_id, config in (
            (source_server_id, source_config),
            (target_server_id, target_config),
        ):
            store._upsert_current_xray_config_snapshot(
                session,
                session.get(ServerModel, server_id),
                json.dumps(config),
                XrayConfigSnapshotSource.AGENT_REPORT,
                None,
                now,
            )
        session.commit()

    update = client.put(
        f"/api/v1/nodes/{identifier}/settings",
        json=settings(client, identifier, config={**node_view["config"], "port": 8443}),
    )
    assert update.status_code == 409
    assert "Disconnect this node from server egress" in update.json()["detail"]
    removal = remove(client, identifier)
    assert removal.status_code == 409
    assert "Disconnect this node from server egress" in removal.json()["detail"]

    # Catalog import must apply the same protection even when this node has no
    # ordinary subscriber credential and only the dedicated egress client remains.
    with store._session() as session:
        for credential in session.scalars(
            select(SubscriptionCredentialModel).where(
                SubscriptionCredentialModel.node_id == str(identifier)
            )
        ):
            session.delete(credential)
        session.commit()
    catalog = client.get("/api/v1/catalog/export").raise_for_status().json()["catalog"]
    imported_node = next(item for item in catalog["nodes"] if item["name"] == node_view["name"])
    imported_node["config"] = {**imported_node["config"], "port": 9443}
    imported = client.post("/api/v1/catalog/import", json={"catalog": catalog})
    assert imported.status_code == 409
    assert "Disconnect this node from server egress" in imported.json()["detail"]


def test_coordinated_server_lock_blocks_node_identity_changes(env):
    client, _, server_id, identifier, *_ = env
    store = client.app.state.inventory
    now = datetime.now(UTC)
    change_id = str(uuid4())
    with store._session() as session:
        session.add(
            AgentChangeSetModel(
                id=change_id,
                name="in-flight egress",
                description="",
                status="dispatched",
                rollback_on_failure=True,
                rollback_reason="",
                resolution_reason="",
                coordination_version=1,
                archived_steps=[],
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ChangeSetServerLockModel(server_id=str(server_id), change_set_id=change_id)
        )
        session.commit()

    response = client.put(
        f"/api/v1/nodes/{identifier}/settings",
        json=settings(client, identifier, enabled=False),
    )
    assert response.status_code == 409
    assert "coordinated server change" in response.json()["detail"]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": " "},
        {"name": "bad\u0000"},
        {"enabled": 1},
        {"server_id": str(uuid4())},
        {"protocol": "trojan"},
        {"acknowledge_runtime_restart": False},
    ],
)
def test_settings_validation(env, changes):
    client, _, _, identifier, *_ = env
    assert (
        client.put(
            f"/api/v1/nodes/{identifier}/settings", json=settings(client, identifier, **changes)
        ).status_code
        == 422
    )


def test_disable_and_enable_only_selected_node(env):
    client, token, _, identifier, *_ = env
    client.put(
        f"/api/v1/nodes/{identifier}/settings", json=settings(client, identifier, enabled=False)
    ).raise_for_status()
    command = current(client, token)
    assert not command["body"]["entries"][0]["enabled"]
    complete(client, token, command)
    client.put(
        f"/api/v1/nodes/{identifier}/settings", json=settings(client, identifier, enabled=True)
    ).raise_for_status()
    assert current(client, token)["body"]["entries"][0]["enabled"]


def test_remove_waits_for_withdrawal_and_native_receipt_preserves_user_and_usage(env):
    client, token, server_id, identifier, plan_id, _ = env
    old_user = client.get("/api/v1/users/alice/settings").json()["user"]
    link = client.post("/api/v1/users/alice/subscription-token").json()
    email = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={
            "token": token,
            "stats": {"user": {email: {"uplink": 100, "downlink": 200}}},
        },
    ).raise_for_status()
    response = remove(client, identifier)
    assert response.status_code == 202, response.text
    removal = response.json()
    assert removal["status"] == "pending"
    assert detail(client, identifier)["node"]["removal_id"] == removal["id"]
    assert remove(client, identifier).json()["id"] == removal["id"]
    assert (
        client.put(
            f"/api/v1/nodes/{identifier}/settings", json=settings(client, identifier)
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/plans",
            json={"name": "invalid", "traffic_limit_gb": 1, "node_ids": [identifier]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/nodes",
            json={
                "server_id": server_id,
                "name": "blocked",
                "protocol": "vless",
            },
        ).status_code
        == 409
    )
    assert (
        client.get("/api/v1/catalog/export?include_credentials=true").json()["catalog"]["nodes"]
        == []
    )
    assert (
        client.get("/api/v1/catalog/export?include_credentials=true").json()["catalog"][
            "credentials"
        ]
        == []
    )
    assert detail(client, identifier)["node"]["enabled"] is False
    actions = drain(client, token, removal["id"])
    assert [body["action"] for body in actions] == ["preview", "apply"]
    assert actions[-1]["outbound_tags"] == ["tokyo-out"]
    assert client.get(f"/api/v1/nodes/{identifier}/settings").status_code == 404
    assert client.get("/api/v1/users/alice/settings").json()["user"] == old_user
    assert client.post("/api/v1/users/alice/subscription-token").json() == link
    assert client.get("/api/v1/users/alice/traffic").json()["total"] == 300
    assert client.get(f"/api/v1/plans/{plan_id}/settings").json()["plan"]["node_ids"] == []
    assert (
        client.post("/api/v1/node-removals/" + removal["id"] + "/retry").json()["status"]
        == "completed"
    )


def test_relationships_closure_shared_inbound_and_cross_server(env):
    client, _, server_id, *_ = env
    parent = create(client, server_id, name="Physical", inbound_tag="shared")
    alias = create(client, server_id, name="Alias", inbound_tag="shared")
    child = create(
        client,
        server_id,
        name="Routed",
        inbound_tag="shared",
        node_type="routed",
        parent_id=parent["id"],
        routed_outbound_tag="route",
    )
    remote = client.post("/api/v1/servers", json={"name": "Remote"}).json()["server"]["id"]
    cross = create(
        client,
        remote,
        name="Cross server",
        inbound_tag="remote",
        node_type="routed",
        target_node_id=child["id"],
        routed_outbound_tag="cross",
    )
    view = detail(client, parent["id"])
    assert {node["id"] for node in view["nodes"]} == {parent["id"], child["id"], cross["id"]}
    assert alias["id"] not in {node["id"] for node in view["nodes"]}
    assert next(step for step in view["servers"] if step["server_id"] == server_id)[
        "retained_inbound_tags"
    ] == ["shared"]
    invalid = client.put(
        f"/api/v1/nodes/{child['id']}/settings",
        json=settings(client, child["id"], target_node_id=cross["id"]),
    )
    assert invalid.status_code == 409, invalid.text
    invalid = client.put(
        f"/api/v1/nodes/{child['id']}/settings",
        json=settings(client, child["id"], parent_id=str(uuid4())),
    )
    assert invalid.status_code == 409, invalid.text
    exported = client.get("/api/v1/catalog/export").json()["catalog"]
    assert (
        next(node for node in exported["nodes"] if node["name"] == "Routed")["parent_name"]
        == "Physical"
    )
    assert (
        next(node for node in exported["nodes"] if node["name"] == "Cross server")[
            "target_node_name"
        ]
        == "Routed"
    )


def test_shared_physical_nodes_reuse_credentials_and_keep_listener(env):
    client, token, server_id, *_ = env
    parent = create(client, server_id, name="Physical", inbound_tag="shared")
    alias = create(client, server_id, name="Alias", inbound_tag="shared")
    plan = client.post(
        "/api/v1/plans",
        json={
            "name": "Aliases",
            "traffic_limit_gb": 1,
            "node_ids": [parent["id"], alias["id"]],
        },
    ).json()["plan"]
    client.post(
        "/api/v1/users/alice/plan", json={"plan_id": plan["id"], "queue_agent_commands": True}
    ).raise_for_status()
    complete(client, token, current(client, token))
    credentials = client.get("/api/v1/users/alice/credentials").json()["credentials"]
    shared = [entry for entry in credentials if entry["node_id"] in {parent["id"], alias["id"]}]
    assert len(shared) == 2 and shared[0]["credential"] == shared[1]["credential"]
    response = remove(client, parent["id"])
    assert response.status_code == 202, response.text
    removal = response.json()
    assert removal["servers"][0]["inbound_tags"] == []
    assert drain(client, token, removal["id"]) == []
    access = client.get("/api/v1/users/alice/access").json()
    assert any(
        entry["enabled"]
        for state in access["servers"]
        for entry in state["entries"]
        if entry["inbound_tag"] == "shared"
    )
    assert detail(client, alias["id"])["node"]["enabled"]


def test_old_raw_restore_is_retired_and_unrelated_mutation_waits(env):
    client, token, server_id, identifier, *_ = env
    store = client.app.state.inventory
    old = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]
    restore = store.create_command(
        UUID(server_id),
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/config",
            body={
                "config": json.dumps(
                    {
                        "inbounds": [
                            {"tag": "vless-443", "settings": {"clients": [old["credential"]]}}
                        ]
                    }
                )
            },
        ),
    )
    unrelated = store.create_command(
        UUID(server_id),
        AgentCommandCreate(
            method="POST",
            path="/api/child/outbounds",
            body={"action": "add", "outbound": {"tag": "another", "protocol": "freedom"}},
        ),
    )
    removal = remove(client, identifier).raise_for_status().json()
    leased = lease(client, token)
    assert str(unrelated.id) not in {item["id"] for item in leased}
    for command in leased:
        if command["path"] == ACCESS:
            complete(client, token, command)
    with store._session() as session:
        assert session.get(CommandModel, str(restore.id)).status == "skipped"
    drain(client, token, removal["id"])
    with store._session() as session:
        assert session.get(CommandModel, str(unrelated.id)).attempts > 0


def test_missing_operation_status_repreviews_after_failed_apply(env):
    client, token, _, identifier, *_ = env
    removal = remove(client, identifier).raise_for_status().json()
    complete(client, token, current(client, token))
    reconcile(client)
    preview = next(command for command in lease(client, token) if command["path"] == ENDPOINT)
    cleanup_result(client, token, preview)
    reconcile(client)
    apply = next(command for command in lease(client, token) if command["path"] == ENDPOINT)
    cleanup_result(client, token, apply, status=409)
    reconcile(client)
    assert job(client, removal["id"])["status"] == "failed"
    client.post("/api/v1/node-removals/" + removal["id"] + "/retry").raise_for_status()
    leased = lease(client, token)
    for command in leased:
        if command["path"] == ACCESS:
            complete(client, token, command)
    status = next(command for command in leased if command["path"] == ENDPOINT)
    assert status["body"] == {"action": "status", "operation_id": apply["body"]["operation_id"]}
    complete(
        client,
        token,
        status,
        body={
            "success": True,
            "node_cleanup": {
                "operation_id": apply["body"]["operation_id"],
                "exists": False,
                "applied": False,
                "revision": None,
                "impact": {},
            },
        },
    )
    actions = drain(client, token, removal["id"])
    assert [body["action"] for body in actions] == ["preview", "apply"]
    assert actions[-1]["operation_id"] != apply["body"]["operation_id"]


def test_schema_upgrade_adds_relationships(env):
    store = env[0].app.state.inventory
    original = ManagedNodeModel.__table__
    metadata = MetaData()
    ServerModel.__table__.to_metadata(metadata)
    legacy = Table(
        "legacy_managed_nodes",
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
            if column.name not in {"parent_id", "target_node_id", "removal_id"}
        ],
    )
    with store._engine.begin() as connection:
        legacy.create(connection)
        names = list(legacy.columns.keys())
        connection.execute(
            legacy.insert().from_select(names, select(*(original.c[name] for name in names)))
        )
        original.drop(connection)
        connection.execute(text("ALTER TABLE legacy_managed_nodes RENAME TO managed_nodes"))
    store.create_schema()
    with store._session() as session:
        assert session.get(ManagedNodeModel, env[3]).parent_id is None
        assert list(session.scalars(select(ManagedNodeRemovalModel))) == []


def test_pending_job_survives_controller_restart_and_blocks_import_sync_server_removal(
    env, tmp_path
):
    client, token, server_id, identifier, *_ = env
    catalog = client.get("/api/v1/catalog/export?include_credentials=true").json()["catalog"]
    removal = remove(client, identifier).raise_for_status().json()
    fresh = make_client(tmp_path)
    assert job(fresh, removal["id"])["status"] == "pending"
    assert detail(fresh, identifier)["node"]["removal_id"] == removal["id"]
    assert (
        fresh.post(
            "/api/v1/catalog/import", json={"catalog": catalog, "import_credentials": True}
        ).status_code
        == 409
    )
    assert (
        fresh.post(
            f"/api/v1/servers/{server_id}/xray/runtime/nodes/{identifier}/sync", json={}
        ).status_code
        == 409
    )
    preview = fresh.get(f"/api/v1/servers/{server_id}/removal").raise_for_status().json()
    assert any("node removals" in item for item in preview["blockers"])
    drain(fresh, token, removal["id"])


def test_cleanup_preview_protects_unselected_outbound_dependency(env):
    client, token, server_id, identifier, *_ = env
    create(client, server_id, name="Protected", node_type="routed", routed_outbound_tag="protected")
    removal = remove(client, identifier).raise_for_status().json()
    complete(client, token, current(client, token))
    reconcile(client)
    preview = next(command for command in lease(client, token) if command["path"] == ENDPOINT)
    cleanup_result(
        client,
        token,
        preview,
        impact={
            "inbound_tags": [],
            "outbound_tags": ["tokyo-out", "protected"],
            "suspended_tags": [],
            "default_outbound_changed": False,
        },
    )
    reconcile(client)
    state = job(client, removal["id"])
    assert state["status"] == "failed" and "another managed node" in state["servers"][0]["error"]
    assert not any(command["path"] == ENDPOINT for command in lease(client, token))
    assert detail(client, identifier)["node"]["removal_id"] == removal["id"]
    client.post("/api/v1/node-removals/" + removal["id"] + "/retry").raise_for_status()
    fresh = next(command for command in lease(client, token) if command["path"] == ENDPOINT)
    assert fresh["body"]["action"] == "preview" and fresh["id"] != preview["id"]


def test_failed_withdrawal_is_visible_and_retryable(env):
    client, token, _, identifier, *_ = env
    removal = remove(client, identifier).raise_for_status().json()
    command = current(client, token)
    complete(client, token, command, status=500, body={"success": False})
    reconcile(client)
    state = job(client, removal["id"])
    assert state["status"] == "failed" and state["servers"][0]["phase"] == "withdrawing"
    retried = (
        client.post("/api/v1/node-removals/" + removal["id"] + "/retry").raise_for_status().json()
    )
    assert retried["status"] == "pending"
    drain(client, token, removal["id"])


def test_retired_resource_tags_and_credentials_cannot_be_reimported(env):
    client, token, server_id, identifier, *_ = env
    catalog = client.get("/api/v1/catalog/export?include_credentials=true").json()["catalog"]
    removal = remove(client, identifier).raise_for_status().json()
    drain(client, token, removal["id"])
    assert (
        client.post(
            "/api/v1/catalog/import", json={"catalog": catalog, "import_credentials": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/nodes",
            json={
                "name": "Retired outbound",
                "server_id": server_id,
                "protocol": "vless",
                "node_type": "routed",
                "routed_outbound_tag": "tokyo-out",
            },
        ).status_code
        == 409
    )


def test_graph_roundtrip_and_wrong_parent_rejection(env):
    client, _, server_id, *_ = env
    parent = create(client, server_id, name="Parent", inbound_tag="graph")
    child = create(
        client,
        server_id,
        name="Child",
        node_type="routed",
        inbound_tag="graph",
        parent_id=parent["id"],
    )
    bad = client.put(
        f"/api/v1/nodes/{child['id']}/settings",
        json=settings(client, child["id"], parent_id=env[3]),
    )
    assert bad.status_code == 409
    catalog = client.get("/api/v1/catalog/export").json()["catalog"]
    assert client.post("/api/v1/catalog/import", json={"catalog": catalog}).status_code == 200
    assert detail(client, child["id"])["node"]["parent_id"] == parent["id"]


def test_stale_removal_revision_cannot_delete_a_new_descendant(env):
    client, _, server_id, identifier, *_ = env
    before = detail(client, identifier)
    create(
        client,
        server_id,
        name="New child",
        node_type="routed",
        inbound_tag="vless-443",
        parent_id=identifier,
    )
    response = client.post(
        f"/api/v1/nodes/{identifier}/remove",
        json={
            "confirm_name": before["node"]["name"],
            "expected_revision": before["revision"],
            "acknowledge_runtime_restart": True,
        },
    )
    assert response.status_code == 409
    assert detail(client, identifier)["node"]["removal_id"] is None
