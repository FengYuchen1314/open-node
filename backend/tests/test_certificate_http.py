import asyncio
import os
from pathlib import Path
from time import time
from unittest.mock import AsyncMock

import pytest
from conftest import authenticated_client
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.certificate_http import WebrootChallenges, harden_work
from open_node.services.certificate_vault import CertificateVault
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import CertificateJob, CertificateStore, ManagedCertificate
from sqlalchemy import text
from test_certificates import create_profile, pair

BASE = "/api/v1/certificates"
TOKEN = "t" * 43
RESPONSE = TOKEN + "." + "a" * 43


def browser(tmp_path):
    root = tmp_path / "site"
    root.mkdir(mode=0o755)
    return authenticated_client(
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
                certificate_state_dir=tmp_path / "vault",
                certificate_lego_binary=Path("/bin/true"),
                certificate_http_address="127.0.0.1:8082",
                certificate_webroots={"site": root},
            )
        )
    )


def payload(challenge="standalone", **extra):
    return {
        "name": "HTTP website",
        "domains": ["localhost"],
        "email": "operator@example.com",
        "challenge_type": challenge,
        "accept_terms": True,
        **({"webroot_id": "site"} if challenge == "webroot" else {}),
        **extra,
    }


def create(client, challenge="standalone"):
    response = client.post(BASE, json=payload(challenge))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("challenge", ["standalone", "webroot"])
def test_http_profiles_without_dns_secrets_and_capability_paths(tmp_path, challenge):
    client = browser(tmp_path)
    row = create(client, challenge)
    assert row["provider_id"] is None and row["challenge_type"] == challenge
    assert client.get(BASE + "/providers").json()["providers"] == []
    capabilities = client.get(BASE + "/capabilities")
    assert capabilities.json()["challenge_types"] == ["dns", "standalone", "webroot"]
    assert capabilities.json()["webroots"] == ["site"]
    assert str(tmp_path) not in capabilities.text and "8082" not in capabilities.text
    assert (
        client.patch(
            BASE + "/" + row["id"], json={"name": row["name"], "auto_renew": True}
        ).status_code
        == 200
    )
    assert client.post(BASE + "/" + row["id"] + "/issue", json={}).status_code == 202


@pytest.mark.parametrize(
    "extra",
    [
        {"domains": ["*.example.com"]},
        {"provider_id": "00000000-0000-0000-0000-000000000001"},
        {"webroot_id": "site"},
        {"http_address": "0.0.0.0:22"},
        {"accept_terms": 1},
        {"accept_terms": "true"},
        {"email": "a/b@example.com"},
        {"challenge_type": "tls"},
    ],
)
def test_http_invalid_combinations_do_not_create_profiles(tmp_path, extra):
    client = browser(tmp_path)
    response = client.post(BASE, json=payload(**extra))
    assert response.status_code == 422, response.text
    assert client.get(BASE).json()["certificates"] == []


@pytest.mark.parametrize("identifier,status", [("../site", 422), ("missing", 409), (None, 422)])
def test_webroot_selection_is_host_controlled(tmp_path, identifier, status):
    client = browser(tmp_path)
    response = client.post(BASE, json=payload("webroot", webroot_id=identifier))
    assert response.status_code == status


def test_disabling_http_challenge_rejects_new_jobs(tmp_path):
    client = browser(tmp_path)
    row = create(client)
    client.app.state.certificates.settings.certificate_http_address = None
    assert client.post(BASE + "/" + row["id"] + "/issue", json={}).status_code == 409
    assert client.post(BASE, json=payload()).status_code == 409


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "localhost:80",
        "0.0.0.0:0",
        ":65536",
        "::1:80",
        "[127.0.0.1]:80",
        "127.0.0.1:abc",
    ],
)
def test_invalid_listener_address(address):
    with pytest.raises(ValueError):
        Settings(certificate_http_address=address)


@pytest.mark.parametrize("address", ["127.0.0.1:8082", ":80", "[::1]:8082", "0.0.0.0:80"])
def test_valid_listener_address(address):
    assert Settings(certificate_http_address=address).certificate_http_address == address


@pytest.mark.parametrize(
    "roots",
    [
        {"bad/id": "/srv/www"},
        {"site": "/"},
        {"site": "relative"},
        {"site": "/srv/../etc"},
        {"a": "/srv/www", "b": "/srv/www"},
    ],
)
def test_invalid_webroot_settings(roots):
    with pytest.raises(ValueError):
        Settings(certificate_webroots=roots)


