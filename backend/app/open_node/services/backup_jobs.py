"""Session-owned, short-lived Web backup jobs for one Web process.

One dedicated thread, started with an empty context, owns the separate jobs
leader flock for its actual lifetime. Other processes fail closed; this is not a
distributed queue. The existing creator alone coordinates application writers.
No request lease, password, factor code, plaintext archive or output path is
retained here. A restart loses this bounded in-memory job history.

Only complete anonymous read-only ciphertext survives creation. Deletion,
expiry, revocation and shutdown stop subsequent reads, not bytes already sent.
The download lock covers each bounded pread and descriptor close, preventing
close/reuse races. Shutdown never claims a still-running creator has stopped.
"""

from __future__ import annotations

import fcntl
import io
import math
import os
import re
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import Context
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import RFC_4122, UUID

from open_node.services.backup_authorization import BackupAuthorization
from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier
from open_node.services.backup_creation import create_control_plane_backup
from open_node.services.backup_encryption import MAX_ENCRYPTED_ARCHIVE_BYTES, _recipient
from open_node.services.backup_state import BackupStateLayout

JOB_TTL_SECONDS = 900
MAX_READY_JOBS = 2
MAX_JOB_HISTORY = 20
DOWNLOAD_CHUNK_BYTES = 65536
_ERRORS = {
    "backup_invalid_request": 422,
    "backup_not_found": 404,
    "backup_busy": 409,
    "backup_not_ready": 409,
    "backup_request_conflict": 409,
    "backup_worker_unavailable": 503,
    "backup_authorization_expired": 403,
    "backup_creation_failed": 500,
    "backup_expired": 410,
}
_ACTIVE = frozenset({"queued", "running", "ready"})


class BackupJobError(RuntimeError):
    def __init__(self, code: str, status_code: int) -> None:
        request_error = code == "backup_invalid_request" and status_code in {413, 415, 416, 422}
        if not request_error and _ERRORS.get(code) != status_code:
            code, status_code = "backup_creation_failed", 500
        self.code, self.status_code = code, status_code
        super().__init__("Backup operation is unavailable.")


def _error(code: str) -> BackupJobError:
    return BackupJobError(code, _ERRORS[code])


def _identifier(value: str) -> None:
    try:
        parsed = UUID(value) if type(value) is str and len(value) == 36 else None
        if (parsed is None or parsed.version != 4
                or parsed.variant != RFC_4122 or str(parsed) != value):
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise _error("backup_invalid_request") from None


def _public_recipient(value: str) -> None:
    try:
        _recipient(value)
    except Exception:
        raise _error("backup_invalid_request") from None


