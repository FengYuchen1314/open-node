"""Official-compatible security events and subscription-probe IP bans."""

from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app

SECURITY = "/api/v1/security"


def make(tmp_path):
    app = create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'security.db'}",
        certificate_state_dir=tmp_path / "certificates",
        _env_file=None,
    ))
    return app, authenticated_client(app)


def test_settings_are_revision_bound_and_strict(tmp_path):
    _app, operator = make(tmp_path)
    current = operator.get(SECURITY + "/settings")
    assert current.status_code == 200
    assert current.json() == {
        "revision": 0,
        "brute_force_enabled": True,
        "brute_force_max_failures": 5,
        "brute_force_window_minutes": 1440,
        "brute_force_block_minutes": 1440,
        "skip_local_ip": True,
        "license_required": False,
    }
    payload = {
        "expected_revision": 0,
        "brute_force_enabled": True,
        "brute_force_max_failures": 3,
        "brute_force_window_minutes": 30,
        "brute_force_block_minutes": 60,
        "skip_local_ip": False,
    }
    saved = operator.put(SECURITY + "/settings", json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["brute_force_max_failures"] == 3
    conflict = operator.put(SECURITY + "/settings", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "security_revision_conflict"
    invalid = operator.put(SECURITY + "/settings", json=payload | {"PRIVATE": "secret"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "security_invalid_request"
    assert "PRIVATE" not in invalid.text and "secret" not in invalid.text


def test_manual_ban_and_unban_keep_append_only_history(tmp_path):
    _app, operator = make(tmp_path)
    created = operator.post(
        SECURITY + "/bans", json={"ip": "2001:4860:4860::8888", "permanent": True},
    )
    assert created.status_code == 201
    assert created.json() == {
        "ip": "2001:4860:4860::8888",
        "reason": "manual",
        "banned_at": created.json()["banned_at"],
        "expires_at": None,
        "permanent": True,
        "fail_count": 0,
        "actor": "admin",
    }
    assert operator.get(SECURITY + "/bans").json()["bans"] == [created.json()]
    removed = operator.delete(SECURITY + "/bans/2001:4860:4860::8888")
    assert removed.status_code == 204 and removed.content == b""
    assert operator.get(SECURITY + "/bans").json()["bans"] == []
    history = operator.get(SECURITY + "/events").json()["events"]
    assert [event["kind"] for event in history] == ["unban", "ban_manual"]
    assert all(event["actor"] == "admin" for event in history)
    missing = operator.delete(SECURITY + "/bans/2001:4860:4860::8888")
    assert missing.status_code == 404
    assert missing.json()["code"] == "security_ban_not_found"


def test_invalid_ip_filters_and_paths_return_fixed_errors(tmp_path):
    _app, operator = make(tmp_path)
    filtered = operator.get(SECURITY + "/events", params={"ip": "not-an-ip"})
    assert filtered.status_code == 422
    assert filtered.json()["code"] == "security_invalid_request"
    invalid_path = operator.delete(SECURITY + "/bans/not-an-ip")
    assert invalid_path.status_code == 422
    assert invalid_path.json()["code"] == "security_invalid_request"


def test_unknown_public_subscription_is_banned_without_logging_token(tmp_path):
    app, operator = make(tmp_path)
    remote = TestClient(
        app, base_url="https://testserver", client=("1.1.1.1", 51000),
    )
    private_token = "PRIVATE-SUBSCRIPTION-TOKEN"
    for _ in range(5):
        response = remote.get(f"/api/v1/subscribe/{private_token}")
        assert response.status_code == 404
    bans = operator.get(SECURITY + "/bans").json()["bans"]
    assert len(bans) == 1
    assert bans[0]["ip"] == "1.1.1.1"
    assert bans[0]["reason"] == "brute_force" and bans[0]["fail_count"] == 5
    events = operator.get(SECURITY + "/events", params={"ip": "1.1.1.1"}).json()["events"]
    assert [event["kind"] for event in events] == ["ban", "probe", "probe", "probe", "probe"]
    assert all(event["path"] == "/api/v1/subscribe/{key}" for event in events)
    assert private_token not in str(events)
    before = len(events)
    assert remote.get(f"/api/v1/subscribe/{private_token}").status_code == 404
    after = operator.get(SECURITY + "/events", params={"ip": "1.1.1.1"}).json()["events"]
    assert len(after) == before


def test_login_failures_and_locks_use_fixed_routes(tmp_path):
    app, operator = make(tmp_path)
    remote = TestClient(
        app, base_url="https://testserver", client=("8.8.8.8", 52000),
    )
    failed = remote.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD + "-wrong"},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert failed.status_code == 401
    events = operator.get(
        SECURITY + "/events", params={"kind": "login_fail", "ip": "8.8.8.8"},
    ).json()["events"]
    assert len(events) == 1
    assert events[0]["username"] == "admin"
    assert events[0]["path"] == "/api/v1/auth/login"
    assert ADMIN_PASSWORD not in str(events)


def test_security_routes_require_administrator_and_never_cache(tmp_path):
    app, operator = make(tmp_path)
    anonymous = TestClient(app, base_url="https://testserver")
    for path in ("/settings", "/bans", "/events"):
        assert anonymous.get(SECURITY + path).status_code == 401
        response = operator.get(SECURITY + path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
