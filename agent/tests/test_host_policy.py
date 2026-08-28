import hashlib
import json
import os
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from open_node_agent import service
from open_node_agent.service import DeploymentError
from test_service import OLD, deployment  # noqa: F401


@pytest.fixture
def host(deployment, monkeypatch):  # noqa: F811
    instance, calls, properties = deployment
    instance.record.update(uid=12345, gid=12345)
    instance.record["unit_text"] = instance.unit_text().replace("RestartSec=3", "RestartSec=4")
    instance.save()
    instance.unit_file.write_text(instance.unit_text())
    instance.config.write_text('{"token": "private-secret", "auto_start": false}\n')
    instance.config.chmod(0o600)
    os.chown(instance.config, 12345, 12345)
    (instance.state / "commands.sqlite").write_bytes(b"preserved journal")
    monkeypatch.setattr(
        service.pwd,
        "getpwnam",
        lambda _: SimpleNamespace(
            pw_uid=12345,
            pw_gid=12345,
            pw_dir=str(instance.state),
        ),
    )
    monkeypatch.setattr(instance, "ready", lambda *args: None)
    monkeypatch.setattr(instance, "verify_policy", lambda *args, **kwargs: None)
    return instance, calls, properties


def tool(tmp_path, contents=b"trusted-binary"):
    path = tmp_path / "nexttrace"
    path.write_bytes(contents)
    path.chmod(0o644)
    return {"nexttrace": path, "digest": hashlib.sha256(contents).hexdigest(), "geoip": False}


def originals(instance):
    return {"config": instance.config.read_bytes(), "unit": instance.unit_file.read_bytes()}


def assert_restored(instance, before):
    instance.load()
    assert instance.record["schema"] == 1
    assert instance.record["pending"] is None
    assert not instance.record.get("policy_restore")
    assert instance.record["current"] == OLD and instance.record["previous"] is None
    assert not instance.record.get("network_diagnostics")
    assert instance.config.read_bytes() == before["config"]
    assert instance.unit_file.read_bytes() == before["unit"]
    assert not (instance.root / "runtime/nexttrace").exists()
    assert (instance.state / "commands.sqlite").read_bytes() == b"preserved journal"
    assert not list(instance.root.glob("policy-*"))


def test_host_policy_updates_only_owned_capabilities_and_is_idempotent(host):
    instance, calls, _ = host
    before = originals(instance)
    instance.policy(True)
    assert instance.record["network_diagnostics"]
    assert "RestartSec=4" in instance.unit_text()
    assert "CAP_SYS_ADMIN" not in instance.unit_text()
    assert instance.config.read_bytes() == before["config"]
    assert instance.record["current"] == OLD
    count = len(calls)
    instance.policy(True)
    assert len(calls) == count
    instance.policy(False)
    assert_restored(instance, before)
    assert not any("enable" in call or "disable" in call for call in calls)


def test_stopped_agent_stays_stopped(host):
    instance, calls, properties = host
    properties["ActiveState"] = "inactive"
    instance.policy(True)
    assert properties["ActiveState"] == "inactive"
    assert not any(call[:2] == ("systemctl", "start") for call in calls)


def test_tool_install_update_and_disable_preserve_settings(host, tmp_path):
    instance, calls, _ = host
    options = tool(tmp_path)
    instance.policy(True, **options)
    config = json.loads(instance.config.read_bytes())
    installed = instance.root / "runtime/nexttrace"
    assert config["token"] == "private-secret" and not config["auto_start"]
    assert config["nexttrace_binary"] == str(installed)
    assert config["nexttrace_geoip"] is False
    assert installed.read_bytes() == options["nexttrace"].read_bytes()
    assert installed.stat().st_mode & 0o777 == 0o755
    assert installed.stat().st_uid == 0
    version = next(call for call in calls if call[-1] == "--version")
    assert version[:4] == ("runuser", "-u", instance.user, "--")
    assert instance.config.stat().st_mode & 0o777 == 0o600
    assert instance.config.stat().st_uid == 12345
    unchanged = instance.config.read_bytes()
    instance.policy(False)
    assert instance.config.read_bytes() == unchanged and installed.exists()
    instance.policy(True)
    instance.policy(True, **tool(tmp_path, b"new-binary"))
    assert installed.read_bytes() == b"new-binary"
    instance.policy(True, geoip=True)
    assert json.loads(instance.config.read_bytes())["nexttrace_geoip"] is True


