"""Browser restore staging, first-run admission, and restart activation."""

import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.domain.restore import BrowserRestoreError
from open_node.main import create_app
from open_node.services import browser_restore as browser_restore_module
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


def test_postgres_activation_preflight_failure_does_not_switch_database(
    tmp_path, monkeypatch,
):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    request_id = str(uuid4())
    pending_name = PENDING_PREFIX + request_id
    pending = root / pending_name
    pending.mkdir(mode=0o700)
    (pending / ".open-node-restore.json").write_text("{}", encoding="utf-8")
    (pending / ".open-node-restore.json").chmod(0o600)
    postgres_metadata = pending / ".open-node-postgres-restore.json"
    postgres_metadata.write_text(
        json.dumps(
            {"schema_version": 1, "stage_database": "open_node_restore_deadbeef"}
        ),
        encoding="utf-8",
    )
    postgres_metadata.chmod(0o600)
    marker = {
        "schema_version": 1,
        "request_id": request_id,
        "restore_id": str(uuid4()),
        "pending_dir": pending_name,
        "phase": "prepared",
        "old_entries": [],
        "new_entries": [],
        "database_engine": "postgresql",
        "stage_database": "open_node_restore_deadbeef",
    }
    marker_path = root / ACTIVATION_MARKER
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    marker_path.chmod(0o600)
    configuration = Settings(
        database_url=(
            "postgresql+psycopg://open_node:"
            "0123456789abcdef0123456789abcdef@postgres:5432/open_node"
        ),
        control_state_dir=root,
        certificate_state_dir=root / "certificates",
        external_subscriptions_state_dir=root / "external-subscriptions",
        federation_state_dir=root / "federation",
        notifications_state_dir=root / "notifications",
        _env_file=None,
    )
    switched = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal switched
        switched = True
        raise AssertionError("database activation must follow filesystem preflight")

    monkeypatch.setattr(
        browser_restore_module, "_activate_postgres_database", fail_if_called,
    )
    with pytest.raises(BrowserRestoreError):
        activate_pending_restore(configuration)
    assert switched is False
    assert not postgres_metadata.exists()


def test_postgres_startup_discards_journaled_orphan_stage(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    orphan = root / (".open-node-restore-" + "a" * 32)
    orphan.mkdir(mode=0o700)
    metadata = orphan / ".open-node-postgres-restore.json"
    metadata.write_text(
        json.dumps(
            {"schema_version": 1, "stage_database": "open_node_restore_deadbeef"}
        ),
        encoding="utf-8",
    )
    metadata.chmod(0o600)
    configuration = Settings(
        database_url=(
            "postgresql+psycopg://open_node:"
            "0123456789abcdef0123456789abcdef@postgres:5432/open_node"
        ),
        control_state_dir=root,
        certificate_state_dir=root / "certificates",
        external_subscriptions_state_dir=root / "external-subscriptions",
        federation_state_dir=root / "federation",
        notifications_state_dir=root / "notifications",
        _env_file=None,
    )
    dropped = []
    monkeypatch.setattr(
        browser_restore_module,
        "require_drop_postgres_database",
        lambda database_url, stage: dropped.append((database_url, stage)),
    )

    assert activate_pending_restore(configuration) == root
    assert dropped == [
        (configuration.database_url, "open_node_restore_deadbeef")
    ]
    assert not orphan.exists()


def test_failed_prepare_keeps_stage_journal_until_database_drop_succeeds(
    tmp_path, monkeypatch,
):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    request_id = str(uuid4())
    pending_name = PENDING_PREFIX + request_id
    pending = root / pending_name
    pending.mkdir(mode=0o700)
    metadata = pending / ".open-node-postgres-restore.json"
    metadata.write_text(
        json.dumps(
            {"schema_version": 1, "stage_database": "open_node_restore_deadbeef"}
        ),
        encoding="utf-8",
    )
    metadata.chmod(0o600)
    outcomes = iter((False, True))
    monkeypatch.setattr(
        browser_restore_module,
        "drop_postgres_database",
        lambda *_args: next(outcomes),
    )

    browser_restore_module._discard_pending_database(
        "postgresql+psycopg://open_node:secret@postgres/open_node",
        root,
        pending_name,
        "",
    )
    assert pending.is_dir()
    browser_restore_module._discard_pending_database(
        "postgresql+psycopg://open_node:secret@postgres/open_node",
        root,
        pending_name,
        "",
    )
    assert not pending.exists()
