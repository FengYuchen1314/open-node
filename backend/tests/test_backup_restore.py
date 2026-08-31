"""Real age + SQLite restore and first-boot admission, synthetic private data only."""

import asyncio
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from open_node import backup_cli
from open_node.api.auth import SESSION_COOKIE
from open_node.core.config import Settings
from open_node.domain.restore import RestoreRecord
from open_node.main import create_app
from open_node.services.agent_bootstrap import AgentBootstrapTicketModel
from open_node.services.backup_creation import create_control_plane_backup
from open_node.services.backup_encryption import decrypted_backup_archive
from open_node.services.backup_restore import BackupRestoreError, restore_backup_archive
from open_node.services.backup_snapshot import configured_backup_layout
from open_node.services.certificates import CertificateJob, ManagedCertificate
from open_node.services.restore_state import RESTORE_MARKER, RestoreState, RestoreStateError
from starlette.websockets import WebSocketDisconnect
from test_backup_encryption import official_age as official_age
from test_backup_encryption import real_age_keys as real_age_keys


def close_app(app):
    for store in (app.state.auth, app.state.inventory, app.state.certificates):
        for name in ("engine", "_engine"):
            engine = getattr(store, name, None)
            if engine is not None:
                engine.dispose()
    app.state.backup_writes.close()


def settings(data, key):
    return Settings(database_url=f"sqlite:///{data / 'open-node.db'}",
                    certificate_state_dir=data / "certificates",
                    subscriber_totp_key=key.decode(), _env_file=None)


@pytest.fixture
def saved(tmp_path, official_age):
    data, scratch = tmp_path / "original", tmp_path / "scratch"
    data.mkdir(mode=0o700)
    scratch.mkdir(mode=0o700)
    key = Fernet.generate_key()
    app = create_app(settings(data, key))
    client = authenticated_client(app)  # No lifespan: nothing may contact a remote service.
    created = client.post("/api/v1/servers", json={"name": "restore-test"}).json()
    queued = client.post(f"/api/v1/servers/{created['server']['id']}/commands", json={
        "method": "GET", "path": "/api/child/system/info",
    })
    assert queued.status_code == 201
    with app.state.inventory._session() as session:
        session.add(AgentBootstrapTicketModel(
            server_id=created["server"]["id"], ticket_hash="a" * 64, credential_hash="b" * 64,
            control_url="https://example.test", transport="websocket", issued_at=1, expires_at=2,
        ))
        session.commit()
    token = client.cookies.get(SESSION_COOKIE)
    app.state.auth.begin_totp(token, ADMIN_PASSWORD)  # Encrypted dependency, not yet active MFA.
    app.state.certificates.vault.cipher()
    certificate_id, job_id = str(uuid4()), str(uuid4())
    with app.state.certificates.session() as session:
        session.add(ManagedCertificate(
            id=certificate_id, name="restore-test", domains=["example.test"],
            status="queued", active_job_id=job_id, auto_renew=True,
        ))
        session.add(CertificateJob(
            id=job_id, certificate_id=certificate_id, kind="issue", force=False,
            status="queued", parameters={},
        ))
        session.commit()
    jobs = data / "certificates" / "jobs"
    jobs.mkdir(mode=0o700)
    (jobs / "old-task.json").write_text("{}")
    (jobs / "old-task.json").chmod(0o600)
    with create_control_plane_backup(
        configured_backup_layout(app.state.settings), barrier=app.state.backup_writes,
        recipient=official_age.public, staging_directory=scratch, totp_key=key,
    ) as backup:
        encrypted = backup.stream.read()
    with decrypted_backup_archive(io.BytesIO(encrypted), official_age.identity) as backup:
        raw = backup.stream.read()
    client.close()
    close_app(app)
    digest = hashlib.sha256((data / "open-node.db").read_bytes()).hexdigest()
    return SimpleNamespace(raw=raw, encrypted=encrypted, key=key, original=data,
                           digest=digest, token=token)


def restore(saved, tmp_path, **options):
    return restore_backup_archive(io.BytesIO(saved.raw), str(tmp_path / "restored"),
                                  totp_key=options.get("key", saved.key))


