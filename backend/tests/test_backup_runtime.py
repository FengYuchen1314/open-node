"""Independent runtime-hook contracts, without importing or exercising the app."""

import asyncio
import contextvars
import inspect
import json
import os
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends
from open_node.services import backup_runtime as runtime
from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupWriteBarrier,
)
from starlette.concurrency import run_in_threadpool

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
UNAVAILABLE = "Backup coordination is unavailable."


@pytest.fixture
def barrier(tmp_path):
    instance = BackupWriteBarrier(tmp_path / "backup.lock")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture(autouse=True)
def no_leaked_runtime_scope():
    assert runtime._CURRENT_OPERATION.get() is None
    yield
    assert runtime._CURRENT_OPERATION.get() is None


def assert_unavailable(action):
    with pytest.raises(BackupCoordinationError) as caught:
        action()
    assert type(caught.value) is BackupCoordinationError
    assert str(caught.value) == UNAVAILABLE


def snapshot(barrier, *, timeout=0):
    with barrier.snapshot(timeout=timeout) as permit:
        permit.assert_active()


async def wait_event(event):
    deadline = time.monotonic() + 3
    while not event.is_set():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)


async def offload_protected_sync(function, /, *args, **kwargs):
    # This is the real Starlette pool path used for a synchronous callback,
    # not either runtime offload helper and not a fake executor.
    return await run_in_threadpool(partial(runtime.protected_sync(function), *args, **kwargs))


OFFLOADERS = [
    runtime.run_in_backup_thread, runtime.run_in_backup_threadpool, offload_protected_sync
]
OFFLOADER_IDS = ["asyncio-helper", "anyio-helper", "protected-sync-callback"]


def test_three_nested_scopes_restore_the_exact_outer_operation_and_live_fd(barrier, tmp_path):
    other = BackupWriteBarrier(tmp_path / "other.lock")
    try:
        with runtime.backup_operation(barrier) as outer:
            original = runtime._CURRENT_OPERATION.get()
            assert original.barrier is barrier and original.kind == "work"
            assert runtime.current_backup_child_fds() == outer.child_fds
            with runtime.backup_operation(barrier, kind="agent") as middle:
                agent = runtime._CURRENT_OPERATION.get()
                assert agent.kind == "agent" and agent.lease is middle
                assert runtime.current_backup_child_fds() != outer.child_fds
                with runtime.backup_operation(other) as inner:
                    current = runtime._CURRENT_OPERATION.get()
                    assert current.barrier is other and current.lease is inner
                    assert runtime.current_backup_child_fds() == inner.child_fds
                assert runtime._CURRENT_OPERATION.get() is agent
                assert runtime.current_backup_child_fds() == middle.child_fds
            assert runtime._CURRENT_OPERATION.get() is original
            assert runtime.current_backup_child_fds() == outer.child_fds
        assert_unavailable(runtime.current_backup_child_fds)
        assert_unavailable(lambda: outer.child_fds)
        assert_unavailable(lambda: middle.child_fds)
        assert_unavailable(lambda: inner.child_fds)
        snapshot(barrier)
        snapshot(other)
    finally:
        other.close()


@pytest.mark.parametrize("error", [RuntimeError("caller error"), KeyboardInterrupt(),
                                 asyncio.CancelledError()])
def test_exception_restores_outer_runtime_scope_and_releases_inner_reference(barrier, error):
    with runtime.backup_operation(barrier) as outer:
        original = runtime._CURRENT_OPERATION.get()
        with pytest.raises(type(error)) as caught:
            with runtime.backup_operation(barrier):
                raise error
        assert caught.value is error
        assert runtime._CURRENT_OPERATION.get() is original
        assert runtime.current_backup_child_fds() == outer.child_fds
    snapshot(barrier)


def test_rejected_nested_scope_does_not_overwrite_outer_context(barrier):
    with runtime.backup_operation(barrier) as lease:
        original = runtime._CURRENT_OPERATION.get()
        with pytest.raises(BackupCoordinationError):
            with runtime.backup_operation(barrier, kind="invalid"):
                pytest.fail("invalid kind was admitted")
        assert runtime._CURRENT_OPERATION.get() is original
        assert runtime.current_backup_child_fds() == lease.child_fds


