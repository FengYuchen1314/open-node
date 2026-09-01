import asyncio
import copy
import hashlib
import json
import os
import sys
from unittest.mock import AsyncMock

import httpx
import open_node_agent.runtime as runtime_module
import pytest
import yaml
from open_node_agent.client import Agent
from open_node_agent.config import AgentConfig, load_config
from open_node_agent.journal import CommandJournal
from open_node_agent.operations import (
    apply_xray_system_config,
    edit_client,
    edit_entries,
    edit_routing,
    telemetry,
    xray_system_config,
)
from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure, run_command


def command(**kwargs):
    return {"request_id": "request-1", "method": "GET", "path": "/api/child/xray/config", **kwargs}


def config_sha256(config) -> str:
    return hashlib.sha256(config.xray_config.read_bytes()).hexdigest()


def system_config_payload(**updates) -> dict:
    payload = {
        "log_level": "warning",
        "dns": {},
        "policy": {},
        "metrics_enabled": False,
        "metrics_listen": "127.0.0.1:11111",
        "stats_enabled": False,
        "grpc_enabled": False,
        "grpc_port": 46736,
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "ftp://remote.example",
        "https://a:b@example.com",
        "https://example.com?token=secret",
        "https://example.com:invalid",
        "https://example.com/path\nrecovery_url:https://evil.example",
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


async def test_public_ip_https_port_is_preserved_for_http_and_websocket(config, monkeypatch):
    public = AgentConfig.model_validate(
        {**config.model_dump(), "master_url": "https://1.1.1.1:58090"}
    )
    assert public.master_url == "https://1.1.1.1:58090"
    assert public.websocket_url() == "wss://1.1.1.1:58090/api/v1/agents/ws"

    requests = []

    async def controller(request):
        requests.append(str(request.url))
        return httpx.Response(200, json={})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "open_node_agent.client.httpx.AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(controller),
            **kwargs,
        ),
    )
    agent = Agent(public)
    try:
        await agent.http_session(duration=0)
    finally:
        await agent.close()

    assert requests[0] == "https://1.1.1.1:58090/api/v1/agents/register"
    assert all(url.startswith("https://1.1.1.1:58090/api/v1/agents/") for url in requests)


async def test_master_url_update_is_private_atomic_and_recovery_guarded(config, tmp_path):
    source = tmp_path / "agent.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "master_url": "https://recovery.example",
                "recovery_url": "https://recovery.example/",
                "token": config.token.get_secret_value(),
                "state_dir": str(config.state_dir),
                "xray_config": str(config.xray_config),
                "auto_start": False,
            },
            sort_keys=False,
        )
    )
    source.chmod(0o600)
    loaded = load_config(source)
    agent = Agent(loaded)
    try:
        assert agent.registration()["capabilities"]["agent_update_master_url"] is True
        updated = await agent.execute(
            {
                "request_id": "update-master",
                "method": "POST",
                "path": "/api/child/agent/update-master-url",
                "body": {
                    "master_url": "https://control.example/new-prefix/",
                    "only_if_recovery": True,
                },
            }
        )
        assert updated["status"] == 200
        assert updated["body"]["updated"] is True
        assert loaded.master_url == "https://control.example/new-prefix"
        persisted = yaml.safe_load(source.read_text())
        assert persisted["master_url"] == "https://control.example/new-prefix"
        assert persisted["token"] == config.token.get_secret_value()
        assert source.stat().st_mode & 0o777 == 0o600
        connection = AsyncMock()
        agent.websocket = connection
        await agent._reconnect_if_requested("update-master")
        connection.close.assert_awaited_once_with(
            code=1000, reason="master URL updated"
        )

        preserved = await agent.execute(
            {
                "request_id": "preserve-working-master",
                "method": "POST",
                "path": "/api/child/agent/update-master-url",
                "body": {
                    "master_url": "https://other.example",
                    "only_if_recovery": True,
                },
            }
        )
        assert preserved["status"] == 200
        assert preserved["body"]["unchanged"] is True
        assert yaml.safe_load(source.read_text())["master_url"] == (
            "https://control.example/new-prefix"
        )
    finally:
        await agent.close()


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


