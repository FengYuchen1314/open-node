import asyncio
import json
import logging
import os
import socket
import sys
from unittest.mock import AsyncMock

import pytest
from open_node_agent import diagnostics
from open_node_agent.client import Agent
from open_node_agent.diagnostics import Diagnostics, LatencyRequest, latency_target, probe_process
from open_node_agent.logs import OwnedLogs, configure_agent_log
from open_node_agent.route_trace import RouteRequest, decode_trace, trace_result
from open_node_agent.runtime import RuntimeFailure


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM/a?q=1", ("example.com", 443, "example.com:443")),
        ("localhost:1234", ("localhost", 1234, "localhost:1234")),
        ("[::1]:8443", ("::1", 8443, "[::1]:8443")),
        ("::1", ("::1", 443, "[::1]:443")),
        ("[2001:db8::1]", ("2001:db8::1", 443, "[2001:db8::1]:443")),
    ],
)
def test_latency_targets_use_standard_url_and_ip_parsing(raw, expected):
    assert latency_target(raw) == expected


@pytest.mark.parametrize(
    "value",
    [
        "--help",
        "host;id",
        "host$(id)",
        "a b",
        "host\nname",
        "https://user:secret@host",
        "host:0",
        "host:65536",
        "host:no",
        "http:///oops",
        "file:///etc/passwd",
        "fe80::1%eth0",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::",
    ],
)
def test_invalid_probe_targets_are_rejected(value):
    with pytest.raises(ValueError):
        latency_target(value)


async def test_real_tcp_latency_deduplicates_and_closes_connections(config):
    accepted = 0

    async def accept(reader, writer):
        nonlocal accepted
        accepted += 1
        await reader.read()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(accept, "127.0.0.1", 0) as server:
        target = "127.0.0.1:" + str(server.sockets[0].getsockname()[1])
        result = await Diagnostics(config).latency({"domains": [target, target]})
        assert result["count"] == 1
        sample = result["results"][0]
        assert sample["success"] and sample["method"] == "tcp"
        assert sample["key"] == target and sample["latency_ms"] >= 0
        await asyncio.sleep(0.02)
        assert accepted == 1


async def test_dns_failure_and_icmp_permission_are_not_network_success(config, monkeypatch):
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=socket.gaierror()))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/ping")
    runner = AsyncMock(return_value=(2, "", "ping: operation not permitted"))
    monkeypatch.setattr(diagnostics, "probe_process", runner)
    result = await Diagnostics(config).latency({"domains": ["test.invalid"], "allow_icmp": True})
    sample = result["results"][0]
    assert not sample["success"]
    assert sample["error"] == "DNS resolution failed"
    assert "permitted" in sample["icmp_error"]


async def test_icmp_fallback_is_explicit_and_preserves_tcp_failure(config, monkeypatch):
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=ConnectionRefusedError()))
    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: "/usr/bin/ping")
    runner = AsyncMock(return_value=(0, "64 bytes from 127.0.0.1: time=0.028 ms", ""))
    monkeypatch.setattr(diagnostics, "probe_process", runner)
    result = await Diagnostics(config).latency(
        {
            "domains": ["127.0.0.1:12345"],
            "allow_icmp": True,
            "timeout_ms": 200,
        }
    )
    sample = result["results"][0]
    assert sample["success"] and sample["method"] == "icmp"
    assert sample["tcp_error"] == "TCP connection refused"
    assert runner.call_args.args[-2:] == ("--", "127.0.0.1")


