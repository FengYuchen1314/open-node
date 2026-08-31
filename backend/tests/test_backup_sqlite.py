"""Real SQLite/WAL and flock checks for the private online-snapshot substep.

Connection subclasses below observe or interrupt the actual sqlite3 backup API;
they never fake a successful snapshot. Fault injection is explicitly local I/O
or callback failure, not evidence of restore readiness or application coverage.
"""

import asyncio
import errno
import fcntl
import hashlib
import io
import json
import os
import select
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from open_node.services import backup_sqlite as snapshot_module
from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupSnapshotPermit,
    BackupWriteBarrier,
)
from open_node.services.backup_sqlite import BackupSQLiteError, sqlite_backup_snapshot

MESSAGE = "SQLite backup snapshot is unavailable."


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create_database(path, *, wal=False, pages=0):
    connection = sqlite3.connect(path)
    if wal:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        "CREATE TABLE parent(id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));"
        "CREATE INDEX parent_value ON parent(value);"
        "INSERT INTO parent VALUES (1,'committed'); INSERT INTO child VALUES (1,1);"
        "PRAGMA user_version=42; PRAGMA application_id=12345;"
    )
    if pages:
        connection.execute("CREATE TABLE padding(value BLOB)")
        connection.executemany("INSERT INTO padding VALUES (zeroblob(4096))", [()] * pages)
    connection.commit()
    return connection


@pytest.fixture
def layout(tmp_path):
    source_dir = tmp_path / "database"
    staging = tmp_path / "staging"
    source_dir.mkdir(mode=0o700)
    staging.mkdir(mode=0o700)
    database = source_dir / "control plane 数据.sqlite3"
    connection = create_database(database)
    connection.close()
    barrier = BackupWriteBarrier(source_dir / ".open-node-backup.lock")
    try:
        yield database, staging, barrier
    finally:
        barrier.close()


def enter_snapshot(database, staging, permit):
    with sqlite_backup_snapshot(database, permit=permit, staging_directory=staging) as result:
        return result.size


def assert_safe_error(action):
    with pytest.raises(BackupSQLiteError) as caught:
        action()
    assert type(caught.value) is BackupSQLiteError
    assert str(caught.value) == MESSAGE
    assert caught.value.code == "backup_sqlite_unavailable"
    assert caught.value.__cause__ is None
    return caught.value


def open_fd_count():
    return len(list(Path("/proc/self/fd").iterdir()))


@contextmanager
def track_connections(monkeypatch, *, on_progress=None, on_execute=None, on_close=None):
    real_connect = sqlite3.connect
    opened = []
    observations = []

    class ObservedConnection(sqlite3.Connection):
        def set_progress_handler(self, handler, steps):
            self.progress_handlers.append((handler, steps))
            return super().set_progress_handler(handler, steps)

        def execute(self, sql, *args, **kwargs):
            if on_execute is not None:
                on_execute(self, sql)
            self.statements.append(sql)
            return super().execute(sql, *args, **kwargs)

        def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.25):
            assert self is not target
            observations.append({"source": self, "target": target, "pages": pages,
                                 "sleep": sleep, "in_transaction": self.in_transaction})

            def real_progress(status, remaining, total):
                observations.append((status, remaining, total))
                if on_progress is not None:
                    on_progress(self, target, status, remaining, total)
                if progress is not None:
                    progress(status, remaining, total)

            return super().backup(
                target, pages=pages, progress=real_progress, name=name, sleep=sleep,
            )

        def close(self):
            try:
                return super().close()
            finally:
                self.closed_by_module = True
                if on_close is not None:
                    on_close(self)

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=ObservedConnection)
        connection.statements = []
        connection.progress_handlers = []
        connection.closed_by_module = False
        connection.uri = args[0]
        opened.append(connection)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(snapshot_module.sqlite3, "connect", connect)
        yield opened, observations


def copy_and_read(result, path):
    assert result.stream.tell() == 0
    with path.open("xb") as destination:
        while block := result.stream.read(65536):
            destination.write(block)
    result.stream.seek(0)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return connection.execute("SELECT id,value FROM parent ORDER BY id").fetchall()
    finally:
        connection.close()