def test_missing_scope_rejects_fd_access_and_a_protected_callback_before_user_code():
    calls = []

    @runtime.protected_sync
    def callback():
        calls.append("called")

    assert_unavailable(runtime.current_backup_child_fds)
    assert_unavailable(callback)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("offload", OFFLOADERS, ids=OFFLOADER_IDS)
async def test_missing_scope_rejects_thread_callbacks_before_user_code(offload):
    called = threading.Event()
    with pytest.raises(BackupCoordinationError) as caught:
        await offload(called.set)
    assert str(caught.value) == UNAVAILABLE
    assert not called.is_set()


def test_protected_nested_callback_takes_its_own_reference_and_restores_runtime_scope(barrier):
    with runtime.backup_operation(barrier) as original_lease:
        original = runtime._CURRENT_OPERATION.get()

        @runtime.protected_sync
        def callback():
            current = runtime._CURRENT_OPERATION.get()
            assert current is not original and current.lease is not original_lease
            assert current.barrier is barrier and current.kind == "work"
            assert runtime.current_backup_child_fds() == original_lease.child_fds
            return "value"

        assert callback() == "value"
        assert runtime._CURRENT_OPERATION.get() is original
    snapshot(barrier)


def test_stale_context_cannot_use_old_fd_but_may_seek_fresh_admission_while_open(barrier):
    with runtime.backup_operation(barrier) as old_lease:
        old_context = contextvars.copy_context()
    assert_unavailable(lambda: old_context.run(runtime.current_backup_child_fds))
    calls = []

    @runtime.protected_sync
    def callback():
        lease = runtime._CURRENT_OPERATION.get().lease
        assert lease is not old_lease
        os.fstat(runtime.current_backup_child_fds()[0])
        calls.append(True)

    old_context.run(callback)
    assert calls == [True]
    assert_unavailable(lambda: old_context.run(runtime.current_backup_child_fds))
    with barrier.snapshot(timeout=0):
        with pytest.raises(BackupBusyError):
            old_context.run(callback)
    assert calls == [True]
    barrier.close()
    assert_unavailable(lambda: old_context.run(callback))


@pytest.mark.asyncio
@pytest.mark.parametrize("offload", OFFLOADERS, ids=OFFLOADER_IDS)
async def test_async_task_with_stale_scope_cannot_start_a_writer_behind_pause(barrier, offload):
    with runtime.backup_operation(barrier):
        old_context = contextvars.copy_context()
    called = threading.Event()
    with barrier.snapshot(timeout=0):
        task = old_context.run(asyncio.create_task, offload(called.set))
        with pytest.raises(BackupBusyError):
            await task
    assert not called.is_set()
    task = old_context.run(asyncio.create_task, offload(called.set))
    await task
    assert called.is_set()
    assert_unavailable(runtime.current_backup_child_fds)


def dependency():
    return "dependency"


class SignaturePayload:
    pass


ANNOTATED_DEPENDENCY = Depends(dependency)
ANNOTATED_VALUE = Annotated[str, ANNOTATED_DEPENDENCY]


def test_protected_sync_preserves_evaluated_signature_depends_identity_and_metadata(barrier):
    marker = Depends(dependency)

    def callback(
        value: "SignaturePayload", /, dependency_value: str = marker,
        *, annotated: "ANNOTATED_VALUE" = "default",
    ) -> "SignaturePayload":
        """Original callback documentation."""
        return value

    callback.application_metadata = {"important": True}
    original = inspect.signature(callback, eval_str=True)
    protected = runtime.protected_sync(callback)
    signature = inspect.signature(protected)
    assert signature == original
    assert signature.parameters["value"].annotation is SignaturePayload
    assert signature.parameters["value"].kind is inspect.Parameter.POSITIONAL_ONLY
    assert signature.parameters["dependency_value"].default is marker
    assert signature.parameters["annotated"].annotation is ANNOTATED_VALUE
    assert signature.return_annotation is SignaturePayload
    assert protected.__name__ == callback.__name__
    assert protected.__qualname__ == callback.__qualname__
    assert protected.__doc__ == callback.__doc__
    assert protected.__wrapped__ is callback
    assert protected.application_metadata is callback.application_metadata
    assert getattr(protected, runtime.PROTECTED_SYNC_ATTRIBUTE) is True
    assert runtime.protected_sync(protected) is protected
    with runtime.backup_operation(barrier):
        value = SignaturePayload()
        assert protected(value) is value


