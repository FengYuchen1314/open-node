import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event

import pyotp
import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services import auth
from open_node.services.auth import (
    Administrator,
    AdministratorAuthenticationError,
    AdministratorFactor,
    AdministratorFactorUnavailable,
    AdministratorSecurityConflict,
    AdministratorSecurityPolicy,
    OperatorChallenge,
    OperatorSession,
)
from sqlalchemy import select, update


def make_app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'auth.db'}"))


def login(client, password=ADMIN_PASSWORD, username="admin", **kwargs):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"X-Open-Node-Client": "browser", **kwargs},
    )


def test_unconfigured_installation_fails_closed(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/session")
        assert response.json() == {
            "configured": False,
            "authenticated": False,
            "username": None,
            "csrf_token": None,
        }
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/servers").status_code == 401
        assert client.post("/api/v1/servers", json={"name": "unauthorized"}).status_code == 401
        assert login(client).status_code == 401
        assert not app.state.inventory.list_servers()


def test_every_management_route_requires_an_operator(tmp_path: Path):
    app = make_app(tmp_path)
    client = TestClient(app)
    checked = 0
    public_http = {
        ("get", "/api/v1/auth/session"),
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/login/verify"),
        ("get", "/api/v1/account/session"),
        ("post", "/api/v1/account/login"),
        ("post", "/api/v1/account/login/verify"),
        ("post", "/api/v1/account/register"),
        ("get", "/api/v1/subscribe/{subscription_key}"),
        ("get", "/x/{code}"),
        ("get", "/t/{code}"),
        ("get", "/api/v1/license/status"),
        ("get", "/api/v1/healthz"),
        ("get", "/api/v1/meta"),
        *{
            ("post", f"/api/v1/agents/{path}")
            for path in [
                "register",
                "heartbeat",
                "traffic",
                "telemetry",
                "scan",
                "commands/lease",
                "commands/{command_id}/result",
                "commands/by-request/{request_id}/result",
            ]
        },
        *{
            ("get", f"{prefix}/{path}")
            for prefix in ["/api/v1/public", "/api/public"]
            for path in ["probe-servers", "probe-settings", "probe-series", "probe-targets"]
        },
    }
    for path, methods in app.openapi()["paths"].items():
        for method in methods:
            if (method, path) in public_http:
                continue
            response = client.request(method, path)
            assert response.status_code == 401, (method, path, response.text)
            checked += 1
    assert checked > 70
    assert client.put("/api/public/probe-settings", json={}).status_code == 401


def test_login_session_is_private_persistent_and_revocable(tmp_path: Path):
    app = make_app(tmp_path)
    client = authenticated_client(app)
    cookie = client.cookies.get("open_node_session")
    with app.state.auth.session() as db:
        admin = db.get(Administrator, 1)
        assert admin.password_hash.startswith("$argon2id$")
        assert ADMIN_PASSWORD not in admin.password_hash
        stored = db.scalar(select(OperatorSession))
        assert stored.token_hash == sha256(cookie.encode()).hexdigest()
    session = client.get("/api/v1/auth/session")
    assert session.json()["authenticated"] is True
    assert session.json()["username"] == "admin"
    assert cookie not in session.text
    assert client.get("/api/v1/servers").headers["cache-control"] == "no-store"
    restarted = TestClient(make_app(tmp_path), base_url="https://testserver")
    restarted.cookies.update(client.cookies)
    assert restarted.get("/api/v1/servers").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert restarted.get("/api/v1/servers").status_code == 401
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_login_rotates_session_and_sets_cookie_flags(tmp_path: Path):
    app = make_app(tmp_path)
    client = authenticated_client(app)
    old = client.cookies.get("open_node_session")
    response = login(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    for flag in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"]:
        assert flag in cookie
    assert client.cookies.get("open_node_session") != old
    assert app.state.auth.authenticate(old, 1800) is None


def test_administrator_totp_enrollment_login_and_recovery_are_private(tmp_path: Path):
    key = Fernet.generate_key().decode()
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'auth.db'}",
            subscriber_totp_key=key,
        )
    )
    client = authenticated_client(app)
    other = authenticated_client(app)
    security = client.get("/api/v1/auth/security")
    assert security.json() == {
        "totp_enabled": False,
        "totp_available": True,
        "recovery_codes_remaining": 0,
        "require_totp": False,
    }

    setup = client.post(
        "/api/v1/auth/totp/setup", json={"password": ADMIN_PASSWORD, "code": ""}
    )
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    with app.state.auth.session() as db:
        factor = db.get(AdministratorFactor, 1)
        assert factor.pending_secret and secret not in factor.pending_secret
        assert db.scalar(select(OperatorChallenge)) is None
    confirmed = client.post(
        "/api/v1/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()}
    )
    assert confirmed.status_code == 200
    recovery_codes = confirmed.json()["recovery_codes"]
    assert len(recovery_codes) == len(set(recovery_codes)) == 10
    assert other.get("/api/v1/servers").status_code == 401
    assert client.get("/api/v1/auth/security").json()["totp_enabled"] is True

    assert client.post("/api/v1/auth/logout").status_code == 204
    challenged = login(client)
    assert challenged.status_code == 200
    assert challenged.json()["authenticated"] is False
    assert challenged.json()["requires_2fa"] is True
    assert challenged.json()["enrollment_required"] is False
    assert client.get("/api/v1/servers").status_code == 401
    challenge = challenged.json()["challenge"]
    invalid = client.post(
        "/api/v1/auth/login/verify",
        json={"challenge": challenge, "code": "000000"},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert invalid.status_code == 401
    verified = client.post(
        "/api/v1/auth/login/verify",
        json={"challenge": challenge, "code": recovery_codes[0]},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert verified.status_code == 200
    assert verified.json()["authenticated"] is True
    assert client.get("/api/v1/servers").status_code == 200

    client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]
    assert client.post("/api/v1/auth/logout").status_code == 204
    replay_challenge = login(client).json()["challenge"]
    replay = client.post(
        "/api/v1/auth/login/verify",
        json={"challenge": replay_challenge, "code": recovery_codes[0]},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert replay.status_code == 401


def test_required_administrator_totp_enrolls_before_session_issue(tmp_path: Path):
    key = Fernet.generate_key().decode()
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'auth.db'}",
            subscriber_totp_key=key,
        )
    )
    app.state.auth.set_administrator("admin", ADMIN_PASSWORD)
    with app.state.auth.session.begin() as db:
        db.add(AdministratorSecurityPolicy(id=1, require_totp=True))
    client = TestClient(app, base_url="https://testserver")

    challenged = login(client)
    body = challenged.json()
    assert body["authenticated"] is False
    assert body["requires_2fa"] is True
    assert body["enrollment_required"] is True
    assert body["enrollment"]["secret"]
    assert client.cookies.get("open_node_session") is None
    with app.state.auth.session() as db:
        row = db.scalar(select(OperatorChallenge))
        assert row.kind == "enroll"
        assert body["enrollment"]["secret"] not in row.pending_secret

    verified = client.post(
        "/api/v1/auth/login/verify",
        json={
            "challenge": body["challenge"],
            "code": pyotp.TOTP(body["enrollment"]["secret"]).now(),
        },
        headers={"X-Open-Node-Client": "browser"},
    )
    assert verified.status_code == 200
    assert verified.json()["authenticated"] is True
    codes = verified.json()["recovery_codes"]
    assert len(codes) == 10
    client.headers["X-CSRF-Token"] = verified.json()["csrf_token"]

    blocked = client.post(
        "/api/v1/auth/totp/disable",
        json={"password": ADMIN_PASSWORD, "code": codes[0]},
    )
    assert blocked.status_code == 409
    optional = client.put(
        "/api/v1/auth/security/policy",
        json={"required": False, "password": ADMIN_PASSWORD, "code": codes[0]},
    )
    assert optional.status_code == 200
    assert optional.json()["require_totp"] is False
    disabled = client.post(
        "/api/v1/auth/totp/disable",
        json={"password": ADMIN_PASSWORD, "code": codes[1]},
    )
    assert disabled.status_code == 204
    assert client.get("/api/v1/auth/security").json()["totp_enabled"] is False


