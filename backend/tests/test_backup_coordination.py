"""Real local flock/concurrency checks for the opt-in coordination primitive.

These tests do not claim that application writers have been integrated. Child
fixtures use only synthetic lock files, pipes and the standard-library module.
"""

import asyncio
import contextvars
import errno
import fcntl
import gc
import json
import os
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from open_node.services import backup_coordination as coordination
from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupSnapshotPermit,
    BackupWriteBarrier,
    BackupWriteLease,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
UNAVAILABLE = "Backup coordination is unavailable."
BUSY = "Backup coordination is busy; try again later."


@pytest.fixture
def barrier(tmp_path):
    instance = BackupWriteBarrier(tmp_path / "backup.lock")
    try:
        yield instance
    finally:
        instance.close()


def assert_unavailable(action):
    with pytest.raises(BackupCoordinationError) as caught:
        action()
    assert type(caught.value) is BackupCoordinationError
    assert str(caught.value) == UNAVAILABLE
    assert caught.value.code == "backup_coordination_unavailable"


def enter_operation(barrier, *, kind="work"):
    with barrier.operation(kind=kind):
        return True


def enter_snapshot(barrier, *, timeout=0):
    with barrier.snapshot(timeout=timeout) as permit:
        permit.assert_active()
        return True


def assert_busy(action):
    with pytest.raises(BackupBusyError) as caught:
        action()
    assert str(caught.value) == BUSY
    assert caught.value.code == "backup_busy"


def wait_phase(barrier, kind):
    with barrier._condition:
        assert barrier._condition.wait_for(
            lambda: getattr(barrier, f"_{kind}_paused"), timeout=3
        )


def assert_admission_restored(barrier):
    assert enter_operation(barrier)
    assert enter_operation(barrier, kind="agent")
    assert enter_snapshot(barrier)


@contextmanager
def held_operation(barrier, kind="work"):
    entered, release = threading.Event(), threading.Event()
    state = {}

    def hold():
        with barrier.operation(kind=kind) as lease:
            state["lease"] = lease
            state["thread"] = threading.get_ident()
            entered.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        try:
            assert entered.wait(3)
            yield state, release
        finally:
            release.set()
            future.result(timeout=5)


