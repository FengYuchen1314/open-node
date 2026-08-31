"""Bounded job-manager checks with a controlled producer, not crypto success tests.

The producer supplies real anonymous RO descriptors; actual age/SQLite creation
is covered independently. These tests exercise real threads, flock, pread,
session isolation, cancellation and descriptor ownership without network access.
"""

import errno
import fcntl
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from uuid import uuid4

import pytest
from open_node.services import backup_jobs as module
from open_node.services.backup_authorization import BackupAuthorization
from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.backup_jobs import BackupJobError, BackupJobManager
from open_node.services.backup_state import BackupStateLayout

RECIPIENT = "age1" + "q" * 58
OTHER_RECIPIENT = "age1" + "p" * 58
PAYLOAD = b"controlled job-manager ciphertext stand-in\n" * 2500
REQUEST_CONTEXT = ContextVar("backup-job-test-request-context", default="empty")
DTO_FIELDS = {"id", "status", "created_at", "expires_at", "size", "sha256",
              "error_code", "restoration_ready"}


class Producer:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls, self.streams, self.contexts = [], [], []
        self.failure = None
        self.cleanup_failure = False

    @contextmanager
    def __call__(self, layout, **kwargs):
        self.calls.append((layout, kwargs))
        self.contexts.append((REQUEST_CONTEXT.get(), kwargs["barrier"]._context.get()))
        self.entered.set()
        assert self.release.wait(5)
        if self.failure is not None:
            raise self.failure
        with tempfile.TemporaryFile("w+b", buffering=0, dir=kwargs["staging_directory"]) as output:
            output.write(PAYLOAD)
            descriptor = os.open(f"/proc/self/fd/{output.fileno()}", os.O_RDONLY | os.O_CLOEXEC)
            with io.FileIO(descriptor, "rb", closefd=True) as stream:
                self.streams.append(stream)
                yield SimpleNamespace(
                    stream=stream, restoration_ready=False,
                    encryption=SimpleNamespace(
                        encrypted_size=len(PAYLOAD),
                        encrypted_sha256=hashlib.sha256(PAYLOAD).hexdigest(),
                    ),
                )
                if self.cleanup_failure:
                    raise OSError("private cleanup path and secret must not escape")


@pytest.fixture
def case(tmp_path, monkeypatch):
    data, staging = tmp_path / "data", tmp_path / "staging"
    data.mkdir(mode=0o700)
    staging.mkdir(mode=0o700)
    layout = BackupStateLayout(data / "database.sqlite3", data / "certificates", None, None, None)
    barrier = BackupWriteBarrier(data / ".open-node-backup.lock")
    producer = Producer()
    monkeypatch.setattr(module, "create_control_plane_backup", producer)
    managers = []
    allowed = [True]

    def make(*, start=True, authorization_check=None):
        manager = BackupJobManager(
            layout, barrier, is_authorized=authorization_check or (lambda _grant: allowed[0]),
            temporary_directory=staging,
        )
        managers.append(manager)
        if start:
            manager.start()
        return manager

    grant = BackupAuthorization("session-one", "security-epoch", time.time() + 900)
    yield SimpleNamespace(make=make, producer=producer, grant=grant, allowed=allowed,
                          staging=staging, layout=layout, barrier=barrier)
    producer.release.set()
    for manager in reversed(managers):
        assert manager.close(5)
    barrier.close()
    assert list(staging.iterdir()) == []


def submit(case, manager, job_id=None):
    return manager.submit(str(uuid4()) if job_id is None else job_id, RECIPIENT, case.grant)


def wait_status(manager, job_id, status, session="session-one"):
    deadline = time.monotonic() + 5
    while True:
        result = manager.get_job(job_id, session)
        if result["status"] == status:
            return result
        assert time.monotonic() < deadline, result
        with manager._condition:
            manager._condition.wait(0.02)


def expect_error(code, function, *args):
    with pytest.raises(BackupJobError) as raised:
        function(*args)
    assert raised.value.code == code
    assert str(raised.value) == "Backup operation is unavailable."
    return raised.value.status_code