async def async_callback():
    return None


def generator_callback():
    yield None


async def async_generator_callback():
    yield None


class AsyncCallable:
    async def __call__(self):
        return None


class GeneratorCallable:
    def __call__(self):
        yield None


class AsyncGeneratorCallable:
    async def __call__(self):
        yield None


@pytest.mark.parametrize(
    "callback",
    [None, 5, async_callback, generator_callback, async_generator_callback,
     AsyncCallable(), GeneratorCallable(), AsyncGeneratorCallable(), dict],
    ids=["none", "noncallable", "async-function", "generator-function", "async-generator-function",
         "async-callable", "generator-callable", "async-generator-callable", "no-signature"],
)
def test_protected_sync_rejects_deferred_or_uninspectable_callbacks(callback):
    assert_unavailable(lambda: runtime.protected_sync(callback))


def test_unresolved_annotations_have_only_the_fixed_error():
    def callback(value):
        return value

    callback.__annotations__ = {"value": "Unresolved_PRIVATE_annotation"}
    with pytest.raises(BackupCoordinationError) as caught:
        runtime.protected_sync(callback)
    assert str(caught.value) == UNAVAILABLE and caught.value.__suppress_context__


def test_callable_with_inaccessible_call_attribute_is_rejected_with_fixed_error():
    class Callable:
        def __getattribute__(self, name):
            if name == "__call__":
                raise AttributeError("PRIVATE callback diagnostic")
            return object.__getattribute__(self, name)

        def __call__(self):
            pytest.fail("callback body must never run")

    callback = Callable()
    assert callable(callback)
    with pytest.raises(BackupCoordinationError) as caught:
        runtime.protected_sync(callback)
    assert str(caught.value) == UNAVAILABLE and caught.value.__suppress_context__


def test_sync_callable_instance_can_be_wrapped_without_changing_its_signature(barrier):
    class Callable:
        def __call__(self, value: int, *, label: str = "x") -> tuple[int, str]:
            return value, label

    callback = Callable()
    protected = runtime.protected_sync(callback)
    assert inspect.signature(protected) == inspect.signature(callback)
    with runtime.backup_operation(barrier):
        assert protected(3, label="literal") == (3, "literal")


@pytest.mark.asyncio
@pytest.mark.parametrize("offload", OFFLOADERS, ids=OFFLOADER_IDS)
async def test_real_thread_helpers_preserve_all_original_args_and_reserved_looking_kwargs(
    barrier, offload
):
    main_thread = threading.get_ident()
    arguments = ("first", "second", 3)
    keywords = {
        "func": "user-func", "function": "user-function", "kind": "user-kind",
        "cancellable": "user-cancellable", "abandon_on_cancel": "user-abandon",
        "limiter": "user-limiter", "self": "user-self", "args": "user-args",
        "kwargs": "user-kwargs", "timeout": "user-timeout",
    }

    def callback(*args, **kwargs):
        assert threading.get_ident() != main_thread
        assert runtime._CURRENT_OPERATION.get().kind == "agent"
        assert runtime.current_backup_child_fds()
        return args, kwargs

    with runtime.backup_operation(barrier, kind="agent"):
        assert await offload(callback, *arguments, **keywords) == (arguments, keywords)
    snapshot(barrier)


