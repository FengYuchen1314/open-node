"""Real ASGI/SQLite/thread boundaries; controlled clocks are explicitly separated."""

import asyncio
import inspect
import json
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from types import ModuleType
from typing import Annotated
from uuid import UUID

import httpx
import pytest
from conftest import ADMIN_PASSWORD, authenticated_client
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from open_node.api import backup as module
from open_node.api import router as project_router
from open_node.api.auth import require_administrator
from open_node.api.backup import (
    BackupAPIRoute,
    BackupHTTPMiddleware,
    agent_backup_operation,
)
from open_node.api.routes import agents
from open_node.api.routes.certificates import CertificateRoute
from open_node.api.routes.subscriber_auth import require_subscriber
from open_node.api.routes.subscription_templates import actor
from open_node.core.config import Settings
from open_node.domain.inventory import AgentCommandCreate
from open_node.services.backup_coordination import (
    BackupBusyError,
    BackupCoordinationError,
    BackupWriteBarrier,
)
from open_node.services.backup_runtime import (
    PROTECTED_SYNC_ATTRIBUTE,
    backup_operation,
    current_backup_child_fds,
    protected_sync,
    run_in_backup_thread,
    run_in_backup_threadpool,
)
from open_node.services.certificate_remote import ENDPOINT, RemoteHTTP01
from open_node.services.certificates import CertificateHTTPLease, CertificateJob, ManagedCertificate
from open_node.services.inventory import CommandModel, CommandStreamFrameModel
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from starlette.responses import Response
from test_subscriptions import create_catalog_fixture

BASE = "/api/v1"


class ThreadCall:
    def __init__(self, function):
        self.errors = []

        def run():
            try:
                function()
            except BaseException as exc:
                self.errors.append(exc)

        self.thread = threading.Thread(target=run, name="backup-http-fixture")
        self.thread.start()

    def join(self):
        self.thread.join(6)
        assert not self.thread.is_alive(), "fixture thread did not finish"
        assert not self.errors, "fixture thread failed"


@contextmanager
def snapshot_attempt(barrier):
    entered, release = threading.Event(), threading.Event()

    def snapshot():
        with barrier.snapshot(timeout=5) as permit:
            permit.assert_active()
            entered.set()
            assert release.wait(5), "fixture snapshot was not released"

    call = ThreadCall(snapshot)
    try:
        yield entered, release
    finally:
        release.set()
        call.join()


@contextmanager
def paused(barrier):
    with snapshot_attempt(barrier) as (entered, release):
        assert entered.wait(2), "fixture did not acquire snapshot"
        yield release


@contextmanager
def held_work(barrier):
    entered, release = threading.Event(), threading.Event()

    def work():
        with backup_operation(barrier):
            entered.set()
            assert release.wait(5), "fixture work was not released"

    call = ThreadCall(work)
    try:
        assert entered.wait(2)
        yield release
    finally:
        release.set()
        call.join()


def work_is_paused(barrier):
    try:
        with barrier.operation():
            return False
    except BackupBusyError:
        return True


async def eventually(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "fixture condition did not complete"
        await asyncio.sleep(0.005)


def scope(path, *, method="GET", root_path="", kind="http"):
    return {
        "type": kind,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "scheme": "ws" if kind == "websocket" else "http",
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 12000),
    }


async def request(app, path, *, method="GET", root_path="", body=b"", on_send=None):
    incoming = asyncio.Queue()
    await incoming.put({"type": "http.request", "body": body, "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)
        if on_send is not None:
            await on_send(message)

    await app(scope(path, method=method, root_path=root_path), incoming.get, send)
    return sent


def status(messages):
    return next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )


def response_json(messages):
    return json.loads(b"".join(message.get("body", b"") for message in messages))


@pytest.fixture
def barrier(tmp_path):
    value = BackupWriteBarrier(tmp_path / "writes.lock")
    try:
        yield value
    finally:
        value.close()


@pytest.fixture
def application(tmp_path):
    from open_node.main import create_app

    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'http.sqlite'}",
            certificate_state_dir=tmp_path / "certificates",
            short_links_enabled=True,
        )
    )
    admin = authenticated_client(app)
    try:
        yield app, admin
    finally:
        admin.close()
        app.state.inventory._engine.dispose()
        app.state.auth.engine.dispose()
        app.state.certificates.engine.dispose()
        app.state.backup_writes.close()


