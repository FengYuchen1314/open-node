import copy
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from open_node_agent.client import Agent
from open_node_agent.node_cleanup import ENDPOINT
from open_node_agent.runtime import RuntimeFailure, atomic_write
from test_subscription_access import entry, setup
from test_subscription_access import execute as access


async def execute(agent, body, identifier=None):
    return await agent.execute(
        {
            "request_id": identifier or uuid4().hex,
            "method": "POST",
            "path": ENDPOINT,
            "body": body,
        }
    )


async def request(agent, **targets):
    targets = targets or {"inbound_tags": ["edge"]}
    preview = await execute(agent, {"action": "preview", **targets})
    assert preview["status"] == 200, preview
    return {
        "action": "apply",
        **targets,
        "operation_id": str(uuid4()),
        "expected_revision": preview["body"]["node_cleanup"]["revision"],
        "acknowledge_runtime_restart": True,
    }, preview["body"]["node_cleanup"]


async def test_preview_is_read_only_and_receipt_idempotent(config):
    agent, original, users = setup(config)
    try:
        payload, preview = await request(agent)
        assert preview["impact"]["inbound_tags"] == ["edge"]
        assert not preview["applied"]
        assert agent.runtime.read() == original
        agent.runtime.restart.assert_not_awaited()
        assert agent.operations.node_cleanup.pending() is None
        result = await execute(agent, payload)
        assert result["status"] == 200, result
        assert result["body"]["node_cleanup"]["applied"]
        assert agent.runtime.read()["inbounds"] == []
        assert agent.runtime.read()["outbounds"] == original["outbounds"]
        assert users[0]["id"] not in json.dumps(result)
        calls = agent.runtime.restart.await_count
        assert (await execute(agent, payload))["body"] == result["body"]
        assert agent.runtime.restart.await_count == calls
        changed = {**payload, "inbound_tags": ["another"]}
        assert (await execute(agent, changed))["status"] == 400
        state = agent.journal.db.execute("SELECT state FROM node_cleanup_jobs").fetchone()[0]
        assert state == "{}"
    finally:
        await agent.close()


async def test_status_distinguishes_unknown_operation_from_prepared_or_completed(config):
    agent, original, _ = setup(config)
    try:
        identifier = str(uuid4())
        result = await execute(agent, {"action": "status", "operation_id": identifier})
        assert result["status"] == 200
        assert result["body"]["node_cleanup"] == {
            "operation_id": identifier,
            "exists": False,
            "applied": False,
            "revision": None,
            "impact": {},
        }
        assert agent.runtime.read() == original
        assert agent.operations.node_cleanup.pending() is None
        payload, _ = await request(agent)
        await execute(agent, payload)
        status = await execute(agent, {"action": "status", "operation_id": payload["operation_id"]})
        assert status["body"]["node_cleanup"]["exists"] is True
        assert status["body"]["node_cleanup"]["applied"] is True
    finally:
        await agent.close()


@pytest.mark.parametrize("protocol", ["vless", "snell", "mieru", "shadowsocks"])
async def test_suspended_listener_cannot_be_restored_after_cleanup(config, protocol):
    agent, _, users = setup(config, protocol, count=1)
    try:
        assert (await access(agent, [entry(protocol, users[0], False)]))["status"] == 200
        payload, preview = await request(agent)
        assert preview["impact"]["suspended_tags"] == ["edge"]
        assert (await execute(agent, payload))["status"] == 200
        assert agent.operations.subscription_access.load() == {}
        assert (await access(agent, [entry(protocol, users[0], True)]))["status"] == 400
        assert agent.runtime.read()["inbounds"] == []
    finally:
        await agent.close()


async def test_dependency_closure_preserves_shared_inbound_rule_filters(config):
    agent, original, _ = setup(config)
    original["inbounds"].append({"tag": "other", "protocol": "socks", "port": 1080})
    original["outbounds"].extend(
        [
            {"tag": "exit", "protocol": "freedom"},
            {"tag": "proxy", "protocol": "freedom", "proxySettings": {"tag": "exit"}},
            {
                "tag": "chain",
                "protocol": "freedom",
                "streamSettings": {"sockopt": {"dialerProxy": "proxy"}},
            },
        ]
    )
    original["routing"]["rules"] = [
        {"type": "field", "outboundTag": "exit", "user": ["alice"]},
        {"type": "field", "outboundTag": "chain", "domain": ["example.com"]},
        {
            "type": "field",
            "outboundTag": "direct",
            "inboundTag": ["edge", "other"],
            "network": "tcp",
        },
        {"type": "field", "outboundTag": "direct", "inboundTag": ["edge"]},
    ]
    config.xray_config.write_text(json.dumps(original))
    try:
        payload, preview = await request(agent, inbound_tags=["edge"], outbound_tags=["exit"])
        assert preview["impact"]["outbound_tags"] == ["chain", "exit", "proxy"]
        assert preview["impact"]["removed_rules"] == 3
        assert preview["impact"]["changed_rules"] == 1
        assert (await execute(agent, payload))["status"] == 200
        final = agent.runtime.read()
        assert final["inbounds"] == [original["inbounds"][1]]
        assert final["outbounds"] == [original["outbounds"][0]]
        assert final["routing"]["rules"] == [
            {
                **original["routing"]["rules"][2],
                "inboundTag": ["other"],
            }
        ]
    finally:
        await agent.close()


