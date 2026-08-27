import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import authenticated_client
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import (
    CertificateError,
    CertificateJob,
    DNSProvider,
    ManagedCertificate,
)
from test_inventory import scan_result_payload


def client(tmp_path):
    return authenticated_client(
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'certificate.db'}",
                certificate_state_dir=tmp_path / "vault",
                certificate_lego_binary=Path("/bin/true"),
            )
        )
    )


def pair(domains=("localhost",), *, expired=False):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domains[0])])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if expired else 90))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(domain) for domain in domains]), False
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode(), key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def create_profile(browser):
    provider = browser.post(
        "/api/v1/certificates/providers",
        json={
            "name": "DNS",
            "provider": "cloudflare",
            "credentials": {"CF_DNS_API_TOKEN": "private-dns-token"},
        },
    )
    assert provider.status_code == 201, provider.text
    created = browser.post(
        "/api/v1/certificates",
        json={
            "name": "Website",
            "domains": ["localhost"],
            "email": "operator@example.com",
            "provider_id": provider.json()["id"],
            "accept_terms": True,
        },
    )
    assert created.status_code == 201, created.text
    return created.json(), provider.json()


def test_private_catalog_encrypts_secrets_and_never_echoes_validation_inputs(tmp_path):
    browser = client(tmp_path)
    profile, provider = create_profile(browser)
    store = browser.app.state.certificates
    with store.session() as db:
        encrypted = db.get(DNSProvider, provider["id"]).credentials
    assert "private-dns-token" not in encrypted
    assert store.vault.open(encrypted) == {"CF_DNS_API_TOKEN": "private-dns-token"}
    assert (tmp_path / "vault/vault.key").stat().st_mode & 0o777 == 0o600
    for url in (
        "/api/v1/certificates",
        "/api/v1/certificates/providers",
        "/api/v1/certificates/" + profile["id"],
    ):
        response = browser.get(url)
        assert "private-dns-token" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert TestClient(browser.app).get(url).status_code == 401
    invalid = browser.post(
        "/api/v1/certificates/providers",
        json={
            "name": "DNS",
            "provider": "cloudflare",
            "credentials": {"CF_DNS_API_TOKEN": "sensitive"},
            "unexpected": "secret-extra",
        },
    )
    assert (
        invalid.status_code == 422
        and "secret-extra" not in invalid.text
        and "sensitive" not in invalid.text
    )
    assert browser.delete("/api/v1/certificates/providers/" + provider["id"]).status_code == 409


def test_certificate_import_export_validation_and_lost_vault_key(tmp_path):
    browser = client(tmp_path)
    cert, key = pair(("*.example.com", "example.com"))
    imported = browser.post(
        "/api/v1/certificates/import", json={"name": "Imported", "cert_pem": cert, "key_pem": key}
    )
    assert imported.status_code == 201, imported.text
    base = "/api/v1/certificates/" + imported.json()["id"]
    assert key not in browser.get(base).text
    assert "key_pem" not in browser.get(base + "/material").json()
    assert browser.get(base + "/material?include_private_key=true").json()["key_pem"] == key
    invalid = browser.post(
        "/api/v1/certificates/import",
        json={"name": "Mismatch", "cert_pem": cert, "key_pem": pair()[1]},
    )
    assert invalid.status_code == 422
    assert browser.post(base + "/renew", json={}).status_code == 409
    (tmp_path / "vault/vault.key").unlink()
    assert browser.get(base + "/material").status_code == 503
    assert not (tmp_path / "vault/vault.key").exists()