async def test_xray_system_config_read_write_is_atomic_and_preserves_unknown_fields(config):
    original = {
        "log": {"loglevel": "warning", "access": "none", "dnsLog": True},
        "dns": {"servers": ["1.1.1.1"]},
        "policy": {
            "levels": {
                "1": {"bufferSize": 0},
                "2": {"handshake": 4},
                "named": {"preserved": True},
            },
            "system": {"preserved": "system"},
            "preserved": "policy",
        },
        "api": {
            "tag": "custom-api",
            "listen": "[::1]:46736",
            "services": ["HandlerService"],
        },
        "inbounds": [],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    config.xray_config.write_text(json.dumps(original))
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=False)
    try:
        read = await agent.execute(
            command(request_id="system-read", path="/api/child/xray/system-config")
        )
        assert read["status"] == 200
        assert read["body"]["config"] == {
            "log_level": "warning",
            "dns": original["dns"],
            "policy": original["policy"],
            "metrics_enabled": False,
            "metrics_listen": "127.0.0.1:11111",
            "stats_enabled": False,
            "grpc_enabled": True,
            "grpc_port": 46736,
            "api_mode": "direct",
            "grpc_disable_supported": True,
            "grpc_port_writable": True,
            "fixed_stats_address": None,
            "writable": True,
            "read_only_reason": None,
        }
        assert read["body"]["sha256"] == config_sha256(config)

        written = await agent.execute(
            command(
                request_id="system-write",
                method="POST",
                path="/api/child/xray/system-config",
                body={
                    **system_config_payload(
                        log_level="info",
                        dns={"servers": ["8.8.8.8"], "queryStrategy": "UseIPv4"},
                        policy=original["policy"],
                        metrics_enabled=True,
                        metrics_listen="[::1]:11112",
                        stats_enabled=True,
                        grpc_enabled=True,
                        grpc_port=46737,
                    ),
                    "expected_sha256": read["body"]["sha256"],
                },
            )
        )
        assert written["status"] == 200
        updated = json.loads(config.xray_config.read_text())
        assert updated["log"] == {**original["log"], "loglevel": "info"}
        assert updated["dns"] == {
            "servers": ["8.8.8.8"],
            "queryStrategy": "UseIPv4",
        }
        assert updated["metrics"] == {"listen": "[::1]:11112"}
        assert updated["stats"] == {}
        assert updated["api"] == {
            "tag": "custom-api",
            "listen": "[::1]:46737",
            "services": ["HandlerService", "StatsService"],
        }
        assert updated["policy"]["levels"]["1"]["bufferSize"] == 0
        assert updated["policy"]["levels"]["1"]["statsUserUplink"] is True
        assert updated["policy"]["levels"]["2"]["statsUserDownlink"] is True
        assert updated["policy"]["levels"]["named"] == {"preserved": True}
        assert updated["policy"]["system"]["preserved"] == "system"
        assert updated["policy"]["system"]["statsOutboundDownlink"] is True
        assert updated["policy"]["preserved"] == "policy"
        assert written["body"]["config"] == xray_system_config(updated)
    finally:
        await agent.close()


def test_xray_system_config_rejects_public_metrics_and_bad_types():
    base = {"inbounds": [], "outbounds": []}
    payload = system_config_payload(
        metrics_enabled=True,
        metrics_listen="0.0.0.0:11111",
        stats_enabled=True,
        grpc_enabled=True,
    )
    with pytest.raises(RuntimeFailure, match="loopback"):
        apply_xray_system_config(base, payload)
    with pytest.raises(RuntimeFailure, match="complete"):
        apply_xray_system_config(base, {**payload, "unknown": True})
    with pytest.raises(RuntimeFailure, match="boolean"):
        apply_xray_system_config(base, {**payload, "metrics_enabled": 1})
    with pytest.raises(RuntimeFailure, match="log_level"):
        apply_xray_system_config(base, {**payload, "log_level": "verbose"})
    with pytest.raises(RuntimeFailure, match="dns must be"):
        apply_xray_system_config(base, {**payload, "dns": []})
    with pytest.raises(RuntimeFailure, match="policy must be"):
        apply_xray_system_config(base, {**payload, "policy": []})


