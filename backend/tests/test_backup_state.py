import errno
import fcntl
import hashlib
import io
import os
import resource
import stat
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import pytest
from open_node.domain.backup import MAX_FILES, MAX_TOTAL_FILE_BYTES
from open_node.services import backup_state as module
from open_node.services.backup_coordination import (
    BackupCoordinationError,
    BackupSnapshotPermit,
    BackupWriteBarrier,
)
from open_node.services.backup_state import (
    BackupStateError,
    BackupStateLayout,
    staged_backup_state,
)


def private_file(path: Path, content: bytes) -> Path:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    # Path.mkdir(parents=True) applies mode only to the leaf directory. Create
    # every missing fixture parent explicitly, without repairing existing ones.
    for parent in reversed(missing):
        parent.mkdir(mode=0o700)
    path.write_bytes(content)
    path.chmod(0o600)
    return path


@pytest.fixture
def state(tmp_path):
    database = private_file(tmp_path / "data" / "instance.db", b"database is separate")
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    layout = BackupStateLayout(
        database=database,
        certificates=tmp_path / "certificates",
        external_subscriptions=tmp_path / "external",
        notifications=tmp_path / "notifications",
        agent_identity=None,
    )
    barrier = BackupWriteBarrier(database.parent / ".open-node-backup.lock")
    try:
        yield layout, staging, barrier
    finally:
        barrier.close()


def copied(state, permit, *, layout=None, database_size=20):
    initial, staging, _barrier = state
    return staged_backup_state(
        initial if layout is None else layout, permit=permit,
        staging_directory=staging, database_size=database_size,
    )


@pytest.mark.parametrize("mask", [0o022, 0o027, 0o077])
def test_deep_private_state_is_accepted_under_common_umasks(state, mask):
    layout, staging, barrier = state
    path = layout.certificates / "jobs/unfinished/http/state.json"
    previous = os.umask(mask)
    try:
        private_file(path, b'{"cleanup":true}')
    finally:
        os.umask(previous)
    for parent in (layout.certificates, *path.parents[:3]):
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with barrier.snapshot(timeout=0) as permit:
        with copied(state, permit) as result:
            assert result.sources[
                "data/certificates/jobs/unfinished/http/state.json"
            ].read() == b'{"cleanup":true}'
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("mask", [0o022, 0o027, 0o077])
def test_fixture_does_not_repair_existing_public_state_directory(state, mask):
    layout, staging, barrier = state
    layout.certificates.mkdir(mode=0o700)
    layout.certificates.chmod(0o755)
    path = layout.certificates / "jobs/unfinished/http/state.json"
    previous = os.umask(mask)
    try:
        private_file(path, b"must-not-copy")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(layout.certificates.stat().st_mode) == 0o755
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Existing public directories must still be rejected")
    assert stat.S_IMODE(layout.certificates.stat().st_mode) == 0o755
    assert path.read_bytes() == b"must-not-copy"
    assert list(staging.iterdir()) == []


