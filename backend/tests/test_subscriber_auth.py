from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from types import SimpleNamespace

import pyotp
import pytest
from conftest import authenticated_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.subscriber_auth import SubscriberAccountUpdate, SubscriberProof
from open_node.main import create_app
from open_node.services import subscriber_auth as module
from open_node.services.auth import LoginWindow
from open_node.services.inventory import ProductUserModel
from open_node.services.subscriber_auth import (
    SubscriberAccount,
    SubscriberAuthenticationError,
    SubscriberChallenge,
    SubscriberSession,
)
from sqlalchemy import delete, select, update
from test_subscriptions import create_catalog_fixture

PASSWORD = "subscriber-password-for-tests"
REPLACEMENT = "replacement-password-for-tests"
PREFIX = "/api/v1/account"
MANAGEMENT = "/api/v1/subscriber-accounts"


def make(tmp_path, *, catalog=False, key=True, username="alice", role="user", short_links=True):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'subscriber.db'}",
        subscriber_totp_key=Fernet.generate_key().decode() if key else None,
        short_links_enabled=short_links,
    )
    app = create_app(settings)
    operator = authenticated_client(app)
    if catalog:
        _, _, _, plan_id = create_catalog_fixture(operator)
        assert (
            operator.post("/api/v1/users/alice/plan", json={"plan_id": plan_id}).status_code == 200
        )
    else:
        assert (
            operator.post("/api/v1/users", json={"username": username, "role": role}).status_code
            == 201
        )
    provision(operator, username)
    return app, operator, TestClient(app, base_url="https://testserver")


