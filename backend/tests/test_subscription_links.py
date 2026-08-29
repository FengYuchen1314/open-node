from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.subscriber_auth import SubscriberShortCodeUpdate
from open_node.domain.subscription_links import SubscriptionShortCodeUpdate
from open_node.main import create_app
from open_node.services import inventory as module
from open_node.services.inventory import (
    LEGACY_SUBSCRIPTION_BEARER_GENERATION,
    SECURE_SUBSCRIPTION_BEARER_GENERATION,
    CommandModel,
    InventoryStore,
    ProductUserConflict,
    ProductUserModel,
    ProductUserSubscriptionTokenModel,
)
from open_node.services.subscriber_auth import SubscriberAuthenticationError
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from test_subscriber_auth import PASSWORD, enable, identity, login, make, provision
from test_subscriptions import create_catalog_fixture, make_client, sqlite_url

PATH = "/api/v1/user-subscription-short-code"
ACCOUNT = "/api/v1/account/subscription-short-code"


def links(client, username="alice"):
    return (
        client.get("/api/v1/user-subscription-token", params={"username": username})
        .raise_for_status()
        .json()["subscription"]
    )


def save(client, code, username="alice", revision=None):
    return client.put(
        PATH,
        params={"username": username},
        json={
            "custom_short_code": code,
            "expected_revision": revision or links(client, username)["revision"],
        },
    )


@pytest.fixture
def env(tmp_path):
    client = make_client(tmp_path)
    token, _, _, plan_id = create_catalog_fixture(client)
    client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    return client, token, plan_id


def test_short_and_legacy_bearer_links_are_disabled_by_default(tmp_path):
    client = authenticated_client(
        create_app(Settings(database_url=sqlite_url(tmp_path / "secure-default.db")))
    )
    _, _, _, plan_id = create_catalog_fixture(client)
    client.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()

    current = links(client)
    assert current["short_links_enabled"] is False
    assert current["short_url"] == current["subscription_url"]
    assert client.get(current["subscription_url"]).status_code == 200
    assert client.get(f"/api/v1/subscribe/{current['generated_short_code']}").status_code == 404
    assert save(client, "guessable-alias").status_code == 403
    assert client.get(f"/x/pkg{current['generated_short_code']}").status_code == 404
    with client.app.state.inventory._coordinated_session() as db:
        db.get(
            ProductUserSubscriptionTokenModel, "alice"
        ).bearer_generation = LEGACY_SUBSCRIPTION_BEARER_GENERATION
        db.commit()
    assert client.get(current["subscription_url"]).status_code == 404


def test_custom_clear_and_replace_preserve_system_links_credentials_usage_and_commands(env):
    client, agent_token, _ = env
    before = links(client)
    credentials = client.get("/api/v1/users/alice/credentials").json()
    email = credentials["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={"token": agent_token, "stats": {"user": {email: {"uplink": 40, "downlink": 60}}}},
    ).raise_for_status()
    user = client.get("/api/v1/users/alice/settings").json()
    usage = client.get("/api/v1/users/alice/traffic").json()
    store = client.app.state.inventory
    with store._session() as db:
        commands = db.scalar(select(func.count()).select_from(CommandModel))
    response = save(client, "  Alice_Link-2  ")
    assert response.status_code == 200, response.text
    current = response.json()["subscription"]
    assert response.json()["license_required"] is False
    assert response.headers["cache-control"] == "no-store"
    assert current["short_code"] == current["custom_short_code"] == "Alice_Link-2"
    assert current["generated_short_code"] == before["short_code"]
    for key in ("token", "subscription_url", "created_at"):
        assert current[key] == before[key]
    for format in ("clash", "sing-box", "xray", "uri-list", "base64"):
        expected = client.get(before["subscription_url"], params={"format": format})
        assert expected.status_code == 200
        for url in (before["short_url"], current["short_url"]):
            result = client.get(url, params={"format": format})
            assert result.status_code == 200
            assert result.content == expected.content
            assert (
                result.headers["subscription-userinfo"] == expected.headers["subscription-userinfo"]
            )
    assert client.get(current["short_url"].lower()).status_code == 404
    assert save(client, "Alice_Link-2").json()["subscription"] == current
    replacement = save(client, "second-link").json()["subscription"]
    assert client.get(current["short_url"]).status_code == 404
    assert client.get(replacement["short_url"]).status_code == 200
    cleared = save(client, "").json()["subscription"]
    assert cleared["custom_short_code"] is None
    assert cleared["short_url"] == before["short_url"]
    assert client.get(replacement["short_url"]).status_code == 404
    assert client.get(before["short_url"]).status_code == 200
    assert client.get("/api/v1/users/alice/credentials").json() == credentials
    assert client.get("/api/v1/users/alice/settings").json() == user
    assert client.get("/api/v1/users/alice/traffic").json() == usage
    with store._session() as db:
        assert db.scalar(select(func.count()).select_from(CommandModel)) == commands