def child_env():
    return {
        "PYTHONPATH": str(APP_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def run_child(script, *args):
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *map(str, args)],
        env=child_env(), capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_none_mode_allows_operations_but_never_a_snapshot():
    instance = BackupWriteBarrier(None)
    with instance.operation() as lease:
        assert lease.child_fds == ()
        with instance.operation() as nested:
            assert nested.child_fds == ()
        with instance.operation(kind="agent") as agent:
            assert agent.child_fds == ()
    assert_unavailable(lambda: enter_snapshot(instance))
    assert enter_operation(instance)
    instance.close()
    instance.close()
    assert_unavailable(lambda: enter_operation(instance))


def test_new_file_is_private_and_anchor_is_not_itself_locked(tmp_path):
    path = tmp_path / "backup.lock"
    instance = BackupWriteBarrier(path)
    try:
        info = path.stat()
        assert stat.S_ISREG(info.st_mode)
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert info.st_uid == os.geteuid() and info.st_nlink == 1
        assert info.st_size == 0
        fd = os.open(path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert_busy(lambda: enter_operation(instance))
        finally:
            os.close(fd)
        assert_admission_restored(instance)
    finally:
        instance.close()


def test_existing_file_is_not_truncated_or_repermissioned(tmp_path):
    path = tmp_path / "backup.lock"
    path.write_bytes(b"existing coordination metadata\x00not a new empty file")
    path.chmod(0o600)
    before = path.stat()
    instance = BackupWriteBarrier(path)
    try:
        assert_admission_restored(instance)
        after = path.stat()
        assert (
            after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ) == (
            before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns
        )
        assert path.read_bytes() == b"existing coordination metadata\x00not a new empty file"
    finally:
        instance.close()


def test_restrictive_umask_only_changes_the_newly_created_owned_file(tmp_path):
    previous = os.umask(0o777)
    try:
        instance = BackupWriteBarrier(tmp_path / "backup.lock")
    finally:
        os.umask(previous)
    try:
        assert stat.S_IMODE((tmp_path / "backup.lock").stat().st_mode) == 0o600
        assert_admission_restored(instance)
    finally:
        instance.close()


@pytest.mark.parametrize("mode", [0o000, 0o400, 0o640, 0o644, 0o700, 0o1600, 0o2600, 0o4600])
def test_existing_bad_modes_are_rejected_without_repair(tmp_path, mode):
    path = tmp_path / "private-path"
    path.write_bytes(b"preserve")
    path.chmod(mode)
    before = path.stat()
    assert_unavailable(lambda: BackupWriteBarrier(path))
    assert path.stat() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_non_regular_or_aliased_lock_files_are_not_opened_as_locks(tmp_path, kind):
    path = tmp_path / "private-path"
    other = tmp_path / "other"
    if kind in ("symlink", "hardlink"):
        other.write_bytes(b"untouched")
        other.chmod(0o600)
        if kind == "symlink":
            path.symlink_to(other)
        else:
            path.hardlink_to(other)
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.mkdir(mode=0o600)
    assert_unavailable(lambda: BackupWriteBarrier(path))
    if other.exists():
        assert other.read_bytes() == b"untouched"


@pytest.mark.parametrize("kind", ["missing", "symlink", "writable", "not-directory"])
def test_parent_must_be_an_existing_trusted_directory(tmp_path, kind):
    parent = tmp_path / "parent"
    if kind == "symlink":
        parent.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "writable":
        parent.mkdir()
        parent.chmod(0o777)
    elif kind == "not-directory":
        parent.write_bytes(b"not a directory")
    assert_unavailable(lambda: BackupWriteBarrier(parent / "backup.lock"))


def test_foreign_owner_is_rejected_without_modifying_the_file(tmp_path, monkeypatch):
    path = tmp_path / "backup.lock"
    path.write_bytes(b"owned by somebody else")
    path.chmod(0o600)
    before = path.stat()
    real_fstat, real_stat = coordination.os.fstat, coordination.os.stat

    def foreign(info):
        if stat.S_ISREG(info.st_mode):
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info

    # Owner-check unit injection only; the lock/conflict tests below use real flock.
    monkeypatch.setattr(coordination.os, "fstat", lambda fd: foreign(real_fstat(fd)))
    monkeypatch.setattr(
        coordination.os, "stat", lambda *args, **kwargs: foreign(real_stat(*args, **kwargs))
    )
    assert_unavailable(lambda: BackupWriteBarrier(path))
    monkeypatch.undo()
    assert path.stat() == before


@pytest.mark.parametrize("path", ["relative-string", 0, True, Path("/"), Path("..")])
def test_invalid_path_arguments_have_fixed_errors(path):
    assert_unavailable(lambda: BackupWriteBarrier(path))


@pytest.mark.parametrize("kind", [None, True, "", "read", "WORK", 1, []])
def test_invalid_kinds_do_not_create_a_lease(barrier, kind):
    assert_unavailable(lambda: enter_operation(barrier, kind=kind))
    assert_admission_restored(barrier)


@pytest.mark.parametrize("timeout", [True, False, None, "1", -1, float("nan"), float("inf"),
                                   -float("inf"), 10 ** 1000])
def test_invalid_timeouts_do_not_pause_admission(barrier, timeout):
    assert_unavailable(lambda: enter_snapshot(barrier, timeout=timeout))
    assert_admission_restored(barrier)


def test_nested_same_kind_joins_but_each_reference_expires_independently(barrier):
    with barrier.operation() as outer:
        outer_fd, = outer.child_fds
        assert not os.get_inheritable(outer_fd)
        with barrier.operation() as inner:
            assert inner.child_fds == (outer_fd,)
        assert_unavailable(lambda: inner.child_fds)
        assert outer.child_fds == (outer_fd,)
        assert_busy(lambda: enter_snapshot(barrier))
    assert_unavailable(lambda: outer.child_fds)
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(outer_fd)
    assert_admission_restored(barrier)


def test_independent_contexts_and_mixed_kinds_have_independent_open_descriptions(barrier):
    with barrier.operation() as work:
        with barrier.operation(kind="agent") as agent:
            assert work.child_fds != agent.child_fds
        context = contextvars.Context()
        scope = barrier.operation()
        independent = context.run(scope.__enter__)
        try:
            assert independent.child_fds != work.child_fds
            left, right = map(os.fstat, [work.child_fds[0], independent.child_fds[0]])
            assert (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
        finally:
            context.run(scope.__exit__, None, None, None)
    assert_admission_restored(barrier)


def test_copied_live_context_keeps_a_reference_after_outer_scope_exits(barrier):
    entered, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            with barrier.operation() as outer:
                expected = outer.child_fds
                context = contextvars.copy_context()

                def hold_joined():
                    with barrier.operation() as joined:
                        assert joined.child_fds == expected
                        entered.set()
                        assert release.wait(5)

                future = pool.submit(context.run, hold_joined)
                assert entered.wait(3)
            assert_unavailable(lambda: outer.child_fds)
            os.fstat(expected[0])
            assert_busy(lambda: enter_snapshot(barrier, timeout=0.03))
            assert_busy(barrier.close)
        finally:
            release.set()
            future.result(timeout=5)
    assert_admission_restored(barrier)


def test_expired_copied_context_cannot_bypass_snapshot_pause(barrier):
    with barrier.operation():
        old_context = contextvars.copy_context()
    with barrier.snapshot(timeout=0):
        assert_busy(lambda: old_context.run(enter_operation, barrier))
    assert old_context.run(enter_operation, barrier)
    assert_admission_restored(barrier)


def test_forged_lease_or_permit_object_is_not_authority(barrier):
    with barrier.operation() as lease:
        forged = BackupWriteLease(barrier, lease._record)
        assert_unavailable(lambda: forged.child_fds)
    with barrier.snapshot(timeout=0) as permit:
        assert_unavailable(BackupSnapshotPermit(barrier).assert_active)
        permit.assert_active()
    assert_unavailable(permit.assert_active)


@pytest.mark.parametrize("case", ["valid", "different-parent", "different-name", "replaced-leaf",
                                 "symlink-leaf", "rebound-parent", "symlink-parent"])
def test_permit_lock_binding_checks_pinned_layout_without_another_flock(
    tmp_path, monkeypatch, case
):
    parent = tmp_path / "state"
    parent.mkdir(mode=0o700)
    path = parent / "backup.lock"
    instance = BackupWriteBarrier(path)
    opened = []
    try:
        with instance.snapshot(timeout=0) as permit:
            requested = path
            if case == "different-parent":
                other = tmp_path / "other"
                other.mkdir(mode=0o700)
                requested = other / path.name
                requested.write_bytes(b"different layout")
                requested.chmod(0o600)
            elif case == "different-name":
                requested = parent / "other.lock"
                requested.write_bytes(b"different leaf")
                requested.chmod(0o600)
            elif case in ("replaced-leaf", "symlink-leaf"):
                old = parent / "old.lock"
                path.rename(old)
                if case == "symlink-leaf":
                    path.symlink_to(old)
                else:
                    path.write_bytes(b"replaced inode")
                    path.chmod(0o600)
            elif case in ("rebound-parent", "symlink-parent"):
                old_parent = tmp_path / "old-state"
                parent.rename(old_parent)
                if case == "symlink-parent":
                    parent.symlink_to(old_parent, target_is_directory=True)
                else:
                    parent.mkdir(mode=0o700)
                    path.write_bytes(b"new parent and new leaf")
                    path.chmod(0o600)
            original_open = coordination.os.open

            def observe_open(*args, **kwargs):
                fd = original_open(*args, **kwargs)
                opened.append(fd)
                return fd

            def forbidden_flock(*_args):
                raise AssertionError("binding validation must not acquire another flock")

            monkeypatch.setattr(coordination.os, "open", observe_open)
            monkeypatch.setattr(coordination.fcntl, "flock", forbidden_flock)
            if case == "valid":
                before = path.stat()
                permit.assert_for_lock(requested)
                assert path.stat() == before
                assert opened
                assert sorted(item.name for item in parent.iterdir()) == [path.name]
            else:
                assert_unavailable(lambda: permit.assert_for_lock(requested))
            for fd in opened:
                with pytest.raises(OSError) as caught:
                    os.fstat(fd)
                assert caught.value.errno == errno.EBADF
        monkeypatch.undo()
    finally:
        instance.close()


@pytest.mark.parametrize("case", ["forged", "expired"])
def test_permit_binding_rejects_invalid_identity_before_any_path_or_lock_inspection(
    barrier, tmp_path, monkeypatch, case
):
    with barrier.snapshot(timeout=0) as permit:
        candidate = BackupSnapshotPermit(barrier) if case == "forged" else permit
    if case == "forged":
        assert candidate is not permit

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid permit must fail before inspecting filesystem state")

    monkeypatch.setattr(barrier, "_check_lock", forbidden)
    monkeypatch.setattr(coordination.os, "open", forbidden)
    assert_unavailable(lambda: candidate.assert_for_lock(tmp_path / "untrusted" / "backup.lock"))


@pytest.mark.parametrize("path", [None, "backup.lock", True, Path("/")])
def test_permit_binding_rejects_invalid_path_arguments_with_fixed_error(barrier, path):
    with barrier.snapshot(timeout=0) as permit:
        assert_unavailable(lambda: permit.assert_for_lock(path))


def test_two_stages_allow_agent_completion_then_drain_agents(barrier):
    work_ready, finish_work = threading.Event(), threading.Event()
    agent_ready, finish_agent = threading.Event(), threading.Event()
    snapshot_ready, finish_snapshot = threading.Event(), threading.Event()

    def work():
        with barrier.operation():
            work_ready.set()
            assert finish_work.wait(5)

    def agent():
        with barrier.operation(kind="agent"):
            assert_busy(lambda: enter_operation(barrier))
            agent_ready.set()
            assert finish_agent.wait(5)

    def snapshot():
        with barrier.snapshot(timeout=3) as permit:
            permit.assert_active()
            snapshot_ready.set()
            assert finish_snapshot.wait(5)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(work)]
        try:
            assert work_ready.wait(3)
            futures.append(pool.submit(snapshot))
            wait_phase(barrier, "work")
            assert_busy(lambda: enter_operation(barrier))
            futures.append(pool.submit(agent))
            assert agent_ready.wait(3)
            assert not snapshot_ready.is_set()
            finish_work.set()
            wait_phase(barrier, "agent")
            assert_busy(lambda: enter_operation(barrier, kind="agent"))
            assert not snapshot_ready.is_set()
            finish_agent.set()
            assert snapshot_ready.wait(3)
            assert_busy(lambda: enter_operation(barrier))
            assert_busy(lambda: enter_operation(barrier, kind="agent"))
            assert_busy(lambda: enter_snapshot(barrier))
            assert_busy(barrier.close)
        finally:
            finish_work.set()
            finish_agent.set()
            finish_snapshot.set()
            for future in futures:
                future.result(timeout=5)
    assert_admission_restored(barrier)


def test_active_work_may_finish_nested_work_and_call_agent_during_first_stage(barrier):
    finish_snapshot = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with barrier.operation() as work:
            def snapshot():
                with barrier.snapshot(timeout=3):
                    assert finish_snapshot.wait(5)

            future = pool.submit(snapshot)
            try:
                wait_phase(barrier, "work")
                with barrier.operation() as joined:
                    assert joined.child_fds == work.child_fds
                with barrier.operation(kind="agent") as agent:
                    assert agent.child_fds != work.child_fds
                    assert_busy(lambda: enter_operation(barrier))
            finally:
                finish_snapshot.set()
        future.result(timeout=5)
    assert_admission_restored(barrier)


def test_new_thread_can_retain_still_live_parent_context_after_work_is_paused(barrier):
    child_ready, release_child = threading.Event(), threading.Event()
    snapshot_ready = threading.Event()

    def snapshot():
        with barrier.snapshot(timeout=3):
            snapshot_ready.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            with barrier.operation() as parent:
                expected = parent.child_fds
                context = contextvars.copy_context()
                snapshot_future = pool.submit(snapshot)
                wait_phase(barrier, "work")

                def joined_child():
                    with barrier.operation() as child:
                        assert child.child_fds == expected
                        child_ready.set()
                        assert release_child.wait(5)

                child_future = pool.submit(context.run, joined_child)
                assert child_ready.wait(3)
            assert not snapshot_ready.is_set()
            assert_unavailable(lambda: parent.child_fds)
            os.fstat(expected[0])
        finally:
            release_child.set()
            child_future.result(timeout=5)
            snapshot_future.result(timeout=5)
    assert snapshot_ready.is_set()
    assert_admission_restored(barrier)


@pytest.mark.parametrize("kind", ["work", "agent"])
def test_drain_timeout_restores_both_admission_stages_without_killing_work(barrier, kind):
    with held_operation(barrier, kind) as (state, _release):
        started = time.monotonic()
        assert_busy(lambda: enter_snapshot(barrier, timeout=0.04))
        assert time.monotonic() - started >= 0.035
        assert state["lease"].child_fds
        assert enter_operation(barrier)
        assert enter_operation(barrier, kind="agent")
    assert_admission_restored(barrier)


def test_one_monotonic_deadline_is_shared_by_both_drains_and_flock(barrier, monkeypatch):
    seen = []
    wait_original, acquire_original = barrier._wait_for_kind, barrier._acquire_exclusive

    def wait(kind, deadline):
        seen.append((kind, deadline))
        return wait_original(kind, deadline)

    def acquire(deadline):
        seen.append(("flock", deadline))
        return acquire_original(deadline)

    monkeypatch.setattr(barrier, "_wait_for_kind", wait)
    monkeypatch.setattr(barrier, "_acquire_exclusive", acquire)
    assert enter_snapshot(barrier, timeout=0.2)
    assert [item[0] for item in seen] == ["work", "agent", "flock"]
    assert len({item[1] for item in seen}) == 1


def test_deadline_consumed_at_work_drain_does_not_grant_agent_another_wait(barrier, monkeypatch):
    phases, waits = [], []
    original_wait_kind, original_wait = barrier._wait_for_kind, barrier._condition.wait

    def wait_kind(kind, deadline):
        phases.append((kind, deadline, time.monotonic()))
        original_wait_kind(kind, deadline)
        if kind == "work":
            # Reproduce descheduling at the exact inter-stage boundary without
            # faking the monotonic clock, real work lease, or held agent lease.
            time.sleep(max(0, deadline - time.monotonic()) + 0.01)

    def wait_condition(timeout):
        waits.append((phases[-1][0], timeout))
        return original_wait(timeout)

    monkeypatch.setattr(barrier, "_wait_for_kind", wait_kind)
    monkeypatch.setattr(barrier._condition, "wait", wait_condition)
    with held_operation(barrier) as (_work, finish_work):
        with held_operation(barrier, "agent"):
            release_timer = threading.Timer(0.01, finish_work.set)
            release_timer.start()
            try:
                assert_busy(lambda: enter_snapshot(barrier, timeout=0.05))
            finally:
                release_timer.cancel()
                release_timer.join(timeout=3)
                assert not release_timer.is_alive()
    assert [kind for kind, _deadline, _started in phases] == ["work"]
    assert time.monotonic() > phases[0][1]
    assert all(kind == "work" for kind, _timeout in waits)
    monkeypatch.undo()
    assert_admission_restored(barrier)


def test_expired_work_deadline_cannot_admit_even_when_no_agent_or_external_lock_remains(
    barrier, monkeypatch
):
    original_wait, original_flock = barrier._wait_for_kind, coordination.fcntl.flock
    attempted_modes = []

    def delay_after_work(kind, deadline):
        original_wait(kind, deadline)
        if kind == "work":
            time.sleep(max(0, deadline - time.monotonic()) + 0.01)

    def record_flock(fd, mode):
        attempted_modes.append(mode)
        return original_flock(fd, mode)

    monkeypatch.setattr(barrier, "_wait_for_kind", delay_after_work)
    monkeypatch.setattr(coordination.fcntl, "flock", record_flock)
    with held_operation(barrier) as (_work, finish_work):
        timer = threading.Timer(0.01, finish_work.set)
        timer.start()
        try:
            assert_busy(lambda: enter_snapshot(barrier, timeout=0.05))
        finally:
            timer.cancel()
            timer.join(timeout=3)
            assert not timer.is_alive()
    assert not any(mode & fcntl.LOCK_EX for mode in attempted_modes)
    monkeypatch.undo()
    assert_admission_restored(barrier)


def test_successful_exclusive_flock_is_closed_if_admission_deadline_has_passed(
    barrier, monkeypatch
):
    original = coordination.fcntl.flock
    acquired = []

    def delay_after_real_exclusive_lock(fd, mode):
        original(fd, mode)
        if mode & fcntl.LOCK_EX:
            acquired.append(fd)
            time.sleep(0.075)

    monkeypatch.setattr(coordination.fcntl, "flock", delay_after_real_exclusive_lock)
    assert_busy(lambda: enter_snapshot(barrier, timeout=0.05))
    assert len(acquired) == 1
    with pytest.raises(OSError) as caught:
        os.fstat(acquired[0])
    assert caught.value.errno == errno.EBADF
    monkeypatch.undo()
    assert_admission_restored(barrier)


def test_zero_timeout_has_one_immediate_try_and_never_waits(barrier, tmp_path, monkeypatch):
    fd = os.open(tmp_path / "backup.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    original = coordination.fcntl.flock
    attempts = []

    def observe(fd, mode):
        if mode & fcntl.LOCK_EX:
            attempts.append(fd)
        return original(fd, mode)

    def forbidden_wait(_seconds):
        raise AssertionError("timeout=0 must never poll or wait")

    monkeypatch.setattr(coordination.fcntl, "flock", observe)
    monkeypatch.setattr(coordination.time, "sleep", forbidden_wait)
    monkeypatch.setattr(barrier._condition, "wait", forbidden_wait)
    try:
        assert_busy(lambda: enter_snapshot(barrier, timeout=0))
        assert len(attempts) == 1
    finally:
        os.close(fd)
    assert enter_snapshot(barrier, timeout=0)
    assert len(attempts) == 2


def test_separate_instances_only_share_kernel_exclusion(tmp_path):
    first = BackupWriteBarrier(tmp_path / "backup.lock")
    second = BackupWriteBarrier(tmp_path / "backup.lock")
    try:
        with first.operation() as lease:
            with second.operation():
                pass
            assert_busy(lambda: enter_snapshot(second, timeout=0.04))
            assert lease.child_fds
            assert enter_operation(second, kind="agent")
        with first.snapshot(timeout=0):
            assert_busy(lambda: enter_operation(second))
            assert_busy(lambda: enter_snapshot(second, timeout=0.04))
        assert_admission_restored(second)
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("error", [RuntimeError("caller error"), KeyboardInterrupt(),
                                 asyncio.CancelledError()])
@pytest.mark.parametrize("scope", ["operation", "snapshot"])
def test_body_exception_is_preserved_and_scoped_locks_are_released(barrier, error, scope):
    with pytest.raises(type(error)) as caught:
        with getattr(barrier, scope)():
            raise error
    assert caught.value is error
    assert_admission_restored(barrier)


@pytest.mark.parametrize("stage", ["work", "agent", "flock"])
def test_internal_interruption_restores_admission(barrier, monkeypatch, stage):
    interruption = KeyboardInterrupt()
    original_wait = barrier._wait_for_kind

    def wait(kind, deadline):
        if kind == stage:
            raise interruption
        return original_wait(kind, deadline)

    def acquire(_deadline):
        raise interruption

    if stage == "flock":
        monkeypatch.setattr(barrier, "_acquire_exclusive", acquire)
    else:
        monkeypatch.setattr(barrier, "_wait_for_kind", wait)
    with pytest.raises(KeyboardInterrupt) as caught:
        enter_snapshot(barrier)
    assert caught.value is interruption
    monkeypatch.undo()
    assert_admission_restored(barrier)


@pytest.mark.parametrize("scope", ["operation", "snapshot"])
def test_kernel_errors_are_fixed_and_do_not_leave_admission_paused(barrier, monkeypatch, scope):
    def fail(_fd, _mode):
        raise OSError(errno.EIO, "SECRET host path and provider diagnostic")

    monkeypatch.setattr(coordination.fcntl, "flock", fail)
    action = enter_operation if scope == "operation" else enter_snapshot
    with pytest.raises(BackupCoordinationError) as caught:
        action(barrier)
    assert str(caught.value) == UNAVAILABLE
    assert caught.value.__suppress_context__
    monkeypatch.undo()
    assert_admission_restored(barrier)


@pytest.mark.parametrize("mutation", ["replace", "delete", "mode", "hardlink", "symlink"])
@pytest.mark.parametrize("scope", ["operation", "snapshot"])
def test_leaf_is_rechecked_against_pinned_inode_before_every_acquisition(
    barrier, tmp_path, mutation, scope
):
    path, old = tmp_path / "backup.lock", tmp_path / "old"
    if mutation in ("replace", "symlink"):
        path.rename(old)
        if mutation == "replace":
            path.write_bytes(b"replacement")
            path.chmod(0o600)
        else:
            path.symlink_to(old)
    elif mutation == "delete":
        path.unlink()
    elif mutation == "hardlink":
        old.hardlink_to(path)
    else:
        path.chmod(0o644)
    action = enter_operation if scope == "operation" else enter_snapshot
    assert_unavailable(lambda: action(barrier))
    assert not barrier._work_paused and not barrier._agent_paused
    assert barrier._snapshot_ticket is None


def test_live_lease_join_and_permit_checks_reject_a_changed_leaf(barrier, tmp_path):
    path = tmp_path / "backup.lock"
    with barrier.operation() as lease:
        path.chmod(0o644)
        assert_unavailable(lambda: lease.child_fds)
        assert_unavailable(lambda: enter_operation(barrier))
        path.chmod(0o600)
        assert lease.child_fds
    with barrier.snapshot(timeout=0) as permit:
        path.chmod(0o644)
        assert_unavailable(permit.assert_active)
        path.chmod(0o600)
        permit.assert_active()
    assert_admission_restored(barrier)


@pytest.mark.parametrize("scope", ["operation", "snapshot"])
def test_post_flock_leaf_recheck_rejects_replacement_during_acquisition(
    barrier, tmp_path, monkeypatch, scope
):
    path, old = tmp_path / "backup.lock", tmp_path / "old"
    original = coordination.fcntl.flock

    def replace_after_real_lock(fd, mode):
        original(fd, mode)
        path.rename(old)
        path.write_bytes(b"different inode")
        path.chmod(0o600)

    monkeypatch.setattr(coordination.fcntl, "flock", replace_after_real_lock)
    action = enter_operation if scope == "operation" else enter_snapshot
    assert_unavailable(lambda: action(barrier))
    monkeypatch.undo()
    path.unlink()
    old.rename(path)
    assert_admission_restored(barrier)


def test_parent_permissions_are_rechecked_without_repair(tmp_path):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    instance = BackupWriteBarrier(parent / "backup.lock")
    try:
        parent.chmod(0o777)
        assert_unavailable(lambda: enter_operation(instance))
        assert_unavailable(lambda: enter_snapshot(instance))
        assert stat.S_IMODE(parent.stat().st_mode) == 0o777
        parent.chmod(0o700)
        assert_admission_restored(instance)
    finally:
        instance.close()


def test_replacement_during_external_lock_wait_fails_closed_and_restores_admission(
    barrier, tmp_path, monkeypatch
):
    path, old = tmp_path / "backup.lock", tmp_path / "old"
    external = os.open(path, os.O_RDWR)
    fcntl.flock(external, fcntl.LOCK_SH | fcntl.LOCK_NB)
    attempted = threading.Event()
    original = coordination.fcntl.flock

    def observe(fd, mode):
        try:
            return original(fd, mode)
        finally:
            if mode & fcntl.LOCK_EX:
                attempted.set()

    monkeypatch.setattr(coordination.fcntl, "flock", observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(enter_snapshot, barrier, timeout=2)
        try:
            assert attempted.wait(3)
            path.rename(old)
            path.write_bytes(b"replacement during wait")
            path.chmod(0o600)
            assert_unavailable(lambda: future.result(timeout=3))
        finally:
            os.close(external)
    monkeypatch.undo()
    path.unlink()
    old.rename(path)
    assert_admission_restored(barrier)


def test_close_refuses_live_references_and_is_idempotent_afterward(barrier):
    with barrier.operation() as lease:
        fd, = lease.child_fds
        assert_busy(barrier.close)
        assert lease.child_fds == (fd,)
    with barrier.snapshot(timeout=0) as permit:
        assert_busy(barrier.close)
        permit.assert_active()
    descriptors = (barrier._anchor_fd, barrier._parent_fd)
    barrier.close()
    barrier.close()
    for fd in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF
    assert_unavailable(lambda: enter_operation(barrier))
    assert_unavailable(lambda: enter_snapshot(barrier))


def test_idle_collection_closes_only_pinned_descriptors_and_keeps_lock_file(tmp_path):
    path = tmp_path / "backup.lock"
    instance = BackupWriteBarrier(path)
    reference, finalizer = weakref.ref(instance), instance._finalizer
    descriptors = (instance._anchor_fd, instance._parent_fd)
    assert finalizer.alive and finalizer.atexit is False
    del instance
    gc.collect()
    assert reference() is None and not finalizer.alive
    for fd in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF
    assert path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("partial_failure", [False, True])
def test_explicit_close_detaches_finalizer_before_fd_reuse_even_on_partial_failure(
    tmp_path, monkeypatch, partial_failure
):
    first_path, second_path = tmp_path / "first", tmp_path / "second"
    first_path.write_bytes(b"first unrelated file")
    second_path.write_bytes(b"second unrelated file")
    instance = BackupWriteBarrier(tmp_path / "backup.lock")
    reference, finalizer = weakref.ref(instance), instance._finalizer
    descriptors = (instance._anchor_fd, instance._parent_fd)
    if partial_failure:
        original_close = coordination.os.close

        def close_then_report_error(fd):
            original_close(fd)
            if fd == descriptors[0]:
                raise OSError(errno.EINTR, "injected partial close diagnostic")

        monkeypatch.setattr(coordination.os, "close", close_then_report_error)
        assert_unavailable(instance.close)
        monkeypatch.undo()
    else:
        instance.close()
    assert not finalizer.alive
    instance.close()
    replacements = [os.open(first_path, os.O_RDONLY), os.open(second_path, os.O_RDONLY)]
    try:
        assert set(replacements) == set(descriptors)
        del instance
        gc.collect()
        assert reference() is None
        assert os.read(replacements[0], 64) == b"first unrelated file"
        assert os.read(replacements[1], 64) == b"second unrelated file"
    finally:
        for fd in replacements:
            os.close(fd)


async def wait_thread_event(event):
    deadline = time.monotonic() + 3
    while not event.is_set():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_run_sync_acquires_in_the_real_thread_and_forwards_arguments(barrier):
    calling_thread = threading.get_ident()

    def calculate(left, *, right):
        assert threading.get_ident() != calling_thread
        with barrier.operation(kind="agent") as lease:
            assert lease.child_fds
        return left + right

    assert await barrier.run_sync(calculate, 4, kind="agent", right=5) == 9
    assert_admission_restored(barrier)


@pytest.mark.asyncio
async def test_cancelled_await_does_not_release_a_still_running_worker_thread(barrier):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    state = {}

    def work():
        with barrier.operation() as nested:
            state["fds"] = nested.child_fds
            entered.set()
            try:
                assert release.wait(5)
            finally:
                finished.set()

    task = asyncio.create_task(barrier.run_sync(work))
    try:
        await wait_thread_event(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not finished.is_set()
        os.fstat(state["fds"][0])
        assert_busy(lambda: enter_snapshot(barrier, timeout=0.04))
        assert_busy(barrier.close)
    finally:
        release.set()
        await wait_thread_event(finished)
        if not task.done():
            await task
    # The worker sets finished immediately before scope cleanup; drain waits for
    # the actual run_sync reference, not merely that user-code event.
    assert enter_snapshot(barrier, timeout=2)
    assert_admission_restored(barrier)


@pytest.mark.asyncio
async def test_cancelled_await_and_dropped_owner_cannot_collect_a_running_thread_lease(tmp_path):
    path = tmp_path / "backup.lock"
    instance = BackupWriteBarrier(path)
    reference = weakref.ref(instance)
    descriptors = (instance._anchor_fd, instance._parent_fd)
    observer = BackupWriteBarrier(path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def work():
        entered.set()
        try:
            assert release.wait(5)
        finally:
            finished.set()

    task = asyncio.create_task(instance.run_sync(work))
    try:
        await wait_thread_event(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        del task
        del instance
        gc.collect()
        assert reference() is not None
        for fd in descriptors:
            os.fstat(fd)
        assert_busy(lambda: enter_snapshot(observer, timeout=0.03))
    finally:
        release.set()
        await wait_thread_event(finished)
    deadline = time.monotonic() + 3
    while reference() is not None:
        gc.collect()
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)
    try:
        for fd in descriptors:
            with pytest.raises(OSError) as caught:
                os.fstat(fd)
            assert caught.value.errno == errno.EBADF
        assert_admission_restored(observer)
    finally:
        observer.close()


@pytest.mark.asyncio
async def test_run_sync_joins_live_context_until_actual_worker_completion(barrier):
    entered, release = threading.Event(), threading.Event()
    with barrier.operation() as outer:
        expected = outer.child_fds

        def work():
            with barrier.operation() as nested:
                assert nested.child_fds == expected
                entered.set()
                assert release.wait(5)
                return "completed"

        task = asyncio.create_task(barrier.run_sync(work))
        await wait_thread_event(entered)
    try:
        assert_unavailable(lambda: outer.child_fds)
        assert_busy(lambda: enter_snapshot(barrier, timeout=0.03))
    finally:
        release.set()
    assert await task == "completed"
    assert_admission_restored(barrier)


@pytest.mark.asyncio
async def test_new_task_with_expired_context_cannot_start_work_behind_a_pause(barrier):
    begin = asyncio.Event()
    called = threading.Event()

    async def later():
        await begin.wait()
        return await barrier.run_sync(called.set)

    with barrier.operation():
        task = asyncio.create_task(later())
    with barrier.snapshot(timeout=0):
        begin.set()
        with pytest.raises(BackupBusyError):
            await task
        assert not called.is_set()
    assert_admission_restored(barrier)


@pytest.mark.asyncio
async def test_run_sync_user_error_propagates_after_its_thread_releases(barrier):
    error = RuntimeError("caller-controlled diagnostic")

    def work():
        raise error

    with pytest.raises(RuntimeError) as caught:
        await barrier.run_sync(work)
    assert caught.value is error
    assert_admission_restored(barrier)


_FD_HOLDER = """
import os
import sys
fd = int(sys.argv[1])
os.fstat(fd)
os.write(1, b'ready\\n')
assert os.read(0, 1) == b'x'
os.close(fd)
"""


def test_real_exec_child_retains_shared_lock_after_parent_lease_exit(barrier):
    child = None
    try:
        with barrier.operation() as lease:
            fd, = lease.child_fds
            child = subprocess.Popen(
                [sys.executable, "-I", "-S", "-B", "-c", _FD_HOLDER, str(fd)],
                pass_fds=lease.child_fds, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env={},
            )
            assert select.select([child.stdout], [], [], 3)[0]
            assert child.stdout.readline() == b"ready\n"
        assert child.poll() is None
        assert_busy(lambda: enter_snapshot(barrier, timeout=0.06))
        stdout, stderr = child.communicate(b"x", timeout=5)
        assert child.returncode == 0 and stdout == stderr == b""
        assert_admission_restored(barrier)
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


_PARENT_EXIT_SUPERVISOR = r'''
import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from open_node.services.backup_coordination import BackupBusyError, BackupWriteBarrier

# Keep orphan handling inside this disposable process, not the pytest runner.
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
holder = """
import os, sys
lock_fd, control_fd, ready_fd = map(int, sys.argv[1:])
os.fstat(lock_fd)
os.write(ready_fd, (str(os.getpid()) + '\\n').encode())
os.close(ready_fd)
assert os.read(control_fd, 1) == b'x'
os.close(control_fd)
os.close(lock_fd)
"""
parent_code = """
import os, subprocess, sys
from pathlib import Path
from open_node.services.backup_coordination import BackupWriteBarrier
path, control_fd, ready_fd = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
barrier = BackupWriteBarrier(Path(path))
with barrier.operation() as lease:
    subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', sys.argv[4],
                      str(lease.child_fds[0]), str(control_fd), str(ready_fd)],
                     pass_fds=(*lease.child_fds, control_fd, ready_fd),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env={})
    os._exit(0)
"""
control_r, control_w = os.pipe()
ready_r, ready_w = os.pipe()
parent = child_pid = None
barrier = BackupWriteBarrier(Path(sys.argv[1]))
try:
    parent = subprocess.Popen([sys.executable, '-B', '-c', parent_code, sys.argv[1],
                               str(control_r), str(ready_w), holder],
                              pass_fds=(control_r, ready_w), env=dict(os.environ),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    os.close(control_r)
    control_r = None
    os.close(ready_w)
    ready_w = None
    assert select.select([ready_r], [], [], 5)[0]
    child_pid = int(os.read(ready_r, 64))
    stdout, stderr = parent.communicate(timeout=5)
    assert parent.returncode == 0 and stdout == stderr == b''
    os.kill(child_pid, 0)
    try:
        with barrier.snapshot(timeout=0.06):
            raise AssertionError('surviving child lost its shared lock')
    except BackupBusyError:
        pass
    os.write(control_w, b'x')
    deadline = time.monotonic() + 5
    while True:
        reaped, status = os.waitpid(child_pid, os.WNOHANG)
        if reaped:
            assert reaped == child_pid and status == 0
            child_pid = None
            break
        assert time.monotonic() < deadline
        time.sleep(0.01)
    with barrier.snapshot(timeout=1) as permit:
        permit.assert_active()
    print(json.dumps({'parent_exit': 0, 'inherited_lock_blocked': True,
                      'orphan_reaped': True, 'snapshot_after_child_exit': True}))
finally:
    for fd in (control_r, control_w, ready_r, ready_w):
        if fd is not None:
            os.close(fd)
    if child_pid is not None:
        os.kill(child_pid, signal.SIGKILL)
        os.waitpid(child_pid, 0)
    if parent is not None and parent.poll() is None:
        parent.kill()
        parent.communicate(timeout=5)
    barrier.close()
'''


def test_actual_parent_process_exit_does_not_unlock_surviving_exec_child(tmp_path):
    assert run_child(_PARENT_EXIT_SUPERVISOR, tmp_path / "backup.lock") == {
        "parent_exit": 0,
        "inherited_lock_blocked": True,
        "orphan_reaped": True,
        "snapshot_after_child_exit": True,
    }


def test_pid_binding_fails_before_touching_a_mutex_inherited_locked(barrier, tmp_path):
    other = BackupWriteBarrier(tmp_path / "snapshot.lock")
    locked, release = threading.Event(), threading.Event()
    read_fd, write_fd = os.pipe()
    child_pid = None

    def hold_mutex():
        with barrier._condition:
            locked.set()
            assert release.wait(5)

    try:
        with barrier.operation() as lease, other.snapshot(timeout=0) as permit:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(hold_mutex)
                assert locked.wait(3)
                child_pid = os.fork()
                if child_pid == 0:
                    try:
                        os.close(read_fd)
                        actions = [
                            lambda: enter_operation(barrier),
                            lambda: enter_snapshot(barrier),
                            lambda: asyncio.run(barrier.run_sync(lambda: None)),
                            barrier.close,
                            lambda: lease.child_fds,
                            permit.assert_active,
                            lambda: permit.assert_for_lock(tmp_path / "snapshot.lock"),
                        ]
                        for action in actions:
                            assert_unavailable(action)
                        os.write(write_fd, b"all-seven-rejected")
                        os._exit(0)
                    except BaseException:
                        os._exit(91)
                try:
                    os.close(write_fd)
                    write_fd = None
                    assert select.select([read_fd], [], [], 3)[0]
                    assert os.read(read_fd, 64) == b"all-seven-rejected"
                    waited, status = os.waitpid(child_pid, 0)
                    assert waited == child_pid and status == 0
                    child_pid = None
                finally:
                    release.set()
                    future.result(timeout=5)
            assert lease.child_fds
            permit.assert_active()
    finally:
        release.set()
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        if child_pid is not None:
            os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
        other.close()
    assert_admission_restored(barrier)


def test_fork_child_gc_does_not_close_parent_pid_descriptors_or_take_old_mutex(tmp_path):
    instance = BackupWriteBarrier(tmp_path / "backup.lock")
    reference = weakref.ref(instance)
    descriptors = (instance._anchor_fd, instance._parent_fd)
    mutex = instance._condition
    locked, release = threading.Event(), threading.Event()
    read_fd, write_fd = os.pipe()
    child_pid = None

    def hold_mutex(lock):
        # Capture only the mutex, never the instance: the fork child must be able
        # to collect its copied instance while this inherited lock remains held.
        with lock:
            locked.set()
            assert release.wait(5)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_mutex, mutex)
            assert locked.wait(3)
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    os.close(read_fd)
                    del instance
                    gc.collect()
                    assert reference() is None
                    for fd in descriptors:
                        os.fstat(fd)
                    os.write(write_fd, b"parent-fds-kept")
                    os._exit(0)
                except BaseException:
                    os._exit(92)
            try:
                os.close(write_fd)
                write_fd = None
                assert select.select([read_fd], [], [], 3)[0]
                assert os.read(read_fd, 64) == b"parent-fds-kept"
                waited, status = os.waitpid(child_pid, 0)
                assert waited == child_pid and status == 0
                child_pid = None
            finally:
                release.set()
                future.result(timeout=5)
        assert_admission_restored(instance)
    finally:
        release.set()
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        if child_pid is not None:
            os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
        instance.close()