def test_snapshot_is_checked_anonymous_readonly_and_only_completed_reader_is_borrowed(
    layout, monkeypatch,
):
    database, staging, barrier = layout
    original_sha = digest(database)
    before_fds = open_fd_count()
    with track_connections(monkeypatch) as (connections, observations):
        with barrier.snapshot() as permit:
            with sqlite_backup_snapshot(
                database, permit=permit, staging_directory=staging,
            ) as result:
                assert result.size == database.stat().st_size
                assert len(result.sha256) == len(result.schema_fingerprint) == 64
                assert result.sqlite_integrity_check == result.foreign_key_check == "passed"
                assert result.standalone is True and result.restoration_ready is False
                assert isinstance(result.stream, io.FileIO)
                assert result.stream.readable() and result.stream.seekable()
                assert not result.stream.writable() and result.stream.tell() == 0
                fd = result.stream.fileno()
                info = os.fstat(fd)
                assert info.st_nlink == 0 and info.st_uid == os.geteuid()
                assert stat.S_IMODE(info.st_mode) == 0o600
                assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
                assert info.st_size == result.size
                assert hashlib.file_digest(result.stream, "sha256").hexdigest() == result.sha256
                result.stream.seek(0)
                with pytest.raises(OSError):
                    os.write(fd, b"should-not-write")
                with pytest.raises(io.UnsupportedOperation):
                    result.stream.write(b"should-not-write")
                assert list(staging.iterdir()) == []
                assert len(connections) == 3
                assert all(item.closed_by_module for item in connections[:2])
                assert result.connection is connections[2] and not connections[2].closed_by_module
                assert "mode=ro" in connections[0].uri and "immutable" not in connections[0].uri
                assert "mode=rw" in connections[1].uri and "immutable" not in connections[1].uri
                assert "mode=ro" in connections[2].uri and "immutable=1" in connections[2].uri
                assert connections[2].progress_handlers[-1] == (None, 0)
                assert not result.connection.in_transaction
                assert observations[0]["in_transaction"] is True
                assert observations[0]["pages"] == 256
                assert not any("VACUUM" in sql for item in connections for sql in item.statements)
                assert "stream=" not in repr(result) and str(database) not in repr(result)
                assert "connection=" not in repr(result)
                with pytest.raises(FrozenInstanceError):
                    result.size = 0
        assert all(item.closed_by_module for item in connections)
    assert result.stream.closed
    with pytest.raises(sqlite3.ProgrammingError):
        result.connection.execute("SELECT 1")
    assert open_fd_count() == before_fds
    assert digest(database) == original_sha


def test_real_wal_three_independent_connections_include_commits_exclude_uncommitted(
    tmp_path, monkeypatch,
):
    folder = tmp_path / "live"
    folder.mkdir(mode=0o700)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    database = folder / "wal.sqlite3"
    committed = create_database(database, wal=True, pages=600)
    uncommitted = sqlite3.connect(database)
    uncommitted.execute("INSERT INTO parent VALUES(2,'uncommitted-private')")
    wal = Path(str(database) + "-wal")
    assert wal.is_file() and wal.stat().st_size > 0
    source_sha, wal_sha = digest(database), digest(wal)
    barrier = BackupWriteBarrier(folder / ".open-node-backup.lock")
    try:
        with track_connections(monkeypatch) as (connections, observations):
            with barrier.snapshot() as permit:
                with sqlite_backup_snapshot(
                    database, permit=permit, staging_directory=staging,
                ) as result:
                    assert len(connections) == 3
                    assert result.connection is connections[2]
                    assert result.connection.execute("SELECT id,value FROM parent").fetchall() == [
                        (1, "committed")
                    ]
                    assert len([item for item in observations if isinstance(item, tuple)]) >= 3
                    assert copy_and_read(result, tmp_path / "standalone.sqlite3") == [
                        (1, "committed")
                    ]
                    assert digest(database) == source_sha and digest(wal) == wal_sha
                    assert all(connection.closed_by_module for connection in connections
                               if connection is not result.connection)
                    assert not result.connection.closed_by_module
            assert all(connection.closed_by_module for connection in connections)
        assert list(staging.iterdir()) == []
        assert uncommitted.execute("SELECT value FROM parent WHERE id=2").fetchone() is not None
    finally:
        uncommitted.rollback()
        uncommitted.close()
        committed.close()
        barrier.close()


def test_commit_between_real_backup_steps_cannot_change_the_fixed_read_transaction(
    tmp_path, monkeypatch,
):
    database = tmp_path / "live.sqlite3"
    writer = create_database(database, wal=True, pages=600)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    barrier = BackupWriteBarrier(tmp_path / ".open-node-backup.lock")
    later_commit = False

    def commit_after_first_page_batch(source, target, status, remaining, total):
        nonlocal later_commit
        if not later_commit:
            assert remaining > 0
            writer.execute("INSERT INTO parent VALUES (2,'committed-later')")
            writer.commit()
            later_commit = True

    try:
        with track_connections(monkeypatch, on_progress=commit_after_first_page_batch):
            with barrier.snapshot() as permit:
                with sqlite_backup_snapshot(
                    database, permit=permit, staging_directory=staging,
                ) as result:
                    assert later_commit
                    assert copy_and_read(result, tmp_path / "fixed-view.sqlite3") == [
                        (1, "committed")
                    ]
        assert writer.execute("SELECT count(*) FROM parent").fetchone() == (2,)
    finally:
        writer.close()
        barrier.close()


