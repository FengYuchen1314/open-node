"""Certificate entry points retain real work, not an idle worker's lock.

The subprocess cases execute disposable local Python children, not a CA. The
remote launcher substitutes only its fixed ACME program with that fixture while
retaining and checking the production launch options and inherited descriptors.
"""

import asyncio
import contextlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_node.core.config import Settings
from open_node.domain.certificates import CertificateCreate
from open_node.domain.inventory import ServerCreate
from open_node.services import certificate_worker
from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupWriteBarrier,
)
from open_node.services.backup_runtime import (
    backup_operation,
    current_backup_child_fds,
    run_in_backup_thread,
)
from open_node.services.certificate_remote import RemoteHTTP01
from open_node.services.certificate_vault import CertificateVault
from open_node.services.certificate_worker import CertificateWorker
from open_node.services.certificates import (
    CertificateHTTPLease,
    CertificateJob,
    CertificateStore,
    ManagedCertificate,
)
from open_node.services.inventory import AgentScanResultModel, CommandModel, InventoryStore
from sqlalchemy import select

ENTRYPOINTS = [
    ("worker", "recover", (), False),
    ("worker", "schedule", (), False),
    ("worker", "deploy_pending", (), True),
    ("worker", "run_one", (-1,), True),
    ("worker", "administer", (None, None, -1), True),
    ("worker", "obtain", (None, None, None, -1), True),
    ("worker", "execute", ([], {}, None, -1), True),
    ("remote", "request_cleanup", ("job-id",), False),
    ("remote", "drain", (), True),
    ("remote", "present", (None, None, []), True),
    ("remote", "obtain", (None, None, -1), True),
    ("remote", "execute", (None, None, None, -1), True),
]
ENTRYPOINT_IDS = [f"{owner}.{name}" for owner, name, _args, _async in ENTRYPOINTS]
ITEMS = [{"domain": "localhost", "token": "t" * 43, "key_authorization": "t" * 43 + "." + "a" * 43}]


@pytest.fixture
def barriers(tmp_path):
    path = tmp_path / "backup.lock"
    writer, observer = BackupWriteBarrier(path), BackupWriteBarrier(path)
    try:
        yield writer, observer
    finally:
        writer.close()
        observer.close()


@pytest.fixture
def basic(tmp_path, barriers):
    writer, observer = barriers
    store = SimpleNamespace(
        settings=SimpleNamespace(certificate_poll_seconds=0.05, certificate_job_timeout=10),
        vault=CertificateVault(tmp_path / "vault"),
    )
    worker = CertificateWorker(store, SimpleNamespace(), backup_writes=writer)
    return SimpleNamespace(
        worker=worker, remote=worker.remote, store=store, writer=writer, observer=observer
    )


@pytest.fixture
def actual(tmp_path, barriers):
    writer, observer = barriers
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'certificates.sqlite'}",
        certificate_state_dir=tmp_path / "certificate-state",
        certificate_lego_binary=Path(sys.executable),
        certificate_http_address="127.0.0.1:8082",
    )
    with backup_operation(writer):
        inventory = InventoryStore(settings.database_url)
        inventory.create_schema()
        store = CertificateStore(settings, inventory)
        connections = SimpleNamespace(dispatch_command=AsyncMock())
        worker = CertificateWorker(store, connections, backup_writes=writer)
    try:
        yield SimpleNamespace(
            worker=worker, remote=worker.remote, store=store, inventory=inventory,
            writer=writer, observer=observer, connections=connections,
        )
    finally:
        store.engine.dispose()
        inventory._engine.dispose()


def assert_blocked(observer):
    with pytest.raises(BackupBusyError), observer.snapshot(timeout=0):
        pytest.fail("a certificate writer admitted an exclusive snapshot")


def assert_released(observer):
    with observer.snapshot(timeout=0) as permit:
        permit.assert_active()


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


def paused(writer):
    with writer._condition:
        return writer._work_paused


async def call_entry(instance, name, args, is_async):
    result = getattr(instance, name)(*args)
    return await result if is_async else result