def test_administrator_factor_challenge_and_totp_are_not_replayable(tmp_path: Path, monkeypatch):
    fixed_time = 1_800_000_000
    monkeypatch.setattr(auth, "time", lambda: fixed_time)
    key = Fernet.generate_key().decode()
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'auth.db'}",
            subscriber_totp_key=key,
        )
    )
    store = app.state.auth
    store.set_administrator("admin", ADMIN_PASSWORD)
    first = store.login("admin", ADMIN_PASSWORD, 43200)
    enrollment = store.begin_totp(first.token, ADMIN_PASSWORD)
    codes = store.confirm_totp(first.token, pyotp.TOTP(enrollment.secret).at(fixed_time))
    challenge = store.login("admin", ADMIN_PASSWORD, 43200).challenge
    with pytest.raises(AdministratorAuthenticationError, match="Invalid verification code"):
        store.complete_login(challenge, pyotp.TOTP(enrollment.secret).at(fixed_time), 43200)

    restarted = auth.AuthStore(f"sqlite:///{tmp_path / 'auth.db'}", key)

    def complete(target):
        try:
            return target.complete_login(challenge, codes[0], 43200)
        except AdministratorAuthenticationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, (store, restarted)))
    assert sum(result is not None for result in results) == 1
    assert sum(result is not None and result.token is not None for result in results) == 1

    expired = store.login("admin", ADMIN_PASSWORD, 43200).challenge
    with store.session.begin() as db:
        db.execute(update(OperatorChallenge).values(expires_at=0))
    with pytest.raises(AdministratorAuthenticationError, match="expired"):
        store.complete_login(expired, codes[1], 43200)


