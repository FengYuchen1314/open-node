import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_node_agent.client import Agent
from open_node_agent.runtime import RuntimeFailure, XrayRuntime
from open_node_agent.service import DeploymentError
from open_node_agent.systemd_access import change_rule, rule, rule_path
from open_node_agent.systemd_runtime import Binding, SystemdRuntime, config_argument, private_config


@pytest.fixture
def external(config, monkeypatch):
    config.runtime_mode = "systemd"
    config.xray_binary = Path("/usr/bin/true")
    unit = {
        "Id": config.xray_service,
        "LoadState": "loaded",
        "NeedDaemonReload": False,
        "Transient": False,
        "FragmentPath": "/usr/bin/true",
        "DropInPaths": [],
        "ActiveState": "inactive",
        "SubState": "dead",
    }
    service = {
        "User": "node-fixture",
        "Group": "12345",
        "DynamicUser": False,
        "Type": "simple",
        "RemainAfterExit": False,
        "SupplementaryGroups": [],
        "ExecStartEx": [
            [
                str(config.xray_binary),
                [str(config.xray_binary), "run", "-config", str(config.xray_config)],
                [],
            ]
        ],
        "StandardInput": "null",
        "WorkingDirectory": "/",
        "Environment": [],
        "MainPID": 0,
    }
    for field in (
        "ExecConditionEx",
        "ExecStartPreEx",
        "ExecStartPostEx",
        "ExecStopEx",
        "ExecStopPostEx",
        "EnvironmentFiles",
        "PassEnvironment",
        "UnsetEnvironment",
        "RootDirectory",
        "RootImage",
        "BindPaths",
        "BindReadOnlyPaths",
        "TemporaryFileSystem",
        "PAMName",
        "PrivateUsers",
        "LoadCredential",
        "LoadCredentialEncrypted",
        "SetCredential",
        "SetCredentialEncrypted",
    ):
        service[field] = []
    adapter = SystemdRuntime(config, uid=12345, gid=12345)
    monkeypatch.setattr(
        "open_node_agent.systemd_runtime.pwd.getpwuid",
        lambda uid: SimpleNamespace(
            pw_name="node-fixture",
            pw_gid=12345,
            pw_dir="/var/lib/node-fixture",
            pw_shell="/usr/sbin/nologin",
        ),
    )
    monkeypatch.setattr("open_node_agent.systemd_runtime.private_config", lambda path, uid: None)

    async def bus(*args):
        if "LoadUnit" in args:
            return {"type": "o", "data": ["/fixture"]}
        data = unit if args[-1].endswith(".Unit") else service
        return {"type": "a{sv}", "data": [{key: {"data": value} for key, value in data.items()}]}

    adapter.bus = AsyncMock(side_effect=bus)
    return config, adapter, unit, service


async def test_binding_uses_structured_argv_environment_and_working_directory(external):
    _, adapter, _, service = external
    service["Environment"] = ["XRAY_LOCATION_ASSET=/opt/assets with spaces", "EXAMPLE=a=b"]
    service["WorkingDirectory"] = "/opt/assets with spaces"
    binding = await adapter.inspect()
    assert not binding.running
    assert binding.directory == "/opt/assets with spaces"
    assert binding.environment["EXAMPLE"] == "a=b"
    assert "PYTHONPATH" not in binding.environment


@pytest.mark.parametrize(
    "field,value",
    [
        ("Id", "different.service"),
        ("LoadState", "not-found"),
        ("NeedDaemonReload", True),
        ("Transient", True),
        ("FragmentPath", ""),
    ],
)
async def test_invalid_unit_binding_is_rejected(external, field, value):
    _, adapter, unit, _ = external
    unit[field] = value
    with pytest.raises(RuntimeFailure):
        await adapter.inspect()


@pytest.mark.parametrize(
    "field,value",
    [
        ("User", "root"),
        ("Group", "0"),
        ("DynamicUser", True),
        ("Type", "oneshot"),
        ("RemainAfterExit", True),
        ("SupplementaryGroups", ["root"]),
        ("ExecStartPreEx", [["/bin/true"]]),
        ("ExecStopEx", [["/bin/true"]]),
        ("EnvironmentFiles", [["/private/secrets", False]]),
        ("PassEnvironment", ["SECRET"]),
        ("RootDirectory", "/chroot"),
        ("BindPaths", [["/one", "/two", False, 0]]),
        ("StandardInput", "socket"),
        ("ImportCredential", ["secret"]),
    ],
)
async def test_unsupported_execution_context_is_rejected(external, field, value):
    _, adapter, _, service = external
    service[field] = value
    with pytest.raises(RuntimeFailure):
        await adapter.inspect()


@pytest.mark.parametrize("flags", [["privileged"], ["no-setuid"], ["ignore-failure"]])
async def test_exec_privilege_prefixes_are_rejected(external, flags):
    _, adapter, _, service = external
    service["ExecStartEx"][0][2] = flags
    with pytest.raises(RuntimeFailure, match="unprefixed"):
        await adapter.inspect()