@pytest.mark.parametrize("owner,name,args,is_async", ENTRYPOINTS, ids=ENTRYPOINT_IDS)
@pytest.mark.parametrize("outcome", ["return", "error", "cancelled"])
@pytest.mark.asyncio
async def test_direct_entry_has_runtime_lease_and_preserves_result_or_error(
    basic, monkeypatch, owner, name, args, is_async, outcome
):
    instance = getattr(basic, owner)
    expected = object()
    error = asyncio.CancelledError() if outcome == "cancelled" else ValueError("fixture failure")
    observed = []

    def body(*received):
        assert received == args
        fds = current_backup_child_fds()
        assert len(fds) == 1
        os.fstat(fds[0])
        assert_blocked(basic.observer)
        observed.append(fds[0])
        if outcome != "return":
            raise error
        return expected

    async def async_body(*received):
        return body(*received)

    monkeypatch.setattr(instance, "_" + name, async_body if is_async else body)
    if outcome == "return":
        assert await call_entry(instance, name, args, is_async) is expected
    else:
        with pytest.raises(type(error)) as raised:
            await call_entry(instance, name, args, is_async)
        assert raised.value is error
    assert len(observed) == 1
    with pytest.raises(OSError):
        os.fstat(observed[0])
    with pytest.raises(BackupCoordinationError):
        current_backup_child_fds()
    assert_released(basic.observer)


@pytest.mark.parametrize("owner,name,args,is_async", ENTRYPOINTS, ids=ENTRYPOINT_IDS)
@pytest.mark.asyncio
async def test_direct_entry_is_rejected_before_any_business_action(
    basic, monkeypatch, owner, name, args, is_async
):
    calls = []

    def body(*received):
        calls.append(received)

    async def async_body(*received):
        return body(*received)

    instance = getattr(basic, owner)
    monkeypatch.setattr(instance, "_" + name, async_body if is_async else body)
    with basic.observer.snapshot(timeout=0), pytest.raises(BackupBusyError):
        await call_entry(instance, name, args, is_async)
    assert calls == []
    assert_released(basic.observer)


def test_worker_and_its_remote_share_explicit_and_compatibility_barriers(basic):
    assert basic.worker.backup_writes is basic.writer
    assert basic.remote.backup_writes is basic.writer
    compatible = CertificateWorker(basic.store, SimpleNamespace())
    standalone = RemoteHTTP01(basic.store, SimpleNamespace())
    try:
        assert compatible.remote.backup_writes is compatible.backup_writes
        assert compatible.backup_writes is not basic.writer
        with backup_operation(compatible.backup_writes):
            assert current_backup_child_fds() == ()
        with backup_operation(standalone.backup_writes):
            assert current_backup_child_fds() == ()
        assert not basic.store.vault.root.exists()
    finally:
        compatible.backup_writes.close()
        standalone.backup_writes.close()


@pytest.mark.parametrize("factory", [CertificateWorker, RemoteHTTP01])
def test_constructor_cannot_enter_during_snapshot(basic, factory):
    with basic.observer.snapshot(timeout=0), pytest.raises(BackupBusyError):
        factory(basic.store, SimpleNamespace(), backup_writes=basic.writer)
    assert not basic.store.vault.root.exists()


@pytest.mark.asyncio
async def test_idle_worker_keeps_only_worker_lock_not_backup_shared_lock(basic, monkeypatch):
    events = []
    cycled = asyncio.Event()
    original_lock = basic.store.vault.lock

    @contextlib.contextmanager
    def worker_lock(*args, **kwargs):
        assert len(current_backup_child_fds()) == 1
        assert_blocked(basic.observer)
        with original_lock(*args, **kwargs) as fd:
            yield fd

    def observed(name):
        fds = current_backup_child_fds()
        assert len(fds) == 1
        assert_blocked(basic.observer)
        events.append((name, fds))

    async def deploy():
        observed("deploy")

    async def drain():
        observed("drain")

    async def run_one(fd):
        os.fstat(fd)
        observed("run_one")
        cycled.set()
        return False

    monkeypatch.setattr(basic.store.vault, "lock", worker_lock)
    monkeypatch.setattr(basic.worker, "_recover", lambda: observed("recover"))
    monkeypatch.setattr(basic.worker, "_schedule", lambda: observed("schedule"))
    monkeypatch.setattr(basic.worker, "_deploy_pending", deploy)
    monkeypatch.setattr(basic.remote, "_drain", drain)
    monkeypatch.setattr(basic.worker, "_run_one", run_one)
    task = asyncio.create_task(basic.worker.run())
    try:
        await asyncio.wait_for(cycled.wait(), 3)
        assert [name for name, _fds in events] == [
            "recover", "deploy", "drain", "schedule", "run_one"
        ]
        assert len({fds for _name, fds in events[1:]}) == 1
        with pytest.raises(BlockingIOError), original_lock("worker.lock", blocking=False):
            pass
        assert_released(basic.observer)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    with original_lock("worker.lock", blocking=False):
        pass
    assert_released(basic.observer)