def test_roundtrip_invalidates_old_authority_and_preserves_source(saved, tmp_path):
    result = restore(saved, tmp_path)
    root = tmp_path / "restored"
    assert result.invalidated_sessions == 1
    assert result.cancelled_agent_commands == result.cancelled_certificate_jobs == 1
    assert result.quarantined_files == 1
    assert not (root / "certificates/jobs").exists()
    assert (root / ".restore-quarantine/certificates/jobs/old-task.json").read_text() == "{}"
    assert (root / "certificates/vault.key").read_bytes() == (
        saved.original / "certificates/vault.key"
    ).read_bytes()
    with sqlite3.connect(root / "open-node.db") as db:
        assert db.execute("SELECT count(*) FROM operator_sessions").fetchone() == (0,)
        assert db.execute("SELECT pending_secret FROM administrator_factors").fetchone() == (None,)
        revoked = db.execute("SELECT revoked_at FROM agent_bootstrap_tickets").fetchone()[0]
        assert revoked is not None
        assert db.execute("SELECT status FROM agent_commands").fetchone() == ("failed",)
        assert db.execute("SELECT status FROM certificate_jobs").fetchone() == ("failed",)
        values = db.execute("SELECT auto_renew, active_job_id FROM managed_certificates").fetchone()
        assert values == (0, None)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    for directory, _, files in os.walk(root):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
        for name in files:
            assert stat.S_IMODE(os.stat(os.path.join(directory, name)).st_mode) == 0o600
    source_digest = hashlib.sha256((saved.original / "open-node.db").read_bytes()).hexdigest()
    assert source_digest == saved.digest


@pytest.mark.parametrize("bad_key", [None, b"wrong", Fernet.generate_key()])
def test_missing_or_wrong_totp_never_publishes(saved, tmp_path, bad_key):
    with pytest.raises(BackupRestoreError):
        restore(saved, tmp_path, key=bad_key)
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".open-node-restore-*"))


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "public_parent"])
def test_existing_or_unsafe_target_is_untouched(saved, tmp_path, kind):
    output = tmp_path / "restored"
    if kind == "directory":
        output.mkdir()
    elif kind == "file":
        output.write_bytes(b"untouched")
    elif kind == "symlink":
        output.symlink_to(saved.original, target_is_directory=True)
    else:
        tmp_path.chmod(0o755)
    with pytest.raises(BackupRestoreError):
        restore(saved, tmp_path)
    if kind == "file":
        assert output.read_bytes() == b"untouched"
    if kind == "symlink":
        assert output.is_symlink()


def test_late_destination_race_does_not_overwrite(saved, tmp_path, monkeypatch):
    from open_node.services import backup_restore as module

    publish = module._publish
    def race(parent, temporary, target):
        os.mkdir(target, dir_fd=parent)
        publish(parent, temporary, target)
    monkeypatch.setattr(module, "_publish", race)
    with pytest.raises(BackupRestoreError):
        restore(saved, tmp_path)
    assert list((tmp_path / "restored").iterdir()) == []
    assert not list(tmp_path.glob(".open-node-restore-*"))


def test_encrypted_cli_restores_without_echoing_secrets(saved, tmp_path, official_age, capsys):
    source, identity, key = (tmp_path / name for name in ("backup.age", "identity", "totp"))
    source.write_bytes(saved.encrypted)
    identity.write_bytes(official_age.identity)
    key.write_bytes(saved.key)
    identity.chmod(0o400)
    key.chmod(0o600)
    result = backup_cli.main([
        "restore", str(source), "--identity", str(identity), "--totp-key-file", str(key),
        "--output", str(tmp_path / "restored"), "--confirm-stopped", "--confirm-trusted-source",
        "--json",
    ])
    output = capsys.readouterr()
    assert result == 0 and not output.err
    assert json.loads(output.out)["review_required"] is True
    assert saved.key.decode() not in output.out and "AGE-SECRET-KEY" not in output.out