def test_result_remains_private_stable_after_permit_release_and_new_writer(layout, tmp_path):
    database, staging, barrier = layout
    scope = barrier.snapshot()
    permit = scope.__enter__()
    released = False
    try:
        with sqlite_backup_snapshot(database, permit=permit, staging_directory=staging) as result:
            scope.__exit__(None, None, None)
            released = True
            with pytest.raises(BackupCoordinationError):
                permit.assert_active()
            with barrier.operation():
                connection = sqlite3.connect(database)
                connection.execute("INSERT INTO parent VALUES(2,'new-live-value')")
                connection.commit()
                connection.close()
            assert copy_and_read(result, tmp_path / "older.sqlite3") == [(1, "committed")]
            assert result.connection.execute(
                "SELECT id,value FROM parent ORDER BY id"
            ).fetchall() == [(1, "committed")]
            assert result.connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not result.connection.in_transaction
            assert hashlib.file_digest(result.stream, "sha256").hexdigest() == result.sha256
            result.stream.seek(0)
            assert list(staging.iterdir()) == []
    finally:
        if not released:
            scope.__exit__(None, None, None)
    assert result.stream.closed
    with pytest.raises(sqlite3.ProgrammingError):
        result.connection.execute("SELECT 1")


def test_borrowed_connection_has_native_readonly_bounds_and_introspection_after_unlink(layout):
    database, staging, barrier = layout
    original_sha = digest(database)
    original = database.stat()
    before_files = set(database.parent.iterdir())
    before_fds = open_fd_count()
    forbidden = staging / "must-not-attach.sqlite3"
    with barrier.snapshot() as permit:
        with sqlite_backup_snapshot(database, permit=permit, staging_directory=staging) as result:
            connection = result.connection
            assert connection.execute("PRAGMA query_only").fetchone() == (1,)
            assert connection.execute("PRAGMA trusted_schema").fetchone() == (0,)
            assert connection.execute("PRAGMA mmap_size").fetchone() == (0,)
            assert connection.execute("PRAGMA cache_size").fetchone() == (-2048,)
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            assert connection.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED) == 0
            assert connection.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH) == 1024 * 1024
            databases = connection.execute("PRAGMA database_list").fetchall()
            assert databases[0][:2] == (0, "main")
            # Integrity/FK checks can initialize SQLite's empty built-in temp
            # schema. It is not an attached database or a temporary data file.
            assert databases[1:] in ([], [(1, "temp", "")])
            assert connection.execute("SELECT * FROM temp.sqlite_schema").fetchall() == []
            target = Path(databases[0][2])
            assert not target.exists() and not target.parent.exists()
            assert connection.execute("PRAGMA main.table_xinfo(parent)").fetchall() == [
                (0, "id", "INTEGER", 0, None, 1, 0),
                (1, "value", "TEXT", 1, None, 0, 0),
            ]
            assert connection.execute("PRAGMA main.foreign_key_list(child)").fetchall() == [
                (0, 0, "parent", "parent_id", "id", "NO ACTION", "NO ACTION", "NONE")
            ]
            assert connection.execute("PRAGMA main.foreign_key_check").fetchall() == []
            assert connection.execute(
                "SELECT name FROM main.sqlite_schema WHERE type='table' ORDER BY name"
            ).fetchall() == [("child",), ("parent",)]
            assert connection.execute(
                "SELECT typeof(value),length(CAST(value AS BLOB)),"
                "CASE WHEN typeof(value)='text' THEN value ELSE NULL END FROM main.parent"
            ).fetchall() == [("text", 9, "committed")]
            with pytest.raises(sqlite3.OperationalError) as insert_error:
                connection.execute("INSERT INTO parent VALUES (2,'must-not-write')")
            assert insert_error.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
            with pytest.raises(sqlite3.OperationalError) as attach_error:
                connection.execute("ATTACH DATABASE ? AS forbidden", (str(forbidden),))
            assert attach_error.value.sqlite_errorcode == sqlite3.SQLITE_ERROR
            assert not forbidden.exists()
            with pytest.raises(sqlite3.DataError):
                connection.execute("SELECT 1 " + " " * (1024 * 1024))
            assert not connection.in_transaction and result.stream.tell() == 0
            info = os.fstat(result.stream.fileno())
            snapshot_fds = []
            for path in Path("/proc/self/fd").iterdir():
                try:
                    fd = int(path.name)
                    opened = os.fstat(fd)
                    if (opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino):
                        snapshot_fds.append(fd)
                        assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
            assert len(snapshot_fds) == 2
            assert info.st_nlink == 0 and list(staging.iterdir()) == []
    assert open_fd_count() == before_fds and set(database.parent.iterdir()) == before_files
    assert digest(database) == original_sha
    current = database.stat()
    assert (current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) == (
        original.st_ino, original.st_size, original.st_mtime_ns, original.st_ctime_ns,
    )


def test_borrowed_connection_rejects_thread_migration_but_original_thread_stays_usable(layout):
    database, staging, barrier = layout
    before = open_fd_count()
    with barrier.snapshot() as permit:
        with sqlite_backup_snapshot(database, permit=permit, staging_directory=staging) as result:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(result.connection.execute, "SELECT count(*) FROM parent")
                with pytest.raises(sqlite3.ProgrammingError):
                    future.result(timeout=5)
            assert result.connection.execute("SELECT count(*) FROM parent").fetchone() == (1,)
            assert not result.connection.in_transaction and result.stream.tell() == 0
    assert open_fd_count() == before and list(staging.iterdir()) == []