@pytest.mark.parametrize("log_level", ["none", "error", "warning", "info", "debug"])
def test_xray_system_config_accepts_every_official_log_level(log_level):
    updated = apply_xray_system_config(
        {
            "log": {"access": "none", "error": "/var/log/xray/error.log"},
            "inbounds": [],
            "outbounds": [],
        },
        system_config_payload(log_level=log_level),
    )
    assert updated["log"] == {
        "access": "none",
        "error": "/var/log/xray/error.log",
        "loglevel": log_level,
    }
    assert updated["dns"] == {}


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("127.0.0.2:46736", "127.0.0.2:46737"),
        ("[::1]:46736", "[::1]:46737"),
    ],
)
def test_xray_system_config_preserves_existing_loopback_api_host(endpoint, expected):
    payload = system_config_payload(grpc_enabled=True, grpc_port=46737)
    updated = apply_xray_system_config(
        {"api": {"listen": endpoint, "services": []}}, payload
    )
    assert updated["api"]["listen"] == expected


def test_xray_system_config_maps_complete_false_stats_policy_without_rejecting_it():
    config = {
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": False,
                    "statsUserDownlink": False,
                    "statsUserOnline": True,
                    "bufferSize": 0,
                }
            },
            "system": {
                "statsInboundUplink": False,
                "statsInboundDownlink": False,
                "statsOutboundUplink": False,
                "statsOutboundDownlink": False,
                "preserved": "system",
            },
            "preserved": "policy",
        },
        "inbounds": [],
        "outbounds": [],
    }
    assert xray_system_config(config)["stats_enabled"] is False
    disabled = apply_xray_system_config(
        config,
        system_config_payload(policy=config["policy"]),
    )
    assert disabled["stats"] == {}
    assert disabled["policy"]["levels"]["0"]["statsUserUplink"] is False
    assert disabled["policy"]["levels"]["0"]["statsUserOnline"] is True
    assert disabled["policy"]["levels"]["0"]["bufferSize"] == 0
    assert disabled["policy"]["system"]["preserved"] == "system"
    assert disabled["policy"]["preserved"] == "policy"
    enabled = apply_xray_system_config(
        config,
        system_config_payload(policy=config["policy"], stats_enabled=True),
    )
    assert enabled["policy"]["levels"]["0"]["statsUserUplink"] is True
    assert enabled["policy"]["system"]["statsOutboundDownlink"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_xray_system_config_normalizes_submitted_policy_stats_and_preserves_the_rest(enabled):
    policy = {
        "levels": {
            "0": {
                "handshake": 4,
                "statsUserUplink": not enabled,
            },
            "named": {"preserved": True},
        },
        "system": {
            "statsInboundDownlink": not enabled,
            "preserved": "system",
        },
        "preserved": "policy",
    }
    existing = {"inbounds": [], "outbounds": []}
    if not enabled:
        existing.update(
            stats={},
            policy={
                "levels": {
                    "0": {"statsUserUplink": True, "statsUserDownlink": True}
                },
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True,
                    "statsOutboundUplink": True,
                    "statsOutboundDownlink": True,
                },
            },
        )
    updated = apply_xray_system_config(
        existing,
        system_config_payload(policy=policy, stats_enabled=enabled),
    )
    assert updated["policy"]["levels"]["0"]["handshake"] == 4
    assert updated["policy"]["levels"]["0"]["statsUserUplink"] is enabled
    assert updated["policy"]["levels"]["0"]["statsUserDownlink"] is enabled
    assert updated["policy"]["levels"]["named"] == {"preserved": True}
    assert updated["policy"]["system"]["statsInboundUplink"] is enabled
    assert updated["policy"]["system"]["statsOutboundDownlink"] is enabled
    assert updated["policy"]["system"]["preserved"] == "system"
    assert updated["policy"]["preserved"] == "policy"


