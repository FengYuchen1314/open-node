"""Real subscriber sessions, tenant fences, explicit import and primary-link merge."""

import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from open_node.services.external_fetch import ExternalFetchResult
from open_node.services.external_subscriptions import (
    ExternalPreviewModel,
    ExternalSourceModel,
    ExternalSubscriptionNotFound,
)
from sqlalchemy import func, select
from test_external_subscriptions import SOURCE_URL, UPSTREAM_UUID, token_path
from test_subscriber_auth import login, provision
from test_subscriptions import create_catalog_fixture, make_client

ADMIN = "/api/v1/external-subscriptions"
ACCOUNT = "/api/v1/account/external-subscriptions"
BODY = f"vless://{UPSTREAM_UUID}@edge.provider.example:443?security=tls#Owned-node".encode()


@pytest.fixture
def workspace(tmp_path):
    operator = make_client(tmp_path)
    _, _, _, plan_id = create_catalog_fixture(operator)
    assert operator.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).status_code == 200
    assert operator.post("/api/v1/users", json={"username": "bob"}).status_code == 201
    clients = {}
    for username in ("alice", "bob"):
        provision(operator, username)
        clients[username] = TestClient(operator.app, base_url="https://testserver")
        assert login(clients[username], username=username).status_code == 200
    calls = []

    def fetch(url, *, user_agent):
        calls.append((url, user_agent))
        return ExternalFetchResult(body=base64.b64encode(BODY), metadata={"download": 123})

    operator.app.state.external_subscriptions.fetcher = fetch
    yield operator, clients["alice"], clients["bob"], calls
    for client in clients.values():
        client.close()
    operator.close()


def create(client):
    response = client.post(ACCOUNT, json={"name": "My source", "url": SOURCE_URL})
    assert response.status_code == 201
    assert SOURCE_URL not in response.text
    return response.json()


def preview(client, source):
    response = client.post(
        f"{ACCOUNT}/{source['id']}/previews", json={"expected_revision": source["revision"]}
    )
    assert response.status_code == 200
    assert SOURCE_URL not in response.text and UPSTREAM_UUID not in response.text
    return response.json()


def confirm(client, value):
    payload = {
        "expected_revision": value["source_revision"], "accept_changes": True,
        "selected_node_ids": [item["node_id"] for item in value["nodes"] if item["selectable"]],
    }
    return client.post(
        f"{ACCOUNT}/{value['source_id']}/previews/{value['id']}/confirm", json=payload
    )


def test_owned_uri_source_requires_explicit_preview_confirmation_and_merges_only_primary(workspace):
    operator, alice, bob, calls = workspace
    path = token_path(operator)
    before = operator.get(path)
    state_paths = ["/api/v1/nodes", "/api/v1/plans", "/api/v1/users/alice/traffic"]
    unchanged = [operator.get(path).json() for path in state_paths]
    source = create(alice)
    assert source["owner_username"] == "alice" and source["revision"] == 1
    assert not calls and operator.get(path).text == before.text
    assert bob.get(ACCOUNT).json()["sources"] == []
    value = preview(alice, source)
    assert len(calls) == 1 and operator.get(path).text == before.text
    receipt = confirm(alice, value)
    assert receipt.status_code == 200 and receipt.json()["imported_count"] == 1
    assert confirm(alice, value).json() == receipt.json()
    output = yaml.safe_load(operator.get(path).text)["proxies"]
    assert output[-1]["uuid"] == UPSTREAM_UUID and output[-1]["name"] == "Owned-node"
    assert output[:-1] == yaml.safe_load(before.text)["proxies"]
    assert len(calls) == 1
    assert [operator.get(path).json() for path in state_paths] == unchanged
    for response in (alice.get(ACCOUNT), alice.get(f"{ACCOUNT}/{source['id']}")):
        assert response.headers["cache-control"] == "no-store"
        assert SOURCE_URL not in response.text and UPSTREAM_UUID not in response.text


