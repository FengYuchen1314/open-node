from hashlib import sha256

import bcrypt
import pyotp
from conftest import authenticated_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import (
    ProductUserModel,
    ProductUserSubscriptionTokenModel,
)
from open_node.services.subscriber_auth import SubscriberAccount
from test_subscriber_auth import PREFIX, login, provision, verify
from test_subscriptions import create_catalog_fixture

BASE = "/api/v1/migrations/mmwx/identities"
PASSWORD = "legacy-password-for-tests"


def legacy_hash(password=PASSWORD):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def bundle(username="alice", **changes):
    recovery = "deadbeef"
    user = {
        "username": username,
        "password_hash": legacy_hash(),
        "email": "legacy@example.com",
        "display_name": "Legacy Alice",
        "source_role": "admin",
        "is_active": True,
        "totp_enabled": True,
        "totp_secret": pyotp.random_base32(),
        "recovery_code_hashes": [sha256(recovery.encode()).hexdigest()],
        "token": "legacy-token-alice-1234567890",
        "generated_short_code": "lga",
        "custom_short_code": "legacy_link",
        **changes,
    }
    return {"version": 1, "source_revision": "main-test", "users": [user]}, recovery


def app(tmp_path, *, key=True):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'legacy.db'}",
        subscriber_totp_key=Fernet.generate_key().decode() if key else None,
    )
    application = create_app(settings)
    return application, authenticated_client(application)