@pytest.mark.parametrize(
    "invalid",
    ["checksum", "consent", "pair", "off", "link", "hardlink", "setuid", "writable", "fifo"],
)
def test_unsafe_inputs_do_not_mutate_or_stop_service(host, tmp_path, invalid):
    instance, calls, _ = host
    before = originals(instance)
    options = tool(tmp_path)
    path = options["nexttrace"]
    if invalid == "checksum":
        options["digest"] = "0" * 64
    elif invalid == "consent":
        options.pop("geoip")
    elif invalid == "pair":
        options.pop("digest")
    elif invalid == "link":
        other = tmp_path / "link"
        other.symlink_to(path)
        options["nexttrace"] = other
    elif invalid == "hardlink":
        os.link(path, tmp_path / "hardlink")
    elif invalid == "setuid":
        path.chmod(0o4755)
    elif invalid == "writable":
        path.chmod(0o666)
    elif invalid == "fifo":
        path.unlink()
        os.mkfifo(path)
    with pytest.raises((DeploymentError, OSError)):
        instance.policy(invalid != "off", **options)
    assert not calls
    assert_restored(instance, before)


@pytest.mark.parametrize(
    "boundary", ["unit", "config", "tool", "reload", "preflight", "ready", "privileges"]
)
def test_failure_restores_exact_files_and_state(host, tmp_path, monkeypatch, boundary):
    instance, _, _ = host
    before = originals(instance)
    original_write, original_command = service.write_file, service.command
    paths = {name: item[0] for name, item in instance.policy_files().items()}
    failed = False

    def fail_once():
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected host failure")

    def write(path, *args, **kwargs):
        original_write(path, *args, **kwargs)
        if boundary in paths and path == paths[boundary]:
            fail_once()

    def command(*args, **kwargs):
        result = original_command(*args, **kwargs)
        if boundary == "reload" and args[:2] == ("systemctl", "daemon-reload"):
            fail_once()
        return result

    monkeypatch.setattr(service, "write_file", write)
    monkeypatch.setattr(service, "command", command)
    for name, target in (
        ("preflight", "preflight"),
        ("ready", "ready"),
        ("privileges", "verify_policy"),
    ):
        if boundary == name:
            monkeypatch.setattr(instance, target, lambda *args, **kwargs: fail_once())
    with pytest.raises(DeploymentError, match="previous host policy restored"):
        instance.policy(True, **tool(tmp_path))
    assert failed
    assert_restored(instance, before)


def interrupt(host, tmp_path, monkeypatch):
    instance, _, _ = host
    before = originals(instance)
    original = instance.recover_policy

    def crash(*args, **kwargs):
        raise OSError("simulated hard interruption")

    with monkeypatch.context() as patch:
        patch.setattr(instance, "preflight", crash)
        patch.setattr(instance, "recover_policy", crash)
        with pytest.raises(DeploymentError, match="recovery is incomplete"):
            instance.policy(True, **tool(tmp_path))
    instance.load()
    assert instance.record["schema"] == 2
    pending = instance.record["pending"]
    assert pending["kind"] == "policy"
    assert "private-secret" not in json.dumps(instance.record)
    directory = instance.policy_path(pending["id"])
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    assert instance.recover_policy == original
    return before, directory


def test_interrupted_policy_recovers_on_a_later_invocation(host, tmp_path, monkeypatch):
    instance, _, _ = host
    before, _ = interrupt(host, tmp_path, monkeypatch)
    instance.recover()
    assert_restored(instance, before)


@pytest.mark.parametrize(
    "foreign",
    [
        "unit",
        "config",
        "tool",
        "dropin",
        "snapshot",
        "metadata",
        "current",
        "hardlink",
        "directory",
    ],
)
def test_recovery_fails_closed_on_foreign_changes(host, tmp_path, monkeypatch, foreign):
    instance, calls, properties = host
    before, directory = interrupt(host, tmp_path, monkeypatch)
    if foreign in instance.policy_files():
        path = instance.policy_files()[foreign][0]
        data = path.read_bytes()
        path.write_bytes(b"foreign-content")
        restore = partial(path.write_bytes, data)
    elif foreign == "dropin":
        properties["DropInPaths"] = "external.conf"
        restore = partial(properties.update, DropInPaths="")
    elif foreign in {"snapshot", "metadata"}:
        path = directory / ("config.before" if foreign == "snapshot" else "undo.json")
        data = path.read_bytes()
        path.write_bytes(b"altered")
        restore = partial(path.write_bytes, data)
    elif foreign == "current":
        current = instance.root / "current"
        current.unlink()
        current.symlink_to(tmp_path)
        restore = partial(instance.set_current, OLD)
    elif foreign == "hardlink":
        other = tmp_path / "linked-unit"
        os.link(instance.unit_file, other)
        restore = other.unlink
    else:
        directory.chmod(0o755)
        restore = partial(directory.chmod, 0o700)
    count = len(calls)
    with pytest.raises((DeploymentError, OSError)):
        instance.recover()
    assert instance.record["pending"]
    assert len(calls) == count
    restore()
    instance.recover()
    assert_restored(instance, before)