def test_duplicate_jobs_terms_provider_fields_and_ca_allowlist(tmp_path):
    browser = client(tmp_path)
    profile, provider = create_profile(browser)
    base = "/api/v1/certificates/" + profile["id"]
    assert browser.post(base + "/issue", json={}).status_code == 202
    assert browser.post(base + "/issue", json={}).status_code == 409
    assert browser.delete(base).status_code == 409
    for domains in (["bad;host"], ["*.example.com", "*.EXAMPLE.COM"], ["../file"]):
        result = browser.post(
            "/api/v1/certificates",
            json={
                "name": "Invalid",
                "domains": domains,
                "email": "operator@example.com",
                "provider_id": provider["id"],
                "accept_terms": True,
            },
        )
        assert result.status_code == 422
    assert (
        browser.post(
            "/api/v1/certificates/providers",
            json={
                "name": "Invalid",
                "provider": "cloudflare",
                "credentials": {"LD_PRELOAD": "secret"},
            },
        ).status_code
        == 409
    )
    result = browser.post(
        "/api/v1/certificates",
        json={
            "name": "Invalid",
            "domains": ["example.com"],
            "email": "operator@example.com",
            "provider_id": provider["id"],
            "accept_terms": False,
        },
    )
    assert result.status_code == 422
    assert (
        browser.post(
            "/api/v1/certificates",
            json={
                "name": "Invalid",
                "domains": ["example.com"],
                "email": "operator@example.com",
                "provider_id": provider["id"],
                "accept_terms": True,
                "directory_url": "https://unapproved.example/directory",
            },
        ).status_code
        == 409
    )


@pytest.mark.asyncio
async def test_worker_preserves_active_material_and_recovers_interrupted_job(tmp_path):
    browser = client(tmp_path)
    profile, _ = create_profile(browser)
    store = browser.app.state.certificates
    worker = CertificateWorker(store, browser.app.state.agent_connections)
    cert, key = pair()

    async def issue(args, env, work, lock_fd):
        assert "private-dns-token" not in str(args)
        assert env["CF_DNS_API_TOKEN"] == "private-dns-token"
        destination = work / "certificates"
        destination.mkdir(mode=0o700, exist_ok=True)
        for name, value in (("localhost.crt", cert), ("localhost.key", key)):
            path = destination / name
            path.write_text(value)
            path.chmod(0o600)

    worker.execute = issue
    store.queue(profile["id"], "issue")
    with store.vault.lock("worker.lock", blocking=False) as lock_fd:
        assert await worker.run_one(lock_fd)
    first = store.detail(profile["id"])
    assert first["certificate"]["status"] == "issued"
    assert len(first["versions"]) == 1
    store.queue(profile["id"], "renew")
    with store.vault.lock("worker.lock", blocking=False) as lock_fd:
        await worker.run_one(lock_fd)
    assert store.detail(profile["id"])["jobs"][0]["status"] == "skipped"
    job = store.queue(profile["id"], "renew", force=True)
    worker.execute = AsyncMock(side_effect=asyncio.CancelledError())
    with store.vault.lock("worker.lock", blocking=False) as lock_fd:
        with pytest.raises(asyncio.CancelledError):
            await worker.run_one(lock_fd)
    assert store.export(profile["id"])["key_pem"] == key
    assert store.detail(profile["id"])["jobs"][0]["status"] == "interrupted"
    with store.session.begin() as db:
        db.get(CertificateJob, job["id"]).status = "running"
        db.get(ManagedCertificate, profile["id"]).active_job_id = job["id"]
    worker.recover()
    assert store.detail(profile["id"])["certificate"]["active_job_id"] is None


def test_renewal_window_handles_short_lived_certificates(tmp_path):
    store = client(tmp_path).app.state.certificates
    assert store.due(SimpleNamespace(not_before=1, expires_at=1 + 6 * 86400), now=1 + 3 * 86400)
    assert not store.due(
        SimpleNamespace(not_before=1, expires_at=1 + 90 * 86400), now=1 + 50 * 86400
    )
    assert store.next_check(SimpleNamespace(not_before=1, expires_at=241)) < time() + 41