def test_borrowed_reader_clears_preparation_progress_and_allows_checker_handler_after_permit(
    layout, monkeypatch,
):
    database, staging, barrier = layout
    actual_preparation_calls = 0
    checker_calls = 0
    real_progress = snapshot_module._Budget.progress

    def observe_preparation(budget):
        nonlocal actual_preparation_calls
        actual_preparation_calls += 1
        return real_progress(budget)

    def checker_progress():
        nonlocal checker_calls
        checker_calls += 1
        return 0

    query = (
        "WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM numbers WHERE n<2000) "
        "SELECT sum(n) FROM numbers"
    )
    monkeypatch.setattr(snapshot_module._Budget, "progress", observe_preparation)
    scope = barrier.snapshot()
    permit = scope.__enter__()
    released = False
    try:
        with track_connections(monkeypatch) as (connections, _):
            with sqlite_backup_snapshot(
                database, permit=permit, staging_directory=staging,
            ) as result:
                assert result.connection is connections[2]
                assert connections[2].progress_handlers[0][1] == 1000
                assert connections[2].progress_handlers[-1] == (None, 0)
                preparation_calls = actual_preparation_calls
                scope.__exit__(None, None, None)
                released = True
                with pytest.raises(BackupCoordinationError):
                    permit.assert_active()
                assert result.connection.execute(query).fetchone() == (2001000,)
                assert actual_preparation_calls == preparation_calls
                result.connection.set_progress_handler(checker_progress, 1000)
                assert result.connection.execute(query).fetchone() == (2001000,)
                assert checker_calls > 0
                result.connection.set_progress_handler(None, 0)
                final_calls = checker_calls
                assert result.connection.execute(query).fetchone() == (2001000,)
                assert checker_calls == final_calls and not result.connection.in_transaction
            assert all(connection.closed_by_module for connection in connections)
    finally:
        if not released:
            scope.__exit__(None, None, None)
    assert result.stream.closed and list(staging.iterdir()) == []


@pytest.mark.parametrize("error_type", [OSError, asyncio.CancelledError])
def test_failure_clearing_completed_reader_handler_never_yields_and_closes_it(
    layout, monkeypatch, error_type,
):
    database, staging, barrier = layout
    before = open_fd_count()
    injected = False
    with track_connections(monkeypatch) as (connections, _):
        real_connect = snapshot_module._connect

        def connect_with_clear_failure(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            if kwargs.get("immutable"):
                original_handler = connection.set_progress_handler

                def fail_once(handler, steps):
                    nonlocal injected
                    if handler is None and not injected:
                        injected = True
                        raise error_type("private cleanup path must not leak")
                    return original_handler(handler, steps)

                connection.set_progress_handler = fail_once
            return connection

        monkeypatch.setattr(snapshot_module, "_connect", connect_with_clear_failure)
        with barrier.snapshot() as permit:
            if error_type is OSError:
                assert_safe_error(lambda: enter_snapshot(database, staging, permit))
            else:
                with pytest.raises(asyncio.CancelledError):
                    enter_snapshot(database, staging, permit)
        assert injected and len(connections) == 3
        assert all(connection.closed_by_module for connection in connections)
    assert open_fd_count() == before and list(staging.iterdir()) == []


def test_context_closes_borrowed_connection_before_raw_stream_without_closing_its_fd(
    layout, monkeypatch,
):
    database, staging, barrier = layout
    before = open_fd_count()
    holder = {}

    def after_close(connection):
        if holder and connection is holder["result"].connection:
            stream = holder["result"].stream
            assert not stream.closed and os.fstat(stream.fileno()).st_nlink == 0
            assert stream.read(16) == b"SQLite format 3\x00"
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
            holder["closed_before_stream"] = True

    with track_connections(monkeypatch, on_close=after_close):
        with barrier.snapshot() as permit:
            with sqlite_backup_snapshot(
                database, permit=permit, staging_directory=staging,
            ) as result:
                holder["result"] = result
    assert holder["closed_before_stream"] and result.stream.closed
    assert open_fd_count() == before and list(staging.iterdir()) == []


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt, asyncio.CancelledError])
def test_consumer_error_survives_borrowed_connection_cleanup_error_and_no_fd_leaks(
    layout, monkeypatch, error_type,
):
    database, staging, barrier = layout
    before = open_fd_count()
    consumer_error = error_type("consumer-owned")
    holder = {}

    def fail_after_real_close(connection):
        if holder and connection is holder["result"].connection:
            assert not holder["result"].stream.closed
            holder["closed"] = True
            raise OSError(errno.EIO, "private cleanup failure must not replace consumer error")

    with track_connections(monkeypatch, on_close=fail_after_real_close) as (connections, _):
        with barrier.snapshot() as permit:
            with pytest.raises(error_type) as caught:
                with sqlite_backup_snapshot(
                    database, permit=permit, staging_directory=staging,
                ) as result:
                    holder["result"] = result
                    raise consumer_error
        assert caught.value is consumer_error and holder["closed"]
        assert all(connection.closed_by_module for connection in connections)
    assert result.stream.closed and open_fd_count() == before and list(staging.iterdir()) == []


