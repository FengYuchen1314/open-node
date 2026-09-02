import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest
import yaml
from open_node_agent.config import AgentConfig
from open_node_agent.managed_protocols import (
    ManagedProtocols,
    ManagedProtocolsRequest,
    MihomoRuntime,
    compile_config,
)
from open_node_agent.runtime import RuntimeFailure
from open_node_agent.subscription_access import AccessRequest, SubscriptionAccess, revision
from pydantic import ValidationError


def user(profile, name="alice"):
    if profile.startswith("vless"):
        return {"name": name, "uuid": str(uuid4())}
    return {"name": name, "password": "correct horse battery staple"}


def listener(profile, index=0, *, users=None, **changes):
    configs = {
        "vless_reality_vision": {
            "sni": f"vision-{index}.example.com",
            "reality_private_key": "A" * 43,
            "reality_short_id": f"{index + 1:016x}",
        },
        "vless_xhttp_reality_xmux": {
            "sni": f"xhttp-{index}.example.com",
            "reality_private_key": "B" * 43,
            "reality_short_id": f"{index + 11:016x}",
            "xhttp_path": "/managed/xhttp",
            "xhttp_host": f"cdn-{index}.example.com",
        },
        "anytls_shadowtls": {"sni": f"anytls-{index}.example.com"},
        "mieru": {},
        "socks5": {},
    }
    value = {
        "tag": f"managed-{index}",
        "node_id": str(uuid4()),
        "profile": profile,
        "listen": "127.0.0.1" if profile not in {"mieru", "socks5"} else "0.0.0.0",
        "port": 12000 + index,
        "enabled": True,
        "client_config": {},
        "server_config": configs[profile],
        "users": [user(profile)] if users is None else users,
    }
    value.update(changes)
    return value


def request(*listeners, revision_value="a" * 64):
    return ManagedProtocolsRequest.model_validate(
        {"revision": revision_value, "listeners": list(listeners)}
    )


def test_compiles_all_five_official_listener_profiles_without_passthrough():
    profiles = [
        "vless_reality_vision",
        "vless_xhttp_reality_xmux",
        "anytls_shadowtls",
        "mieru",
        "socks5",
    ]
    compiled = compile_config(
        request(*(listener(profile, i) for i, profile in enumerate(profiles)))
    )
    assert set(compiled) == {"mode", "log-level", "allow-lan", "listeners", "rules"}
    assert [item["type"] for item in compiled["listeners"]] == [
        "vless",
        "vless",
        "anytls",
        "mieru",
        "socks",
    ]
    vision, xhttp, anytls, mieru, socks = compiled["listeners"]
    assert vision["users"][0]["flow"] == "xtls-rprx-vision"
    assert vision["reality-config"]["dest"] == "vision-0.example.com:443"
    assert xhttp["xhttp-config"] == {
        "path": "/managed/xhttp",
        "host": "cdn-1.example.com",
    }
    assert anytls["users"] == {"alice": "correct horse battery staple"}
    assert anytls["shadow-tls"] == {
        "enable": True,
        "version": 3,
        "users": [{"name": "alice", "password": "correct horse battery staple"}],
        "handshake": {"dest": "anytls-2.example.com:443"},
    }
    assert mieru["transport"] == "TCP"
    assert mieru["users"] == {"alice": "correct horse battery staple"}
    assert socks["users"] == [
        {"username": "alice", "password": "correct horse battery staple"}
    ]
    assert all(
        "client_config" not in item and "node_id" not in item
        for item in compiled["listeners"]
    )