def preview(operator, source, replace=False):
    response = operator.post(
        BASE + "/preview", json={"bundle": source, "replace_existing": replace}
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(operator, source, state, replace=False, **changes):
    payload = {
        "bundle": source,
        "replace_existing": replace,
        "expected_revision": state["revision"],
        "confirm_user_count": state["total_users"],
        **changes,
    }
    return operator.post(BASE + "/import", json=payload)


def test_import_preserves_login_totp_recovery_and_subscription_token(tmp_path):
    application, operator = app(tmp_path)
    _, _, _, plan_id = create_catalog_fixture(operator)
    operator.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    source, recovery = bundle()
    state = preview(operator, source)
    assert state | {"revision": "hidden"} == {
        "revision": "hidden",
        "ready": True,
        "total_users": 1,
        "new_users": 0,
        "existing_users": 1,
        "imported_accounts": 1,
        "replaced_accounts": 0,
        "skipped_accounts": 0,
        "imported_tokens": 1,
        "replaced_tokens": 0,
        "skipped_tokens": 0,
        "imported_totp": 1,
        "blockers": [],
        "warnings": ["alice: source administrator will import as subscriber"],
        "license_required": False,
    }
    response = apply(operator, source, state)
    assert response.status_code == 200, response.text
    assert PASSWORD not in response.text
    assert source["users"][0]["totp_secret"] not in response.text

    with application.state.inventory._session() as session:
        user = session.get(ProductUserModel, "alice")
        account = session.get(SubscriberAccount, "alice")
        token = session.get(ProductUserSubscriptionTokenModel, "alice")
        assert user.role == "user" and user.display_name == "Alice"
        assert account.password_hash.startswith("$2")
        assert source["users"][0]["totp_secret"] not in account.totp_secret
        assert account.recovery_hashes == ["legacy:" + sha256(recovery.encode()).hexdigest()]
        assert (token.token, token.short_code, token.custom_short_code) == (
            "legacy-token-alice-1234567890",
            "lga",
            "legacy_link",
        )

    subscriber = TestClient(application, base_url="https://testserver")
    attempt = login(subscriber, password=PASSWORD)
    assert attempt.json()["requires_2fa"]
    completed = verify(subscriber, attempt.json()["challenge"], recovery)
    assert completed.status_code == 200 and completed.json()["authenticated"]
    with application.state.inventory._session() as session:
        account = session.get(SubscriberAccount, "alice")
        assert account.password_hash.startswith("$argon2id$")
        assert account.recovery_hashes == [] and account.totp_secret

    rendered = subscriber.get("/api/v1/subscribe/legacy-token-alice-1234567890?format=xray")
    assert rendered.status_code == 200
    assert rendered.headers["Cache-Control"] == "no-store"


def test_existing_state_is_skipped_or_explicitly_replaced_and_sessions_are_revoked(tmp_path):
    application, operator = app(tmp_path)
    operator.post("/api/v1/users", json={"username": "alice"}).raise_for_status()
    provision(operator)
    current = operator.post("/api/v1/users/alice/subscription-token").json()["subscription"]
    subscriber = TestClient(application, base_url="https://testserver")
    assert login(subscriber).status_code == 200

    source, _ = bundle(totp_enabled=False, totp_secret=None, recovery_code_hashes=[])
    skipped = preview(operator, source)
    assert skipped["skipped_accounts"] == 1 and skipped["skipped_tokens"] == 1
    apply(operator, source, skipped).raise_for_status()
    assert login(TestClient(application, base_url="https://testserver")).status_code == 200
    assert subscriber.get(PREFIX + "/me").status_code == 200
    assert (
        operator.get("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
        == current["token"]
    )

    replacing = preview(operator, source, replace=True)
    assert replacing["replaced_accounts"] == 1 and replacing["replaced_tokens"] == 1
    apply(operator, source, replacing, replace=True).raise_for_status()
    assert subscriber.get(PREFIX + "/me").status_code == 401
    assert (
        login(TestClient(application, base_url="https://testserver"), password=PASSWORD).status_code
        == 200
    )
    assert (
        operator.get("/api/v1/users/alice/subscription-token").json()["subscription"]["token"]
        == "legacy-token-alice-1234567890"
    )


def test_preview_blocks_missing_totp_key_collisions_stale_revision_and_count(tmp_path):
    application, operator = app(tmp_path, key=False)
    operator.post("/api/v1/users", json={"username": "alice"}).raise_for_status()
    source, _ = bundle()
    blocked = preview(operator, source)
    assert not blocked["ready"] and "OPEN_NODE_SUBSCRIBER_TOTP_KEY" in blocked["blockers"][0]
    assert apply(operator, source, blocked).status_code == 409

    source, _ = bundle(totp_enabled=False, totp_secret=None, recovery_code_hashes=[])
    state = preview(operator, source)
    operator.post("/api/v1/users/alice/subscription-token").raise_for_status()
    assert apply(operator, source, state).status_code == 409
    refreshed = preview(operator, source, replace=True)
    assert apply(operator, source, refreshed, replace=True, confirm_user_count=2).status_code == 409

    other, _ = bundle(
        username="bob",
        token="legacy-token-alice-1234567890",
        generated_short_code="bob",
        custom_short_code=None,
        totp_enabled=False,
        totp_secret=None,
        recovery_code_hashes=[],
    )
    combined = {"version": 1, "users": [source["users"][0], other["users"][0]]}
    collision = preview(operator, combined, replace=True)
    assert not collision["ready"] and any("collides" in item for item in collision["blockers"])

    invalid_path = tmp_path / "invalid-totp"
    invalid_path.mkdir()
    _, valid_operator = app(invalid_path)
    invalid, _ = bundle(totp_secret="not-base32***")
    invalid_state = preview(valid_operator, invalid)
    assert not invalid_state["ready"]
    assert invalid_state["blockers"] == ["alice: legacy TOTP secret is invalid"]


def test_secret_validation_errors_are_redacted_and_routes_require_admin(tmp_path):
    application, operator = app(tmp_path)
    source, _ = bundle(password_hash="secret-marker-not-a-hash")
    response = operator.post(BASE + "/preview", json={"bundle": source})
    assert response.status_code == 422
    assert "secret-marker" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    anonymous = TestClient(application, base_url="https://testserver")
    denied = anonymous.post(BASE + "/preview", json={"bundle": source})
    assert denied.status_code == 401 and denied.headers["Cache-Control"] == "no-store"