def test_every_project_router_protects_sync_endpoints_before_dependency_construction():
    routers = {
        id(value): value for value in vars(project_router).values() if isinstance(value, APIRouter)
    }
    for item in vars(project_router).values():
        if isinstance(item, ModuleType):
            routers.update(
                {id(value): value for value in vars(item).values() if isinstance(value, APIRouter)}
            )
    count = 0
    for router in routers.values():
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            assert isinstance(route, BackupAPIRoute)
            original = inspect.unwrap(route.endpoint)
            if not inspect.iscoroutinefunction(original):
                count += 1
                assert getattr(route.endpoint, PROTECTED_SYNC_ATTRIBUTE, False)
                assert inspect.signature(route.endpoint) == inspect.signature(
                    original, eval_str=True
                )
    assert len(routers) == 34
    assert count > 100
    assert issubclass(CertificateRoute, BackupAPIRoute)
    for dependency in (require_administrator, require_subscriber, actor):
        assert getattr(dependency, PROTECTED_SYNC_ATTRIBUTE, False)


def test_route_wrapper_is_idempotent_and_does_not_replace_the_module_function():
    def endpoint(number: int = 1):
        return {"number": number}

    first = BackupAPIRoute("/first", endpoint)
    second = BackupAPIRoute("/second", first.endpoint)
    assert first.endpoint is second.endpoint
    assert not getattr(endpoint, PROTECTED_SYNC_ATTRIBUTE, False)
    assert inspect.signature(first.endpoint) == inspect.signature(endpoint)


@pytest.mark.asyncio
async def test_sync_route_without_runtime_context_fails_closed_with_safe_503():
    app = FastAPI()
    router = APIRouter(route_class=BackupAPIRoute)
    called = []

    @router.get("/secret-path")
    def endpoint():
        called.append(True)

    app.include_router(router)
    messages = await request(app, "/secret-path")
    assert status(messages) == 503
    assert response_json(messages) == {
        "code": "backup_coordination_unavailable",
        "detail": "备份停写协调暂不可用，请稍后重试。",
        "license_required": False,
    }
    assert not called


AGENT_PATHS = [
    "/agents/register",
    "/agents/heartbeat",
    "/agents/traffic",
    "/agents/telemetry",
    "/agents/commands/lease",
    "/agents/scan",
    "/agents/commands/00000000-0000-4000-8000-000000000001/result",
    "/agents/commands/by-request/request-1/result",
]
WORK_PATHS = [
    "/agents",
    "/agents/identity",
    "/agents/bootstrap/redeem",
    "/agents/heartbeat/extra",
    "/agents//heartbeat",
    "/agents/commands/by-request//result",
    "/agents/commands/request-1/result/extra",
    "/agents/register-other",
    "/notifications/test",
    "/account/register",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [BASE, "/custom/v2"])
