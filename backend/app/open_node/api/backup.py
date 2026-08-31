"""HTTP and Agent admission boundaries for the cooperating backup write barrier."""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from fastapi.routing import APIRoute
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupWriteBarrier,
    BackupWriteLease,
    OperationKind,
)
from open_node.services.backup_runtime import backup_operation, protected_sync

AGENT_BACKUP_WAIT_SECONDS = 60.0
AGENT_BACKUP_POLL_SECONDS = 0.1
_AGENT_POST_PATH = re.compile(
    r"/agents/(?:register|heartbeat|traffic|telemetry|commands/lease|scan|"
    r"commands/[^/]+/result|commands/by-request/[^/]+/result)"
)


def _unavailable_response(error: BackupCoordinationError) -> JSONResponse:
    busy = isinstance(error, BackupBusyError)
    return JSONResponse(
        status_code=503,
        content={
            "code": "backup_busy" if busy else "backup_coordination_unavailable",
            "detail": (
                "系统正在进行备份停写，请稍后重试。"
                if busy
                else "备份停写协调暂不可用，请稍后重试。"
            ),
            "license_required": False,
        },
        headers={"Retry-After": "1", "Cache-Control": "no-store"},
    )


class BackupAPIRoute(APIRoute):
    """Wrap a sync endpoint before FastAPI captures its dependency signature."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        call = endpoint if inspect.isfunction(endpoint) else endpoint.__call__
        if not (inspect.iscoroutinefunction(endpoint) or inspect.iscoroutinefunction(call)):
            endpoint = protected_sync(endpoint)
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except BackupCoordinationError as exc:
                return _unavailable_response(exc)

        return handle


def _route_path(scope: Scope) -> str:
    """Match ASGI root_path semantics without unquoting or collapsing separators."""
    path = scope.get("path", "")
    root_path = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        if path == root_path:
            path = ""
        elif path[len(root_path) : len(root_path) + 1] == "/":
            path = path[len(root_path) :]
    return path.rstrip("/") or "/"


class BackupHTTPMiddleware:
    """Hold admission through response bodies, background work and request cleanup."""

    def __init__(self, app: ASGIApp, *, barrier: BackupWriteBarrier, api_prefix: str) -> None:
        self.app = app
        self.barrier = barrier
        self.api_prefix = api_prefix.rstrip("/")

    def _kind(self, scope: Scope, path: str) -> OperationKind:
        if scope.get("method") == "POST" and path.startswith(self.api_prefix + "/"):
            relative = path[len(self.api_prefix) :]
            if _AGENT_POST_PATH.fullmatch(relative):
                return "agent"
        return "work"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = _route_path(scope)
        if path == "/healthz" and scope.get("method") in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        started = False

        async def observed_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            if not isinstance(self.barrier, BackupWriteBarrier):
                raise BackupCoordinationError()
            with backup_operation(self.barrier, kind=self._kind(scope, path)):
                await self.app(scope, receive, observed_send)
        except BackupCoordinationError as exc:
            if started:
                # A partial response cannot be replaced by a successful-looking new one.
                raise BackupCoordinationError() from None
            await _unavailable_response(exc)(scope, receive, send)


@asynccontextmanager
async def agent_backup_operation(barrier: BackupWriteBarrier) -> AsyncIterator[BackupWriteLease]:
    """Keep one received message locally while admission is paused; never replay writes."""
    if not isinstance(barrier, BackupWriteBarrier):
        raise BackupCoordinationError()
    deadline = monotonic() + AGENT_BACKUP_WAIT_SECONDS
    retrying = False
    while True:
        if retrying and monotonic() >= deadline:
            raise BackupBusyError()
        operation = backup_operation(barrier, kind="agent")
        try:
            lease = operation.__enter__()
        except BackupBusyError:
            retrying = True
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise BackupBusyError() from None
            await asyncio.sleep(min(AGENT_BACKUP_POLL_SECONDS, remaining))
        else:
            break
    try:
        yield lease
    finally:
        operation.__exit__(*sys.exc_info())