async def test_latency_concurrency_is_bounded_and_cancellation_drains_tasks(config, monkeypatch):
    active, peak = 0, 0

    async def connect(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(30)
        finally:
            active -= 1

    monkeypatch.setattr(asyncio, "open_connection", connect)
    task = asyncio.create_task(
        Diagnostics(config).latency({"domains": [f"host{index}.invalid" for index in range(40)]})
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0 and peak == 16


async def test_probe_tool_output_and_time_are_bounded(tmp_path):
    pid = tmp_path / "pid"
    with pytest.raises(TimeoutError):
        await probe_process(
            sys.executable,
            "-c",
            f"import os,time;open({str(pid)!r},'w').write(str(os.getpid()));time.sleep(30)",
            timeout=0.3,
        )
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)
    with pytest.raises(RuntimeFailure, match="output limit"):
        await probe_process(sys.executable, "-c", "print('x' * 12000000)", timeout=3)


def hop(ip, ttl=1, asn="", country=""):
    return {
        "Success": True,
        "Address": {"IP": ip},
        "TTL": ttl,
        "RTT": 1234567,
        "Geo": {"asnumber": asn, "country_en": country},
    }


def test_nexttrace_json_retains_evidence_without_inventing_service_tiers():
    result = trace_result(
        {
            "Hops": [
                [hop("192.0.2.1", asn="AS4809", country="Hong Kong")],
                [hop("192.0.2.2", ttl=2, asn="4134", country="China")],
            ]
        },
        "192.0.2.2",
    )
    assert result["success"] and result["reached"]
    assert result["route_type"] == "CN2"
    assert result["entry_hop"]["ip"] == "192.0.2.2"
    assert result["path_asns"] == ["4809", "4134"]
    assert "unverified" in result["reason"]
    assert result["hops"][0]["rtt_ms"] == 1.235


def test_unresponsive_and_unlocated_routes_remain_unknown():
    result = trace_result({"Hops": [[{"Success": False}], [hop("::1")]]}, "::1")
    assert result["success"] and result["reached"]
    assert result["route_type"] == "Unknown" and result["entry_hop"] is None
    assert not trace_result({"Hops": []}, "::1")["success"]
    with pytest.raises(ValueError):
        trace_result({"Hops": ["bad"]}, "::1")


def test_nexttrace_non_root_advisory_does_not_hide_valid_json():
    assert decode_trace('Capability advisory\n{"Hops": []}\n') == {"Hops": []}
    assert decode_trace('{invalid banner}\n{"Hops": []}\n') == {"Hops": []}
    with pytest.raises(ValueError):
        decode_trace('{"Hops": []}\ntrailing junk')
    with pytest.raises(ValueError):
        decode_trace('{"not_a_trace": []}')


async def test_route_tool_missing_and_failed_are_explicit(config, monkeypatch):
    request = {"targets": [{"carrier": "telecom", "host": "127.0.0.1", "port": 80}]}
    probe = Diagnostics(config)
    result = await probe.return_route(request)
    assert result["results"][0]["success"] is False
    assert not probe.route_available()
    monkeypatch.setattr(probe, "route_available", lambda: True)
    monkeypatch.setattr(
        diagnostics, "probe_process", AsyncMock(return_value=(1, "", "permission denied"))
    )
    assert "permission" in (await probe.return_route(request))["results"][0]["error"]
    monkeypatch.setattr(diagnostics, "probe_process", AsyncMock(side_effect=TimeoutError()))
    assert "timed out" in (await probe.return_route(request))["results"][0]["error"]
    monkeypatch.setattr(
        diagnostics,
        "probe_process",
        AsyncMock(
            return_value=(
                0,
                json.dumps(
                    {
                        "Hops": [[hop("127.0.0.1")]],
                    }
                ),
                "",
            )
        ),
    )
    result = (await probe.return_route(request))["results"][0]
    assert result["success"] and result["reached"]


@pytest.mark.parametrize(
    "body",
    [
        {"targets": []},
        {"targets": [{"carrier": "telecom", "host": "--file=/etc/passwd"}]},
        {"targets": [{"carrier": "mobile", "host": "example.com", "port": 0}]},
        {"ip_version": 5, "targets": [{"carrier": "unicom", "host": "::1"}]},
    ],
)
def test_route_request_rejects_invalid_or_command_line_input(body):
    with pytest.raises(ValueError):
        RouteRequest.model_validate(body)


def test_probe_request_limits():
    with pytest.raises(ValueError):
        LatencyRequest(domains=["host"] * 201)
    with pytest.raises(ValueError):
        LatencyRequest(domains=["host"], timeout_ms=0)


async def test_log_purge_preserves_state_and_live_writers(config):
    agent = Agent(config)
    try:
        sentinel = config.state_dir / "credentials.json"
        sentinel.write_text("private")
        archive = config.state_dir / "xray.log.1"
        archive.write_text("old logs")
        agent.runtime.log.warning("before")
        inode = (config.state_dir / "xray.log").stat().st_ino
        logs = OwnedLogs(config)
        assert "credentials.json" not in str(logs.list())
        result = logs.delete({"all": ["1"]})
        assert result["success"] and result["freed"] > 0
        assert not archive.exists() and sentinel.read_text() == "private"
        assert (config.state_dir / "xray.log").stat().st_ino == inode
        agent.runtime.log.warning("after")
        assert logs.tail({"service": ["xray"]})["logs"] == "after"
        assert (config.state_dir / "commands.sqlite").exists()
    finally:
        await agent.close()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
async def test_log_file_boundaries(config, kind):
    agent = Agent(config)
    try:
        victim = config.state_dir.parent / "outside"
        victim.write_text("preserve")
        unsafe = config.state_dir / "agent.log"
        if kind == "symlink":
            unsafe.symlink_to(victim)
        elif kind == "hardlink":
            os.link(victim, unsafe)
        else:
            os.mkfifo(unsafe)
        logs = OwnedLogs(config)
        assert "agent.log" not in str(logs.list())
        with pytest.raises((OSError, ValueError)):
            logs.tail({})
        assert not logs.delete({"all": ["1"]})["success"]
        assert victim.read_text() == "preserve"
        with pytest.raises(ValueError):
            logs.delete({"name": ["../outside"]})
    finally:
        await agent.close()


async def test_agent_log_rotation_redaction_and_line_limit(config):
    agent = Agent(config)
    handler = configure_agent_log(config)
    logger = logging.getLogger("open-node-agent")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info("token %s", config.token.get_secret_value())
        logs = OwnedLogs(config)
        assert config.token.get_secret_value() not in logs.tail({})["logs"]
        assert "[redacted]" in logs.tail({})["logs"]
        handler.doRollover()
        logger.info("first\nsecond")
        assert logs.tail({"lines": ["1"]})["logs"] == "second"
        assert {item["name"] for item in logs.list()["files"]} >= {"agent.log", "agent.log.1"}
        logs.delete({"all": ["1"]})
        logger.info("new entry")
        assert "new entry" in logs.tail({})["logs"]
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
        await agent.close()
