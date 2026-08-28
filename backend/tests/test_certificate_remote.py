import asyncio
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from conftest import authenticated_client
from open_node.core.config import Settings
from open_node.main import create_app
from open_node.services.certificate_remote import ENDPOINT, RemoteHTTP01
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import (
    CertificateError,
    CertificateHTTPLease,
    CertificateJob,
    CertificateStore,
    ManagedCertificate,
)
from open_node.services.inventory import AgentScanResultModel, CommandModel
from sqlalchemy import select, text
from test_certificate_http import BASE, payload
from test_certificates import pair

ITEMS = [{"domain": "localhost", "token": "t" * 43, "key_authorization": "t" * 43 + "." + "a" * 43}]


def setup(tmp_path, mode="standalone"):
    client = authenticated_client(
        create_app(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
                certificate_state_dir=tmp_path / "vault",
            )
        )
    )
    node = client.post("/api/v1/servers", json={"name": "Validation node"}).json()
    scan = {"version": 1, "standalone": True, "webroots": ["site"], "cleanup_error": None}
    response = client.post(
        "/api/v1/agents/scan", json={"token": node["agent_token"], "http01": scan}
    )
    assert response.status_code == 200, response.text
    response = client.post(BASE, json=payload(mode, validation_server_id=node["server"]["id"]))
    assert response.status_code == 201, response.text
    store = client.app.state.certificates
    with store.session() as db:
        row = db.get(ManagedCertificate, response.json()["id"])
    return client, store, row, node


def acknowledge(store, identifier, *, good=True):
    with store.write() as db:
        command = db.get(CommandModel, str(identifier))
        command.status = "succeeded" if good else "failed"
        command.result_body = {"success": good, "lease_id": command.body["lease_id"]}


def transport(store):
    async def send(_inventory, command):
        acknowledge(store, command.id)

    return SimpleNamespace(dispatch_command=AsyncMock(side_effect=send))


@pytest.mark.parametrize("mode", ["standalone", "webroot"])
def test_remote_profiles_work_without_local_http_or_lego(tmp_path, mode):
    client, store, row, node = setup(tmp_path, mode)
    assert row.validation_server_id == node["server"]["id"]
    caps = client.get(BASE + "/capabilities").json()
    assert not caps["available"] and caps["remote_http_available"]
    assert caps["challenge_types"] == ["dns"] and caps["webroots"] == []
    assert caps["validation_nodes"][0]["webroots"] == ["site"]
    assert "token" not in str(caps) and str(tmp_path) not in str(caps)
    assert client.post(BASE + "/" + row.id + "/issue", json={}).status_code == 202


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"version": 1, "standalone": False, "webroots": []},
        {"version": 1, "standalone": True, "webroots": [], "cleanup_error": "host attention"},
    ],
)
def test_remote_queue_rechecks_node_policy(tmp_path, change):
    client, store, row, node = setup(tmp_path)
    with store.write() as db:
        db.get(AgentScanResultModel, node["server"]["id"]).http01 = change
    assert client.post(BASE + "/" + row.id + "/issue", json={}).status_code == 409


@pytest.mark.parametrize(
    "extra,status",
    [
        ({"validation_server_id": str(uuid4())}, 409),
        ({"domains": ["*.example.com"]}, 422),
        ({"challenge_type": "dns", "provider_id": str(uuid4())}, 422),
        ({"challenge_type": "webroot", "webroot_id": "../site"}, 422),
        ({"challenge_type": "webroot", "webroot_id": "unapproved"}, 409),
    ],
)
def test_remote_input_cannot_override_node_opt_in(tmp_path, extra, status):
    client, _store, _row, node = setup(tmp_path)
    response = client.post(
        BASE,
        json=payload(
            validation_server_id=node["server"]["id"],
            **{key: value for key, value in extra.items() if key != "validation_server_id"},
        )
        | (
            {"validation_server_id": extra["validation_server_id"]}
            if "validation_server_id" in extra
            else {}
        ),
    )
    assert response.status_code == status, response.text


@pytest.mark.parametrize("roots", [["../x"], ["site", "site"], ["x"] * 17])
def test_reported_webroot_ids_are_validated(tmp_path, roots):
    client, _store, _row, node = setup(tmp_path)
    response = client.post(
        "/api/v1/agents/scan",
        json={
            "token": node["agent_token"],
            "http01": {"webroots": roots},
        },
    )
    assert response.status_code == 422


def test_old_sqlite_certificate_and_scan_schema_upgrade(tmp_path):
    client, store, row, node = setup(tmp_path)
    with store.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE managed_certificates DROP COLUMN validation_server_id")
        )
        connection.execute(text("ALTER TABLE agent_scan_results DROP COLUMN http01"))
    restored = create_app(store.settings)
    migrated = authenticated_client(restored)
    assert migrated.get(BASE + "/" + row.id).json()["certificate"]["validation_server_id"] is None
    scan = migrated.get("/api/v1/servers/" + node["server"]["id"] + "/scan/latest").json()
    assert scan["scan"]["http01"] is None
    assert migrated.get(BASE + "/capabilities").json()["validation_nodes"] == []