def provision(operator, username="alice", password=PASSWORD, reset_totp=False):
    params = {"username": username}
    status = operator.get(MANAGEMENT, params=params)
    assert status.status_code == 200, status.text
    response = operator.put(
        MANAGEMENT,
        params=params,
        json={
            "expected_revision": status.json()["revision"],
            "new_password": password,
            "reset_totp": reset_totp,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def login(client, password=PASSWORD, username="alice"):
    response = client.post(
        PREFIX + "/login",
        json={"username": username, "password": password},
        headers={"X-Open-Node-Client": "browser"},
    )
    if response.status_code == 200 and response.json()["authenticated"]:
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def identity(app, client):
    return app.state.subscriber_auth.authenticate(client.cookies.get("open_node_subscriber"))


def enable(app, client, monkeypatch):
    clock = [module.time.time()]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    assert login(client).status_code == 200
    setup = client.post(PREFIX + "/totp/setup", json={"password": PASSWORD})
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    confirmed = client.post(
        PREFIX + "/totp/confirm", json={"code": pyotp.TOTP(secret).at(clock[0])}
    )
    assert confirmed.status_code == 200, confirmed.text
    return clock, secret, confirmed.json()["recovery_codes"]


def verify(client, challenge, code):
    return client.post(
        PREFIX + "/login/verify",
        json={"challenge": challenge, "code": code},
        headers={"X-Open-Node-Client": "browser"},
    )


def clear_limits(app):
    with app.state.auth.session.begin() as db:
        db.execute(delete(LoginWindow))


def test_unconfigured_and_disabled_accounts_do_not_log_in(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'blank.db'}"))
    client = TestClient(app, base_url="https://testserver")
    assert client.get(PREFIX + "/session").json()["authenticated"] is False
    assert login(client).status_code == 401
    operator = authenticated_client(app)
    operator.post("/api/v1/users", json={"username": "alice", "is_active": False})
    assert login(client).status_code == 401
    provision(operator)
    assert login(client).status_code == 401
    assert client.get(PREFIX + "/me").status_code == 401


@pytest.mark.parametrize("role", ["user", "admin"])
def test_roles_cookies_and_operator_routes_are_isolated(tmp_path, role):
    app, operator, client = make(tmp_path, role=role)
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"):
        assert flag in cookie
    assert not client.cookies.get("open_node_session")
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False
    assert operator.get(PREFIX + "/me").status_code == 401
    for path, methods in app.openapi()["paths"].items():
        if (
            path.startswith("/api/v1/account/")
            or "agents" in path
            or "public" in path
            or "subscribe/" in path
            or path == "/x/{code}"
            or path == "/t/{code}"
            or path.endswith(("healthz", "meta", "license/status"))
            or "/auth/" in path
        ):
            continue
        for method in methods:
            if (method, path) == ("get", "/api/v1/branding"):
                public = client.get(path)
                assert public.status_code == 200
                assert set(public.json()) == {"site_title", "brand_title", "license_required"}
                assert public.json()["license_required"] is False
                continue
            response = client.request(method, path)
            assert response.status_code == 401, (method, path, response.text)
    assert client.get(PREFIX + "/me").json()["username"] == "alice"


def test_persistent_hashed_session_rotation_and_logout(tmp_path):
    app, _, client = make(tmp_path)
    assert login(client).status_code == 200
    first = client.cookies.get("open_node_subscriber")
    with app.state.inventory._session() as db:
        account = db.get(SubscriberAccount, "alice")
        device = db.scalar(select(SubscriberSession))
        assert account.password_hash.startswith("$argon2id$")
        assert PASSWORD not in account.password_hash
        assert device.token_hash == sha256(first.encode()).hexdigest()
        assert first not in str(device.__dict__)
    restarted = TestClient(create_app(app.state.settings), base_url="https://testserver")
    restarted.cookies.update(client.cookies)
    assert restarted.get(PREFIX + "/me").status_code == 200
    assert login(client).status_code == 200
    assert first != client.cookies.get("open_node_subscriber")
    assert restarted.get(PREFIX + "/me").status_code == 401
    assert client.post(PREFIX + "/logout").status_code == 204
    assert client.get(PREFIX + "/me").status_code == 401


@pytest.mark.parametrize("field", ["expires_at", "last_seen_at"])
def test_absolute_and_idle_session_expiry(tmp_path, field):
    app, _, client = make(tmp_path)
    login(client)
    with app.state.inventory._session() as db:
        db.execute(update(SubscriberSession).values({field: 0}))
        db.commit()
    assert client.get(PREFIX + "/me").status_code == 401


def test_origin_csrf_and_secret_validation_responses(tmp_path):
    _, _, client = make(tmp_path)
    body = {"username": "alice", "password": PASSWORD}
    assert client.post(PREFIX + "/login", json=body).status_code == 403
    assert (
        client.post(
            PREFIX + "/login",
            json=body,
            headers={"X-Open-Node-Client": "browser", "Origin": "null"},
        ).status_code
        == 403
    )
    login(client)
    csrf = client.headers.pop("X-CSRF-Token")
    for headers in (
        {},
        {"X-CSRF-Token": "wrong"},
        {"X-CSRF-Token": csrf, "Origin": "https://attacker.invalid"},
    ):
        assert client.post(PREFIX + "/subscription-token", headers=headers).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    response = client.post(
        PREFIX + "/password", json={"password": PASSWORD, "new_password": "too-short"}
    )
    assert response.status_code == 422
    assert "too-short" not in response.text and PASSWORD not in response.text
    for path in ("/me", "/security", "/sessions", "/session", "/unknown"):
        assert client.get(PREFIX + path).headers["cache-control"] == "no-store"


def test_password_reset_is_guarded_and_preserves_subscription_state(tmp_path):
    app, operator, client = make(tmp_path, catalog=True)
    login(client)
    before = client.get(PREFIX + "/me").json()
    token = client.post(PREFIX + "/subscription-token").json()
    status = operator.get(MANAGEMENT, params={"username": "alice"}).json()
    provision(operator, password=REPLACEMENT)
    assert (
        operator.put(
            MANAGEMENT,
            params={"username": "alice"},
            json={"expected_revision": status["revision"], "new_password": PASSWORD},
        ).status_code
        == 409
    )
    assert client.get(PREFIX + "/me").status_code == 401
    assert login(client).status_code == 401
    assert login(client, REPLACEMENT).status_code == 200
    assert client.get(PREFIX + "/me").json() == before
    assert client.post(PREFIX + "/subscription-token").json() == token
    assert operator.get(MANAGEMENT, params={"username": "missing"}).status_code == 404
    with app.state.inventory._session() as db:
        assert db.scalar(select(SubscriberChallenge)) is None


def test_password_change_and_stale_inflight_identity(tmp_path):
    app, _, client = make(tmp_path)
    login(client)
    stale = identity(app, client)
    second = TestClient(app, base_url="https://testserver")
    login(second)
    wrong = {"password": "wrong", "new_password": REPLACEMENT}
    assert client.post(PREFIX + "/password", json=wrong).status_code == 400
    assert client.get(PREFIX + "/me").status_code == 200
    assert (
        client.post(PREFIX + "/password", json={**wrong, "password": PASSWORD}).status_code == 204
    )
    assert second.get(PREFIX + "/me").status_code == 401
    with pytest.raises(SubscriberAuthenticationError):
        app.state.subscriber_auth.subscription_token(stale)
    assert login(client, REPLACEMENT).status_code == 200


@pytest.mark.parametrize("path", ["active", "management", "catalog"])
def test_disable_then_enable_never_revives_old_sessions(tmp_path, path):
    app, operator, client = make(tmp_path)
    login(client)
    if path == "active":
        assert (
            operator.patch("/api/v1/users/alice/active", json={"is_active": False}).status_code
            == 200
        )
    elif path == "management":
        before = operator.get("/api/v1/users/alice/settings").json()
        response = operator.put(
            "/api/v1/users/alice/settings",
            json={
                "expected_revision": before["revision"],
                "display_name": "Alice",
                "is_active": False,
                "acknowledge_runtime_restart": True,
            },
        )
        assert response.status_code == 200, response.text
    else:
        catalog = operator.get("/api/v1/catalog/export").json()["catalog"]
        catalog["users"][0]["is_active"] = False
        response = operator.post("/api/v1/catalog/import", json={"catalog": catalog})
        assert response.status_code == 200, response.text
    assert operator.patch("/api/v1/users/alice/active", json={"is_active": True}).status_code == 200
    assert client.get(PREFIX + "/me").status_code == 401
    assert login(client).status_code == 200
    assert identity(app, client)


def test_self_service_profile_links_formats_and_rotation_are_scoped(tmp_path):
    app, operator, client = make(tmp_path, catalog=True)
    operator.post("/api/v1/users", json={"username": "bob", "remark": "private administrator note"})
    provision(operator, "bob")
    login(client)
    profile = client.get(PREFIX + "/me").json()
    assert profile["quota"]["plan_name"] == "Premium"
    assert profile["quota"]["traffic_limit_bytes"] == 128 * 1024**3
    assert "remark" not in profile and "bob" not in str(profile)
    assert client.get(PREFIX + "/me?username=bob").json()["username"] == "alice"
    token = client.post(PREFIX + "/subscription-token").json()["subscription"]
    for client_format in ("clash", "sing-box", "xray", "uri-list", "base64"):
        response = client.get(token["subscription_url"], params={"format": client_format})
        assert response.status_code == 200, response.text
    assert (
        client.post(PREFIX + "/subscription-token/reset", json={"password": "wrong"}).status_code
        == 400
    )
    rotated = client.post(PREFIX + "/subscription-token/reset", json={"password": PASSWORD}).json()[
        "subscription"
    ]
    assert rotated["token"] != token["token"]
    assert client.get(token["short_url"]).status_code == 404
    assert client.get(token["subscription_url"]).status_code == 404
    assert client.get(rotated["short_url"]).status_code == 200
    with app.state.inventory._session() as db:
        db.get(ProductUserModel, "alice").plan_expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
    assert client.get(PREFIX + "/me").json()["quota"]["expired"] is True
    assert client.get(rotated["short_url"]).status_code == 404


def test_session_listing_revocation_and_other_account_isolation(tmp_path):
    app, operator, client = make(tmp_path)
    operator.post("/api/v1/users", json={"username": "bob"})
    provision(operator, "bob")
    login(client)
    another = TestClient(app, base_url="https://testserver")
    bob = TestClient(app, base_url="https://testserver")
    login(another)
    login(bob, username="bob")
    devices = client.get(PREFIX + "/sessions").json()
    assert len(devices) == 2 and sum(row["current"] for row in devices) == 1
    bob_id = bob.get(PREFIX + "/sessions").json()[0]["id"]
    assert client.delete(PREFIX + "/sessions/" + bob_id).status_code == 204
    assert bob.get(PREFIX + "/me").status_code == 200
    assert client.delete(PREFIX + "/sessions").status_code == 204
    assert another.get(PREFIX + "/me").status_code == 401
    current = client.get(PREFIX + "/sessions").json()[0]
    assert client.delete(PREFIX + "/sessions/" + current["id"]).status_code == 204
    assert client.get(PREFIX + "/me").status_code == 401


def test_totp_enrollment_encryption_replay_and_one_time_challenges(tmp_path, monkeypatch):
    app, operator, client = make(tmp_path)
    clock, secret, codes = enable(app, client, monkeypatch)
    with app.state.inventory._session() as db:
        account = db.get(SubscriberAccount, "alice")
        assert secret not in account.totp_secret
        assert all(code not in str(account.recovery_hashes) for code in codes)
        assert account.pending_secret is None
    assert len(codes) == len(set(codes)) == 10
    assert client.get(PREFIX + "/security").json()["totp_enabled"] is True
    fresh = TestClient(app, base_url="https://testserver")
    pending = login(fresh).json()
    assert pending["requires_2fa"] and not pending["authenticated"] and not fresh.cookies
    assert fresh.get(PREFIX + "/me").status_code == 401
    assert verify(fresh, pending["challenge"], pyotp.TOTP(secret).at(clock[0])).status_code == 401
    clock[0] += 30
    valid = verify(fresh, pending["challenge"], pyotp.TOTP(secret).at(clock[0]))
    assert valid.status_code == 200 and valid.json()["authenticated"]
    assert verify(fresh, pending["challenge"], codes[0]).status_code == 401
    assert fresh.get(PREFIX + "/me").status_code == 200
    provision(operator, password=REPLACEMENT)
    assert fresh.get(PREFIX + "/me").status_code == 401
    assert login(fresh, REPLACEMENT).json()["requires_2fa"]


def test_recovery_codes_are_single_use_and_do_not_disable_totp(tmp_path, monkeypatch):
    app, _, client = make(tmp_path)
    _, _, codes = enable(app, client, monkeypatch)
    fresh = TestClient(app, base_url="https://testserver")
    challenge = login(fresh).json()["challenge"]
    assert verify(fresh, challenge, codes[0]).status_code == 200
    assert fresh.get(PREFIX + "/security").json() == {
        "totp_enabled": True,
        "totp_available": True,
        "recovery_codes_remaining": 9,
    }
    challenge = login(fresh).json()["challenge"]
    assert verify(fresh, challenge, codes[0]).status_code == 401
    assert verify(fresh, challenge, codes[1]).status_code == 200


def test_enrollment_is_session_bound_and_expires(tmp_path, monkeypatch):
    app, _, client = make(tmp_path)
    clock = [1_800_000_000.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: clock[0]))
    login(client)
    another = TestClient(app, base_url="https://testserver")
    login(another)
    setup = client.post(PREFIX + "/totp/setup", json={"password": PASSWORD}).json()
    code = pyotp.TOTP(setup["secret"]).at(clock[0])
    assert another.post(PREFIX + "/totp/confirm", json={"code": code}).status_code == 409
    clock[0] += 601
    code = pyotp.TOTP(setup["secret"]).at(clock[0])
    assert client.post(PREFIX + "/totp/confirm", json={"code": code}).status_code == 409
    assert client.get(PREFIX + "/security").json()["totp_enabled"] is False


@pytest.mark.parametrize(
    "action", ["/password", "/totp/disable", "/totp/recovery-codes", "/subscription-token/reset"]
)
def test_sensitive_actions_require_password_and_current_factor(tmp_path, monkeypatch, action):
    app, _, client = make(tmp_path)
    _, _, codes = enable(app, client, monkeypatch)
    payload = {"password": PASSWORD, "new_password": REPLACEMENT}
    assert client.post(PREFIX + action, json=payload).status_code == 400
    response = client.post(PREFIX + action, json={**payload, "code": codes[0]})
    assert response.status_code in {200, 204}, response.text
    if action == "/totp/recovery-codes":
        assert not set(response.json()["recovery_codes"]).intersection(codes)
    if action == "/totp/disable":
        assert client.get(PREFIX + "/security").json()["totp_enabled"] is False
    if action == "/password":
        assert client.get(PREFIX + "/me").status_code == 401


def test_missing_or_wrong_encryption_key_fails_closed_but_recovery_works(tmp_path, monkeypatch):
    app, _, client = make(tmp_path)
    clock, secret, codes = enable(app, client, monkeypatch)
    clock[0] += 30
    app.state.subscriber_auth.cipher = None
    fresh = TestClient(app, base_url="https://testserver")
    challenge = login(fresh).json()["challenge"]
    assert verify(fresh, challenge, pyotp.TOTP(secret).at(clock[0])).status_code == 503
    app.state.subscriber_auth.cipher = Fernet(Fernet.generate_key())
    assert verify(fresh, challenge, pyotp.TOTP(secret).at(clock[0])).status_code == 503
    assert verify(fresh, challenge, codes[0]).status_code == 200


def test_missing_key_prevents_enrollment_not_password_login(tmp_path):
    assert Settings(subscriber_totp_key="").subscriber_totp_key is None
    _, _, client = make(tmp_path, key=False)
    assert login(client).status_code == 200
    assert client.get(PREFIX + "/security").json()["totp_available"] is False
    assert client.post(PREFIX + "/totp/setup", json={"password": PASSWORD}).status_code == 503
    with pytest.raises(ValueError, match="Fernet"):
        Settings(subscriber_totp_key="not-a-valid-encryption-key")


def test_challenge_attempt_budget_expiry_and_atomic_consumption(tmp_path, monkeypatch):
    app, _, client = make(tmp_path)
    clock, secret, _ = enable(app, client, monkeypatch)
    service = app.state.subscriber_auth
    _, _, challenge = service.login("alice", PASSWORD, "test", "test")
    for _ in range(5):
        with pytest.raises(SubscriberAuthenticationError):
            service.complete_login(challenge, "invalid", "test", "test")
    clock[0] += 30
    code = pyotp.TOTP(secret).at(clock[0])
    with pytest.raises(SubscriberAuthenticationError):
        service.complete_login(challenge, code, "test", "test")
    _, _, challenge = service.login("alice", PASSWORD, "test", "test")
    clock[0] += 301
    with pytest.raises(SubscriberAuthenticationError):
        service.complete_login(challenge, pyotp.TOTP(secret).at(clock[0]), "test", "test")
    _, _, challenge = service.login("alice", PASSWORD, "test", "test")

    def complete(_):
        try:
            return (
                service.complete_login(challenge, pyotp.TOTP(secret).at(clock[0]), "test", "test")
                is not None
            )
        except SubscriberAuthenticationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(complete, range(2))) == 1


