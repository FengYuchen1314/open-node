"""An opt-in, process-local admission barrier backed by Linux advisory flock.

This is infrastructure only: it does not discover or wrap application writers,
take a database snapshot, or make an archive safe to restore. Every cooperating
writer must hold an operation lease for its *actual* lifetime. A subprocess must
inherit lease.child_fds with pass_fds and retain them until its work has ended.

Snapshots first stop new work while allowing agent completion messages, then
stop agents, drain their leases, and acquire a separately opened exclusive lock.
The admission stages belong to this instance, not to other processes. Other
instances/processes coordinate only through SH/EX locks and can cause a timeout.
Uncooperative writers are not covered. Use a stable, trusted local directory;
this module does not promise network-filesystem locking or hostile same-process
descriptor safety. Call snapshot from a dedicated thread, not an event loop.

Each independent operation opens the pinned inode again. Nested same-kind calls
may join a live, reference-counted context record; an expired copied context is
never authority to bypass admission. run_sync acquires inside the real worker
thread, so cancellation of its awaiting task cannot prematurely release a lease.
Only close is used to release flock descriptors: explicit LOCK_UN would also
unlock a surviving child's inherited open-file description.
"""

from __future__ import annotations

import asyncio
import fcntl
import math
import os
import stat
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar

OperationKind = Literal["work", "agent"]
_T = TypeVar("_T")
_LOCK_POLL_SECONDS = 0.025


class BackupCoordinationError(RuntimeError):
    code = "backup_coordination_unavailable"
    _message = "Backup coordination is unavailable."

    def __init__(self) -> None:
        super().__init__(self._message)


class BackupBusyError(BackupCoordinationError):
    code = "backup_busy"
    _message = "Backup coordination is busy; try again later."


def _close_fds(*fds: int | None) -> None:
    failed = False
    for fd in fds:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                failed = True
    if failed:
        raise BackupCoordinationError() from None


def _finalize_pinned_fds(pid: int, anchor_fd: int, parent_fd: int) -> None:
    # No instance/mutex reference, no interpreter-exit work, and no inherited
    # parent-PID cleanup after fork. Active leases/threads retain the instance.
    if os.getpid() != pid:
        return
    for fd in (anchor_fd, parent_fd):
        try:
            os.close(fd)
        except OSError:
            pass


@dataclass(eq=False, slots=True)
class _LeaseRecord:
    kind: OperationKind
    fd: int | None
    holders: set[BackupWriteLease] = field(default_factory=set)


class BackupWriteLease:
    """A scoped reference, not a reusable or serializable authorization token."""

    __slots__ = ("_barrier", "_record")

    def __init__(self, barrier: BackupWriteBarrier, record: _LeaseRecord) -> None:
        self._barrier, self._record = barrier, record

    @property
    def child_fds(self) -> tuple[int, ...]:
        barrier = self._barrier
        barrier._check_pid()
        with barrier._condition:
            barrier._check_open()
            if not barrier._record_active(self._record) or self not in self._record.holders:
                raise BackupCoordinationError()
            barrier._check_lock(self._record.fd)
            return () if self._record.fd is None else (self._record.fd,)


class BackupSnapshotPermit:
    """Valid only within the issuing snapshot context and on its original PID."""

    __slots__ = ("_barrier",)

    def __init__(self, barrier: BackupWriteBarrier) -> None:
        self._barrier = barrier

    def assert_active(self) -> None:
        barrier = self._barrier
        barrier._check_pid()
        with barrier._condition:
            barrier._check_open()
            if barrier._permit is not self or barrier._exclusive_fd is None:
                raise BackupCoordinationError()
            barrier._check_lock(barrier._exclusive_fd)


    def assert_for_lock(self, lock_path: Path) -> None:
        """Require this active permit's pinned parent and exact lock filename.

        This is a read-only layout assertion, not another flock acquisition or
        permission to use an arbitrary database path. A requested path rebound
        to a different parent is rejected despite the held original directory FD.
        """
        barrier = self._barrier
        barrier._check_pid()
        with barrier._condition:
            barrier._check_open()
            if barrier._permit is not self or barrier._exclusive_fd is None:
                raise BackupCoordinationError()
            barrier._check_lock(barrier._exclusive_fd)
            if not isinstance(lock_path, Path) or lock_path.name != barrier._name:
                raise BackupCoordinationError()
            parent_fd = None
            try:
                parent_fd = os.open(
                    lock_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                )
                requested_parent = os.fstat(parent_fd)
                held_parent = os.fstat(barrier._parent_fd)
                if (requested_parent.st_dev, requested_parent.st_ino) != (
                    held_parent.st_dev, held_parent.st_ino
                ):
                    raise BackupCoordinationError()
                leaf = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
                if not barrier._regular(leaf) or (leaf.st_dev, leaf.st_ino) != barrier._inode:
                    raise BackupCoordinationError()
                barrier._check_lock(barrier._exclusive_fd)
            except (OSError, TypeError, ValueError):
                raise BackupCoordinationError() from None
            finally:
                _close_fds(parent_fd)