@pytest.mark.parametrize("kind", ["none", "duck", "forged", "expired", "uninitialized"])
def test_invalid_permit_rejected_before_source_or_staging_io(layout, monkeypatch, kind):
    database, staging, barrier = layout

    class DuckPermit:
        def assert_active(self):
            return None

        def assert_for_lock(self, path):
            return None

    with barrier.snapshot() as live:
        permits = {"none": None, "duck": DuckPermit(), "forged": BackupSnapshotPermit(barrier),
                   "expired": live, "uninitialized": object.__new__(BackupSnapshotPermit)}
        if kind != "expired":
            with monkeypatch.context() as patch:
                patch.setattr(snapshot_module, "_directory", lambda *args, **kwargs: pytest.fail(
                    "invalid permit must fail before directory I/O"
                ))
                with pytest.raises(BackupCoordinationError):
                    enter_snapshot(database, staging, permits[kind])
    if kind == "expired":
        with pytest.raises(BackupCoordinationError):
            enter_snapshot(database, staging, live)
    assert list(staging.iterdir()) == []


def test_real_permit_for_other_database_directory_is_not_authority(layout, tmp_path):
    database, staging, barrier = layout
    other_folder = tmp_path / "other"
    other_folder.mkdir(mode=0o700)
    other = other_folder / "database.sqlite3"
    connection = create_database(other)
    connection.close()
    with barrier.snapshot() as permit:
        with pytest.raises(BackupCoordinationError):
            enter_snapshot(other, staging, permit)
        assert enter_snapshot(database, staging, permit) > 0


def test_second_database_in_same_layout_intentionally_uses_same_permit(layout):
    database, staging, barrier = layout
    other = database.parent / "second.sqlite3"
    connection = create_database(other)
    connection.close()
    with barrier.snapshot() as permit:
        assert enter_snapshot(other, staging, permit) > 0


@pytest.mark.parametrize("input_kind", ["str", "none", "memory", "file-uri", "missing", "directory",
                                       "symlink", "hardlink", "fifo", "writable", "setuid", "empty",
                                       "junk", "truncated", "oversize"])
def test_unsafe_or_invalid_source_is_rejected_without_publishing(layout, input_kind):
    database, staging, barrier = layout
    source = database
    if input_kind == "str":
        source = str(database)
    elif input_kind == "none":
        source = None
    elif input_kind == "memory":
        source = database.parent / ":memory:"
    elif input_kind == "file-uri":
        source = Path("file:" + str(database))
    elif input_kind == "missing":
        source = database.parent / "absent.sqlite3"
    elif input_kind == "directory":
        source = database.parent / "folder"
        source.mkdir()
    elif input_kind == "symlink":
        source = database.parent / "alias.sqlite3"
        source.symlink_to(database)
    elif input_kind == "hardlink":
        source = database.parent / "hard.sqlite3"
        source.hardlink_to(database)
    elif input_kind == "fifo":
        source = database.parent / "pipe"
        os.mkfifo(source)
    elif input_kind == "writable":
        database.chmod(0o666)
    elif input_kind == "setuid":
        database.chmod(0o4600)
    elif input_kind == "empty":
        database.write_bytes(b"")
    elif input_kind == "junk":
        database.write_bytes(b"secret not a database" * 100)
    elif input_kind == "truncated":
        with database.open("r+b") as stream:
            stream.truncate(110)
    elif input_kind == "oversize":
        with database.open("r+b") as stream:
            stream.truncate(snapshot_module.MAX_SQLITE_SNAPSHOT_BYTES + 1)
    before = open_fd_count()
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(source, staging, permit))
    assert list(staging.iterdir()) == []
    assert open_fd_count() == before


@pytest.mark.parametrize("bad_staging", ["str", "none", "missing", "file", "symlink", "mode755",
                                        "world-writable"])
def test_staging_must_be_existing_private_directory_and_never_overwrites(layout, bad_staging):
    database, staging, barrier = layout
    marker = staging / "existing-must-survive"
    marker.write_bytes(b"caller-owned")
    given = staging
    if bad_staging == "str":
        given = str(staging)
    elif bad_staging == "none":
        given = None
    elif bad_staging == "missing":
        given = staging / "missing"
    elif bad_staging == "file":
        given = marker
    elif bad_staging == "symlink":
        given = staging.parent / "alias"
        given.symlink_to(staging, target_is_directory=True)
    elif bad_staging == "mode755":
        staging.chmod(0o755)
    elif bad_staging == "world-writable":
        staging.chmod(0o777)
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(database, given, permit))
    assert marker.read_bytes() == b"caller-owned"
    assert list(staging.iterdir()) == [marker]


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink"])
def test_unsafe_sqlite_sidecars_fail_before_sqlite_can_follow_them(
    layout, monkeypatch, suffix, kind,
):
    database, staging, barrier = layout
    sidecar = Path(str(database) + suffix)
    if kind == "symlink":
        sidecar.symlink_to(database)
    elif kind == "fifo":
        os.mkfifo(sidecar)
    else:
        secret = database.parent / "secret"
        secret.write_bytes(b"not-for-sqlite")
        sidecar.hardlink_to(secret)
    monkeypatch.setattr(snapshot_module.sqlite3, "connect", lambda *a, **k: pytest.fail(
        "unsafe sidecar must fail before SQLite connect"
    ))
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert list(staging.iterdir()) == []


