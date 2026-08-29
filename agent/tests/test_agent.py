import asyncio
import json
import sys
from unittest.mock import AsyncMock

import pytest
from open_node_agent.client import Agent
from open_node_agent.config import AgentConfig, load_config
from open_node_agent.journal import CommandJournal
from open_node_agent.operations import edit_client, edit_entries, edit_routing, telemetry
from open_node_agent.runtime import RuntimeFailure, run_command


def command(**kwargs):
    return {"request_id": "request-1", "method": "GET", "path": "/api/child/xray/config", **kwargs}


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "ftp://remote.example",
        "https://a:b@example.com",
        "https://example.com?token=secret",
    ],
)
def test_unsafe_control_plane_urls_are_rejected(url):
    with pytest.raises(ValueError):
        AgentConfig(master_url=url, token="secret")


def test_tls_and_secret_configuration(tmp_path):
    with pytest.raises(ValueError) as error:
        AgentConfig(master_url="http://remote.example", token="must-not-appear")
    assert "must-not-appear" not in str(error.value)
    config = AgentConfig(master_url="https://control.example/prefix/", token="node-secret")
    assert config.websocket_url() == "wss://control.example/prefix/api/v1/agents/ws"
    assert "node-secret" not in repr(config)
    assert config.tls_context().check_hostname
    with pytest.raises(ValueError):
        AgentConfig(
            master_url="https://control.example", token="secret", master_public_key="legacy-key"
        )
    path = tmp_path / "config.yaml"
    path.write_text("master_url: https://control.example\ntoken: secret\n")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        load_config(path)
    path.chmod(0o600)
    assert load_config(path).master_url == "https://control.example"
    path.write_text("token: [secret,\n")
    with pytest.raises(ValueError) as malformed:
        load_config(path)
    assert "secret" not in str(malformed.value)


def test_state_lock_and_private_permissions(tmp_path):
    directory = tmp_path / "state"
    journal = CommandJournal(directory)
    try:
        with pytest.raises(RuntimeError, match="Another agent"):
            CommandJournal(directory)
        assert (directory / "commands.sqlite").stat().st_mode & 0o777 == 0o600
    finally:
        journal.close()
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        CommandJournal(directory)


async def test_commands_are_deduplicated_across_concurrency_and_restart(config):
    agent = Agent(config)
    handler = AsyncMock(return_value={"success": True})
    agent.operations.handle = handler
    try:
        first, second = await asyncio.gather(agent.execute(command()), agent.execute(command()))
        assert first == second
        assert handler.await_count == 1
        assert len(agent.journal.pending_results()) == 1
        agent.journal.acknowledge("request-1")
        assert agent.journal.pending_results() == []
    finally:
        await agent.close()
    restarted = Agent(config)
    restarted.operations.handle = handler
    try:
        assert await restarted.execute(command()) == first
        assert handler.await_count == 1
        conflict = await restarted.execute(command(path="/api/child/scan", method="POST"))
        assert conflict["status"] == 409
        assert handler.await_count == 1
    finally:
        await restarted.close()


async def test_interrupted_commands_are_not_blindly_repeated(config):
    agent = Agent(config)
    agent.journal.begin(command(query="", body=None, stream=False))
    await agent.close()
    restarted = Agent(config)
    restarted.operations.handle = AsyncMock()
    try:
        response = await restarted.execute(command())
        assert response["status"] == 409
        assert "interrupted" in response["error"]
        restarted.operations.handle.assert_not_awaited()
    finally:
        await restarted.close()


async def test_command_timeouts_are_persisted(config):
    agent = Agent(config)

    async def slow(_):
        await asyncio.sleep(60)

    agent.operations.handle = AsyncMock(side_effect=slow)
    try:
        response = await agent.execute(command(timeout_ms=1000))
        assert response["status"] == 504
        assert await agent.execute(command(timeout_ms=1000)) == response
        assert agent.operations.handle.await_count == 1
    finally:
        await agent.close()