def ready(case, manager):
    job = submit(case, manager)
    return wait_status(manager, job["id"], "ready")


def test_start_stop_and_real_cross_process_single_worker(case):
    manager = case.make(start=False)
    assert not manager.available and manager.unavailable_code == "backup_worker_unavailable"
    expect_error("backup_worker_unavailable", submit, case, manager)
    manager.start()
    manager.start()
    assert manager.available and manager.unavailable_code is None
    # A new interpreter must not become a second Web backup worker on this DB.
    script = """
import json, sys
from pathlib import Path
from open_node.services.backup_coordination import BackupWriteBarrier
from open_node.services.backup_jobs import BackupJobManager
from open_node.services.backup_state import BackupStateLayout
root = Path(sys.argv[1])
barrier = BackupWriteBarrier(root / '.open-node-backup.lock')
manager = BackupJobManager(BackupStateLayout(root / 'database.sqlite3', root / 'certificates',
                                           None, None, None), barrier, is_authorized=lambda _: True)
manager.start()
print(json.dumps({'available': manager.available, 'code': manager.unavailable_code,
                  'closed': manager.close()}))
barrier.close()
"""
    process = subprocess.run([sys.executable, "-B", "-c", script, str(case.layout.database.parent)],
                             capture_output=True, timeout=10, check=False)
    assert process.returncode == 0 and process.stderr == b""
    assert json.loads(process.stdout) == {
        "available": False, "code": "backup_worker_unavailable", "closed": True,
    }
    assert manager.close() and not manager.available
    assert case.make().available


def test_worker_does_not_inherit_request_context_or_live_lease(case):
    marker = REQUEST_CONTEXT.set("request-private-context")
    try:
        with case.barrier.operation():
            manager = case.make()
            job = submit(case, manager)
            wait_status(manager, job["id"], "ready")
    finally:
        REQUEST_CONTEXT.reset(marker)
    assert case.producer.contexts == [("empty", None)]
    assert manager._thread is not threading.current_thread()


def test_uuid_idempotency_owner_isolation_and_recipient_conflict(case):
    case.producer.release.clear()
    manager = case.make()
    job = submit(case, manager)
    assert case.producer.entered.wait(2)
    assert submit(case, manager, job["id"])["id"] == job["id"]
    assert manager.find_job(job["id"], "session-one", RECIPIENT)["id"] == job["id"]
    assert len(case.producer.calls) == 1
    assert manager.list_jobs("other-session") == []
    assert manager.find_job(str(uuid4()), "session-one", RECIPIENT) is None
    expect_error("backup_not_found", manager.get_job, job["id"], "other-session")
    expect_error("backup_not_found", manager.find_job, job["id"], "other-session", RECIPIENT)
    expect_error("backup_request_conflict", manager.find_job,
                 job["id"], "session-one", OTHER_RECIPIENT)
    expect_error("backup_request_conflict", manager.submit, job["id"], OTHER_RECIPIENT, case.grant)
    other = BackupAuthorization("other-session", "epoch", time.time() + 900)
    expect_error("backup_not_found", manager.submit, job["id"], RECIPIENT, other)


def test_running_delete_does_not_release_actual_work_slot(case):
    case.producer.release.clear()
    manager = case.make()
    job = submit(case, manager)
    assert case.producer.entered.wait(2)
    manager.delete_job(job["id"], "session-one")
    assert manager.get_job(job["id"], "session-one")["status"] == "cancelled"
    expect_error("backup_busy", submit, case, manager)
    case.producer.release.set()
    with manager._condition:
        assert manager._condition.wait_for(lambda: manager._active is None, timeout=3)
    assert all(stream.closed for stream in case.producer.streams)
    assert ready(case, manager)["status"] == "ready"


def test_close_timeout_retains_leader_until_creator_actually_finishes(case):
    case.producer.release.clear()
    manager = case.make()
    job = submit(case, manager)
    assert case.producer.entered.wait(2)
    assert manager.close(timeout=0) is False
    assert manager._thread.is_alive() and not manager.available
    assert not case.make().available
    expect_error("backup_worker_unavailable", submit, case, manager)
    case.producer.release.set()
    assert manager.close(timeout=3) is True
    assert manager.get_job(job["id"], "session-one")["status"] == "cancelled"
    assert case.make().available


