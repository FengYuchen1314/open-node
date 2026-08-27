import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from open_node_agent.client import Agent, AuthenticationRejected
from websockets.asyncio.server import serve


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_websocket_resends_unacknowledged_result_without_execution(config):
    agent = Agent(config)
    agent.operations.handle = AsyncMock(return_value={"success": True})
    agent.collect_telemetry = AsyncMock(return_value={})
    agent.runtime.scan = AsyncMock(return_value={})
    results = []
    connections = 0
    command = {"request_id": "durable", "method": "POST", "path": "/api/child/scan"}

    async def controller(connection):
        nonlocal connections
        connections += 1
        current = connections
        auth = json.loads(await connection.recv())
        assert auth["payload"]["token"] == config.token.get_secret_value()
        await connection.send(
            json.dumps(
                {
                    "type": "auth_result",
                    "payload": {
                        "success": True,
                        "license_status": "expired",
                        "quota": 0,
                    },
                }
            )
        )
        await connection.send(json.dumps({"type": "rpc_reply_ack", "payload": ["invalid"]}))
        await connection.send(json.dumps({"type": "rpc_call", "payload": command}))
        async for raw in connection:
            message = json.loads(raw)
            if message["type"] != "rpc_reply":
                continue
            results.append(message["payload"])
            if current == 1:
                await connection.close()
                return
            await connection.send(
                json.dumps(
                    {
                        "type": "rpc_reply_ack",
                        "payload": {
                            "request_id": "durable",
                        },
                    }
                )
            )
            if len(results) >= 3:
                await wait_until(lambda: not agent.journal.pending_results())
                await connection.close()
                return

    try:
        async with serve(controller, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            agent.config = config.model_copy(update={"master_url": f"http://127.0.0.1:{port}"})
            agent.tasks = [asyncio.create_task(agent.worker())]
            async with asyncio.timeout(10):
                await agent.websocket_session()
                assert len(agent.journal.pending_results()) == 1
                await agent.websocket_session()
        assert results == [results[0]] * 3
        assert results[0]["status"] == 200
        agent.operations.handle.assert_awaited_once()
        assert not agent.journal.pending_results()
    finally:
        await agent.close()


@pytest.mark.parametrize("payload", [{"success": False}, ["invalid"]])
async def test_websocket_rejects_failed_or_malformed_auth(config, payload):
    agent = Agent(config)

    async def controller(connection):
        await connection.recv()
        await connection.send(json.dumps({"type": "auth_result", "payload": payload}))

    try:
        async with serve(controller, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            agent.config = config.model_copy(update={"master_url": f"http://127.0.0.1:{port}"})
            with pytest.raises(AuthenticationRejected):
                await agent.websocket_session()
    finally:
        await agent.close()


async def test_websocket_reporting_failure_ends_the_session(config):
    agent = Agent(config)
    agent.websocket_reports = AsyncMock(side_effect=OSError("reporting unavailable"))

    async def controller(connection):
        await connection.recv()
        await connection.send(json.dumps({"type": "auth_result", "payload": {"success": True}}))
        await connection.wait_closed()

    try:
        async with serve(controller, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            agent.config = config.model_copy(update={"master_url": f"http://127.0.0.1:{port}"})
            async with asyncio.timeout(5):
                with pytest.raises(OSError, match="reporting"):
                    await agent.websocket_session()
        assert agent.websocket is None
    finally:
        await agent.close()


async def test_http_heartbeats_continue_and_failed_results_replay(config, monkeypatch):
    agent = Agent(config.model_copy(update={"heartbeat_seconds": 1, "poll_seconds": 0.2}))
    agent.collect_telemetry = AsyncMock(return_value={})
    agent.runtime.scan = AsyncMock(return_value={"xray_running": False})
    requests = []
    leased = False
    result_attempts = 0

    async def slow(_):
        await asyncio.sleep(1.2)
        assert len([path for path in requests if path.endswith("/heartbeat")]) >= 2
        return {"success": True}

    agent.operations.handle = AsyncMock(side_effect=slow)

    async def controller(request):
        nonlocal leased, result_attempts
        path = request.url.path
        requests.append(path)
        assert json.loads(request.content)["token"] == config.token.get_secret_value()
        if path.endswith("/lease"):
            commands = (
                []
                if leased
                else [
                    {
                        "id": "command-id",
                        "request_id": "http-request",
                        "method": "POST",
                        "path": "/api/child/scan",
                    }
                ]
            )
            leased = True
            return httpx.Response(200, json={"commands": commands})
        if path.endswith("/result"):
            result_attempts += 1
            if result_attempts == 1:
                raise httpx.ReadError("result acknowledgement lost", request=request)
            assert json.loads(request.content)["status"] == 200
        return httpx.Response(200, json={})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "open_node_agent.client.httpx.AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(controller),
            **kwargs,
        ),
    )
    try:
        with pytest.raises(httpx.ReadError):
            await agent.http_session(duration=3)
        assert len(agent.journal.pending_results()) == 1
        await agent.http_session(duration=0.3)
        assert result_attempts == 2
        assert agent.journal.pending_results() == []
        assert "/api/v1/agents/scan" in requests
        agent.operations.handle.assert_awaited_once()
    finally:
        await agent.close()