def test_sqlite_foreign_key_failure_is_fixed_safe_error_not_row_or_table_data(layout):
    database, staging, barrier = layout
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO child VALUES(2,9999)")
    connection.commit()
    assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() != []
    connection.close()
    before = digest(database)
    with barrier.snapshot() as permit:
        error = assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert "child" not in str(error) and "9999" not in str(error)
    assert list(staging.iterdir()) == [] and digest(database) == before


def test_schema_fingerprint_is_independent_of_data_and_changes_for_schema_or_user_version(layout):
    database, staging, barrier = layout

    def fingerprint():
        with barrier.snapshot() as permit:
            with sqlite_backup_snapshot(
                database, permit=permit, staging_directory=staging,
            ) as result:
                return result.schema_fingerprint

    original = fingerprint()
    connection = sqlite3.connect(database)
    expected = hashlib.sha256(b"open-node-sqlite-schema-v1\n")
    expected.update(json.dumps([12345, 42], separators=(",", ":")).encode() + b"\n")
    for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY "
        "type COLLATE BINARY,name COLLATE BINARY,tbl_name COLLATE BINARY"
    ):
        expected.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
    assert original == expected.hexdigest()
    connection.execute("INSERT INTO parent VALUES(2,'a completely different value')")
    connection.commit()
    assert fingerprint() == original
    connection.execute("CREATE VIEW name_only AS SELECT value FROM parent")
    connection.commit()
    changed_schema = fingerprint()
    assert changed_schema != original
    connection.execute("PRAGMA user_version=43")
    assert fingerprint() != changed_schema
    connection.close()


@pytest.mark.parametrize("page_size", [512, 65536])
def test_real_minimum_and_maximum_sqlite_page_sizes(layout, page_size):
    database, staging, barrier = layout
    database.unlink()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA page_size=" + str(page_size))
    connection.execute("CREATE TABLE original(value TEXT)")
    connection.execute("INSERT INTO original VALUES ('page-size-test')")
    connection.commit()
    assert connection.execute("PRAGMA page_size").fetchone() == (page_size,)
    connection.close()
    with barrier.snapshot() as permit:
        assert enter_snapshot(database, staging, permit) == 2 * page_size


def test_actual_index_corruption_is_rejected_by_sqlite_integrity_check(layout):
    database, staging, barrier = layout
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA writable_schema=ON")
    connection.execute("UPDATE sqlite_schema SET rootpage=99999 WHERE name='parent_value'")
    connection.commit()
    connection.close()
    before = digest(database)
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert digest(database) == before and list(staging.iterdir()) == []


@pytest.mark.parametrize("limit", ["database-size", "schema-bytes", "schema-rows", "schema-field"])
def test_lowered_test_limits_exercise_real_size_and_schema_rejection(layout, monkeypatch, limit):
    # These deliberately small limits exercise branches, not a claim that the
    # real 1GiB upper boundary was materialized in this focused suite.
    database, staging, barrier = layout
    if limit == "database-size":
        monkeypatch.setattr(snapshot_module, "MAX_SQLITE_SNAPSHOT_BYTES", 4096)
    elif limit == "schema-bytes":
        monkeypatch.setattr(snapshot_module, "_MAX_SCHEMA_BYTES", 1)
    elif limit == "schema-rows":
        monkeypatch.setattr(snapshot_module, "_MAX_SCHEMA_ROWS", 1)
    else:
        monkeypatch.setattr(snapshot_module, "_MAX_SCHEMA_FIELD_BYTES", 80)
    before = open_fd_count()
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert open_fd_count() == before and list(staging.iterdir()) == []


