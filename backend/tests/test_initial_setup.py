"""Credentialless first-run setup, restore credential and race boundaries."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.initial_setup import InitialSetupError, InitialSetupRequest
from open_node.main import create_app
from open_node.services.auth import (
    Administrator,
    AdministratorProfile,
    InitialSetupTicket,
    password_hash,
)
from open_node.services.backup_restore import _quiesce
from open_node.services.branding import BrandingSettingsModel
from open_node.services.initial_setup import InitialSetupStore
from sqlalchemy import delete, event
from sqlalchemy.exc import SQLAlchemyError

HEADERS = {"X-Open-Node-Client": "browser"}
PASSWORD = "  first-administrator-secret  "


@pytest.fixture
def setup_app(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'setup.db'}",
                              certificate_state_dir=tmp_path / "certificates", _env_file=None))
    yield app
    for engine in (app.state.auth.engine, app.state.inventory._engine,
                   app.state.certificates.engine):
        engine.dispose()
    app.state.backup_writes.close()


def payload(**changes):
    return dict(
        username="first-admin", password=PASSWORD,
        site_title="  中文站点 🧭  ", brand_title="我的面板", confirm_new_install=True,
    ) | changes


def test_complete_without_credential_login_and_no_remote_reissue(setup_app):
    app = setup_app
    store = InitialSetupStore(app.state.auth)
    client = TestClient(app, base_url="https://testserver")
    initial = client.get("/api/v1/setup")
    assert initial.json() == dict(configured=False, available=True)
    assert initial.headers["cache-control"] == "no-store"
    token, expires = store.issue()
    assert len(token) == 43
    ready = client.get("/api/v1/setup")
    assert ready.json() == dict(configured=False, available=True)
    assert token not in ready.text and PASSWORD not in ready.text
    with app.state.auth.session() as db:
        ticket = db.get(InitialSetupTicket, 1)
        assert ticket.token_hash != token
        assert ticket.expires_at == pytest.approx(expires.timestamp(), abs=0.000001, rel=0)
    response = client.post("/api/v1/setup", json=payload(), headers=HEADERS)
    assert response.status_code == 201
    assert response.json() == dict(configured=True, login_required=True)
    assert "set-cookie" not in response.headers
    assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/v1/servers").status_code == 401
    assert client.get("/api/v1/branding").json()["site_title"] == "中文站点 🧭"
    assert app.state.branding.get_settings().revision == 1
    assert client.post("/api/v1/setup", json=payload(), headers=HEADERS).status_code == 409
    with pytest.raises(InitialSetupError, match="already completed"):
        store.issue()
    assert client.post("/api/v1/setup/issue", headers=HEADERS).status_code == 404
    login = client.post("/api/v1/auth/login", headers=HEADERS,
                        json={"username": "first-admin", "password": PASSWORD})
    assert login.status_code == 200 and login.json()["authenticated"]
    with app.state.auth.session() as db:
        ticket = db.get(InitialSetupTicket, 1)
        assert ticket.token_hash is None and ticket.completed_at is not None
        profile = db.get(AdministratorProfile, 1)
        assert (profile.email, profile.nickname, profile.avatar_url, profile.revision) == (
            "", "first-admin", "", 0,
        )


def test_initial_profile_and_versioned_administrator_update(setup_app):
    app = setup_app
    client = TestClient(app, base_url="https://testserver")
    created = client.post("/api/v1/setup", headers=HEADERS, json=payload(
        email=" operator@example.test ",
        nickname="  运维   管理员  ",
        avatar_url="https://cdn.example.test/avatar.png",
    ))
    assert created.status_code == 201
    login = client.post("/api/v1/auth/login", headers=HEADERS, json={
        "username": "first-admin", "password": PASSWORD,
    })
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    profile = client.get("/api/v1/auth/profile")
    assert profile.json() == {
        "username": "first-admin",
        "email": "operator@example.test",
        "nickname": "运维 管理员",
        "avatar_url": "https://cdn.example.test/avatar.png",
        "revision": 0,
    }
    updated = client.put("/api/v1/auth/profile", headers={"X-CSRF-Token": csrf}, json={
        "email": "next@example.test",
        "nickname": "主控管理员",
        "avatar_url": "",
        "expected_revision": 0,
    })
    assert updated.status_code == 200
    assert updated.json()["revision"] == 1
    conflict = client.put("/api/v1/auth/profile", headers={"X-CSRF-Token": csrf}, json={
        "email": "private@example.test",
        "nickname": "旧草稿",
        "avatar_url": "",
        "expected_revision": 0,
    })
    assert conflict.status_code == 409
    assert "private@example.test" not in conflict.text
    assert client.get("/api/v1/auth/profile").json()["email"] == "next@example.test"


@pytest.mark.parametrize("changes", [
    {"email": "invalid"},
    {"nickname": "x" * 121},
    {"nickname": "控制\n字符"},
    {"avatar_url": "http://example.test/avatar.png"},
    {"avatar_url": "https://user:secret@example.test/avatar.png"},
    {"avatar_url": "file:///etc/passwd"},
])
def test_initial_profile_fields_are_safe(setup_app, changes):
    response = TestClient(setup_app).post(
        "/api/v1/setup", headers=HEADERS, json=payload(**changes),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "setup_invalid_request"
    assert not setup_app.state.auth.configured()


def test_restore_credential_expiry_does_not_gate_administrator_setup(setup_app):
    now = [1000]
    store = InitialSetupStore(setup_app.state.auth, clock=lambda: now[0])
    old, _ = store.issue()
    token, _ = store.issue()
    with pytest.raises(InitialSetupError) as failure:
        store.authorize_restore(old)
    assert failure.value.code == "setup_ticket_invalid"
    assert store.authorize_restore(token) == store._digest(token)
    now[0] += 1800
    assert store.status().available
    with pytest.raises(InitialSetupError) as failure:
        store.authorize_restore(token)
    assert failure.value.code == "setup_ticket_invalid"
    store.complete(InitialSetupRequest(**payload()))
    assert setup_app.state.auth.configured()


def test_administrator_setup_rejects_restore_credentials(setup_app):
    response = TestClient(setup_app).post(
        "/api/v1/setup", headers=HEADERS, json=payload(setup_token="a" * 43),
    )
    assert response.status_code == 422
    assert response.json()["code"] == "setup_invalid_request"
    assert not setup_app.state.auth.configured()


@pytest.mark.parametrize("changes", [
    {"confirm_new_install": False}, {"confirm_new_install": 1}, {"confirm_new_install": "true"},
    {"setup_token": 12}, {"setup_token": {"secret": "PRIVATE"}}, {"password": "too-short"},
    {"password": "a" * 1025}, {"username": "bad space"}, {"username": 12},
    {"site_title": "\u202eunsafe"}, {"brand_title": "x" * 41}, {"PRIVATE-EXTRA": "PRIVATE"},
])
def test_strict_safe_payload(setup_app, changes):
    response = TestClient(setup_app).post("/api/v1/setup", json=payload(**changes),
                                         headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == "setup_invalid_request"
    assert "PRIVATE" not in response.text
    assert not setup_app.state.auth.configured()


@pytest.mark.parametrize("body,status", [
    ('{"password":"PRIVATE","password":"PRIVATE"}', 422),
    ('{"password":NaN}', 422), ('[1,2]', 422), ("[" * 1500, 422),
    ("PRIVATE" * 3000, 413),
])
def test_bounded_unique_json(setup_app, body, status):
    response = TestClient(setup_app).post("/api/v1/setup", content=body,
                                         headers=HEADERS | {"Content-Type": "application/json"})
    assert response.status_code == status
    assert "PRIVATE" not in response.text


def test_origin_header_media_and_durable_limit(setup_app):
    client = TestClient(setup_app)
    assert client.post("/api/v1/setup", json=payload()).status_code == 403
    assert client.post("/api/v1/setup", json=payload(), headers=HEADERS | {
        "Origin": "https://not-this-panel.test",
    }).status_code == 403
    assert client.post("/api/v1/setup", content="PRIVATE", headers=HEADERS).status_code == 415
    for _ in range(9):
        assert client.post("/api/v1/setup", json=payload(username="bad space"),
                           headers=HEADERS).status_code == 422
    response = TestClient(setup_app).post("/api/v1/setup", json=payload(), headers=HEADERS)
    assert response.status_code == 429 and response.headers["retry-after"] == "60"
    assert not setup_app.state.auth.configured()


@pytest.mark.parametrize("race", ["double", "cli"])
def test_commit_rechecks_authority_after_hash(setup_app, monkeypatch, race):
    app = setup_app
    store = InitialSetupStore(app.state.auth)
    original_hash = password_hash.hash
    barrier = Barrier(2)

    def pause(value):
        hashed = original_hash(value)
        if race == "double":
            barrier.wait(timeout=10)
        elif race == "cli":
            monkeypatch.setattr(password_hash, "hash", original_hash)
            app.state.auth.set_administrator("cli-admin", PASSWORD)
        return hashed

    monkeypatch.setattr(password_hash, "hash", pause)

    def finish(username):
        try:
            store.complete(InitialSetupRequest(**payload(username=username)))
            return "completed"
        except InitialSetupError as exc:
            return exc.code

    if race == "double":
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(finish, ["first", "second"]))
        assert sorted(outcomes) == ["completed", "setup_already_completed"]
        assert app.state.branding.get_settings().revision == 1
    else:
        assert finish("browser") == "setup_already_completed"
        assert app.state.branding.get_settings().revision == 0


def test_commit_failure_rolls_back_all_changes(setup_app):
    app = setup_app
    store = InitialSetupStore(app.state.auth)
    def fail(_connection):
        raise SQLAlchemyError("PRIVATE-COMMIT-FAILURE")

    event.listen(app.state.auth.engine, "commit", fail)
    try:
        with pytest.raises(InitialSetupError) as failure:
            store.complete(InitialSetupRequest(**payload()))
        assert "PRIVATE" not in str(failure.value)
    finally:
        event.remove(app.state.auth.engine, "commit", fail)
    assert not app.state.auth.configured()
    assert app.state.branding.get_settings().revision == 0
    assert store.status().available
    store.complete(InitialSetupRequest(**payload()))
    assert app.state.auth.configured()


def test_missing_branding_is_not_partial_success(setup_app):
    app = setup_app
    store = InitialSetupStore(app.state.auth)
    with app.state.auth.session.begin() as db:
        db.execute(delete(BrandingSettingsModel))
    with pytest.raises(InitialSetupError):
        store.complete(InitialSetupRequest(**payload()))
    assert store.status().available and not app.state.auth.configured()


def test_local_creation_deletion_and_restore_do_not_reopen_setup(setup_app):
    app = setup_app
    store = InitialSetupStore(app.state.auth)
    store.issue()
    app.state.auth.set_administrator("local-admin", PASSWORD)
    with app.state.auth.session.begin() as db:
        db.execute(delete(Administrator))
    assert store.status().configured and not store.status().available
    with pytest.raises(InitialSetupError):
        store.issue()
    # Restores also consume optional preexisting setup tickets, even malformed
    # historical entries. This uses the real application schema, no remote jobs.
    with app.state.auth.session.begin() as db:
        row = db.get(InitialSetupTicket, 1)
        row.token_hash = "b" * 64
        row.expires_at = 9999999999
    with sqlite3.connect(app.state.auth.engine.url.database) as db:
        _quiesce(db)
        assert db.execute("SELECT token_hash,expires_at FROM initial_setup_tickets").fetchone() == (
            None, 0,
        )
        db.execute("DROP TABLE initial_setup_tickets")
        _quiesce(db)  # Older archives did not have the optional table.


def test_cli_issue_does_not_read_password_and_rejects_existing_admin(setup_app):
    app = setup_app
    environment = os.environ | {
        "OPEN_NODE_DATABASE_URL": str(app.state.auth.engine.url),
        "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
    }

    def run(*args):
        return subprocess.run([sys.executable, "-m", "open_node.admin", *args],
                              env=environment, input="", text=True, capture_output=True, timeout=15)

    first = run("prepare-setup", "--json", "--if-unconfigured")
    assert first.returncode == 0, first.stderr
    issued = json.loads(first.stdout)
    assert len(issued["setup_token"]) == 43
    assert "Password:" not in first.stderr
    app.state.auth.set_administrator("local-admin", PASSWORD)
    result = run("prepare-setup", "--json")
    assert result.returncode == 1 and result.stdout == ""
    assert issued["setup_token"] not in result.stderr
    assert "已初始化" in result.stderr
    idempotent = run("prepare-setup", "--if-unconfigured")
    assert idempotent.returncode == 0 and idempotent.stderr == ""
    assert "无需签发" in idempotent.stdout
    assert issued["setup_token"] not in idempotent.stdout