@pytest.mark.parametrize(
    "path,expected", [(p, "agent") for p in AGENT_PATHS] + [(p, "work") for p in WORK_PATHS]
)
async def test_exact_fallback_classification_enters_the_real_barrier(
    barrier, monkeypatch, prefix, path, expected
):
    kinds = []
    original = barrier.operation

    @contextmanager
    def observe(*, kind="work"):
        kinds.append(kind)
        with original(kind=kind) as lease:
            yield lease

    monkeypatch.setattr(barrier, "operation", observe)

    async def terminal(asgi_scope, receive, send):
        assert current_backup_child_fds()
        await Response(status_code=204)(asgi_scope, receive, send)

    wrapped = BackupHTTPMiddleware(terminal, barrier=barrier, api_prefix=prefix)
    assert status(await request(wrapped, prefix + path, method="POST")) == 204
    assert kinds == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,root_path,method,expected",
    [
        (BASE + "/agents/heartbeat/", "", "POST", "agent"),
        ("/panel" + BASE + "/agents/heartbeat", "/panel", "POST", "agent"),
        (BASE + "/agents/heartbeat", "/panel", "POST", "agent"),
        ("/panel-extra" + BASE + "/agents/heartbeat", "/panel", "POST", "work"),
        (BASE + "/agents/heartbeat", "", "GET", "work"),
        (BASE + "/agents/heartbeat", "", "PUT", "work"),
        ("/api/v10/agents/heartbeat", "", "POST", "work"),
        ("/api/remote/ws", "", "POST", "work"),
    ],
)
async def test_fallback_prefix_root_path_slash_and_method_boundaries(
    barrier, path, root_path, method, expected
):
    wrapped = BackupHTTPMiddleware(None, barrier=barrier, api_prefix=BASE)
    request_scope = scope(path, root_path=root_path, method=method)
    assert wrapped._kind(request_scope, module._route_path(request_scope)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/t/private-code", "/x/private-code", BASE + "/auth/session"])
async def test_all_http_methods_and_public_aliases_pause_before_the_app(barrier, method, path):
    called = []

    async def terminal(*_):
        called.append(True)

    wrapped = BackupHTTPMiddleware(terminal, barrier=barrier, api_prefix=BASE)
    with paused(barrier):
        messages = await request(wrapped, path, method=method)
    assert status(messages) == 503
    assert response_json(messages)["code"] == "backup_busy"
    headers = dict(messages[0]["headers"])
    assert headers[b"cache-control"] == b"no-store"
    assert headers[b"retry-after"] == b"1"
    assert "private-code" not in str(messages)
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,method,expected",
    [
        ("/healthz", "GET", 204),
        ("/healthz/", "HEAD", 204),
        ("/healthz", "POST", 503),
        (BASE + "/healthz", "GET", 503),
        (BASE + "/branding", "GET", 503),
    ],
)
async def test_only_root_readonly_health_is_exempt(barrier, path, method, expected):
    async def terminal(asgi_scope, receive, send):
        await Response(status_code=204)(asgi_scope, receive, send)

    wrapped = BackupHTTPMiddleware(terminal, barrier=barrier, api_prefix=BASE)
    with paused(barrier):
        messages = await request(wrapped, path, method=method)
    assert status(messages) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_kind", ["dependency", "background"])
async def test_full_asgi_lifetime_includes_after_body_cleanup(barrier, tmp_path, cleanup_kind):
    events = []
    app = FastAPI()
    router = APIRouter(route_class=BackupAPIRoute)
    output = tmp_path / "after-body.txt"

    def persist():
        assert current_backup_child_fds()
        output.write_text("after response body", encoding="utf-8")

    async def finish():
        assert events == ["body"]
        assert current_backup_child_fds()
        await run_in_backup_thread(persist)
        events.append("cleanup")

    async def dependency():
        yield
        if cleanup_kind == "dependency":
            await finish()

    @router.get("/cleanup")
    async def endpoint(_value: Annotated[object, Depends(dependency)]):
        return Response(
            "ok", background=BackgroundTask(finish) if cleanup_kind == "background" else None
        )

    async def observe(message):
        if message["type"] == "http.response.body":
            events.append("body")

    app.include_router(router)
    wrapped = BackupHTTPMiddleware(app, barrier=barrier, api_prefix=BASE)
    messages = await request(wrapped, "/cleanup", on_send=observe)
    assert status(messages) == 200
    assert events == ["body", "cleanup"]
    assert output.read_text() == "after response body"
    with barrier.snapshot(timeout=1):
        pass


