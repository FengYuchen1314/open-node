import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from acme import messages
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from open_node.domain.certificates import CertificateImport, CertificateRevoke
from open_node.services import certificate_acme
from open_node.services.certificate_acme import account_paths, fingerprint, signing_key
from open_node.services.certificate_vault import material
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import (
    CertificateError,
    CertificateJob,
    CertificateStore,
    CertificateTarget,
    ManagedCertificate,
)
from open_node.services.inventory import CommandModel
from sqlalchemy import text
from test_certificates import client, create_profile, pair
from test_inventory import scan_result_payload


def setup(tmp_path, *, issued=False):
    browser = client(tmp_path)
    profile, _ = create_profile(browser)
    store = browser.app.state.certificates
    worker = CertificateWorker(store, browser.app.state.agent_connections)
    if issued:
        with store.write() as db:
            store.publish(db, db.get(ManagedCertificate, profile["id"]), material(*pair()))
    return browser, store, worker, "/api/v1/certificates/" + profile["id"]


async def run(worker):
    with worker.store.vault.lock("worker.lock", blocking=False) as fd:
        assert await worker.run_one(fd)


@pytest.mark.asyncio
async def test_pre_registration_update_executes_and_keeps_eab_private(tmp_path):
    browser, store, worker, base = setup(tmp_path)
    response = browser.post(
        base + "/account",
        json={
            "email": "new@example.com",
            "eab_action": "replace",
            "eab_kid": "private-key-id",
            "eab_hmac_key": "private-hmac-key",
        },
    )
    assert response.status_code == 202, response.text
    assert "parameters" not in response.json()
    assert TestClient(browser.app).post(base + "/account", json={}).status_code == 401
    assert browser.post(base + "/account", json={"email": "other@example.com"}).status_code == 409
    await run(worker)
    detail = browser.get(base).json()
    assert detail["account"]["email"] == "new@example.com"
    assert detail["account"]["state"] == "not_registered"
    assert detail["account"]["eab_configured"]
    assert detail["jobs"][0]["status"] == "succeeded"
    assert "private-hmac-key" not in json.dumps(detail)
    with store.session() as db:
        row = db.get(ManagedCertificate, profile_id(base))
        assert store.vault.open(row.eab)["kid"] == "private-key-id"
        assert row.account_email is None
    assert not list(store.vault.root.glob("*/jobs/*/request.json"))
    for path in store.vault.root.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    assert (
        browser.post(
            base + "/account", json={"email": "third@example.com", "eab_action": "remove"}
        ).status_code
        == 202
    )
    await run(worker)
    assert not browser.get(base).json()["account"]["eab_configured"]


def profile_id(base):
    return base.rsplit("/", 1)[1]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "bad/path@example.com"},
        {"email": "new@example.com", "eab_action": "replace"},
        {"email": "new@example.com", "eab_hmac_key": "secret"},
        {"email": "new@example.com", "eab_action": "remove", "eab_kid": "secret"},
        {"email": "new@example.com", "extra": "secret"},
    ],
)
def test_account_input_rejects_invalid_or_ambiguous_secrets(tmp_path, payload):
    browser, _, _, base = setup(tmp_path)
    response = browser.post(base + "/account", json=payload)
    assert response.status_code == 422
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_account_failure_retry_is_latest_only_and_retains_sealed_parameters(tmp_path):
    browser, store, worker, base = setup(tmp_path)
    first = browser.post(
        base + "/account",
        json={
            "email": "new@example.com",
            "eab_action": "replace",
            "eab_kid": "secret-id",
            "eab_hmac_key": "secret-hmac",
        },
    ).json()
    original = worker.administer
    worker.administer = AsyncMock(side_effect=RuntimeError("secret-response"))
    await run(worker)
    detail = browser.get(base).json()
    assert detail["account"]["email"] == "operator@example.com"
    assert detail["account"]["pending_email"] == "new@example.com"
    assert detail["account"]["retry_job_id"] == first["id"]
    assert "secret-response" not in json.dumps(detail)
    endpoint = base + "/account/jobs/" + first["id"] + "/retry"
    retry = browser.post(endpoint)
    assert retry.status_code == 202
    assert browser.post(endpoint).status_code == 409
    worker.administer = original
    await run(worker)
    assert browser.get(base).json()["account"]["retry_job_id"] is None
    assert browser.post(endpoint).status_code == 409
    with store.session() as db:
        assert (
            db.get(CertificateJob, retry.json()["id"]).parameters
            == db.get(CertificateJob, first["id"]).parameters
        )


