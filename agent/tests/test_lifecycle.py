import asyncio
import json

import pytest
from open_node_agent import lifecycle_host as host
from open_node_agent import lifecycle_protocol as protocol
from open_node_agent.client import Agent
from open_node_agent.lifecycle_report import deliver


def command(request_id="upgrade-request", **overrides):
    return {
        "request_id": request_id,
        "method": "POST",
        "path": "/api/child/agent/upgrade-stream",
        "query": "",
        "body": {"version": "0.2.0", "sha256": "a" * 64},
        "stream": True,
        **overrides,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": "../foreign"},
        {"request_id": "--help"},
        {"request_id": ".."},
        {"method": "GET"},
        {"path": "/api/child/system/info"},
        {"path": []},
        {"query": {}},
        {"query": "shell=true"},
        {"stream": "true"},
        {"body": None},
        {"body": {"version": "latest", "sha256": "a" * 64}},
        {"body": {"version": "0.2.0", "sha256": "A" * 64}},
        {"body": {"version": "0.2.0", "sha256": "a" * 64, "url": "https://elsewhere.invalid"}},
        {"path": "/api/child/agent/uninstall-stream", "body": {}},
        {"path": "/api/child/agent/rollback", "body": {"confirm": "true"}},
    ],
)
def test_host_protocol_rejects_unapproved_input(changes):
    with pytest.raises(ValueError):
        protocol.validate_command(command(**changes))


def test_host_job_identity_is_durable_and_deduplicated(tmp_path):
    store = host.JobStore(tmp_path)
    original = command()
    job = store.submit(original)
    assert job["status"] == "queued"
    assert store.submit(original)["fingerprint"] == job["fingerprint"]
    with pytest.raises(ValueError, match="different content"):
        store.submit(command(body={"version": "0.2.1", "sha256": "b" * 64}))
    with pytest.raises(ValueError, match="active"):
        store.submit(command("second-request"))
    store.started(original["request_id"])
    recovered = host.JobStore(tmp_path)
    assert recovered.get(original["request_id"])["status"] == "running"
    result = {"request_id": original["request_id"], "status": 200, "body": {"success": True}}
    recovered.finish(original["request_id"], result)
    recovered.finish(original["request_id"], {**result, "status": 500})
    assert recovered.submit(original)["result"] == result
    assert not recovered.get(original["request_id"])["reported"]
    recovered.acknowledge(original["request_id"])
    assert recovered.get(original["request_id"])["reported"]
    assert recovered.submit(command("second-request"))["status"] == "queued"


@pytest.mark.parametrize(
    "source",
    ["http://example.com", "https://user:secret@example.com", "https://example.com/?token=secret"],
)
def test_release_source_requires_host_approved_https(source):
    with pytest.raises(host.service.DeploymentError, match="HTTPS"):
        host.validate_base_url(source)


def test_reporter_never_reads_agent_configuration_as_root(tmp_path):
    with pytest.raises(ValueError, match="Agent account"):
        deliver(tmp_path / "unreadable.json", {})


async def test_deferred_agent_command_does_not_claim_success_and_resumes(config, tmp_path):
    config.lifecycle_socket = tmp_path / "lifecycle.sock"
    seen = []
    final = None

    async def handle(reader, writer):
        request = json.loads(await reader.readline())
        seen.append(request)
        writer.write(json.dumps({"ok": True, "result": final}).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=config.lifecycle_socket)
    agent = Agent(config)
    try:
        payload = command()
        assert await agent.execute(payload) is None
        assert not agent.journal.pending_results()
        assert await agent.execute(payload) is None
        assert len(seen) == 2
        final = {
            "request_id": payload["request_id"],
            "status": 500,
            "error": "Candidate failed; old Agent restored",
            "body": {"success": False, "current": "old"},
        }
        assert await agent.execute(payload) == final
        assert await agent.execute(payload) == final
        assert len(seen) == 3
        assert agent.journal.pending_results() == [final]
    finally:
        await agent.close()
        server.close()
        await server.wait_closed()


async def test_lost_submission_reply_remains_unresolved_instead_of_false_failure(config, tmp_path):
    config.lifecycle_socket = tmp_path / "lifecycle.sock"

    async def handle(reader, writer):
        await reader.readline()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=config.lifecycle_socket)
    agent = Agent(config)
    try:
        assert await agent.execute(command()) is None
        assert not agent.journal.pending_results()
    finally:
        await agent.close()
        server.close()
        await server.wait_closed()


async def test_disabled_or_unavailable_helper_fails_without_delegating(config, tmp_path):
    agent = Agent(config)
    try:
        result = await agent.execute(command())
        assert result["status"] == 400
        assert "host owner" in result["error"]
        config.lifecycle_socket = tmp_path / "missing.sock"
        result = await agent.execute(command("second-request"))
        assert result["status"] == 400
        assert "unavailable" in result["error"]
    finally:
        await agent.close()


async def test_short_lease_cannot_fail_an_accepted_host_job(config, tmp_path):
    config.lifecycle_socket = tmp_path / "lifecycle.sock"

    async def handle(reader, writer):
        await reader.readline()
        await asyncio.sleep(1.2)
        writer.write(b'{"ok":true,"result":null}\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=config.lifecycle_socket)
    agent = Agent(config)
    try:
        assert await agent.execute(command(timeout_ms=1000)) is None
        assert not agent.journal.pending_results()
    finally:
        await agent.close()
        server.close()
        await server.wait_closed()


async def test_cancelled_submission_remains_unresolved_for_redelivery(config, tmp_path):
    config.lifecycle_socket = tmp_path / "lifecycle.sock"
    accepted = asyncio.Event()
    finished = asyncio.Event()

    async def handle(reader, writer):
        try:
            await reader.readline()
            accepted.set()
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server = await asyncio.start_unix_server(handle, path=config.lifecycle_socket)
    agent = Agent(config)
    try:
        task = asyncio.create_task(agent.execute(command()))
        await asyncio.wait_for(accepted.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not agent.journal.pending_results()
        await asyncio.wait_for(finished.wait(), timeout=2)
    finally:
        await agent.close()
        server.close()
        await server.wait_closed()
