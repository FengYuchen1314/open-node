import asyncio
import json
import os
import socket
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from open_node_agent import online
from open_node_agent.client import Agent
from open_node_agent.operations import apply_xray_system_config, xray_system_config


@pytest.fixture
def agent(config):
    config.xray_config.write_text(json.dumps({
        "api": {"tag": "api", "listen": "127.0.0.1:46736", "services": ["StatsService"]},
        "stats": {}, "policy": {"levels": {"0": {"statsUserOnline": True}}},
        "inbounds": [],
    }))
    instance = Agent(config)
    instance.runtime.running = AsyncMock(return_value=True)
    yield instance
    instance.runtime.log_handler.close()


def response(users):
    return 0, json.dumps({"users": ["user>>>" + email + ">>>online" for email in users]})


async def test_collects_official_counter_names_and_normalizes_ipv6(agent):
    runner = AsyncMock(side_effect=[response(["alice"]), (0, json.dumps({
        "name": "user>>>alice>>>online",
        "ips": {"198.51.100.2": "1", "[2001:db8::1]": "2", "::ffff:198.51.100.2": "3"},
    }))])
    sample = await online.collect_online(agent.runtime, runner)
    assert sample["online_users"] == {"alice": ["198.51.100.2", "2001:db8::1"]}
    assert sample["online_collection"]["status"] == "ready"
    assert runner.call_args_list[0].args[1:3] == ("api", "statsgetallonlineusers")
    assert runner.call_args_list[1].args[-1] == "-email=alice"
    assert runner.call_args.kwargs["timeout"] == 2.5


@pytest.mark.parametrize("payload", [{}, {"users": []}])
async def test_empty_official_response_is_valid_zero(agent, payload):
    runner = AsyncMock(return_value=(0, json.dumps(payload)))
    result = await online.collect_online(agent.runtime, runner)
    assert result["online_users"] == {}
    assert result["online_collection"]["status"] == "ready"


@pytest.mark.parametrize("output,expected", [
    ("rpc error: code = Unimplemented desc = secret", "unsupported"),
    ("xray api: unknown command", "unsupported"),
    ("dial tcp failed: secret", "error"),
])
async def test_failures_are_not_zero_online_and_do_not_disclose_cli_output(agent, output, expected):
    result = await online.collect_online(agent.runtime, AsyncMock(return_value=(1, output)))
    assert result["online_collection"]["status"] == expected
    assert result["online_users"] == {}
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("payload", [{"users": "alice"}, {"users": ["alice"]},
                                      {"users": ["user>>>bad\nname>>>online"]}, []])
async def test_malformed_enumeration_is_error(agent, payload):
    runner = AsyncMock(return_value=(0, json.dumps(payload)))
    result = await online.collect_online(agent.runtime, runner)
    assert result["online_collection"]["status"] == "error"


@pytest.mark.parametrize("code,body,status", [
    (1, "rpc error: code = NotFound desc = expired", "ready"),
    (1, "rpc error: code = Unimplemented", "unsupported"),
    (0, '{"ips":{"hostname.invalid":1}}', "error"),
    (0, '{"name":"user>>>bob>>>online", "ips":{}}', "error"),
])
async def test_closed_connection_race_and_per_user_errors(agent, code, body, status):
    runner = AsyncMock(side_effect=[response(["alice"]), (code, body)])
    result = await online.collect_online(agent.runtime, runner)
    assert result["online_collection"]["status"] == status
    assert result["online_users"] == {}


async def test_policy_coverage_and_runtime_readiness(agent):
    runner = AsyncMock(return_value=(0, "{}"))
    config = agent.runtime.read()
    config["inbounds"] = [{"settings": {"clients": [{"email": "other", "level": 2}]}}]
    agent.config.xray_config.write_text(json.dumps(config))
    sample = await online.collect_online(agent.runtime, runner)
    assert sample["online_collection"]["status"] == "limited"
    agent.runtime.running.return_value = False
    sample = await online.collect_online(agent.runtime, runner)
    assert sample["online_collection"]["status"] == "stopped"
    config["policy"]["levels"]["0"]["statsUserOnline"] = False
    agent.config.xray_config.write_text(json.dumps(config))
    sample = await online.collect_online(agent.runtime, runner)
    assert sample["online_collection"]["status"] == "not_configured"
    agent.config.stats_address = "198.51.100.1:46736"
    sample = await online.collect_online(agent.runtime, runner)
    assert sample["online_collection"]["status"] == "not_configured"
    assert runner.await_count == 1


