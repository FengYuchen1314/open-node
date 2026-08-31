import hashlib
import io
import json
import subprocess
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from uuid import uuid4

import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services import backup_jobs
from open_node.services.backup_validation import validate_backup_archive
from test_backup_encryption import official_age as official_age
from test_backup_encryption import real_age_keys as real_age_keys

PUBLIC_SHAPE = "age1" + "q" * 58


@pytest.fixture
def client(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    app = create_app(Settings(
        database_url=f"sqlite:///{data / 'instance.db'}",
        certificate_state_dir=data / "certificates",
        backup_temporary_directory=scratch,
        _env_file=None,
    ))
    try:
        with authenticated_client(app) as value:
            yield value
    finally:
        for store in (app.state.auth, app.state.inventory, app.state.certificates):
            for name in ("engine", "_engine"):
                engine = getattr(store, name, None)
                if engine is not None:
                    engine.dispose()
        app.state.backup_writes.close()


def payload(**changes):
    value = {"request_id": str(uuid4()), "recipient": PUBLIC_SHAPE, "password": ADMIN_PASSWORD}
    return {**value, **changes}


def test_backup_index_is_private_and_describes_actual_scope(client):
    response = client.get("/api/v1/backups")
    assert response.status_code == 200
    assert response.json() == {
        "available": True, "unavailable_code": None, "jobs": [], "max_completed": 2,
        "ttl_seconds": 900, "requires_two_factor": False, "restoration_supported": False,
        "offline_restoration_supported": True,
        "recovery": {"blocked": False, "restart_required": False, "record": None},
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    anonymous = TestClient(client.app, base_url="https://testserver")
    for method, suffix in (("GET", ""), ("POST", ""), ("GET", f"/{uuid4()}"),
                           ("GET", f"/{uuid4()}/download"), ("DELETE", f"/{uuid4()}")):
        assert anonymous.request(method, "/api/v1/backups" + suffix).status_code == 401


@pytest.mark.parametrize("headers", [
    {"x-csrf-token": "wrong"}, {"origin": "https://not-allowed.example"},
])
def test_creation_requires_csrf_and_allowed_origin_before_reading_secrets(client, headers):
    response = client.post("/api/v1/backups", json=payload(), headers=headers)
    assert response.status_code == 403
    assert ADMIN_PASSWORD not in response.text
    assert client.get("/api/v1/backups").json()["jobs"] == []


@pytest.mark.parametrize("body,status", [
    (b"not-json-private", 422),
    (b'{"password":"secret-one","password":"secret-two"}', 422),
    (json.dumps(payload(extra="secret-extra")).encode(), 422),
    (json.dumps(payload(request_id="../private")).encode(), 422),
    (json.dumps(payload(recipient="AGE-SECRET-KEY-1" + "Q" * 58)).encode(), 422),
    (json.dumps(payload(password=12345)).encode(), 422),
    (b"x" * 8193, 413),
])
def test_invalid_or_oversized_requests_have_fixed_non_echo_errors(client, body, status):
    response = client.post(
        "/api/v1/backups", content=body, headers={"Content-Type": "application/json"},
    )
    assert response.status_code == status
    assert response.json() == {
        "code": "backup_invalid_request", "detail": "备份请求格式不正确，请检查公钥和输入内容。",
        "license_required": False,
    }
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/backups").json()["jobs"] == []


def test_bad_password_cannot_enqueue_a_job_and_has_no_sensitive_error_detail(client):
    response = client.post("/api/v1/backups", json=payload(password="private-wrong-password"))
    assert response.status_code == 403
    assert response.json()["code"] == "backup_authorization_expired"
    assert "private-wrong-password" not in response.text
    assert not client.get("/api/v1/backups").json()["jobs"]


def test_duplicate_request_is_reconciled_without_second_proof_and_is_session_bound(
    client, monkeypatch,
):
    original = backup_jobs.create_control_plane_backup
    release, entered = threading.Event(), threading.Event()
    calls = []
    authorize = client.app.state.backup_authorizer.issue

    @contextmanager
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        with original(*args, **kwargs) as result:
            yield result

    def observed(*args, **kwargs):
        calls.append(True)
        return authorize(*args, **kwargs)

    monkeypatch.setattr(backup_jobs, "create_control_plane_backup", held)
    monkeypatch.setattr(client.app.state.backup_authorizer, "issue", observed)
    request = payload()
    try:
        first = client.post("/api/v1/backups", json=request)
        assert first.status_code == 202, first.text
        assert entered.wait(3)
        duplicate = client.post("/api/v1/backups", json={**request, "password": "not-reused"})
        assert duplicate.status_code == 202
        identifier = request["request_id"]
        assert first.json()["id"] == duplicate.json()["id"] == identifier
        assert calls == [True]
        assert client.get(f"/api/v1/backups/{identifier}/download").status_code == 409
        conflict = client.post(
            "/api/v1/backups", json={**request, "recipient": "age1" + "p" * 58},
        )
        assert conflict.status_code == 409
        other = authenticated_client(client.app)
        assert other.get(f"/api/v1/backups/{identifier}").status_code == 404
        assert other.delete(f"/api/v1/backups/{identifier}").status_code == 404
        assert client.delete(f"/api/v1/backups/{identifier}").status_code == 204
        assert client.get(f"/api/v1/backups/{identifier}/download").status_code in (404, 409)
    finally:
        release.set()


def test_real_http_creation_download_independent_age_decryption_and_delete(client, official_age):
    response = client.post("/api/v1/backups", json=payload(recipient=official_age.public))
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    deadline = time.monotonic() + 20
    while True:
        status = client.get(f"/api/v1/backups/{identifier}")
        if status.status_code == 200 and status.json()["status"] not in {"queued", "running"}:
            break
        assert time.monotonic() < deadline
        threading.Event().wait(0.02)
    job = status.json()
    assert job["status"] == "ready", job
    assert job["restoration_ready"] is False
    assert client.get(
        f"/api/v1/backups/{identifier}/download", headers={"Range": "bytes=0-9"},
    ).status_code == 416
    downloaded = client.get(f"/api/v1/backups/{identifier}/download")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"age-encryption.org/v1\n")
    assert len(downloaded.content) == job["size"]
    assert hashlib.sha256(downloaded.content).hexdigest() == job["sha256"]
    assert downloaded.headers["x-content-sha256"] == job["sha256"]
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-disposition"] == (
        f'attachment; filename="open-node-backup-{identifier}.zip.age"'
    )
    with tempfile.TemporaryFile("w+b", buffering=0) as key:
        key.write(official_age.identity)
        key.seek(0)
        decrypted = subprocess.run(
            [str(official_age.binary), "--decrypt", "-i", f"/proc/self/fd/{key.fileno()}"],
            input=downloaded.content, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            pass_fds=(key.fileno(),), timeout=10, env={}, check=False,
        )
    assert decrypted.returncode == 0
    validated = validate_backup_archive(io.BytesIO(decrypted.stdout))
    assert validated.manifest.format == "open-node-control-plane-backup"
    with zipfile.ZipFile(io.BytesIO(decrypted.stdout)) as archive:
        assert "data/open-node.db" in archive.namelist()
    assert client.delete(f"/api/v1/backups/{identifier}").status_code == 204
    assert client.get(f"/api/v1/backups/{identifier}/download").status_code in (404, 409)