@pytest.mark.parametrize("change", ["config", "suspended", "limits"])
async def test_preview_revision_covers_config_suspension_and_limiter(config, change):
    agent, original, _ = setup(config)
    try:
        payload, _ = await request(agent)
        if change == "config":
            original["log"] = {"loglevel": "warning"}
            config.xray_config.write_text(json.dumps(original))
        elif change == "suspended":
            agent.operations.subscription_access.save(
                {
                    "other": {
                        "inbound": {
                            "tag": "other",
                            "protocol": "vless",
                            "settings": {"clients": []},
                        },
                        "index": 0,
                        "phase": "suspended",
                        "config_revision": "a" * 64,
                    }
                }
            )
        else:
            atomic_write(
                agent.runtime.limiter.path,
                json.dumps(
                    {
                        "version": 1,
                        "inbounds": [{"inbound_tag": "edge"}],
                    }
                ).encode(),
            )
        before = agent.operations.node_cleanup.current()
        result = await execute(agent, payload)
        assert result["status"] == 400
        assert "preview" in result["error"]
        assert agent.operations.node_cleanup.current() == before
        assert agent.operations.node_cleanup.pending() is None
    finally:
        await agent.close()


@pytest.mark.parametrize("change", ["duplicate_tag", "balancer", "api", "last_outbound"])
async def test_ambiguous_or_shared_control_resources_are_not_removed(config, change):
    agent, original, _ = setup(config)
    targets = {"inbound_tags": ["edge"]}
    if change == "duplicate_tag":
        original["inbounds"].append(copy.deepcopy(original["inbounds"][0]))
    elif change == "balancer":
        original["outbounds"].append({"tag": "exit", "protocol": "freedom"})
        original["routing"]["balancers"] = [{"tag": "pool", "selector": ["ex"]}]
        targets = {"outbound_tags": ["exit"]}
    elif change == "api":
        original["api"] = {"tag": "edge"}
    else:
        targets = {"outbound_tags": ["direct"]}
    config.xray_config.write_text(json.dumps(original))
    try:
        assert (await execute(agent, {"action": "preview", **targets}))["status"] == 400
        assert agent.runtime.read() == original
        agent.runtime.restart.assert_not_awaited()
    finally:
        await agent.close()


@pytest.mark.parametrize(
    "change",
    [
        {"operation_id": "not-a-uuid"},
        {"acknowledge_runtime_restart": False},
        {"acknowledge_runtime_restart": 1},
        {"expected_revision": "x"},
        {"inbound_tags": ["edge", "edge"]},
        {"inbound_tags": [" "]},
        {"inbound_tags": []},
        {"unknown": True},
    ],
)
async def test_request_validation(config, change):
    agent, original, _ = setup(config)
    try:
        payload, _ = await request(agent)
        assert (await execute(agent, {**payload, **change}))["status"] == 400
        assert agent.runtime.read() == original
        assert agent.operations.node_cleanup.pending() is None
    finally:
        await agent.close()


async def test_validation_failure_does_not_prepare_a_job(config):
    agent, original, _ = setup(config)
    try:
        payload, _ = await request(agent)
        agent.runtime.validate.return_value = (False, "secret-validation-output")
        result = await execute(agent, payload)
        assert result["status"] == 400
        assert "secret-validation-output" not in json.dumps(result)
        assert agent.runtime.read() == original
        assert agent.operations.node_cleanup.pending() is None
    finally:
        await agent.close()


async def test_interruption_recovers_before_other_runtime_mutations(config):
    agent, original, _ = setup(config)
    payload, _ = await request(agent)
    agent.runtime.write = AsyncMock(side_effect=OSError("interrupted"))
    result = await execute(agent, payload)
    assert result["status"] == 500
    assert agent.operations.node_cleanup.pending()
    assert not (await agent.health_report())["runtime_ready"]
    assert agent.runtime.read() == original
    await agent.close()
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "ok"))
    try:
        result = await agent.execute(
            {
                "request_id": uuid4().hex,
                "method": "POST",
                "path": "/api/child/inbounds",
                "body": {
                    "action": "add",
                    "inbound": {"tag": "new", "protocol": "socks", "port": 1080},
                },
            }
        )
        assert result["status"] == 200, result
        assert [item["tag"] for item in agent.runtime.read()["inbounds"]] == ["new"]
        assert agent.operations.node_cleanup.pending() is None
        assert (await execute(agent, payload))["body"]["node_cleanup"]["applied"]
    finally:
        await agent.close()