def test_all_roles_jobs_and_independent_slices_remain_stable_after_permit_release(state):
    layout, staging, barrier = state
    files = {
        "data/certificates/vault.key": (layout.certificates / "vault.key", b"certificate-key"),
        "data/certificates/vault.initialized": (
            layout.certificates / "vault.initialized", b"certificate-marker",
        ),
        "data/certificates/jobs/unfinished/http/state.json": (
            layout.certificates / "jobs/unfinished/http/state.json", b'{"cleanup":true}',
        ),
        "data/external-subscriptions/vault.key": (
            layout.external_subscriptions / "vault.key", b"external-key",
        ),
        "data/external-subscriptions/vault.initialized": (
            layout.external_subscriptions / "vault.initialized", b"external-marker",
        ),
        "data/notifications/telegram.key": (
            layout.notifications / "telegram.key", b"notification-key",
        ),
        "data/notifications/telegram.initialized": (
            layout.notifications / "telegram.initialized", b"notification-marker",
        ),
        "secrets/agent-identity.seed": (staging.parent / "identity" / "seed", b"s" * 32),
    }
    for path, content in files.values():
        private_file(path, content)
    layout = replace(layout, agent_identity=files["secrets/agent-identity.seed"][0])
    private_file(layout.certificates / "worker.lock", b"")
    private_file(layout.certificates / "vault.lock", b"")
    private_file(layout.external_subscriptions / "vault.lock", b"")
    with ExitStack() as lifetime:
        with barrier.snapshot(timeout=0) as permit:
            result = lifetime.enter_context(copied(state, permit, layout=layout))
            assert result.present_roots == {
                "certificates", "external_subscriptions", "notifications", "agent_identity",
            }
            assert set(result.sources) == set(files)
            assert list(staging.iterdir()) == []
            with pytest.raises(TypeError):
                result.sources["new"] = io.BytesIO()
            for entry in result.entries:
                expected = files[entry.path][1]
                assert (entry.size, entry.sha256) == (
                    len(expected), hashlib.sha256(expected).hexdigest(),
                )
        with barrier.operation():
            for path, _content in files.values():
                path.write_bytes(b"later state")
        for name, source in result.sources.items():
            assert source.read(2) == files[name][1][:2]
        for name, source in result.sources.items():
            assert source.read() == files[name][1][2:]
            assert source.seek(0) == 0
            assert source.read() == files[name][1]
            assert not source.writable()
            with pytest.raises((OSError, NotImplementedError)):
                source.write(b"not writable")
        spool = next(iter(result.sources.values()))._spool
        assert os.fstat(spool.fileno()).st_nlink == 0
        assert fcntl.fcntl(spool.fileno(), fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        with pytest.raises(OSError) as error:
            os.write(spool.fileno(), b"not writable")
        assert error.value.errno == errno.EBADF
    assert spool.closed
    assert all(source.closed for source in result.sources.values())
    assert list(staging.iterdir()) == []


def test_missing_or_lock_only_state_is_reported_without_creating_keys(state):
    layout, staging, barrier = state
    with barrier.snapshot(timeout=0) as permit:
        with copied(state, permit) as result:
            assert not result.entries and not result.sources and not result.present_roots
    assert not layout.certificates.exists()
    private_file(layout.certificates / "worker.lock", b"")
    with barrier.snapshot(timeout=0) as permit:
        with copied(state, permit) as result:
            assert not result.entries
            assert result.present_roots == {"certificates"}
    assert {path.name for path in layout.certificates.iterdir()} == {"worker.lock"}
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("kind", ["none", "forged", "expired", "wrong-database"])
def test_permit_identity_and_database_binding_precede_file_access(
    state, tmp_path, monkeypatch, kind,
):
    layout, _staging, barrier = state
    if kind == "expired":
        with barrier.snapshot(timeout=0) as permit:
            pass
    elif kind == "forged":
        permit = BackupSnapshotPermit(barrier)
    else:
        permit = None

    def unexpected(*_args, **_kwargs):
        raise AssertionError("State files must not be inspected without the matching permit")

    monkeypatch.setattr(module, "_directory", unexpected)
    with ExitStack() as stack:
        if kind == "wrong-database":
            permit = stack.enter_context(barrier.snapshot(timeout=0))
            layout = replace(layout, database=tmp_path / "different" / "instance.db")
        with pytest.raises(BackupCoordinationError):
            with copied(state, permit, layout=layout):
                pytest.fail("Untrusted permit reached the body")


@pytest.mark.parametrize("kind", [
    "symlink", "hardlink", "fifo", "world-readable", "executable", "directory-public",
    "symlink-root", "symlink-ancestor", "root-file", "nonempty-lock",
])
def test_unsafe_source_entries_fail_without_following_or_repairing_them(state, tmp_path, kind):
    layout, staging, barrier = state
    path = layout.certificates / "entry"
    sentinel = private_file(tmp_path / "outside" / "sentinel", b"do-not-copy-or-change")
    if kind == "symlink-root":
        layout.certificates.symlink_to(sentinel.parent, target_is_directory=True)
    elif kind == "symlink-ancestor":
        alias = tmp_path / "alias"
        alias.symlink_to(sentinel.parent, target_is_directory=True)
        layout = replace(layout, certificates=alias / "missing")
    elif kind == "root-file":
        private_file(layout.certificates, b"not a directory")
    else:
        layout.certificates.mkdir(mode=0o700)
        if kind == "symlink":
            path.symlink_to(sentinel)
        elif kind == "hardlink":
            os.link(sentinel, path)
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        elif kind == "directory-public":
            path.mkdir(mode=0o755)
            path.chmod(0o755)
        elif kind == "nonempty-lock":
            private_file(layout.certificates / "worker.lock", b"unexpected state")
        else:
            private_file(path, b"secret")
            path.chmod(0o644 if kind == "world-readable" else 0o700)
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError, match="^Backup state snapshot is unavailable\\.$"):
            with copied(state, permit, layout=layout):
                pytest.fail("Unsafe source yielded")
    assert sentinel.read_bytes() == b"do-not-copy-or-change"
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("kind", ["same-root", "nested-root", "database-root", "staging-root"])
def test_layout_rejects_overlapping_state_database_and_staging(state, kind):
    layout, staging, barrier = state
    changes = {
        "same-root": {"notifications": layout.certificates},
        "nested-root": {"notifications": layout.certificates / "nested"},
        "database-root": {"certificates": layout.database.parent},
        "staging-root": {"certificates": staging},
    }[kind]
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit, layout=replace(layout, **changes)):
                pytest.fail("Overlapping layout yielded")


