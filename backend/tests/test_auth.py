import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services import auth
from open_node.services.auth import Administrator, OperatorSession
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
        ("get", "/api/v1/account/session"),
        ("post", "/api/v1/account/login"),
        ("post", "/api/v1/account/login/verify"),
        ("get", "/api/v1/subscribe/{subscription_key}"),
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
    assert invoke("reset-password", "replacement-password").returncode == 0
    assert client.get("/api/v1/servers").status_code == 401
    assert login(client, "replacement-password").status_code == 200


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