class BackupWriteBarrier:
    def __init__(self, lock_path: Path | None) -> None:
        self._pid = os.getpid()
        self._condition = threading.Condition(threading.RLock())
        self._context: ContextVar[_LeaseRecord | None] = ContextVar(
            "backup_write_lease", default=None
        )
        self._records: set[_LeaseRecord] = set()
        self._closed = False
        self._work_paused = False
        self._agent_paused = False
        self._snapshot_ticket: object | None = None
        self._permit: BackupSnapshotPermit | None = None
        self._exclusive_fd: int | None = None
        self._parent_fd: int | None = None
        self._anchor_fd: int | None = None
        self._name: str | None = None
        self._inode: tuple[int, int] | None = None
        self._finalizer: weakref.finalize | None = None
        if lock_path is not None:
            self._pin(lock_path)

    def _check_pid(self) -> None:
        # Never touch an inherited mutex first: another thread held it at fork.
        if os.getpid() != self._pid:
            raise BackupCoordinationError()

    def _check_open(self) -> None:
        if self._closed:
            raise BackupCoordinationError()

    @staticmethod
    def _regular(info: os.stat_result) -> bool:
        return (
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
        )

    def _pin(self, path: Path) -> None:
        if not isinstance(path, Path) or path.name in ("", ".", ".."):
            raise BackupCoordinationError()
        parent_fd = anchor_fd = None
        try:
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                | os.O_CLOEXEC)
            parent = os.fstat(parent_fd)
            if parent.st_uid not in (0, os.geteuid()) or parent.st_mode & 0o022:
                raise BackupCoordinationError()
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            try:
                anchor_fd = os.open(path.name, flags | os.O_CREAT | os.O_EXCL,
                                    0o600, dir_fd=parent_fd)
            except FileExistsError:
                # Do not even open an existing device/FIFO or fix existing modes.
                if not self._regular(os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)):
                    raise BackupCoordinationError() from None
                anchor_fd = os.open(path.name, flags, dir_fd=parent_fd)
            else:
                # Only this newly created inode may need owner bits removed by umask.
                os.fchmod(anchor_fd, 0o600)
            self._parent_fd, self._anchor_fd, self._name = parent_fd, anchor_fd, path.name
            current = os.fstat(anchor_fd)
            self._inode = (current.st_dev, current.st_ino)
            self._check_lock(anchor_fd)
            self._finalizer = weakref.finalize(
                self, _finalize_pinned_fds, self._pid, anchor_fd, parent_fd
            )
            self._finalizer.atexit = False
        except BaseException as exc:
            if self._finalizer is not None:
                self._finalizer.detach()
            _close_fds(anchor_fd, parent_fd)
            self._parent_fd = self._anchor_fd = None
            if isinstance(exc, Exception):
                raise BackupCoordinationError() from None
            raise

    def _check_lock(self, fd: int | None = None) -> None:
        if self._anchor_fd is None:
            return
        try:
            parent = os.fstat(self._parent_fd)
            if parent.st_uid not in (0, os.geteuid()) or parent.st_mode & 0o022:
                raise BackupCoordinationError()
            for info in (
                os.fstat(self._anchor_fd),
                os.stat(self._name, dir_fd=self._parent_fd, follow_symlinks=False),
                os.fstat(self._anchor_fd if fd is None else fd),
            ):
                if not self._regular(info) or (info.st_dev, info.st_ino) != self._inode:
                    raise BackupCoordinationError()
        except (OSError, TypeError, ValueError):
            raise BackupCoordinationError() from None

    def _open_lock(self) -> int:
        self._check_lock()
        fd = None
        try:
            fd = os.open(self._name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         dir_fd=self._parent_fd)
            self._check_lock(fd)
            return fd
        except BaseException as exc:
            _close_fds(fd)
            if isinstance(exc, Exception):
                raise BackupCoordinationError() from None
            raise

    def _record_active(self, record: _LeaseRecord | None) -> bool:
        return record is not None and record in self._records and bool(record.holders)

    @contextmanager
    def operation(self, *, kind: OperationKind = "work") -> Iterator[BackupWriteLease]:
        self._check_pid()
        if type(kind) is not str or kind not in ("work", "agent"):
            raise BackupCoordinationError()
        with self._condition:
            self._check_open()
            self._check_lock()
            record = self._context.get()
            if not self._record_active(record) or record.kind != kind:
                if (kind == "work" and self._work_paused) or (
                    kind == "agent" and self._agent_paused
                ):
                    raise BackupBusyError()
                fd = None
                if self._anchor_fd is not None:
                    fd = self._open_lock()
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        self._check_lock(fd)
                    except BaseException as exc:
                        _close_fds(fd)
                        if isinstance(exc, BlockingIOError):
                            raise BackupBusyError() from None
                        if isinstance(exc, Exception):
                            raise BackupCoordinationError() from None
                        raise
                record = _LeaseRecord(kind, fd)
                self._records.add(record)
            else:
                self._check_lock(record.fd)
            lease = BackupWriteLease(self, record)
            record.holders.add(lease)
            token = self._context.set(record)
        try:
            yield lease
        finally:
            self._check_pid()
            try:
                self._context.reset(token)
            finally:
                self._release(lease)

    def _release(self, lease: BackupWriteLease) -> None:
        self._check_pid()
        with self._condition:
            record = lease._record
            if not self._record_active(record) or lease not in record.holders:
                raise BackupCoordinationError()
            record.holders.remove(lease)
            if not record.holders:
                self._records.remove(record)
                try:
                    _close_fds(record.fd)
                finally:
                    self._condition.notify_all()

    @staticmethod
    def _check_deadline(deadline: float | None) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise BackupBusyError() from None

    def _wait_for_kind(self, kind: OperationKind, deadline: float | None) -> None:
        # The condition is held by the caller; wait releases it for real work.
        while any(record.kind == kind for record in self._records):
            if deadline is None:
                raise BackupBusyError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BackupBusyError()
            self._condition.wait(min(remaining, threading.TIMEOUT_MAX))

    def _acquire_exclusive(self, deadline: float | None) -> int:
        self._check_deadline(deadline)
        fd = self._open_lock()
        try:
            while True:
                self._check_lock(fd)
                self._check_deadline(deadline)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    if deadline is None:
                        raise BackupBusyError() from None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BackupBusyError() from None
                    time.sleep(min(remaining, _LOCK_POLL_SECONDS))
                else:
                    self._check_lock(fd)
                    self._check_deadline(deadline)
                    return fd
        except BaseException as exc:
            _close_fds(fd)
            if isinstance(exc, BackupCoordinationError):
                raise
            if isinstance(exc, Exception):
                raise BackupCoordinationError() from None
            raise

    @contextmanager
    def snapshot(self, *, timeout: float = 15.0) -> Iterator[BackupSnapshotPermit]:
        """Admit before one positive deadline, or try once with timeout=0.

        The deadline is checked between local syscalls; it cannot interrupt a
        blocked kernel call. Once yielded, the permit lasts until context exit.
        """
        self._check_pid()
        if type(timeout) not in (int, float):
            raise BackupCoordinationError()
        try:
            seconds = float(timeout)
        except (OverflowError, ValueError):
            raise BackupCoordinationError() from None
        if not math.isfinite(seconds) or seconds < 0:
            raise BackupCoordinationError()
        deadline = None if seconds == 0 else time.monotonic() + seconds
        ticket = None
        fd = None
        try:
            with self._condition:
                self._check_open()
                if self._anchor_fd is None:
                    raise BackupCoordinationError()
                if self._snapshot_ticket is not None or self._record_active(self._context.get()):
                    raise BackupBusyError()
                self._check_lock()
                self._snapshot_ticket = ticket = object()
                self._work_paused = True
                self._condition.notify_all()
            with self._condition:
                self._wait_for_kind("work", deadline)
                self._check_deadline(deadline)
                self._agent_paused = True
                self._condition.notify_all()
                self._wait_for_kind("agent", deadline)
                self._check_deadline(deadline)
            fd = self._acquire_exclusive(deadline)
            with self._condition:
                self._check_deadline(deadline)
                self._exclusive_fd = fd
                self._permit = permit = BackupSnapshotPermit(self)
            yield permit
        finally:
            self._check_pid()
            if ticket is not None:
                with self._condition:
                    self._permit = None
                    self._exclusive_fd = None
                    try:
                        _close_fds(fd)
                    finally:
                        self._snapshot_ticket = None
                        self._work_paused = self._agent_paused = False
                        self._condition.notify_all()

    async def run_sync(
        self, fn: Callable[..., _T], *args: object, kind: OperationKind = "work", **kwargs: object
    ) -> _T:
        self._check_pid()

        def invoke() -> _T:
            with self.operation(kind=kind):
                return fn(*args, **kwargs)

        return await asyncio.to_thread(invoke)

    def close(self) -> None:
        self._check_pid()
        with self._condition:
            if self._closed:
                return
            if self._records or self._snapshot_ticket is not None:
                raise BackupBusyError()
            self._closed = True
            if self._finalizer is not None:
                # Detach before any close: even partial failure must not let a
                # later GC callback close unrelated, reused descriptor numbers.
                self._finalizer.detach()
            _close_fds(self._anchor_fd, self._parent_fd)
