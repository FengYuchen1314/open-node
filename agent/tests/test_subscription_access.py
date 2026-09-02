import copy
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from open_node_agent.client import Agent
from open_node_agent.operations import assert_managed_egress_preserved
from open_node_agent.runtime import RuntimeFailure
from open_node_agent.subscription_access import ENDPOINT, revision

PROTOCOLS = ["vless", "vmess", "trojan", "shadowsocks", "hysteria", "anytls", "snell", "mieru"]


def client(protocol, email="alice"):
    values = {
        "vless": {"id": str(uuid4())},
        "vmess": {"id": str(uuid4())},
        "trojan": {"password": "password-" + email},
        "shadowsocks": {"password": "key-" + email, "method": "aes-128-gcm"},
        "hysteria": {"auth": "auth-" + email},
        "anytls": {"password": "password-" + email},
        "snell": {"psk": "key-" + email, "version": 6, "v6Mode": "unshaped"},
        "mieru": {"username": email, "password": "password-" + email},
        "socks": {"user": email, "pass": "password-" + email},
        "socks5": {"user": email, "pass": "password-" + email},
    }
    return {"email": email, **values[protocol]}


def inbound(protocol, users):
    key = (
        "accounts"
        if protocol in {"socks", "socks5"}
        else "users"
        if protocol in {"anytls", "snell", "mieru"}
        else "clients"
    )
    return {
        "tag": "edge",
        "protocol": protocol,
        "port": 14443,
        "settings": {key: users},
        "sniffing": {"enabled": True},
    }


def entry(protocol, credential, enabled):
    return {"tag": "edge", "protocol": protocol, "client": credential, "enabled": enabled}


async def execute(agent, entries, **extra):
    return await agent.execute(
        {
            "request_id": uuid4().hex,
            "method": "POST",
            "path": ENDPOINT,
            "body": {"entries": entries, "revision": revision(entries), **extra},
        }
    )


def setup(config, protocol="vless", count=2):
    users = [client(protocol, name) for name in ["alice", "bob"][:count]]
    original = {
        "inbounds": [inbound(protocol, users)],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": [{"marktag": "route", "outboundTag": "direct", "user": ["alice"]}]},
    }
    config.xray_config.write_text(json.dumps(original))
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=True)
    agent.runtime.restart = AsyncMock()
    return agent, original, users


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_revocation_preserves_other_users_and_restores_same_credential(config, protocol):
    agent, original, users = setup(config, protocol)
    try:
        result = await execute(agent, [entry(protocol, users[0], False)])
        assert result["status"] == 200, result
        key = "users" if protocol in {"anytls", "snell", "mieru"} else "clients"
        assert agent.runtime.read()["inbounds"][0]["settings"][key] == [users[1]]
        assert agent.runtime.read()["routing"] == original["routing"]
        assert result["body"]["access"]["disabled"] == 1
        result = await execute(agent, [entry(protocol, users[0], True)])
        assert result["status"] == 200, result
        assert agent.runtime.read()["inbounds"][0]["settings"][key] == [users[1], users[0]]
        assert agent.runtime.restart.await_count == 2
    finally:
        await agent.close()


async def test_access_rejects_direct_managed_egress_client_mutation_before_write(config):
    agent, original, _ = setup(config)
    managed = {
        "email": "open_node_egress__source__target",
        "id": "managed-id",
    }
    original["inbounds"][0]["settings"]["clients"].append(managed)
    config.xray_config.write_text(json.dumps(original))
    try:
        result = await execute(agent, [entry("vless", managed, False)])
        assert result["status"] == 400, result
        assert "dedicated server egress workflow" in result["error"]
        assert agent.runtime.read() == original
        agent.runtime.restart.assert_not_awaited()
    finally:
        await agent.close()