@pytest.mark.parametrize("kind", ["account", "revoke"])
@pytest.mark.asyncio
async def test_administration_cancellation_and_restart_preserve_claim(tmp_path, kind):
    browser, store, worker, base = setup(tmp_path, issued=True)
    detail = browser.get(base).json()
    version = detail["versions"][0]
    if kind == "account":
        job = browser.post(base + "/account", json={"email": "new@example.com"}).json()
    else:
        job = browser.post(
            base + "/versions/" + version["id"] + "/revoke", json={"confirm": True}
        ).json()
    worker.administer = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await run(worker)
    detail = browser.get(base).json()
    assert detail["certificate"]["active_job_id"] == job["id"]
    assert detail["jobs"][0]["status"] == "queued"
    with store.write() as db:
        db.get(CertificateJob, job["id"]).status = "running"
    worker.recover()
    worker.administer = AsyncMock(
        return_value={
            "email": "new@example.com",
            "storage_email": "operator@example.com",
            "registered": True,
            "fingerprint": version["fingerprint"],
        }
    )
    await run(worker)
    detail = browser.get(base).json()
    assert detail["jobs"][0]["status"] == "succeeded"
    assert detail["certificate"]["active_job_id"] is None
    assert detail["certificate"]["status"] == ("issued" if kind == "account" else "revoked")


