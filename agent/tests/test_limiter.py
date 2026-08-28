import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_node_agent.client import Agent
from open_node_agent.limiter import (
    LimitDocument,
    LimitPolicy,
    NativeLimiter,
    validate_credentials,
)
from open_node_agent.runtime import RuntimeFailure


def inbound(protocol="vless", email="alice"):
    container = "users" if protocol in {"anytls", "snell", "mieru"} else "clients"
    return {"tag": "edge", "protocol": protocol, "settings": {container: [{"email": email}]}}


def fake_runtime(tmp_path, config=None):
    return SimpleNamespace(
        config=SimpleNamespace(state_dir=tmp_path),
        binding=AsyncMock(return_value=None),
        read=lambda: config or {"inbounds": [inbound()]},
        validate=AsyncMock(return_value=(True, "")),
    )


def store_policy(limiter, value):
    limiter.directory.mkdir(mode=0o700, exist_ok=True)
    limiter.path.write_text(json.dumps(value))
    limiter.path.chmod(0o600)


@pytest.mark.parametrize(
    "protocol", ["vless", "vmess", "trojan", "shadowsocks", "hysteria", "anytls", "snell", "mieru"]
)
def test_limits_bind_to_authenticated_users(protocol):
    config = {"inbounds": [inbound(protocol)]}
    policy = LimitPolicy(
        inbound_tag="edge", node_limit=50000, users=[{"email": "alice", "device_limit": 2}]
    )
    validate_credentials(config, policy)
    policy.users[0].email = "unknown"
    with pytest.raises(RuntimeFailure, match="authentication credential"):
        validate_credentials(config, policy)
    validate_credentials(config, policy, existing=True)


@pytest.mark.parametrize("protocol", ["vless", "anytls"])
def test_node_policy_cannot_silently_ignore_unnamed_credentials(protocol):
    config = {"inbounds": [inbound(protocol, "")]}
    with pytest.raises(RuntimeFailure, match="every authentication credential"):
        validate_credentials(config, LimitPolicy(inbound_tag="edge", node_limit=1))


@pytest.mark.parametrize(
    "value",
    [
        {"inbound_tag": "edge", "users": [{"email": "alice"}, {"email": "alice"}]},
        {"inbound_tag": "edge", "users": [{"email": " alice"}]},
        {"inbound_tag": "edge", "users": [{"email": "x", "speed_limit": True}]},
        {"inbound_tag": "edge", "users": [{"email": "x", "device_limit": 1000001}]},
        {"inbound_tag": "edge", "node_limit": 1 << 51},
        {"inbound_tag": "edge", "unknown": 1},
        {
            "inbound_tag": "edge",
            "auto_speed_rules": [
                {
                    "type": "burst",
                    "threshold_mbps": 1,
                    "sustained_seconds": 2,
                    "window_seconds": 1,
                    "burst_count": 1,
                    "limit_mbps": 1,
                    "limit_duration": 1,
                }
            ],
        },
    ],
)
def test_invalid_limits_fail_validation(value):
    with pytest.raises(ValueError):
        LimitPolicy.model_validate(value)


def test_policy_file_permissions_and_native_nulls(tmp_path):
    limiter = NativeLimiter(fake_runtime(tmp_path))
    assert limiter.document() == {"version": 1, "inbounds": []}
    store_policy(limiter, {"version": 1, "inbounds": None})
    assert limiter.document()["inbounds"] == []
    limiter.path.chmod(0o644)
    with pytest.raises(RuntimeFailure, match="private"):
        limiter.document()
    limiter.path.chmod(0o600)
    os.link(limiter.path, tmp_path / "linked-policy")
    with pytest.raises(RuntimeFailure, match="private"):
        limiter.document()


def test_duplicate_native_inbounds_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        LimitDocument.model_validate({"version": 1, "inbounds": [{"inbound_tag": "x"}] * 2})