@pytest.mark.asyncio
async def test_started_response_is_not_replaced_with_a_second_status(barrier):
    sent = []

    async def terminal(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise BackupCoordinationError()

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    wrapped = BackupHTTPMiddleware(terminal, barrier=barrier, api_prefix=BASE)
    with pytest.raises(BackupCoordinationError):
        await wrapped(scope("/partial"), receive, send)
    assert len(sent) == 1 and sent[0]["status"] == 200
    with barrier.snapshot(timeout=1):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["endpoint", "dependency", "asyncio", "anyio"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_raw_task_cancel_cannot_release_a_running_writer_thread(
    barrier, tmp_path, entry, cancel
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    written = tmp_path / "actual-thread-write.txt"
    worker_ids = []
    parent_events = []
    app = FastAPI()
    router = APIRouter(route_class=BackupAPIRoute)

    def writer():
        worker_ids.append(threading.get_ident())
        assert current_backup_child_fds()
        entered.set()
        try:
            assert release.wait(5), "writer was not released"
            assert current_backup_child_fds()
            written.write_text("real thread completed its owned write", encoding="utf-8")
        finally:
            finished.set()

    if entry == "endpoint":
        router.add_api_route("/writer", writer, methods=["GET"])
    elif entry == "dependency":
        guarded = protected_sync(writer)

        @router.get("/writer")
        async def endpoint(_value: Annotated[object, Depends(guarded)]):
            return {"ok": True}
    else:
        offload = run_in_backup_thread if entry == "asyncio" else run_in_backup_threadpool

        @router.get("/writer")
        async def endpoint():
            await offload(writer)
            return {"ok": True}

    app.include_router(router)
    wrapped = BackupHTTPMiddleware(app, barrier=barrier, api_prefix=BASE)

    async def parent():
        try:
            return await request(wrapped, "/writer")
        finally:
            parent_events.append(written.exists())

    task = asyncio.create_task(parent())
    try:
        await eventually(entered.is_set)
        assert worker_ids == [worker_ids[0]]
        assert worker_ids[0] != threading.get_ident()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert parent_events == [False]
            with pytest.raises(BackupBusyError), barrier.snapshot(timeout=0):
                pass
        release.set()
        await eventually(finished.is_set)
        if not cancel:
            assert status(await asyncio.wait_for(task, 3)) == 200
            assert parent_events == [True]
        with barrier.snapshot(timeout=2):
            assert written.read_text() == "real thread completed its owned write"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await eventually(finished.is_set)


@pytest.mark.asyncio
async def test_agent_helper_retries_only_before_entry_and_preserves_cancellation(
    barrier, monkeypatch
):
    original = module.backup_operation
    attempts = []

    @contextmanager
    def observe(value, *, kind="work"):
        attempts.append(kind)
        with original(value, kind=kind) as lease:
            yield lease

    monkeypatch.setattr(module, "backup_operation", observe)
    with pytest.raises(BackupBusyError):
        async with agent_backup_operation(barrier):
            raise BackupBusyError()
    assert attempts == ["agent"]
    attempts.clear()
    with paused(barrier):

        async def waiting():
            async with agent_backup_operation(barrier):
                pytest.fail("a paused operation was entered")

        task = asyncio.create_task(waiting())
        await eventually(lambda: bool(attempts))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_agent_wait_uses_sixty_second_bound_with_controlled_clock_not_real_delay(
    barrier, monkeypatch
):
    original_sleep = asyncio.sleep
    clock, intervals = [0.0], []

    async def advance(delay):
        intervals.append(delay)
        clock[0] += delay
        await original_sleep(0)

    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.asyncio, "sleep", advance)
    assert module.AGENT_BACKUP_WAIT_SECONDS == 60.0
    assert module.AGENT_BACKUP_POLL_SECONDS == 0.1
    with paused(barrier), pytest.raises(BackupBusyError):
        async with agent_backup_operation(barrier):
            pytest.fail("controlled paused operation was entered")
    assert clock[0] == pytest.approx(60.0)
    assert intervals and all(0 < item <= 0.1 for item in intervals)


def test_real_nested_private_public_account_routes_and_public_aliases(application, monkeypatch):
    app, admin = application
    barrier = app.state.backup_writes
    _token, _server, node, plan = create_catalog_fixture(admin)
    assert admin.post(BASE + "/users/alice/plan", json={"plan_id": plan}).status_code == 200
    account = admin.get(BASE + "/subscriber-accounts", params={"username": "alice"}).json()
    saved = admin.put(
        BASE + "/subscriber-accounts",
        params={"username": "alice"},
        json={
            "expected_revision": account["revision"],
            "new_password": ADMIN_PASSWORD,
            "reset_totp": False,
        },
    )
    assert saved.status_code == 200
    subscriber = TestClient(app, base_url="https://testserver")
    try:
        login = subscriber.post(
            BASE + "/account/login",
            json={"username": "alice", "password": ADMIN_PASSWORD},
            headers={"X-Open-Node-Client": "browser"},
        )
        assert login.status_code == 200 and login.json()["authenticated"]
        share = admin.post(
            BASE + "/temporary-subscriptions",
            json={
                "username": "alice",
                "label": "Backup gate",
                "node_ids": [node],
                "max_access": 3,
                "expires_in_seconds": 300,
            },
        )
        assert share.status_code == 201
        share_path = httpx.URL(share.json()["subscription_url"]).path
        observed = []
        original = barrier.operation

        @contextmanager
        def observe(*, kind="work"):
            observed.append((threading.get_ident(), kind))
            with original(kind=kind) as lease:
                yield lease

        monkeypatch.setattr(barrier, "operation", observe)
        cases = [
            (admin, BASE + "/users", 200),
            (admin, BASE + "/subscription-templates/starter", 200),
            (subscriber, BASE + "/account/subscription-templates/starter", 200),
            (admin, BASE + "/branding", 200),
            (admin, "/api/public/probe-settings", 200),
            (admin, BASE + "/public/probe-settings", 200),
            (admin, share_path + "?format=xray", 200),
            (admin, "/x/not-a-real-profile", 404),
        ]
        for client, path, expected in cases:
            observed.clear()
            assert client.get(path).status_code == expected
            assert {kind for _, kind in observed} == {"work"}
            assert len({identifier for identifier, _ in observed}) >= 2
        listed = admin.get(BASE + "/temporary-subscriptions").json()["subscriptions"]
        assert listed[0]["access_count"] == 1
        with paused(barrier):
            for client, path, _expected in cases:
                assert client.get(path).status_code == 503
            assert admin.get("/healthz").status_code == 200
        listed = admin.get(BASE + "/temporary-subscriptions").json()["subscriptions"]
        assert listed[0]["access_count"] == 1
    finally:
        subscriber.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("by_request", [False, True])
async def test_phase_one_allows_real_certificate_presentation_receipt(application, by_request):
    app, admin = application
    barrier, store = app.state.backup_writes, app.state.certificates
    server = admin.post(BASE + "/servers", json={"name": "HTTP-01 receipt fixture"}).json()
    scanned = admin.post(
        BASE + "/agents/scan",
        json={
            "token": server["agent_token"],
            "http01": {"version": 1, "standalone": True, "webroots": [], "cleanup_error": None},
        },
    )
    assert scanned.status_code == 200
    created = admin.post(
        BASE + "/certificates",
        json={
            "name": "HTTP-01 fixture",
            "domains": ["localhost"],
            "email": "operator@example.com",
            "challenge_type": "standalone",
            "accept_terms": True,
            "validation_server_id": server["server"]["id"],
        },
    )
    assert created.status_code == 201
    with backup_operation(barrier):
        queued = store.queue(created.json()["id"], "issue")
    with store.session() as db:
        row = db.get(ManagedCertificate, created.json()["id"])
        job = db.get(CertificateJob, queued["id"])
    remote = RemoteHTTP01(store, app.state.agent_connections, backup_writes=barrier)

    async def present():
        with backup_operation(barrier):
            await remote.present(
                row,
                job,
                [
                    {
                        "domain": "localhost",
                        "token": "t" * 43,
                        "key_authorization": "t" * 43 + "." + "a" * 43,
                    }
                ],
            )

    def lease_created():
        with store.session() as db:
            return db.scalar(select(CertificateHTTPLease.id)) is not None

    task = asyncio.create_task(present())
    try:
        await eventually(lease_created)
        with snapshot_attempt(barrier) as (entered, _release):
            try:
                await eventually(lambda: work_is_paused(barrier))
                assert not entered.is_set()
                assert admin.get(BASE + "/branding").status_code == 503
                response = admin.post(
                    BASE + "/agents/commands/lease",
                    json={"token": server["agent_token"], "max_commands": 10},
                )
                assert response.status_code == 200
                commands = response.json()["commands"]
                command = next(item for item in commands if item["path"] == ENDPOINT)
                route = (
                    "/agents/commands/by-request/" + command["request_id"]
                    if by_request
                    else "/agents/commands/" + command["id"]
                )
                accepted = admin.post(
                    BASE + route + "/result",
                    json={
                        "token": server["agent_token"],
                        "status": 200,
                        "body": {"success": True, "lease_id": command["body"]["lease_id"]},
                    },
                )
                assert accepted.status_code == 200
                await asyncio.wait_for(task, 3)
                await eventually(entered.is_set)
                assert (
                    admin.post(
                        BASE + "/agents/heartbeat", json={"token": server["agent_token"]}
                    ).status_code
                    == 503
                )
                with store.session() as db:
                    assert db.get(CommandModel, command["id"]).status == "succeeded"
            finally:
                # Join the snapshot thread only after the actual work task has
                # relinquished its lease, including when an assertion fails.
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class Socket:
    """An actual ASGI websocket session with bounded fixture waits and cleanup."""

    def __init__(self, app, path):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.task = asyncio.create_task(
            app(scope(path, kind="websocket"), self.incoming.get, self.outgoing.put)
        )

    async def connect(self):
        await self.incoming.put({"type": "websocket.connect"})
        assert (await self.receive())["type"] == "websocket.accept"

    async def send(self, payload):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps(payload)})

    async def receive(self):
        return await asyncio.wait_for(self.outgoing.get(), 3)

    async def receive_json(self):
        message = await self.receive()
        assert message["type"] == "websocket.send"
        return json.loads(message["text"])

    async def close(self):
        await self.incoming.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 3)
        finally:
            if not self.task.done():
                self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