async def test_deadline_cancels_inflight_queries_and_bounds_concurrency(agent, monkeypatch):
    monkeypatch.setattr(online, "COLLECTION_TIMEOUT", 0.04)
    active = peak = closed = 0

    async def runner(*args, **kwargs):
        nonlocal active, peak, closed
        if args[2] == "statsgetallonlineusers":
            return response([f"u{i}" for i in range(20)])
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
            closed += 1

    result = await online.collect_online(agent.runtime, runner)
    assert result["online_collection"]["status"] == "error"
    assert active == 0 and peak == closed == online.CONCURRENCY


async def test_user_ip_and_total_limits_are_explicit_partial_results(agent):
    async def runner(*args, **kwargs):
        if args[2] == "statsgetallonlineusers":
            return response([f"u{i:03}" for i in range(257)])
        return 0, json.dumps({"ips": {f"198.51.100.{i}": 1 for i in range(1, 66)}})
    result = await online.collect_online(agent.runtime, runner)
    assert result["online_collection"]["status"] == "limited"
    assert len(result["online_users"]) <= 256
    assert max(map(len, result["online_users"].values())) == 64
    assert sum(map(len, result["online_users"].values())) == 4096


async def test_agent_telemetry_forwards_collection_on_both_transports(agent):
    agent.runtime.stats = AsyncMock(return_value=None)
    agent.runtime.online_users = AsyncMock(return_value={
        "online_users": {"alice": ["198.51.100.2"]},
        "online_collection": {
            "status": "ready", "interval_seconds": 30, "source": "xray_stats_api",
        },
    })
    agent.runtime.limiter.status = AsyncMock(return_value={"available": False})
    sample = await agent.collect_telemetry()
    assert sample["online_users"] == {"alice": ["198.51.100.2"]}
    assert sample["online_collection"]["status"] == "ready"


def test_stats_toggle_enables_online_on_every_numeric_level():
    current = {"policy": {"levels": {"0": {}, "2": {"handshake": 5}}}}
    description = xray_system_config(current)
    keys = {"log_level", "dns", "policy", "metrics_enabled", "metrics_listen",
            "stats_enabled", "grpc_enabled", "grpc_port"}
    payload = {key: description[key] for key in keys}
    payload.update(stats_enabled=True, grpc_enabled=True)
    changed = apply_xray_system_config(current, payload)
    assert all(level["statsUserOnline"] for level in changed["policy"]["levels"].values())
    assert changed["policy"]["levels"]["2"]["handshake"] == 5


async def test_real_xray_active_ip_appears_and_disappears(agent):
    binary = os.environ.get("OPEN_NODE_TEST_XRAY")
    if not binary:
        pytest.skip("Set OPEN_NODE_TEST_XRAY to a verified Xray binary on the isolated VPS")

    def free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    async def echo(reader, writer):
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    api_port, inbound_port = free_port(), free_port()
    identifier = uuid4()
    configuration = agent.runtime.read()
    configuration["api"]["listen"] = f"127.0.0.1:{api_port}"
    configuration["inbounds"] = [{"tag": "test", "listen": "127.0.0.1", "port": inbound_port,
        "protocol": "vless", "settings": {"decryption": "none", "clients": [
            {"id": str(identifier), "email": "online-smoke", "level": 0},
        ]}}]
    configuration["outbounds"] = [{"protocol": "freedom", "tag": "direct"}]
    agent.config.xray_config.write_text(json.dumps(configuration))
    agent.runtime.binary = Path(binary)
    process = await asyncio.create_subprocess_exec(
        binary, "run", "-c", str(agent.config.xray_config),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    writer = None
    try:
        async with asyncio.timeout(12):
            for _ in range(80):
                try:
                    reader, writer = await asyncio.open_connection(
                        "127.0.0.1", inbound_port, local_addr=("127.0.0.2", 0),
                    )
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            assert writer is not None
            async with await asyncio.start_server(echo, "127.0.0.1", 0) as target:
                target_port = target.sockets[0].getsockname()[1]
                writer.write(bytes([0]) + identifier.bytes + bytes([0, 1])
                             + target_port.to_bytes(2, "big") + bytes([1, 127, 0, 0, 1]) + b"hello")
                await writer.drain()
                assert await reader.readexactly(7) == b"\x00\x00hello"
                current = await agent.runtime.online_users()
                assert current["online_collection"]["status"] == "ready"
                assert current["online_users"] == {"online-smoke": ["127.0.0.2"]}
                writer.close()
                await writer.wait_closed()
                for _ in range(20):
                    current = await agent.runtime.online_users()
                    if not current["online_users"]:
                        break
                    await asyncio.sleep(0.05)
                assert current["online_collection"]["status"] == "ready"
                assert current["online_users"] == {}
    finally:
        if writer:
            writer.close()
        if process.returncode is None:
            process.terminate()
        await process.wait()