@pytest.mark.parametrize(
    "existing",
    [
        {
            "stats": {},
            "policy": {},
            "api": {
                "listen": "127.0.0.1:46736",
                "services": ["HandlerService", "StatsService"],
            },
        },
        {
            "policy": {
                "levels": {
                    "0": {"statsUserUplink": True, "statsUserDownlink": True}
                },
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True,
                    "statsOutboundUplink": True,
                    "statsOutboundDownlink": True,
                },
            },
            "api": {
                "listen": "127.0.0.1:46736",
                "services": ["StatsService"],
            },
        },
        {
            "stats": {},
            "policy": {
                "levels": {
                    "0": {"statsUserUplink": True, "statsUserDownlink": True}
                },
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True,
                    "statsOutboundUplink": True,
                    "statsOutboundDownlink": True,
                },
            },
            "api": {
                "listen": "127.0.0.1:46736",
                "services": ["HandlerService"],
            },
        },
    ],
)
def test_xray_system_config_unrelated_edit_preserves_uncoupled_stats_policy_and_api(existing):
    existing = {**existing, "inbounds": [], "outbounds": []}
    description = xray_system_config(existing)

    updated = apply_xray_system_config(
        existing,
        system_config_payload(
            log_level="info",
            policy=existing["policy"],
            stats_enabled=description["stats_enabled"],
            grpc_enabled=True,
        ),
    )

    assert updated.get("stats") == existing.get("stats")
    assert updated["policy"] == existing["policy"]
    assert updated["api"] == existing["api"]


@pytest.mark.parametrize(
    ("protocol", "rule_type"),
    [("tunnel", "field"), ("dokodemo-door", None)],
)
def test_xray_system_config_safely_edits_verified_traditional_api(protocol, rule_type):
    rule = {"inboundTag": ["api"], "outboundTag": "api"}
    if rule_type:
        rule["type"] = rule_type
    config = {
        "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 46736,
                "protocol": protocol,
                "settings": {"address": "127.0.0.1"},
            }
        ],
        "routing": {"rules": [rule]},
        "outbounds": [],
    }
    description = xray_system_config(config)
    assert description["api_mode"] == "routed"
    assert description["grpc_port"] == 46736
    assert description["writable"] is True
    updated = apply_xray_system_config(
        config,
        system_config_payload(
            policy=config["policy"],
            stats_enabled=True,
            grpc_enabled=True,
            grpc_port=46737,
        ),
    )
    assert "listen" not in updated["api"]
    assert updated["inbounds"][0]["port"] == 46737
    assert updated["routing"] == config["routing"]
    fixed = xray_system_config(updated, stats_address="127.0.0.1:46737")
    assert fixed["writable"] is True
    assert fixed["grpc_disable_supported"] is False
    assert fixed["grpc_port_writable"] is False
    assert fixed["fixed_stats_address"] == "127.0.0.1:46737"
    with pytest.raises(RuntimeFailure, match="cannot be disabled"):
        apply_xray_system_config(
            config,
            system_config_payload(policy=config["policy"], stats_enabled=True),
        )


@pytest.mark.parametrize("protocol", ["tunnel", "dokodemo-door"])
async def test_stats_auto_discovers_the_same_verified_routed_api_binding(config, protocol):
    routed = {
        "api": {"tag": "api", "services": ["StatsService"]},
        "inbounds": [
            {
                "tag": "api",
                "listen": "::1",
                "port": 46736,
                "protocol": protocol,
                "settings": {"rewriteAddress": "127.0.0.1"},
            }
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["api"],
                    "outboundTag": "api",
                }
            ]
        },
        "outbounds": [],
    }
    config.xray_config.write_text(json.dumps(routed))
    agent = Agent(config)
    try:
        assert agent.runtime.stats_endpoint() == "[::1]:46736"

        routed["routing"]["rules"].append(
            {"inboundTag": ["api"], "outboundTag": "direct"}
        )
        config.xray_config.write_text(json.dumps(routed))
        assert agent.runtime.stats_endpoint() is None
    finally:
        await agent.close()


def test_xray_system_config_requires_the_operator_stats_endpoint():
    config = {
        "api": {
            "tag": "api",
            "listen": "[::1]:46736",
            "services": ["StatsService"],
        },
        "inbounds": [],
        "outbounds": [],
    }
    payload = system_config_payload(grpc_enabled=True, grpc_port=46737)
    with pytest.raises(RuntimeFailure, match="stats_address"):
        apply_xray_system_config(config, payload, stats_address="[::1]:46736")


