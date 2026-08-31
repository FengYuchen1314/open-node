import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.external_subscriptions import ExternalPreviewConfirm
from open_node.main import create_app
from open_node.services.external_fetch import ExternalFetchResult
from open_node.services.external_subscriptions import (
    ExternalNodeModel,
    ExternalPreviewModel,
    ExternalSourceModel,
    ExternalSubscriptionConflict,
    ExternalSubscriptionNotFound,
)
from open_node.services.inventory import (
    ProductUserModel,
    ProductUserRemovalModel,
    SubscriptionPlanModel,
)
from sqlalchemy import delete, func, select
from test_subscriptions import create_catalog_fixture, make_client, sqlite_url

PREFIX = "/api/v1/external-subscriptions"
SOURCE_URL = "https://provider.example/private-subscription?token=source-secret-do-not-echo"
UPSTREAM_UUID = "4ac23fce-f91b-4e5f-a722-d2a85b8c3324"
ROTATED_UUID = "5fb3a937-aa3b-4bc8-ae30-8ce19356e741"


def proxy(name="Upstream A", credential=UPSTREAM_UUID, **extra):
    return {
        "name": name,
        "type": "vless",
        "server": "edge.provider.example",
        "port": 443,
        "uuid": credential,
        "tls": True,
        **extra,
    }


def feed(client, proxies, *, metadata=None):
    calls = []
    body = yaml.safe_dump({"proxies": proxies}, allow_unicode=True).encode()

    def fetch(url, *, user_agent):
        calls.append((url, user_agent))
        return ExternalFetchResult(body=body, metadata=metadata or {})

    client.app.state.external_subscriptions.fetcher = fetch
    return calls


def create_source(client, *, owner="alice", url=SOURCE_URL, name="External provider"):
    response = client.post(PREFIX, json={"owner_username": owner, "name": name, "url": url})
    assert response.status_code == 201, response.text
    return response.json()