def test_compiler_matches_the_real_v11930_validated_fixture():
    values = [
        listener(
            "vless_reality_vision",
            0,
            tag="managed-vision",
            port=12000,
            server_config={
                "sni": "vision.example.com",
                "reality_private_key": "jNXHt1yRo0vDuchQlIP6Z0ZvjT3KtzVI-T4E7RoLJS0",
                "reality_short_id": "0123456789abcdef",
            },
            users=[{"name": "alice", "uuid": "9d0cb9d0-964f-4ef6-897d-6c6b3ccf9e68"}],
        ),
        listener(
            "vless_xhttp_reality_xmux",
            1,
            tag="managed-xhttp",
            port=12001,
            server_config={
                "sni": "xhttp.example.com",
                "reality_private_key": "jNXHt1yRo0vDuchQlIP6Z0ZvjT3KtzVI-T4E7RoLJS0",
                "reality_short_id": "1123456789abcdef",
                "xhttp_path": "/managed/xhttp",
                "xhttp_host": "cdn.example.com",
            },
            users=[{"name": "bob", "uuid": "64c9a30b-e08b-4be4-951a-a91c20c7bcb9"}],
        ),
        listener(
            "anytls_shadowtls",
            2,
            tag="managed-anytls",
            port=12002,
            server_config={"sni": "anytls.example.com"},
            users=[{"name": "carol", "password": "anytls-secret"}],
        ),
        listener(
            "mieru",
            3,
            tag="managed-mieru",
            port=12003,
            users=[{"name": "dave", "password": "mieru-secret"}],
        ),
        listener(
            "socks5",
            4,
            tag="managed-socks",
            port=12004,
            users=[{"name": "erin", "password": "socks-secret"}],
        ),
    ]
    actual = compile_config(request(*values))
    fixture = Path(__file__).parent / "fixtures" / "mihomo-v1.19.30-all.yaml"
    assert actual == yaml.safe_load(fixture.read_text())


def test_empty_or_disabled_listener_is_declared_but_never_materialized():
    desired = request(
        listener("socks5", users=[]),
        listener("mieru", 1, enabled=False),
    )
    assert len(desired.listeners) == 2
    assert compile_config(desired)["listeners"] == []


def test_client_config_defaults_and_bindings_reject_only_actual_overlap():
    without_client = listener("mieru")
    del without_client["client_config"]
    assert request(without_client).listeners[0].client_config.model_dump() == {
        "server": None,
        "port": None,
    }
    distinct = request(
        listener("mieru", 0, listen="192.0.2.10", port=12000),
        listener("socks5", 1, listen="192.0.2.11", port=12000),
    )
    assert len(distinct.listeners) == 2
    with pytest.raises(ValidationError, match="overlap"):
        request(
            listener("mieru", 0, port=12000),
            listener("socks5", 1, listen="127.0.0.1", port=12000),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"server_config": {"sni": "ok.example", "command": "include /etc/shadow"}},
        {"server_config": {"sni": "ok.example", "certificate": "/etc/shadow"}},
        {"listen": "example.com"},
        {"listen": "0.0.0.0"},
        {"tag": "../../unit"},
    ],
)
def test_dto_rejects_directives_paths_and_nonliteral_loopback(change):
    value = listener("anytls_shadowtls")
    value.update(change)
    with pytest.raises(ValidationError):
        request(value)


def test_xhttp_rejects_traversal_query_and_unknown_fields():
    for server_config in [
        {
            "sni": "x.example",
            "reality_private_key": "A" * 43,
            "reality_short_id": "0123456789abcdef",
            "xhttp_path": "/../private",
        },
        {
            "sni": "x.example",
            "reality_private_key": "A" * 43,
            "reality_short_id": "0123456789abcdef",
            "xhttp_path": "/ok?include=/etc/passwd",
        },
    ]:
        with pytest.raises(ValidationError):
            request(listener("vless_xhttp_reality_xmux", server_config=server_config))


def config(tmp_path):
    return AgentConfig(
        master_url="http://panel.test",
        token="fixture-token",
        allow_insecure_http=True,
        state_dir=tmp_path / "state",
        xray_binary=tmp_path / "xray",
        xray_config=tmp_path / "xray.json",
        mihomo_binary=tmp_path / "mihomo",
        mihomo_config=tmp_path / "private" / "mihomo.yaml",
    )


async def test_runtime_validation_uses_only_pinned_binary_test_file_flags(
    tmp_path, monkeypatch
):
    runtime = MihomoRuntime(config(tmp_path))
    command = AsyncMock(return_value=(0, "configuration file test is successful"))
    monkeypatch.setattr("open_node_agent.managed_protocols.run_command", command)
    try:
        assert await runtime.validate({"listeners": [], "rules": ["MATCH,DIRECT"]}) == (
            True,
            "configuration file test is successful",
        )
        args = command.await_args.args
        assert args[0] == str(runtime.binary)
        assert args[1:3] == ("-t", "-f")
        assert Path(args[3]).parent == runtime.path.parent
        assert not Path(args[3]).exists()
    finally:
        runtime.log_handler.close()