def test_login_throttle_persists_across_workers_and_ignores_forwarded_ip(tmp_path):
    app, _, client = make(tmp_path)
    for index in range(10):
        response = client.post(
            PREFIX + "/login",
            json={"username": "alice", "password": "wrong"},
            headers={"X-Open-Node-Client": "browser", "X-Forwarded-For": f"192.0.2.{index}"},
        )
        assert response.status_code == 401
    restarted = TestClient(create_app(app.state.settings), base_url="https://testserver")
    response = login(restarted)
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_reset_cannot_race_an_old_password_login(tmp_path, monkeypatch):
    app, _, _ = make(tmp_path)
    service = app.state.subscriber_auth
    verified, resume = Event(), Event()
    original = module.password_hash.verify_and_update

    def paused(password, hashed):
        result = original(password, hashed)
        verified.set()
        assert resume.wait(10)
        return result

    monkeypatch.setattr(module.password_hash, "verify_and_update", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.login, "alice", PASSWORD, "test", "test")
        try:
            assert verified.wait(10)
            service.set_password(
                "alice",
                SubscriberAccountUpdate(
                    expected_revision=service.management("alice").revision, new_password=REPLACEMENT
                ),
            )
        finally:
            resume.set()
        with pytest.raises(SubscriberAuthenticationError):
            pending.result(10)