def test_xray_system_config_marks_a_drifted_fixed_stats_endpoint_read_only():
    config = {
        "api": {
            "tag": "api",
            "listen": "[::1]:46736",
            "services": ["StatsService"],
        },
        "inbounds": [],
        "outbounds": [],
    }
    description = xray_system_config(config, stats_address="[::1]:46737")
    assert description["api_mode"] == "direct"
    assert description["grpc_port"] == 46736
    assert description["grpc_disable_supported"] is False
    assert description["grpc_port_writable"] is False
    assert description["writable"] is False
    assert "does not match" in description["read_only_reason"]

    payload = system_config_payload(grpc_enabled=True, grpc_port=46737)
    with pytest.raises(RuntimeFailure, match="does not match"):
        apply_xray_system_config(config, payload, stats_address="[::1]:46737")


def test_xray_system_config_keeps_other_fields_writable_at_the_fixed_stats_endpoint():
    config = {
        "api": {
            "tag": "api",
            "listen": "[::1]:46736",
            "services": ["StatsService"],
        },
        "inbounds": [],
        "outbounds": [],
    }
    description = xray_system_config(config, stats_address="[::1]:46736")
    assert description["writable"] is True
    assert description["read_only_reason"] is None
    assert description["grpc_disable_supported"] is False
    assert description["grpc_port_writable"] is False

    updated = apply_xray_system_config(
        config,
        system_config_payload(
            metrics_enabled=True,
            metrics_listen="127.0.0.1:11112",
            stats_enabled=True,
            grpc_enabled=True,
        ),
        stats_address="[::1]:46736",
    )
    assert updated["metrics"]["listen"] == "127.0.0.1:11112"
    assert updated["api"]["listen"] == "[::1]:46736"


async def test_xray_system_config_get_reports_operator_stats_endpoint_drift(config):
    config.xray_config.write_text(
        json.dumps(
            {
                "api": {
                    "tag": "api",
                    "listen": "127.0.0.1:46736",
                    "services": ["StatsService"],
                },
                "inbounds": [],
                "outbounds": [],
            }
        )
    )
    config.stats_address = "127.0.0.1:46737"
    agent = Agent(config)
    try:
        result = await agent.execute(
            command(request_id="fixed-stats-drift", path="/api/child/xray/system-config")
        )
        assert result["status"] == 200
        description = result["body"]["config"]
        assert description["fixed_stats_address"] == "127.0.0.1:46737"
        assert description["writable"] is False
        assert description["grpc_disable_supported"] is False
        assert description["grpc_port_writable"] is False
        assert "does not match" in description["read_only_reason"]
    finally:
        await agent.close()


@pytest.mark.parametrize("stats_address", ["", "localhost:46736", "0.0.0.0:46736"])
def test_xray_system_config_marks_invalid_fixed_stats_addresses_read_only(stats_address):
    description = xray_system_config(
        {
            "api": {"listen": "127.0.0.1:46736", "services": []},
            "inbounds": [],
            "outbounds": [],
        },
        stats_address=stats_address,
    )
    assert description["writable"] is False
    assert description["grpc_disable_supported"] is False
    assert description["grpc_port_writable"] is False
    assert "literal loopback" in description["read_only_reason"]


def test_xray_system_config_rejects_ambiguous_api_routes_and_tag_collisions():
    base = {
        "api": {"tag": "api", "services": ["StatsService"]},
        "inbounds": [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": 46736,
                "protocol": "tunnel",
                "settings": {"rewriteAddress": "127.0.0.1"},
            }
        ],
        "routing": {
            "rules": [{"inboundTag": ["api"], "outboundTag": "api"}]
        },
        "outbounds": [],
    }
    with_extra_constraint = copy.deepcopy(base)
    with_extra_constraint["routing"]["rules"][0]["network"] = "tcp"
    assert xray_system_config(with_extra_constraint)["writable"] is False
    assert "dedicated inbound" in xray_system_config(with_extra_constraint)[
        "read_only_reason"
    ]

    with_extra_route = copy.deepcopy(base)
    with_extra_route["routing"]["rules"].append(
        {"inboundTag": ["api"], "outboundTag": "direct"}
    )
    assert xray_system_config(with_extra_route)["writable"] is False

    with_outbound_collision = copy.deepcopy(base)
    with_outbound_collision["outbounds"] = [{"tag": "api", "protocol": "freedom"}]
    assert xray_system_config(with_outbound_collision)["writable"] is False
    assert "conflicts" in xray_system_config(with_outbound_collision)["read_only_reason"]