@pytest.mark.parametrize(
    "tail",
    [
        ["-config", "/different.json"],
        ["-confdir", "/etc/xray"],
        ["-config", "/xray.json", "-c", "/xray.json"],
        ["-config", "$CONFIG"],
        ["-config", "/xray.json", "-format", "yaml"],
        ["-config"],
    ],
)
def test_ambiguous_or_different_config_arguments_are_rejected(tail):
    with pytest.raises(RuntimeFailure):
        config_argument(["/xray", "run", *tail], Path("/xray"), Path("/xray.json"))


@pytest.mark.parametrize(
    "tail",
    [["-c", "/xray.json"], ["-config=/xray.json"], ["--config", "/xray.json", "-format=json"]],
)
def test_single_explicit_json_argument_is_accepted(tail):
    config_argument(["/xray", "run", *tail], Path("/xray"), Path("/xray.json"))


async def test_missing_properties_fail_closed(external):
    _, adapter, _, service = external
    del service["ExecStartEx"]
    with pytest.raises(RuntimeFailure, match="Cannot verify"):
        await adapter.inspect()


async def test_live_process_must_match_the_configured_executable(external):
    _, adapter, unit, service = external
    unit.update(ActiveState="active", SubState="running")
    service["MainPID"] = os.getpid()
    with pytest.raises(RuntimeFailure, match="running process differs"):
        await adapter.inspect()


async def test_system_control_is_guarded_and_never_prompts(external, monkeypatch):
    config, adapter, unit, _ = external
    command = AsyncMock(return_value=(0, ""))
    monkeypatch.setattr("open_node_agent.systemd_runtime.run_command", command)
    await adapter.control("stop")
    command.assert_awaited_once_with(
        "systemctl", "--system", "--no-ask-password", "stop", config.xray_service
    )
    command.reset_mock()
    unit["Id"] = "other.service"
    with pytest.raises(RuntimeFailure):
        await adapter.control("stop")
    command.assert_not_awaited()


async def test_denied_control_reports_actionable_error_without_echoing_output(
    external, monkeypatch
):
    _, adapter, _, _ = external
    monkeypatch.setattr(
        "open_node_agent.systemd_runtime.run_command", AsyncMock(return_value=(1, "SECRET"))
    )
    with pytest.raises(RuntimeFailure, match="polkit") as error:
        await adapter.control("start")
    assert "SECRET" not in str(error.value)


async def test_invalid_binding_keeps_agent_health_and_scan_available(config):
    config.runtime_mode = "systemd"
    agent = Agent(config)
    agent.runtime.systemd.inspect = AsyncMock(side_effect=RuntimeFailure("Binding changed"))
    agent.runtime.systemd.control = AsyncMock()
    try:
        agent.control_contact()
        health = await agent.health_report()
        assert health["connected"] and not health["runtime_ready"]
        scan = await agent.operations.scan()
        assert scan["message"] == "Binding changed" and scan["inbounds"] == []
        original = config.xray_config.read_bytes()
        for action in (agent.runtime.start, agent.runtime.restart):
            with pytest.raises(RuntimeFailure, match="Binding changed"):
                await action()
        with pytest.raises(RuntimeFailure):
            await agent.runtime.write({"inbounds": []})
        assert config.xray_config.read_bytes() == original
        agent.runtime.systemd.control.assert_not_awaited()
        agent.runtime.systemd.inspect = AsyncMock(return_value=Binding(False, {}, "/"))
        assert (await agent.health_report())["runtime_ready"]
        assert (await agent.runtime.scan())["message"] is None
    finally:
        await agent.close()
    agent.runtime.systemd.control.assert_not_awaited()


async def test_restart_validates_before_stopping_an_existing_service(config):
    config.runtime_mode = "systemd"
    runtime = XrayRuntime(config)
    runtime.validate = AsyncMock(return_value=(False, "invalid config"))
    runtime.systemd.control = AsyncMock()
    try:
        with pytest.raises(RuntimeFailure, match="invalid config"):
            await runtime.restart()
        runtime.systemd.control.assert_not_awaited()
    finally:
        await runtime.close()


async def test_external_validation_uses_unit_environment(config, monkeypatch):
    config.runtime_mode = "systemd"
    runtime = XrayRuntime(config)
    runtime.systemd.inspect = AsyncMock(
        return_value=Binding(False, {"XRAY_LOCATION_ASSET": "/data"}, "/data")
    )
    command = AsyncMock(return_value=(0, "OK"))
    monkeypatch.setattr("open_node_agent.runtime.run_command", command)
    try:
        assert await runtime.validate({}) == (True, "OK")
        assert command.await_args.kwargs == {
            "env": {"XRAY_LOCATION_ASSET": "/data"},
            "cwd": "/data",
        }
        assert not list(config.xray_config.parent.glob(".open-node-test-*"))
    finally:
        await runtime.close()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable", "public", "directory"])
