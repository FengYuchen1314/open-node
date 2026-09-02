import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from open_node.domain.inventory import XrayConfigSnapshotSource
from open_node.domain.server_management import ServerRemovalRequest
from open_node.domain.subscriptions import ManagedNodeCreate
from open_node.services.certificates import (
    CertificateHTTPLease,
    CertificateTarget,
    ManagedCertificate,
)
from open_node.services.inventory import (
    AgentChangeSetModel,
    Base,
    ChangeSetServerLockModel,
    CommandModel,
    ManagedNodeModel,
    ServerModel,
    SubscriptionTrafficLedgerModel,
)
from open_node.services.server_egress import ServerEgress
from sqlalchemy import delete, select, text
from test_inventory import make_client


@pytest.fixture
def env(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/v1/servers", json={"name": "alpha", "domain": "old.example"}).json()
    return client, created, client.app.state.inventory


def base(env):
    return "/api/v1/servers/" + env[1]["server"]["id"]


def node(env, name="node", host="old.example", server_id=None):
    response = env[0].post(
        "/api/v1/nodes",
        json={
            "name": name,
            "server_id": server_id or env[1]["server"]["id"],
            "protocol": "vless",
            "inbound_tag": name,
            "config": {
                "type": "vless",
                "server": host,
                "port": 443,
                "uuid": "{{client_uuid}}",
                "sni": "tls.example",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["node"]


def settings(env, **changes):
    read = env[0].get(base(env) + "/settings").json()
    fields = ("name", "ip_address", "ip_address_v6", "domain", "domain_v6", "ipv6_enabled")
    return (
        {key: read["server"][key] for key in fields}
        | {"expected_revision": read["revision"]}
        | changes
    )


def removal(env):
    response = env[0].get(base(env) + "/removal")
    assert response.status_code == 200, response.text
    preview = response.json()
    return preview, {
        "expected_revision": preview["revision"],
        "confirm_name": preview["server_name"],
        "acknowledge_remote_runtime": True,
    }


def remove(env):
    response = env[0].post(base(env) + "/remove", json=removal(env)[1])
    assert response.status_code == 200, response.text
    return response.json()


def report(env, up=100, down=200, email="user@example.com"):
    response = env[0].post(
        "/api/v1/agents/telemetry",
        json={
            "token": env[1]["agent_token"],
            "stats": {"user": {email: {"uplink": up, "downlink": down}}},
        },
    )
    assert response.status_code == 200, response.text


def xray_snapshot(env, server_id, config):
    with env[2]._coordinated_session() as session:
        env[2]._upsert_current_xray_config_snapshot(
            session,
            session.get(ServerModel, server_id),
            json.dumps(config),
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            datetime.now(UTC),
        )
        session.commit()


def settings_for(env, server_id, **changes):
    read = env[0].get(f"/api/v1/servers/{server_id}/settings").json()
    fields = ("name", "ip_address", "ip_address_v6", "domain", "domain_v6", "ipv6_enabled")
    return (
        {key: read["server"][key] for key in fields}
        | {"expected_revision": read["revision"]}
        | changes
    )


def removal_for(env, server_id):
    path = f"/api/v1/servers/{server_id}"
    response = env[0].get(path + "/removal")
    assert response.status_code == 200, response.text
    preview = response.json()
    return preview, {
        "expected_revision": preview["revision"],
        "confirm_name": preview["server_name"],
        "acknowledge_remote_runtime": True,
    }


def subscriber(env, nodes):
    env[0].post("/api/v1/users", json={"username": "user"}).raise_for_status()
    plan = (
        env[0]
        .post(
            "/api/v1/plans",
            json={
                "name": "plan",
                "node_ids": [item["id"] for item in nodes],
                "traffic_limit_gb": 1,
                "node_multipliers": {item["id"]: 2 for item in nodes},
                "node_name_overrides": {item["id"]: "Alias " + item["name"] for item in nodes},
                "node_name_override_enabled": True,
                "node_speed_limits": {item["id"]: 3 for item in nodes},
                "node_device_limits": {item["id"]: 4 for item in nodes},
            },
        )
        .json()["plan"]
    )
    assigned = env[0].post("/api/v1/users/user/plan", json={"plan_id": plan["id"]})
    assert assigned.status_code == 200, assigned.text
    credentials = env[0].get("/api/v1/users/user/credentials").json()["credentials"]
    return plan, credentials


def test_edit_syncs_only_matching_hosts_and_keeps_identity_and_credentials(env):
    inherited = node(env)
    custom = node(env, "custom", "custom.example")
    _, credentials = subscriber(env, [inherited, custom])
    response = env[0].put(
        base(env) + "/settings", json=settings(env, name=" renamed ", domain="NEW.Example.")
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["server"]["name"] == "renamed"
    assert updated["server"]["domain"] == "new.example"
    assert updated["server"]["id"] == env[1]["server"]["id"]
    assert updated["updated_node_ids"] == [inherited["id"]]
    nodes = {item["id"]: item for item in env[0].get("/api/v1/nodes").json()["nodes"]}
    assert nodes[inherited["id"]]["config"]["server"] == "new.example"
    assert nodes[inherited["id"]]["config"]["sni"] == "tls.example"
    assert nodes[custom["id"]]["config"]["server"] == "custom.example"
    assert env[0].get("/api/v1/users/user/credentials").json()["credentials"] == credentials
    report(env)
    assert env[2].authenticate_agent(env[1]["agent_token"]).name == "renamed"


def test_edit_opt_out_stale_revision_and_duplicate_names(env):
    inherited = node(env)
    old = settings(env, name="renamed", domain="new.example", sync_node_hosts=False)
    assert env[0].put(base(env) + "/settings", json=old).status_code == 200
    assert env[0].get("/api/v1/nodes").json()["nodes"][0]["id"] == inherited["id"]
    assert env[0].get("/api/v1/nodes").json()["nodes"][0]["config"]["server"] == "old.example"
    assert env[0].put(base(env) + "/settings", json=old).status_code == 409
    env[0].post("/api/v1/servers", json={"name": "taken"}).raise_for_status()
    assert env[0].put(base(env) + "/settings", json=settings(env, name="taken")).status_code == 409


def test_source_server_settings_and_removal_scan_remote_managed_client(env):
    target = env[0].post(
        "/api/v1/servers", json={"name": "target", "domain": "target.example"}
    ).json()
    target_node = node(
        env,
        name="target-node",
        host="target.example",
        server_id=target["server"]["id"],
    )
    _, _, email = ServerEgress._identity(env[1]["server"]["id"], target_node["id"])
    xray_snapshot(
        env,
        target["server"]["id"],
        {
            "inbounds": [
                {
                    "tag": target_node["inbound_tag"],
                    "protocol": "vless",
                    "settings": {"clients": [{"email": email, "id": "secret"}]},
                }
            ],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        },
    )

    update = env[0].put(
        base(env) + "/settings",
        json=settings(env, domain="new.example", sync_node_hosts=False),
    )
    assert update.status_code == 409
    assert "Disconnect managed server egress" in update.json()["detail"]
    preview, confirmation = removal(env)
    assert "Disconnect managed server egress" in " ".join(preview["blockers"])
    removed = env[0].post(base(env) + "/remove", json=confirmation)
    assert removed.status_code == 409
    assert env[0].get(base(env) + "/settings").json()["server"]["domain"] == "old.example"


def test_target_server_settings_sync_and_removal_scan_source_managed_records(env):
    target = env[0].post(
        "/api/v1/servers", json={"name": "target", "domain": "target.example"}
    ).json()
    target_id = target["server"]["id"]
    target_node = node(env, "target-node", "target.example", target_id)
    outbound_tag, marktag, _ = ServerEgress._identity(
        env[1]["server"]["id"], target_node["id"]
    )
    xray_snapshot(
        env,
        env[1]["server"]["id"],
        {
            "inbounds": [],
            "outbounds": [
                {"tag": outbound_tag, "protocol": "vless"},
                {"tag": "direct", "protocol": "freedom"},
            ],
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "marktag": marktag,
                        "outboundTag": outbound_tag,
                    }
                ]
            },
        },
    )

    update = env[0].put(
        f"/api/v1/servers/{target_id}/settings",
        json=settings_for(env, target_id, domain="new-target.example", sync_node_hosts=True),
    )
    assert update.status_code == 409
    assert "Disconnect managed server egress" in update.json()["detail"]
    persisted = next(
        item
        for item in env[0].get("/api/v1/nodes").json()["nodes"]
        if item["id"] == target_node["id"]
    )
    assert persisted["config"]["server"] == "target.example"

    preview, confirmation = removal_for(env, target_id)
    assert "Disconnect managed server egress" in " ".join(preview["blockers"])
    removed = env[0].post(f"/api/v1/servers/{target_id}/remove", json=confirmation)
    assert removed.status_code == 409


def test_change_set_lock_blocks_server_settings_update(env):
    change_id = str(uuid4())
    now = datetime.now(UTC)
    with env[2]._coordinated_session() as session:
        session.add(
            AgentChangeSetModel(
                id=change_id,
                name="in-flight server change",
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
            ChangeSetServerLockModel(
                server_id=env[1]["server"]["id"], change_set_id=change_id
            )
        )
        session.commit()

    response = env[0].put(base(env) + "/settings", json=settings(env, name="renamed"))
    assert response.status_code == 409
    assert "coordinated server change" in response.json()["detail"]
    assert env[0].get(base(env) + "/settings").json()["server"]["name"] == "alpha"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"name": "a\nb"},
        {"domain": "https://example.com"},
        {"domain": "host:443"},
        {"domain": "a..example"},
        {"ip_address": "::1"},
        {"ip_address_v6": "192.0.2.1"},
        {"agent_token": "replacement"},
        {"listen_port": 80},
    ],
)
def test_settings_validation_and_runtime_fields_rejected(env, changes):
    assert env[0].put(base(env) + "/settings", json=settings(env, **changes)).status_code == 422