def preview(client, source):
    response = client.post(
        f"{PREFIX}/{source['id']}/previews", json={"expected_revision": source["revision"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirmation_payload(value, selected=None):
    return {
        "expected_revision": value["source_revision"],
        "accept_changes": True,
        "selected_node_ids": (
            [node["node_id"] for node in value["nodes"] if node["selectable"]]
            if selected is None
            else selected
        ),
    }


def confirm(client, value, selected=None):
    response = client.post(
        f"{PREFIX}/{value['source_id']}/previews/{value['id']}/confirm",
        json=confirmation_payload(value, selected),
    )
    assert response.status_code == 200, response.text
    return response.json()


def detail(client, source):
    response = client.get(f"{PREFIX}/{source['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def token_path(client, username="alice"):
    response = client.post(f"/api/v1/users/{username}/subscription-token")
    assert response.status_code == 201, response.text
    return "/api/v1/subscribe/" + response.json()["subscription"]["token"]


@pytest.fixture
def catalog(tmp_path):
    client = make_client(tmp_path)
    _agent, server_id, node_id, plan_id = create_catalog_fixture(client)
    client.app.state.external_test_agent_token = _agent
    client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    return client, server_id, node_id, plan_id


def test_source_create_is_offline_and_secrets_are_encrypted_and_not_returned(catalog, tmp_path):
    client, *_ = catalog
    calls = feed(client, [proxy()])
    source = create_source(client)
    assert calls == []
    assert source["revision"] == 1 and source["node_count"] == 0
    assert source["last_synced_at"] is None and source["metadata"] == {}
    assert "url" not in source and "user_agent" not in source
    fetched = preview(client, source)
    assert calls == [(SOURCE_URL, "clash-meta/2.4.0")]
    assert len(fetched["nodes"]) == 1 and fetched["nodes"][0]["selectable"]
    for response in (client.get(PREFIX), client.get(f"{PREFIX}/{source['id']}")):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert SOURCE_URL not in response.text and UPSTREAM_UUID not in response.text
    assert SOURCE_URL not in json.dumps(fetched) and UPSTREAM_UUID not in json.dumps(fetched)
    database = (tmp_path / "open-node-test.db").read_bytes()
    assert SOURCE_URL.encode() not in database and UPSTREAM_UUID.encode() not in database
    root = tmp_path / "external-subscriptions"
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "vault.key").stat().st_mode & 0o777 == 0o600
    assert (root / "vault.key").read_bytes() not in database


def test_confirmation_merges_original_upstream_credentials_without_touching_managed_state(catalog):
    client, *_ = catalog
    path = token_path(client)
    before = client.get(path)
    ledger = client.get("/api/v1/users/alice/traffic").json()
    credentials = client.get("/api/v1/users/alice/credentials").json()
    nodes = client.get("/api/v1/nodes").json()
    plans = client.get("/api/v1/plans").json()
    calls = feed(client, [proxy()], metadata={"upload": 7, "download": 11, "total": 9999999})
    source = create_source(client)
    value = preview(client, source)
    assert client.get(path).text == before.text
    assert detail(client, source)["source"]["metadata"] == {}
    receipt = confirm(client, value)
    assert receipt["imported_count"] == 1 and receipt["revision"] == 2
    rendered = client.get(path)
    output = yaml.safe_load(rendered.text)["proxies"]
    assert output[0] == yaml.safe_load(before.text)["proxies"][0]
    assert output[1]["uuid"] == UPSTREAM_UUID
    assert rendered.headers["subscription-userinfo"] == before.headers["subscription-userinfo"]
    assert client.get("/api/v1/users/alice/traffic").json() == ledger
    assert client.get("/api/v1/users/alice/credentials").json() == credentials
    assert client.get("/api/v1/nodes").json() == nodes
    assert client.get("/api/v1/plans").json() == plans
    assert len(calls) == 1  # Neither list/detail nor downloads initiate a fetch.
    assert detail(client, source)["source"]["metadata"] == {
        "upload": 7,
        "download": 11,
        "total": 9999999,
    }
    store = client.app.state.inventory
    with store._session() as session:
        saved = session.get(ExternalPreviewModel, value["id"])
        assert saved.secret is None and saved.receipt["imported_count"] == 1


def test_refresh_preview_is_read_only_and_confirm_updates_missing_and_selected_new_nodes(catalog):
    client, *_ = catalog
    path = token_path(client)
    feed(client, [proxy(), proxy("Old B")])
    source = create_source(client)
    confirm(client, preview(client, source))
    source = detail(client, source)["source"]
    old = client.get(path).text
    feed(client, [proxy(credential=ROTATED_UUID), proxy("New C"), proxy("Do not select")])
    value = preview(client, source)
    changes = {node["upstream_name"]: node for node in value["nodes"]}
    assert changes["Upstream A"]["change"] == "updated"
    assert changes["Upstream A"]["changed_fields"] == ["uuid"]
    assert changes["Old B"]["change"] == "missing"
    assert client.get(path).text == old
    receipt = confirm(client, value, [changes["New C"]["node_id"]])
    assert (receipt["updated_count"], receipt["missing_count"], receipt["imported_count"]) == (
        1,
        1,
        1,
    )
    output = yaml.safe_load(client.get(path).text)["proxies"]
    assert next(item for item in output if item["name"] == "Upstream A")["uuid"] == ROTATED_UUID
    assert {item["name"] for item in output} >= {"Upstream A", "New C"}
    assert "Old B" not in {item["name"] for item in output}
    assert "Do not select" not in {item["name"] for item in output}
    absent = next(
        node for node in detail(client, source)["nodes"] if node["upstream_name"] == "Old B"
    )
    assert not absent["present"] and not absent["available"]


def test_rename_disable_and_restoration_preserve_operator_choices(catalog):
    client, *_ = catalog
    path = token_path(client)
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    state = detail(client, source)
    node = state["nodes"][0]
    edit = client.put(
        f"{PREFIX}/{source['id']}/nodes/{node['id']}",
        json={
            "expected_revision": state["source"]["revision"],
            "name": "My name",
            "enabled": False,
        },
    )
    assert edit.status_code == 200, edit.text
    feed(client, [])
    confirm(client, preview(client, edit.json()["source"]))
    feed(client, [proxy(credential=ROTATED_UUID)])
    confirm(client, preview(client, detail(client, source)["source"]))
    node = detail(client, source)["nodes"][0]
    assert node["name"] == "My name" and node["present"] and not node["enabled"]
    assert ROTATED_UUID not in client.get(path).text
    state = client.put(
        f"{PREFIX}/{source['id']}/nodes/{node['id']}",
        json={
            "expected_revision": detail(client, source)["source"]["revision"],
            "name": "My name",
            "enabled": True,
        },
    ).json()
    assert state["nodes"][0]["available"]
    assert ROTATED_UUID in client.get(path).text and "My name" in client.get(path).text


def test_receipt_is_repeatable_and_rejects_a_different_selection(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    first = confirm(client, value)
    assert confirm(client, value) == first
    assert client.get(f"{PREFIX}/{source['id']}/previews/{value['id']}").json()["receipt"] == first
    rejected = client.post(
        f"{PREFIX}/{source['id']}/previews/{value['id']}/confirm",
        json=confirmation_payload(value, []),
    )
    assert rejected.status_code == 409
    assert detail(client, source)["source"]["node_count"] == 1
    assert detail(client, source)["source"]["revision"] == 2


def test_concurrent_confirmation_applies_once_and_returns_one_receipt(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    payload = ExternalPreviewConfirm.model_validate(confirmation_payload(value))
    service = client.app.state.external_subscriptions
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(lambda _: service.confirm(source["id"], value["id"], payload), range(2))
        )
    assert receipts[0] == receipts[1]
    assert detail(client, source)["source"]["revision"] == 2
    assert len(detail(client, source)["nodes"]) == 1


@pytest.mark.parametrize("mutation", ["edit", "node", "delete"])
def test_stale_preview_cannot_overwrite_changed_or_deleted_source(catalog, mutation):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    state = detail(client, source)
    source = state["source"]
    feed(client, [proxy(credential=ROTATED_UUID)])
    value = preview(client, source)
    if mutation == "edit":
        client.put(
            f"{PREFIX}/{source['id']}",
            json={
                "expected_revision": source["revision"],
                "name": "Changed",
                "enabled": False,
            },
        ).raise_for_status()
    elif mutation == "node":
        client.put(
            f"{PREFIX}/{source['id']}/nodes/{state['nodes'][0]['id']}",
            json={
                "expected_revision": source["revision"],
                "name": "Changed node",
                "enabled": False,
            },
        ).raise_for_status()
    else:
        client.post(
            f"{PREFIX}/{source['id']}/delete",
            json={
                "expected_revision": source["revision"],
                "confirm": True,
            },
        ).raise_for_status()
    response = client.post(
        f"{PREFIX}/{source['id']}/previews/{value['id']}/confirm", json=confirmation_payload(value)
    )
    assert response.status_code == (404 if mutation == "delete" else 409)


@pytest.mark.parametrize("replacement", [False, True])
def test_late_fetch_cannot_resurrect_deleted_owner_or_source(catalog, replacement):
    client, *_ = catalog
    source = create_source(client)
    entered, release = Event(), Event()
    service = client.app.state.external_subscriptions

    def fetch(_url, *, user_agent):
        entered.set()
        assert release.wait(10)
        return ExternalFetchResult(
            body=yaml.safe_dump({"proxies": [proxy()]}).encode(), metadata={}
        )

    service.fetcher = fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.prepare_preview, source["id"], 1)
        assert entered.wait(10)
        try:
            with client.app.state.inventory._session() as session:
                session.execute(
                    delete(ProductUserModel).where(ProductUserModel.username == "alice")
                )
                session.commit()
            if replacement:
                client.post("/api/v1/users", json={"username": "alice"}).raise_for_status()
        finally:
            release.set()
        with pytest.raises(ExternalSubscriptionNotFound):
            pending.result(10)
    assert client.get(PREFIX).json()["sources"] == []
    with client.app.state.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(ExternalPreviewModel)) == 0


def test_source_url_edits_are_offline_and_old_inflight_fetch_is_rejected(catalog):
    client, *_ = catalog
    source = create_source(client)
    entered, release = Event(), Event()
    service = client.app.state.external_subscriptions

    def fetch(_url, *, user_agent):
        entered.set()
        assert release.wait(10)
        return ExternalFetchResult(
            body=yaml.safe_dump({"proxies": [proxy()]}).encode(), metadata={}
        )

    service.fetcher = fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.prepare_preview, source["id"], 1)
        assert entered.wait(10)
        try:
            edited = client.put(
                f"{PREFIX}/{source['id']}",
                json={
                    "expected_revision": 1,
                    "name": source["name"],
                    "enabled": True,
                    "url": "https://new.provider.example/another?token=changed-secret",
                    "user_agent": "Custom/1",
                },
            )
            assert edited.status_code == 200, edited.text
        finally:
            release.set()
        with pytest.raises(ExternalSubscriptionConflict):
            pending.result(10)
    calls = feed(client, [proxy()])
    preview(client, edited.json())
    assert calls == [("https://new.provider.example/another?token=changed-secret", "Custom/1")]


def test_owner_and_source_boundaries_hold_for_identical_names_and_urls(catalog):
    client, _server, _node, plan_id = catalog
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    client.post("/api/v1/users/bob/plan", json={"plan_id": plan_id}).raise_for_status()
    alice_path, bob_path = token_path(client), token_path(client, "bob")
    alice = create_source(client)
    assert (
        client.post(
            PREFIX, json={"owner_username": "alice", "name": "Duplicate", "url": SOURCE_URL}
        ).status_code
        == 409
    )
    bob = create_source(client, owner="bob")
    feed(client, [proxy()])
    a_preview = preview(client, alice)
    feed(client, [proxy(credential=ROTATED_UUID)])
    b_preview = preview(client, bob)
    assert (
        client.post(
            f"{PREFIX}/{bob['id']}/previews/{a_preview['id']}/confirm",
            json=confirmation_payload(a_preview),
        ).status_code
        == 404
    )
    confirm(client, a_preview)
    confirm(client, b_preview)
    assert (
        UPSTREAM_UUID in client.get(alice_path).text
        and ROTATED_UUID not in client.get(alice_path).text
    )
    assert (
        ROTATED_UUID in client.get(bob_path).text and UPSTREAM_UUID not in client.get(bob_path).text
    )
    alice_node = detail(client, alice)["nodes"][0]
    assert (
        client.put(
            f"{PREFIX}/{bob['id']}/nodes/{alice_node['id']}",
            json={
                "expected_revision": 2,
                "name": "Not mine",
                "enabled": False,
            },
        ).status_code
        == 404
    )


def test_primary_opt_in_does_not_expand_temporary_or_shared_named_rendering(catalog):
    client, _server, managed_node, _plan = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    assert UPSTREAM_UUID in client.get(path).text
    temporary = client.post(
        "/api/v1/temporary-subscriptions",
        json={
            "username": "alice",
            "label": "Managed only",
            "node_ids": [managed_node],
            "max_access": 2,
            "expires_in_seconds": 300,
        },
    )
    assert temporary.status_code == 201, temporary.text
    assert UPSTREAM_UUID not in client.get(temporary.json()["subscription_url"]).text
    inventory = client.app.state.inventory
    from open_node.domain.subscriptions import SubscriptionClientFormat

    with inventory._session() as session:
        owner = session.get(ProductUserModel, "alice")
        plan = session.get(SubscriptionPlanModel, owner.current_plan_id)
        # Named profiles with no node subset also pass None to this shared path.
        rendered = inventory._render_user_subscription(
            session, owner, plan, SubscriptionClientFormat.CLASH, selected_node_ids=None
        )
        assert UPSTREAM_UUID not in rendered.content


@pytest.mark.parametrize("owner_state", ["inactive", "expired", "no_plan", "quota", "removing"])
def test_external_nodes_do_not_bypass_local_subscription_eligibility(catalog, owner_state):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    assert client.get(path).status_code == 200
    if owner_state == "quota":
        email = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
        client.post(
            "/api/v1/agents/telemetry",
            json={
                "token": client.app.state.external_test_agent_token,
                "stats": {"user": {email: {"uplink": 7, "downlink": 11}}},
            },
        ).raise_for_status()
    with client.app.state.inventory._session() as session:
        owner = session.get(ProductUserModel, "alice")
        if owner_state == "inactive":
            owner.is_active = False
        elif owner_state == "expired":
            owner.plan_expires_at = datetime.now(UTC) - timedelta(days=1)
        elif owner_state == "no_plan":
            owner.current_plan_id = None
        elif owner_state == "quota":
            session.get(SubscriptionPlanModel, owner.current_plan_id).traffic_limit_bytes = 1
        else:
            removal_id = str(uuid4())
            session.add(
                ProductUserRemovalModel(
                    id=removal_id,
                    username="alice",
                    requested_at=datetime.now(UTC),
                )
            )
            session.flush()
            owner.removal_id = removal_id
        session.commit()
    if owner_state == "quota":
        assert client.app.state.inventory.subscription_user_quota("alice").over_quota
    assert client.get(path).status_code == 404


def test_missing_key_fails_closed_without_replacement_and_backup_restores_access(catalog, tmp_path):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    before = client.get(path).text
    key_path = tmp_path / "external-subscriptions" / "vault.key"
    backup = key_path.read_bytes()
    key_path.unlink()
    assert client.get(path).status_code == 404
    assert (
        client.post(f"{PREFIX}/{source['id']}/previews", json={"expected_revision": 2}).status_code
        == 503
    )
    assert not key_path.exists()
    key_path.write_bytes(backup)
    key_path.chmod(0o600)
    restarted = TestClient(
        create_app(Settings(database_url=sqlite_url(tmp_path / "open-node-test.db")))
    )
    assert restarted.get(path).text == before


def test_expiry_cancel_and_pending_limit_do_not_change_active_nodes(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    values = [preview(client, source) for _ in range(3)]
    assert (
        client.post(f"{PREFIX}/{source['id']}/previews", json={"expected_revision": 1}).status_code
        == 409
    )
    assert client.delete(f"{PREFIX}/{source['id']}/previews/{values[0]['id']}").status_code == 200
    assert client.delete(f"{PREFIX}/{source['id']}/previews/{values[0]['id']}").status_code == 200
    with client.app.state.inventory._session() as session:
        session.get(ExternalPreviewModel, values[1]["id"]).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
        session.commit()
    assert (
        client.post(
            f"{PREFIX}/{source['id']}/previews/{values[1]['id']}/confirm",
            json=confirmation_payload(values[1]),
        ).status_code
        == 409
    )
    new = preview(client, source)
    assert new["id"] not in {value["id"] for value in values}
    assert detail(client, source)["nodes"] == []


@pytest.mark.parametrize(
    "bad_body", [b"", b"<html>Oops</html>", b"proxies: invalid", b"proxies: ["]
)
def test_invalid_refresh_preserves_the_confirmed_snapshot(catalog, bad_body):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    before = client.get(path).text
    source = detail(client, source)["source"]
    client.app.state.external_subscriptions.fetcher = lambda *_args, **_kwargs: ExternalFetchResult(
        body=bad_body, metadata={}
    )
    result = client.post(
        f"{PREFIX}/{source['id']}/previews", json={"expected_revision": source["revision"]}
    )
    assert result.status_code == 422, result.text
    assert client.get(path).text == before and detail(client, source)["source"] == source


def test_private_api_auth_origin_csrf_request_bounds_and_secret_error_redaction(catalog):
    client, *_ = catalog
    body = {"owner_username": "alice", "name": "External", "url": SOURCE_URL}
    anonymous = TestClient(client.app, base_url="https://testserver")
    assert anonymous.get(PREFIX).status_code == 401
    assert anonymous.post(PREFIX, json=body).status_code == 401
    assert client.post(PREFIX, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.post(PREFIX, json=body, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    invalid = client.post(
        PREFIX, json={**body, "url": "http://provider.example/?token=secret-error-url"}
    )
    assert invalid.status_code == 422 and "secret-error-url" not in invalid.text
    unknown = client.post(PREFIX, json={**body, SOURCE_URL: "private"})
    assert unknown.status_code == 422 and SOURCE_URL not in unknown.text
    assert (
        client.post(
            PREFIX, content=b"x" * 65537, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    assert (
        client.post(
            PREFIX, content=json.dumps(body), headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.get(PREFIX).json()["sources"] == []


@pytest.mark.parametrize("bad", [False, 0, 1, "true", None])
def test_confirmation_requires_explicit_boolean_acknowledgment(catalog, bad):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    response = client.post(
        f"{PREFIX}/{source['id']}/previews/{value['id']}/confirm",
        json={
            **confirmation_payload(value),
            "accept_changes": bad,
        },
    )
    assert response.status_code == 422
    assert detail(client, source)["nodes"] == []


def test_unoffered_selection_and_owner_edit_are_rejected_without_partial_writes(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    assert (
        client.post(
            f"{PREFIX}/{source['id']}/previews/{value['id']}/confirm",
            json={
                **confirmation_payload(value),
                "selected_node_ids": [str(uuid4())],
            },
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"{PREFIX}/{source['id']}",
            json={
                "expected_revision": 1,
                "name": "Moved",
                "enabled": True,
                "owner_username": "bob",
            },
        ).status_code
        == 422
    )
    assert detail(client, source)["nodes"] == []
    assert detail(client, source)["source"]["revision"] == 1


def test_source_delete_cascades_nodes_previews_and_receipts(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    confirm(client, value)
    preview(client, detail(client, source)["source"])
    deleted = client.post(
        f"{PREFIX}/{source['id']}/delete", json={"expected_revision": 2, "confirm": True}
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"]
    with client.app.state.inventory._session() as session:
        for model in (ExternalSourceModel, ExternalNodeModel, ExternalPreviewModel):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_failed_confirmation_rolls_back_credentials_metadata_and_revision(catalog, monkeypatch):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    before = client.get(path).text
    before_state = detail(client, source)
    feed(client, [proxy(credential=ROTATED_UUID), proxy("New")], metadata={"total": 1234})
    value = preview(client, before_state["source"])
    service = client.app.state.external_subscriptions
    original = service._bump

    def fail_after_write(*args):
        original(*args)
        raise ExternalSubscriptionConflict("Injected transaction failure")

    monkeypatch.setattr(service, "_bump", fail_after_write)
    result = client.post(
        f"{PREFIX}/{source['id']}/previews/{value['id']}/confirm", json=confirmation_payload(value)
    )
    assert result.status_code == 409
    assert client.get(path).text == before
    assert detail(client, source) == before_state
    pending = client.get(f"{PREFIX}/{source['id']}/previews/{value['id']}").json()
    assert pending["receipt"] is None
    monkeypatch.setattr(service, "_bump", original)
    assert confirm(client, value)["updated_count"] == 1


def test_changing_source_url_preserves_confirmed_nodes_until_explicit_confirmation(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    previous = client.get(path).text
    calls = feed(client, [proxy(credential=ROTATED_UUID)])
    updated = client.put(
        f"{PREFIX}/{source['id']}",
        json={
            "expected_revision": 2,
            "name": source["name"],
            "enabled": True,
            "url": "https://other.provider.example/sub?key=other-private-key",
            "user_agent": "Custom/2",
        },
    )
    assert updated.status_code == 200
    assert calls == [] and client.get(path).text == previous
    value = preview(client, updated.json())
    assert calls == [("https://other.provider.example/sub?key=other-private-key", "Custom/2")]
    assert client.get(path).text == previous
    confirm(client, value)
    assert ROTATED_UUID in client.get(path).text and UPSTREAM_UUID not in client.get(path).text
    restored = client.put(
        f"{PREFIX}/{source['id']}",
        json={"expected_revision": 4, "name": source["name"], "enabled": True, "user_agent": ""},
    )
    assert restored.status_code == 200 and not restored.json()["has_custom_user_agent"]
    preview(client, restored.json())
    assert calls[-1] == (
        "https://other.provider.example/sub?key=other-private-key",
        "clash-meta/2.4.0",
    )


def test_identical_names_and_addresses_stay_scoped_to_each_source(catalog):
    client, *_ = catalog
    path = token_path(client)
    managed = yaml.safe_load(client.get(path).text)["proxies"][0]
    first = create_source(client)
    second = create_source(client, url=SOURCE_URL + "-second", name="Second provider")
    feed(client, [proxy(managed["name"])])
    confirm(client, preview(client, first))
    feed(client, [proxy(managed["name"], credential=ROTATED_UUID)])
    confirm(client, preview(client, second))
    proxies = yaml.safe_load(client.get(path).text)["proxies"]
    assert proxies[0] == managed
    assert len(proxies) == 3 and len({item["name"] for item in proxies}) == 3
    assert {item["uuid"] for item in proxies[1:]} == {UPSTREAM_UUID, ROTATED_UUID}
    feed(client, [])
    confirm(client, preview(client, detail(client, first)["source"]))
    result = client.get(path).text
    assert UPSTREAM_UUID not in result and ROTATED_UUID in result
    assert detail(client, second)["source"]["revision"] == 2
    assert detail(client, second)["nodes"][0]["available"]


def test_unsupported_refresh_requires_ack_and_can_later_restore_a_saved_node(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    previous = client.get(path).text
    feed(
        client,
        [{"name": "Upstream A", "type": "hysteria", "server": "provider.example", "port": 443}],
    )
    value = preview(client, detail(client, source)["source"])
    assert value["nodes"][0]["existing"] and value["nodes"][0]["change"] == "unavailable"
    assert not value["nodes"][0]["selectable"]
    assert client.get(path).text == previous
    confirm(client, value, [])
    assert UPSTREAM_UUID not in client.get(path).text
    assert not detail(client, source)["nodes"][0]["available"]
    feed(client, [proxy(credential=ROTATED_UUID)])
    restored = preview(client, detail(client, source)["source"])
    assert restored["nodes"][0]["change"] == "updated"
    assert confirm(client, restored)["updated_count"] == 1
    assert ROTATED_UUID in client.get(path).text


@pytest.mark.parametrize("purpose", ["source", "node", "preview"])
def test_encrypted_material_cannot_be_transplanted_between_sources(catalog, purpose):
    client, *_ = catalog
    source = create_source(client)
    other = create_source(client, url=SOURCE_URL + "-other", name="Other source")
    feed(client, [proxy()])
    first_preview = preview(client, source)
    other_preview = preview(client, other)
    if purpose == "node":
        confirm(client, first_preview)
        confirm(client, other_preview)
    inventory = client.app.state.inventory
    with inventory._session() as session:
        if purpose == "source":
            first = session.get(ExternalSourceModel, source["id"])
            second = session.get(ExternalSourceModel, other["id"])
        elif purpose == "node":
            first = session.scalar(
                select(ExternalNodeModel).where(ExternalNodeModel.source_id == source["id"])
            )
            second = session.scalar(
                select(ExternalNodeModel).where(ExternalNodeModel.source_id == other["id"])
            )
        else:
            first = session.get(ExternalPreviewModel, first_preview["id"])
            second = session.get(ExternalPreviewModel, other_preview["id"])
        second.secret = first.secret
        session.commit()
    if purpose == "node":
        response = client.get(token_path(client))
        assert response.status_code == 404
    elif purpose == "source":
        response = client.post(f"{PREFIX}/{other['id']}/previews", json={"expected_revision": 1})
        assert response.status_code == 503
    else:
        response = client.post(
            f"{PREFIX}/{other['id']}/previews/{other_preview['id']}/confirm",
            json=confirmation_payload(other_preview),
        )
        assert response.status_code == 503
        assert detail(client, other)["source"]["revision"] == 1
    assert SOURCE_URL not in response.text and UPSTREAM_UUID not in response.text


@pytest.mark.parametrize(
    "body",
    [
        '{"owner_username":"alice","name":"First","name":"Second","url":"secret"}',
        '{"owner_username":"alice","name":"N","url":"secret","enabled":NaN}',
        '{"owner_username":"alice","name":"N","url":"secret","extra":Infinity}',
    ],
)
def test_ambiguous_json_is_rejected_without_echoing_the_secret(catalog, body):
    client, *_ = catalog
    response = client.post(PREFIX, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and "secret" not in response.text
    assert client.get(PREFIX).json()["sources"] == []


def test_receipt_is_unavailable_after_seven_days_without_reapplying_changes(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    confirm(client, value)
    with client.app.state.inventory._session() as session:
        row = session.get(ExternalPreviewModel, value["id"])
        row.applied_at = datetime.now(UTC) - timedelta(days=8)
        session.commit()
    path = f"{PREFIX}/{source['id']}/previews/{value['id']}"
    assert client.get(path).status_code == 404
    assert client.post(path + "/confirm", json=confirmation_payload(value)).status_code == 404
    state = detail(client, source)
    assert state["source"]["revision"] == 2 and len(state["nodes"]) == 1
    preview(client, state["source"])
    with client.app.state.inventory._session() as session:
        assert session.get(ExternalPreviewModel, value["id"]) is None


@pytest.mark.parametrize("failure", ["wrong-key", "missing-directory"])
def test_wrong_key_or_missing_vault_never_replaces_existing_encryption(catalog, tmp_path, failure):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    confirm(client, preview(client, source))
    path = token_path(client)
    previous = client.get(path).text
    root = tmp_path / "external-subscriptions"
    key_path = root / "vault.key"
    original_key = key_path.read_bytes()
    if failure == "wrong-key":
        replacement = Fernet.generate_key()
        key_path.write_bytes(replacement)
    else:
        saved = tmp_path / "saved-external-vault"
        root.rename(saved)
    assert client.get(path).status_code == 404
    response = client.post(f"{PREFIX}/{source['id']}/previews", json={"expected_revision": 2})
    assert response.status_code == 503 and UPSTREAM_UUID not in response.text
    if failure == "wrong-key":
        assert key_path.read_bytes() == replacement
        key_path.write_bytes(original_key)
    else:
        assert not key_path.exists() and not (root / "vault.initialized").exists()
        for name in ("vault.key", "vault.initialized"):
            shutil.copy2(saved / name, root / name)
    assert key_path.read_bytes() == original_key
    assert client.get(path).text == previous


def test_pending_owner_removal_blocks_late_fetch_and_confirmation(catalog):
    client, *_ = catalog
    feed(client, [proxy()])
    source = create_source(client)
    value = preview(client, source)
    service = client.app.state.external_subscriptions
    entered, release = Event(), Event()

    def fetch(_url, *, user_agent):
        entered.set()
        assert release.wait(10)
        return ExternalFetchResult(yaml.safe_dump({"proxies": [proxy()]}).encode(), {})

    service.fetcher = fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.prepare_preview, source["id"], 1)
        assert entered.wait(10)
        try:
            with client.app.state.inventory._session() as session:
                removal_id = str(uuid4())
                session.add(
                    ProductUserRemovalModel(
                        id=removal_id,
                        username="alice",
                        requested_at=datetime.now(UTC),
                    )
                )
                session.flush()
                session.get(ProductUserModel, "alice").removal_id = removal_id
                session.commit()
        finally:
            release.set()
        with pytest.raises(ExternalSubscriptionConflict):
            pending.result(10)
    response = client.post(
        f"{PREFIX}/{source['id']}/previews/{value['id']}/confirm",
        json=confirmation_payload(value),
    )
    assert response.status_code == 409
    assert detail(client, source)["source"]["revision"] == 1
    assert detail(client, source)["nodes"] == []
    with client.app.state.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(ExternalPreviewModel)) == 1


def test_external_name_collision_does_not_retarget_a_managed_dialer(catalog, monkeypatch):
    from copy import deepcopy

    from open_node.domain.subscriptions import SubscriptionClientFormat

    client, *_ = catalog
    feed(client, [proxy("Proxy")])
    source = create_source(client)
    confirm(client, preview(client, source))
    inventory = client.app.state.inventory
    managed = [
        (str(uuid4()), proxy("Proxy", credential=ROTATED_UUID)),
        (str(uuid4()), proxy("Chain", credential=ROTATED_UUID, **{"dialer-proxy": "Proxy"})),
    ]
    monkeypatch.setattr(
        inventory, "_subscription_proxy_configs", lambda *_: (deepcopy(managed), [])
    )
    with inventory._session() as session:
        owner = session.get(ProductUserModel, "alice")
        plan = session.get(SubscriptionPlanModel, owner.current_plan_id)
        proxies, report = inventory._prepare_subscription_format(
            session,
            owner,
            plan,
            SubscriptionClientFormat.CLASH,
            include_external=True,
        )
    assert [node.available for node in report.nodes] == [True, True, True]
    assert [node["name"] for node in proxies] == ["Proxy (2)", "Chain", "Proxy (3)"]
    assert proxies[1]["dialer-proxy"] == "Proxy (2)"
    assert proxies[0]["uuid"] == ROTATED_UUID and proxies[2]["uuid"] == UPSTREAM_UUID
