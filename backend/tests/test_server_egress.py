import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from open_node.api.backup import BackupHTTPMiddleware
from open_node.api.redaction import public_change_set
from open_node.api.routes.server_egress import router
from open_node.domain.inventory import ServerCreate, XrayConfigSnapshotSource
from open_node.domain.server_egress import ServerEgressPreviewRequest
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.inventory import InventoryStore, ManagedNodeModel, ServerModel
from open_node.services.server_egress import ServerEgress, ServerEgressConflict
from open_node.services.server_sharing import FederatedServerModel, ServerShareModel


def make_client(tmp_path: Path) -> TestClient:
    database = (tmp_path / "server-egress.db").as_posix()
    app = FastAPI()
    app.state.inventory = InventoryStore(f"sqlite:///{database}")
    app.state.inventory.create_schema()
    app.state.agent_connections = AgentConnectionManager()
    app.include_router(router, prefix="/api/v1")
    app.add_middleware(
        BackupHTTPMiddleware,
        barrier=BackupWriteBarrier(None),
        api_prefix="/api/v1",
    )
    return TestClient(app, base_url="https://testserver")


def seed_fixture(client: TestClient):
    store = client.app.state.inventory
    source_id = str(store.create_server(ServerCreate(name="source", ip_address="198.51.100.10")).id)
    target_id = str(store.create_server(ServerCreate(name="target", ip_address="203.0.113.20")).id)
    node_id = str(uuid4())
    now = datetime.now(UTC)
    source_config = {
        "inbounds": [],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {
            "rules": [
                {"type": "field", "network": "tcp,udp", "outboundTag": "direct"}
            ]
        },
        "observatory": {"subjectSelector": ["legacy"]},
    }
    target_config = {
        "inbounds": [
            {
                "tag": "target-vless",
                "protocol": "vless",
                "settings": {"clients": []},
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": []},
    }
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=node_id,
                name="Target VLESS",
                server_id=target_id,
                protocol="vless",
                node_type="physical",
                inbound_tag="target-vless",
                tags=[],
                enabled=True,
                client_template={"id": "{id}", "email": "{client_email}"},
                config={
                    "name": "Target VLESS",
                    "type": "vless",
                    "server": "{server_host}",
                    "port": 443,
                    "uuid": "{id}",
                    "network": "tcp",
                },
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        for server_id, config in (
            (source_id, source_config),
            (target_id, target_config),
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
    return source_id, target_id, node_id, source_config, target_config


def configure_tls_node(
    client: TestClient,
    target_id: str,
    node_id: str,
    **updates,
) -> None:
    store = client.app.state.inventory
    with store._session() as session:
        server = session.get(ServerModel, target_id)
        server.domain = "proxy.example.com"
        node = session.get(ManagedNodeModel, node_id)
        node.config = {
            **deepcopy(node.config),
            "tls": True,
            "servername": "tls.example.com",
            "alpn": ["h2"],
            "skip-cert-verify": True,
            **updates,
        }
        session.commit()


def test_managed_tls_candidate_requires_revision_bound_pin_and_injects_it(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, *_ = seed_fixture(client)
    configure_tls_node(client, target_id, node_id)

    catalog_response = client.get(f"/api/v1/servers/{source_id}/egress")
    assert catalog_response.status_code == 200, catalog_response.text
    candidate = next(
        item for item in catalog_response.json()["candidates"] if item["node_id"] == node_id
    )
    assert candidate["available"] is True
    assert candidate["tls_probe"] == {
        "protocol": "vless",
        "address": "proxy.example.com",
        "port": 443,
        "server_name": "tls.example.com",
        "alpn": ["h2"],
    }
    for credential_key in ("id", "uuid", "password", "users", "settings"):
        assert credential_key not in candidate["tls_probe"]

    missing = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    )
    assert missing.status_code == 409
    assert "pinned_peer_cert_sha256" in missing.json()["detail"]

    malformed = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id, "pinned_peer_cert_sha256": "not-a-pin"},
    )
    assert malformed.status_code == 422

    openssl_pin = ":".join(["AB"] * 32)
    request = {
        "target_node_id": node_id,
        "pinned_peer_cert_sha256": openssl_pin,
    }
    preview_response = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json=request,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["pinned_peer_cert_sha256"] == "ab" * 32
    assert preview["tls_probe"] == candidate["tls_probe"]

    stale = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            "target_node_id": node_id,
            "pinned_peer_cert_sha256": "cd" * 32,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()

    applied = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            **request,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    )
    assert applied.status_code == 200, applied.text
    change = client.app.state.inventory.get_change_set(applied.json()["change_set_id"])
    source_step = next(step for step in change.steps if str(step.server_id) == source_id)
    outbound = next(
        item
        for item in source_step.forward.body["config"]["outbounds"]
        if item.get("tag") == preview["outbound_tag"]
    )
    tls_settings = outbound["streamSettings"]["tlsSettings"]
    assert tls_settings["pinnedPeerCertSha256"] == "ab" * 32
    assert "allowInsecure" not in tls_settings