def test_ready_capacity_does_not_evict_other_complete_packages(case):
    manager = case.make()
    first, second = ready(case, manager), ready(case, manager)
    expect_error("backup_busy", submit, case, manager)
    manager.delete_job(first["id"], "session-one")
    third = ready(case, manager)
    assert third["id"] != second["id"]
    assert manager.get_job(second["id"], "session-one")["sha256"] == second["sha256"]
    with manager.download(second["id"], "session-one") as download:
        assert download.read(10) == PAYLOAD[:10]
    assert sum(job["status"] == "ready" for job in manager.list_jobs("session-one")) == 2


def test_ready_handoff_closed_producer_readonly_anonymous_fd_and_bounded_reads(case):
    manager = case.make()
    job = ready(case, manager)
    assert set(job) == DTO_FIELDS and job["restoration_ready"] is False
    assert RECIPIENT not in json.dumps(job) and case.grant.session_hash not in json.dumps(job)
    assert all(stream.closed for stream in case.producer.streams)
    descriptor = manager._jobs[job["id"]].fd
    info = os.fstat(descriptor)
    assert info.st_nlink == 0 and stat.S_IMODE(info.st_mode) == 0o600
    assert fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
    assert not os.get_inheritable(descriptor)
    with manager.download(job["id"], "session-one") as download:
        assert download.filename == f"open-node-backup-{job['id']}.zip.age"
        first = download.read(10**9)
        assert len(first) == 65536
        remaining = download.read(65536)
        assert first + remaining == PAYLOAD and download.read(65536) == b""
    assert os.lseek(descriptor, 0, os.SEEK_CUR) == 0


def test_only_one_download_independent_offsets_and_consumer_exception_cleanup(case):
    manager = case.make()
    job = ready(case, manager)
    with manager.download(job["id"], "session-one") as first:
        assert first.read(7) == PAYLOAD[:7]
        with pytest.raises(BackupJobError, match="Backup operation is unavailable") as raised:
            with manager.download(job["id"], "session-one"):
                pytest.fail("second download admitted")
        assert raised.value.code == "backup_busy"
    with pytest.raises(LookupError, match="consumer"):
        with manager.download(job["id"], "session-one") as second:
            assert second.read(7) == PAYLOAD[:7]
            raise LookupError("consumer")
    assert second._fd is None and manager._download is None
    with manager.download(job["id"], "session-one") as third:
        assert third.read(7) == PAYLOAD[:7]
    expect_error("backup_not_ready", first.read, 7)


def test_delete_serializes_with_pread_and_old_context_cannot_close_reused_fd(case, monkeypatch):
    manager = case.make()
    job = ready(case, manager)
    entered, release, deleting, deleted = (threading.Event() for _ in range(4))
    original = os.pread

    def blocked_read(fd, size, offset):
        entered.set()
        assert release.wait(3)
        return original(fd, size, offset)

    monkeypatch.setattr(module.os, "pread", blocked_read)
    context = manager.download(job["id"], "session-one")
    download = context.__enter__()

    def remove():
        deleting.set()
        manager.delete_job(job["id"], "session-one")
        deleted.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reading = executor.submit(download.read, 8)
            try:
                assert entered.wait(2)
                removal = executor.submit(remove)
                assert deleting.wait(2) and not deleted.wait(0.02)
            finally:
                release.set()
            assert reading.result(timeout=2) == PAYLOAD[:8]
            removal.result(timeout=2)
        assert download._fd is None
        expect_error("backup_not_ready", download.read, 8)
        # New files may reuse released integers; old context cleanup must not
        # close an unrelated descriptor after its own fd was cleared.
        unrelated = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
        try:
            context.__exit__(None, None, None)
            assert os.fstat(unrelated).st_nlink > 0
        finally:
            os.close(unrelated)
    finally:
        release.set()
        context.__exit__(None, None, None)