@pytest.mark.asyncio
@pytest.mark.parametrize("offload", OFFLOADERS, ids=OFFLOADER_IDS)
async def test_raw_task_cancel_keeps_the_actual_thread_writer_covered_until_it_finishes(
    barrier, tmp_path, offload
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    marker = tmp_path / "completed-thread-write"
    state = {}

    def writer():
        state["fds"] = runtime.current_backup_child_fds()
        state["thread"] = threading.get_ident()
        entered.set()
        try:
            assert release.wait(5)
            os.fstat(runtime.current_backup_child_fds()[0])
            marker.write_bytes(b"completed after raw task cancellation")
        finally:
            finished.set()

    async def request():
        with runtime.backup_operation(barrier):
            await offload(writer)

    task = asyncio.create_task(request())
    try:
        await wait_event(entered)
        assert state["thread"] != threading.get_ident()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not marker.exists() and not finished.is_set()
        assert_unavailable(runtime.current_backup_child_fds)
        os.fstat(state["fds"][0])
        with pytest.raises(BackupBusyError):
            snapshot(barrier, timeout=0.04)
    finally:
        release.set()
        await wait_event(finished)
        if not task.done():
            await task
    snapshot(barrier, timeout=2)
    assert marker.read_bytes() == b"completed after raw task cancellation"


@pytest.mark.asyncio
@pytest.mark.parametrize("offload", OFFLOADERS, ids=OFFLOADER_IDS)
async def test_live_parent_can_start_thread_after_admission_pauses_without_deadlock(
    barrier, offload
):
    snapshot_done = threading.Event()

    def take_snapshot():
        snapshot(barrier, timeout=3)
        snapshot_done.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with runtime.backup_operation(barrier) as parent:
            future = pool.submit(take_snapshot)
            with barrier._condition:
                assert barrier._condition.wait_for(lambda: barrier._work_paused, timeout=3)

            def child():
                assert runtime.current_backup_child_fds() == parent.child_fds
                assert not snapshot_done.is_set()
                return "joined while parent active"

            assert await offload(child) == "joined while parent active"
        future.result(timeout=5)
    assert snapshot_done.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("offload", OFFLOADERS, ids=OFFLOADER_IDS)
async def test_thread_callback_exception_restores_context_and_releases_its_reference(
    barrier, offload
):
    error = RuntimeError("user callback error")

    def callback():
        assert runtime.current_backup_child_fds()
        raise error

    with runtime.backup_operation(barrier):
        original = runtime._CURRENT_OPERATION.get()
        with pytest.raises(RuntimeError) as caught:
            await offload(callback)
        assert caught.value is error
        assert runtime._CURRENT_OPERATION.get() is original
    assert_unavailable(runtime.current_backup_child_fds)
    snapshot(barrier)


@pytest.mark.parametrize("driver", ["sqlite", "sqlite+pysqlite"])
def test_same_sqlite_parent_shares_one_private_inode_without_initializing_databases(
    tmp_path, driver
):
    parent = tmp_path / "new-state"
    first_db, second_db = parent / "first.db", parent / "second.db"
    first = runtime.configured_backup_barrier(f"{driver}:///{first_db}")
    second = runtime.configured_backup_barrier(f"{driver}:///{second_db}")
    try:
        assert not first_db.exists() and not second_db.exists()
        assert sorted(item.name for item in parent.iterdir()) == [runtime.BACKUP_LOCK_NAME]
        lock = parent / runtime.BACKUP_LOCK_NAME
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600 and lock.stat().st_size == 0
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
        with runtime.backup_operation(first) as one, runtime.backup_operation(second) as two:
            left, right = os.fstat(one.child_fds[0]), os.fstat(two.child_fds[0])
            assert (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)
        with runtime.backup_operation(first):
            with pytest.raises(BackupBusyError):
                snapshot(second, timeout=0.02)
        snapshot(second)
        assert not first_db.exists() and not second_db.exists()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("parent_name", ["relative", "$BACKUP_TEST_STATE", "~"])
def test_relative_sqlite_filenames_do_not_expand_environment_or_home(
    tmp_path, monkeypatch, parent_name
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BACKUP_TEST_STATE", str(tmp_path / "must-not-expand"))
    instance = runtime.configured_backup_barrier(f"sqlite:///{parent_name}/database.db")
    try:
        snapshot(instance)
        assert (tmp_path / parent_name / runtime.BACKUP_LOCK_NAME).is_file()
        assert not (tmp_path / parent_name / "database.db").exists()
        assert not (tmp_path / "must-not-expand").exists()
    finally:
        instance.close()


@pytest.mark.parametrize(
    "url",
    ["sqlite://", "sqlite:///:memory:", "sqlite+pysqlite:///:memory:",
     "sqlite:///file::memory:?cache=shared", "sqlite:///file:example.db?mode=ro&uri=true",
     "sqlite:///example.db?uri=false", "sqlite+aiosqlite:///example.db",
     "postgresql://synthetic:synthetic@127.0.0.1/database", "mysql:///database"],
    ids=["empty", "memory", "pysqlite-memory", "file-memory", "file-uri", "uri-option",
         "unsupported-sqlite-driver", "postgresql", "mysql"],
)
def test_memory_non_sqlite_and_file_uri_configs_never_issue_snapshot_or_create_files(
    tmp_path, monkeypatch, url
):
    monkeypatch.chdir(tmp_path)
    instance = runtime.configured_backup_barrier(url)
    try:
        with runtime.backup_operation(instance):
            assert runtime.current_backup_child_fds() == ()
        assert_unavailable(lambda: snapshot(instance))
        assert list(tmp_path.iterdir()) == []
    finally:
        instance.close()


@pytest.mark.parametrize("url", [None, 12, "not-a-database-url", "sqlite:///bad\x00/db"])
def test_invalid_configuration_errors_are_fixed_and_do_not_fall_back_to_unlocked(
    tmp_path, monkeypatch, url
):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BackupCoordinationError) as caught:
        runtime.configured_backup_barrier(url)
    assert str(caught.value) == UNAVAILABLE and caught.value.__suppress_context__
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "hardlink", "directory"])
def test_unsafe_existing_lock_does_not_silently_downgrade_or_change_existing_content(
    tmp_path, unsafe
):
    lock, target = tmp_path / runtime.BACKUP_LOCK_NAME, tmp_path / "existing"
    target.write_bytes(b"do not alter")
    target.chmod(0o600)
    if unsafe == "mode":
        lock.write_bytes(b"bad permissions remain")
        lock.chmod(0o644)
    elif unsafe == "symlink":
        lock.symlink_to(target)
    elif unsafe == "hardlink":
        lock.hardlink_to(target)
    else:
        lock.mkdir(mode=0o700)
    before = lock.lstat()
    assert_unavailable(lambda: runtime.configured_backup_barrier(f"sqlite:///{tmp_path / 'db'}"))
    assert lock.lstat() == before
    assert target.read_bytes() == b"do not alter"
    assert not (tmp_path / "db").exists()