@pytest.mark.parametrize(
    "pin_key",
    ["tls-fingerprint", "pinnedPeerCertSha256", "pcs", "pinSHA256"],
)
def test_managed_tls_uses_official_node_pin_aliases_without_probe(tmp_path, pin_key):
    client = make_client(tmp_path)
    source_id, target_id, node_id, *_ = seed_fixture(client)
    configure_tls_node(
        client,
        target_id,
        node_id,
        **{pin_key: ":".join(["EF"] * 32)},
    )

    catalog = client.get(f"/api/v1/servers/{source_id}/egress").raise_for_status().json()
    candidate = next(item for item in catalog["candidates"] if item["node_id"] == node_id)
    assert candidate["tls_probe"] is None

    preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["pinned_peer_cert_sha256"] == "ef" * 32


def test_managed_tls_invalid_node_pin_is_treated_as_missing(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, *_ = seed_fixture(client)
    configure_tls_node(client, target_id, node_id, pcs="invalid")

    catalog = client.get(f"/api/v1/servers/{source_id}/egress").raise_for_status().json()
    candidate = next(item for item in catalog["candidates"] if item["node_id"] == node_id)
    assert candidate["tls_probe"] is not None
    preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    )
    assert preview.status_code == 409


def test_managed_tls_pin_skips_reality_probe_and_closes_hysteria_insecure_gap(tmp_path):
    service = ServerEgress(make_client(tmp_path).app.state.inventory)
    reality = {
        "protocol": "vless",
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {"serverName": "reality.example"},
        },
    }
    assert service._secure_generated_tls_outbound(reality, {}, None) == (None, None)

    insecure_hysteria = {
        "protocol": "hysteria",
        "settings": {"address": "hy.example", "port": 443},
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "tlsSettings": {"allowInsecure": True},
            "hysteriaSettings": {"version": 2},
        },
    }
    with pytest.raises(ServerEgressConflict, match="Hysteria.*tls-fingerprint"):
        service._secure_generated_tls_outbound(insecure_hysteria, {}, None)
    assert "allowInsecure" not in insecure_hysteria["streamSettings"]["tlsSettings"]

    trusted_hysteria = deepcopy(insecure_hysteria)
    assert service._secure_generated_tls_outbound(trusted_hysteria, {}, None) == (None, None)

    pinned_hysteria = deepcopy(insecure_hysteria)
    pinned_hysteria["streamSettings"]["tlsSettings"]["allowInsecure"] = True
    descriptor, pin = service._secure_generated_tls_outbound(
        pinned_hysteria,
        {"pinSHA256": ":".join(["12"] * 32)},
        None,
    )
    assert descriptor is None
    assert pin == "12" * 32
    assert pinned_hysteria["streamSettings"]["tlsSettings"] == {
        "pinnedPeerCertSha256": "12" * 32
    }