def test_certificate_deployment_uses_owned_paths_and_records_command_atomically(tmp_path):
    browser = client(tmp_path)
    cert, key = pair(("*.example.com", "example.com"))
    imported = browser.post(
        "/api/v1/certificates/import", json={"name": "Wildcard", "cert_pem": cert, "key_pem": key}
    ).json()
    base = "/api/v1/certificates/" + imported["id"]
    created = browser.post("/api/v1/servers", json={"name": "certificate-target"}).json()
    server_id = created["server"]["id"]
    scan = scan_result_payload()
    browser.post("/api/v1/agents/scan", json={"token": created["agent_token"], **scan})
    target = browser.post(
        base + "/targets",
        json={
            "server_id": server_id,
            "domain": "www.example.com",
            "cert_name": "_.example.com",
            "reload": "both",
        },
    ).json()
    result = browser.post(base + "/targets/" + target["id"] + "/deploy")
    assert result.status_code == 201, result.text
    command = result.json()["command"]
    assert command["body"]["cert_path"] == scan["nginx"]["certificate_dir"] + "/_.example.com.pem"
    assert command["body"]["key_pem"] == key
    detail = browser.get(base).json()
    assert detail["targets"][0]["command_id"] == command["id"]
    assert key not in json.dumps(detail)
    assert browser.post(base + "/targets/" + target["id"] + "/deploy").status_code == 409
    assert (
        browser.post(
            base + "/targets",
            json={
                "server_id": server_id,
                "domain": "two.level.example.com",
                "cert_name": "invalid",
            },
        ).status_code
        == 409
    )
    other = browser.post(
        "/api/v1/certificates/import", json={"name": "Duplicate", "cert_pem": cert, "key_pem": key}
    ).json()
    assert (
        browser.post(
            "/api/v1/certificates/" + other["id"] + "/targets",
            json={
                "server_id": server_id,
                "domain": "www.example.com",
                "cert_name": "_.example.com",
            },
        ).status_code
        == 409
    )


def test_concurrent_requests_create_only_one_job(tmp_path):
    browser = client(tmp_path)
    profile, _ = create_profile(browser)
    store = browser.app.state.certificates
    barrier = Barrier(4)

    def enqueue(_):
        barrier.wait(timeout=5)
        try:
            return store.queue(profile["id"], "issue")["id"]
        except CertificateError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(enqueue, range(4)))
    assert sum(bool(result) for result in results) == 1
    assert len(store.detail(profile["id"])["jobs"]) == 1


def test_manual_and_automatic_deployment_share_an_atomic_claim(tmp_path):
    from open_node.services.inventory import CommandModel
    from sqlalchemy import select

    browser = client(tmp_path)
    store = browser.app.state.certificates
    cert, key = pair()
    profile = browser.post(
        "/api/v1/certificates/import",
        json={
            "name": "Concurrent",
            "cert_pem": cert,
            "key_pem": key,
        },
    ).json()
    server = browser.post("/api/v1/servers", json={"name": "target"}).json()
    browser.post(
        "/api/v1/agents/scan",
        json={
            "token": server["agent_token"],
            **scan_result_payload(),
        },
    )
    target = browser.post(
        f"/api/v1/certificates/{profile['id']}/targets",
        json={
            "server_id": server["server"]["id"],
            "domain": "localhost",
            "cert_name": "localhost",
        },
    ).json()
    barrier = Barrier(4)

    def deploy(_):
        barrier.wait(timeout=5)
        try:
            return store.deploy(profile["id"], target["id"]).id
        except CertificateError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(deploy, range(4)))
    assert sum(bool(result) for result in results) == 1
    with store.session() as db:
        commands = list(
            db.scalars(
                select(CommandModel).where(
                    CommandModel.path == "/api/child/cert/deploy",
                )
            )
        )
        assert len(commands) == 1


@pytest.mark.asyncio
async def test_worker_reads_rotated_credentials_and_keeps_issuance_success_on_dispatch_error(
    tmp_path,
):
    browser = client(tmp_path)
    profile, provider = create_profile(browser)
    store = browser.app.state.certificates
    store.queue(profile["id"], "issue")
    response = browser.put(
        "/api/v1/certificates/providers/" + provider["id"],
        json={
            "name": "Rotated",
            "provider": "cloudflare",
            "credentials": {"CF_DNS_API_TOKEN": "rotated"},
        },
    )
    assert response.status_code == 200
    worker = CertificateWorker(store, browser.app.state.agent_connections)
    cert, key = pair()

    async def obtain(row, credentials, job, lock_fd):
        from open_node.services.certificate_vault import material

        assert store.vault.open(credentials.credentials)["CF_DNS_API_TOKEN"] == "rotated"
        return material(cert, key, row.domains)

    worker.obtain = obtain
    worker.deploy_pending = AsyncMock(side_effect=ConnectionError("offline"))
    with store.vault.lock("worker.lock") as lock_fd:
        with pytest.raises(ConnectionError):
            await worker.run_one(lock_fd)
    assert store.detail(profile["id"])["jobs"][0]["status"] == "succeeded"
    assert store.export(profile["id"])["cert_pem"] == cert