def _close(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            # The owner first clears its integer, and never retries close on a
            # potentially reused descriptor. No raw OS error reaches an API.
            pass


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class _Job:
    id: str
    recipient: str = field(repr=False)
    authorization: BackupAuthorization = field(repr=False)
    created: float
    expires: float
    deadline: float
    status: str = "queued"
    size: int | None = None
    sha256: str | None = None
    error_code: str | None = None
    fd: int | None = field(default=None, repr=False)

    def dto(self) -> dict:
        return {
            "id": self.id, "status": self.status, "created_at": _iso(self.created),
            "expires_at": _iso(self.expires), "size": self.size, "sha256": self.sha256,
            "error_code": self.error_code, "restoration_ready": False,
        }


class BackupDownload:
    """Borrowed context-bound stream; no descriptor or server path is exposed."""

    __slots__ = ("_manager", "_job", "_fd", "_position", "size", "sha256", "filename")

    def __init__(self, manager: BackupJobManager, job: _Job, fd: int) -> None:
        self._manager, self._job, self._fd, self._position = manager, job, fd, 0
        self.size, self.sha256 = job.size, job.sha256
        self.filename = f"open-node-backup-{job.id}.zip.age"

    def read(self, size: int) -> bytes:
        return self._manager._read_download(self, size)


class BackupJobManager:
    def __init__(
        self, layout: BackupStateLayout, barrier: BackupWriteBarrier, *,
        is_authorized: Callable[[BackupAuthorization], bool], totp_key: bytes | None = None,
        agent_public_key: bytes | None = None, temporary_directory: Path | None = None,
    ) -> None:
        self._pid = os.getpid()
        self._layout, self._barrier = layout, barrier
        self._is_authorized = is_authorized
        self._totp_key, self._agent_public_key = totp_key, agent_public_key
        self._temporary_parent = (Path("/tmp") if temporary_directory is None
                                  else temporary_directory)
        self._condition = threading.Condition(threading.RLock())
        self._jobs: OrderedDict[str, _Job] = OrderedDict()
        self._active: _Job | None = None
        self._download: BackupDownload | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._accepting = False
        self._stopping = False
        self._leader_permit = None
        self._leader_path = (
            layout.state_root or layout.database.parent
        ) / ".open-node-backup-jobs.lock"

    def _check_pid(self) -> None:
        if os.getpid() != self._pid:
            raise _error("backup_worker_unavailable")

    def _available_locked(self) -> bool:
        if not self._accepting or self._stopping or self._leader_permit is None:
            return False
        try:
            self._leader_permit.assert_for_lock(self._leader_path)
        except Exception:
            self._accepting = False
            self._stopping = True
            self._condition.notify_all()
            return False
        return self._thread is not None and self._thread.is_alive()

    @property
    def available(self) -> bool:
        if os.getpid() != self._pid:
            return False
        with self._condition:
            return self._available_locked()

    @property
    def unavailable_code(self) -> str | None:
        return None if self.available else "backup_worker_unavailable"

    def start(self) -> None:
        self._check_pid()
        with self._condition:
            if self._thread is not None or self._stopping:
                return
            self._thread = threading.Thread(
                target=Context().run, args=(self._run,), name="open-node-backup-jobs", daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._stopping = True
                self._ready.set()
                return
        if not self._ready.wait(5):
            with self._condition:
                self._stopping = True
                self._condition.notify_all()

    def close(self, timeout: float = 5) -> bool:
        try:
            valid_timeout = (type(timeout) in (int, float)
                             and math.isfinite(timeout) and timeout >= 0)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise _error("backup_invalid_request")
        if os.getpid() != self._pid:
            return False
        with self._condition:
            self._stopping, self._accepting = True, False
            for job in self._jobs.values():
                if job.status in _ACTIVE:
                    self._discard_locked(job, "cancelled")
            self._condition.notify_all()
            worker = self._thread
        if worker is None:
            return True
        if worker is threading.current_thread():
            return False
        if worker.ident is None:
            return True
        worker.join(min(timeout, threading.TIMEOUT_MAX))
        return not worker.is_alive()

    def _authorized(self, authorization: BackupAuthorization) -> bool:
        try:
            return (
                type(authorization) is BackupAuthorization
                and type(authorization.session_hash) is str and bool(authorization.session_hash)
                and type(authorization.security_epoch) is str and bool(authorization.security_epoch)
                and type(authorization.expires_at) in (int, float)
                and math.isfinite(authorization.expires_at)
                and authorization.expires_at > time.time()
                and self._is_authorized(authorization) is True
            )
        except Exception:
            return False

    def _discard_locked(self, job: _Job, status: str, code: str | None = None) -> None:
        job.status, job.error_code = status, code
        fd, job.fd = job.fd, None
        _close(fd)
        if self._download is not None and self._download._job is job:
            self._finish_download_locked(self._download)

    def _expire_locked(self) -> None:
        now, monotonic = time.time(), time.monotonic()
        for job in self._jobs.values():
            if job.status in _ACTIVE and (now >= job.expires or monotonic >= job.deadline):
                self._discard_locked(job, "expired", "backup_expired")

    def _lookup_locked(self, job_id: str, session_hash: str) -> _Job:
        _identifier(job_id)
        job = self._jobs.get(job_id)
        if job is None or job.authorization.session_hash != session_hash:
            raise _error("backup_not_found")
        return job

    def list_jobs(self, session_hash: str) -> list[dict]:
        self._check_pid()
        with self._condition:
            self._expire_locked()
            return [job.dto() for job in reversed(self._jobs.values())
                    if job.authorization.session_hash == session_hash]

    def get_job(self, job_id: str, session_hash: str) -> dict:
        self._check_pid()
        with self._condition:
            self._expire_locked()
            return self._lookup_locked(job_id, session_hash).dto()

    def find_job(self, job_id: str, session_hash: str, recipient: str) -> dict | None:
        self._check_pid()
        _identifier(job_id)
        _public_recipient(recipient)
        with self._condition:
            self._expire_locked()
            if job_id not in self._jobs:
                return None
            job = self._lookup_locked(job_id, session_hash)
            if job.recipient != recipient:
                raise _error("backup_request_conflict")
            return job.dto()

    def submit(self, job_id: str, recipient: str, authorization: BackupAuthorization) -> dict:
        self._check_pid()
        _identifier(job_id)
        _public_recipient(recipient)
        if type(authorization) is not BackupAuthorization:
            raise _error("backup_authorization_expired")
        with self._condition:
            self._expire_locked()
            if job_id in self._jobs:
                job = self._lookup_locked(job_id, authorization.session_hash)
                if job.recipient != recipient:
                    raise _error("backup_request_conflict")
                return job.dto()
            if not self._available_locked():
                raise _error("backup_worker_unavailable")
            if not self._authorized(authorization):
                raise _error("backup_authorization_expired")
            if self._active is not None or any(
                job.status == "queued" for job in self._jobs.values()
            ):
                raise _error("backup_busy")
            if sum(job.status == "ready" for job in self._jobs.values()) >= MAX_READY_JOBS:
                raise _error("backup_busy")
            while len(self._jobs) >= MAX_JOB_HISTORY:
                oldest = next((key for key, job in self._jobs.items()
                               if job.status not in _ACTIVE and job is not self._active), None)
                if oldest is None:
                    raise _error("backup_busy")
                del self._jobs[oldest]
            now = time.time()
            expires = min(now + JOB_TTL_SECONDS, authorization.expires_at)
            job = _Job(job_id, recipient, authorization, now, expires,
                       time.monotonic() + max(0, expires - now))
            self._jobs[job_id] = job
            self._condition.notify_all()
            return job.dto()

    def delete_job(self, job_id: str, session_hash: str) -> None:
        self._check_pid()
        with self._condition:
            self._expire_locked()
            job = self._lookup_locked(job_id, session_hash)
            self._discard_locked(job, "cancelled")
            self._condition.notify_all()

    def _downloadable_locked(self, job: _Job) -> None:
        self._expire_locked()
        if job.status == "expired":
            raise _error("backup_expired")
        if job.status == "cancelled" and job.error_code == "backup_authorization_expired":
            raise _error("backup_authorization_expired")
        if job.status != "ready" or job.fd is None:
            raise _error("backup_not_ready")
        if not self._authorized(job.authorization):
            self._discard_locked(job, "cancelled", "backup_authorization_expired")
            raise _error("backup_authorization_expired")

    @contextmanager
    def download(self, job_id: str, session_hash: str) -> Iterator[BackupDownload]:
        self._check_pid()
        with self._condition:
            job = self._lookup_locked(job_id, session_hash)
            self._downloadable_locked(job)
            if not self._available_locked():
                raise _error("backup_worker_unavailable")
            if self._download is not None:
                raise _error("backup_busy")
            try:
                fd = fcntl.fcntl(job.fd, fcntl.F_DUPFD_CLOEXEC, 3)
            except OSError:
                raise _error("backup_creation_failed") from None
            download = self._download = BackupDownload(self, job, fd)
        try:
            yield download
        finally:
            # No inherited mutex or descriptor ownership after fork.
            if os.getpid() == self._pid:
                with self._condition:
                    self._finish_download_locked(download)

    def _finish_download_locked(self, download: BackupDownload) -> None:
        fd, download._fd = download._fd, None
        if self._download is download:
            self._download = None
        _close(fd)

    def _read_download(self, download: BackupDownload, size: int) -> bytes:
        self._check_pid()
        if type(size) is not int or size <= 0:
            raise _error("backup_invalid_request")
        with self._condition:
            job = download._job
            self._downloadable_locked(job)
            if self._download is not download or download._fd is None:
                raise _error("backup_not_ready")
            if not self._available_locked():
                raise _error("backup_worker_unavailable")
            count = min(size, DOWNLOAD_CHUNK_BYTES, download.size - download._position)
            if count <= 0:
                return b""
            try:
                raw = os.pread(download._fd, count, download._position)
                if len(raw) != count:
                    raise OSError
            except OSError:
                self._discard_locked(job, "failed", "backup_creation_failed")
                raise _error("backup_creation_failed") from None
            download._position += len(raw)
            return raw

    @staticmethod
    def _retain(created) -> tuple[int, int, str]:
        stream, report = created.stream, created.encryption
        size, digest = report.encrypted_size, report.encrypted_sha256
        if (
            not isinstance(stream, io.FileIO) or stream.writable() or not stream.readable()
            or type(size) is not int or not 1 <= size <= MAX_ENCRYPTED_ARCHIVE_BYTES
            or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or created.restoration_ready is not False
        ):
            raise _error("backup_creation_failed")
        original = stream.fileno()
        info = os.fstat(original)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_size != size or info.st_nlink != 0
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
            or fcntl.fcntl(original, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
        ):
            raise _error("backup_creation_failed")
        descriptor = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 3)
        try:
            if os.fstat(descriptor) != info or os.get_inheritable(descriptor):
                raise _error("backup_creation_failed")
            return descriptor, size, digest
        except BaseException:
            _close(descriptor)
            raise

    def _create(self, job: _Job, staging: Path) -> None:
        fd = None
        try:
            with create_control_plane_backup(
                self._layout, barrier=self._barrier, recipient=job.recipient,
                staging_directory=staging, totp_key=self._totp_key,
                agent_public_key=self._agent_public_key,
            ) as created:
                fd, size, digest = self._retain(created)
            # Publish only after the creator context has also closed. A cleanup
            # exception cannot leave a supposedly ready response behind.
            with self._condition:
                self._expire_locked()
                if job.status != "running" or self._stopping:
                    return
                if not self._available_locked():
                    self._discard_locked(job, "cancelled", "backup_worker_unavailable")
                elif not self._authorized(job.authorization):
                    self._discard_locked(job, "cancelled", "backup_authorization_expired")
                else:
                    job.fd, fd = fd, None
                    job.size, job.sha256, job.status = size, digest, "ready"
        except Exception as error:
            with self._condition:
                if job.status == "running":
                    code = ("backup_busy" if isinstance(error, BackupBusyError)
                            else "backup_creation_failed")
                    self._discard_locked(job, "failed", code)
        finally:
            _close(fd)
            with self._condition:
                if self._active is job:
                    self._active = None
                self._condition.notify_all()

    def _run(self) -> None:
        leader = None
        staging = None
        try:
            leader = BackupWriteBarrier(self._leader_path)
            with leader.snapshot(timeout=0) as permit:
                staging = Path(tempfile.mkdtemp(
                    prefix="open-node-backup-jobs-", dir=self._temporary_parent,
                ))
                with self._condition:
                    self._leader_permit = permit
                    self._accepting = not self._stopping
                    self._ready.set()
                    self._condition.notify_all()
                while True:
                    with self._condition:
                        if not self._available_locked():
                            break
                        self._expire_locked()
                        job = next((item for item in self._jobs.values()
                                    if item.status == "queued"), None)
                        if job is None:
                            self._condition.wait(1)
                            continue
                        if not self._authorized(job.authorization):
                            self._discard_locked(job, "cancelled", "backup_authorization_expired")
                            continue
                        self._active, job.status = job, "running"
                    self._create(job, staging)
        except BaseException:
            # A broken leader, storage failure, or exceptional thread exit is a
            # fixed unavailable state, never a traceback/path in a job response.
            pass
        finally:
            with self._condition:
                self._accepting, self._stopping, self._leader_permit = False, True, None
                for job in self._jobs.values():
                    if job.status in _ACTIVE:
                        self._discard_locked(job, "cancelled", "backup_worker_unavailable")
                self._ready.set()
                self._condition.notify_all()
            if leader is not None:
                try:
                    leader.close()
                except Exception:
                    pass
            if staging is not None:
                try:
                    staging.rmdir()
                except OSError:
                    # Only the directory we created may be removed. Never
                    # recursively delete paths left by an unexpected producer.
                    pass
