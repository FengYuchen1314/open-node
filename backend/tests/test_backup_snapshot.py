import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from dataclasses import replace

import pytest
from open_node.core.config import Settings
from open_node.services import backup_snapshot as module
from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier
from open_node.services.backup_runtime import backup_operation
from open_node.services.backup_snapshot import (
    BackupSnapshotError,
    capture_control_plane_snapshot,
    configured_backup_layout,
)
from open_node.services.backup_state import BackupStateError, BackupStateLayout


@pytest.fixture
def instance(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / "instance.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE consistency (value INTEGER NOT NULL)")
    connection.execute("INSERT INTO consistency VALUES (1)")
    connection.commit()
    connection.close()
    database.chmod(0o600)
    certificates = data / "certificates"
    certificates.mkdir(mode=0o700)
    state_file = certificates / "state"
    state_file.write_bytes(b"1")
    state_file.chmod(0o600)
    staging = data / "staging"
    staging.mkdir(mode=0o700)
    layout = BackupStateLayout(database, certificates, None, None, None)
    barrier = BackupWriteBarrier(data / ".open-node-backup.lock")
    try:
        yield layout, staging, barrier
    finally:
        barrier.close()


def snapshot_value(snapshot):
    # R4's borrowed read-only connection is opened before unlinking the private
    # completed SQLite target, not by reopening a deleted /proc/self/fd pathname.
    return snapshot.database.connection.execute("SELECT value FROM consistency").fetchone()[0]


def update_instance(layout, barrier, value):
    with backup_operation(barrier):
        # SQLite's connection context commits/rolls back but does not close.
        with closing(sqlite3.connect(layout.database)) as connection, connection:
            connection.execute("UPDATE consistency SET value=?", (value,))
        (layout.certificates / "state").write_bytes(str(value).encode())


def test_actual_database_and_files_are_stable_after_exclusive_scope_ends(instance):
    layout, staging, barrier = instance
    with capture_control_plane_snapshot(layout, barrier=barrier, staging_directory=staging) as copy:
        assert snapshot_value(copy) == 1
        assert copy.state.sources["data/certificates/state"].read() == b"1"
        assert copy.restoration_ready is False
        assert copy.created_at.endswith("Z")
        assert list(staging.iterdir()) == []
        update_instance(layout, barrier, 2)
        assert snapshot_value(copy) == 1
        source = copy.state.sources["data/certificates/state"]
        source.seek(0)
        assert source.read() == b"1"
        assert source.writable() is False and copy.database.stream.writable() is False
    assert copy.database.stream.closed and source.closed
    with pytest.raises(sqlite3.ProgrammingError):
        snapshot_value(copy)
    assert list(staging.iterdir()) == []


def test_snapshot_waits_until_one_writer_finishes_both_database_and_file_changes(instance):
    layout, staging, barrier = instance
    committed, finish_files, started = threading.Event(), threading.Event(), threading.Event()

    def writer():
        with backup_operation(barrier):
            with closing(sqlite3.connect(layout.database)) as connection, connection:
                connection.execute("UPDATE consistency SET value=2")
            committed.set()
            assert finish_files.wait(5)
            (layout.certificates / "state").write_bytes(b"2")

    def capture():
        started.set()
        with capture_control_plane_snapshot(
            layout, barrier=barrier, staging_directory=staging,
        ) as copy:
            return snapshot_value(copy), copy.state.sources["data/certificates/state"].read()

    with ThreadPoolExecutor(max_workers=2) as executor:
        write_future = executor.submit(writer)
        assert committed.wait(5)
        read_future = executor.submit(capture)
        try:
            assert started.wait(5)
            assert not read_future.done()
        finally:
            finish_files.set()
        write_future.result(timeout=5)
        assert read_future.result(timeout=5) == (2, b"2")


def test_a_work_context_must_not_call_snapshot_or_silently_drop_its_lease(instance):
    layout, staging, barrier = instance
    with backup_operation(barrier):
        with pytest.raises(BackupBusyError):
            with capture_control_plane_snapshot(
                layout, barrier=barrier, staging_directory=staging,
            ):
                pytest.fail("Snapshot accepted its own work lease")
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("failure", [RuntimeError("consumer"), asyncio.CancelledError()])
def test_result_lifetime_closes_every_snapshot_resource_on_consumer_failure(instance, failure):
    layout, staging, barrier = instance
    with pytest.raises(type(failure)) as caught:
        with capture_control_plane_snapshot(
            layout, barrier=barrier, staging_directory=staging,
        ) as copy:
            raise failure
    assert caught.value is failure
    assert copy.database.stream.closed
    assert all(stream.closed for stream in copy.state.sources.values())
    update_instance(layout, barrier, 3)
    assert list(staging.iterdir()) == []