def test_administrator_enrollment_requires_key_and_policy_requires_factor(tmp_path: Path):
    app = make_app(tmp_path)
    client = authenticated_client(app)
    assert client.get("/api/v1/auth/security").json()["totp_available"] is False
    unavailable = client.post(
        "/api/v1/auth/totp/setup", json={"password": ADMIN_PASSWORD, "code": ""}
    )
    assert unavailable.status_code == 503
    policy = client.put(
        "/api/v1/auth/security/policy",
        json={"required": True, "password": ADMIN_PASSWORD, "code": ""},
    )
    assert policy.status_code == 409
    assert client.get("/api/v1/auth/security").json()["require_totp"] is False


def test_administrator_challenge_attempt_limit_persists_across_restart(tmp_path: Path):
    key = Fernet.generate_key().decode()
    url = f"sqlite:///{tmp_path / 'auth.db'}"
    store = auth.AuthStore(url, key)
    store.set_administrator("admin", ADMIN_PASSWORD)
    session = store.login("admin", ADMIN_PASSWORD, 43200)
    enrollment = store.begin_totp(session.token, ADMIN_PASSWORD)
    codes = store.confirm_totp(session.token, pyotp.TOTP(enrollment.secret).now())
    challenge = store.login("admin", ADMIN_PASSWORD, 43200).challenge
    for _ in range(5):
        with pytest.raises(AdministratorAuthenticationError, match="Invalid verification code"):
            store.complete_login(challenge, "invalid-code", 43200)
    restarted = auth.AuthStore(url, key)
    with pytest.raises(AdministratorAuthenticationError, match="expired"):
        restarted.complete_login(challenge, codes[0], 43200)
    fresh = restarted.login("admin", ADMIN_PASSWORD, 43200).challenge
    assert restarted.complete_login(fresh, codes[0], 43200).token


def test_administrator_factor_budget_survives_new_challenges_and_ips(tmp_path: Path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(auth, "time", lambda: now)
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'auth.db'}",
            subscriber_totp_key=Fernet.generate_key().decode(),
        )
    )
    store = app.state.auth
    store.set_administrator("admin", ADMIN_PASSWORD)
    session = store.login("admin", ADMIN_PASSWORD, 43200)
    enrollment = store.begin_totp(session.token, ADMIN_PASSWORD)
    codes = store.confirm_totp(session.token, pyotp.TOTP(enrollment.secret).at(now))
    for _ in range(12):
        with pytest.raises(AdministratorAuthenticationError, match="expired"):
            store.complete_login("random-unknown-challenge", "123456", 43200)
    for attempt in range(11):
        client = TestClient(app, base_url="https://testserver", client=(f"192.0.2.{attempt + 1}", 1))
        challenge = login(client).json()["challenge"]
        response = client.post(
            "/api/v1/auth/login/verify",
            json={"challenge": challenge, "code": "invalid" if attempt < 10 else codes[0]},
            headers={"X-Open-Node-Client": "browser"},
        )
        assert response.status_code == (401 if attempt < 10 else 429)
        assert client.cookies.get("open_node_session") is None
    assert response.headers["retry-after"] == "60"
    now += 61
    resumed = store.complete_login(challenge, codes[0], 43200)
    assert resumed.token
    assert store.security().recovery_codes_remaining == 9