@pytest.mark.parametrize("name", ["unexpected", "jobs/entry", "worker.lock"])
def test_external_state_accepts_only_its_exact_key_pair_and_empty_runtime_lock(state, name):
    layout, _staging, barrier = state
    private_file(layout.external_subscriptions / name, b"unexpected")
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Unknown external state was silently omitted")


@pytest.mark.parametrize("name", ["vault.key", "vault.lock", "subdir/token", "token.txt"])
def test_notification_unknown_state_is_not_silently_omitted(state, name):
    layout, _staging, barrier = state
    private_file(layout.notifications / name, b"unexpected")
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Unknown notification state yielded")


@pytest.mark.parametrize("kind", ["missing", "short", "long", "symlink"])
def test_explicit_agent_identity_requires_one_private_32_byte_seed(state, tmp_path, kind):
    layout, _staging, barrier = state
    seed = tmp_path / "identity" / "seed"
    seed.parent.mkdir(mode=0o700)
    if kind == "symlink":
        target = private_file(tmp_path / "other-seed", b"s" * 32)
        seed.symlink_to(target)
    elif kind != "missing":
        private_file(seed, b"s" * (31 if kind == "short" else 33))
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit, layout=replace(layout, agent_identity=seed)):
                pytest.fail("Invalid Agent seed yielded")


@pytest.mark.parametrize("size", [None, True, -1, MAX_TOTAL_FILE_BYTES + 1])
def test_database_budget_is_strict_before_staging(state, size):
    _layout, staging, barrier = state
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit, database_size=size):
                pytest.fail("Invalid database budget yielded")
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("payload_size", [7, 8])
def test_state_bytes_share_the_database_one_gib_budget(state, payload_size):
    layout, staging, barrier = state
    private_file(layout.certificates / "payload", b"p" * payload_size)
    with barrier.snapshot(timeout=0) as permit:
        if payload_size == 8:
            with pytest.raises(BackupStateError):
                with copied(state, permit, database_size=MAX_TOTAL_FILE_BYTES - 7):
                    pytest.fail("Aggregate size overflow yielded")
        else:
            with copied(state, permit, database_size=MAX_TOTAL_FILE_BYTES - 7) as result:
                assert result.entries[0].size == 7
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("count", [MAX_FILES - 1, MAX_FILES])
def test_full_file_budget_uses_one_spool_not_one_descriptor_per_file(state, count):
    layout, _staging, barrier = state
    for index in range(count):
        private_file(layout.certificates / f"file-{index:04}", b"")
    previous = resource.getrlimit(resource.RLIMIT_NOFILE)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(128, previous[0]), previous[1]))
        with barrier.snapshot(timeout=0) as permit:
            if count == MAX_FILES:
                with pytest.raises(BackupStateError):
                    with copied(state, permit):
                        pytest.fail("File-count overflow yielded")
            else:
                with copied(state, permit) as result:
                    assert len(result.entries) == count
                    assert len({id(stream._spool) for stream in result.sources.values()}) == 1
                    assert all(stream.read(1) == b"" for stream in result.sources.values())
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, previous)


@pytest.mark.parametrize("kind", ["grow", "shrink", "replace", "mode", "rebind-root"])
def test_observed_source_changes_reject_snapshot_without_publishing(state, monkeypatch, kind):
    layout, staging, barrier = state
    path = private_file(layout.certificates / "payload", b"original")
    original = module.os.read
    changed = False

    def changing(fd, size):
        nonlocal changed
        block = original(fd, size)
        if not changed:
            changed = True
            if kind == "grow":
                path.write_bytes(b"original-plus")
            elif kind == "shrink":
                path.write_bytes(b"o")
            elif kind == "replace":
                path.unlink()
                private_file(path, b"replaced")
            elif kind == "mode":
                path.chmod(0o644)
            else:
                layout.certificates.rename(layout.certificates.with_name("old-certificates"))
                layout.certificates.mkdir(mode=0o700)
        return block

    monkeypatch.setattr(module.os, "read", changing)
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Changed source yielded")
    assert changed and list(staging.iterdir()) == []


@pytest.mark.parametrize("failure", [OSError(errno.ENOSPC, "secret-path"), KeyboardInterrupt()])
def test_io_failure_and_interruption_clean_anonymous_spool_and_preserve_base_exception(
    state, monkeypatch, failure,
):
    layout, staging, barrier = state
    private_file(layout.certificates / "payload", b"secret")
    before = len(list(Path("/proc/self/fd").iterdir()))

    def fail(_fd):
        raise failure

    monkeypatch.setattr(module.os, "fsync", fail)
    with barrier.snapshot(timeout=0) as permit:
        expected = KeyboardInterrupt if isinstance(failure, KeyboardInterrupt) else BackupStateError
        with pytest.raises(expected) as error:
            with copied(state, permit):
                pytest.fail("Interrupted copy yielded")
        if isinstance(failure, KeyboardInterrupt):
            assert error.value is failure
        else:
            assert "secret-path" not in str(error.value)
    assert list(staging.iterdir()) == []
    assert len(list(Path("/proc/self/fd").iterdir())) == before


