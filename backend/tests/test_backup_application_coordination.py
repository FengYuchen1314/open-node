"""Application/CLI and real worker lifetime checks, not snapshot/restore tests."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from open_node import main as application
from open_node.core.config import Settings
from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupWriteBarrier,
)
from open_node.services.backup_runtime import (
    BACKUP_LOCK_NAME,
    configured_backup_barrier,
    current_backup_child_fds,
)
from open_node.services.notification_worker import NotificationWorker
from open_node.services.secure_channel import AgentIdentity
from open_node.services.server_traffic import ServerTrafficWorker
from open_node.services.subscription_access import SubscriptionAccessWorker
from open_node.services.telegram_transport import TelegramOutcome

NOW = datetime(2026, 8, 31, 1, tzinfo=UTC)
TEST_PASSWORD = "backup-coordination-synthetic-password"


def settings_for(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'state.db'}",
        certificate_state_dir=tmp_path / "certificates",
        notifications_state_dir=tmp_path / "notifications",
    )


def snapshot_once(barrier):
    with barrier.snapshot(timeout=0) as permit:
        permit.assert_active()


@pytest.fixture
def barriers(tmp_path):
    path = tmp_path / BACKUP_LOCK_NAME
    application_barrier = BackupWriteBarrier(path)
    external_barrier = BackupWriteBarrier(path)
    try:
        yield application_barrier, external_barrier
    finally:
        external_barrier.close()
        application_barrier.close()


async def wait_for_thread(event):
    assert await asyncio.to_thread(event.wait, 5), "synthetic worker did not reach its checkpoint"


async def wait_idle(barrier):
    async with asyncio.timeout(5):
        while True:
            with barrier._condition:
                if not barrier._records:
                    return
            await asyncio.sleep(0.001)


async def cancel_task(task):
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_startup_is_refused_before_any_store_constructor(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    barrier = configured_backup_barrier(settings.database_url)
    entered = []

    def forbidden(*args, **kwargs):
        entered.append(True)
        raise AssertionError("startup touched a store while exclusive lock was held")

    monkeypatch.setattr(application, "AuthStore", forbidden)
    try:
        with barrier.snapshot(timeout=0), pytest.raises(BackupBusyError):
            application.create_app(settings)
        assert entered == []
        assert not (tmp_path / "state.db").exists()
        assert not settings.certificate_state_dir.exists()
    finally:
        barrier.close()


def test_every_initialization_store_uses_the_same_real_lock(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    external = configured_backup_barrier(settings.database_url)
    seen = []

    def wrap(original, name):
        def checked(*args, **kwargs):
            assert len(current_backup_child_fds()) == 1
            with pytest.raises(BackupBusyError):
                snapshot_once(external)
            seen.append(name)
            return original(*args, **kwargs)

        return checked

    for name in ("AuthStore", "InventoryStore", "CertificateStore"):
        monkeypatch.setattr(application, name, wrap(getattr(application, name), name))
    for store_type in (application.BrandingStore, application.NotificationStore):
        name = store_type.__name__ + ".create_schema"
        monkeypatch.setattr(store_type, "create_schema", wrap(store_type.create_schema, name))
    try:
        app = application.create_app(settings)
        assert set(seen) == {
            "AuthStore", "InventoryStore", "CertificateStore",
            "BrandingStore.create_schema", "NotificationStore.create_schema",
        }
        snapshot_once(external)
        app.state.backup_writes.close()
    finally:
        external.close()


def test_failed_startup_closes_the_unused_barrier(tmp_path, monkeypatch):
    barrier = configured_backup_barrier(settings_for(tmp_path).database_url)
    monkeypatch.setattr(
        application,
        "configured_backup_barrier",
        lambda _url, _state_root: barrier,
    )

    def broken(*_args, **_kwargs):
        assert current_backup_child_fds()
        raise ValueError("synthetic initialization failure")

    monkeypatch.setattr(application, "AuthStore", broken)
    with pytest.raises(ValueError, match="synthetic initialization failure"):
        application.create_app(settings_for(tmp_path))
    with pytest.raises(BackupCoordinationError), barrier.operation():
        pytest.fail("closed startup barrier admitted another operation")
    external = configured_backup_barrier(settings_for(tmp_path).database_url)
    try:
        snapshot_once(external)
    finally:
        external.close()


def test_all_worker_constructors_share_barrier_but_idle_tasks_do_not(tmp_path, monkeypatch):
    app = application.create_app(settings_for(tmp_path))
    created, running, stopped = [], [], []

    class IdleWorker:
        def __init__(self, *args, backup_writes, **kwargs):
            assert backup_writes is app.state.backup_writes
            assert current_backup_child_fds()
            self.identifier = len(created)
            created.append(self.identifier)

        async def run(self):
            with pytest.raises(BackupCoordinationError):
                current_backup_child_fds()
            running.append(self.identifier)
            try:
                await asyncio.Future()
            finally:
                stopped.append(self.identifier)

    for name in (
        "CertificateWorker", "SubscriptionAccessWorker",
        "ServerTrafficWorker", "NotificationWorker", "ExternalRefreshWorker",
        "DDNSWorker", "FederationRefreshWorker",
    ):
        monkeypatch.setattr(application, name, IdleWorker)
    try:
        for _ in range(2):
            with TestClient(app) as client:
                assert client.get("/healthz").status_code == 200
                snapshot_once(app.state.backup_writes)
            with app.state.backup_writes.operation() as lease:
                assert lease.child_fds
        assert created == list(range(14))
        assert sorted(running) == sorted(stopped) == created
    finally:
        app.state.backup_writes.close()


def invoke_admin(tmp_path, action):
    environment = {
        **os.environ,
        "OPEN_NODE_DATABASE_URL": settings_for(tmp_path).database_url,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
    }
    return subprocess.run(
        [sys.executable, "-B", "-m", "open_node.admin", action, "--password-stdin"],
        input=TEST_PASSWORD + "\n", text=True, capture_output=True, env=environment,
        timeout=30, check=False,
    )


def test_real_admin_process_refuses_exclusive_but_cooperates_with_app_readers(tmp_path):
    barrier = configured_backup_barrier(settings_for(tmp_path).database_url)
    try:
        with barrier.snapshot(timeout=0):
            denied = invoke_admin(tmp_path, "create")
        assert denied.returncode == 1
        assert denied.stdout == ""
        assert denied.stderr == "备份停写协调暂不可用，请稍后重试。\n"
        assert TEST_PASSWORD not in denied.stdout + denied.stderr
        assert not (tmp_path / "state.db").exists()
        with barrier.operation():
            created = invoke_admin(tmp_path, "create")
            reset = invoke_admin(tmp_path, "reset-password")
        assert created.returncode == reset.returncode == 0
        assert created.stderr == reset.stderr == ""
        assert (tmp_path / "state.db").is_file()
        snapshot_once(barrier)
    finally:
        barrier.close()


def test_admin_rejects_unsafe_existing_lock_without_rewriting_it(tmp_path):
    lock = tmp_path / BACKUP_LOCK_NAME
    lock.write_bytes(b"synthetic untrusted lock")
    lock.chmod(0o644)
    result = invoke_admin(tmp_path, "create")
    assert result.returncode == 1
    assert result.stderr == "备份停写协调暂不可用，请稍后重试。\n"
    assert lock.read_bytes() == b"synthetic untrusted lock"
    assert lock.stat().st_mode & 0o777 == 0o644
    assert not (tmp_path / "state.db").exists()


def invoke_identity(tmp_path, action, path, **environment):
    return subprocess.run(
        [sys.executable, "-B", "-m", "open_node.agent_identity", action, str(path)],
        text=True, capture_output=True, timeout=15, check=False,
        env={
            **os.environ,
            "OPEN_NODE_DATABASE_URL": settings_for(tmp_path).database_url,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONNOUSERSITE": "1",
            **environment,
        },
    )


def test_identity_creation_obeys_same_lock_and_never_opens_database(tmp_path):
    path = tmp_path / "identity" / "seed"
    barrier = configured_backup_barrier(settings_for(tmp_path).database_url)
    try:
        with barrier.snapshot(timeout=0):
            denied = invoke_identity(tmp_path, "create", path)
        assert denied.returncode == 1 and denied.stdout == ""
        assert denied.stderr == "备份停写协调暂不可用，请稍后重试。\n"
        assert not path.parent.exists()
        with barrier.operation():
            created = invoke_identity(tmp_path, "create", path)
        assert created.returncode == 0 and created.stderr == ""
        assert json.loads(created.stdout) == AgentIdentity.load(path).public_metadata()
        assert len(path.read_bytes()) == 32
        assert not (tmp_path / "state.db").exists()
        snapshot_once(barrier)
    finally:
        barrier.close()


def test_identity_show_remains_read_only_and_independent_of_database_settings(tmp_path):
    path = tmp_path / "identity" / "seed"
    identity = AgentIdentity.create(path)
    barrier = configured_backup_barrier(settings_for(tmp_path).database_url)
    try:
        with barrier.snapshot(timeout=0):
            shown = invoke_identity(
                tmp_path, "show", path, OPEN_NODE_DATABASE_URL="not-a-valid-database-url",
            )
        assert shown.returncode == 0 and shown.stderr == ""
        assert json.loads(shown.stdout) == identity.public_metadata()
        assert not (tmp_path / "state.db").exists()
    finally:
        barrier.close()


class SyntheticStore:
    def __init__(self, callback, *, has_claim=False):
        self.callback = callback
        self.has_claim = has_claim
        self.calls = []
        self.receipts = []

    def call(self, stage):
        self.calls.append(stage)
        self.callback(stage)

    def _server_traffic(self):
        return self

    def _subscription_access(self):
        return self

    def reset_due(self):
        self.call("traffic")
        return 0

    def reset_due_subscription_traffic(self, _request):
        self.call("access_reset")

    def run_once(self):
        self.call("access_reconcile")
        return []

    def backfill(self):
        self.call("access_backfill")

    def recover(self, *, now):
        self.call("recover")

    def scan(self, *, now):
        self.call("scan")

    def claim(self, *, now, lease_seconds):
        self.call("claim")
        if not self.has_claim:
            return None
        self.has_claim = False
        return SimpleNamespace(
            deadline_at=NOW + timedelta(seconds=40), token="synthetic-token",
            chat_id="synthetic-chat", text="synthetic-text",
        )

    def finish(self, claim, outcome, *, now):
        self.call("finish")
        self.receipts.append(outcome)


class AcceptedTransport:
    async def send(self, *_args):
        return TelegramOutcome(state="accepted", code="telegram_accepted", message_id=1)


class WaitingTransport:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def send(self, *_args):
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.closed = True


def make_worker(stage, store, barrier):
    if stage == "traffic":
        return ServerTrafficWorker(store, backup_writes=barrier)
    if stage.startswith("access_"):
        return SubscriptionAccessWorker(store, None, backup_writes=barrier)
    return NotificationWorker(
        store, AcceptedTransport(), clock=lambda: NOW, backup_writes=barrier,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", [
    "traffic", "access_reset", "access_reconcile", "access_backfill",
    "recover", "scan", "claim", "finish",
])
async def test_actual_worker_keeps_kernel_lock_after_awaiting_task_cancel(
    tmp_path, barriers, stage,
):
    barrier, external = barriers
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    output = tmp_path / "completed-write.txt"
    loop_thread = threading.get_ident()

    def callback(current):
        assert current_backup_child_fds()
        assert threading.get_ident() != loop_thread
        if current != stage:
            return
        entered.set()
        try:
            assert release.wait(10)
            output.write_text(stage, encoding="utf-8")
        finally:
            finished.set()

    store = SyntheticStore(callback, has_claim=stage == "finish")
    worker = make_worker(stage, store, barrier)
    task = asyncio.create_task(worker.run() if stage == "access_backfill" else worker.tick())
    try:
        await wait_for_thread(entered)
        await cancel_task(task)
        assert task.cancelled() and not finished.is_set()
        assert not output.exists()
        for lock in (barrier, external):
            with pytest.raises(BackupBusyError):
                snapshot_once(lock)
    finally:
        release.set()
        await wait_for_thread(finished)
        await cancel_task(task)
        await wait_idle(barrier)
    assert output.read_text(encoding="utf-8") == stage
    snapshot_once(barrier)
    snapshot_once(external)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["traffic", "access_reset", "recover"])
async def test_paused_cycle_does_no_work_and_is_admitted_after_resume(barriers, stage):
    barrier, external = barriers
    store = SyntheticStore(lambda _stage: None)
    worker = make_worker(stage, store, barrier)
    with external.snapshot(timeout=0), pytest.raises(BackupBusyError):
        await worker.tick()
    assert store.calls == []
    await worker.tick()
    assert store.calls
    snapshot_once(external)


@pytest.mark.asyncio
async def test_notification_network_wait_is_part_of_the_operation(barriers):
    barrier, external = barriers
    store = SyntheticStore(lambda _stage: None, has_claim=True)
    transport = WaitingTransport()
    worker = NotificationWorker(store, transport, clock=lambda: NOW, backup_writes=barrier)
    task = asyncio.create_task(worker.tick())
    try:
        await asyncio.wait_for(transport.started.wait(), 5)
        assert store.calls == ["recover", "scan", "claim"]
        with pytest.raises(BackupBusyError):
            snapshot_once(external)
        # Completion traffic is a separate admission kind, not an idle WS lease.
        with barrier.operation(kind="agent"):
            assert not transport.closed
    finally:
        await cancel_task(task)
        await wait_idle(barrier)
    assert transport.closed
    assert [receipt.state for receipt in store.receipts] == ["unknown"]
    snapshot_once(external)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_shutdown_again", [False, True])
async def test_late_shutdown_receipt_keeps_lock_after_tick_has_finished(
    tmp_path, barriers, cancel_shutdown_again,
):
    barrier, external = barriers
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    output = tmp_path / "late-receipt.txt"

    def callback(stage):
        if stage != "finish":
            return
        assert current_backup_child_fds()
        entered.set()
        try:
            assert release.wait(12)
            output.write_text("unknown", encoding="utf-8")
        finally:
            finished.set()

    store = SyntheticStore(callback, has_claim=True)
    transport = WaitingTransport()
    worker = NotificationWorker(store, transport, clock=lambda: NOW, backup_writes=barrier)
    task = asyncio.create_task(worker.tick())
    try:
        await asyncio.wait_for(transport.started.wait(), 5)
        task.cancel()
        await wait_for_thread(entered)
        if cancel_shutdown_again:
            task.cancel()
        # The first variant exercises the product's real three-second bounded
        # shutdown wait; the second directly cancels that awaiting task again.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 6)
        assert transport.closed and not finished.is_set()
        assert not output.exists()
        with pytest.raises(BackupBusyError):
            snapshot_once(external)
    finally:
        release.set()
        await wait_for_thread(finished)
        await cancel_task(task)
        await wait_idle(barrier)
    assert output.read_text(encoding="utf-8") == "unknown"
    assert [receipt.state for receipt in store.receipts] == ["unknown"]
    snapshot_once(external)


@pytest.mark.asyncio
async def test_access_dispatch_and_its_final_write_are_inside_work_scope(barriers):
    barrier, external = barriers
    started = asyncio.Event()
    final_writes = []
    store = SyntheticStore(lambda _stage: None)
    store.run_once = lambda: ["synthetic-command"]

    class Connections:
        async def dispatch_command(self, current_store, command):
            assert current_store is store and command == "synthetic-command"
            assert current_backup_child_fds()
            started.set()
            try:
                await asyncio.Future()
            finally:
                assert current_backup_child_fds()
                with pytest.raises(BackupBusyError):
                    snapshot_once(external)
                final_writes.append(command)

    worker = SubscriptionAccessWorker(store, Connections(), backup_writes=barrier)
    task = asyncio.create_task(worker.tick())
    try:
        await asyncio.wait_for(started.wait(), 5)
        with pytest.raises(BackupBusyError):
            snapshot_once(external)
    finally:
        await cancel_task(task)
        await wait_idle(barrier)
    assert final_writes == ["synthetic-command"]
    snapshot_once(external)