@pytest.mark.parametrize(
    "code",
    [
        "a",
        "x" * 17,
        "a/b",
        "a?b",
        "a#b",
        "a%b",
        "a b",
        "a\\b",
        ".",
        "..",
        "\u4e2d\u6587",
        "a\x00b",
        "AdMiN",
        "ROOT",
        "open-node",
        "account",
        "subscribe",
    ],
)
def test_invalid_and_reserved_codes(code):
    with pytest.raises(ValidationError):
        SubscriptionShortCodeUpdate(custom_short_code=code, expected_revision="a" * 64)


@pytest.mark.parametrize("code", [None, 123, True, {}, []])
def test_invalid_api_types_do_not_change_links(env, code):
    client, _, _ = env
    before = links(client)
    assert save(client, code).status_code == 422
    assert links(client) == before


def test_other_users_generated_custom_and_legacy_token_namespaces_are_reserved(env):
    client, _, plan = env
    client.post("/api/v1/users", json={"username": "Bob"}).raise_for_status()
    client.post("/api/v1/users/Bob/plan", json={"plan_id": plan}).raise_for_status()
    bob = links(client, "Bob")
    assert save(client, "Bob_Link", "Bob").status_code == 200
    before = links(client)
    for value in ("bob", "BOB_LINK", bob["short_code"].upper()):
        response = save(client, value)
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "This short code is unavailable"
    with client.app.state.inventory._coordinated_session() as db:
        db.get(ProductUserSubscriptionTokenModel, "Bob").token = "LegacyKey"
        db.commit()
    assert save(client, "legacykey").status_code == 409
    assert links(client) == before
    assert client.get(bob["short_url"]).status_code == 200
    assert client.get("/api/v1/subscribe/LegacyKey").status_code == 200
    assert save(client, "alice").status_code == 200


def test_revision_guards_reset_and_concurrent_edit_without_touching_user_revision(env):
    client, _, _ = env
    first = links(client)
    second = save(client, "first-code").json()["subscription"]
    assert save(client, "stale-code", revision=first["revision"]).status_code == 409
    assert links(client) == second
    reset = (
        client.post("/api/v1/users/alice/subscription-token/reset")
        .raise_for_status()
        .json()["subscription"]
    )
    assert reset["custom_short_code"] is None
    assert reset["token"] != first["token"]
    assert reset["generated_short_code"] != first["generated_short_code"]
    for url in (first["subscription_url"], first["short_url"], second["short_url"]):
        assert client.get(url).status_code == 404
    assert client.get(reset["short_url"]).status_code == 200
    assert save(client, "stale-code", revision=second["revision"]).status_code == 409


@pytest.mark.parametrize("username", ["a/b", ".", "..", "reader+name"])
def test_query_alias_targets_exact_username(env, username):
    client, _, plan = env
    original = links(client)
    client.post("/api/v1/users", json={"username": username}).raise_for_status()
    client.post(
        "/api/v1/user-plan", params={"username": username}, json={"plan_id": plan}
    ).raise_for_status()
    result = save(client, "own-code", username)
    assert result.status_code == 200, result.text
    assert result.json()["subscription"]["username"] == username
    assert links(client) == original


def test_two_controllers_cannot_claim_case_variants_of_the_same_code(env):
    client, _, _ = env
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    revisions = {name: links(client, name)["revision"] for name in ("alice", "bob")}
    stores = [
        InventoryStore(client.app.state.settings.database_url, short_links_enabled=True)
        for _ in range(2)
    ]
    barrier = Barrier(2)

    def claim(item):
        index, username, code = item
        barrier.wait(timeout=10)
        try:
            return (
                stores[index]
                .set_subscription_short_code(
                    username,
                    SubscriptionShortCodeUpdate(
                        custom_short_code=code, expected_revision=revisions[username]
                    ),
                )
                .username
            )
        except ProductUserConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(claim, [(0, "alice", "Same-Code"), (1, "bob", "same-code")]))
    assert sum(value is not None for value in result) == 1
    assert sum(links(client, name)["custom_short_code"] is not None for name in revisions) == 1


def test_generators_avoid_custom_and_legacy_namespaces(env, monkeypatch):
    client, _, _ = env
    assert save(client, "abcdef12").status_code == 200
    store = client.app.state.inventory
    with store._session() as db:
        values = iter(["abcdef12", "00112233"])
        monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex=next(values)))
        assert store._unique_subscription_short_code(db) == "00112233"
        values = iter(["ABCDEF12", "new-unique-token"])
        monkeypatch.setattr(module, "token_urlsafe", lambda _: next(values))
        assert store._unique_subscription_token(db) == "new-unique-token"