async def test_access_preserves_managed_egress_and_warp_records_around_write(config):
    agent, original, users = setup(config)
    original["inbounds"][0]["settings"]["clients"].append(
        {"email": "open_node_egress__source__target", "id": "managed-id"}
    )
    original["outbounds"].extend(
        [
            {"tag": "managed-egress:source:target", "protocol": "vless"},
            {"tag": "warp-v4", "protocol": "wireguard", "settings": {"secretKey": "v4"}},
            {"tag": "warp-v6", "protocol": "wireguard", "settings": {"secretKey": "v6"}},
        ]
    )
    original["routing"]["rules"].append(
        {
            "marktag": "managed-egress-rule:source:target",
            "outboundTag": "managed-egress:source:target",
        }
    )
    config.xray_config.write_text(json.dumps(original))
    state = {"config": copy.deepcopy(original)}

    def read():
        return copy.deepcopy(state["config"])

    async def write(value, *, restart=False, expected=None, **_):
        if expected is not None and state["config"] != expected:
            raise RuntimeFailure("Xray configuration changed during the guarded update")
        state["config"] = copy.deepcopy(value)
        return {"success": True, "restart_required": not restart}

    agent.runtime.read = Mock(side_effect=read)
    agent.runtime.write = AsyncMock(side_effect=write)
    try:
        result = await execute(agent, [entry("vless", users[0], False)])
        assert result["status"] == 200, result
        assert_managed_egress_preserved(original, agent.runtime.read())
    finally:
        await agent.close()


@pytest.mark.parametrize("protocol", ["socks", "socks5"])
async def test_xray_socks_access_uses_official_accounts_container(config, protocol):
    agent, original, users = setup(config, protocol)
    state = {"config": copy.deepcopy(original)}

    def read():
        return copy.deepcopy(state["config"])

    async def write(value, *, restart=False, expected=None, **_):
        if expected is not None and state["config"] != expected:
            raise RuntimeFailure("Xray configuration changed during the guarded update")
        state["config"] = copy.deepcopy(value)
        return {"success": True, "restart_required": not restart}

    agent.runtime.read = Mock(side_effect=read)
    agent.runtime.write = AsyncMock(side_effect=write)
    try:
        assert "accounts" in original["inbounds"][0]["settings"]
        assert "clients" not in original["inbounds"][0]["settings"]
        result = await execute(agent, [entry(protocol, users[0], False)])
        assert result["status"] == 200, result
        settings = agent.runtime.read()["inbounds"][0]["settings"]
        assert settings["accounts"] == [users[1]]
        assert "clients" not in settings
    finally:
        await agent.close()


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_last_user_suspends_listener_and_survives_agent_restart(config, protocol):
    agent, original, users = setup(config, protocol, count=1)
    result = await execute(agent, [entry(protocol, users[0], False)])
    assert result["status"] == 200, result
    assert agent.runtime.read()["inbounds"] == []
    record = agent.operations.subscription_access.load()["edge"]
    assert record["phase"] == "suspended"
    assert users[0] not in record["inbound"]["settings"].values()
    await agent.close()
    agent = Agent(config)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    try:
        result = await execute(agent, [entry(protocol, users[0], True)])
        assert result["status"] == 200, result
        restored = agent.runtime.read()["inbounds"][0]
        assert restored["port"] == original["inbounds"][0]["port"]
        key = "users" if protocol in {"anytls", "snell", "mieru"} else "clients"
        assert restored["settings"][key] == users
        assert agent.operations.subscription_access.load() == {}
    finally:
        await agent.close()


@pytest.mark.parametrize("enabled", [True, False])
async def test_identity_conflicts_are_atomic_and_do_not_echo_secrets(config, enabled):
    agent, original, users = setup(config)
    altered = {**users[1], "id": "independently-modified-secret"}
    try:
        result = await execute(
            agent, [entry("vless", users[0], False), entry("vless", altered, enabled)]
        )
        assert result["status"] == 400
        assert "identity changed" in result["error"]
        assert "independently-modified-secret" not in result["error"]
        assert agent.runtime.read() == original
        agent.runtime.restart.assert_not_awaited()
    finally:
        await agent.close()


async def test_shared_password_cannot_be_reported_revoked(config):
    agent, original, users = setup(config, "trojan")
    users[1]["password"] = users[0]["password"]
    config.xray_config.write_text(json.dumps(original))
    try:
        result = await execute(agent, [entry("trojan", users[0], False)])
        assert result["status"] == 400
        assert "shared" in result["error"]
        assert agent.runtime.read() == original
        result = await execute(agent, [entry("trojan", user, False) for user in users])
        assert result["status"] == 200
    finally:
        await agent.close()