async def test_cancelled_runtime_start_stops_the_spawned_candidate(tmp_path, monkeypatch):
    class Output:
        async def read(self, _size):
            return b""

    class Process:
        def __init__(self):
            self.returncode = None
            self.stdout = Output()
            self.terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        def kill(self):
            self.returncode = -9

        async def wait(self):
            return self.returncode

    runtime = MihomoRuntime(config(tmp_path))
    process = Process()
    runtime.read_raw = Mock(return_value=b"mode: rule\nlisteners: []\n")
    runtime.validate = AsyncMock(return_value=(True, "valid"))
    monkeypatch.setattr(
        "open_node_agent.managed_protocols.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr(
        "open_node_agent.managed_protocols.os.geteuid", lambda: 65534, raising=False
    )
    try:
        task = asyncio.create_task(runtime.start())
        for _ in range(100):
            if runtime.process is process:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.terminated is True
        assert runtime.process is None
        assert runtime.loaded_sha256 is None
    finally:
        runtime.log_handler.close()


async def test_put_is_revision_idempotent_and_persists_empty_declaration(tmp_path, monkeypatch):
    manager = ManagedProtocols(config(tmp_path))
    monkeypatch.setattr(
        "open_node_agent.managed_protocols.atomic_write",
        lambda path, content: path.write_bytes(content),
    )
    manager.runtime.validate = AsyncMock(return_value=(True, "configuration file is valid"))
    manager.runtime.write = AsyncMock(return_value={"running": False})
    body = {
        "revision": "b" * 64,
        "listeners": [listener("socks5", users=[])],
    }
    try:
        first = await manager.apply(body)
        assert first["changed"] is True
        assert manager.load().listeners[0].users == []
        second = await manager.apply({**body, "listeners": []})
        assert second == {"revision": "b" * 64, "changed": False, "listener_count": 1}
        manager.runtime.write.assert_awaited_once()
    finally:
        manager.runtime.log_handler.close()


async def test_put_rejects_tag_or_binding_already_owned_by_xray(tmp_path):
    xray = SimpleNamespace(
        read=lambda: {
            "inbounds": [
                {"tag": "legacy", "listen": "127.0.0.1", "port": 12000}
            ]
        }
    )
    manager = ManagedProtocols(config(tmp_path), xray)
    try:
        with pytest.raises(RuntimeFailure, match="conflicts with an Xray inbound"):
            await manager.apply(
                {
                    "revision": "c" * 64,
                    "listeners": [
                        listener("vless_reality_vision", tag="legacy", port=13000)
                    ],
                }
            )
        with pytest.raises(RuntimeFailure, match="conflicts with an Xray inbound"):
            await manager.apply(
                {
                    "revision": "d" * 64,
                    "listeners": [listener("vless_reality_vision", port=12000)],
                }
            )
        xray.read = lambda: {"inbounds": "not-a-list"}
        with pytest.raises(RuntimeFailure, match="inspect Xray inbound ownership"):
            await manager.apply(
                {
                    "revision": "e" * 64,
                    "listeners": [listener("vless_reality_vision", port=14000)],
                }
            )
        xray.read = lambda: {"inbounds": []}
        manager.xray_reserved_inbounds = lambda: [
            {"tag": "managed-0", "listen": "127.0.0.1", "port": 14000}
        ]
        with pytest.raises(RuntimeFailure, match="conflicts with an Xray inbound"):
            await manager.apply(
                {
                    "revision": "f" * 64,
                    "listeners": [listener("vless_reality_vision", port=15000)],
                }
            )
    finally:
        manager.runtime.log_handler.close()


async def test_same_revision_and_monitor_fail_closed_after_xray_drift(tmp_path):
    xray = SimpleNamespace(read=lambda: {"inbounds": []})
    manager = ManagedProtocols(config(tmp_path), xray)
    desired = request(listener("vless_reality_vision"), revision_value="9" * 64)
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(desired.model_dump_json())
    manager.runtime.running = AsyncMock(return_value=True)
    manager.runtime.stop = AsyncMock()
    xray.read = lambda: {"inbounds": [{"tag": "managed-0", "port": 15000}]}
    try:
        with pytest.raises(RuntimeFailure, match="tag conflicts"):
            await manager.apply({"revision": "9" * 64, "listeners": []})
        with pytest.raises(RuntimeFailure, match="tag conflicts"):
            await manager.ensure_started()
        manager.runtime.stop.assert_awaited_once()
    finally:
        manager.runtime.log_handler.close()