def test_every_source_node_preview_and_receipt_route_fences_other_owners(workspace):
    operator, alice, bob, calls = workspace
    own, other = create(alice), create(bob)
    value = preview(bob, other)
    assert confirm(bob, value).status_code == 200
    before = operator.get(f"{ADMIN}/{other['id']}").json()
    other_path = f"{ACCOUNT}/{other['id']}"
    preview_path = f"{other_path}/previews/{value['id']}"
    attempts = [
        ("GET", other_path, None),
        ("PUT", other_path, {"expected_revision": 2, "name": "Take over", "enabled": False}),
        ("POST", other_path + "/delete", {"expected_revision": 2, "confirm": True}),
        ("PUT", other_path + "/nodes/" + value["nodes"][0]["node_id"],
         {"expected_revision": 2, "name": "Take over", "enabled": False}),
        ("POST", other_path + "/previews", {"expected_revision": 2}),
        ("GET", preview_path, None),
        ("POST", preview_path + "/confirm",
         {"expected_revision": 1, "selected_node_ids": [], "accept_changes": True}),
        ("DELETE", preview_path, None),
        ("GET", f"{ACCOUNT}/{own['id']}/previews/{value['id']}", None),
        ("DELETE", f"{ACCOUNT}/{own['id']}/previews/{value['id']}", None),
        ("DELETE", f"{ACCOUNT}/{own['id']}/previews/{uuid4()}", None),
    ]
    for method, path, body in attempts:
        response = alice.request(method, path, json=body)
        assert response.status_code == 404, (method, response.status_code)
        assert SOURCE_URL not in response.text and UPSTREAM_UUID not in response.text
    assert operator.get(f"{ADMIN}/{other['id']}").json() == before
    assert len(calls) == 1
    assert [item["id"] for item in alice.get(ACCOUNT).json()["sources"]] == [own["id"]]
    assert len(operator.get(ADMIN).json()["sources"]) == 2


def test_owner_in_request_is_forbidden_and_source_https_policy_is_unchanged(workspace):
    _, alice, _, calls = workspace
    for value in (
        {"owner_username": "bob", "url": SOURCE_URL},
        {"owner_username": "alice", "url": SOURCE_URL},
        {"url": "http://provider.example/private"},
        {"url": "https://127.0.0.1/private"},
        {"url": "https://169.254.169.254/latest/meta-data/"},
        {"url": "https://[::1]/private"},
    ):
        response = alice.post(ACCOUNT, json={"name": "Private", **value})
        assert response.status_code == 422
        assert value["url"] not in response.text
    source = create(alice)
    result = alice.put(f"{ACCOUNT}/{source['id']}", json={
        "expected_revision": 1, "name": "Hijack", "enabled": True, "owner_username": "bob",
    })
    assert result.status_code == 422 and not calls


def test_cookie_roles_csrf_origin_and_revoked_session_are_real_guards(workspace):
    operator, alice, _, calls = workspace
    app = operator.app
    anonymous = TestClient(app, base_url="https://testserver")
    try:
        assert anonymous.get(ACCOUNT).status_code == 401
    finally:
        anonymous.close()
    assert operator.get(ACCOUNT).status_code == 401  # An admin cookie is not a subscriber.
    assert alice.get(ADMIN).status_code == 401
    for headers in ({"X-CSRF-Token": ""}, {"Origin": "https://evil.example"}):
        response = alice.post(ACCOUNT, json={"name": "Blocked", "url": SOURCE_URL}, headers=headers)
        assert response.status_code == 403 and SOURCE_URL not in response.text
    assert alice.post("/api/v1/account/logout").status_code == 204
    assert alice.get(ACCOUNT).status_code == 401 and not calls