@pytest.mark.asyncio
async def test_lease_cleanup_is_durable_and_needs_explicit_matching_ack(tmp_path):
    client, store, row, _node = setup(tmp_path)
    queued = store.queue(row.id, "issue")
    with store.session() as db:
        job = db.get(CertificateJob, queued["id"])
    remote = RemoteHTTP01(store, transport(store))
    await remote.present(row, job, ITEMS)
    with store.session() as db:
        lease = db.scalar(select(CertificateHTTPLease))
        command = db.get(CommandModel, lease.present_command_id)
        assert command.path == ENDPOINT and command.method == "PUT"
        assert 0 < command.body["expires_at"] - time() <= 570
        assert set(command.body) == {"lease_id", "expires_at", "mode", "webroot_id", "challenges"}
        assert not lease.cleanup_requested
    with store.write() as db:
        db.get(ManagedCertificate, row.id).active_job_id = None
    assert client.get(BASE + "/" + row.id).json()["jobs"][0]["cleanup_pending"]
    assert client.delete(BASE + "/" + row.id).status_code == 409
    silent = SimpleNamespace(dispatch_command=AsyncMock())
    restarted = RemoteHTTP01(CertificateStore(store.settings, store.inventory), silent)
    restarted.request_cleanup()
    await restarted.drain()
    with store.write() as db:
        current = db.get(CertificateHTTPLease, lease.id)
        cleanup = db.get(CommandModel, current.cleanup_command_id)
        assert cleanup.method == "DELETE" and cleanup.depends_on_command_id is None
        cleanup.status = "succeeded"
        cleanup.result_body = {"success": True, "lease_id": "wrong-lease"}
        previous = cleanup.id
        current.next_attempt = 0
    await restarted.drain()
    with store.write() as db:
        current = db.get(CertificateHTTPLease, lease.id)
        assert current.cleanup_command_id != previous and current.released_at is None
        outgoing = current.cleanup_command_id
        current.next_attempt = 0
    acknowledge(store, outgoing)
    await restarted.drain()
    assert not client.get(BASE + "/" + row.id).json()["jobs"][0]["cleanup_pending"]
    assert client.delete(BASE + "/" + row.id).status_code == 200


@pytest.mark.asyncio
async def test_failed_presentation_still_releases_without_dependency(tmp_path):
    _client, store, row, _node = setup(tmp_path)
    queued = store.queue(row.id, "issue")
    with store.session() as db:
        job = db.get(CertificateJob, queued["id"])

    async def fail(_inventory, command):
        acknowledge(store, command.id, good=False)

    remote = RemoteHTTP01(store, SimpleNamespace(dispatch_command=AsyncMock(side_effect=fail)))
    with pytest.raises(CertificateError, match="did not confirm"):
        await remote.present(row, job, ITEMS)
    remote.request_cleanup(job.id)
    await remote.drain()
    with store.session() as db:
        lease = db.scalar(select(CertificateHTTPLease))
        assert lease.cleanup_requested and lease.cleanup_command_id
        assert db.get(CommandModel, lease.cleanup_command_id).depends_on_command_id is None


@pytest.mark.asyncio
async def test_remote_worker_resumes_same_job_after_cancel_and_hard_restart(tmp_path):
    client, store, row, _node = setup(tmp_path)
    queued = store.queue(row.id, "issue")
    worker = CertificateWorker(store, transport(store))
    worker.remote.obtain = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await worker.run_one(-1)
    detail = client.get(BASE + "/" + row.id).json()
    assert detail["jobs"][0]["status"] == "queued"
    assert detail["certificate"]["active_job_id"] == queued["id"]
    with store.write() as db:
        db.get(CertificateJob, queued["id"]).status = "running"
    worker.recover()
    with store.session() as db:
        assert db.get(CertificateJob, queued["id"]).status == "queued"
    cert, key = pair()
    from open_node.services.certificate_vault import material

    worker.remote.obtain = AsyncMock(return_value=material(cert, key))
    assert await worker.run_one(-1)
    detail = client.get(BASE + "/" + row.id).json()
    assert detail["jobs"][0]["status"] == "succeeded" and len(detail["versions"]) == 1
    assert detail["certificate"]["active_job_id"] is None


def test_remote_renewal_is_scheduled_without_lego(tmp_path):
    _client, store, row, _node = setup(tmp_path)
    cert, key = pair()
    from open_node.services.certificate_vault import material

    with store.write() as db:
        current = db.get(ManagedCertificate, row.id)
        store.publish(db, current, material(cert, key))
        current.expires_at = time() + 100
        current.next_attempt = 0
    CertificateWorker(store, transport(store)).schedule()
    with store.session() as db:
        job = db.scalar(select(CertificateJob))
        assert job.kind == "renew" and job.status == "queued"


@pytest.mark.asyncio
async def test_cleanup_send_timeout_keeps_its_command_for_reconnect(tmp_path):
    _client, store, row, _node = setup(tmp_path)
    queued = store.queue(row.id, "issue")
    with store.session() as db:
        job = db.get(CertificateJob, queued["id"])
    remote = RemoteHTTP01(store, transport(store))
    await remote.present(row, job, ITEMS)
    remote.request_cleanup(job.id)
    remote.connections.dispatch_command.side_effect = TimeoutError
    await remote.drain()
    with store.write() as db:
        lease = db.scalar(select(CertificateHTTPLease))
        lease.next_attempt = 0
        command_id = lease.cleanup_command_id
        assert command_id and lease.released_at is None
        assert db.get(CommandModel, command_id).status == "pending"
    remote.connections = transport(store)
    await remote.drain()
    with store.write() as db:
        lease = db.scalar(select(CertificateHTTPLease))
        assert lease.cleanup_command_id == command_id
        lease.next_attempt = 0
    await remote.drain()
    with store.session() as db:
        assert db.scalar(select(CertificateHTTPLease)).released_at is not None
