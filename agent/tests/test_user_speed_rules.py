import json
from unittest.mock import AsyncMock

import pytest
from open_node_agent.limiter import LimitPolicy, LimitUser, NativeLimiter, SpeedRule
from open_node_agent.runtime import RuntimeFailure
from test_limiter import fake_runtime, store_policy

RULE = {
    "type": "sustained",
    "threshold_mbps": 2.0,
    "sustained_seconds": 2,
    "window_seconds": 0,
    "burst_count": 0,
    "limit_mbps": 0.5,
    "limit_duration": 5,
}


def test_empty_user_rules_preserve_legacy_wire_format():
    user = LimitUser(email="alice")
    assert "auto_speed_rules" not in user.model_dump()
    policy = LimitPolicy(inbound_tag="edge", users=[user])
    assert "auto_speed_rules" not in policy.model_dump()["users"][0]
    policy.users[0].auto_speed_rules = [SpeedRule.model_validate(RULE)]
    assert policy.model_dump()["users"][0]["auto_speed_rules"] == [RULE]


@pytest.mark.parametrize(
    "rules",
    [
        [{}],
        [RULE | {"window_seconds": -1}],
        [RULE] * 101,
        [RULE | {"limit_mbps": 0}],
        [RULE | {"unknown": 1}],
    ],
)
def test_invalid_per_user_rules_are_rejected(rules):
    with pytest.raises(ValueError):
        LimitPolicy(inbound_tag="edge", users=[{"email": "alice", "auto_speed_rules": rules}])


async def test_rules_only_provision_is_enforced_and_preserves_other_policies(tmp_path):
    runtime = fake_runtime(tmp_path)
    limiter = NativeLimiter(runtime)
    limiter.require_user_rules = AsyncMock()
    current = {
        "revision": "a" * 64,
        "inbounds": [
            {
                "inbound_tag": "edge",
                "node_limit": 1000,
                "auto_speed_rules": [RULE],
                "users": [{"email": "other", "auto_speed_rules": [RULE]}],
            }
        ],
    }
    limiter.request = AsyncMock(side_effect=[current, {"revision": "b" * 64}])
    result = await limiter.provision(
        [{"inbound_tag": "edge", "user": {"email": "alice", "auto_speed_rules": [RULE]}}],
        runtime.read(),
    )
    assert result["revision"] == "b" * 64
    limiter.require_user_rules.assert_awaited_once()
    policy = limiter.request.call_args.kwargs["body"]["policies"][0]
    assert policy["auto_speed_rules"] == [RULE]
    assert policy["node_limit"] == 1000
    assert {user["email"]: user["auto_speed_rules"] for user in policy["users"]} == {
        "other": [RULE],
        "alice": [RULE],
    }
    store_policy(limiter, {"version": 1, "inbounds": [policy]})
    limiter.request = AsyncMock(
        side_effect=[{"revision": "b" * 64, "inbounds": [policy]}, {"revision": "c" * 64}]
    )
    await limiter.provision([{"inbound_tag": "edge", "user": {"email": "alice"}}], runtime.read())
    cleared = limiter.request.call_args.kwargs["body"]["policies"][0]
    assert not next(user for user in cleared["users"] if user["email"] == "alice").get(
        "auto_speed_rules"
    )
    assert next(user for user in cleared["users"] if user["email"] == "other")[
        "auto_speed_rules"
    ] == [RULE]


@pytest.mark.parametrize("capability", [None, False, True, 0, 2])
async def test_old_or_invalid_runtime_capability_fails_before_any_write(
    tmp_path, monkeypatch, capability
):
    runtime = fake_runtime(tmp_path)
    runtime.binary = tmp_path / "xray"
    runtime.binary.touch()
    monkeypatch.setattr(
        "open_node_agent.limiter.run_command",
        AsyncMock(
            return_value=(0, json.dumps({"limiter": 1, "user_auto_speed_rules": capability}))
        ),
    )
    limiter = NativeLimiter(runtime)
    limiter.request = AsyncMock()
    assert await limiter.supported()
    with pytest.raises(RuntimeFailure, match="Upgrade"):
        await limiter.provision(
            [{"inbound_tag": "edge", "user": {"email": "alice", "auto_speed_rules": [RULE]}}],
            runtime.read(),
        )
    limiter.request.assert_not_awaited()
    runtime.validate.assert_not_awaited()


async def test_new_runtime_capability_is_cached_and_stored_rules_block_downgrade(
    tmp_path, monkeypatch
):
    runtime = fake_runtime(tmp_path)
    runtime.binary = tmp_path / "xray"
    runtime.binary.touch()
    probe = AsyncMock(return_value=(0, '{"limiter":1,"user_auto_speed_rules":1}'))
    monkeypatch.setattr("open_node_agent.limiter.run_command", probe)
    limiter = NativeLimiter(runtime)
    await limiter.require_user_rules()
    await limiter.require_user_rules()
    assert probe.await_count == 1
    store_policy(
        limiter,
        {
            "version": 1,
            "inbounds": [
                {"inbound_tag": "edge", "users": [{"email": "alice", "auto_speed_rules": [RULE]}]}
            ],
        },
    )
    old = tmp_path / "old-xray"
    old.touch()
    probe.return_value = (0, '{"limiter":1}')
    with pytest.raises(RuntimeFailure, match="Upgrade"):
        await limiter.require_binary(old)


@pytest.mark.parametrize("capability", [None, False, True, 0, 2, "1"])
async def test_mieru_udp_target_requires_strict_versioned_capability(
    tmp_path, monkeypatch, capability
):
    runtime = fake_runtime(tmp_path)
    runtime.binary = tmp_path / "xray"
    runtime.binary.touch()
    monkeypatch.setattr(
        "open_node_agent.limiter.run_command",
        AsyncMock(
            return_value=(
                0,
                json.dumps({"limiter": 1, "mieru_udp_target": capability}),
            )
        ),
    )
    limiter = NativeLimiter(runtime)
    assert not await limiter.mieru_udp_target_supported()


async def test_mieru_udp_target_capability_is_cached_per_binary_identity(tmp_path, monkeypatch):
    runtime = fake_runtime(tmp_path)
    runtime.binary = tmp_path / "xray"
    runtime.binary.write_bytes(b"old")
    probe = AsyncMock(return_value=(0, '{"limiter":1,"mieru_udp_target":1}'))
    monkeypatch.setattr("open_node_agent.limiter.run_command", probe)
    limiter = NativeLimiter(runtime)
    assert await limiter.mieru_udp_target_supported()
    assert await limiter.mieru_udp_target_supported()
    assert probe.await_count == 1

    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"new-runtime")
    replacement.replace(runtime.binary)
    probe.return_value = (0, '{"limiter":1}')
    assert not await limiter.mieru_udp_target_supported()
    assert probe.await_count == 2


async def test_manual_user_rules_require_new_runtime(tmp_path):
    limiter = NativeLimiter(fake_runtime(tmp_path))
    limiter.require_user_rules = AsyncMock(side_effect=RuntimeFailure("Upgrade"))
    limiter.request = AsyncMock()
    with pytest.raises(RuntimeFailure, match="Upgrade"):
        await limiter.apply(
            {"inbound_tag": "edge", "users": [{"email": "alice", "auto_speed_rules": [RULE]}]}
        )
    limiter.request.assert_not_awaited()