@pytest.mark.parametrize("username", ["group/alice@example.com", "group/\u7528\u6237@example.com"])
def test_slashes_unicode_and_control_plane_names_are_not_privileges(tmp_path, username):
    _, operator, client = make(tmp_path, username=username, role="admin")
    assert login(client, username=username).status_code == 200
    assert client.get(PREFIX + "/me").json()["username"] == username
    assert client.get("/api/v1/servers").status_code == 401
    assert operator.get(MANAGEMENT, params={"username": username}).json()["configured"]


def test_enabling_totp_revokes_other_sessions_and_explicit_admin_recovery(tmp_path, monkeypatch):
    app, operator, client = make(tmp_path)
    other = TestClient(app, base_url="https://testserver")
    login(other)
    _, _, _ = enable(app, client, monkeypatch)
    assert other.get(PREFIX + "/me").status_code == 401
    challenge = login(other).json()["challenge"]
    provision(operator, password=REPLACEMENT, reset_totp=True)
    assert verify(other, challenge, "000000").status_code == 401
    assert client.get(PREFIX + "/me").status_code == 401
    assert login(other, REPLACEMENT).json()["authenticated"] is True
    assert other.get(PREFIX + "/security").json()["recovery_codes_remaining"] == 0


def test_removal_cascades_login_state_and_recreation_requires_new_password(tmp_path, monkeypatch):
    app, operator, client = make(tmp_path)
    enable(app, client, monkeypatch)
    detail = operator.get("/api/v1/users/alice/settings").json()
    response = operator.post(
        "/api/v1/users/alice/remove",
        json={
            "expected_revision": detail["revision"],
            "confirm_name": "alice",
            "acknowledge_runtime_restart": True,
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "completed"
    with app.state.inventory._session() as db:
        for model in (SubscriberAccount, SubscriberSession, SubscriberChallenge):
            assert db.scalar(select(model)) is None
    assert operator.post("/api/v1/users", json={"username": "alice"}).status_code == 201
    assert client.get(PREFIX + "/me").status_code == 401
    assert login(client).status_code == 401
    provision(operator, password=REPLACEMENT)
    assert login(client, REPLACEMENT).json()["authenticated"]


def test_no_more_than_twenty_active_sessions_and_concurrent_token_creation(tmp_path):
    app, _, client = make(tmp_path)
    service = app.state.subscriber_auth
    issued = [service.login("alice", PASSWORD, "test", "test") for _ in range(21)]
    assert service.authenticate(issued[0][0]) is None
    assert service.authenticate(issued[-1][0]) is not None
    with app.state.inventory._session() as db:
        assert len(db.scalars(select(SubscriberSession)).all()) == 20
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: service.subscription_token(issued[-1][1]), range(4)))
    assert len({item.token for item in tokens}) == 1


def test_used_totp_counter_survives_restart(tmp_path, monkeypatch):
    app, _, client = make(tmp_path)
    clock, secret, _ = enable(app, client, monkeypatch)
    restarted = create_app(app.state.settings).state.subscriber_auth
    _, _, challenge = restarted.login("alice", PASSWORD, "test", "test")
    with pytest.raises(SubscriberAuthenticationError):
        restarted.complete_login(challenge, pyotp.TOTP(secret).at(clock[0]), "test", "test")
    clock[0] += 30
    assert restarted.complete_login(challenge, pyotp.TOTP(secret).at(clock[0]), "test", "test")


def test_concurrent_recovery_code_consumption_is_atomic(tmp_path, monkeypatch):
    app, _, client = make(tmp_path)
    _, _, codes = enable(app, client, monkeypatch)
    service = app.state.subscriber_auth
    signed_in = identity(app, client)
    proof = SubscriberProof(password=PASSWORD, code=codes[0])

    def rotate(_):
        try:
            service.subscription_token(signed_in, proof)
            return True
        except SubscriberAuthenticationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(rotate, range(2))) == 1
    assert service.security(signed_in).recovery_codes_remaining == 9