def test_catalog_preview_apply_are_safe_and_compile_guarded_cross_server_change(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, source_config, _ = seed_fixture(client)

    catalog = client.get(f"/api/v1/servers/{source_id}/egress")
    assert catalog.status_code == 200
    candidate = next(item for item in catalog.json()["candidates"] if item["node_id"] == node_id)
    assert candidate["available"] is True
    assert candidate["configured"] is False

    request = {
        "target_node_id": node_id,
        "promote_to_default": True,
        "routing": {"domains": ["domain:example.com"]},
        "observatory": None,
        "burstObservatory": {
            "subjectSelector": ["managed-egress"],
            "pingConfig": {"interval": "5s"},
        },
    }
    preview_response = client.post(f"/api/v1/servers/{source_id}/egress/preview", json=request)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["action"] == "create"
    assert preview["will_be_default"] is True
    assert preview["observatory_action"] == "remove"
    assert preview["burst_observatory_action"] == "set"
    assert "password" not in preview_response.text
    assert "uuid" not in preview_response.text

    undispatched = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            **request,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": False,
        },
    )
    assert undispatched.status_code == 422

    stale = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={**request, "expected_preview_revision": "0" * 64, "dispatch": True},
    )
    assert stale.status_code == 409
    assert "stale" in stale.json()["detail"].lower()

    applied = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            **request,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert set(body) == {
        "preview",
        "change_set_id",
        "change_set_status",
        "command_ids",
        "license_required",
    }
    assert body["change_set_status"] == "dispatched"
    assert len(body["command_ids"]) == 2
    for secret_field in ("client", "expected_config", "config", "password", "uuid"):
        assert secret_field not in applied.text

    change = client.app.state.inventory.get_change_set(body["change_set_id"])
    assert [str(step.server_id) for step in change.steps] == [
        target_id,
        source_id,
    ]
    target_step, source_step = change.steps
    target_candidate = target_step.forward.body["config"]
    generated_client = target_candidate["inbounds"][0]["settings"]["clients"][0]
    generated_credential = generated_client["id"]
    assert target_step.forward.path == "/api/child/egress/apply"
    assert target_step.forward.body["expected_config"]["inbounds"][0]["settings"][
        "clients"
    ] == []
    assert target_step.rollback.path == "/api/child/egress/apply"
    assert target_step.rollback.body["expected_config"] == target_candidate
    assert target_step.rollback.body["config"]["inbounds"][0]["settings"]["clients"] == []
    assert target_step.rollback.body["allow_diverged_managed_state"] == {
        "inbound_tag": "target-vless",
        "client_email": generated_client["email"],
    }
    assert source_step.forward.path == "/api/child/egress/apply"
    assert source_step.forward.body["expected_config"] == source_config
    candidate_config = source_step.forward.body["config"]
    assert candidate_config["outbounds"][0]["tag"] == preview["outbound_tag"]
    assert candidate_config["routing"]["rules"][0]["marktag"] == preview["routing_marktag"]
    assert candidate_config["routing"]["rules"][1]["outboundTag"] == "direct"
    assert "observatory" not in candidate_config
    assert candidate_config["burstObservatory"]["pingConfig"] == {"interval": "5s"}
    assert source_step.rollback.body["expected_config"] == candidate_config
    assert source_step.rollback.body["config"] == source_config
    assert source_step.rollback.body["allow_diverged_managed_state"] == {
        "outbound_tag": preview["outbound_tag"],
        "routing_marktag": preview["routing_marktag"],
    }
    public = public_change_set(change)
    assert all(step.forward.body == {"redacted": True} for step in public.steps)
    assert all(step.rollback.body == {"redacted": True} for step in public.steps)
    serialized = public.model_dump_json()
    assert "expected_config" not in serialized
    assert generated_credential not in serialized


def test_remove_revokes_target_client_after_guarded_source_disconnect(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, _, target_config = seed_fixture(client)
    preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    ).json()
    store = client.app.state.inventory
    with store._session() as session:
        prepared = ServerEgress(store)._prepare(
            session,
            source_id,
            ServerEgressPreviewRequest(target_node_id=node_id),
        )
    client_record = prepared["client"]
    source_candidate = deepcopy(prepared["candidate"])
    # Simulate a manual partial cleanup: the source outbound is gone while the
    # dedicated credential remains on the target.
    source_candidate["outbounds"] = [
        item for item in source_candidate["outbounds"] if item.get("tag") != preview["outbound_tag"]
    ]
    active_target = deepcopy(target_config)
    active_target["inbounds"][0]["settings"]["clients"].append(client_record)

    now = datetime.now(UTC)
    with store._session() as session:
        for server_id, config in (
            (source_id, source_candidate),
            (target_id, active_target),
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

    orphan = client.get(f"/api/v1/servers/{source_id}/egress").json()
    candidate = next(item for item in orphan["candidates"] if item["node_id"] == node_id)
    assert candidate["configured"] is True
    assert candidate["has_target_client"] is True
    assert candidate["needs_repair"] is True
    repair_preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    )
    assert repair_preview.status_code == 200
    assert repair_preview.json()["action"] == "repair"

    removal_preview = client.post(
        f"/api/v1/servers/{source_id}/egress/remove/preview",
        json={"target_node_id": node_id},
    )
    assert removal_preview.status_code == 200, removal_preview.text
    assert removal_preview.json()["action"] == "remove"
    removed = client.post(
        f"/api/v1/servers/{source_id}/egress/remove",
        json={
            "target_node_id": node_id,
            "expected_preview_revision": removal_preview.json()["preview_revision"],
            "dispatch": True,
        },
    )
    assert removed.status_code == 200, removed.text
    assert "client" not in removed.text

    removal = store.get_change_set(removed.json()["change_set_id"])
    source_step, target_step = removal.steps
    assert str(source_step.server_id) == source_id
    assert source_step.forward.path == "/api/child/egress/apply"
    assert all(
        item.get("tag") != removal_preview.json()["outbound_tag"]
        for item in source_step.forward.body["config"]["outbounds"]
    )
    assert str(target_step.server_id) == target_id
    assert target_step.forward.path == "/api/child/egress/apply"
    assert target_step.forward.body["config"]["inbounds"][0]["settings"]["clients"] == []
    assert target_step.rollback.path == "/api/child/egress/apply"
    assert target_step.rollback.body["config"]["inbounds"][0]["settings"]["clients"] == [
        client_record
    ]
    assert target_step.rollback.body["allow_diverged_managed_state"] == {
        "inbound_tag": "target-vless",
        "client_email": client_record["email"],
    }