def test_reverse_xray_candidate_cannot_claim_managed_tag_or_port(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    desired = request(listener("vless_reality_vision"))
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(desired.model_dump_json())
    try:
        with pytest.raises(RuntimeFailure, match="tag conflicts"):
            manager.assert_xray_compatible(
                {"inbounds": [{"tag": "managed-0", "port": 15000}]}
            )
        with pytest.raises(RuntimeFailure, match="listener conflicts"):
            manager.assert_xray_compatible(
                {"inbounds": [{"tag": "legacy", "port": 12000}]}
            )
        with pytest.raises(RuntimeFailure, match="safely inspect"):
            manager.assert_xray_compatible({"inbounds": [{"port": "12000"}]})
    finally:
        manager.runtime.log_handler.close()


async def test_monitor_reconciles_power_loss_between_config_and_state(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    desired = request(listener("socks5"))
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(desired.model_dump_json())
    manager.runtime.read_raw = Mock(return_value=b"stale candidate\n")
    manager.runtime.write = AsyncMock(return_value={"running": True})
    try:
        await manager.ensure_started()
        manager.runtime.write.assert_awaited_once_with(
            yaml.safe_dump(
                compile_config(desired), sort_keys=False, allow_unicode=False
            ).encode(),
            activate=True,
            expected=b"stale candidate\n",
        )
    finally:
        manager.runtime.log_handler.close()


async def test_monitor_restarts_a_running_stale_generation(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    desired = request(listener("socks5"))
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(desired.model_dump_json())
    encoded = yaml.safe_dump(
        compile_config(desired), sort_keys=False, allow_unicode=False
    ).encode()
    manager.runtime.read_raw = Mock(return_value=encoded)
    manager.runtime.running = AsyncMock(return_value=True)
    manager.runtime.loaded_sha256 = "0" * 64
    manager.runtime.restart = AsyncMock()
    try:
        await manager.ensure_started()
        manager.runtime.restart.assert_awaited_once()
    finally:
        manager.runtime.log_handler.close()


def access_entry(protocol, client, enabled):
    return SimpleNamespace(
        tag="managed-0",
        protocol=protocol,
        client=client,
        enabled=enabled,
        routing_user_additions=[],
        limiter=None,
    )


def test_access_add_remove_accepts_user_pass_aliases(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    initial = request(listener("socks5", users=[]))
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(initial.model_dump_json())
    try:
        added = manager.access_candidate(
            [access_entry("socks", {"user": "alice", "pass": "secret"}, True)]
        )
        assert added.listeners[0].users[0].model_dump() == {
            "name": "alice",
            "uuid": None,
            "password": "secret",
        }
        manager.state_path.write_text(added.model_dump_json())
        removed = manager.access_candidate(
            [access_entry("socks5", {"username": "alice", "password": "secret"}, False)]
        )
        assert removed.listeners[0].users == []
    finally:
        manager.runtime.log_handler.close()


def test_access_accepts_email_only_socks_name_and_rejects_changed_secret(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    initial = request(listener("socks5", users=[]))
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(initial.model_dump_json())
    try:
        entry = {
            "tag": "managed-0",
            "protocol": "socks5",
            "client": {"email": "socks@example.test", "password": "secret"},
            "enabled": True,
        }
        body = {"revision": revision([entry]), "entries": [entry]}
        candidate = manager.access_candidate(
            [access_entry("socks5", entry["client"], True)]
        )
        assert candidate.listeners[0].users[0].name == "socks@example.test"
        AccessRequest.model_validate(body)
        manager.state_path.write_text(candidate.model_dump_json())
        with pytest.raises(RuntimeFailure, match="identity changed"):
            manager.access_candidate(
                [
                    access_entry(
                        "socks5",
                        {"email": "socks@example.test", "password": "changed"},
                        False,
                    )
                ]
            )
    finally:
        manager.runtime.log_handler.close()


def test_access_accepts_email_only_mieru_name(tmp_path):
    entry = {
        "tag": "managed-0",
        "protocol": "mieru",
        "client": {"email": "mieru@example.test", "password": "secret"},
        "enabled": True,
    }
    AccessRequest.model_validate(
        {"revision": revision([entry]), "entries": [entry]}
    )


def test_access_candidate_revalidates_maximum_user_count(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    initial = request(
        listener(
            "socks5",
            users=[
                {"name": f"user-{index}", "password": f"secret-{index}"}
                for index in range(10_000)
            ],
        )
    )
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(initial.model_dump_json())
    try:
        with pytest.raises(RuntimeFailure, match="listener constraints"):
            manager.access_candidate(
                [
                    access_entry(
                        "socks5",
                        {"username": "overflow", "password": "overflow-secret"},
                        True,
                    )
                ]
            )
        assert len(manager.load().listeners[0].users) == 10_000
    finally:
        manager.runtime.log_handler.close()


def test_access_disable_rejects_alias_change_for_same_password(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    initial = request(
        listener(
            "anytls_shadowtls",
            users=[{"name": "original", "password": "stable-secret"}],
        )
    )
    manager.state_path.parent.mkdir(parents=True, exist_ok=True)
    manager.state_path.write_text(initial.model_dump_json())
    try:
        with pytest.raises(RuntimeFailure, match="identity changed"):
            manager.access_candidate(
                [
                    access_entry(
                        "anytls",
                        {"email": "renamed", "password": "stable-secret"},
                        False,
                    )
                ]
            )
        assert manager.load().listeners[0].users[0].name == "original"
    finally:
        manager.runtime.log_handler.close()


async def test_xray_final_journal_failure_rolls_back_committed_runtime():
    access = SubscriptionAccess.__new__(SubscriptionAccess)
    access.runtime = SimpleNamespace(
        limiter=SimpleNamespace(provision=AsyncMock(return_value=None)),
        write=AsyncMock(
            side_effect=[
                {"success": True, "restart_required": False},
                {"success": True, "restart_required": False},
            ]
        ),
    )
    access.save = Mock(side_effect=[None, OSError("journal fsync failed"), None])
    original = {"inbounds": []}
    candidate = {"inbounds": [{"tag": "legacy"}]}
    plan = {
        "original": original,
        "config": candidate,
        "saved": {},
        "staged": {},
        "suspended": set(),
        "limits": [],
    }
    with pytest.raises(OSError, match="journal fsync failed"):
        await access._commit_xray(plan)
    assert access.runtime.write.await_args_list == [
        call(candidate, restart=True, expected=original),
        call(original, restart=True, expected=candidate),
    ]
    assert access.save.call_args_list == [call({}), call({}), call({})]


async def test_scan_reports_status_and_listener_shape_without_secrets(tmp_path):
    manager = ManagedProtocols(config(tmp_path))
    desired = request(listener("vless_reality_vision"))
    manager.state_path.write_text(desired.model_dump_json())
    manager.runtime.running = AsyncMock(return_value=True)
    manager.runtime.version = AsyncMock(return_value="Mihomo Meta v1.19.30 linux amd64")
    try:
        result = await manager.scan()
        encoded = json.dumps(result)
        assert result["mihomo_running"] is True
        assert result["managed_protocol_listeners"] == [
            {
                "tag": "managed-0",
                "profile": "vless_reality_vision",
                "listen": "127.0.0.1",
                "port": 12000,
                "enabled": True,
                "active": True,
            }
        ]
        assert "reality_private_key" not in encoded
        assert desired.listeners[0].users[0].uuid not in encoded
    finally:
        manager.runtime.log_handler.close()


async def test_dual_runtime_failure_rolls_back_mihomo_candidate():
    vless = listener("vless_reality_vision")
    managed_before = request(vless)
    managed_after = copy.deepcopy(managed_before)
    manager = SimpleNamespace(
        tags=lambda: {"managed-0"},
        load=lambda: managed_before,
        access_candidate=lambda entries: managed_after,
        validate_request=AsyncMock(return_value=b"valid"),
        commit_request=AsyncMock(),
        rollback_request=AsyncMock(),
        assert_xray_compatible=lambda config: None,
    )
    access = SubscriptionAccess.__new__(SubscriptionAccess)
    access.runtime = SimpleNamespace(read=lambda: {"inbounds": []})
    access.managed_protocols = manager
    access._prepare_xray = AsyncMock(return_value={"candidate": True})
    access._commit_xray = AsyncMock(side_effect=RuntimeFailure("Xray restart failed"))
    entries = [
        {
            "tag": "managed-0",
            "protocol": "vless",
            "client": {
                "email": "managed@example.test",
                "id": vless["users"][0]["uuid"],
            },
            "enabled": True,
        },
        {
            "tag": "legacy-xray",
            "protocol": "vless",
            "client": {"email": "legacy@example.test", "id": str(uuid4())},
            "enabled": True,
        },
    ]
    body = {"revision": revision(entries), "entries": entries}
    with pytest.raises(RuntimeFailure, match="Xray restart failed"):
        await access.apply(body)
    manager.commit_request.assert_awaited_once_with(managed_after, expected=managed_before)
    manager.rollback_request.assert_awaited_once_with(managed_before, expected=managed_after)