@pytest.mark.parametrize("key_state", ["missing", "replaced"])
def test_administrator_recovery_survives_encryption_key_loss(tmp_path: Path, key_state: str):
    url = f"sqlite:///{tmp_path / 'auth.db'}"
    store = auth.AuthStore(url, Fernet.generate_key().decode())
    store.set_administrator("admin", ADMIN_PASSWORD)
    session = store.login("admin", ADMIN_PASSWORD, 43200)
    enrollment = store.begin_totp(session.token, ADMIN_PASSWORD)
    codes = store.confirm_totp(session.token, pyotp.TOTP(enrollment.secret).now())
    restarted = auth.AuthStore(
        url, None if key_state == "missing" else Fernet.generate_key().decode()
    )
    challenge = restarted.login("admin", ADMIN_PASSWORD, 43200).challenge
    with pytest.raises(AdministratorFactorUnavailable):
        restarted.complete_login(challenge, pyotp.TOTP(enrollment.secret).now(), 43200)
    recovered = restarted.complete_login(challenge, codes[0], 43200)
    assert recovered.token
    assert restarted.security().recovery_codes_remaining == 9
    restarted.update_totp(recovered.token, ADMIN_PASSWORD, codes[1], disable=True)
    assert restarted.security().totp_enabled is False


def test_administrator_pending_enrollment_is_session_bound_and_expires(tmp_path: Path):
    store = auth.AuthStore(f"sqlite:///{tmp_path / 'auth.db'}", Fernet.generate_key().decode())
    store.set_administrator("admin", ADMIN_PASSWORD)
    first = store.login("admin", ADMIN_PASSWORD, 43200)
    second = store.login("admin", ADMIN_PASSWORD, 43200)
    enrollment = store.begin_totp(first.token, ADMIN_PASSWORD)
    code = pyotp.TOTP(enrollment.secret).now()
    with pytest.raises(AdministratorSecurityConflict, match="expired"):
        store.confirm_totp(second.token, code)
    with store.session.begin() as db:
        db.execute(update(AdministratorFactor).values(pending_expires_at=0))
    with pytest.raises(AdministratorSecurityConflict, match="expired"):
        store.confirm_totp(first.token, code)
    assert store.security().totp_enabled is False


@pytest.mark.parametrize("local_reset", [False, True])
def test_administrator_password_changes_invalidate_factor_challenges(
    tmp_path: Path, local_reset: bool
):
    store = auth.AuthStore(f"sqlite:///{tmp_path / 'auth.db'}", Fernet.generate_key().decode())
    store.set_administrator("admin", ADMIN_PASSWORD)
    session = store.login("admin", ADMIN_PASSWORD, 43200)
    enrollment = store.begin_totp(session.token, ADMIN_PASSWORD)
    codes = store.confirm_totp(session.token, pyotp.TOTP(enrollment.secret).now())
    challenge = store.login("admin", ADMIN_PASSWORD, 43200).challenge
    if local_reset:
        store.set_administrator("admin", "replacement-password", reset=True)
    else:
        assert store.change_password(ADMIN_PASSWORD, "replacement-password")
    with pytest.raises(AdministratorAuthenticationError, match="expired"):
        store.complete_login(challenge, codes[0], 43200)
    assert store.authenticate(session.token, 1800) is None
    assert store.security().totp_enabled is not local_reset


def test_auth_validation_does_not_echo_password_or_factor(tmp_path: Path):
    client = TestClient(make_app(tmp_path))
    secret = "private-factor-" * 10
    invalid = client.post(
        "/api/v1/auth/login/verify",
        json={"challenge": "private-challenge", "code": secret},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    assert "private-challenge" not in invalid.text
    assert invalid.headers["cache-control"] == "no-store"
    assert invalid.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("column", ["expires_at", "last_seen_at"])
def test_session_absolute_and_idle_expiry(tmp_path: Path, column: str):
    app = make_app(tmp_path)
    client = authenticated_client(app)
    with app.state.auth.session.begin() as db:
        db.execute(update(OperatorSession).values({column: 0}))
    assert client.get("/api/v1/servers").status_code == 401
    assert client.get("/api/v1/auth/session").json()["authenticated"] is False


def test_csrf_and_origin_validation(tmp_path: Path):
    app = make_app(tmp_path)
    client = authenticated_client(app)
    csrf = client.headers.pop("X-CSRF-Token")
    path = "/api/v1/servers"
    assert client.post(path, json={"name": "bad"}).status_code == 403
    assert (
        client.post(path, json={"name": "bad"}, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    headers = {"X-CSRF-Token": csrf, "Origin": "https://attacker.invalid"}
    assert client.post(path, json={"name": "bad"}, headers=headers).status_code == 403
    headers["Origin"] = "https://testserver"
    assert client.post(path, json={"name": "good"}, headers=headers).status_code == 201
    assert login(client, Origin="null").status_code == 403
    assert login(client, Origin="https://attacker.invalid").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
        ).status_code
        == 403
    )


def test_password_change_revokes_all_sessions(tmp_path: Path):
    app = make_app(tmp_path)
    first = authenticated_client(app)
    second = authenticated_client(app)
    invalid = first.post(
        "/api/v1/auth/password",
        json={"current_password": "wrong", "new_password": "new-long-password"},
    )
    assert invalid.status_code == 400
    assert second.get("/api/v1/servers").status_code == 200
    valid = first.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "new-long-password"},
    )
    assert valid.status_code == 204
    assert first.get("/api/v1/servers").status_code == 401
    assert second.get("/api/v1/servers").status_code == 401
    assert login(second).status_code == 401
    assert login(second, "new-long-password").status_code == 200