def test_recovery_refuses_to_erase_a_previous_nexttrace(host, tmp_path, monkeypatch):
    instance, _, _ = host
    instance.policy(True, **tool(tmp_path))
    before = (instance.root / "runtime/nexttrace").read_bytes()

    def fail(*args):
        raise DeploymentError("invalid candidate")

    monkeypatch.setattr(instance, "preflight", fail)
    with pytest.raises(DeploymentError, match="previous host policy restored"):
        instance.policy(True, **tool(tmp_path, b"bad-update"))
    assert (instance.root / "runtime/nexttrace").read_bytes() == before
    assert instance.record["network_diagnostics"]


def test_helper_restoration_retry_does_not_roll_back_committed_policy(host, monkeypatch):
    instance, calls, _ = host
    finish = instance.finish_policy_helpers

    def fail():
        raise OSError("helper restart unavailable")

    monkeypatch.setattr(instance, "finish_policy_helpers", fail)
    with pytest.raises(DeploymentError, match="Policy committed"):
        instance.policy(True)
    instance.load()
    assert instance.record["schema"] == 1
    assert instance.record["pending"] is None
    assert instance.record["policy_restore"]
    assert instance.record["network_diagnostics"]
    for operation in (
        lambda: instance.policy(False),
        lambda: instance.upgrade(Path("candidate.whl")),
        instance.uninstall,
        lambda: instance.activate(OLD),
        lambda: instance.install(Path("candidate.whl")),
    ):
        with pytest.raises(DeploymentError, match="recover first"):
            operation()
    count = len(calls)
    monkeypatch.setattr(instance, "finish_policy_helpers", finish)
    instance.recover()
    assert len(calls) == count
    assert instance.record["network_diagnostics"]
    assert not instance.record.get("policy_restore")


def test_unknown_unit_policy_is_not_rewritten(host):
    instance, calls, _ = host
    instance.record["unit_text"] += "AmbientCapabilities=CAP_SYS_ADMIN\n"
    instance.unit_file.write_text(instance.unit_text())
    instance.save()
    with pytest.raises(DeploymentError, match="Unrecognized"):
        instance.policy(True)
    assert not calls


@pytest.mark.parametrize("after_write", [False, True])
def test_manifest_commit_failure_has_unambiguous_recovery(host, tmp_path, monkeypatch, after_write):
    instance, _, _ = host
    before = originals(instance)
    write = service.write_file
    saves = 0

    def fail_commit(path, *args, **kwargs):
        nonlocal saves
        if path == instance.manifest:
            saves += 1
        if path == instance.manifest and saves == 2 and not after_write:
            raise OSError("manifest commit failed before replacement")
        write(path, *args, **kwargs)
        if path == instance.manifest and saves == 2 and after_write:
            raise OSError("manifest directory sync failed after replacement")

    with monkeypatch.context() as patch:
        patch.setattr(service, "write_file", fail_commit)
        with pytest.raises(OSError):
            instance.policy(True, **tool(tmp_path))
    instance.load()
    assert instance.record["schema"] == (1 if after_write else 2)
    instance.recover()
    if after_write:
        assert instance.record["network_diagnostics"]
        assert not instance.record.get("policy_restore")
        assert not list(instance.root.glob("policy-*"))
    else:
        assert_restored(instance, before)


def test_busy_helper_is_not_stopped_for_policy_change(host, monkeypatch):
    instance, calls, _ = host
    instance.record["lifecycle"] = {"fixture": True}
    instance.save()
    helper = SimpleNamespace(
        verify_helper=lambda _: None,
        JobStore=lambda _: SimpleNamespace(rows=lambda _: [{"status": "queued"}]),
    )
    monkeypatch.setattr(service, "lifecycle_helper", lambda: helper)
    with pytest.raises(DeploymentError, match="job is pending"):
        instance.policy(True)
    assert not any(call[:2] == ("systemctl", "stop") for call in calls)
    assert instance.record["pending"] is None


def test_enabling_requires_a_diagnostics_capable_installed_package(host, monkeypatch):
    instance, calls, _ = host
    original = service.command

    def command(*args, **kwargs):
        if args[-1] == "import open_node_agent.diagnostics":
            raise DeploymentError("installed Agent has no diagnostics module")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "command", command)
    with pytest.raises(DeploymentError, match="no diagnostics"):
        instance.policy(True)
    assert not any(call[:2] == ("systemctl", "stop") for call in calls)
    assert instance.record["pending"] is None