@pytest.mark.asyncio
async def test_busy_startup_is_retried_without_preparing_vault_or_writing(basic, monkeypatch):
    unavailable, started = asyncio.Event(), asyncio.Event()
    calls = []

    def warning(_format, error_name):
        assert error_name == "BackupBusyError"
        unavailable.set()

    def recover():
        assert len(current_backup_child_fds()) == 1
        calls.append("recover")

    async def deploy():
        calls.append("deploy")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(certificate_worker.log, "warning", warning)
    monkeypatch.setattr(basic.worker, "_recover", recover)
    monkeypatch.setattr(basic.worker, "_deploy_pending", deploy)
    task = None
    try:
        with basic.observer.snapshot(timeout=0):
            task = asyncio.create_task(basic.worker.run())
            await asyncio.wait_for(unavailable.wait(), 3)
            assert calls == []
            assert not basic.store.vault.root.exists()
        await asyncio.wait_for(started.wait(), 3)
        assert calls == ["recover", "deploy"]
    finally:
        if task is not None:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert_released(basic.observer)


@pytest.mark.asyncio
async def test_offloaded_direct_recovery_outlives_cancelled_awaiter(basic, monkeypatch):
    ready, release, finished = threading.Event(), threading.Event(), threading.Event()
    event_loop_thread = threading.get_ident()
    seen = []

    def recover():
        assert threading.get_ident() != event_loop_thread
        seen.append(current_backup_child_fds())
        ready.set()
        assert release.wait(5)
        assert_blocked(basic.observer)
        finished.set()

    async def caller():
        with backup_operation(basic.writer):
            await run_in_backup_thread(basic.worker.recover)

    monkeypatch.setattr(basic.worker, "_recover", recover)
    task = asyncio.create_task(caller())
    try:
        await wait_until(ready.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
        assert_blocked(basic.observer)
        os.fstat(seen[0][0])
    finally:
        release.set()
        await wait_until(finished.is_set)
        await wait_until(lambda: not basic.writer._records)
        if not task.done():
            await task
    assert_released(basic.observer)


def local_profile(actual):
    with backup_operation(actual.writer):
        created = actual.store.create(
            CertificateCreate(
                name="Coordinated local certificate", domains=["localhost"],
                email="operator@example.com", challenge_type="standalone", accept_terms=True,
            )
        )
        job = actual.store.queue(created["id"], "issue")
    return created, job


@pytest.mark.asyncio
async def test_busy_run_one_preserves_real_queued_job_and_can_later_complete(actual, monkeypatch):
    profile, job = local_profile(actual)
    before = actual.store.detail(profile["id"])
    with actual.observer.snapshot(timeout=0), pytest.raises(BackupBusyError):
        await actual.worker.run_one(-1)
    assert actual.store.detail(profile["id"]) == before
    calls = []

    async def no_candidate(_row, _provider, active_job, _fd):
        assert active_job.id == job["id"]
        assert len(current_backup_child_fds()) == 1
        assert_blocked(actual.observer)
        with actual.store.session() as db:
            assert db.get(CertificateJob, job["id"]).status == "running"
            assert db.get(ManagedCertificate, profile["id"]).status == "issuing"
        calls.append(active_job.id)
        return None

    monkeypatch.setattr(actual.worker, "_obtain", no_candidate)
    assert await actual.worker.run_one(-1) is True
    detail = actual.store.detail(profile["id"])
    assert detail["jobs"][0]["status"] == "skipped"
    assert detail["certificate"]["active_job_id"] is None
    assert calls == [job["id"]]
    assert_released(actual.observer)


def remote_profile(actual):
    with backup_operation(actual.writer):
        server = actual.inventory.create_server(ServerCreate(name="Private validation fixture"))
        with actual.store.write() as db:
            now = datetime.now(UTC)
            db.add(
                AgentScanResultModel(
                    server_id=str(server.id), reported_at=now, updated_at=now,
                    http01={"version": 1, "standalone": True, "webroots": []},
                )
            )
        profile = actual.store.create(
            CertificateCreate(
                name="Coordinated remote certificate", domains=["localhost"],
                email="operator@example.com", challenge_type="standalone", accept_terms=True,
                validation_server_id=server.id,
            )
        )
        queued = actual.store.queue(profile["id"], "issue")
        with actual.store.session() as db:
            return db.get(ManagedCertificate, profile["id"]), db.get(CertificateJob, queued["id"])


@pytest.mark.asyncio
async def test_remote_presentation_allows_real_agent_receipt_during_first_drain(actual):
    row, job = remote_profile(actual)
    sent = asyncio.Event()
    snapshot_entered, finish_snapshot = threading.Event(), threading.Event()
    outgoing = []
    work_fds, agent_fds = [], []

    async def dispatch(_inventory, command):
        outgoing.append(command)
        work_fds.append(current_backup_child_fds())
        sent.set()

    actual.connections.dispatch_command.side_effect = dispatch

    def snapshot():
        with actual.writer.snapshot(timeout=3) as permit:
            permit.assert_active()
            snapshot_entered.set()
            assert finish_snapshot.wait(5)

    def acknowledge():
        agent_fds.append(current_backup_child_fds())
        with actual.store.write() as db:
            command = db.get(CommandModel, str(outgoing[0].id))
            command.status = "succeeded"
            command.result_body = {"success": True, "lease_id": command.body["lease_id"]}

    task = asyncio.create_task(actual.remote.present(row, job, ITEMS))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = None
        try:
            await asyncio.wait_for(sent.wait(), 3)
            future = pool.submit(snapshot)
            await wait_until(lambda: paused(actual.writer))
            assert not snapshot_entered.is_set()
            with pytest.raises(BackupBusyError), backup_operation(actual.writer):
                pass
            with backup_operation(actual.writer, kind="agent"):
                await run_in_backup_thread(acknowledge)
            await asyncio.wait_for(task, 3)
            await wait_until(snapshot_entered.is_set)
            assert len(outgoing) == 1
            assert len(work_fds[0]) == len(agent_fds[0]) == 1
            assert work_fds[0] != agent_fds[0]
        finally:
            finish_snapshot.set()
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if future is not None:
                future.result(timeout=5)
    with actual.store.session() as db:
        lease = db.scalar(select(CertificateHTTPLease))
        assert lease.present_command_id == str(outgoing[0].id)
        assert not lease.cleanup_requested
    actual.remote.request_cleanup(job.id)
    with actual.store.session() as db:
        assert db.get(CertificateHTTPLease, lease.id).cleanup_requested
    assert_released(actual.observer)


def install_native_launcher(basic, monkeypatch, owner, *, wait_for_release):
    """Observe actual inherited FDs; only remote's network program is replaced."""
    launch = asyncio.create_subprocess_exec
    processes, inherited = [], []
    work = basic.store.vault.root / "native"
    basic.store.vault.prepare()
    work.mkdir(mode=0o700)

    async def start(*args, **kwargs):
        fds = kwargs["pass_fds"]
        assert len(fds) == len(set(fds)) == 2
        assert fds[1:] == current_backup_child_fds()
        assert kwargs["start_new_session"] is True
        assert kwargs["cwd"] == work
        inherited.append(fds)
        identities = [(os.fstat(fd).st_dev, os.fstat(fd).st_ino) for fd in fds]
        program = (
            "import json,os,pathlib,signal,time\n"
            f"fds={fds!r}\n"
            f"expected={identities!r}\n"
            "actual=[(os.fstat(fd).st_dev,os.fstat(fd).st_ino) for fd in fds]\n"
            "assert actual == expected\n"
            "signal.signal(signal.SIGTERM, lambda *_: pathlib.Path('term').touch())\n"
            "pathlib.Path('ready.tmp').write_text(json.dumps({'pid':os.getpid(),'fds':fds}))\n"
            "os.replace('ready.tmp','ready.json')\n"
        )
        if wait_for_release:
            program += "while not pathlib.Path('release').exists(): time.sleep(0.005)\n"
        if owner == "remote":
            assert args[:3] == (sys.executable, "-I", "-c")
            assert "open_node.services.certificate_remote_acme" in args
            assert kwargs["stdin"] == asyncio.subprocess.PIPE
            assert kwargs["stderr"] == asyncio.subprocess.PIPE
            assert kwargs["limit"] == 16384
            # Preserve the production private umask/exec launcher around the
            # harmless native program. No test URL or CA enters the product.
            actual_args = (*args[:4], sys.executable, "-I", "-c", program)
        else:
            assert args[:3] == (sys.executable, "-I", "-c")
            assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
            assert kwargs["stderr"] == asyncio.subprocess.STDOUT
            actual_args = (*args[:4], sys.executable, "-I", "-c", program)
        process = await launch(*actual_args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    return work, processes, inherited


async def native_execute(basic, owner, work, lock_fd):
    if owner == "worker":
        await basic.worker.execute(
            [sys.executable, "-c", "pass"], {"PATH": os.defpath}, work, lock_fd
        )
    else:
        await basic.remote.execute(work / "request.json", None, None, lock_fd)


@pytest.mark.parametrize("owner", ["worker", "remote"])
@pytest.mark.asyncio
async def test_real_child_inherits_both_locks_and_repeated_cancel_waits_for_cleanup(
    basic, monkeypatch, owner
):
    work, processes, inherited = install_native_launcher(
        basic, monkeypatch, owner, wait_for_release=True
    )
    task = None
    with basic.store.vault.lock("worker.lock", blocking=False) as worker_fd:
        try:
            task = asyncio.create_task(native_execute(basic, owner, work, worker_fd))
            await wait_until(lambda: (work / "ready.json").exists())
            receipt = json.loads((work / "ready.json").read_text())
            assert tuple(receipt["fds"]) == inherited[0]
            assert receipt["fds"][0] == worker_fd
            assert_blocked(basic.observer)
            task.cancel()
            await wait_until(lambda: (work / "term").exists())
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert processes[0].returncode is None
            assert_blocked(basic.observer)
            (work / "release").touch()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert processes[0].returncode == 0
            with pytest.raises(ProcessLookupError):
                os.kill(receipt["pid"], 0)
            assert (work / "last-job.log").is_file()
            assert (work / "last-job.log").stat().st_mode & 0o777 == 0o600
            os.fstat(worker_fd)
            with pytest.raises(OSError):
                os.fstat(inherited[0][1])
            assert_released(basic.observer)
        finally:
            (work / "release").touch()
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()
    with basic.store.vault.lock("worker.lock", blocking=False):
        pass


@pytest.mark.parametrize("owner", ["worker", "remote"])
@pytest.mark.asyncio
async def test_cleanup_keeps_its_own_reference_after_child_exit_until_log_write(
    basic, monkeypatch, owner
):
    work, processes, inherited = install_native_launcher(
        basic, monkeypatch, owner, wait_for_release=False
    )
    observations = []

    def observe(path):
        if path.name == "last-job.log":
            assert processes[0].returncode == 0
            assert current_backup_child_fds() == inherited[0][1:]
            assert_blocked(basic.observer)
            with basic.writer._condition:
                records = list(basic.writer._records)
                assert len(records) == 1
                # The execute scope and its independently retained cleanup task.
                assert len(records[0].holders) >= 2
            observations.append(path)

    if owner == "worker":
        original_path = certificate_worker.private_path

        def checked_path(root, path):
            observe(path)
            return original_path(root, path)

        monkeypatch.setattr(certificate_worker, "private_path", checked_path)
    else:
        original_write = basic.store.vault.write

        def checked_write(path, data):
            observe(path)
            return original_write(path, data)

        monkeypatch.setattr(basic.store.vault, "write", checked_write)
    with basic.store.vault.lock("worker.lock", blocking=False) as worker_fd:
        await native_execute(basic, owner, work, worker_fd)
        assert observations == [work / "last-job.log"]
        assert_released(basic.observer)
    assert all(process.returncode == 0 for process in processes)


@pytest.mark.parametrize("owner", ["worker", "remote"])
@pytest.mark.asyncio
async def test_subprocess_creation_failure_releases_lease_without_retry(basic, monkeypatch, owner):
    calls = []
    failure = OSError("fixture spawn failure")

    async def fail(*args, **kwargs):
        assert len(current_backup_child_fds()) == 1
        calls.append((args, kwargs["pass_fds"]))
        raise failure

    basic.store.vault.prepare()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    with basic.store.vault.lock("worker.lock", blocking=False) as worker_fd:
        with pytest.raises(OSError) as raised:
            await native_execute(basic, owner, basic.store.vault.root, worker_fd)
        assert raised.value is failure
        assert len(calls) == 1
        assert len(calls[0][1]) == 2
        assert not (basic.store.vault.root / "last-job.log").exists()
        assert_released(basic.observer)
