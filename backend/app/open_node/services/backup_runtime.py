"""Bind application operations and their real worker threads to a backup barrier.

This module supplies runtime hooks, not a snapshot or restore implementation.
An HTTP request, background cycle, or Agent message establishes an operation;
every offloaded writer then takes its own reference inside the executing thread.
Copied ContextVars do not by themselves retain a lease. Missing runtime context
fails closed instead of silently running an unregistered writer.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial, wraps
from pathlib import Path
from typing import ParamSpec, TypeVar

from sqlalchemy.engine import make_url
from starlette.concurrency import run_in_threadpool

from open_node.services.backup_coordination import (
    BackupCoordinationError,
    BackupWriteBarrier,
    BackupWriteLease,
    OperationKind,
)

_P = ParamSpec("_P")
_R = TypeVar("_R")
BACKUP_LOCK_NAME = ".open-node-backup.lock"
PROTECTED_SYNC_ATTRIBUTE = "__open_node_backup_protected_sync__"


@dataclass(frozen=True, slots=True)
class _RuntimeOperation:
    barrier: BackupWriteBarrier
    kind: OperationKind
    lease: BackupWriteLease


_CURRENT_OPERATION: ContextVar[_RuntimeOperation | None] = ContextVar(
    "open_node_backup_runtime_operation", default=None
)


def configured_backup_barrier(database_url: str) -> BackupWriteBarrier:
    """Use the same private lock for application startup and the local admin CLI.

    Only ordinary local SQLite files have a supported snapshot lock layout.
    Other database configurations retain process-local operation accounting but
    cannot issue a snapshot permit. Unsafe existing lock state is never silently
    downgraded to an unlocked configuration.
    """
    try:
        url = make_url(database_url)
        database = url.database
        if (
            url.drivername not in {"sqlite", "sqlite+pysqlite"}
            or not database
            or database == ":memory:"
            or database.startswith("file:")
            or "uri" in url.query
        ):
            return BackupWriteBarrier(None)
        # Match SQLite's ordinary filename semantics; do not interpret a file
        # URI, expand an environment variable, or import the application.
        parent = Path(database).absolute().parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return BackupWriteBarrier(parent / BACKUP_LOCK_NAME)
    except Exception:
        raise BackupCoordinationError() from None


@contextmanager
def backup_operation(
    barrier: BackupWriteBarrier, *, kind: OperationKind = "work"
) -> Iterator[BackupWriteLease]:
    """Establish an operation without blocking admission on the event loop."""
    with barrier.operation(kind=kind) as lease:
        token = _CURRENT_OPERATION.set(_RuntimeOperation(barrier, kind, lease))
        try:
            yield lease
        finally:
            _CURRENT_OPERATION.reset(token)


def _current_operation() -> _RuntimeOperation:
    operation = _CURRENT_OPERATION.get()
    if operation is None:
        raise BackupCoordinationError()
    return operation


def current_backup_child_fds() -> tuple[int, ...]:
    """Return only the current active lease's explicitly inheritable handles.

    Callers must pass them to subprocess pass_fds. Neither callers nor child
    processes may explicitly unlock them; close releases a reference, while an
    explicit flock LOCK_UN would also unlock surviving inherited references.
    """
    return _current_operation().lease.child_fds


def _protected_call(
    function: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs
) -> _R:
    operation = _current_operation()
    with backup_operation(operation.barrier, kind=operation.kind):
        return function(*args, **kwargs)


def protected_sync(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Protect a sync endpoint/dependency at its actual thread entry point.

    This is intentionally not an async handler wrapper. The evaluated original
    signature preserves FastAPI injection, including postponed annotations and
    Depends identities, without rewriting its private dependency graph.
    """
    if getattr(function, PROTECTED_SYNC_ATTRIBUTE, False):
        return function
    if not callable(function):
        raise BackupCoordinationError()
    try:
        call = function.__call__
    except (AttributeError, TypeError):
        raise BackupCoordinationError() from None
    if (
        inspect.iscoroutinefunction(function)
        or inspect.iscoroutinefunction(call)
        or inspect.isgeneratorfunction(function)
        or inspect.isgeneratorfunction(call)
        or inspect.isasyncgenfunction(function)
        or inspect.isasyncgenfunction(call)
    ):
        raise BackupCoordinationError()
    try:
        signature = inspect.signature(function, eval_str=True)
    except (TypeError, ValueError, NameError):
        raise BackupCoordinationError() from None

    @wraps(function)
    def invoke(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        return _protected_call(function, *args, **kwargs)

    invoke.__signature__ = signature
    setattr(invoke, PROTECTED_SYNC_ATTRIBUTE, True)
    return invoke


async def run_in_backup_thread(
    function: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs
) -> _R:
    """Keep asyncio's executor while retaining inside its actual worker."""
    _current_operation()
    return await asyncio.to_thread(_protected_call, function, *args, **kwargs)


async def run_in_backup_threadpool(
    function: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs
) -> _R:
    """Keep Starlette/AnyIO's pool and limiter, including for raw task cancel."""
    _current_operation()
    return await run_in_threadpool(partial(_protected_call, function, *args, **kwargs))