def test_committed_wal_size_is_checked_even_when_main_database_is_below_test_cap(
    tmp_path, monkeypatch,
):
    database = tmp_path / "wal.sqlite3"
    writer = create_database(database, wal=True, pages=40)
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    barrier = BackupWriteBarrier(tmp_path / ".open-node-backup.lock")
    cap = 65536
    assert database.stat().st_size < cap
    assert writer.execute("PRAGMA page_count").fetchone()[0] * 4096 > cap
    # Sidecar policy is also bounded; choose a cap above half the actual WAL
    # while still below the committed logical database size.
    cap = max(cap, Path(str(database) + "-wal").stat().st_size // 2 + 4096)
    monkeypatch.setattr(snapshot_module, "MAX_SQLITE_SNAPSHOT_BYTES", cap)
    try:
        with barrier.snapshot() as permit:
            assert_safe_error(lambda: enter_snapshot(database, staging, permit))
        assert list(staging.iterdir()) == []
    finally:
        writer.close()
        barrier.close()


def test_staging_parent_rebound_during_real_copy_is_rejected_and_other_files_survive(
    layout, monkeypatch,
):
    database, staging, barrier = layout
    retained = staging.parent / "retained-original-staging"
    rebounded = False

    def rebind(source, target, status, remaining, total):
        nonlocal rebounded
        if not rebounded:
            staging.rename(retained)
            staging.mkdir(mode=0o700)
            (staging / "unrelated").write_bytes(b"must not be deleted")
            rebounded = True

    before = open_fd_count()
    with track_connections(monkeypatch, on_progress=rebind):
        with barrier.snapshot() as permit:
            assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert rebounded and list(retained.iterdir()) == []
    assert (staging / "unrelated").read_bytes() == b"must not be deleted"
    assert open_fd_count() == before


def test_preexisting_random_stage_name_is_never_reused_or_removed(layout, monkeypatch):
    database, staging, barrier = layout
    name = "sqlite-snapshot-" + "a" * 32
    existing = staging / name
    existing.mkdir(mode=0o700)
    marker = existing / "snapshot.sqlite3"
    marker.write_bytes(b"pre-existing destination must survive")
    monkeypatch.setattr(snapshot_module.secrets, "token_hex", lambda length: "a" * 32)
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert marker.read_bytes() == b"pre-existing destination must survive"


@pytest.mark.parametrize("outcome", ["interrupt", "cancel", "deadline"])
def test_real_sqlite_vm_progress_cancellation_or_deadline_preserves_safe_semantics(
    layout, monkeypatch, outcome,
):
    database, staging, barrier = layout
    connection = sqlite3.connect(database)
    connection.executemany("INSERT INTO parent VALUES (?, 'vm-iteration')",
                           [(value,) for value in range(2, 2000)])
    connection.commit()
    connection.close()
    in_integrity = False
    actual_vm_callbacks = 0
    real_progress = snapshot_module._Budget.progress

    def notice_execute(connection, sql):
        nonlocal in_integrity
        if sql == "PRAGMA integrity_check(1)":
            in_integrity = True

    def interrupt_from_actual_vm(budget):
        nonlocal actual_vm_callbacks
        if in_integrity:
            actual_vm_callbacks += 1
            if outcome == "interrupt":
                budget.interrupted = KeyboardInterrupt()
            elif outcome == "cancel":
                budget.interrupted = asyncio.CancelledError()
            else:
                budget.deadline = 0
        return real_progress(budget)

    monkeypatch.setattr(snapshot_module._Budget, "progress", interrupt_from_actual_vm)
    before = open_fd_count()
    expected = {"interrupt": KeyboardInterrupt, "cancel": asyncio.CancelledError,
                "deadline": BackupSQLiteError}[outcome]
    with track_connections(monkeypatch, on_execute=notice_execute) as (connections, _):
        with barrier.snapshot() as permit:
            with pytest.raises(expected) as caught:
                enter_snapshot(database, staging, permit)
        assert all(connection.closed_by_module for connection in connections)
    assert actual_vm_callbacks > 0
    if outcome == "deadline":
        assert str(caught.value) == MESSAGE
    assert list(staging.iterdir()) == [] and open_fd_count() == before


@pytest.mark.parametrize("signal_kind", ["interrupt", "cancel", "ordinary", "expired-permit",
                                        "replaced-lock", "replaced-source", "expired-clock"])
def test_failure_during_real_backup_steps_closes_every_connection_fd_and_private_file(
    layout, monkeypatch, signal_kind,
):
    database, staging, barrier = layout
    scope = barrier.snapshot()
    permit = scope.__enter__()
    closed_scope = False
    before_fds = open_fd_count()
    calls = 0

    def interrupt(source, target, status, remaining, total):
        nonlocal closed_scope, calls
        calls += 1
        if signal_kind == "interrupt":
            raise KeyboardInterrupt()
        if signal_kind == "cancel":
            raise asyncio.CancelledError()
        if signal_kind == "ordinary":
            raise RuntimeError("secret path/sql/raw-provider-value")
        if signal_kind == "expired-permit":
            scope.__exit__(None, None, None)
            closed_scope = True
        elif signal_kind == "replaced-lock":
            lock = database.parent / ".open-node-backup.lock"
            lock.rename(database.parent / "retained-original-lock")
            lock.touch(mode=0o600)
        elif signal_kind == "replaced-source":
            database.rename(database.parent / "retained-original.sqlite3")
            database.write_bytes(b"replaced source")
        elif signal_kind == "expired-clock":
            monkeypatch.setattr(snapshot_module.time, "monotonic", lambda: float("inf"))

    try:
        with track_connections(monkeypatch, on_progress=interrupt) as (connections, observations):
            expected = {"interrupt": KeyboardInterrupt, "cancel": asyncio.CancelledError,
                        "expired-permit": BackupCoordinationError,
                        "replaced-lock": BackupCoordinationError}.get(
                            signal_kind, BackupSQLiteError
                        )
            with pytest.raises(expected) as caught:
                enter_snapshot(database, staging, permit)
            assert calls > 0 and observations
            if expected is BackupSQLiteError:
                assert str(caught.value) == MESSAGE
            assert all(connection.closed_by_module for connection in connections)
    finally:
        if not closed_scope:
            scope.__exit__(None, None, None)
    assert open_fd_count() == before_fds - 1  # snapshot's exclusive FD was released
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("phase", ["target-create", "source-connect", "target-connect",
                                   "validation-connect", "readonly-open", "fdopen"])
def test_specific_io_failures_never_yield_or_leak_partial_staging(layout, monkeypatch, phase):
    database, staging, barrier = layout
    real_open, real_connect, real_fdopen = os.open, sqlite3.connect, os.fdopen
    calls = 0
    injected = False

    def open_fault(path, flags, *args, **kwargs):
        nonlocal injected
        if str(path) == "snapshot.sqlite3":
            if phase == "target-create" and flags & os.O_CREAT:
                injected = True
                raise OSError(errno.ENOSPC, "/secret/target must not leak")
            if phase == "readonly-open" and flags & os.O_ACCMODE == os.O_RDONLY:
                injected = True
                raise OSError(errno.EIO, "/secret/readonly must not leak")
        return real_open(path, flags, *args, **kwargs)

    def connect_fault(*args, **kwargs):
        nonlocal calls, injected
        calls += 1
        selected = {"source-connect": 1, "target-connect": 2, "validation-connect": 3}
        if selected.get(phase) == calls:
            injected = True
            raise sqlite3.OperationalError("private SQL and private path")
        return real_connect(*args, **kwargs)

    def fdopen_fault(*args, **kwargs):
        nonlocal injected
        if phase == "fdopen":
            injected = True
            raise OSError(errno.EMFILE, "secret fd table")
        return real_fdopen(*args, **kwargs)

    before = open_fd_count()
    monkeypatch.setattr(snapshot_module.os, "open", open_fault)
    monkeypatch.setattr(snapshot_module.sqlite3, "connect", connect_fault)
    monkeypatch.setattr(snapshot_module.os, "fdopen", fdopen_fault)
    with barrier.snapshot() as permit:
        assert_safe_error(lambda: enter_snapshot(database, staging, permit))
    assert injected and open_fd_count() == before
    assert list(staging.iterdir()) == []


def test_exception_inside_consumer_preserves_exception_and_closes_anonymous_result(layout):
    database, staging, barrier = layout
    before = open_fd_count()
    with barrier.snapshot() as permit:
        with pytest.raises(ValueError, match="consumer-owned"):
            with sqlite_backup_snapshot(
                database, permit=permit, staging_directory=staging,
            ) as result:
                raise ValueError("consumer-owned")
    assert result.stream.closed and list(staging.iterdir()) == []
    assert open_fd_count() == before


def test_independent_process_shared_flock_blocks_permit_then_allows_real_snapshot(layout):
    database, staging, barrier = layout
    read_ready, write_ready = os.pipe()
    read_release, write_release = os.pipe()
    script = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_SH)