def test_removal_prunes_plans_preserves_user_usage_and_revokes_agent(env, tmp_path):
    first = node(env)
    other = env[0].post("/api/v1/servers", json={"name": "other"}).json()
    remaining = node(env, "other-node", "other.example", other["server"]["id"])
    plan, credentials = subscriber(env, [first, remaining])
    email = next(item["email"] for item in credentials if item["node_id"] == first["id"])
    report(env, email=email)
    token = env[0].post("/api/v1/users/user/subscription-token").json()["subscription"]["token"]
    before = env[0].get("/api/v1/users/user/quota").json()["quota"]
    preview, confirmation = removal(env)
    assert preview["nodes"] == [{"id": first["id"], "name": first["name"]}]
    assert not preview["blockers"] and preview["user_count"] == 1
    report(env, up=150, down=250, email=email)  # Ordinary reports do not invalidate confirmation.
    response = env[0].post(base(env) + "/remove", json=confirmation)
    assert response.status_code == 200, response.text
    assert response.json()["removed_node_count"] == 1
    quota = env[0].get("/api/v1/users/user/quota").json()["quota"]
    assert quota["upload"] == 150 and quota["download"] == 250
    assert quota["charged_usage_bytes"] == 800
    assert quota["charged_usage_bytes"] >= before["charged_usage_bytes"]
    entries = env[0].get("/api/v1/users/user/traffic").json()["entries"]
    assert len(entries) == 1 and entries[0]["archived"]
    assert entries[0]["server_name"] == "alpha"
    assert entries[0]["charged_usage_bytes"] == 800
    updated = env[0].get("/api/v1/plans").json()["plans"][0]
    assert updated["id"] == plan["id"] and updated["node_ids"] == [remaining["id"]]
    assert updated["node_name_overrides"] == {remaining["id"]: "Alias " + remaining["name"]}
    assert updated["node_name_override_enabled"] is True
    for field in ("node_multipliers", "node_speed_limits", "node_device_limits"):
        assert first["id"] not in updated[field]
    assert (
        env[0].post("/api/v1/agents/heartbeat", json={"token": env[1]["agent_token"]}).status_code
        == 401
    )
    assert env[0].get(base(env) + "/settings").status_code == 404
    assert env[0].get("/api/v1/servers").json()[0]["id"] == other["server"]["id"]
    fresh = make_client(tmp_path)
    assert fresh.get("/api/v1/users/user/quota").json()["quota"]["download"] == 250
    assert fresh.get("/api/v1/subscribe/" + token).status_code == 200
    fresh.post("/api/v1/users/user/traffic/reset", json={}).raise_for_status()
    assert fresh.get("/api/v1/users/user/quota").json()["quota"]["charged_usage_bytes"] == 0
    with env[2]._session() as session:
        for table in Base.metadata.tables.values():
            for reference in table.foreign_keys:
                if reference.column.table.name == "servers":
                    assert (
                        session.execute(
                            select(table).where(
                                table.c[reference.parent.name] == env[1]["server"]["id"]
                            )
                        ).first()
                        is None
                    ), table.name
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_removal_prunes_user_limit_overrides_on_removed_nodes(env):
    first = node(env)
    other = env[0].post("/api/v1/servers", json={"name": "other"}).json()
    remaining = node(env, "other-node", "other.example", other["server"]["id"])
    subscriber(env, [first, remaining])
    view = env[0].get("/api/v1/users/user/settings").raise_for_status().json()
    values = {key: view["user"][key] for key in ("display_name", "email", "remark", "is_active")}
    env[0].put(
        "/api/v1/users/user/settings",
        json={
            **values,
            "expected_revision": view["revision"],
            "acknowledge_runtime_restart": True,
            "limit_overrides": {
                "traffic_limit_gb": 3,
                "node_speed_limits": {first["id"]: 2, remaining["id"]: 0},
                "node_device_limits": {first["id"]: 1},
            },
        },
    ).raise_for_status()
    remove(env)
    limits = (
        env[0]
        .get("/api/v1/users/user/settings")
        .raise_for_status()
        .json()["user"]["limit_overrides"]
    )
    assert limits["traffic_limit_gb"] == 3
    assert limits["node_speed_limits"] == {remaining["id"]: 0}
    assert limits["node_device_limits"] == {}