async def test_restore_failure_retains_the_suspended_template(config):
    agent, _, users = setup(config, "mieru", count=1)
    try:
        assert (await execute(agent, [entry("mieru", users[0], False)]))["status"] == 200
        agent.runtime.restart.side_effect = [RuntimeFailure("restart failed"), None]
        result = await execute(agent, [entry("mieru", users[0], True)])
        assert result["status"] == 400
        assert agent.runtime.read()["inbounds"] == []
        assert "edge" in agent.operations.subscription_access.load()
        agent.runtime.restart.side_effect = None
        assert (await execute(agent, [entry("mieru", users[0], True)]))["status"] == 200
    finally:
        await agent.close()


async def test_interrupted_final_journal_save_rolls_back_xray(config):
    agent, original, users = setup(config, count=1)
    try:
        access = agent.operations.subscription_access
        save = access.save

        def interrupt(value):
            if value.get("edge", {}).get("phase") == "suspended":
                raise OSError("interrupted after config write")
            save(value)

        access.save = interrupt
        assert (await execute(agent, [entry("vless", users[0], False)]))["status"] == 500
        assert agent.runtime.read() == original
        access.save = save
        assert access.load() == {}
        assert (await execute(agent, [entry("vless", users[0], False)]))["status"] == 200
        assert agent.runtime.read()["inbounds"] == []
    finally:
        await agent.close()


@pytest.mark.parametrize(
    "change", ["revision", "no_restart", "duplicate", "protocol", "email", "limiter"]
)
async def test_invalid_requests_do_not_write(config, change):
    agent, original, users = setup(config)
    item = entry("vless", users[0], False)
    entries, extra = [item], {}
    if change == "revision":
        extra["revision"] = "a" * 64
    elif change == "no_restart":
        extra["no_restart"] = True
    elif change == "duplicate":
        entries.append(copy.deepcopy(item))
    elif change == "protocol":
        item["protocol"] = "socks"
    elif change == "email":
        item["client"] = {"id": users[0]["id"]}
    else:
        item["limiter"] = {"inbound_tag": "wrong", "user": {"email": "alice"}}
    try:
        assert (await execute(agent, entries, **extra))["status"] == 400
        assert agent.runtime.read() == original
    finally:
        await agent.close()


async def test_guarded_write_rejects_independent_file_changes(config):
    agent, original, users = setup(config)
    changed = {**original, "log": {"loglevel": "error"}}

    async def provision(*args):
        config.xray_config.write_text(json.dumps(changed))

    agent.runtime.limiter.provision = provision
    try:
        result = await execute(agent, [entry("vless", users[0], False)])
        assert result["status"] == 400
        assert "changed during" in result["error"]
        assert agent.runtime.read() == changed
    finally:
        await agent.close()


async def test_multiple_suspended_listeners_restore_in_original_order(config):
    agent, original, users = setup(config, count=1)
    original["inbounds"] = [
        {**copy.deepcopy(original["inbounds"][0]), "tag": tag, "port": 14000 + index}
        for index, tag in enumerate(["z-last", "a-first", "m-middle"])
    ]
    config.xray_config.write_text(json.dumps(original))
    try:
        entries = [
            {**entry("vless", users[0], False), "tag": tag}
            for tag in ["a-first", "m-middle", "z-last"]
        ]
        assert (await execute(agent, entries))["status"] == 200
        for item in entries:
            item["enabled"] = True
        assert (await execute(agent, entries))["status"] == 200
        assert agent.runtime.read() == original
    finally:
        await agent.close()


async def test_restore_rejects_an_independently_changed_routing_target(config):
    agent, original, users = setup(config)
    item = entry("vless", users[0], True)
    item["routing_user_additions"] = [
        {"marktag": "route", "outbound_tag": "different", "user_email": "alice"}
    ]
    try:
        result = await execute(agent, [item])
        assert result["status"] == 400
        assert "routing target changed" in result["error"]
        assert agent.runtime.read() == original
    finally:
        await agent.close()