def test_external_config_rejects_unsafe_host_paths(tmp_path, kind):
    path = tmp_path / "xray.json"
    path.write_text("{}")
    path.chmod(0o600)
    if kind == "symlink":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        path = link
    elif kind == "hardlink":
        os.link(path, tmp_path / "other.json")
    elif kind == "writable":
        path.chmod(0o620)
    elif kind == "public":
        path.chmod(0o644)
    else:
        path = tmp_path
    with pytest.raises(RuntimeFailure):
        private_config(path, os.geteuid())


@pytest.mark.parametrize(
    "user,service",
    [
        ("root", "x.service"),
        ("nobody", "x.service"),
        ("node", "--other.service"),
        ('node"', "x.service"),
        ("node", "*.service"),
        ("node", "x.service\n"),
    ],
)
def test_polkit_rule_rejects_ambiguous_identity(user, service):
    with pytest.raises(DeploymentError):
        rule(user, service)


def test_polkit_grant_is_exact_and_non_cached():
    content = rule("node", "dedicated.service").decode()
    assert 'subject.user === "node"' in content
    assert 'action.lookup("unit") === "dedicated.service"' in content
    assert 'action.lookup("verb")' in content
    assert 'manage-units"' in content
    assert "manage-unit-files" not in content and "KEEP" not in content
    assert "reload-daemon" not in content and "kill" not in content
    assert rule_path("node", "one.service") != rule_path("node", "two.service")


def test_rule_directory_cannot_be_publicly_writable(tmp_path, monkeypatch):
    # The directory trust check is kept real; /tmp itself is intentionally not trusted.
    monkeypatch.setattr("open_node_agent.systemd_access.os.geteuid", lambda: 0)
    with pytest.raises(DeploymentError, match="trusted"):
        change_rule("node", "x.service", grant=True, directory=tmp_path)


async def test_malformed_bus_response_is_a_runtime_error(config, monkeypatch):
    adapter = SystemdRuntime(config)
    for output in ("not-json", "[]", '{"type":"s"}'):
        monkeypatch.setattr(
            "open_node_agent.systemd_runtime.run_command", AsyncMock(return_value=(0, output))
        )
        with pytest.raises(RuntimeFailure, match="Invalid systemd"):
            await adapter.bus("get-property")


async def test_binding_rechecked_after_validation_before_file_mutation(config):
    config.runtime_mode = "systemd"
    runtime = XrayRuntime(config)
    runtime.validate = AsyncMock(return_value=(True, "OK"))
    runtime.running = AsyncMock(return_value=True)
    runtime.systemd.inspect = AsyncMock(side_effect=RuntimeFailure("Definition changed"))
    original = config.xray_config.read_bytes()
    try:
        with pytest.raises(RuntimeFailure, match="Definition changed"):
            await runtime.write({"inbounds": []})
        assert config.xray_config.read_bytes() == original
    finally:
        await runtime.close()


async def test_writable_unit_file_blocks_binding(external, tmp_path):
    _, adapter, unit, _ = external
    path = tmp_path / "writable.service"
    path.write_text("[Service]\n")
    path.chmod(0o666)
    unit["DropInPaths"] = [str(path)]
    with pytest.raises(RuntimeFailure, match="root-owned"):
        await adapter.inspect()


async def test_wrong_property_type_is_reported_without_crashing(external):
    _, adapter, _, service = external
    service["Group"] = True
    with pytest.raises(RuntimeFailure, match="Cannot verify"):
        await adapter.inspect()


async def test_executable_symlink_loop_is_reported_without_crashing(external, tmp_path):
    config, adapter, _, service = external
    path = tmp_path / "loop"
    path.symlink_to(path)
    config.xray_binary = path
    service["ExecStartEx"][0][0] = str(path)
    service["ExecStartEx"][0][1][0] = str(path)
    with pytest.raises(RuntimeFailure, match="Cannot verify"):
        await adapter.inspect()


@pytest.mark.parametrize("mode", [0o600, 0o640])
def test_external_config_accepts_private_owner_file(tmp_path, mode):
    path = tmp_path / "xray.json"
    path.write_text("{}")
    path.chmod(mode)
    private_config(path, os.geteuid())


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
async def test_unsafe_external_config_does_not_block_telemetry(config, kind):
    config.runtime_mode = "systemd"
    config.xray_config.unlink()
    if kind == "fifo":
        os.mkfifo(config.xray_config, 0o600)
    else:
        config.xray_config.symlink_to("/etc/passwd")
    runtime = XrayRuntime(config)
    try:
        with pytest.raises(RuntimeFailure):
            runtime.read()
        assert await runtime.stats() is None
    finally:
        await runtime.close()