def test_removal_preserves_legacy_usage_without_ledgers(env):
    _, credentials = subscriber(env, [node(env)])
    report(env, email=credentials[0]["email"])
    with env[2]._coordinated_session() as session:
        session.execute(delete(SubscriptionTrafficLedgerModel))
        session.commit()
    assert env[0].get("/api/v1/users/user/quota").json()["quota"]["download"] == 200
    remove(env)
    assert env[0].get("/api/v1/users/user/quota").json()["quota"]["download"] == 200


def test_removal_rejects_new_nodes_wrong_name_and_missing_ack(env):
    _, payload = removal(env)
    assert (
        env[0].post(base(env) + "/remove", json=payload | {"confirm_name": "wrong"}).status_code
        == 409
    )
    assert (
        env[0]
        .post(base(env) + "/remove", json=payload | {"acknowledge_remote_runtime": False})
        .status_code
        == 422
    )
    node(env)
    assert env[0].post(base(env) + "/remove", json=payload).status_code == 409
    assert len(env[0].get("/api/v1/servers").json()) == 1


def test_change_set_blocker_and_immutable_archive_for_all_targets(env):
    other = env[0].post("/api/v1/servers", json={"name": "other"}).json()
    change = (
        env[0]
        .post(
            "/api/v1/change-sets",
            json={
                "name": "shared",
                "steps": [
                    {
                        "server_id": item["server"]["id"],
                        "forward": {
                            "method": "GET",
                            "path": "/api/child/system/info",
                        },
                    }
                    for item in (env[1], other)
                ],
            },
        )
        .json()["change_set"]
    )
    assert removal(env)[0]["blockers"]
    assert env[0].post(base(env) + "/remove", json=removal(env)[1]).status_code == 409
    env[0].post(f"/api/v1/change-sets/{change['id']}/rollback", json={}).raise_for_status()
    remove(env)
    archived = env[0].get(f"/api/v1/change-sets/{change['id']}").json()["change_set"]
    assert len(archived["steps"]) == 2
    assert all(step["archived"] for step in archived["steps"])
    assert {step["server_name"] for step in archived["steps"]} == {"alpha", "other"}
    assert env[0].post(f"/api/v1/change-sets/{change['id']}/rollback", json={}).status_code == 409
    assert env[0].post(f"/api/v1/change-sets/{change['id']}/dispatch").status_code == 409