def test_bad_state_after_real_sqlite_copy_closes_database_and_resumes_application(
    instance, monkeypatch,
):
    layout, staging, barrier = instance
    original = module.sqlite_backup_snapshot
    captured = []

    def remember(*args, **kwargs):
        class Observe:
            def __enter__(self):
                self.scope = original(*args, **kwargs)
                result = self.scope.__enter__()
                captured.append(result)
                return result

            def __exit__(self, *error):
                return self.scope.__exit__(*error)

        return Observe()

    (layout.certificates / "state").chmod(0o644)
    monkeypatch.setattr(module, "sqlite_backup_snapshot", remember)
    with pytest.raises(BackupStateError):
        with capture_control_plane_snapshot(layout, barrier=barrier, staging_directory=staging):
            pytest.fail("Unsafe source state yielded")
    assert len(captured) == 1 and captured[0].stream.closed
    (layout.certificates / "state").chmod(0o600)
    update_instance(layout, barrier, 4)
    assert list(staging.iterdir()) == []


def test_overlap_is_rejected_before_creating_sqlite_staging_files(instance, monkeypatch):
    layout, _staging, barrier = instance

    def unexpected(*args, **kwargs):
        pytest.fail("SQLite must not use a source-state tree as staging")

    monkeypatch.setattr(module, "sqlite_backup_snapshot", unexpected)
    with pytest.raises(BackupStateError):
        with capture_control_plane_snapshot(
            layout, barrier=barrier, staging_directory=layout.certificates,
        ):
            pytest.fail("Overlapping staging yielded")
    assert {path.name for path in layout.certificates.iterdir()} == {"state"}


@pytest.mark.parametrize("url", [
    "sqlite://", "sqlite:///:memory:", "sqlite+pysqlite:///:memory:",
    "sqlite:///file:memory?mode=memory&uri=true", "sqlite:///database?uri=false",
    "postgresql://localhost/instance", "sqlite+aiosqlite:///database", "not-a-url",
])
def test_unsupported_database_layouts_fail_without_creating_any_files(tmp_path, monkeypatch, url):
    monkeypatch.chdir(tmp_path)
    settings = Settings(database_url=url, _env_file=None)
    with pytest.raises(
        BackupSnapshotError, match="^Control-plane backup snapshot is unavailable\\.$",
    ):
        configured_backup_layout(settings)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("driver", ["sqlite", "sqlite+pysqlite"])
def test_configured_paths_match_application_defaults_without_initializing_storage(
    tmp_path, monkeypatch, driver,
):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        database_url=f"{driver}:///missing/data.db", certificate_state_dir="certificates",
        _env_file=None,
    )
    layout = configured_backup_layout(settings)
    assert layout.database == tmp_path / "missing" / "data.db"
    assert layout.certificates == tmp_path / "certificates"
    assert layout.external_subscriptions == tmp_path / "missing" / "external-subscriptions"
    assert layout.notifications == tmp_path / "missing" / "notifications"
    assert layout.agent_identity is None
    assert list(tmp_path.iterdir()) == []


def test_custom_directories_and_identity_are_never_silently_replaced_with_defaults(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'db' / 'state.db'}",
        certificate_state_dir=tmp_path / "certs",
        external_subscriptions_state_dir=tmp_path / "external",
        notifications_state_dir=tmp_path / "notifications",
        agent_identity_file=tmp_path / "keys" / "seed", _env_file=None,
    )
    layout = configured_backup_layout(settings)
    assert layout.certificates == settings.certificate_state_dir
    assert layout.external_subscriptions == settings.external_subscriptions_state_dir
    assert layout.notifications == settings.notifications_state_dir
    assert layout.agent_identity == settings.agent_identity_file
    assert list(tmp_path.iterdir()) == []


def test_second_snapshot_does_not_close_a_first_completed_snapshot(instance):
    layout, staging, barrier = instance
    with ExitStack() as lifetime:
        first = lifetime.enter_context(capture_control_plane_snapshot(
            layout, barrier=barrier, staging_directory=staging,
        ))
        update_instance(layout, barrier, 2)
        with capture_control_plane_snapshot(
            replace(layout), barrier=barrier, staging_directory=staging,
        ) as second:
            assert snapshot_value(first) == 1 and snapshot_value(second) == 2
        assert second.database.stream.closed
        assert snapshot_value(first) == 1