@pytest.mark.asyncio
async def test_revocation_ledger_covers_legacy_copies_retry_and_survives_deletion(tmp_path):
    browser, store, worker, base = setup(tmp_path, issued=True)
    data = store.export(profile_id(base))
    imported = browser.post(
        "/api/v1/certificates/import",
        json={"name": "Copy", "cert_pem": data["cert_pem"], "key_pem": data["key_pem"]},
    ).json()
    copy_base = "/api/v1/certificates/" + imported["id"]
    with store.write() as db:
        db.execute(text("UPDATE certificate_versions SET fingerprint = NULL"))
    version = browser.get(base).json()["versions"][0]
    endpoint = base + "/versions/" + version["id"] + "/revoke"
    response = browser.post(endpoint, json={"confirm": True, "reason": 1})
    assert response.status_code == 202, response.text
    assert browser.post(endpoint, json={"confirm": True}).status_code == 409
    for path in (base, copy_base):
        detail = browser.get(path).json()
        assert detail["certificate"]["status"] == "revocation_pending"
        assert not detail["certificate"]["auto_renew"]
        assert detail["versions"][0]["revocation"]["status"] == "pending"
    worker.administer = AsyncMock(side_effect=TimeoutError("secret"))
    await run(worker)
    assert browser.get(copy_base).json()["versions"][0]["revocation"]["status"] == "unknown"
    assert browser.post(endpoint, json={"confirm": True}).status_code == 202
    worker.administer = AsyncMock(
        return_value={"fingerprint": fingerprint(data), "already_revoked": True}
    )
    await run(worker)
    assert browser.get(base).json()["certificate"]["status"] == "revoked"
    assert browser.post(base + "/versions/" + version["id"] + "/activate").status_code == 409
    assert browser.patch(base, json={"name": "Website", "auto_renew": True}).status_code == 409
    forced = browser.post(base + "/renew", json={})
    assert forced.status_code == 202 and forced.json()["force"]
    worker.obtain = AsyncMock(return_value=material(*pair()))
    await run(worker)
    assert browser.get(base).json()["certificate"]["status"] == "issued"
    for path in (base, copy_base):
        assert browser.delete(path).status_code == 200
    assert (
        browser.post(
            "/api/v1/certificates/import",
            json={"name": "Again", "cert_pem": data["cert_pem"], "key_pem": data["key_pem"]},
        ).status_code
        == 409
    )
    assert CertificateStore(store.settings, store.inventory).list() == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"confirm": False},
        {"confirm": 1},
        {"confirm": "true"},
        {"confirm": True, "reason": True},
        {"confirm": True, "reason": "1"},
        {"confirm": True, "reason": 2},
        {"confirm": True, "reason": 6},
        {"confirm": True, "directory_url": "http://example.com/directory"},
    ],
)
def test_revocation_requires_explicit_confirmation_and_supported_reason(tmp_path, payload):
    browser, _, _, base = setup(tmp_path, issued=True)
    version = browser.get(base).json()["versions"][0]
    response = browser.post(base + "/versions/" + version["id"] + "/revoke", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_historical_revocation_preserves_current_renewal_and_rejects_wrong_ca(tmp_path):
    browser, store, worker, base = setup(tmp_path, issued=True)
    version = browser.get(base).json()["versions"][0]
    with store.write() as db:
        store.publish(db, db.get(ManagedCertificate, profile_id(base)), material(*pair()))
    endpoint = base + "/versions/" + version["id"] + "/revoke"
    assert (
        browser.post(
            endpoint, json={"confirm": True, "directory_url": "https://other.example/directory"}
        ).status_code
        == 409
    )
    assert browser.post(endpoint, json={"confirm": True}).status_code == 202
    worker.administer = AsyncMock(return_value={"fingerprint": version["fingerprint"]})
    await run(worker)
    detail = browser.get(base).json()
    assert detail["certificate"]["status"] == "issued" and detail["certificate"]["auto_renew"]
    assert detail["versions"][0]["revocation"] is None


def test_imported_certificate_needs_an_allowed_ca_and_concurrent_import_is_blocked(tmp_path):
    browser, store, _, base = setup(tmp_path, issued=True)
    data = store.export(profile_id(base))
    payload = CertificateImport(name="Copy", cert_pem=data["cert_pem"], key_pem=data["key_pem"])
    copy = store.import_certificate(payload)
    copy_base = "/api/v1/certificates/" + copy["id"]
    endpoint = copy_base + "/versions/" + copy["version_id"] + "/revoke"
    assert browser.post(endpoint, json={"confirm": True}).status_code == 409
    barrier = Barrier(2)

    def revoke():
        barrier.wait()
        return store.queue_revocation(
            copy["id"],
            copy["version_id"],
            CertificateRevoke(
                confirm=True, directory_url=store.settings.certificate_acme_directories[0]
            ),
        )

    def duplicate():
        barrier.wait()
        try:
            return store.import_certificate(payload)
        except CertificateError:
            return None

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(revoke), pool.submit(duplicate)
        assert a.result()["status"] == "queued"
        result = b.result()
    if result:
        assert store.detail(result["id"])["versions"][0]["revocation"]["status"] == "pending"


@pytest.mark.parametrize(
    "key",
    [
        ec.SECP256R1(),
        ec.SECP384R1(),
        ec.SECP521R1(),
        None,
    ],
)
def test_signing_keys_are_supported(key):
    private = (
        ec.generate_private_key(key)
        if key
        else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    )
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    jwk, alg = signing_key(pem)
    signature = alg.sign(jwk.key, b"payload")
    assert alg.verify(jwk.public_key().key, b"payload", signature)


@pytest.mark.parametrize("local_state", ["registered", "key_only", "corrupt"])
def test_account_contact_uses_original_key_and_storage_alias(tmp_path, monkeypatch, local_state):
    _, store, _, base = setup(tmp_path)
    request = {
        "profile_work": str(store.vault.root / profile_id(base)),
        "directory_url": store.settings.certificate_acme_directories[0],
        "email": "new@example.com",
        "storage_email": "operator@example.com",
        "eab_action": "keep",
    }
    account, key = account_paths(
        store.vault,
        Path(request["profile_work"]),
        request["directory_url"],
        request["storage_email"],
    )
    store.vault.write(key, pair()[1].encode())
    key_hash = hashlib.sha256(key.read_bytes()).hexdigest()
    if local_state != "key_only":
        store.vault.write(
            account,
            b"[]"
            if local_state == "corrupt"
            else b'{"registration":{"uri":"https://ca.example/account/1"}}',
        )
    registration = messages.RegistrationResource(
        body=messages.Registration.from_json(
            {
                "status": "valid",
                "contact": ["mailto:operator@example.com"],
                "key": signing_key(store.vault.read(key).decode())[0].public_key().to_json(),
                "externalAccountBinding": {
                    "protected": "public-proof",
                    "payload": "public-payload",
                    "signature": "public-signature",
                },
            }
        ),
        uri="https://ca.example/account/1",
    )
    updated = registration.update(
        body=registration.body.update(contact=("mailto:new@example.com",))
    )
    acme = SimpleNamespace(
        query_registration=Mock(return_value=registration),
        update_registration=Mock(return_value=updated),
        net=SimpleNamespace(session=Mock()),
    )
    monkeypatch.setattr(certificate_acme, "connect", Mock(return_value=acme))
    result = certificate_acme.update_account(store.vault, request)
    assert result == {
        "email": "new@example.com",
        "storage_email": "operator@example.com",
        "registered": True,
    }
    assert hashlib.sha256(key.read_bytes()).hexdigest() == key_hash
    assert json.loads(account.read_text())["email"] == "new@example.com"
    acme.update_registration.assert_called_once()
    assert acme.update_registration.call_args.args[0].body.json_dumps() == "{}"
    assert (
        json.loads(account.read_text())["registration"]["body"]["externalAccountBinding"][
            "protected"
        ]
        == "public-proof"
    )
    acme.query_registration.return_value = updated
    certificate_acme.update_account(store.vault, request)
    assert acme.update_registration.call_count == 1
    with pytest.raises(certificate_acme.AdministrationError, match="eab_already_bound"):
        certificate_acme.update_account(store.vault, {**request, "eab_action": "remove"})


def test_corrupt_account_metadata_and_additive_old_schema(tmp_path):
    browser, store, _, base = setup(tmp_path, issued=True)
    row_id = profile_id(base)
    account, _ = account_paths(
        store.vault,
        store.vault.root / row_id,
        store.settings.certificate_acme_directories[0],
        "operator@example.com",
    )
    store.vault.write(account, b"[]")
    assert browser.get(base).json()["account"]["state"] == "unavailable"
    old = browser.get(base).json()["versions"][0]["id"]
    with store.engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_certificate_versions_fingerprint")
        connection.exec_driver_sql("ALTER TABLE managed_certificates DROP COLUMN account_email")
        connection.exec_driver_sql("ALTER TABLE certificate_jobs DROP COLUMN parameters")
        connection.exec_driver_sql("ALTER TABLE certificate_versions DROP COLUMN fingerprint")
    migrated = CertificateStore(store.settings, store.inventory)
    assert migrated.detail(row_id)["certificate"]["version_id"] == old
    assert migrated.export(row_id)["cert_pem"]


@pytest.mark.asyncio
async def test_durable_success_receipt_prevents_duplicate_acme_operation(tmp_path):
    browser, store, worker, base = setup(tmp_path)
    job_id = browser.post(base + "/account", json={"email": "new@example.com"}).json()["id"]
    with store.session() as db:
        job, row = db.get(CertificateJob, job_id), db.get(ManagedCertificate, profile_id(base))
    with store.vault.lock("worker.lock", blocking=False) as fd:
        first = await worker.administer(row, job, fd)
        worker.execute = AsyncMock(side_effect=AssertionError("Must not rerun CA operation"))
        assert await worker.administer(row, job, fd) == first
    assert not list(store.vault.root.glob("*/jobs/*/request.json"))


def deployment(browser, base):
    server = browser.post("/api/v1/servers", json={"name": "Target"}).json()
    assert (
        browser.post(
            "/api/v1/agents/scan", json={"token": server["agent_token"], **scan_result_payload()}
        ).status_code
        == 200
    )
    return browser.post(
        base + "/targets",
        json={
            "server_id": server["server"]["id"],
            "domain": "localhost",
            "cert_name": "test",
            "auto_deploy": False,
        },
    ).json()


def test_retained_commands_block_revocation_even_without_a_target(tmp_path):
    browser, store, _, base = setup(tmp_path, issued=True)
    target = deployment(browser, base)
    command = store.deploy(profile_id(base), target["id"])
    assert browser.delete(base + "/targets/" + target["id"]).status_code == 409
    version = browser.get(base).json()["certificate"]["version_id"]
    endpoint = base + "/versions/" + version + "/revoke"
    assert browser.post(endpoint, json={"confirm": True}).status_code == 409
    with store.write() as db:
        db.delete(db.get(CertificateTarget, target["id"]))
    assert browser.post(endpoint, json={"confirm": True}).status_code == 409
    with store.write() as db:
        db.get(CommandModel, str(command.id)).status = "succeeded"
    assert browser.post(endpoint, json={"confirm": True}).status_code == 202
    replacement = browser.post(
        base + "/targets",
        json={
            "server_id": target["server_id"],
            "domain": "localhost",
            "cert_name": "new",
            "auto_deploy": False,
        },
    ).json()
    assert browser.post(base + "/targets/" + replacement["id"] + "/deploy").status_code == 409


def test_revocation_and_deployment_cannot_both_pass_a_stale_state_check(tmp_path):
    browser, store, _, base = setup(tmp_path, issued=True)
    target = deployment(browser, base)
    version = browser.get(base).json()["certificate"]["version_id"]
    barrier = Barrier(2)

    def execute(operation):
        barrier.wait(timeout=5)
        try:
            operation()
            return True
        except CertificateError:
            return False

    with ThreadPoolExecutor(2) as pool:
        revoke = pool.submit(
            execute,
            lambda: store.queue_revocation(
                profile_id(base), version, CertificateRevoke(confirm=True)
            ),
        )
        deploy = pool.submit(execute, lambda: store.deploy(profile_id(base), target["id"]))
        assert sum([revoke.result(), deploy.result()]) == 1


@pytest.mark.parametrize("destination", ["missing", "different_key", "different_account"])
def test_unregistered_key_is_preserved_without_overwriting_other_accounts(
    tmp_path, monkeypatch, destination
):
    _, store, _, base = setup(tmp_path)
    work = store.vault.root / profile_id(base)
    directory = store.settings.certificate_acme_directories[0]
    _, key = account_paths(store.vault, work, directory, "operator@example.com")
    new_account, new_key = account_paths(store.vault, work, directory, "new@example.com")
    pem = pair()[1].encode()
    store.vault.write(key, pem)
    if destination == "different_key":
        store.vault.write(new_key, pair()[1].encode())
    elif destination == "different_account":
        store.vault.write(new_account, b"{}")
    acme = SimpleNamespace(
        query_registration=Mock(
            side_effect=messages.Error(typ="urn:ietf:params:acme:error:accountDoesNotExist")
        ),
        net=SimpleNamespace(session=Mock()),
    )
    monkeypatch.setattr(certificate_acme, "connect", Mock(return_value=acme))
    request = {
        "profile_work": str(work),
        "directory_url": directory,
        "email": "new@example.com",
        "storage_email": "operator@example.com",
        "eab_action": "replace",
    }
    if destination != "missing":
        with pytest.raises(
            certificate_acme.AdministrationError, match="account_destination_conflict"
        ):
            certificate_acme.update_account(store.vault, request)
    else:
        result = certificate_acme.update_account(store.vault, request)
        assert not result["registered"]
        assert store.vault.read(new_key) == pem
    assert store.vault.read(key) == pem


@pytest.mark.parametrize(
    "url", ["http://ca.example", "https://user:password@ca.example", "https://ca.example/#fragment"]
)
def test_acme_transport_rejects_insecure_or_credentialed_endpoints(url, monkeypatch):
    send = Mock()
    monkeypatch.setattr(certificate_acme.requests.Session, "request", send)
    with certificate_acme.BoundedSession() as session:
        with pytest.raises(ValueError):
            session.request("GET", url)
    send.assert_not_called()


def test_acme_transport_bounds_responses_and_disables_redirects(monkeypatch):
    response = certificate_acme.requests.Response()
    response._content = b"x" * 1048577
    response._content_consumed = True
    send = Mock(return_value=response)
    monkeypatch.setattr(certificate_acme.requests.Session, "request", send)
    with certificate_acme.BoundedSession() as session:
        assert not session.trust_env
        with pytest.raises(ValueError, match="size limit"):
            session.get("https://ca.example")
    assert send.call_args.kwargs["allow_redirects"] is False


def test_acme_connection_preserves_client_identity(monkeypatch):
    network = SimpleNamespace(session=certificate_acme.requests.Session())
    network.session.headers["User-Agent"] = "Open-Node/0.1"
    monkeypatch.setattr(certificate_acme.client, "ClientNetwork", Mock(return_value=network))
    monkeypatch.setattr(
        certificate_acme.client.ClientV2,
        "get_directory",
        Mock(return_value={"newNonce": "https://ca.example/nonce"}),
    )
    pem = pair()[1]
    result = certificate_acme.connect({"directory_url": "https://ca.example/directory"}, pem)
    try:
        assert result.net.session.headers["User-Agent"] == "Open-Node/0.1"
        assert not result.net.session.trust_env
    finally:
        result.net.session.close()


@pytest.mark.asyncio
async def test_receipt_mismatch_never_publishes_a_success(tmp_path):
    browser, store, worker, base = setup(tmp_path)
    job_id = browser.post(base + "/account", json={"email": "new@example.com"}).json()["id"]
    result = store.vault.root / profile_id(base) / "jobs" / job_id / "result.json"
    store.vault.write(
        result,
        json.dumps(
            {
                "job_id": job_id,
                "request_digest": "wrong",
                "status": "succeeded",
                "email": "attacker@example.com",
            }
        ).encode(),
    )
    worker.execute = AsyncMock()
    await run(worker)
    worker.execute.assert_awaited_once()
    detail = browser.get(base).json()
    assert detail["jobs"][0]["status"] == "failed"
    assert detail["account"]["email"] == "operator@example.com"


@pytest.mark.asyncio
async def test_failed_private_directory_check_removes_temporary_material(tmp_path):
    browser, store, worker, base = setup(tmp_path, issued=True)
    version = browser.get(base).json()["certificate"]["version_id"]
    assert (
        browser.post(base + "/versions/" + version + "/revoke", json={"confirm": True}).status_code
        == 202
    )
    work = store.vault.root / profile_id(base)
    work.mkdir(mode=0o700)
    (work / "unexpected-link").symlink_to(tmp_path, target_is_directory=True)
    worker.execute = AsyncMock()
    await run(worker)
    worker.execute.assert_not_awaited()
    assert not list(work.glob("jobs/*/request.json"))
    detail = browser.get(base).json()
    assert detail["jobs"][0]["status"] == "failed"
    assert detail["certificate"]["status"] == "revocation_unknown"


@pytest.mark.asyncio
async def test_recovery_never_republishes_a_revoked_disk_candidate(tmp_path):
    browser, store, worker, base = setup(tmp_path, issued=True)
    old = store.export(profile_id(base))
    version = browser.get(base).json()["certificate"]["version_id"]
    with store.write() as db:
        store.publish(db, db.get(ManagedCertificate, profile_id(base)), material(*pair()))
    assert (
        browser.post(base + "/versions/" + version + "/revoke", json={"confirm": True}).status_code
        == 202
    )
    worker.administer = AsyncMock(return_value={"fingerprint": fingerprint(old)})
    await run(worker)
    work = store.vault.root / profile_id(base)
    store.vault.write(work / "certificates/localhost.crt", old["cert_pem"].encode())
    store.vault.write(work / "certificates/localhost.key", old["key_pem"].encode())
    with store.write() as db:
        row = db.get(ManagedCertificate, profile_id(base))
        row.expires_at = row.not_before + 1
    new = material(*pair())

    async def execute(args, *_):
        assert "--ari-disable" in args and "--days" in args
        store.vault.write(work / "certificates/localhost.crt", new["cert_pem"].encode())
        store.vault.write(work / "certificates/localhost.key", new["key_pem"].encode())

    worker.execute = execute
    assert browser.post(base + "/renew", json={}).status_code == 202
    await run(worker)
    assert store.export(profile_id(base))["serial"] == new["serial"]