def test_cross_server_command_dependency_blocks_removal(env):
    other = env[0].post("/api/v1/servers", json={"name": "other"}).json()
    first = (
        env[0]
        .post(base(env) + "/commands", json={"method": "GET", "path": "/api/child/system/info"})
        .json()["command"]
    )
    second = (
        env[0]
        .post(
            f"/api/v1/servers/{other['server']['id']}/commands",
            json={"method": "GET", "path": "/api/child/system/info"},
        )
        .json()["command"]
    )
    with env[2]._coordinated_session() as session:
        dependent = session.get(CommandModel, second["id"])
        dependent.depends_on_command_id = first["id"]
        session.commit()
    assert any("another server" in item for item in removal(env)[0]["blockers"])
    with env[2]._coordinated_session() as session:
        session.get(CommandModel, second["id"]).status = "skipped"
        session.commit()
    remove(env)
    with env[2]._session() as session:
        dependent = session.get(CommandModel, second["id"])
        assert dependent is not None and dependent.depends_on_command_id is None


def test_certificate_validation_and_cleanup_block_then_preserve_profile(env):
    identifier = str(uuid4())
    with env[0].app.state.certificates.write() as session:
        session.add(
            ManagedCertificate(
                id=identifier,
                name="cert",
                domains=["old.example"],
                validation_server_id=env[1]["server"]["id"],
                auto_renew=True,
                active_job_id="job",
            )
        )
        session.add(
            CertificateTarget(
                id=str(uuid4()),
                certificate_id=identifier,
                server_id=env[1]["server"]["id"],
                domain="old.example",
                cert_name="cert",
                reload="none",
                auto_deploy=True,
            )
        )
    assert any("validation is active" in item for item in removal(env)[0]["blockers"])
    with env[0].app.state.certificates.write() as session:
        session.get(ManagedCertificate, identifier).active_job_id = None
        session.add(
            CertificateHTTPLease(
                id="lease",
                certificate_id=identifier,
                job_id="job",
                server_id=env[1]["server"]["id"],
                presentation={},
                present_command_id="present",
            )
        )
    assert any("cleanup" in item for item in removal(env)[0]["blockers"])
    with env[0].app.state.certificates.write() as session:
        session.get(CertificateHTTPLease, "lease").released_at = 1
    remove(env)
    with env[0].app.state.certificates.session() as session:
        certificate = session.get(ManagedCertificate, identifier)
        assert certificate is not None and certificate.auto_renew is False
        assert "server removed" in certificate.last_error
        assert session.scalars(select(CertificateTarget)).all() == []