@asynccontextmanager
async def socket(app, path):
    client = Socket(app, path)
    try:
        await client.connect()
        yield client
    finally:
        await client.close()


def auth_message(server):
    return {
        "type": "auth",
        "payload": {
            "token": server["agent_token"],
            "hostname": "backup-fixture",
            "capabilities": {"rpc": False, "stream": True},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [BASE + "/agents/ws", "/api/remote/ws"])
@pytest.mark.parametrize("stage", ["registration", "message"])
async def test_websocket_buffers_one_message_and_idle_connections_do_not_block_snapshot(
    application, monkeypatch, path, stage
):
    app, admin = application
    barrier, inventory = app.state.backup_writes, app.state.inventory
    server = admin.post(BASE + "/servers", json={"name": "Paused websocket"}).json()
    attempted = threading.Event()
    original_operation = module.backup_operation
    writes = []
    original_heartbeat = inventory.record_heartbeat

    @contextmanager
    def observe(value, *, kind="work"):
        if kind == "agent":
            attempted.set()
        with original_operation(value, kind=kind) as lease:
            yield lease

    def heartbeat(payload):
        assert current_backup_child_fds()
        writes.append(payload.listen_port)
        return original_heartbeat(payload)

    monkeypatch.setattr(module, "backup_operation", observe)
    monkeypatch.setattr(inventory, "record_heartbeat", heartbeat)
    async with socket(app, path) as client:
        if stage == "message":
            await client.send(auth_message(server))
            assert (await client.receive_json())["payload"]["success"]
        with paused(barrier) as release:
            attempted.clear()
            message = (
                auth_message(server)
                if stage == "registration"
                else {"type": "heartbeat", "payload": {"listen_port": 24567}}
            )
            await client.send(message)
            await eventually(attempted.is_set)
            assert client.outgoing.empty()
            assert not writes
            assert bool(inventory.list_agents()) == (stage == "message")
            release.set()
        reply = await client.receive_json()
        assert reply["type"] == ("auth_result" if stage == "registration" else "heartbeat_ack")
        assert writes == ([] if stage == "registration" else [24567])
        with paused(barrier):
            assert not client.task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_type",
    ["heartbeat", "traffic", "telemetry", "scan_result", "rpc_reply", "rpc_stream_data", "ping"],
)
async def test_every_agent_message_and_followup_dispatch_has_an_active_lease(
    application, monkeypatch, message_type
):
    app, admin = application
    barrier, inventory = app.state.backup_writes, app.state.inventory
    server = admin.post(BASE + "/servers", json={"name": "Agent message fixture"}).json()
    handled, dispatched = [], []
    original_handler = agents._handle_agent_ws_message
    connections = app.state.agent_connections
    original_dispatch = connections.dispatch_pending_commands

    async def handle(*args):
        assert current_backup_child_fds()
        handled.append(args[-1]["type"])
        await original_handler(*args)
        assert current_backup_child_fds()

    async def dispatch(*args):
        assert current_backup_child_fds()
        dispatched.append(True)
        await original_dispatch(*args)

    monkeypatch.setattr(agents, "_handle_agent_ws_message", handle)
    monkeypatch.setattr(connections, "dispatch_pending_commands", dispatch)
    async with socket(app, BASE + "/agents/ws") as client:
        await client.send(auth_message(server))
        assert (await client.receive_json())["payload"]["success"]
        payload = {}
        command = None
        if message_type in {"rpc_reply", "rpc_stream_data"}:
            with backup_operation(barrier):
                command = inventory.create_command(
                    UUID(server["server"]["id"]),
                    AgentCommandCreate(
                        method="GET",
                        path="/api/child/system/info",
                        stream=message_type == "rpc_stream_data",
                    ),
                )
                inventory.lease_commands(server["agent_token"], 10)
            payload = (
                {"request_id": command.request_id, "status": 200, "body": {"hostname": "fixture"}}
                if message_type == "rpc_reply"
                else {"request_id": command.request_id, "data": "fixture frame"}
            )
        await client.send({"type": message_type, "payload": payload})
        await eventually(lambda: handled == [message_type] and len(dispatched) >= 2)
        if message_type == "rpc_stream_data":
            with inventory._session() as db:
                assert db.scalar(select(func.count()).select_from(CommandStreamFrameModel)) == 1
        else:
            acknowledgement = await client.receive_json()
            assert (
                acknowledgement["type"]
                == {
                    "heartbeat": "heartbeat_ack",
                    "traffic": "telemetry_ack",
                    "telemetry": "telemetry_ack",
                    "scan_result": "scan_result_ack",
                    "rpc_reply": "rpc_reply_ack",
                    "ping": "pong",
                }[message_type]
            )
        if message_type == "rpc_reply":
            with inventory._session() as db:
                assert db.get(CommandModel, str(command.id)).status == "succeeded"


@pytest.mark.asyncio
async def test_public_probe_websocket_remains_readonly_without_runtime_context(application):
    app, _admin = application
    with paused(app.state.backup_writes):
        async with socket(app, "/api/public/probe-ws") as client:
            payload = await client.receive_json()
            assert "servers" in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [BackupCoordinationError, BackupBusyError])
async def test_websocket_failed_admission_closes_1013_without_business_receipt(
    application, monkeypatch, error
):
    app, admin = application
    server = admin.post(BASE + "/servers", json={"name": "Unavailable websocket"}).json()

    @asynccontextmanager
    async def unavailable(_barrier):
        raise error()
        yield  # pragma: no cover -- shape of a failing async context manager

    monkeypatch.setattr(agents, "agent_backup_operation", unavailable)
    async with socket(app, BASE + "/agents/ws") as client:
        await client.send(auth_message(server))
        reply = await client.receive()
        assert reply["type"] == "websocket.close" and reply["code"] == 1013
        assert client.outgoing.empty()
        assert not app.state.inventory.list_agents()