def test_database_rejects_case_variant_duplicates_and_restart_preserves_codes(env):
    client, _, _ = env
    client.post("/api/v1/users", json={"username": "bob"}).raise_for_status()
    links(client, "bob")
    saved = save(client, "Unique-Link").json()["subscription"]
    store = client.app.state.inventory
    with pytest.raises(IntegrityError), store._coordinated_session() as db:
        db.get(ProductUserSubscriptionTokenModel, "bob").custom_short_code = "UNIQUE-LINK"
        db.commit()
    restarted = InventoryStore(
        client.app.state.settings.database_url,
        short_links_enabled=True,
    )
    restarted.create_schema()
    assert restarted.get_or_create_subscription_token("alice").revision == saved["revision"]
    assert links(client) == saved


def test_old_schema_upgrade_preserves_all_existing_keys_in_compatibility_mode(env):
    client, _, _ = env
    old = links(client)
    store = client.app.state.inventory
    with store._engine.begin() as db:
        db.execute(text("DROP INDEX uq_subscription_custom_short_code"))
        db.execute(text("DROP INDEX ix_product_user_subscription_tokens_custom_short_code"))
        db.execute(
            text("ALTER TABLE product_user_subscription_tokens DROP COLUMN custom_short_code")
        )
        db.execute(
            text("ALTER TABLE product_user_subscription_tokens DROP COLUMN bearer_generation")
        )
    store.create_schema()
    upgraded = links(client)
    for key in ("token", "generated_short_code", "created_at", "updated_at"):
        assert upgraded[key] == old[key]
    with store._session() as db:
        token = db.get(ProductUserSubscriptionTokenModel, "alice")
        assert token.bearer_generation == LEGACY_SUBSCRIPTION_BEARER_GENERATION
    assert save(client, "after-upgrade").status_code == 200
    assert client.get(old["subscription_url"]).status_code == 200
    assert_indexed_key_lookup(store)


def test_old_schema_upgrade_rotates_unmarked_keys_once_by_default(tmp_path):
    database_url = sqlite_url(tmp_path / "secure-upgrade.db")
    first = authenticated_client(create_app(Settings(database_url=database_url)))
    _, _, _, plan_id = create_catalog_fixture(first)
    first.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    links(first)
    store = first.app.state.inventory
    legacy = {
        "token": "legacy-token",
        "short_code": "oldshort",
        "custom_short_code": "oldcustom",
    }
    with store._engine.begin() as db:
        db.execute(
            text(
                "UPDATE product_user_subscription_tokens "
                "SET token=:token, short_code=:short_code, custom_short_code=:custom_short_code"
            ),
            legacy,
        )
        db.execute(
            text("ALTER TABLE product_user_subscription_tokens DROP COLUMN bearer_generation")
        )

    restarted = authenticated_client(create_app(Settings(database_url=database_url)))
    current = links(restarted)
    assert current["token"] != legacy["token"]
    assert len(current["token"]) >= 43
    assert current["generated_short_code"] != legacy["short_code"]
    assert current["custom_short_code"] is None
    with restarted.app.state.inventory._session() as db:
        token = db.get(ProductUserSubscriptionTokenModel, "alice")
        assert token.bearer_generation == SECURE_SUBSCRIPTION_BEARER_GENERATION
    for key in legacy.values():
        assert restarted.get(f"/api/v1/subscribe/{key}").status_code == 404
    assert restarted.get(current["subscription_url"]).status_code == 200

    second_restart = authenticated_client(create_app(Settings(database_url=database_url)))
    assert links(second_restart) == current


def assert_indexed_key_lookup(store):
    with store._engine.connect() as db:
        rows = db.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT username FROM product_user_subscription_tokens "
                "WHERE token=:key OR short_code=:key OR custom_short_code=:key"
            ),
            {"key": "example-link"},
        ).all()
    plan = "\n".join(row[-1] for row in rows)
    assert "SCAN product_user_subscription_tokens" not in plan, plan
    assert "ix_product_user_subscription_tokens_custom_short_code" in plan, plan


def test_all_subscription_key_forms_use_indexed_lookups(env):
    assert_indexed_key_lookup(env[0].app.state.inventory)


@pytest.mark.parametrize("restriction", ["disabled", "expired", "quota"])
def test_custom_and_generated_links_obey_existing_availability(env, restriction):
    client, agent_token, _ = env
    before = links(client)
    custom = save(client, "restricted-link").json()["subscription"]
    email = client.get("/api/v1/users/alice/credentials").json()["credentials"][0]["email"]
    client.post(
        "/api/v1/agents/telemetry",
        json={"token": agent_token, "stats": {"user": {email: {"uplink": 2, "downlink": 2}}}},
    ).raise_for_status()
    with client.app.state.inventory._coordinated_session() as db:
        user = db.get(ProductUserModel, "alice")
        if restriction == "disabled":
            user.is_active = False
        elif restriction == "expired":
            user.plan_expires_at = datetime.now(UTC) - timedelta(days=1)
        else:
            user.traffic_limit_override_bytes = 1
        db.commit()
    for url in (before["subscription_url"], before["short_url"], custom["short_url"]):
        assert client.get(url).status_code == 404