def test_concurrent_node_create_and_remove_cannot_leave_orphans(env):
    payload = ServerRemovalRequest.model_validate(removal(env)[1])
    barrier = Barrier(2)

    def run(action):
        barrier.wait()
        try:
            if action == "remove":
                env[2]._server_management().remove(env[1]["server"]["id"], payload)
            else:
                env[2].create_managed_node(
                    ManagedNodeCreate(
                        name="concurrent",
                        server_id=UUID(env[1]["server"]["id"]),
                        protocol="vless",
                    )
                )
        except ValueError:
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, ("remove", "create")))
    with env[2]._session() as session:
        for item in session.scalars(select(ManagedNodeModel)):
            assert session.get(ServerModel, item.server_id) is not None


def test_removal_revokes_connected_websocket_and_reconnect(env):
    with env[0].websocket_connect("/api/v1/agents/ws") as ws:
        ws.send_json(
            {"type": "auth", "payload": {"token": env[1]["agent_token"], "hostname": "agent"}}
        )
        assert ws.receive_json()["type"] == "auth_result"
        remove(env)
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with env[0].websocket_connect("/api/v1/agents/ws") as ws:
        ws.send_json(
            {"type": "auth", "payload": {"token": env[1]["agent_token"], "hostname": "agent"}}
        )
        assert ws.receive_json()["payload"]["success"] is False


