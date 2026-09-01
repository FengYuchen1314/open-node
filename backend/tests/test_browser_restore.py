"""Browser restore staging, first-run admission, and restart activation."""

import hashlib
import sqlite3

import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi.testclient import TestClient
from open_node.domain.restore import BrowserRestoreError
from open_node.main import create_app
from open_node.services.browser_restore import (
    ACTIVATION_MARKER,
    PENDING_PREFIX,
    ROLLBACK_PREFIX,
    UPLOAD_DIRECTORY,
    activate_pending_restore,
)
from open_node.services.initial_setup import InitialSetupStore
from test_backup_encryption import official_age as official_age
from test_backup_encryption import real_age_keys as real_age_keys
from test_backup_restore import close_app, settings
from test_backup_restore import saved as saved


def _prepare_payload(saved, **values):
    return {
        "format": values.get("format", "plain"),
        "identity": values.get("identity", ""),
        "subscriber_totp_key": saved.key.decode(),
        "confirm_replace_instance": True,
        "confirm_trusted_backup": True,
        **values.get("authorization", {"password": ADMIN_PASSWORD, "code": ""}),
    }


def _upload(client, endpoint: str, content: bytes, headers=None):
    return client.post(
        endpoint, content=content,
        headers={"Content-Type": "application/octet-stream", **(headers or {})},
    )


def test_administrator_plain_upload_prepares_without_overwriting_and_activates(saved, tmp_path):
    data = tmp_path / "current"
    data.mkdir(mode=0o700)
    configuration = settings(data, saved.key)
    app = create_app(configuration)
    client = authenticated_client(app)
    assert client.get("/api/v1/backups").json()["restoration_supported"] is True
    uploaded = _upload(client, "/api/v1/backups/restore-uploads", saved.raw)
    assert uploaded.status_code == 201, uploaded.text
    receipt = uploaded.json()
    assert receipt["size"] == len(saved.raw)
    assert receipt["sha256"] == hashlib.sha256(saved.raw).hexdigest()
    prepared = client.post(
        f"/api/v1/backups/restore-uploads/{receipt['id']}/prepare",
        json=_prepare_payload(saved),
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["restart_required"] is True
    assert prepared.json()["automatic_restart"] is False
    with sqlite3.connect(data / "open-node.db") as database:
        assert database.execute("SELECT count(*) FROM servers").fetchone() == (0,)
    assert (data / ACTIVATION_MARKER).is_file()
    assert (data / (PENDING_PREFIX + receipt["id"]) / "open-node.db").is_file()

    client.close()
    close_app(app)
    assert activate_pending_restore(configuration) == data
    assert not (data / ACTIVATION_MARKER).exists()
    assert not (data / (PENDING_PREFIX + receipt["id"])).exists()
    rollback = data / (ROLLBACK_PREFIX + receipt["id"])
    assert (rollback / "open-node.db").is_file()
    assert (rollback / UPLOAD_DIRECTORY).is_dir()
    with sqlite3.connect(data / "open-node.db") as database:
        assert database.execute("SELECT name FROM servers").fetchall() == [("restore-test",)]
        assert database.execute("SELECT count(*) FROM operator_sessions").fetchone() == (0,)
    assert (data / ".open-node-restore.json").is_file()
    assert (data / "restore.env").is_file()

    restored = create_app(configuration)
    try:
        status = restored.state.restore_state.read()
        assert status.blocked is True
        assert str(status.record.id) == prepared.json()["id"]
    finally:
        close_app(restored)


def test_first_run_token_can_prepare_official_age_backup(saved, official_age, tmp_path):
    data = tmp_path / "fresh"
    data.mkdir(mode=0o700)
    configuration = settings(data, saved.key)
    app = create_app(configuration)
    token, _expires = InitialSetupStore(app.state.auth).issue()
    client = TestClient(app, base_url="https://testserver")
    browser = {
        "Origin": "https://testserver",
        "X-Open-Node-Client": "browser",
        "X-Open-Node-Setup-Token": token,
    }
    uploaded = _upload(
        client, "/api/v1/setup/restore-uploads", saved.encrypted, browser,
    )
    assert uploaded.status_code == 201, uploaded.text
    receipt = uploaded.json()
    payload = _prepare_payload(
        saved, format="age", identity=official_age.identity.decode(),
        authorization={"setup_token": token},
    )
    prepared = client.post(
        f"/api/v1/setup/restore-uploads/{receipt['id']}/prepare",
        json=payload, headers={"Origin": "https://testserver", "X-Open-Node-Client": "browser"},
    )
    assert prepared.status_code == 200, prepared.text
    with sqlite3.connect(data / "open-node.db") as database:
        assert database.execute("SELECT count(*) FROM administrator").fetchone() == (0,)

    client.close()
    close_app(app)
    activate_pending_restore(configuration)
    with sqlite3.connect(data / "open-node.db") as database:
        assert database.execute("SELECT username FROM administrator").fetchall() == [("admin",)]
        ticket = database.execute(
            "SELECT token_hash, expires_at FROM initial_setup_tickets WHERE id=1"
        ).fetchone()
        assert ticket is None or ticket == (None, 0.0)


def test_incomplete_upload_is_removed_and_does_not_consume_a_slot(saved, tmp_path):
    data = tmp_path / "partial"
    data.mkdir(mode=0o700)
    app = create_app(settings(data, saved.key))
    try:
        writer = app.state.browser_restore.writer("a" * 64, len(saved.raw))
        with pytest.raises(BrowserRestoreError):
            with writer:
                writer.write(saved.raw[:22])
                writer.finish()
        uploads = data / UPLOAD_DIRECTORY
        assert list(uploads.iterdir()) == []
        with app.state.browser_restore.writer("a" * 64, len(saved.raw)) as complete:
            complete.write(saved.raw)
            receipt = complete.finish()
        assert receipt.size == len(saved.raw)
    finally:
        close_app(app)