@pytest.mark.parametrize(
    ("existing", "message"),
    [
        ({"log": []}, "log configuration must be an object"),
        ({"log": {"loglevel": "trace"}}, "loglevel is not supported"),
        ({"log": {"loglevel": []}}, "loglevel is not supported"),
        ({"dns": []}, "DNS configuration must be an object"),
        ({"policy": []}, "stats policy must be an object"),
        ({"metrics": {"tag": "metrics"}}, "tag-only metrics"),
        ({"metrics": {"listen": "0.0.0.0:11111"}}, "loopback listener"),
        (
            {"api": {"tag": "api", "services": ["StatsService"]}},
            "Traditional routed Xray API",
        ),
        (
            {"api": {"listen": "0.0.0.0:46736", "services": []}},
            "loopback listener",
        ),
        (
            {
                "policy": {
                    "levels": {"0": {"statsUserUplink": True}},
                    "system": {},
                }
            },
            "Partial Xray stats policy",
        ),
        (
            {
                "policy": {
                    "levels": {
                        "0": {"statsUserUplink": True, "statsUserDownlink": True},
                        "1": {},
                    },
                    "system": {
                        "statsInboundUplink": True,
                        "statsInboundDownlink": True,
                        "statsOutboundUplink": True,
                        "statsOutboundDownlink": True,
                    },
                }
            },
            "Partial Xray stats policy",
        ),
    ],
)
def test_xray_system_config_rejects_unrepresentable_existing_shapes(existing, message):
    payload = system_config_payload()
    description = xray_system_config(existing)
    assert description["writable"] is False
    assert message in description["read_only_reason"]
    with pytest.raises(RuntimeFailure, match=message):
        apply_xray_system_config(existing, payload)


@pytest.mark.parametrize(
    "existing",
    [
        {"log": []},
        {"log": {"loglevel": "trace"}},
        {"dns": []},
        {"policy": []},
        {"metrics": {"tag": "metrics"}},
        {"api": {"tag": "api", "services": ["StatsService"]}},
        {
            "policy": {
                "levels": {"0": {"statsUserUplink": True}},
                "system": {},
            }
        },
    ],
)
async def test_xray_system_config_post_rejects_unrepresentable_config_without_writing(
    config, existing
):
    existing.update(inbounds=[], outbounds=[])
    config.xray_config.write_text(json.dumps(existing))
    before = config.xray_config.read_bytes()
    agent = Agent(config)
    try:
        result = await agent.execute(
            command(
                request_id="unrepresentable-system-write",
                method="POST",
                path="/api/child/xray/system-config",
                body={
                    **system_config_payload(),
                    "expected_sha256": config_sha256(config),
                },
            )
        )
        assert result["status"] == 400
        assert config.xray_config.read_bytes() == before
    finally:
        await agent.close()


async def test_xray_config_files_are_bounded_to_the_primary_file(config):
    primary = config.xray_config.with_name("xray.conf")
    config.xray_config.replace(primary)
    config = config.model_copy(update={"xray_config": primary})
    extra = config.xray_config.parent / "extra.json"
    extra.write_text("{}\n")
    extra.chmod(0o600)
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=False)
    try:
        listed = await agent.execute(
            command(request_id="files-list", path="/api/child/xray/config-files")
        )
        assert listed["status"] == 200
        entries = listed["body"]["files"]["main"]
        assert [item["name"] for item in entries] == ["xray.conf"]
        assert entries[0]["writable"] is True
        assert entries[0]["read_only_reason"] is None
        assert entries[0]["sha256"] == config_sha256(config)

        read = await agent.execute(
            command(
                request_id="file-read",
                path="/api/child/xray/config-files",
                query="file=xray.conf",
            )
        )
        assert read["status"] == 200
        assert json.loads(read["body"]["content"])["outbounds"][0]["tag"] == "direct"
        assert read["body"]["sha256"] == config_sha256(config)
        assert read["body"]["writable"] is True
        assert read["body"]["read_only_reason"] is None

        rejected = await agent.execute(
            command(
                request_id="fragment-write",
                method="POST",
                path="/api/child/xray/config-files",
                body={
                    "file": "extra.json",
                    "content": "{}",
                    "expected_sha256": read["body"]["sha256"],
                },
            )
        )
        assert rejected["status"] == 400
        assert extra.read_text() == "{}\n"

        traversal = await agent.execute(
            command(
                request_id="file-traversal",
                path="/api/child/xray/config-files",
                query="file=..%2Fsecret.json",
            )
        )
        assert traversal["status"] == 400

        replacement = {"inbounds": [], "outbounds": [{"tag": "blocked"}]}
        written = await agent.execute(
            command(
                request_id="file-write",
                method="POST",
                path="/api/child/xray/config-files",
                body={
                    "file": "xray.conf",
                    "content": json.dumps(replacement),
                    "expected_sha256": read["body"]["sha256"],
                },
            )
        )
        assert written["status"] == 200
        assert json.loads(config.xray_config.read_text()) == replacement
    finally:
        await agent.close()