def test_removed_agent_is_rejected_by_a_different_worker_connection(env):
    with env[0].websocket_connect("/api/v1/agents/ws") as ws:
        ws.send_json(
            {"type": "auth", "payload": {"token": env[1]["agent_token"], "hostname": "agent"}}
        )
        assert ws.receive_json()["type"] == "auth_result"
        # Direct storage removal models a write performed by another backend worker.
        env[2]._server_management().remove(
            env[1]["server"]["id"], ServerRemovalRequest.model_validate(removal(env)[1])
        )
        ws.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_automatic_reset_clears_archived_usage(env):
    _, credentials = subscriber(env, [node(env)])
    report(env, email=credentials[0]["email"])
    remove(env)
    from open_node.domain.subscriptions import SubscriptionDueTrafficResetRequest
    from open_node.services.inventory import ProductUserModel

    now = datetime(2030, 6, 15, 12, tzinfo=UTC)
    with env[2]._coordinated_session() as session:
        user = session.get(ProductUserModel, "user")
        user.is_reset = True
        user.reset_day = 15
        user.plan_started_at = now - timedelta(days=40)
        user.plan_expires_at = now + timedelta(days=30)
        session.commit()
    result = env[2].reset_due_subscription_traffic(SubscriptionDueTrafficResetRequest(now=now))
    assert result.summary.reset_users == 1
    assert env[0].get("/api/v1/users/user/traffic").json()["total"] == 0


def test_authorization_and_missing_servers(env):
    anonymous = TestClient(env[0].app, base_url="https://testserver")
    assert anonymous.get(base(env) + "/settings").status_code == 401
    assert anonymous.get(base(env) + "/removal").status_code == 401
    confirmation = removal(env)[1]
    assert anonymous.post(base(env) + "/remove", json=confirmation).status_code == 401
    assert env[0].get(f"/api/v1/servers/{uuid4()}/settings").status_code == 404
    del env[0].headers["X-CSRF-Token"]
    assert env[0].post(base(env) + "/remove", json=confirmation).status_code == 403


def test_remote_certificate_create_rechecks_server_after_preflight(env, monkeypatch):
    env[0].post(
        "/api/v1/agents/scan",
        json={
            "token": env[1]["agent_token"],
            "http01": {"version": 1, "standalone": True, "webroots": []},
        },
    ).raise_for_status()
    certificates = env[0].app.state.certificates
    original = certificates.check_challenge

    def remove_after_preflight(payload):
        original(payload)
        remove(env)

    monkeypatch.setattr(certificates, "check_challenge", remove_after_preflight)
    response = env[0].post(
        "/api/v1/certificates",
        json={
            "name": "remote",
            "domains": ["old.example"],
            "email": "operator@example.com",
            "challenge_type": "standalone",
            "validation_server_id": env[1]["server"]["id"],
            "accept_terms": True,
        },
    )
    assert response.status_code == 409, response.text
    with certificates.session() as session:
        assert session.scalars(select(ManagedCertificate)).all() == []


def test_existing_database_gains_archive_column_without_changing_history(env, tmp_path):
    change = (
        env[0]
        .post(
            "/api/v1/change-sets",
            json={
                "name": "existing",
                "steps": [
                    {
                        "server_id": env[1]["server"]["id"],
                        "forward": {
                            "method": "GET",
                            "path": "/api/child/system/info",
                        },
                    }
                ],
            },
        )
        .json()["change_set"]
    )
    with env[2]._engine.begin() as connection:
        connection.execute(text("ALTER TABLE agent_change_sets DROP COLUMN archived_steps"))
    fresh = make_client(tmp_path)
    assert fresh.get("/api/v1/change-sets/" + change["id"]).json()["change_set"] == change
    assert fresh.get(base(env) + "/settings").json()["server"]["name"] == "alpha"


def test_removal_rolls_back_all_records_if_usage_archive_fails(env, monkeypatch):
    plan, credentials = subscriber(env, [node(env)])
    report(env, email=credentials[0]["email"])
    payload = ServerRemovalRequest.model_validate(removal(env)[1])
    original = env[2]._subscription_user_traffic
    calls = 0

    def failing_archive(session, username):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected archive failure")
        return original(session, username)

    monkeypatch.setattr(env[2], "_subscription_user_traffic", failing_archive)
    with pytest.raises(RuntimeError, match="injected archive failure"):
        env[2]._server_management().remove(env[1]["server"]["id"], payload)
    assert env[2].authenticate_agent(env[1]["agent_token"]).name == "alpha"
    assert env[0].get("/api/v1/plans").json()["plans"] == [plan]
    assert env[0].get("/api/v1/users/user/credentials").json()["credentials"] == credentials
    assert env[0].get("/api/v1/users/user/traffic").json()["total"] == 300
    assert not env[0].get("/api/v1/users/user/traffic").json()["entries"][0]["archived"]