@pytest.mark.parametrize("unsafe", ["writable", "symlink", "not-directory"])
def test_unsafe_existing_parent_is_not_repaired_or_downgraded(tmp_path, unsafe):
    parent = tmp_path / "parent"
    if unsafe == "writable":
        parent.mkdir()
        parent.chmod(0o777)
    elif unsafe == "symlink":
        parent.symlink_to(tmp_path, target_is_directory=True)
    else:
        parent.write_bytes(b"not a directory")
    before = parent.lstat()
    assert_unavailable(lambda: runtime.configured_backup_barrier(f"sqlite:///{parent / 'db'}"))
    assert parent.lstat() == before


def test_import_and_configuration_do_not_import_main_or_initialize_sqlite(tmp_path):
    script = r"""
import json
import sqlite3
import sys
from pathlib import Path

def forbidden_connection(*args, **kwargs):
    raise AssertionError('configuration must never initialize SQLite')

sqlite3.connect = forbidden_connection
from open_node.services.backup_runtime import configured_backup_barrier, BACKUP_LOCK_NAME
root = Path(sys.argv[1])
database = root / 'database.db'
barrier = configured_backup_barrier('sqlite:///' + str(database))
try:
    assert 'open_node.main' not in sys.modules
    assert 'open_node.services.inventory' not in sys.modules
    assert not database.exists()
    assert sorted(path.name for path in root.iterdir()) == [BACKUP_LOCK_NAME]
    with barrier.snapshot(timeout=0) as permit:
        permit.assert_active()
    print(json.dumps({'main_imported': False, 'database_initialized': False,
                      'only_private_lock_created': True}))
finally:
    barrier.close()
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        env={"PYTHONPATH": str(APP_ROOT), "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONNOUSERSITE": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "main_imported": False,
        "database_initialized": False,
        "only_private_lock_created": True,
    }