os.write(int(sys.argv[2]), b'R')
os.read(int(sys.argv[3]), 1)
os.close(fd)
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(database.parent / ".open-node-backup.lock"),
         str(write_ready), str(read_release)], pass_fds=(write_ready, read_release),
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"},
    )
    os.close(write_ready)
    os.close(read_release)
    try:
        assert select.select([read_ready], [], [], 5)[0]
        assert os.read(read_ready, 1) == b"R"
        with pytest.raises(BackupBusyError):
            with barrier.snapshot(timeout=0.03):
                pytest.fail("external shared lock cannot yield a snapshot permit")
        os.write(write_release, b"X")
        assert child.communicate(timeout=5) == (b"", b"")
        assert child.returncode == 0
        with barrier.snapshot() as permit:
            assert enter_snapshot(database, staging, permit) > 0
    finally:
        os.close(read_ready)
        os.close(write_release)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_actual_sigint_during_paginated_backup_propagates_and_cleans_up(layout, monkeypatch):
    database, staging, barrier = layout
    before = open_fd_count()
    sent = False

    def signal_self(source, target, status, remaining, total):
        nonlocal sent
        sent = True
        os.kill(os.getpid(), signal.SIGINT)

    with track_connections(monkeypatch, on_progress=signal_self) as (connections, _):
        with barrier.snapshot() as permit:
            with pytest.raises(KeyboardInterrupt):
                enter_snapshot(database, staging, permit)
        assert sent and all(connection.closed_by_module for connection in connections)
    assert open_fd_count() == before and list(staging.iterdir()) == []


def test_snapshot_actual_thread_owns_permit_until_real_sqlite_work_finishes(layout, monkeypatch):
    database, staging, barrier = layout
    entered, release = threading.Event(), threading.Event()

    def hold_real_backup(source, target, status, remaining, total):
        entered.set()
        assert release.wait(5)

    def snapshot_in_thread():
        with barrier.snapshot() as permit:
            with sqlite_backup_snapshot(
                database, permit=permit, staging_directory=staging,
            ) as result:
                return result.sha256

    with track_connections(monkeypatch, on_progress=hold_real_backup):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(snapshot_in_thread)
            try:
                assert entered.wait(5)
                with pytest.raises(BackupBusyError):
                    with barrier.operation():
                        pytest.fail("work cannot enter during the SQLite copy")
            finally:
                release.set()
            assert len(future.result(timeout=5)) == 64
    with barrier.operation():
        pass
    assert list(staging.iterdir()) == []