def test_removal_blocks_edits_and_deletes_custom_links(env):
    client, _, _ = env
    custom = save(client, "retired-user").json()["subscription"]
    detail = client.get("/api/v1/users/alice/settings").json()
    removed = client.post(
        "/api/v1/users/alice/remove",
        json={
            "expected_revision": detail["revision"],
            "confirm_name": "alice",
            "acknowledge_runtime_restart": True,
            "acknowledge_unmanaged_credentials": True,
        },
    )
    assert removed.status_code == 202, removed.text
    assert client.get(custom["short_url"]).status_code == 404
    assert save(client, "new-code", revision=custom["revision"]).status_code in (404, 409)


def test_operator_authentication_and_csrf_are_required(env):
    client, _, _ = env
    before = links(client)
    body = {"custom_short_code": "private-code", "expected_revision": before["revision"]}
    anonymous = TestClient(client.app, base_url="https://testserver")
    assert anonymous.put(PATH, params={"username": "alice"}, json=body).status_code == 401
    assert (
        client.put(
            PATH, params={"username": "alice"}, json=body, headers={"X-CSRF-Token": "bad"}
        ).status_code
        == 403
    )
    assert links(client) == before


def test_subscriber_edits_only_own_links_with_password_proof(tmp_path):
    app, operator, client = make(tmp_path, catalog=True)
    login(client).raise_for_status()
    before = links(operator)
    body = {
        "custom_short_code": "self-service",
        "expected_revision": before["revision"],
        "password": PASSWORD,
    }
    assert client.put(ACCOUNT, json={**body, "password": "wrong"}).status_code == 400
    assert client.put(ACCOUNT, json={**body, "username": "bob"}).status_code == 422
    assert client.put(ACCOUNT, json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert (
        client.put(ACCOUNT, json=body, headers={"Origin": "https://other.example"}).status_code
        == 403
    )
    assert client.put(PATH, params={"username": "alice"}, json=body).status_code == 401
    assert operator.put(ACCOUNT, json=body).status_code == 401
    response = client.put(ACCOUNT, json=body)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["subscription"]["token"] == before["token"]
    assert client.get(response.json()["subscription"]["short_url"]).status_code == 200
    stale = identity(app, client)
    provision(operator)
    with pytest.raises(SubscriberAuthenticationError):
        app.state.subscriber_auth.set_short_code(
            stale, SubscriberShortCodeUpdate(**{**body, "custom_short_code": "stale-session"})
        )
    assert links(operator)["custom_short_code"] == "self-service"


def test_subscriber_short_code_edit_is_disabled_by_default(tmp_path):
    _app, operator, client = make(tmp_path, catalog=True, short_links=False)
    login(client).raise_for_status()
    before = links(operator)
    response = client.put(
        ACCOUNT,
        json={
            "custom_short_code": "guessable-alias",
            "expected_revision": before["revision"],
            "password": PASSWORD,
        },
    )

    assert response.status_code == 403
    assert links(operator) == before


def test_factor_proof_is_required_and_failed_conflicts_do_not_consume_recovery_code(
    tmp_path, monkeypatch
):
    app, operator, client = make(tmp_path, catalog=True)
    _, _, recovery = enable(app, client, monkeypatch)
    before = links(operator)
    body = {
        "custom_short_code": "protected-link",
        "expected_revision": before["revision"],
        "password": PASSWORD,
    }
    assert client.put(ACCOUNT, json=body).status_code == 400
    assert (
        client.put(
            ACCOUNT, json={**body, "code": recovery[0], "expected_revision": "0" * 64}
        ).status_code
        == 409
    )
    assert client.get("/api/v1/account/security").json()["recovery_codes_remaining"] == len(
        recovery
    )
    response = client.put(ACCOUNT, json={**body, "code": recovery[0]})
    assert response.status_code == 200, response.text
    current = response.json()["subscription"]
    assert (
        client.get("/api/v1/account/security").json()["recovery_codes_remaining"]
        == len(recovery) - 1
    )
    assert (
        client.put(
            ACCOUNT,
            json={
                **body,
                "code": recovery[0],
                "custom_short_code": "replayed-code",
                "expected_revision": current["revision"],
            },
        ).status_code
        == 400
    )
    reset = (
        client.post(
            "/api/v1/account/subscription-token/reset",
            json={"password": PASSWORD, "code": recovery[1]},
        )
        .raise_for_status()
        .json()["subscription"]
    )
    assert reset["custom_short_code"] is None
    assert client.get(current["short_url"]).status_code == 404