def test_authorization_checked_in_worker_before_creator(case):
    caller = threading.current_thread()
    manager = case.make(authorization_check=lambda _grant: threading.current_thread() is caller)
    job = submit(case, manager)
    result = wait_status(manager, job["id"], "cancelled")
    assert result["error_code"] == "backup_authorization_expired"
    assert case.producer.calls == []


def test_authorization_revoked_before_publish_never_makes_ready(case):
    case.producer.release.clear()
    manager = case.make()
    job = submit(case, manager)
    assert case.producer.entered.wait(2)
    case.allowed[0] = False
    case.producer.release.set()
    result = wait_status(manager, job["id"], "cancelled")
    assert result["error_code"] == "backup_authorization_expired"
    assert result["size"] is None and manager._jobs[job["id"]].fd is None
    assert all(stream.closed for stream in case.producer.streams)


def test_authorization_revocation_stops_each_subsequent_download_read(case):
    manager = case.make()
    job = ready(case, manager)
    with manager.download(job["id"], "session-one") as download:
        assert download.read(4) == PAYLOAD[:4]
        case.allowed[0] = False
        assert expect_error("backup_authorization_expired", download.read, 4) == 403
        assert download._fd is None
    assert manager.get_job(job["id"], "session-one")["status"] == "cancelled"


def test_ttl_expiry_closes_stored_and_download_fds(case):
    manager = case.make()
    job = ready(case, manager)
    with manager.download(job["id"], "session-one") as download:
        assert download.read(4) == PAYLOAD[:4]
        with manager._condition:
            # Deterministic passage beyond this job's clock budget; no global
            # clock monkeypatch affects unrelated tests or real thread joins.
            manager._jobs[job["id"]].deadline = time.monotonic() - 1
        assert expect_error("backup_expired", download.read, 4) == 410
        assert download._fd is None
    assert manager.get_job(job["id"], "session-one")["status"] == "expired"
    assert manager._jobs[job["id"]].fd is None


@pytest.mark.parametrize("during_cleanup", [False, True])
def test_disk_or_cleanup_failure_is_safe_failed_not_ready(case, during_cleanup):
    if during_cleanup:
        case.producer.cleanup_failure = True
    else:
        case.producer.failure = OSError(errno.ENOSPC, "secret and private path")
    manager = case.make()
    result = wait_status(manager, submit(case, manager)["id"], "failed")
    assert result["error_code"] == "backup_creation_failed"
    assert result["size"] is result["sha256"] is None
    assert "secret" not in json.dumps(result) and "path" not in json.dumps(result)
    assert all(stream.closed for stream in case.producer.streams)
    assert manager._jobs[result["id"]].fd is None


def test_history_is_bounded_and_input_and_shutdown_validation_are_fixed(case):
    manager = case.make()
    original = None
    with manager._condition:
        for _ in range(25):
            job = submit(case, manager)
            original = original or job["id"]
            manager.delete_job(job["id"], "session-one")
    assert len(manager.list_jobs("session-one")) == 20
    expect_error("backup_not_found", manager.get_job, original, "session-one")
    for invalid in ("not-a-uuid", str(uuid4()).upper(), "0" * 36):
        expect_error("backup_invalid_request", manager.submit, invalid, RECIPIENT, case.grant)
    expect_error("backup_invalid_request", manager.submit, str(uuid4()), "private-key", case.grant)
    for timeout in (True, -1, float("nan"), float("inf")):
        expect_error("backup_invalid_request", manager.close, timeout)
    for status in (413, 415, 416, 422):
        error = BackupJobError("backup_invalid_request", status)
        assert error.code == "backup_invalid_request" and error.status_code == status
    assert manager.available


def test_unsafe_leader_lock_fails_closed_without_touching_target(case):
    target = case.layout.database.parent / "unrelated"
    target.write_bytes(b"must not change")
    target.chmod(0o600)
    lock = case.layout.database.parent / ".open-node-backup-jobs.lock"
    lock.symlink_to(target)
    manager = case.make()
    assert not manager.available and manager.unavailable_code == "backup_worker_unavailable"
    assert target.read_bytes() == b"must not change"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not case.layout.database.exists()