async def test_long_failure_can_be_accepted_by_control_plane(config):
    agent = Agent(config)
    agent.operations.handle = AsyncMock(side_effect=ValueError("x" * 10000))
    try:
        result = await agent.execute(command())
        assert result["status"] == 400
        assert len(result["error"]) == 2048
        assert agent.journal.pending_results() == [result]
    finally:
        await agent.close()


async def test_unimplemented_and_unexpected_operations_do_not_report_success(config):
    agent = Agent(config)
    try:
        unsupported = await agent.execute(command(path="/api/child/warp/install"))
        assert unsupported["status"] == 501
        agent.operations.handle = AsyncMock(side_effect=[RuntimeError("bug"), {"success": True}])
        assert (await agent.execute(command(request_id="unexpected")))["status"] == 500
        assert (await agent.execute(command(request_id="next")))["status"] == 200
    finally:
        await agent.close()


async def test_configuration_validation_cannot_be_forced_off(config):
    agent = Agent(config)
    old = config.xray_config.read_bytes()
    agent.runtime.validate = AsyncMock(return_value=(False, "invalid runtime config"))
    try:
        result = await agent.execute(
            command(method="POST", body={"config": {"bad": True}, "force": True})
        )
        assert result["status"] == 400
        assert config.xray_config.read_bytes() == old
        bad_path = await agent.execute(
            command(request_id="other-path", body={"path": "/etc/passwd"})
        )
        assert bad_path["status"] == 400
    finally:
        await agent.close()


@pytest.mark.parametrize("error", [RuntimeFailure("bind failed"), asyncio.CancelledError()])
async def test_failed_runtime_restart_restores_previous_config(config, error):
    agent = Agent(config)
    old = config.xray_config.read_bytes()
    config.xray_config.chmod(0o640)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=True)
    agent.runtime.restart = AsyncMock(side_effect=[error, None])
    try:
        with pytest.raises(type(error)):
            await agent.runtime.write({"inbounds": [], "outbounds": []}, restart=True)
        assert config.xray_config.read_bytes() == old
        assert config.xray_config.stat().st_mode & 0o777 == 0o640
        assert agent.runtime.restart.await_count == 2
    finally:
        await agent.close()


async def test_batch_failure_does_not_partially_write(config):
    config.xray_config.write_text(
        json.dumps(
            {"inbounds": [{"tag": "vless", "protocol": "vless", "settings": {"clients": []}}]}
        )
    )
    old = config.xray_config.read_bytes()
    agent = Agent(config)
    agent.runtime.write = AsyncMock()
    try:
        result = await agent.execute(
            command(
                method="POST",
                path="/api/child/batch-apply",
                body={
                    "inbound_clients": [
                        {"tag": "vless", "client": {"email": "alice", "id": "id"}},
                        {"tag": "missing", "client": {"email": "bob", "id": "id"}},
                    ],
                },
            )
        )
        assert result["status"] == 400
        assert config.xray_config.read_bytes() == old
        agent.runtime.write.assert_not_awaited()
    finally:
        await agent.close()


async def test_service_stop_intent_survives_agent_restart(config):
    agent = Agent(config)
    agent.runtime.stop = AsyncMock()
    try:
        result = await agent.execute(
            command(
                method="POST",
                path="/api/child/services/control",
                body={"service": "xray", "action": "stop"},
            )
        )
        assert result["body"]["success"] is True
    finally:
        await agent.close()
    restarted = Agent(config)
    try:
        assert restarted.journal.desired_running(True) is False
    finally:
        await restarted.close()


def test_client_and_routing_changes_preserve_other_entries():
    inbound = {"protocol": "vless", "settings": {"clients": [{"email": "other", "id": "1"}]}}
    edit_client(inbound, {"email": "alice", "id": "2"})
    edit_client(inbound, {"email": "alice", "id": "3"})
    assert inbound["settings"]["clients"] == [
        {"email": "other", "id": "1"},
        {"email": "alice", "id": "3"},
    ]
    edit_client(inbound, {"email": "alice"}, remove=True)
    assert inbound["settings"]["clients"] == [{"email": "other", "id": "1"}]
    config = {
        "routing": {"rules": [{"marktag": "routed", "user": ["alice"], "outboundTag": "proxy"}]}
    }
    edit_routing(
        config, {"action": "remove_user_from_rule", "marktag": "routed", "user_email": "alice"}
    )
    assert config["routing"]["rules"] == []