@pytest.mark.asyncio
async def test_deployment_worker_runs_without_acme_binary(tmp_path):
    store = client(tmp_path).app.state.certificates
    store.settings.certificate_lego_binary = None
    worker = CertificateWorker(store, SimpleNamespace())
    worker.deploy_pending = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await worker.run()
    worker.deploy_pending.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_subprocess_permissions_inherited_lock_and_cancellation(tmp_path):
    store = client(tmp_path).app.state.certificates
    worker = CertificateWorker(store, SimpleNamespace())
    work = store.vault.root / "subprocess"
    store.vault.prepare()
    work.mkdir(mode=0o700)
    environment = {"PATH": os.defpath}
    with store.vault.lock("worker.lock") as lock_fd:
        await worker.execute(
            [
                sys.executable,
                "-c",
                "import os,pathlib; os.fstat(int(os.environ['LOCK_FD'])); "
                "pathlib.Path('private').write_text('ok')",
            ],
            {**environment, "LOCK_FD": str(lock_fd)},
            work,
            lock_fd,
        )
        assert (work / "private").stat().st_mode & 0o777 == 0o600
        task = asyncio.create_task(
            worker.execute(
                [
                    sys.executable,
                    "-c",
                    "import os,pathlib,time; pathlib.Path('pid').write_text(str(os.getpid())); "
                    "time.sleep(60)",
                ],
                environment,
                work,
                lock_fd,
            )
        )
        async with asyncio.timeout(5):
            while not (work / "pid").exists():
                await asyncio.sleep(0.02)
        pid = int((work / "pid").read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        with pytest.raises(BlockingIOError), store.vault.lock("worker.lock", blocking=False):
            pass
        with pytest.raises(CertificateError, match="output limit"):
            await worker.execute(
                [sys.executable, "-c", "print('x'*300000)"], environment, work, lock_fd
            )
    assert (work / "last-job.log").stat().st_size == 262144
    assert (work / "last-job.log").stat().st_mode & 0o777 == 0o600
    with store.vault.lock("worker.lock", blocking=False):
        pass


@pytest.mark.asyncio
async def test_cancellation_during_cleanup_is_not_swallowed(tmp_path, monkeypatch):
    store = client(tmp_path).app.state.certificates
    worker = CertificateWorker(store, SimpleNamespace())
    store.vault.prepare()

    async def cancelled_after_cleanup(task):
        await task
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "shield", cancelled_after_cleanup)
    with store.vault.lock("worker.lock") as lock_fd, pytest.raises(asyncio.CancelledError):
        await worker.execute(
            [sys.executable, "-c", "pass"], {"PATH": os.defpath}, store.vault.root, lock_fd
        )


def test_existing_database_cannot_create_a_replacement_vault_key(tmp_path):
    browser = client(tmp_path)
    create_profile(browser)
    vault = tmp_path / "vault"
    (vault / "vault.key").unlink()
    (vault / "vault.initialized").unlink()
    restarted = client(tmp_path)
    for instance in (browser, restarted):
        response = instance.post(
            "/api/v1/certificates/providers",
            json={
                "name": "New provider",
                "provider": "cloudflare",
                "credentials": {"CF_DNS_API_TOKEN": "new-token"},
            },
        )
        assert response.status_code == 503
    assert not (vault / "vault.key").exists()


def test_versions_are_scoped_and_expired_imports_rejected(tmp_path):
    browser = client(tmp_path)
    cert, key = pair(expired=True)
    assert (
        browser.post(
            "/api/v1/certificates/import",
            json={
                "name": "Expired",
                "cert_pem": cert,
                "key_pem": key,
            },
        ).status_code
        == 422
    )
    cert, key = pair()
    first, second = [
        browser.post(
            "/api/v1/certificates/import",
            json={
                "name": name,
                "cert_pem": cert,
                "key_pem": key,
            },
        ).json()
        for name in ("First", "Second")
    ]
    assert (
        browser.post(
            f"/api/v1/certificates/{first['id']}/versions/{second['version_id']}/activate"
        ).status_code
        == 409
    )