def test_first_boot_and_fresh_review_require_explicit_restart(saved, tmp_path, monkeypatch):
    from open_node import main

    result = restore(saved, tmp_path)
    constructors = []
    class Worker:
        def __init__(self, *args, **kwargs):
            constructors.append(self)
        async def run(self):
            await asyncio.Event().wait()
    for name in ("CertificateWorker", "SubscriptionAccessWorker", "ServerTrafficWorker",
                 "NotificationWorker"):
        monkeypatch.setattr(main, name, Worker)
    configuration = settings(tmp_path / "restored", saved.key)
    app = create_app(configuration)
    try:
        with TestClient(app, base_url="https://testserver") as anonymous:
            assert not constructors
            anonymous.cookies.set(SESSION_COOKIE, saved.token)
            assert anonymous.get("/api/v1/backups").status_code == 401
            assert anonymous.get("/api/v1/servers").status_code == 503
            assert anonymous.post("/api/v1/agents/commands/lease", json={}).status_code == 503
            assert anonymous.get("/", follow_redirects=False).headers["location"] == "/backups"
            with pytest.raises(WebSocketDisconnect):
                with anonymous.websocket_connect("/api/remote/ws"):
                    pytest.fail("Restored instance accepted an Agent WebSocket")
            client = authenticated_client(app)
            payload = dict(id=str(result.id), password=ADMIN_PASSWORD, code="",
                           confirm_original_stopped=True, confirm_configuration=True,
                           confirm_trusted_backup=True)
            endpoint = "/api/v1/backups/restore-review"
            wrong_csrf = client.post(endpoint, json=payload, headers={"X-CSRF-Token": "wrong"})
            assert wrong_csrf.status_code == 403
            implicit = client.post(endpoint, json={**payload, "confirm_configuration": 1})
            assert implicit.status_code == 422
            assert client.post(endpoint, json={**payload, "password": "wrong"}).status_code == 403
            response = client.post(endpoint, json=payload)
            assert response.status_code == 200 and response.json()["restart_required"] is True
            assert response.headers["cache-control"] == "no-store"
            duplicate = client.post(endpoint, json={**payload, "password": "unused"})
            assert duplicate.json() == response.json()
            assert client.get("/api/v1/servers").status_code == 503
            assert not constructors
            client.close()
    finally:
        close_app(app)
    restarted = create_app(configuration)
    try:
        assert restarted.state.restore_state.blocked is False
        with authenticated_client(restarted) as client:
            assert len(constructors) == 4
            assert client.get("/api/v1/servers").status_code == 200
    finally:
        close_app(restarted)


def test_damaged_or_removed_marker_fails_closed(saved, tmp_path):
    restore(saved, tmp_path)
    root = tmp_path / "restored"
    state = RestoreState(settings(root, saved.key).database_url)
    marker = root / RESTORE_MARKER
    marker.write_text("{}")
    with pytest.raises(RestoreStateError):
        state.read()
    with pytest.raises(RestoreStateError):
        RestoreState(settings(root, saved.key).database_url)
    marker.unlink()
    with pytest.raises(RestoreStateError):
        state.read()


@pytest.mark.parametrize("statement", [
    "CREATE VIEW unexpected AS SELECT 1",
    "CREATE TRIGGER unexpected AFTER INSERT ON administrator BEGIN SELECT 1; END",
    "CREATE TABLE unexpected (value INT, derived INT GENERATED ALWAYS AS (value + 1))",
])
def test_sqlite_restore_rejects_executable_schema(statement):
    from open_node.services.backup_restore import _database

    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE administrator (id INTEGER)")
        db.execute("INSERT INTO administrator VALUES (1)")
        db.execute(statement)
        db.commit()
        with pytest.raises(BackupRestoreError):
            _database(db)


def test_private_marker_is_strict_and_review_needs_restart(tmp_path):
    record = RestoreRecord(
        id=uuid4(), created_at=datetime.now(UTC), archive_sha256="a" * 64,
        invalidated_sessions=0, cancelled_agent_commands=0, cancelled_certificate_jobs=0,
        quarantined_files=0,
    )
    marker = tmp_path / RESTORE_MARKER
    marker.write_text(record.model_dump_json())
    marker.chmod(0o600)
    url = f"sqlite:///{tmp_path / 'open-node.db'}"
    state = RestoreState(url)
    assert state.blocked is True
    assert state.review(record.id).restart_required is True
    assert state.blocked is True and RestoreState(url).blocked is False
    marker.write_text(record.model_dump_json().replace('"version":1', '"version":1,"version":1'))
    with pytest.raises(RestoreStateError):
        state.read()
    with pytest.raises(RestoreStateError):
        RestoreState(url)
    marker.unlink()
    with pytest.raises(RestoreStateError):
        state.read()


def test_restore_compose_template_parses_with_private_environment(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose configuration parser is not installed")
    source = Path(__file__).resolve().parents[2] / "deploy/compose.restore.example.yaml"
    environment = tmp_path / "restore.env"
    environment.write_text("OPEN_NODE_DATABASE_URL=sqlite:////var/lib/open-node/open-node.db\n")
    environment.chmod(0o600)
    result = subprocess.run(
        ["docker", "compose", "-f", str(source), "config", "--quiet"],
        env={"PATH": os.environ["PATH"], "OPEN_NODE_RESTORE_IMAGE": "open-node:restore-test",
             "OPEN_NODE_RESTORE_DATA_DIR": str(tmp_path)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10, check=False,
    )
    assert result.returncode == 0, "Restore Compose configuration rejected"
