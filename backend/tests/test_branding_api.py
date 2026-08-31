import json

import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.branding import BrandingError
from open_node.main import create_app
from open_node.services.branding import BrandingStore
from sqlalchemy import text
from test_subscriber_auth import login as subscriber_login
from test_subscriber_auth import make as make_subscriber

PUBLIC = "/api/v1/branding"
PRIVATE = "/api/v1/system-settings/branding"
SECRET = "branding-invalid-input-not-for-echo"


def make_app(tmp_path, **settings):
    return create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'branding.db'}",
            certificate_state_dir=tmp_path / "certificates",
            **settings,
        )
    )


@pytest.fixture
def client(tmp_path):
    return authenticated_client(make_app(tmp_path))


def payload(**changes):
    return {"expected_revision": 0, "site_title": "示例站点", "brand_title": "示例", **changes}


def assert_safe(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert SECRET not in response.text


def test_public_branding_is_an_exact_projection_without_authentication(client, monkeypatch):
    def forbidden_auth(*_args):
        raise AssertionError("Public branding must not authenticate a session")

    monkeypatch.setattr(client.app.state.auth, "authenticate", forbidden_auth)
    outsider = TestClient(client.app, base_url="https://testserver")
    response = outsider.get(PUBLIC)
    assert response.status_code == 200
    assert response.json() == {
        "site_title": "Open Node", "brand_title": "Open Node", "license_required": False,
    }
    assert_safe(response)


def test_admin_save_is_atomic_public_and_versioned_without_a_license_gate(client):
    initial = client.get(PRIVATE)
    assert initial.status_code == 200
    assert initial.json() == {
        "revision": 0, "site_title": "Open Node", "brand_title": "Open Node",
        "license_required": False,
    }
    saved = client.put(PRIVATE, json=payload(site_title="  示例站点 🚀  "))
    assert saved.status_code == 200
    assert saved.json() == {
        "revision": 1, "site_title": "示例站点 🚀", "brand_title": "示例",
        "license_required": False,
    }
    public = TestClient(client.app, base_url="https://testserver").get(PUBLIC)
    assert public.json() == {
        "site_title": "示例站点 🚀", "brand_title": "示例", "license_required": False,
    }
    stale = client.put(PRIVATE, json=payload(site_title="不能覆盖"))
    assert stale.status_code == 409
    assert stale.json()["code"] == "branding_revision_conflict"
    assert client.get(PRIVATE).json() == saved.json()
    for response in (initial, saved, public, stale):
        assert_safe(response)


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_anonymous_admin_routes_refuse_before_body_validation(client, method):
    outsider = TestClient(client.app, base_url="https://testserver")
    response = outsider.request(method, PRIVATE, content=SECRET)
    assert response.status_code == 401
    assert_safe(response)


def test_real_subscriber_can_read_public_but_not_manage_branding(tmp_path):
    _app, _admin, subscriber = make_subscriber(tmp_path)
    assert subscriber_login(subscriber).json()["authenticated"] is True
    assert subscriber.get(PUBLIC).status_code == 200
    for method in ("GET", "PUT"):
        response = subscriber.request(method, PRIVATE, content=SECRET)
        assert response.status_code == 401
        assert_safe(response)


def test_origin_and_csrf_are_checked_before_body_validation(client):
    csrf = client.headers.pop("X-CSRF-Token")
    no_csrf = client.put(PRIVATE, content=SECRET)
    client.headers["X-CSRF-Token"] = csrf
    bad_origin = client.put(
        PRIVATE, content=SECRET, headers={"Origin": "https://attacker.invalid"}
    )
    assert no_csrf.status_code == bad_origin.status_code == 403
    for response in (no_csrf, bad_origin):
        assert_safe(response)
    assert client.get(PRIVATE).json()["revision"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b"null", b"[]", b"true", b"1", b"\xff\xfe",
        b'{"expected_revision":0,"expected_revision":1}',
        b'{"expected_revision":NaN}',
        b'{"expected_revision":Infinity}',
        b'{"expected_revision":0,"secret":"' + SECRET.encode() + b'"}',
        b"[" * 1500 + b"]" * 1500,
    ],
)
def test_malformed_json_is_bounded_and_never_echoed(client, body):
    response = client.put(PRIVATE, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["code"] == "branding_invalid_request"
    assert response.json()["license_required"] is False
    assert_safe(response)
    assert client.get(PRIVATE).json()["revision"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"site_title": ""}, {"site_title": "  "}, {"site_title": None},
        {"site_title": True}, {"site_title": 123}, {"site_title": ["text"]},
        {"site_title": "名" * 81}, {"brand_title": "称" * 41},
        {"site_title": SECRET + "\n"}, {"site_title": "\u202e" + SECRET},
        {"site_title": "\ud800"}, {"brand_title": "\u200d\u200c"},
        {"expected_revision": True}, {"expected_revision": 0.0},
        {"expected_revision": "0"}, {"expected_revision": -1},
        {"expected_revision": 9007199254740992},
        {"license_required": True}, {"token": SECRET}, {"logo_url": SECRET},
    ],
)
def test_invalid_fields_are_not_coerced_or_persisted(client, change):
    response = client.put(
        PRIVATE, content=json.dumps(payload(**change)).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "branding_invalid_request"
    assert_safe(response)
    assert client.get(PRIVATE).json()["revision"] == 0


def test_wrong_content_type_and_oversize_body_fail_without_reflection(client):
    wrong_type = client.put(PRIVATE, content=SECRET)
    too_large = client.put(
        PRIVATE, content=(SECRET + "x" * 4097).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert wrong_type.status_code == 415
    assert too_large.status_code == 413
    for response in (wrong_type, too_large):
        assert response.json()["code"] == "branding_invalid_request"
        assert_safe(response)


def test_html_looking_names_remain_json_text_and_no_other_configuration_changes(client):
    settings = client.app.state.settings.model_dump()
    probe = client.app.state.inventory.probe_settings().model_dump()
    notification = client.get("/api/v1/notifications/settings").json()
    title = '<img src="https://attacker.invalid/x" onerror="alert(1)">'
    saved = client.put(PRIVATE, json=payload(site_title=title, brand_title="免费测试"))
    assert saved.status_code == 200
    public = client.get(PUBLIC)
    assert public.json()["site_title"] == title
    assert public.headers["content-type"].startswith("application/json")
    assert client.app.state.settings.model_dump() == settings
    assert client.app.state.inventory.probe_settings().model_dump() == probe
    assert client.get("/api/v1/notifications/settings").json() == notification
    assert client.get("/api/v1/notifications/deliveries").json()["deliveries"] == []


def test_restart_keeps_original_session_and_saved_public_values(tmp_path):
    app = make_app(tmp_path)
    client = authenticated_client(app)
    session = client.get("/api/v1/auth/session").json()
    saved = client.put(PRIVATE, json=payload()).json()
    restarted = TestClient(make_app(tmp_path), base_url="https://testserver")
    restarted.cookies.update(client.cookies)
    assert restarted.get("/api/v1/auth/session").json() == session
    assert restarted.get(PRIVATE).json() == saved
    assert restarted.get(PUBLIC).json()["site_title"] == saved["site_title"]


@pytest.mark.parametrize("path", [PUBLIC, PRIVATE])
def test_missing_branding_table_returns_fixed_503_without_blocking_health(client, path):
    with client.app.state.inventory._engine.begin() as connection:
        connection.execute(text("DROP TABLE site_branding_settings"))
    response = client.get(path)
    assert response.status_code == 503
    assert response.json()["code"] == "branding_storage_unavailable"
    assert_safe(response)
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True


def test_branding_schema_failure_does_not_block_application_startup(tmp_path, monkeypatch, caplog):
    def unavailable(_self):
        raise BrandingError(503, "branding_storage_unavailable")

    monkeypatch.setattr(BrandingStore, "create_schema", unavailable)
    app = make_app(tmp_path)
    client = authenticated_client(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True
    response = client.get(PUBLIC)
    assert response.status_code == 503
    assert_safe(response)
    assert "Branding settings could not be initialized" in caplog.text


def test_custom_api_prefix_preserves_public_and_private_boundaries(tmp_path):
    app = make_app(tmp_path, api_prefix="/controller/v2")
    app.state.auth.set_administrator("admin", ADMIN_PASSWORD)
    client = TestClient(app, base_url="https://testserver")
    login = client.post(
        "/controller/v2/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD},
        headers={"X-Open-Node-Client": "browser"},
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    public = client.get("/controller/v2/branding")
    private = client.get("/controller/v2/system-settings/branding")
    assert public.status_code == private.status_code == 200
    saved = client.put("/controller/v2/system-settings/branding", json=payload())
    assert saved.status_code == 200 and saved.json()["revision"] == 1
    for response in (public, private, saved):
        assert_safe(response)
