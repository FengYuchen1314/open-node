import asyncio
import contextlib
import json
import logging
import os
import socket
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, ValidationError
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from open_node_agent import __version__
from open_node_agent.config import AgentConfig
from open_node_agent.journal import CommandJournal
from open_node_agent.lifecycle import DeferredCommand
from open_node_agent.lifecycle_protocol import is_lifecycle_command
from open_node_agent.operations import Operations, telemetry
from open_node_agent.runtime import XrayRuntime, atomic_write

log = logging.getLogger("open-node-agent")


class RPCCommand(BaseModel):
    id: str | None = None
    request_id: str = Field(min_length=1, max_length=255)
    method: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    path: str = Field(min_length=1, max_length=255, pattern=r"^/api/child/")
    query: str = Field(default="", max_length=2048)
    body: dict | None = None
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    stream: bool = False


class AuthenticationRejected(ValueError):
    pass


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.journal = CommandJournal(config.state_dir)
        self.runtime = XrayRuntime(config)
        self.operations = Operations(self.runtime, self.journal)
        self.queue = asyncio.Queue(maxsize=32)
        self.execution_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.websocket = None
        self.tasks: list[asyncio.Task] = []
        self.connected = False
        self.last_contact: float | None = None

    def control_contact(self) -> None:
        self.connected = True
        self.last_contact = time.monotonic()

    async def health_report(self) -> dict:
        desired = self.journal.desired_running(self.config.auto_start)
        return {
            "pid": os.getpid(),
            "agent_version": __version__,
            "package_path": str(Path(__file__).resolve().parent),
            "observed_at": time.time(),
            "connected": (
                self.connected
                and self.last_contact is not None
                and time.monotonic() - self.last_contact
                <= max(45, self.config.heartbeat_seconds * 3)
            ),
            "runtime_ready": (not desired or await self.runtime.running()) and (
                not self.journal.desired_running(False, "nginx")
                or await self.operations.nginx.running()
            ),
        }

    async def health_loop(self) -> None:
        while True:
            atomic_write(
                self.config.state_dir / "health.json",
                json.dumps(await self.health_report()).encode(),
            )
            await asyncio.sleep(1)

    def registration(self) -> dict:
        return {
            "token": self.config.token.get_secret_value(),
            "hostname": self.config.hostname or socket.gethostname()[:255],
            "agent_version": "open-node/" + __version__,
            "connection_mode": self.config.connection_mode,
            "xray_mode": "external",
            "listen_port": 0,
            "capabilities": {
                "rpc": True,
                "stream": True,
                "return_route_test": self.operations.diagnostics.route_available(),
            },
        }

    async def execute(self, payload: dict) -> dict | None:
        command = RPCCommand.model_validate(payload).model_dump()
        async with self.execution_lock:
            lifecycle = is_lifecycle_command(command)
            if cached := self.journal.begin(
                command, resume=lifecycle and self.config.lifecycle_socket is not None
            ):
                return cached
            result = {"request_id": command["request_id"], "status": 200}
            if command["id"]:
                result["command_id"] = command["id"]
            try:
                if lifecycle:
                    # Submission has its own bounded handshake; a lease is not a host-job deadline.
                    result = await self.operations.lifecycle.submit(command)
                    if command["id"]:
                        result["command_id"] = command["id"]
                else:
                    async with asyncio.timeout(command["timeout_ms"] / 1000):
                        result["body"] = await self.operations.handle(command)
            except DeferredCommand:
                return None
            except TimeoutError:
                result.update(status=504, error="Agent command timed out")
            except NotImplementedError as exc:
                result.update(status=501, error=str(exc))
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                result.update(status=400, error=str(exc))
            except OSError as exc:
                result.update(
                    status=500, error=f"Host operation failed: {exc.strerror or type(exc).__name__}"
                )
            except Exception as exc:
                log.error("Command failed unexpectedly (%s)", type(exc).__name__)
                result.update(status=500, error="Unexpected agent operation failure")
            if "error" in result:
                result["error"] = result["error"][:2048]
            self.journal.finish(result)
            return result

    async def send(self, kind: str, payload: dict) -> None:
        async with self.send_lock:
            connection = self.websocket
            if connection is not None:
                with contextlib.suppress(OSError, WebSocketException):
                    await connection.send(json.dumps({"type": kind, "payload": payload}))

    async def worker(self) -> None:
        while True:
            command = await self.queue.get()
            try:
                result = await self.execute(command)
                if result is None:
                    continue
                if command.get("stream"):
                    await self.send(
                        "rpc_stream_data",
                        {
                            "request_id": command["request_id"],
                            "data": json.dumps({"status": result["status"], "complete": True}),
                        },
                    )
                await self.send("rpc_reply", result)
            finally:
                self.queue.task_done()

    async def collect_telemetry(self) -> dict:
        report = telemetry()
        try:
            report["stats"] = await self.runtime.stats()
        except (ValueError, OSError, TimeoutError):
            report["stats"] = None
        return report

    async def websocket_reports(self) -> None:
        next_telemetry = 0.0
        while True:
            await self.send("heartbeat", {})
            for result in self.journal.pending_results():
                await self.send("rpc_reply", result)
            if time.monotonic() >= next_telemetry:
                await self.send("telemetry", await self.collect_telemetry())
                await self.send("scan_result", await self.operations.scan())
                next_telemetry = time.monotonic() + self.config.telemetry_seconds
            await asyncio.sleep(self.config.heartbeat_seconds)

    async def websocket_session(self) -> None:
        kwargs = (
            {"ssl": self.config.tls_context()}
            if self.config.master_url.startswith("https:")
            else {}
        )
        async with connect(
            self.config.websocket_url(),
            proxy=None,
            max_size=4 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
            **kwargs,
        ) as connection:
            await connection.send(json.dumps({"type": "auth", "payload": self.registration()}))
            async with asyncio.timeout(10):
                reply = json.loads(await connection.recv())
            if (
                not isinstance(reply, dict)
                or reply.get("type") != "auth_result"
                or not isinstance(reply.get("payload"), dict)
                or reply.get("payload", {}).get("success") is not True
            ):
                raise AuthenticationRejected("Agent token was rejected")
            self.websocket = connection
            self.control_contact()
            log.info("Connected to Open Node over WebSocket")
            reporter = asyncio.create_task(self.websocket_reports())
            receiver = asyncio.create_task(self.websocket_receive(connection))
            try:
                for result in self.journal.pending_results():
                    await self.send("rpc_reply", result)
                done, _ = await asyncio.wait(
                    (reporter, receiver), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    await task
            finally:
                self.websocket = None
                self.connected = False
                for task in (reporter, receiver):
                    task.cancel()
                for task in (reporter, receiver):
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def websocket_receive(self, connection) -> None:
        async for raw in connection:
            message = json.loads(raw)
            if not isinstance(message, dict):
                continue
            payload = message.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            if message.get("type") == "rpc_call":
                try:
                    command = RPCCommand.model_validate(payload).model_dump()
                except ValidationError:
                    log.warning("Rejected malformed RPC command")
                    continue
                await self.queue.put(command)
            elif message.get("type") == "rpc_reply_ack":
                self.journal.acknowledge(str(payload.get("request_id", "")))
            elif message.get("type") == "heartbeat_ack":
                self.control_contact()

    async def http_session(self, duration: float | None = None) -> None:
        token = self.config.token.get_secret_value()
        started = time.monotonic()
        base = self.config.master_url + "/api/v1/agents"
        async with httpx.AsyncClient(
            verify=self.config.tls_context(), trust_env=False, timeout=15, follow_redirects=False
        ) as client:
            registered = await client.post(base + "/register", json=self.registration())
            registered.raise_for_status()
            self.control_contact()
            reporter = asyncio.create_task(self.http_reports(client, base, token))
            try:
                while duration is None or time.monotonic() - started < duration:
                    if reporter.done():
                        await reporter
                    for result in self.journal.pending_results():
                        target = (
                            f"/commands/{result['command_id']}/result"
                            if result.get("command_id")
                            else (
                                "/commands/by-request/"
                                + quote(result["request_id"], safe="")
                                + "/result"
                            )
                        )
                        response = await client.post(
                            base + target, json={"token": token, **result}
                        )
                        if response.status_code != 404 or not result.get("command_id"):
                            response.raise_for_status()
                        self.journal.acknowledge(result["request_id"])
                    response = await self._post(
                        client, base + "/commands/lease", {"token": token, "max_commands": 1}
                    )
                    for command in response.json()["commands"]:
                        result = await self.execute(command)
                        if result is None:
                            continue
                        await self._post(
                            client,
                            base + f"/commands/{command['id']}/result",
                            {"token": token, **result},
                        )
                        self.journal.acknowledge(result["request_id"])
                    await asyncio.sleep(self.config.poll_seconds)
            finally:
                self.connected = False
                reporter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reporter

    async def http_reports(self, client, base, token):
        next_report = 0.0
        while True:
            await self._post(client, base + "/heartbeat", {"token": token})
            self.control_contact()
            if time.monotonic() >= next_report:
                await self._post(
                    client, base + "/telemetry", {"token": token, **await self.collect_telemetry()}
                )
                await self._post(
                    client, base + "/scan", {"token": token, **await self.operations.scan()}
                )
                next_report = time.monotonic() + self.config.telemetry_seconds
            await asyncio.sleep(self.config.heartbeat_seconds)

    @staticmethod
    async def _post(client, url, payload):
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response

    async def monitor_runtime(self) -> None:
        while True:
            try:
                async with self.runtime.lock:
                    if (
                        self.journal.desired_running(self.config.auto_start)
                        and not await self.runtime.running()
                    ):
                        await self.runtime.start()
            except (OSError, ValueError, TimeoutError) as exc:
                log.warning(
                    "Xray unavailable (%s); agent connection remains active", type(exc).__name__
                )
            try:
                async with self.runtime.lock:
                    if self.journal.desired_running(False, "nginx"):
                        await self.operations.nginx.start()
            except (OSError, ValueError, TimeoutError) as exc:
                log.warning(
                    "Nginx unavailable (%s); agent connection remains active", type(exc).__name__
                )
            await asyncio.sleep(5)

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            self.tasks = [
                group.create_task(self.worker()),
                group.create_task(self.monitor_runtime()),
                group.create_task(self.connection_loop()),
                group.create_task(self.health_loop()),
            ]

    async def connection_loop(self) -> None:
        delay = 1
        while True:
            try:
                if self.config.connection_mode == "http":
                    await self.http_session()
                else:
                    await self.websocket_session()
                delay = 1
            except AuthenticationRejected:
                log.warning("Authentication rejected; check this node's token")
                delay = 30
            except (OSError, ValueError, TimeoutError, WebSocketException, httpx.HTTPError) as exc:
                log.warning("Control-plane connection lost (%s)", type(exc).__name__)
                if self.config.connection_mode == "auto":
                    with contextlib.suppress(OSError, ValueError, TimeoutError, httpx.HTTPError):
                        await self.http_session(duration=30)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.operations.nginx.close()
        await self.runtime.close()
        self.journal.close()