def test_preview_revision_binds_the_rendered_target_server_endpoint(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, *_ = seed_fixture(client)
    preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    ).raise_for_status().json()
    store = client.app.state.inventory
    with store._session() as session:
        target = session.get(ServerModel, target_id)
        target.domain = "changed.example.com"
        session.commit()

    response = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            "target_node_id": node_id,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    )
    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()


def test_omitted_routing_keeps_existing_rule_and_null_explicitly_removes_it(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, _, _ = seed_fixture(client)
    store = client.app.state.inventory
    configured_request = ServerEgressPreviewRequest.model_validate(
        {
            "target_node_id": node_id,
            "routing": {"domains": ["domain:keep.example"]},
        }
    )
    with store._session() as session:
        configured = ServerEgress(store)._prepare(session, source_id, configured_request)
        now = datetime.now(UTC)
        for server_id, config in (
            (source_id, configured["candidate"]),
            (target_id, configured["target_candidate"]),
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

    kept = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["routing_action"] == "keep"
    assert kept.json()["routing"] is None

    removed = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={"target_node_id": node_id, "routing": None},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["routing_action"] == "remove"
    assert removed.json()["preview_revision"] != kept.json()["preview_revision"]

    with store._session() as session:
        keep_candidate = ServerEgress(store)._prepare(
            session,
            source_id,
            ServerEgressPreviewRequest.model_validate({"target_node_id": node_id}),
        )["candidate"]
        remove_candidate = ServerEgress(store)._prepare(
            session,
            source_id,
            ServerEgressPreviewRequest.model_validate(
                {"target_node_id": node_id, "routing": None}
            ),
        )["candidate"]
    marktag = kept.json()["routing_marktag"]
    kept_rules = keep_candidate["routing"]["rules"]
    removed_rules = remove_candidate["routing"]["rules"]
    assert next(rule for rule in kept_rules if rule.get("marktag") == marktag)["domain"] == [
        "domain:keep.example"
    ]
    assert all(rule.get("marktag") != marktag for rule in removed_rules)


def test_socks5_physical_node_can_become_authenticated_managed_egress(tmp_path):
    client = make_client(tmp_path)
    source_id, _, _, _, _ = seed_fixture(client)
    store = client.app.state.inventory
    target_id = str(
        store.create_server(ServerCreate(name="residential", ip_address="192.0.2.44")).id
    )
    node_id = str(uuid4())
    now = datetime.now(UTC)
    target_config = {
        "inbounds": [
            {
                "tag": "socks5-1080",
                "protocol": "socks",
                "settings": {"auth": "password", "accounts": [], "udp": True},
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=node_id,
                name="Residential SOCKS5",
                server_id=target_id,
                protocol="socks",
                node_type="physical",
                inbound_tag="socks5-1080",
                tags=["socks5"],
                enabled=True,
                client_template={},
                config={
                    "name": "Residential SOCKS5",
                    "type": "socks5",
                    "server": "{server_host}",
                    "port": 1080,
                    "udp": True,
                },
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        store._upsert_current_xray_config_snapshot(
            session,
            session.get(ServerModel, target_id),
            json.dumps(target_config),
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            now,
        )
        session.commit()

    catalog = client.get(f"/api/v1/servers/{source_id}/egress").json()
    entry = next(item for item in catalog["candidates"] if item["node_id"] == node_id)
    assert entry["available"] is True

    request = {"target_node_id": node_id, "promote_to_default": True}
    preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview", json=request
    ).raise_for_status().json()
    applied = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            **request,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    ).raise_for_status().json()
    change = store.get_change_set(applied["change_set_id"])
    target_step, source_step = change.steps
    account = target_step.forward.body["config"]["inbounds"][0]["settings"]["accounts"][0]
    remote_user = source_step.forward.body["config"]["outbounds"][0]["settings"][
        "servers"
    ][0]["users"][0]
    assert account["email"].startswith("open_node_egress__")
    assert account["user"] == remote_user["user"]
    assert account["pass"] == remote_user["pass"]
    assert source_step.forward.body["config"]["outbounds"][0]["protocol"] == "socks"
    assert account["pass"] not in json.dumps(applied)


def test_http_physical_node_can_become_authenticated_managed_egress(tmp_path):
    client = make_client(tmp_path)
    source_id, _, _, _, _ = seed_fixture(client)
    store = client.app.state.inventory
    target_id = str(store.create_server(ServerCreate(name="http-upstream")).id)
    node_id = str(uuid4())
    now = datetime.now(UTC)
    target_config = {
        "inbounds": [
            {
                "tag": "http-3128",
                "protocol": "http",
                "settings": {"accounts": []},
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=node_id,
                name="Authenticated HTTP",
                server_id=target_id,
                protocol="http",
                node_type="physical",
                inbound_tag="http-3128",
                tags=["http"],
                enabled=True,
                client_template={},
                config={
                    "name": "Authenticated HTTP",
                    "type": "http",
                    "server": "{server_host}",
                    "port": 3128,
                },
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        store._upsert_current_xray_config_snapshot(
            session,
            session.get(ServerModel, target_id),
            json.dumps(target_config),
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            now,
        )
        session.commit()

    request = {"target_node_id": node_id}
    preview = client.post(
        f"/api/v1/servers/{source_id}/egress/preview", json=request
    ).raise_for_status().json()
    applied = client.post(
        f"/api/v1/servers/{source_id}/egress/apply",
        json={
            **request,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    ).raise_for_status().json()
    target_step, source_step = store.get_change_set(applied["change_set_id"]).steps
    account = target_step.forward.body["config"]["inbounds"][0]["settings"]["accounts"][0]
    remote = source_step.forward.body["config"]["outbounds"][-1]
    remote_user = remote["settings"]["servers"][0]["users"][0]
    assert remote["protocol"] == "http"
    assert account["email"].startswith("open_node_egress__")
    assert (account["user"], account["pass"]) == (remote_user["user"], remote_user["pass"])
    assert account["pass"] not in json.dumps(applied)


def test_catalog_rejects_unsupported_and_federated_targets_and_source(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, _, _, _ = seed_fixture(client)
    store = client.app.state.inventory
    now = datetime.now(UTC)
    unsupported_id = str(uuid4())
    unsupported_server_id = str(
        store.create_server(ServerCreate(name="unsupported", ip_address="192.0.2.30")).id
    )
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=unsupported_id,
                name="Unsupported Mieru",
                server_id=unsupported_server_id,
                protocol="mieru",
                node_type="physical",
                inbound_tag="socks-in",
                tags=[],
                enabled=True,
                client_template={},
                config={"type": "mieru", "server": "203.0.113.20", "port": 1080},
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            FederatedServerModel(
                id=target_id,
                name="federated target",
                owner_url="https://owner.example",
                token_secret="secret",
                prefix="remote",
                snapshot={},
                revision=0,
                last_synced_at=now,
                created_at=now,
            )
        )
        session.commit()

    catalog = client.get(f"/api/v1/servers/{source_id}/egress")
    assert catalog.status_code == 200
    entries = {entry["node_id"]: entry for entry in catalog.json()["candidates"]}
    assert entries[unsupported_id]["available"] is False
    assert "protocol" in entries[unsupported_id]["unavailable_reason"].lower()
    available_target = next(entry for key, entry in entries.items() if key != unsupported_id)
    assert available_target["available"] is False
    assert "federated" in available_target["unavailable_reason"].lower()

    with store._session() as session:
        session.delete(session.get(FederatedServerModel, target_id))
        session.flush()
        session.add(
            FederatedServerModel(
                id=source_id,
                name="federated source",
                owner_url="https://owner.example",
                token_secret="secret",
                prefix="remote-source",
                snapshot={},
                revision=0,
                last_synced_at=now,
                created_at=now,
            )
        )
        session.commit()
    rejected = client.get(f"/api/v1/servers/{source_id}/egress")
    assert rejected.status_code == 409
    assert "federated" in rejected.json()["detail"].lower()


def test_locally_owned_servers_remain_usable_while_shared_out(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, node_id, _, _ = seed_fixture(client)
    store = client.app.state.inventory
    now = datetime.now(UTC)
    with store._session() as session:
        for index, server_id in enumerate((source_id, target_id), start=1):
            session.add(
                ServerShareModel(
                    id=str(uuid4()),
                    server_id=server_id,
                    token_hash=str(index) * 64,
                    label=f"owned share {index}",
                    allow_manage_xray=True,
                    revision=0,
                    created_at=now,
                )
            )
        session.commit()

    catalog = client.get(f"/api/v1/servers/{source_id}/egress")
    assert catalog.status_code == 200
    candidate = next(item for item in catalog.json()["candidates"] if item["node_id"] == node_id)
    assert candidate["available"] is True


def seed_same_server_reality_fixture(client: TestClient):
    store = client.app.state.inventory
    server_id = str(
        store.create_server(
            ServerCreate(name="same-server", ip_address="198.51.100.41")
        ).id
    )
    node_id = str(uuid4())
    now = datetime.now(UTC)
    config = {
        "inbounds": [
            {
                "tag": "source-vless",
                "protocol": "vless",
                "settings": {"clients": []},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"],
                    "domainsExcluded": ["Manual.Example"],
                },
            },
            {
                "tag": "target-vless",
                "protocol": "vless",
                "settings": {"clients": []},
            },
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": []},
    }
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=node_id,
                name="Same-server REALITY upstream",
                server_id=server_id,
                protocol="vless",
                node_type="physical",
                inbound_tag="target-vless",
                tags=[],
                enabled=True,
                client_template={"id": "{id}", "email": "{client_email}"},
                config={
                    "name": "Same-server REALITY upstream",
                    "type": "vless",
                    "server": "{server_host}",
                    "port": 24443,
                    "uuid": "{id}",
                    "network": "tcp",
                    "tls": True,
                    "servername": "Reality.Example",
                    "reality-opts": {
                        "public-key": "A" * 43,
                        "short-id": "aabbccdd",
                    },
                },
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        store._upsert_current_xray_config_snapshot(
            session,
            session.get(ServerModel, server_id),
            json.dumps(config),
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            now,
        )
        session.commit()
    return server_id, node_id, config


def test_same_server_different_inbound_is_one_atomic_reversible_change(tmp_path):
    client = make_client(tmp_path)
    server_id, node_id, original = seed_same_server_reality_fixture(client)
    store = client.app.state.inventory
    request = {
        "target_node_id": node_id,
        "routing": {"inbound_tags": ["source-vless"]},
    }

    catalog = client.get(f"/api/v1/servers/{server_id}/egress").raise_for_status().json()
    candidate = next(item for item in catalog["candidates"] if item["node_id"] == node_id)
    assert candidate["available"] is True

    preview = client.post(
        f"/api/v1/servers/{server_id}/egress/preview", json=request
    ).raise_for_status().json()
    applied = client.post(
        f"/api/v1/servers/{server_id}/egress/apply",
        json={
            **request,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    ).raise_for_status().json()
    assert len(applied["command_ids"]) == 1

    change = store.get_change_set(applied["change_set_id"])
    assert len(change.steps) == 1
    step = change.steps[0]
    assert str(step.server_id) == server_id
    assert step.forward.body["expected_config"] == original
    active = step.forward.body["config"]
    source_inbound, target_inbound = active["inbounds"]
    assert source_inbound["sniffing"]["domainsExcluded"] == [
        "Manual.Example",
        "reality.example",
    ]
    assert "sniffing" not in target_inbound
    assert len(target_inbound["settings"]["clients"]) == 1
    assert active["outbounds"][-1]["streamSettings"]["realitySettings"][
        "serverName"
    ] == "Reality.Example"
    assert active["routing"]["rules"][0]["inboundTag"] == ["source-vless"]
    state = active[ServerEgress.MANAGED_SNI_STATE_KEY]["inbounds"]["source-vless"]
    assert state["ownedDomains"] == ["reality.example"]
    assert state["references"][preview["outbound_tag"]] == ["reality.example"]
    assert step.rollback.body["expected_config"] == active
    assert step.rollback.body["config"] == original
    assert step.rollback.body["allow_diverged_managed_state"] == {
        "outbound_tag": preview["outbound_tag"],
        "routing_marktag": preview["routing_marktag"],
        "inbound_tag": "target-vless",
        "client_email": target_inbound["settings"]["clients"][0]["email"],
    }


def test_same_server_rejects_default_and_target_inbound_loops(tmp_path):
    client = make_client(tmp_path)
    server_id, node_id, _ = seed_same_server_reality_fixture(client)
    promoted = client.post(
        f"/api/v1/servers/{server_id}/egress/preview",
        json={
            "target_node_id": node_id,
            "promote_to_default": True,
            "routing": {"inbound_tags": ["source-vless"]},
        },
    )
    assert promoted.status_code == 409
    assert "same-server" in promoted.json()["detail"]

    looped = client.post(
        f"/api/v1/servers/{server_id}/egress/preview",
        json={
            "target_node_id": node_id,
            "routing": {"inbound_tags": ["target-vless"]},
        },
    )
    assert looped.status_code == 409
    assert "back to itself" in looped.json()["detail"]


def test_same_server_remove_is_atomic_and_cleans_only_owned_reality_exclude(tmp_path):
    client = make_client(tmp_path)
    server_id, node_id, original = seed_same_server_reality_fixture(client)
    store = client.app.state.inventory
    with store._session() as session:
        prepared = ServerEgress(store)._prepare(
            session,
            server_id,
            ServerEgressPreviewRequest.model_validate(
                {
                    "target_node_id": node_id,
                    "routing": {"inbound_tags": ["source-vless"]},
                }
            ),
        )
        active = prepared["candidate"]
        store._upsert_current_xray_config_snapshot(
            session,
            session.get(ServerModel, server_id),
            json.dumps(active),
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            datetime.now(UTC),
        )
        session.commit()

    preview = client.post(
        f"/api/v1/servers/{server_id}/egress/remove/preview",
        json={"target_node_id": node_id},
    ).raise_for_status().json()
    removed = client.post(
        f"/api/v1/servers/{server_id}/egress/remove",
        json={
            "target_node_id": node_id,
            "expected_preview_revision": preview["preview_revision"],
            "dispatch": True,
        },
    ).raise_for_status().json()
    assert len(removed["command_ids"]) == 1
    step = store.get_change_set(removed["change_set_id"]).steps[0]
    assert step.forward.body["expected_config"] == active
    assert step.forward.body["config"] == original
    assert step.rollback.body["config"] == active


def test_reality_exclude_reference_count_preserves_manual_and_shared_domains(tmp_path):
    client = make_client(tmp_path)
    service = ServerEgress(client.app.state.inventory)
    first_tag = "managed-egress:first"
    second_tag = "managed-egress:second"

    def reality(tag):
        return {
            "tag": tag,
            "protocol": "vless",
            "streamSettings": {
                "security": "reality",
                "realitySettings": {"serverName": "shared.example"},
            },
        }

    config = {
        "inbounds": [
            {
                "tag": "source",
                "protocol": "vless",
                "settings": {"clients": []},
                "sniffing": {"domainsExcluded": ["Manual.Example"]},
            }
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            reality(first_tag),
            reality(second_tag),
        ],
    }
    service._apply_managed_sniffing(
        config,
        first_tag,
        ["manual.example", "shared.example"],
        ["source"],
    )
    service._apply_managed_sniffing(
        config,
        second_tag,
        ["shared.example"],
        ["source"],
    )
    state = config[ServerEgress.MANAGED_SNI_STATE_KEY]["inbounds"]["source"]
    assert state["ownedDomains"] == ["shared.example"]
    assert set(state["references"]) == {first_tag, second_tag}

    config["outbounds"] = [
        outbound for outbound in config["outbounds"] if outbound.get("tag") != first_tag
    ]
    service._apply_managed_sniffing(config, first_tag, [], [])
    assert config["inbounds"][0]["sniffing"]["domainsExcluded"] == [
        "Manual.Example",
        "shared.example",
    ]

    config["outbounds"] = [
        outbound for outbound in config["outbounds"] if outbound.get("tag") != second_tag
    ]
    service._apply_managed_sniffing(config, second_tag, [], [])
    assert config["inbounds"][0]["sniffing"]["domainsExcluded"] == ["Manual.Example"]
    assert ServerEgress.MANAGED_SNI_STATE_KEY not in config


def test_reality_exclude_cleanup_is_scoped_to_each_source_inbound(tmp_path):
    client = make_client(tmp_path)
    service = ServerEgress(client.app.state.inventory)
    first_tag = "managed-egress:first"
    second_tag = "managed-egress:second"

    def reality(tag):
        return {
            "tag": tag,
            "protocol": "vless",
            "streamSettings": {
                "security": "reality",
                "realitySettings": {"serverName": "shared.example"},
            },
        }

    config = {
        "inbounds": [
            {"tag": "first-in", "sniffing": {"domainsExcluded": []}},
            {"tag": "second-in", "sniffing": {"domainsExcluded": []}},
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            reality(first_tag),
            reality(second_tag),
        ],
        "routing": {
            "rules": [
                {"inboundTag": ["first-in"], "outboundTag": first_tag},
                {"inboundTag": ["second-in"], "outboundTag": second_tag},
            ]
        },
    }
    service._apply_managed_sniffing(
        config, first_tag, ["shared.example"], ["first-in"]
    )
    service._apply_managed_sniffing(
        config, second_tag, ["shared.example"], ["second-in"]
    )

    config["outbounds"] = [
        outbound for outbound in config["outbounds"] if outbound.get("tag") != first_tag
    ]
    config["routing"]["rules"] = [
        rule for rule in config["routing"]["rules"] if rule.get("outboundTag") != first_tag
    ]
    service._apply_managed_sniffing(config, first_tag, [], [])

    assert config["inbounds"][0]["sniffing"]["domainsExcluded"] == []
    assert config["inbounds"][1]["sniffing"]["domainsExcluded"] == ["shared.example"]
    assert "first-in" not in config[ServerEgress.MANAGED_SNI_STATE_KEY]["inbounds"]


def test_same_server_rejects_two_managed_egress_edge_cycle(tmp_path):
    client = make_client(tmp_path)
    server_id, target_node_id, original = seed_same_server_reality_fixture(client)
    store = client.app.state.inventory
    reverse_node_id = str(uuid4())
    now = datetime.now(UTC)
    reverse_tag, _, _ = ServerEgress._identity(server_id, reverse_node_id)
    config = deepcopy(original)
    config["outbounds"].append({"tag": reverse_tag, "protocol": "freedom"})
    config["routing"]["rules"].append(
        {
            "type": "field",
            "inboundTag": ["target-vless"],
            "outboundTag": reverse_tag,
        }
    )
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=reverse_node_id,
                name="Reverse same-server node",
                server_id=server_id,
                protocol="vless",
                node_type="physical",
                inbound_tag="source-vless",
                tags=[],
                enabled=True,
                client_template={"id": "{id}", "email": "{client_email}"},
                config={"type": "vless", "server": "{server_host}", "port": 443},
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        store._upsert_current_xray_config_snapshot(
            session,
            session.get(ServerModel, server_id),
            json.dumps(config),
            XrayConfigSnapshotSource.AGENT_REPORT,
            None,
            now,
        )
        session.commit()

    response = client.post(
        f"/api/v1/servers/{server_id}/egress/preview",
        json={
            "target_node_id": target_node_id,
            "routing": {"inbound_tags": ["source-vless"]},
        },
    )
    assert response.status_code == 409
    assert "cycle" in response.json()["detail"]


def test_cross_server_rejects_two_managed_egress_edge_cycle(tmp_path):
    client = make_client(tmp_path)
    source_id, target_id, target_node_id, source_config, target_config = seed_fixture(client)
    store = client.app.state.inventory
    reverse_node_id = str(uuid4())
    now = datetime.now(UTC)
    source_config = deepcopy(source_config)
    target_config = deepcopy(target_config)
    source_config["inbounds"] = [
        {"tag": "source-vless", "protocol": "vless", "settings": {"clients": []}}
    ]
    reverse_tag, _, _ = ServerEgress._identity(target_id, reverse_node_id)
    target_config["outbounds"].append({"tag": reverse_tag, "protocol": "freedom"})
    target_config["routing"]["rules"].append(
        {
            "type": "field",
            "inboundTag": ["target-vless"],
            "outboundTag": reverse_tag,
        }
    )
    with store._session() as session:
        session.add(
            ManagedNodeModel(
                id=reverse_node_id,
                name="Reverse cross-server node",
                server_id=source_id,
                protocol="vless",
                node_type="physical",
                inbound_tag="source-vless",
                tags=[],
                enabled=True,
                client_template={"id": "{id}", "email": "{client_email}"},
                config={"type": "vless", "server": "{server_host}", "port": 443},
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        for server_id, config in (
            (source_id, source_config),
            (target_id, target_config),
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

    response = client.post(
        f"/api/v1/servers/{source_id}/egress/preview",
        json={
            "target_node_id": target_node_id,
            "routing": {"inbound_tags": ["source-vless"]},
        },
    )
    assert response.status_code == 409
    assert "cycle" in response.json()["detail"]


def test_managed_egress_rejects_balancer_selector_and_fallback(tmp_path):
    client = make_client(tmp_path)
    source_id, _, node_id, source_config, _ = seed_fixture(client)
    store = client.app.state.inventory
    for balancer in (
        {"tag": "pool", "selector": ["managed-egress:"]},
        {
            "tag": "pool",
            "selector": ["direct"],
            "fallbackTag": ServerEgress._identity(source_id, node_id)[0],
        },
    ):
        config = deepcopy(source_config)
        config["routing"]["balancers"] = [balancer]
        with store._session() as session:
            store._upsert_current_xray_config_snapshot(
                session,
                session.get(ServerModel, source_id),
                json.dumps(config),
                XrayConfigSnapshotSource.AGENT_REPORT,
                None,
                datetime.now(UTC),
            )
            session.commit()
        response = client.post(
            f"/api/v1/servers/{source_id}/egress/preview",
            json={"target_node_id": node_id},
        )
        assert response.status_code == 409
        assert "balancer" in response.json()["detail"]