def test_reordering_requires_complete_unique_tags():
    config = {"outbounds": [{"tag": "direct"}, {"tag": "proxy"}]}
    with pytest.raises(RuntimeFailure):
        edit_entries(config, "outbounds", {"action": "reorder", "tags": ["direct", "direct"]})
    edit_entries(config, "outbounds", {"action": "reorder", "tags": ["proxy", "direct"]})
    assert config["outbounds"][0]["tag"] == "proxy"


def test_private_route_cleanup_retries_are_strictly_idempotent():
    outbound = {"tag": "private:alice:1", "protocol": "freedom"}
    config = {"outbounds": [outbound], "routing": {"rules": []}}
    edit_entries(
        config,
        "outbounds",
        {"action": "add", "outbound": outbound, "allow_existing": True},
    )
    with pytest.raises(RuntimeFailure, match="already exists"):
        edit_entries(
            config,
            "outbounds",
            {
                "action": "add",
                "outbound": {**outbound, "protocol": "blackhole"},
                "allow_existing": True,
            },
        )
    edit_entries(
        config,
        "outbounds",
        {"action": "remove", "tag": outbound["tag"], "ignore_missing": True},
    )
    edit_entries(
        config,
        "outbounds",
        {"action": "remove", "tag": outbound["tag"], "ignore_missing": True},
    )

    rule = {
        "type": "field",
        "marktag": outbound["tag"],
        "user": ["alice"],
        "outboundTag": outbound["tag"],
    }
    edit_routing(config, {"action": "add_rule", "rule": rule, "allow_existing": True})
    edit_routing(config, {"action": "add_rule", "rule": rule, "allow_existing": True})
    edit_routing(
        config,
        {"action": "remove_rule", "marktag": rule["marktag"], "ignore_missing": True},
    )
    edit_routing(
        config,
        {"action": "remove_rule", "marktag": rule["marktag"], "ignore_missing": True},
    )


async def test_subprocess_output_and_time_are_bounded():
    with pytest.raises(RuntimeFailure, match="output limit"):
        await run_command(sys.executable, "-c", "print('x' * 300000)")
    with pytest.raises(TimeoutError):
        await run_command(sys.executable, "-c", "import time; time.sleep(10)", timeout=0.1)


def test_host_telemetry_has_nonnegative_counters():
    report = telemetry()
    assert report["system"]["rx_total"] >= 0
    assert report["sysmetrics"]["mem_total"] > 0
    assert report["sysmetrics"]["has_cpu"] is True


async def test_speed_is_a_rate_not_an_accumulated_counter(config, monkeypatch):
    agent = Agent(config)
    counters = {"rx_total": 1000, "tx_total": 2000, "boot_time_unix": 100}
    clock = [10.0]
    monkeypatch.setattr("open_node_agent.operations.telemetry", lambda: {"system": dict(counters)})
    monkeypatch.setattr("open_node_agent.operations.monotonic", lambda: clock[0])
    try:
        operation = {"method": "GET", "path": "/api/child/speed"}
        assert await agent.operations.handle(operation) == {
            "success": True,
            "upload_speed": 0,
            "download_speed": 0,
        }
        counters.update(rx_total=1800, tx_total=4000)
        clock[0] = 12.0
        assert await agent.operations.handle(operation) == {
            "success": True,
            "upload_speed": 1000,
            "download_speed": 400,
        }
        clock[0] = 14.0
        counters.update(rx_total=10, tx_total=0)
        assert (await agent.operations.handle(operation))["download_speed"] == 0
        clock[0] = 15.0
        counters.update(rx_total=5000, boot_time_unix=200)
        assert (await agent.operations.handle(operation))["download_speed"] == 0
    finally:
        await agent.close()