def owned_root(tmp_path):
    vault = CertificateVault(tmp_path / "vault")
    root = tmp_path / "site"
    root.mkdir(mode=0o755)
    manager = WebrootChallenges(vault)
    return manager, root


def test_webroot_cleanup_preserves_website_and_recovers_after_restart(tmp_path):
    manager, root = owned_root(tmp_path)
    (root / "index.html").write_text("existing website")
    manager.prepare(root)
    challenge = root / ".well-known/acme-challenge" / TOKEN
    challenge.write_text(RESPONSE)
    WebrootChallenges(manager.vault).recover()
    assert not challenge.exists()
    assert (root / "index.html").read_text() == "existing website"
    manager.prepare(root)
    record = next(manager.registry.glob("*.json"))
    assert record.stat().st_mode & 0o777 == 0o600


def test_does_not_claim_nonempty_unowned_challenge_directory(tmp_path):
    manager, root = owned_root(tmp_path)
    directory = root / ".well-known/acme-challenge"
    directory.mkdir(parents=True)
    (directory / TOKEN).write_text(RESPONSE)
    with pytest.raises(ValueError, match="unowned"):
        manager.prepare(root)
    assert (directory / TOKEN).read_text() == RESPONSE


@pytest.mark.parametrize(
    "unexpected", ["ordinary", "symlink", "hardlink", "fifo", "directory", "wrong-response"]
)
def test_cleanup_refuses_unknown_or_linked_content_without_deleting_anything(tmp_path, unexpected):
    manager, root = owned_root(tmp_path)
    manager.prepare(root)
    directory = root / ".well-known/acme-challenge"
    good = directory / TOKEN
    good.write_text(RESPONSE)
    outsider = tmp_path / "untouched"
    outsider.write_text("private original")
    bad = directory / ("x" * 43)
    if unexpected == "symlink":
        bad.symlink_to(outsider)
    elif unexpected == "hardlink":
        os.link(outsider, bad)
    elif unexpected == "fifo":
        os.mkfifo(bad)
    elif unexpected == "directory":
        bad.mkdir()
    elif unexpected == "ordinary":
        bad = directory / "index.html"
        bad.write_text("a website")
    else:
        bad.write_text("not an ACME response")
    with pytest.raises(ValueError):
        manager.cleanup(root)
    assert good.exists() and bad.lstat()
    assert outsider.read_text() == "private original"


@pytest.mark.parametrize("component", ["root", "wellknown", "challenge"])
def test_webroot_symlink_components_are_rejected(tmp_path, component):
    manager, root = owned_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == "root":
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
    elif component == "wellknown":
        (root / ".well-known").symlink_to(outside, target_is_directory=True)
    else:
        (root / ".well-known").mkdir()
        (root / ".well-known/acme-challenge").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        manager.prepare(root)
    assert not list(outside.iterdir())


def test_webroot_cannot_expose_private_vault_or_replace_owned_directory(tmp_path):
    manager, root = owned_root(tmp_path)
    with pytest.raises(ValueError):
        manager.prepare(tmp_path)
    manager.prepare(root)
    challenge = root / ".well-known/acme-challenge"
    challenge.rename(root / ".well-known/retired")
    challenge.mkdir()
    with pytest.raises(ValueError, match="ownership"):
        manager.prepare(root)


