"""One coordinated SQLite online snapshot, not a complete backup or restore.

Only an already-active permit for the source directory's backup lock is accepted.
The source is opened mode=ro (never immutable), and a read transaction fixes the
committed WAL view before Connection.backup copies pages to a newly created file.
Nothing checkpoints, vacuums, or writes SQL to the source. SQLite may maintain
its own WAL shared-memory bookkeeping; this is not a filesystem no-write promise.

The caller supplies an existing, private 0700 staging directory. A random private
child contains the writable target; after independent SQLite/FK checks, every
writable handle is closed and the target becomes a read-only anonymous stream.
The completed target's independent, immutable read-only connection is borrowed
alongside the stream. Use it exclusively on its creation thread, never close it
yourself, and never retain it beyond this context. No progress handler remains
installed when it is borrowed. The caller may release the permit after yield
without invalidating either private read-only handle.

The 30s deadline is checked between operations, per backup batch, in SQL progress
callbacks, and while hashing. It cannot interrupt blocked kernel I/O. Cancellation
that actually reaches this synchronous code is cleaned up and propagated; merely
cancelling an asyncio task waiting for a thread does not stop the thread. This
does not cover uncooperative writers, hostile same-UID code, or remote filesystems.
The output is capped at 1GiB; schema metadata is capped separately at 16MiB/65536
rows/1MiB per field. A schema fingerprint is a versioned hash, not schema approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal

from open_node.domain.backup import MAX_FILE_BYTES
from open_node.services.backup_coordination import BackupCoordinationError, BackupSnapshotPermit

MAX_SQLITE_SNAPSHOT_BYTES = MAX_FILE_BYTES
SQLITE_SNAPSHOT_TIMEOUT_SECONDS = 30.0
_LOCK_NAME = ".open-node-backup.lock"
_PAGE_BATCH = 256
_BLOCK_BYTES = 65536
_MAX_SCHEMA_ROWS = 65536
_MAX_SCHEMA_BYTES = 16 * 1024 * 1024
_MAX_SCHEMA_FIELD_BYTES = 1024 * 1024
_SIDECARS = ("-wal", "-shm", "-journal")
_TARGET = "snapshot.sqlite3"


class BackupSQLiteError(RuntimeError):
    code = "backup_sqlite_unavailable"

    def __init__(self) -> None:
        super().__init__("SQLite backup snapshot is unavailable.")


@dataclass(frozen=True, slots=True)
class SQLiteBackupSnapshot:
    stream: BinaryIO = field(repr=False)
    connection: sqlite3.Connection = field(repr=False)
    size: int
    sha256: str
    schema_fingerprint: str
    sqlite_integrity_check: Literal["passed"] = "passed"
    foreign_key_check: Literal["passed"] = "passed"
    standalone: Literal[True] = True
    restoration_ready: Literal[False] = False


def _regular(info: os.stat_result, *, private: bool = False) -> bool:
    mode = stat.S_IMODE(info.st_mode)
    return (
        stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
        and not mode & 0o7022 and (not private or mode == 0o600)
    )


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _directory(path: Path, *, private: bool = False) -> int:
    # Traverse without following any component symlink. Only the final directory
    # needs the caller-owned/private policy; e.g. /tmp may be sticky and shared.
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise BackupSQLiteError()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid not in (0, os.geteuid()) or mode & 0o022:
            raise BackupSQLiteError()
        if private and (info.st_uid != os.geteuid() or mode != 0o700):
            raise BackupSQLiteError()
        return fd
    except BaseException:
        os.close(fd)
        raise


class _Budget:
    def __init__(self, permit: BackupSnapshotPermit, source: Path) -> None:
        self.permit = permit
        self.source = source
        self.deadline = time.monotonic() + SQLITE_SNAPSHOT_TIMEOUT_SECONDS
        self.interrupted: BaseException | None = None

    def check(self) -> None:
        if self.interrupted is not None:
            raise self.interrupted
        self.permit.assert_active()
        if time.monotonic() >= self.deadline:
            raise BackupSQLiteError()

    def bind(self) -> None:
        self.check()
        self.permit.assert_for_lock(self.source.parent / _LOCK_NAME)

    def progress(self) -> int:
        # Python sqlite3 does not propagate progress-handler exceptions. Preserve
        # the original cancellation/coordination error and abort the VM explicitly.
        try:
            self.check()
        except BaseException as error:
            self.interrupted = error
            return 1
        return 0


def _execute(connection: sqlite3.Connection, sql: str, budget: _Budget) -> list[tuple]:
    budget.check()
    try:
        with _cursor(connection, sql) as cursor:
            # Only fixed scalar/check queries use this helper, never a data dump.
            rows = cursor.fetchmany(2)
    except Exception:
        budget.check()
        raise
    budget.check()
    return rows


@contextmanager
def _cursor(connection: sqlite3.Connection, sql: str) -> Iterator[sqlite3.Cursor]:
    cursor = connection.execute(sql)
    try:
        yield cursor
    finally:
        cursor.close()


def _connect(
    path: Path, mode: str, budget: _Budget, *, immutable: bool = False,
) -> sqlite3.Connection:
    budget.check()
    if immutable and mode != "ro":
        raise BackupSQLiteError()
    uri = path.absolute().as_uri() + "?mode=" + mode + "&cache=private"
    if immutable:
        uri += "&immutable=1"
    connection = sqlite3.connect(
        uri, uri=True, timeout=0.0, isolation_level=None,
    )
    try:
        connection.set_progress_handler(budget.progress, 1000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, _MAX_SCHEMA_FIELD_BYTES)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        _execute(connection, "PRAGMA trusted_schema=OFF", budget)
        _execute(connection, "PRAGMA mmap_size=0", budget)
        _execute(connection, "PRAGMA cache_size=-2048", budget)
        if mode == "ro":
            _execute(connection, "PRAGMA query_only=ON", budget)
        return connection
    except BaseException:
        connection.close()
        raise


def _check_source(source: Path, parent_fd: int, source_fd: int, budget: _Budget) -> None:
    budget.bind()
    held = os.fstat(source_fd)
    named = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not _regular(held) or not _regular(named) or _inode(held) != _inode(named)
        or not 100 <= held.st_size <= MAX_SQLITE_SNAPSHOT_BYTES
    ):
        raise BackupSQLiteError()
    # SQLite opens these neighbours itself. Reject unsafe aliases before opening
    # a connection and on each batch. A same-UID race cannot be made impossible.
    for suffix in _SIDECARS:
        try:
            info = os.stat(source.name + suffix, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not _regular(info) or info.st_size > 2 * MAX_SQLITE_SNAPSHOT_BYTES:
            raise BackupSQLiteError()


def _fingerprint(connection: sqlite3.Connection, budget: _Budget) -> str:
    # Hash only metadata, never interpolate or execute stored SQL. Binary ordering
    # and JSON framing distinguish NULL, empty strings and embedded delimiters.
    columns = ("type", "name", "tbl_name", "sql")
    sizes = [f"coalesce(length(CAST({column} AS BLOB)),0)" for column in columns]
    totals = _execute(
        connection,
        "SELECT count(*),coalesce(sum(" + "+".join(sizes) + "),0),"
        "coalesce(max(max(" + ",".join(sizes) + ")),0) FROM sqlite_schema",
        budget,
    )
    count, total, largest = totals[0]
    if count > _MAX_SCHEMA_ROWS or total > _MAX_SCHEMA_BYTES or largest > _MAX_SCHEMA_FIELD_BYTES:
        raise BackupSQLiteError()
    digest = hashlib.sha256(b"open-node-sqlite-schema-v1\n")
    version = [_execute(connection, "PRAGMA application_id", budget)[0][0],
               _execute(connection, "PRAGMA user_version", budget)[0][0]]
    digest.update(json.dumps(version, separators=(",", ":")).encode() + b"\n")
    with _cursor(
        connection, "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "ORDER BY type COLLATE BINARY,name COLLATE BINARY,tbl_name COLLATE BINARY",
    ) as cursor:
        for row in cursor:
            budget.check()
            digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
            digest.update(b"\n")
    budget.check()
    return digest.hexdigest()


class _Resources:
    def __init__(self) -> None:
        self.fds: list[int] = []
        self.connections: list[sqlite3.Connection] = []
        self.stream: BinaryIO | None = None
        self.stage_parent: int | None = None
        self.stage_fd: int | None = None
        self.stage_name: str | None = None
        self.stage_inode: tuple[int, int] | None = None

    def close_connections(self) -> None:
        pending, self.connections = self.connections, []
        failure = None
        for connection in reversed(pending):
            try:
                try:
                    connection.set_progress_handler(None, 0)
                finally:
                    connection.close()
            except BaseException as error:
                failure = failure or error
        if failure is not None:
            raise failure

    def remove_stage(self) -> None:
        if self.stage_name is None:
            return
        named = os.stat(self.stage_name, dir_fd=self.stage_parent, follow_symlinks=False)
        if _inode(named) != self.stage_inode or not stat.S_ISDIR(named.st_mode):
            raise BackupSQLiteError()
        if self.stage_fd is not None:
            if _inode(os.fstat(self.stage_fd)) != self.stage_inode:
                raise BackupSQLiteError()
            allowed = {_TARGET, *(_TARGET + suffix for suffix in _SIDECARS)}
            for name in os.listdir(self.stage_fd):
                info = os.stat(name, dir_fd=self.stage_fd, follow_symlinks=False)
                if name not in allowed or not _regular(info, private=True):
                    raise BackupSQLiteError()
                os.unlink(name, dir_fd=self.stage_fd)
        os.rmdir(self.stage_name, dir_fd=self.stage_parent)
        self.stage_name = None
        fd, self.stage_fd = self.stage_fd, None
        if fd is not None:
            os.close(fd)

    def close(self) -> None:
        failure = None
        actions = [self.close_connections]
        if self.stream is not None:
            actions.append(self.stream.close)
        actions.append(self.remove_stage)
        for action in actions:
            try:
                action()
            except BaseException as error:
                failure = failure or error
        if self.stage_fd is not None:
            self.fds.append(self.stage_fd)
            self.stage_fd = None
        pending, self.fds = self.fds, []
        for fd in reversed(pending):
            try:
                os.close(fd)
            except OSError as error:
                failure = failure or error
        if failure is not None:
            raise failure


def _check_stage(
    staging: Path, resources: _Resources, target_inode: tuple[int, int], budget: _Budget,
) -> None:
    budget.check()
    parent = os.stat(staging, follow_symlinks=False)
    child = os.stat(resources.stage_name, dir_fd=resources.stage_parent, follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent.st_mode) or not stat.S_ISDIR(child.st_mode)
        or parent.st_uid != os.geteuid() or child.st_uid != os.geteuid()
        or stat.S_IMODE(parent.st_mode) != 0o700 or stat.S_IMODE(child.st_mode) != 0o700
        or _inode(parent) != _inode(os.fstat(resources.stage_parent))
        or _inode(child) != resources.stage_inode
        or _inode(os.fstat(resources.stage_fd)) != resources.stage_inode
    ):
        raise BackupSQLiteError()
    target = os.stat(_TARGET, dir_fd=resources.stage_fd, follow_symlinks=False)
    if (
        not _regular(target, private=True) or _inode(target) != target_inode
        or target.st_size > MAX_SQLITE_SNAPSHOT_BYTES
    ):
        raise BackupSQLiteError()


def _prepare(
    source: Path, staging: Path, permit: BackupSnapshotPermit, resources: _Resources,
) -> SQLiteBackupSnapshot:
    budget = _Budget(permit, source)
    budget.bind()
    parent_fd = _directory(source.parent)
    resources.fds.append(parent_fd)
    source_fd = os.open(
        source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd,
    )
    resources.fds.append(source_fd)
    _check_source(source, parent_fd, source_fd, budget)
    if os.pread(source_fd, 16, 0) != b"SQLite format 3\x00":
        raise BackupSQLiteError()
    stage_parent = _directory(staging, private=True)
    resources.fds.append(stage_parent)
    resources.stage_parent = stage_parent
    budget.check()
    name = "sqlite-snapshot-" + secrets.token_hex(16)
    os.mkdir(name, mode=0o700, dir_fd=stage_parent)
    resources.stage_name = name
    resources.stage_inode = _inode(os.stat(name, dir_fd=stage_parent, follow_symlinks=False))
    resources.stage_fd = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=stage_parent,
    )
    stage_path = staging / name
    target_path = stage_path / _TARGET
    writable_fd = os.open(
        _TARGET, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600, dir_fd=resources.stage_fd,
    )
    # Close the creation handle immediately. SQLite's own target connection is
    # the only writer, and its inode is checked again before publication.
    try:
        # Only this newly O_EXCL-created file, never an existing target.
        os.fchmod(writable_fd, 0o600)
        target_inode = _inode(os.fstat(writable_fd))
    finally:
        os.close(writable_fd)
    source_connection = _connect(source, "ro", budget)
    resources.connections.append(source_connection)
    _check_source(source, parent_fd, source_fd, budget)
    _execute(source_connection, "BEGIN", budget)
    _execute(source_connection, "SELECT count(*) FROM sqlite_schema", budget)
    page_size = _execute(source_connection, "PRAGMA page_size", budget)[0][0]
    page_count = _execute(source_connection, "PRAGMA page_count", budget)[0][0]
    if (
        page_size not in {512, 1024, 2048, 4096, 8192, 16384, 32768, 65536}
        or page_count < 1 or page_count * page_size > MAX_SQLITE_SNAPSHOT_BYTES
    ):
        raise BackupSQLiteError()
    _check_stage(staging, resources, target_inode, budget)
    target_connection = _connect(target_path, "rw", budget)
    resources.connections.append(target_connection)
    _check_stage(staging, resources, target_inode, budget)
    _execute(target_connection, "PRAGMA page_size=" + str(page_size), budget)
    _execute(target_connection, "PRAGMA journal_mode=DELETE", budget)
    _execute(target_connection, "PRAGMA max_page_count=" + str(
        MAX_SQLITE_SNAPSHOT_BYTES // page_size
    ), budget)

    def progress(status: int, remaining: int, total: int) -> None:
        _check_source(source, parent_fd, source_fd, budget)
        if (
            status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE,
                           sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            or remaining < 0 or total < 0 or total > page_count
        ):
            raise BackupSQLiteError()
        _check_stage(staging, resources, target_inode, budget)

    budget.check()
    source_connection.backup(target_connection, pages=_PAGE_BATCH, progress=progress, sleep=0.005)
    budget.check()
    if _execute(target_connection, "PRAGMA journal_mode=DELETE", budget) != [("delete",)]:
        raise BackupSQLiteError()
    resources.close_connections()
    _check_source(source, parent_fd, source_fd, budget)
    # This independent read-only connection proves the closed target is usable
    # without the source handle or any target WAL. The DELETE header is mandatory.
    if set(os.listdir(resources.stage_fd)) != {_TARGET}:
        raise BackupSQLiteError()
    _check_stage(staging, resources, target_inode, budget)
    # Only this fully completed standalone target is immutable, never the live
    # source. Opening before unlink retains a usable SQLite VFS file handle.
    checked = _connect(target_path, "ro", budget, immutable=True)
    resources.connections.append(checked)
    if _execute(checked, "PRAGMA integrity_check(1)", budget) != [("ok",)]:
        raise BackupSQLiteError()
    if _execute(checked, "PRAGMA foreign_key_check", budget) != []:
        raise BackupSQLiteError()
    try:
        fingerprint = _fingerprint(checked, budget)
    except Exception:
        budget.check()
        raise
    checked.set_progress_handler(None, 0)
    if checked.in_transaction:
        raise BackupSQLiteError()
    budget.bind()
    _check_stage(staging, resources, target_inode, budget)
    readonly_fd = os.open(
        _TARGET, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=resources.stage_fd,
    )
    try:
        resources.stream = os.fdopen(readonly_fd, "rb", buffering=0)
    except BaseException:
        os.close(readonly_fd)
        raise
    info = os.fstat(readonly_fd)
    if (
        not _regular(info, private=True) or _inode(info) != target_inode
        or not 100 <= info.st_size <= MAX_SQLITE_SNAPSHOT_BYTES
    ):
        raise BackupSQLiteError()
    digest = hashlib.sha256()
    read_size = 0
    while True:
        budget.check()
        block = resources.stream.read(_BLOCK_BYTES)
        if not block:
            break
        read_size += len(block)
        if read_size > info.st_size:
            raise BackupSQLiteError()
        digest.update(block)
    if read_size != info.st_size or resources.stream.seek(0) != 0:
        raise BackupSQLiteError()
    resources.remove_stage()
    budget.bind()
    if os.fstat(readonly_fd).st_nlink != 0:
        raise BackupSQLiteError()
    return SQLiteBackupSnapshot(
        resources.stream, checked, read_size, digest.hexdigest(), fingerprint,
    )


@contextmanager
def sqlite_backup_snapshot(
    source_database: Path, *, permit: BackupSnapshotPermit, staging_directory: Path,
) -> Iterator[SQLiteBackupSnapshot]:
    """Borrow checked private read handles, both closed when this context exits.

    ``connection`` must stay on this thread, must not be closed by the borrower,
    and must not escape the context. The preparation permit may be released
    before reading the result. No writable SQLite handle survives publication.
    """
    resources = _Resources()
    try:
        if type(permit) is not BackupSnapshotPermit:
            raise BackupCoordinationError()
        try:
            permit.assert_active()
        except (AttributeError, TypeError):
            raise BackupCoordinationError() from None
        if (
            not isinstance(source_database, Path) or not isinstance(staging_directory, Path)
            or str(source_database).startswith("file:") or source_database.name == ":memory:"
            or source_database.name in ("", ".", "..")
        ):
            raise BackupSQLiteError()
        result = _prepare(
            source_database.absolute(), staging_directory.absolute(), permit, resources,
        )
    except BackupCoordinationError:
        raise
    except Exception:
        raise BackupSQLiteError() from None
    finally:
        if sys.exc_info()[0] is not None:
            try:
                resources.close()
            except BaseException:
                pass  # Preserve the preparation failure/cancellation, not raw cleanup details.
    try:
        yield result
    finally:
        active_error = sys.exc_info()[0]
        try:
            resources.close()
        except BaseException:
            if active_error is None:
                raise BackupSQLiteError() from None