def test_deadline_is_enforced_before_yield_and_does_not_renew_per_file(state, monkeypatch):
    layout, staging, barrier = state
    private_file(layout.certificates / "payload", b"secret")
    original = module.os.fsync
    elapsed = 0
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed)

    def expire(fd):
        nonlocal elapsed
        original(fd)
        elapsed = module.MAX_STATE_SECONDS

    monkeypatch.setattr(module.os, "fsync", expire)
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Expired copy yielded")
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("kind", ["permit", "deadline"])
def test_final_slice_construction_cannot_yield_after_permit_or_deadline_expiry(
    state, monkeypatch, kind,
):
    layout, staging, barrier = state
    private_file(layout.certificates / "payload", b"secret")
    original = module._Slice
    created = []
    elapsed = 0
    monkeypatch.setattr(module.time, "monotonic", lambda: elapsed)
    with ExitStack() as scope:
        permit = scope.enter_context(barrier.snapshot(timeout=0))

        def expire(*args):
            nonlocal elapsed
            result = original(*args)
            created.append(result)
            if kind == "permit":
                scope.close()
            else:
                elapsed = module.MAX_STATE_SECONDS
            return result

        monkeypatch.setattr(module, "_Slice", expire)
        expected = BackupCoordinationError if kind == "permit" else BackupStateError
        with pytest.raises(expected):
            with copied(state, permit):
                pytest.fail("Snapshot expired while preparing its final result")
    assert created and all(stream.closed for stream in created)
    assert list(staging.iterdir()) == []


def test_consumer_exception_is_not_relabelled_and_all_streams_close(state):
    layout, staging, barrier = state
    private_file(layout.certificates / "payload", b"secret")
    failure = ValueError("consumer error")
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(ValueError) as error:
            with copied(state, permit) as result:
                raise failure
    assert error.value is failure
    assert all(stream.closed for stream in result.sources.values())
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("kind", ["directories", "depth", "invalid-name"])
def test_directory_walk_and_logical_paths_have_independent_limits(state, monkeypatch, kind):
    layout, staging, barrier = state
    if kind == "directories":
        monkeypatch.setattr(module, "MAX_STATE_ITEMS", 3)
        for index in range(4):
            (layout.certificates / str(index)).mkdir(mode=0o700, parents=True)
    elif kind == "depth":
        monkeypatch.setattr(module, "MAX_STATE_DEPTH", 2)
        (layout.certificates / "a" / "b" / "c").mkdir(mode=0o700, parents=True)
    else:
        private_file(layout.certificates / "name:invalid", b"secret")
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Unbounded or invalid tree yielded")
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("kind", ["missing", "symlink", "public", "non-directory"])
def test_staging_requires_an_existing_owned_private_real_directory(state, tmp_path, kind):
    layout, staging, barrier = state
    staging.rmdir()
    if kind == "symlink":
        other = tmp_path / "other-staging"
        other.mkdir(mode=0o700)
        staging.symlink_to(other, target_is_directory=True)
    elif kind == "public":
        staging.mkdir(mode=0o755)
        staging.chmod(0o755)
    elif kind == "non-directory":
        private_file(staging, b"not a directory")
    with barrier.snapshot(timeout=0) as permit:
        with pytest.raises(BackupStateError):
            with copied(state, permit):
                pytest.fail("Unsafe staging directory yielded")
    assert not layout.certificates.exists()


def test_slice_boundaries_do_not_leak_neighbouring_files_and_seek_independently(state):
    layout, _staging, barrier = state
    private_file(layout.certificates / "first", b"A" * (module.READ_CHUNK_BYTES + 3))
    private_file(layout.certificates / "second", b"second-secret")
    with barrier.snapshot(timeout=0) as permit:
        with copied(state, permit) as result:
            first = result.sources["data/certificates/first"]
            second = result.sources["data/certificates/second"]
            assert first.read() == b"A" * module.READ_CHUNK_BYTES
            assert first.read() == b"AAA" and first.read() == b""
            assert second.tell() == 0 and second.read(6) == b"second"
            assert first.seek(-1, io.SEEK_END) == module.READ_CHUNK_BYTES + 2
            assert first.read(100) == b"A"
            assert second.read() == b"-secret"
            assert first.seek(10**30) == 10**30 and first.read() == b""
            for operation in (lambda: first.seek(-1), lambda: first.read(-2)):
                with pytest.raises(ValueError):
                    operation()