@pytest.mark.parametrize("mode", ["standalone", "webroot"])
@pytest.mark.asyncio
async def test_http_worker_issue_renewal_scheduling_and_interruption(tmp_path, mode):
    client = browser(tmp_path)
    profile = create(client, mode)
    store = client.app.state.certificates
    worker = CertificateWorker(store, client.app.state.agent_connections)
    certificates = []

    async def issue(args, env, work, lock_fd):
        assert "--http" in args and "--dns" not in args
        assert "--dns.resolvers" not in args
        assert set(env) == {"PATH", "HOME", "LANG"}
        assert (
            args[args.index("--http.port") + 1] == "127.0.0.1:8082"
            if mode == "standalone"
            else args[args.index("--http.webroot") + 1] == str(tmp_path / "site")
        )
        cert, key = pair()
        certificates.append(cert)
        destination = work / "certificates"
        destination.mkdir(mode=0o700, exist_ok=True)
        for name, value in (("localhost.crt", cert), ("localhost.key", key)):
            (destination / name).write_text(value)
        if mode == "webroot":
            (tmp_path / "site/.well-known/acme-challenge" / TOKEN).write_text(RESPONSE)

    worker.execute = issue
    store.queue(profile["id"], "issue")
    with store.vault.lock("worker.lock") as fd:
        await worker.run_one(fd)
    assert store.detail(profile["id"])["certificate"]["status"] == "issued"
    assert store.export(profile["id"])["cert_pem"] == certificates[-1]
    for path in store.vault.root.rglob("*"):
        assert not path.stat().st_mode & 0o077
    with store.session.begin() as db:
        row = db.get(ManagedCertificate, profile["id"])
        row.not_before, row.expires_at, row.next_attempt = time() - 200, time() + 10, 0
    worker.schedule()
    assert store.detail(profile["id"])["jobs"][0]["kind"] == "renew"
    with store.vault.lock("worker.lock") as fd:
        await worker.run_one(fd)
    assert len(store.detail(profile["id"])["versions"]) == 2
    assert store.export(profile["id"])["cert_pem"] == certificates[-1]
    job = store.queue(profile["id"], "renew", force=True)
    worker.execute = AsyncMock(side_effect=asyncio.CancelledError)
    with store.vault.lock("worker.lock") as fd:
        with pytest.raises(asyncio.CancelledError):
            await worker.run_one(fd)
    assert store.detail(profile["id"])["jobs"][0]["status"] == "interrupted"
    assert len(store.detail(profile["id"])["versions"]) == 2
    if mode == "webroot":
        assert not list((tmp_path / "site/.well-known/acme-challenge").iterdir())
    with store.session.begin() as db:
        db.get(CertificateJob, job["id"]).status = "running"
        db.get(ManagedCertificate, profile["id"]).active_job_id = job["id"]
    worker.recover()
    assert store.detail(profile["id"])["certificate"]["active_job_id"] is None


def test_legacy_certificate_schema_upgrade_preserves_dns_and_imported_profiles(tmp_path):
    client = browser(tmp_path)
    profile, provider = create_profile(client)
    cert, key = pair()
    imported = client.post(
        BASE + "/import", json={"name": "PEM", "cert_pem": cert, "key_pem": key}
    ).json()
    store = client.app.state.certificates
    with store.engine.begin() as connection:
        connection.execute(text("ALTER TABLE managed_certificates DROP COLUMN challenge_type"))
        connection.execute(text("ALTER TABLE managed_certificates DROP COLUMN webroot_id"))
    restored = CertificateStore(store.settings, store.inventory)
    assert restored.detail(profile["id"])["certificate"]["challenge_type"] == "dns"
    assert restored.detail(profile["id"])["certificate"]["provider_id"] == provider["id"]
    assert restored.export(imported["id"])["cert_pem"] == cert
    assert restored.detail(imported["id"])["certificate"]["directory_url"] is None
    assert not restored.detail(imported["id"])["certificate"]["auto_renew"]
    CertificateStore(store.settings, store.inventory)


def test_private_work_hardening_refuses_links(tmp_path):
    root = tmp_path / "work"
    root.mkdir(mode=0o700)
    path = root / "key"
    path.write_text("private")
    path.chmod(0o644)
    harden_work(root)
    assert path.stat().st_mode & 0o777 == 0o600
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    outside.chmod(0o644)
    (root / "linked").symlink_to(outside)
    with pytest.raises(ValueError):
        harden_work(root)
    assert outside.stat().st_mode & 0o777 == 0o644


def test_http_eab_only_catalog_detects_lost_vault_key_on_restart(tmp_path):
    client = browser(tmp_path)
    data = payload(eab_kid="fixture", eab_hmac_key="private-eab-key")
    assert client.post(BASE, json=data).status_code == 201
    store = client.app.state.certificates
    (store.vault.root / "vault.key").unlink()
    (store.vault.root / "vault.initialized").unlink()
    restored = create_app(store.settings)
    response = authenticated_client(restored).post(BASE, json=data)
    assert response.status_code == 503
    assert not (store.vault.root / "vault.key").exists()
    assert "private-eab-key" not in response.text


@pytest.mark.asyncio
async def test_queued_http_job_fails_cleanly_when_host_disables_mode(tmp_path):
    client = browser(tmp_path)
    profile = create(client)
    store = client.app.state.certificates
    store.queue(profile["id"], "issue")
    store.settings.certificate_http_address = None
    worker = CertificateWorker(store, client.app.state.agent_connections)
    worker.execute = AsyncMock()
    with store.vault.lock("worker.lock") as fd:
        assert await worker.run_one(fd)
    detail = store.detail(profile["id"])
    assert detail["jobs"][0]["status"] == "failed"
    assert detail["certificate"]["active_job_id"] is None
    worker.execute.assert_not_called()