async def test_apply_checks_credentials_before_control_request(tmp_path):
    limiter = NativeLimiter(fake_runtime(tmp_path))
    limiter.request = AsyncMock()
    with pytest.raises(RuntimeFailure, match="authentication credential"):
        await limiter.apply({"inbound_tag": "edge", "users": [{"email": "wrong"}]})
    limiter.request.assert_not_awaited()
    await limiter.apply({"inbound_tag": "edge", "node_limit": 100, "expected_revision": "a" * 64})
    assert limiter.request.call_args.kwargs["expected"] == "a" * 64
    await limiter.apply({"action": "remove", "inbound_tag": "edge", "expected_revision": "b" * 64})
    assert limiter.request.call_args.args == ("DELETE",)
    with pytest.raises(RuntimeFailure, match="one inbound"):
        await limiter.apply({"action": "remove", "inbound_tag": "edge", "node_limit": 0})


async def test_provision_validates_before_persisting_and_merges_other_users(tmp_path):
    runtime = fake_runtime(tmp_path)
    limiter = NativeLimiter(runtime)
    bindings = [
        {"inbound_tag": "edge", "user": {"email": "alice", "speed_limit": 4000, "device_limit": 2}}
    ]
    current = {
        "revision": "a" * 64,
        "inbounds": [
            {
                "inbound_tag": "edge",
                "node_limit": 9000,
                "users": [{"email": "other", "speed_limit": 7000}],
            }
        ],
    }
    limiter.request = AsyncMock(side_effect=[current, {"revision": "b" * 64}])
    result = await limiter.provision(bindings, runtime.read())
    assert result["revision"] == "b" * 64
    runtime.validate.assert_awaited_once()
    write = limiter.request.call_args.kwargs
    assert write["expected"] == "a" * 64
    policy = write["body"]["policies"][0]
    assert policy["node_limit"] == 9000
    assert [(user["email"], user["speed_limit"]) for user in policy["users"]] == [
        ("other", 7000),
        ("alice", 4000),
    ]
    limiter.request.reset_mock()
    runtime.validate.return_value = (False, "invalid candidate")
    with pytest.raises(RuntimeFailure, match="before limiter changes"):
        await limiter.provision(bindings, runtime.read())
    limiter.request.assert_not_awaited()


async def test_unlimited_provision_does_not_require_patched_runtime(tmp_path):
    runtime = fake_runtime(tmp_path)
    limiter = NativeLimiter(runtime)
    limiter.request = AsyncMock()
    assert (
        await limiter.provision(
            [{"inbound_tag": "edge", "user": {"email": "alice"}}], runtime.read()
        )
        is None
    )
    limiter.request.assert_not_awaited()
    runtime.validate.assert_not_awaited()


async def test_existing_policies_block_unsupported_binary_and_nameless_config(tmp_path):
    limiter = NativeLimiter(fake_runtime(tmp_path))
    store_policy(limiter, {"version": 1, "inbounds": [{"inbound_tag": "edge", "node_limit": 1000}]})
    limiter.supported = AsyncMock(return_value=False)
    with pytest.raises(RuntimeFailure, match="cannot enforce"):
        await limiter.require_binary()
    with pytest.raises(RuntimeFailure, match="every authentication"):
        limiter.require_config({"inbounds": [inbound(email="")]})


async def test_failed_batch_keeps_persisted_limits_and_never_reports_success(config):
    agent = Agent(config)
    agent.runtime.read = lambda: {"inbounds": [inbound()]}
    agent.runtime.limiter.provision = AsyncMock(return_value={"revision": "a" * 64})
    agent.runtime.write = AsyncMock(side_effect=RuntimeFailure("config failure"))
    try:
        result = await agent.execute(
            {
                "request_id": "limited-batch",
                "method": "POST",
                "path": "/api/child/batch-apply",
                "body": {
                    "limiter_users": [
                        {"inbound_tag": "edge", "user": {"email": "alice", "speed_limit": 100}}
                    ]
                },
            }
        )
        assert result["status"] >= 400
        assert "persisted" in result["error"]
        agent.runtime.limiter.provision.assert_awaited_once()
        agent.runtime.write.assert_awaited_once()
    finally:
        await agent.close()