async def test_host_edits_block_recovery_but_remain_inspectable(config):
    agent, original, _ = setup(config)
    try:
        payload, _ = await request(agent)
        agent.runtime.write = AsyncMock(side_effect=OSError("interrupted"))
        assert (await execute(agent, payload))["status"] == 500
        original["log"] = {"loglevel": "warning"}
        config.xray_config.write_text(json.dumps(original))
        result = await execute(agent, payload)
        assert result["status"] == 400 and "review" in result["error"]
        status = await execute(agent, {"action": "status", "operation_id": payload["operation_id"]})
        assert not status["body"]["node_cleanup"]["applied"]
        read = await agent.execute(
            {"request_id": uuid4().hex, "method": "GET", "path": "/api/child/xray/config"}
        )
        assert read["status"] == 200
        assert agent.runtime.read() == original
    finally:
        await agent.close()


async def test_interruption_after_config_write_recovers_suspended_state(config):
    agent, _, users = setup(config, count=1)
    assert (await access(agent, [entry("vless", users[0], False)]))["status"] == 200
    payload, _ = await request(agent)
    write = agent.runtime.write

    async def interrupted(*args, **kwargs):
        await write(*args, **kwargs)
        raise OSError("interrupted after runtime update")

    agent.runtime.write = interrupted
    assert (await execute(agent, payload))["status"] == 500
    assert "edge" in agent.operations.subscription_access.load()
    await agent.close()
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "ok"))
    try:
        await agent.operations.node_cleanup.recover()
        assert agent.operations.subscription_access.load() == {}
        assert (await access(agent, [entry("vless", users[0], True)]))["status"] == 400
        assert (await execute(agent, payload))["body"]["node_cleanup"]["applied"]
    finally:
        await agent.close()


async def test_cleanup_removes_only_selected_private_limiter_policy(config):
    agent, _, _ = setup(config)
    atomic_write(
        agent.runtime.limiter.path,
        json.dumps(
            {
                "version": 1,
                "inbounds": [{"inbound_tag": "edge"}, {"inbound_tag": "other"}],
            }
        ).encode(),
    )
    before = agent.runtime.limiter.document()
    try:
        payload, preview = await request(agent)
        assert preview["impact"]["removed_limiter_policies"] == 1
        assert (await execute(agent, payload))["status"] == 200
        assert agent.runtime.limiter.document() == {
            "version": 1,
            "inbounds": [before["inbounds"][1]],
        }
        assert agent.runtime.limiter.path.stat().st_mode & 0o777 == 0o600
    finally:
        await agent.close()


async def test_terminal_retry_does_not_remove_independently_recreated_tag(config):
    agent, original, _ = setup(config)
    try:
        payload, _ = await request(agent)
        assert (await execute(agent, payload))["status"] == 200
        config.xray_config.write_text(json.dumps(original))
        calls = agent.runtime.restart.await_count
        assert (await execute(agent, payload))["status"] == 200
        assert agent.runtime.read() == original
        assert agent.runtime.restart.await_count == calls
    finally:
        await agent.close()


async def test_edit_after_activation_cannot_receive_a_completed_receipt(config):
    agent, original, _ = setup(config)
    try:
        payload, _ = await request(agent)
        write = agent.runtime.write

        async def edited_after_write(*args, **kwargs):
            result = await write(*args, **kwargs)
            config.xray_config.write_text(json.dumps(original))
            return result

        agent.runtime.write = edited_after_write
        result = await execute(agent, payload)
        assert result["status"] == 400 and "review" in result["error"]
        status = agent.operations.node_cleanup.status(payload["operation_id"])
        assert not status["node_cleanup"]["applied"]
        assert agent.operations.node_cleanup.pending()
    finally:
        await agent.close()


async def test_failed_runtime_write_retains_old_clients_caps(config):
    agent, original, _ = setup(config)
    atomic_write(
        agent.runtime.limiter.path,
        json.dumps(
            {
                "version": 1,
                "inbounds": [
                    {
                        "inbound_tag": "edge",
                        "users": [{"uid": 0, "email": "alice", "speed_limit": 125000}],
                    }
                ],
            }
        ).encode(),
    )
    before = agent.runtime.limiter.document()
    try:
        payload, _ = await request(agent)
        agent.runtime.write = AsyncMock(side_effect=RuntimeFailure("restart failed"))
        assert (await execute(agent, payload))["status"] == 400
        assert agent.runtime.limiter.document() == before
        assert agent.runtime.read() == original
        assert agent.operations.node_cleanup.pending()
    finally:
        await agent.close()


@pytest.mark.parametrize("operation", ["install", "install-stream", "remove", "remove-stream"])
async def test_legacy_get_mutations_observe_the_cleanup_barrier(config, operation):
    agent, _, _ = setup(config)
    try:
        agent.operations.node_cleanup.recover = AsyncMock(
            side_effect=RuntimeFailure("Pending cleanup needs review")
        )
        agent.operations.nginx.handle = AsyncMock()
        result = await agent.execute(
            {
                "request_id": uuid4().hex,
                "method": "GET",
                "path": "/api/child/nginx/" + operation,
            }
        )
        assert result["status"] == 400
        agent.operations.nginx.handle.assert_not_awaited()
    finally:
        await agent.close()
