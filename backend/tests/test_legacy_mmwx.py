from hashlib import sha256

import bcrypt
import pyotp
from conftest import authenticated_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.inventory import (
    LEGACY_SUBSCRIPTION_BEARER_GENERATION,
    SECURE_SUBSCRIPTION_BEARER_GENERATION,
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


def app(tmp_path, *, key=True, short_links=True):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'legacy.db'}",
        subscriber_totp_key=Fernet.generate_key().decode() if key else None,
        short_links_enabled=short_links,
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
        "mapped_packages": 0,
        "assigned_plans": 0,
        "imported_profiles": 0,
        "replaced_profiles": 0,
        "skipped_profiles": 0,
        "imported_profile_assignments": 0,
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
        assert token.bearer_generation == LEGACY_SUBSCRIPTION_BEARER_GENERATION

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


def test_default_import_rotates_legacy_bearer_and_aliases_once(tmp_path):
    application, operator = app(tmp_path, short_links=False)
    _, _, _, plan_id = create_catalog_fixture(operator)
    operator.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    source, _ = bundle(totp_enabled=False, totp_secret=None, recovery_code_hashes=[])
    state = preview(operator, source)
    apply(operator, source, state).raise_for_status()

    current = operator.get("/api/v1/users/alice/subscription-token").json()["subscription"]
    assert current["token"] != "legacy-token-alice-1234567890"
    assert len(current["token"]) >= 43
    assert current["generated_short_code"] != "lga"
    assert current["custom_short_code"] is None
    with application.state.inventory._session() as session:
        token = session.get(ProductUserSubscriptionTokenModel, "alice")
        assert token.bearer_generation == SECURE_SUBSCRIPTION_BEARER_GENERATION

    for legacy_key in ("legacy-token-alice-1234567890", "lga", "legacy_link"):
        assert operator.get(f"/api/v1/subscribe/{legacy_key}").status_code == 404
    assert operator.get(current["subscription_url"]).status_code == 200

    _, restarted = app(tmp_path, short_links=False)
    after_restart = restarted.get("/api/v1/users/alice/subscription-token").json()["subscription"]
    assert after_restart == current


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


def test_import_maps_packages_profiles_assignments_and_legacy_x_links(tmp_path):
    application, operator = app(tmp_path)
    _, _, node_id, plan_id = create_catalog_fixture(operator)
    operator.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).raise_for_status()
    source, _ = bundle(
        source_role="user",
        totp_enabled=False,
        totp_secret=None,
        recovery_code_hashes=[],
        source_package_id=7,
        package_started_at="2026-08-01T00:00:00Z",
        package_expires_at="2027-08-01T00:00:00Z",
        is_reset=True,
        reset_day=1,
    )
    source["packages"] = [{"source_id": 7, "name": "Legacy Premium", "short_code": "pkg"}]
    source["subscription_profiles"] = [
        {
            "source_id": 11,
            "owner_username": "alice",
            "name": "Mobile",
            "description": "Phone profile",
            "source_type": "create",
            "filename": "mobile.yaml",
            "template_filename": "mobile-template.yaml",
            "file_short_code": "mob",
            "custom_short_code": "phone",
            "selected_tags": ["mobile"],
            "selected_node_ids": [101],
            "selected_custom_rule_ids": [],
            "selected_override_script_ids": [],
            "raw_output": False,
            "sort_order": 1,
            "expires_at": None,
            "assigned_usernames": ["alice"],
            "created_at": "2026-08-01T00:00:00Z",
            "updated_at": "2026-08-02T00:00:00Z",
        },
        {
            "source_id": 12,
            "owner_username": "alice",
            "name": "Raw backup",
            "description": "",
            "source_type": "upload",
            "filename": "raw.yaml",
            "template_filename": "",
            "file_short_code": "raw",
            "custom_short_code": None,
            "selected_tags": [],
            "selected_node_ids": [],
            "selected_custom_rule_ids": [],
            "selected_override_script_ids": [],
            "raw_output": True,
            "sort_order": 2,
            "expires_at": None,
            "assigned_usernames": ["alice"],
            "created_at": None,
            "updated_at": None,
        },
    ]
    missing = preview(operator, source)
    assert not missing["ready"] and "Map every in-use legacy package" in missing["blockers"][0]

    mappings = {"package_mappings": {"7": plan_id}}
    response = operator.post(
        BASE + "/preview",
        json={"bundle": source, "replace_existing": False, **mappings},
    )
    assert response.status_code == 200
    state = response.json()
    assert state["ready"] and state["mapped_packages"] == 1
    assert state["imported_profiles"] == 2 and state["imported_profile_assignments"] == 2
    apply(operator, source, state, **mappings).raise_for_status()

    for code in ("mob" + "lga", "phone", "pkg" + "lga"):
        rendered = operator.get(f"/x/{code}?format=xray")
        assert rendered.status_code == 200, rendered.text
        assert rendered.json()["outbounds"][0]["protocol"] == "vless"
    assert operator.get("/x/rawlga?format=xray").status_code == 404

    profiles = operator.get("/api/v1/subscription-profiles").json()["profiles"]
    assert [item["name"] for item in profiles] == ["Mobile", "Raw backup"]
    assert profiles[0]["assigned_usernames"] == ["alice"]
    configured = operator.put(
        f"/api/v1/subscription-profiles/{profiles[1]['id']}",
        json={
            "name": "Raw managed",
            "description": "Rebuilt in Open Node",
            "node_ids": [node_id],
            "clash_template_id": None,
            "surge_template_id": None,
            "assigned_usernames": ["alice"],
            "enabled": True,
            "expected_revision": profiles[1]["revision"],
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["enabled"]
    assert operator.get("/x/rawlegacy_link?format=xray").status_code == 200
    assert (
        operator.put(
            f"/api/v1/subscription-profiles/{profiles[1]['id']}",
            json={
                "name": "Stale",
                "description": "",
                "node_ids": [],
                "assigned_usernames": ["alice"],
                "enabled": True,
                "expected_revision": profiles[1]["revision"],
            },
        ).status_code
        == 409
    )
    subscriber = TestClient(application, base_url="https://testserver")
    assert login(subscriber, password=PASSWORD).status_code == 200
    assigned = subscriber.get("/api/v1/account/subscription-profiles")
    assert assigned.status_code == 200
    assert [item["name"] for item in assigned.json()["profiles"]] == [
        "Mobile",
        "Raw managed",
    ]
    assert "/x/moblegacy_link" in assigned.json()["profiles"][0]["subscription_url"]

    application.state.settings.short_links_enabled = False
    assert operator.get("/x/phone?format=xray").status_code == 404
    assert subscriber.get("/api/v1/account/subscription-profiles").json()["profiles"] == []