async def test_xray_jsonc_primary_is_visible_but_read_only(config):
    primary = config.xray_config.with_suffix(".jsonc")
    config.xray_config.replace(primary)
    content = b'// preserved JSONC comments\n{"inbounds": [], "outbounds": []}\n'
    primary.write_bytes(content)
    primary.chmod(0o600)
    config = config.model_copy(update={"xray_config": primary})
    agent = Agent(config)
    try:
        listed = await agent.execute(
            command(request_id="jsonc-list", path="/api/child/xray/config-files")
        )
        assert listed["status"] == 200
        assert listed["body"]["files"]["main"][0]["writable"] is False
        assert "JSONC" in listed["body"]["files"]["main"][0]["read_only_reason"]

        read = await agent.execute(
            command(
                request_id="jsonc-read",
                path="/api/child/xray/config-files",
                query="file=xray.jsonc",
            )
        )
        assert read["status"] == 200
        assert read["body"]["content"].startswith("// preserved")
        assert read["body"]["writable"] is False
        assert "JSONC" in read["body"]["read_only_reason"]
        assert read["body"]["sha256"] == hashlib.sha256(content).hexdigest()

        rejected = await agent.execute(
            command(
                request_id="jsonc-write",
                method="POST",
                path="/api/child/xray/config-files",
                body={
                    "file": "xray.jsonc",
                    "content": "{}",
                    "expected_sha256": read["body"]["sha256"],
                },
            )
        )
        assert rejected["status"] == 400
        assert "read-only" in rejected["error"]
        assert primary.read_bytes() == content
    finally:
        await agent.close()


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
async def test_xray_config_files_reject_unsafe_primary_files(config, kind):
    primary = config.xray_config
    target = primary.with_name("unsafe-source")
    target.write_bytes(primary.read_bytes())
    target.chmod(0o600)
    primary.unlink()
    if kind == "symlink":
        primary.symlink_to(target)
    else:
        os.link(target, primary)
    agent = Agent(config)
    try:
        result = await agent.execute(
            command(request_id=f"unsafe-{kind}", path="/api/child/xray/config-files")
        )
        assert result["status"] == 400
        expected = "Symlink" if kind == "symlink" else "Hard-linked"
        assert expected in result["error"]
    finally:
        await agent.close()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files require POSIX")
async def test_xray_config_files_reject_fifo_primary_without_opening_it(config):
    config.xray_config.unlink()
    os.mkfifo(config.xray_config, 0o600)
    agent = Agent(config)
    try:
        result = await agent.execute(
            command(request_id="unsafe-fifo", path="/api/child/xray/config-files")
        )
        assert result["status"] == 400
        assert "regular file" in result["error"]
    finally:
        await agent.close()


async def test_xray_config_files_reject_oversize_and_invalid_utf8(config):
    agent = Agent(config)
    try:
        config.xray_config.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
        oversized = await agent.execute(
            command(request_id="oversized-primary", path="/api/child/xray/config-files")
        )
        assert oversized["status"] == 400
        assert "2 MiB" in oversized["error"]

        config.xray_config.write_bytes(b"\xff\xfe")
        invalid = await agent.execute(
            command(
                request_id="invalid-utf8-primary",
                path="/api/child/xray/config-files",
                query="file=xray.json",
            )
        )
        assert invalid["status"] == 400
        assert "UTF-8" in invalid["error"]
    finally:
        await agent.close()