def test_own_rename_disable_delete_and_stale_cas_do_not_mutate_other_sources(workspace):
    operator, alice, bob, _ = workspace
    source, other = create(alice), create(bob)
    value = preview(alice, source)
    assert confirm(alice, value).status_code == 200
    prefix = f"{ACCOUNT}/{source['id']}"
    updated = alice.put(prefix + "/nodes/" + value["nodes"][0]["node_id"], json={
        "expected_revision": 2, "name": "我的节点", "enabled": False,
    })
    assert updated.status_code == 200 and updated.json()["source"]["revision"] == 3
    saved = alice.put(prefix, json={"expected_revision": 3, "name": "我的来源", "enabled": False})
    assert saved.status_code == 200 and saved.json()["revision"] == 4
    stale = alice.put(prefix, json={"expected_revision": 3, "name": "Stale", "enabled": True})
    assert stale.status_code == 409
    assert alice.get(prefix).json()["source"]["name"] == "我的来源"
    removed = alice.post(prefix + "/delete", json={"expected_revision": 4, "confirm": True})
    assert removed.status_code == 200
    assert alice.get(prefix).status_code == 404
    assert operator.get(f"{ADMIN}/{other['id']}").json()["source"]["revision"] == 1


def test_source_deleted_during_fetch_cannot_be_recreated_by_late_account_preview(workspace):
    operator, alice, _, _ = workspace
    source = create(alice)
    entered, release = Event(), Event()

    def held_fetch(_url, *, user_agent):
        entered.set()
        assert release.wait(5)
        return ExternalFetchResult(body=BODY, metadata={})

    operator.app.state.external_subscriptions.fetcher = held_fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            alice.post, f"{ACCOUNT}/{source['id']}/previews", json={"expected_revision": 1}
        )
        try:
            assert entered.wait(5)
            assert operator.post(f"{ADMIN}/{source['id']}/delete", json={
                "expected_revision": 1, "confirm": True,
            }).status_code == 200
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 404
    with operator.app.state.inventory._session() as session:
        assert session.get(ExternalSourceModel, source["id"]) is None
        assert session.scalar(select(func.count()).select_from(ExternalPreviewModel)) == 0


def test_store_rechecks_owner_inside_commit_after_network_returns(workspace):
    operator, alice, _, _ = workspace
    source = create(alice)
    store = operator.app.state.external_subscriptions

    def transfer_while_fetching(_url, *, user_agent):
        # Controlled concurrent DB mutation, not an API-supported owner change.
        with operator.app.state.inventory._session() as session:
            row = session.get(ExternalSourceModel, source["id"])
            row.owner_username = "bob"
            session.commit()
        return ExternalFetchResult(body=BODY, metadata={})

    store.fetcher = transfer_while_fetching
    with pytest.raises(ExternalSubscriptionNotFound):
        store.prepare_preview(source["id"], 1, owner_username="alice")
    with pytest.raises(ExternalSubscriptionNotFound):
        store.detail(source["id"], owner_username="alice")
    with pytest.raises(ExternalSubscriptionNotFound):
        store.preview(source["id"], uuid4(), owner_username="alice")
    with operator.app.state.inventory._session() as session:
        assert session.scalar(select(func.count()).select_from(ExternalPreviewModel)) == 0


def test_refresh_schedule_enforces_subscriber_ownership_csrf_and_explicit_consent(workspace):
    operator, alice, bob, calls = workspace
    source = create(alice)
    endpoint = f"{ACCOUNT}/{source['id']}/refresh-schedule"
    payload = dict(expected_revision=1, enabled=True, interval_minutes=60,
                   scope="all", accept_changes=True)
    assert bob.put(endpoint, json=payload).status_code == 404
    assert operator.put(endpoint, json=payload).status_code == 401
    for headers in ({"X-CSRF-Token": ""}, {"Origin": "https://evil.example"}):
        assert alice.put(endpoint, json=payload, headers=headers).status_code == 403
    assert alice.put(endpoint, json={**payload, "owner_username": "bob"}).status_code == 422
    assert alice.put(endpoint, json={**payload, "accept_changes": False}).status_code == 422
    response = alice.put(endpoint, json=payload)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["refresh"]["enabled"] is True
    assert response.json()["refresh"]["scope"] == "all"
    assert SOURCE_URL not in response.text and not calls
    assert alice.put(endpoint, json=payload).status_code == 409