def test_login_rate_limit_is_persistent_and_atomic(tmp_path: Path):
    app = make_app(tmp_path)
    client = TestClient(app)
    for _ in range(10):
        assert login(client, "wrong").status_code == 401
    restarted = TestClient(make_app(tmp_path))
    limited = login(restarted)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(
            pool.map(lambda _: app.state.auth.allow_login_attempt("another-peer"), range(20))
        )
    assert sum(accepted) == 10


def test_public_endpoints_and_agent_tokens_remain_separate(tmp_path: Path):
    app = make_app(tmp_path)
    operator = authenticated_client(app)
    created = operator.post("/api/v1/servers", json={"name": "edge"}).json()
    public = TestClient(app)
    for path in [
        "/healthz",
        "/api/v1/license/status",
        "/api/v1/public/probe-servers",
        "/api/public/probe-settings",
    ]:
        assert public.get(path).status_code == 200
    assert public.get("/api/v1/subscribe/missing").status_code == 404
    assert (
        public.post(
            "/api/v1/agents/commands/lease", json={"token": created["agent_token"]}
        ).status_code
        == 200
    )
    assert (
        public.get(
            "/api/v1/servers", headers={"Authorization": f"Bearer {created['agent_token']}"}
        ).status_code
        == 401
    )
    assert (
        public.post(
            "/api/v1/agents/commands/lease",
            json={"token": operator.cookies.get("open_node_session")},
        ).status_code
        == 401
    )


def test_cli_create_and_reset_without_exposing_password(tmp_path: Path):
    app = make_app(tmp_path)
    env = {**os.environ, "OPEN_NODE_DATABASE_URL": f"sqlite:///{tmp_path / 'auth.db'}"}

    def invoke(action, password):
        return subprocess.run(
            [sys.executable, "-m", "open_node.admin", action, "--password-stdin"],
            input=password + "\n",
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )

    weak = invoke("create", "short")
    assert weak.returncode == 1
    assert "short" not in weak.stderr
    assert invoke("create", ADMIN_PASSWORD).returncode == 0
    assert invoke("create", ADMIN_PASSWORD).returncode == 1
    client = authenticated_client(app)
    with app.state.auth.session.begin() as db:
        db.add(AdministratorFactor(administrator_id=1, totp_secret="encrypted"))
        db.add(AdministratorSecurityPolicy(id=1, require_totp=True))
    for _ in range(10):
        app.state.auth.allow_login_attempt("testclient")
    assert login(client).status_code == 429
    reset = invoke("reset-password", "replacement-password")
    assert reset.returncode == 0
    assert "two-factor settings were cleared" in reset.stdout
    assert client.get("/api/v1/servers").status_code == 401
    reset_login = login(client, "replacement-password")
    assert reset_login.status_code == 200
    assert reset_login.json()["authenticated"] is True
    with app.state.auth.session() as db:
        assert db.get(AdministratorFactor, 1) is None
        assert db.get(AdministratorSecurityPolicy, 1).require_totp is False


def test_wildcard_cors_is_rejected():
    with pytest.raises(ValueError, match="explicit origins"):
        Settings(cors_origins=["*"])


def test_password_reset_prevents_inflight_old_password_login(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    app.state.auth.set_administrator("admin", ADMIN_PASSWORD)
    verified, resume = Event(), Event()
    original_verify = auth.password_hash.verify

    def paused_verify(password, hashed):
        result = original_verify(password, hashed)
        verified.set()
        assert resume.wait(timeout=10)
        return result

    monkeypatch.setattr(auth.password_hash, "verify", paused_verify)
    with ThreadPoolExecutor(max_workers=1) as pool:
        login_result = pool.submit(app.state.auth.login, "admin", ADMIN_PASSWORD, 43200)
        try:
            assert verified.wait(timeout=10)
            app.state.auth.set_administrator("admin", "replacement-password", reset=True)
        finally:
            resume.set()
        assert login_result.result(timeout=10) is None
    assert app.state.auth.login("admin", "replacement-password", 43200) is not None