@pytest.mark.parametrize("operation", ["system-config", "config-files"])
async def test_xray_guarded_writes_reject_concurrent_config_changes(config, operation):
    concurrent = {
        "inbounds": [],
        "outbounds": [{"tag": "concurrent", "protocol": "blackhole"}],
    }
    agent = Agent(config)
    expected_sha256 = config_sha256(config)

    async def validate_after_concurrent_write(_candidate):
        config.xray_config.write_text(json.dumps(concurrent))
        return True, "valid"

    agent.runtime.validate = AsyncMock(side_effect=validate_after_concurrent_write)
    agent.runtime.running = AsyncMock(return_value=False)
    try:
        if operation == "system-config":
            body = {
                **system_config_payload(
                    metrics_enabled=True,
                    stats_enabled=True,
                    grpc_enabled=True,
                ),
                "expected_sha256": expected_sha256,
            }
            path = "/api/child/xray/system-config"
        else:
            body = {
                "file": "xray.json",
                "content": json.dumps(
                    {"inbounds": [], "outbounds": [{"tag": "replacement"}]}
                ),
                "expected_sha256": expected_sha256,
            }
            path = "/api/child/xray/config-files"
        result = await agent.execute(
            command(
                request_id=f"concurrent-{operation}",
                method="POST",
                path=path,
                body=body,
            )
        )
        assert result["status"] == 400
        assert "changed during" in result["error"]
        assert json.loads(config.xray_config.read_text()) == concurrent
    finally:
        await agent.close()


async def test_xray_guarded_write_compares_the_file_displaced_by_the_atomic_swap(
    config, monkeypatch
):
    concurrent = {
        "inbounds": [],
        "outbounds": [{"tag": "exchange-race", "protocol": "blackhole"}],
    }
    expected_sha256 = config_sha256(config)
    real_exchange = runtime_module._rename_exchange
    raced = False

    def race_before_exchange(directory_fd, left, right):
        nonlocal raced
        if not raced:
            raced = True
            config.xray_config.write_text(json.dumps(concurrent))
        return real_exchange(directory_fd, left, right)

    monkeypatch.setattr(runtime_module, "_rename_exchange", race_before_exchange)
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=False)
    try:
        result = await agent.execute(
            command(
                request_id="atomic-exchange-race",
                method="POST",
                path="/api/child/xray/config-files",
                body={
                    "file": "xray.json",
                    "content": json.dumps({"inbounds": [], "outbounds": []}),
                    "expected_sha256": expected_sha256,
                },
            )
        )
        assert result["status"] == 400
        assert "changed during" in result["error"]
        assert json.loads(config.xray_config.read_text()) == concurrent
    finally:
        await agent.close()


async def test_xray_file_write_rejects_same_json_with_a_stale_raw_revision(config):
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=False)
    try:
        read = await agent.execute(
            command(
                request_id="raw-revision-read",
                path="/api/child/xray/config-files",
                query="file=xray.json",
            )
        )
        assert read["status"] == 200
        original = json.loads(config.xray_config.read_text())
        config.xray_config.write_text(json.dumps(original, indent=4) + "\n")

        stale = await agent.execute(
            command(
                request_id="raw-revision-write",
                method="POST",
                path="/api/child/xray/config-files",
                body={
                    "file": "xray.json",
                    "content": read["body"]["content"],
                    "expected_sha256": read["body"]["sha256"],
                },
            )
        )
        assert stale["status"] == 400
        assert "changed since it was read" in stale["error"]
        agent.runtime.validate.assert_not_awaited()
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


async def test_failed_restart_does_not_overwrite_a_newer_external_config(config):
    external = {
        "inbounds": [],
        "outbounds": [{"tag": "external-writer", "protocol": "blackhole"}],
    }
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=True)

    async def fail_after_external_write():
        config.xray_config.write_text(json.dumps(external))
        raise RuntimeFailure("restart failed")

    agent.runtime.restart = AsyncMock(side_effect=fail_after_external_write)
    try:
        with pytest.raises(RuntimeFailure, match="newer configuration was preserved"):
            await agent.runtime.write(
                {"inbounds": [], "outbounds": [{"tag": "candidate"}]},
                restart=True,
            )
        assert json.loads(config.xray_config.read_text()) == external
        assert agent.runtime.restart.await_count == 1
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
