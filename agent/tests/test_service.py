import json
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from open_node_agent import __version__, service
from open_node_agent.client import Agent
from open_node_agent.service import Deployment, DeploymentError, validate_root, wheel_info

OLD = "0.1.0-" + "a" * 16
NEW = "0.2.0-" + "b" * 16


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    root.mkdir(mode=0o755)
    units = tmp_path / "units"
    units.mkdir()
    instance = Deployment(root, "open-node-agent-test.service", unit_dir=units, timeout=3)
    instance.record = {
        "schema": 1,
        "installation_id": "test-install",
        "root": str(root),
        "unit": instance.unit,
        "user": instance.user,
        "uid": None,
        "gid": None,
        "status": "installed",
        "current": OLD,
        "previous": None,
        "pending": None,
        "releases": {OLD: {"version": "0.1.0"}, NEW: {"version": "0.2.0"}},
    }
    for name in ("state", "config", "runtime", "releases"):
        (root / name).mkdir()
    for release in (OLD, NEW):
        instance.release_path(release).mkdir()
    instance.set_current(OLD)
    instance.save()
    instance.unit_file.write_text(instance.unit_text())
    instance.unit_file.chmod(0o644)
    calls = []
    properties = {
        "ActiveState": "active",
        "MainPID": "42",
        "FragmentPath": str(instance.unit_file),
        "DropInPaths": "",
    }

    def run(*args, **kwargs):
        calls.append(tuple(map(str, args)))
        if args[:2] in {("systemctl", "stop"), ("systemctl", "disable")}:
            properties["ActiveState"] = "inactive"
        if args[:2] in {("systemctl", "start"), ("systemctl", "enable")}:
            properties["ActiveState"] = "active"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service, "command", run)
    monkeypatch.setattr(instance, "properties", lambda: properties)
    monkeypatch.setattr(instance, "preflight", lambda release: None)
    return instance, calls, properties


@pytest.mark.parametrize(
    "root",
    [
        "/",
        "/opt",
        "/etc/systemd",
        "/usr/local",
        "/var/lib",
        "/opt/open-node",
        "/tmp/../etc",
        "relative",
        "/opt/with space",
    ],
)
def test_system_paths_are_not_installation_roots(root):
    with pytest.raises(DeploymentError):
        validate_root(Path(root))


def test_symlink_ancestors_are_rejected(tmp_path):
    link = tmp_path / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(DeploymentError, match="symlink"):
        validate_root(link / "owned")


def test_wheel_identity_is_validated(tmp_path):
    wheel = tmp_path / "release.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "open_node_agent.dist-info/METADATA", "Name: open-node-agent\nVersion: 0.1.0\n"
        )
    info = wheel_info(wheel)
    assert info["id"].startswith("0.1.0-")
    assert len(info["sha256"]) == 64
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("other.dist-info/METADATA", "Name: unrelated\nVersion: 0.1.0\n")
    with pytest.raises(DeploymentError, match="not an Open Node"):
        wheel_info(wheel)


def test_existing_unowned_directory_is_never_taken_over(tmp_path):
    root = tmp_path / "unrelated"
    root.mkdir()
    sentinel = root / "user-file"
    sentinel.write_text("preserve")
    instance = Deployment(root, "open-node-agent-test.service")
    with pytest.raises(DeploymentError, match="not owned"):
        instance.install(Path("not-a-wheel.whl"))
    assert sentinel.read_text() == "preserve"


def test_release_identity_collision_cannot_reuse_another_wheel(deployment, monkeypatch):
    instance, calls, _ = deployment
    instance.record["releases"][OLD]["sha256"] = "original-digest"
    monkeypatch.setattr(service, "wheel_info", lambda _: {"id": OLD, "sha256": "different-digest"})
    with pytest.raises(DeploymentError, match="identity collision"):
        instance.stage(Path("candidate.whl"))
    assert not calls


def test_changed_unit_and_dropins_prevent_mutations(deployment):
    instance, calls, properties = deployment
    instance.unit_file.write_text("[Service]\nExecStart=/bin/true\n")
    with pytest.raises(DeploymentError, match="modified"):
        instance.uninstall()
    assert not calls
    instance.unit_file.write_text(instance.unit_text())
    properties["DropInPaths"] = "/etc/systemd/system/override.conf"
    with pytest.raises(DeploymentError, match="overrides"):
        instance.uninstall()
    assert not calls


def test_uninstall_preserves_data_and_does_not_follow_links(deployment, tmp_path):
    instance, _, _ = deployment
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("keep")
    (instance.release_path(OLD) / "link").symlink_to(outside, target_is_directory=True)
    instance.config.write_text("private-token")
    journal = instance.state / "commands.sqlite"
    journal.write_text("commands")
    instance.uninstall()
    assert instance.config.read_text() == "private-token"
    assert journal.read_text() == "commands"
    assert (outside / "sentinel").read_text() == "keep"
    assert not instance.unit_file.exists()
    assert not instance.release_path(OLD).exists()
    assert instance.record["status"] == "removed"


def test_deletion_rejects_traversal_and_foreign_current_file(deployment):
    instance, _, _ = deployment
    with pytest.raises(DeploymentError, match="outside"):
        instance.remove_owned(instance.root / ".." / "outside")
    (instance.root / "current").unlink()
    (instance.root / "current").write_text("keep")
    with pytest.raises(DeploymentError, match="not a symlink"):
        instance.set_current(NEW)


def test_preflight_failure_keeps_running_release(deployment, monkeypatch):
    instance, calls, _ = deployment

    def fail(_):
        raise DeploymentError("invalid candidate")

    monkeypatch.setattr(instance, "preflight", fail)
    with pytest.raises(DeploymentError, match="invalid candidate"):
        instance.activate(NEW)
    assert instance.record["current"] == OLD
    assert instance.record["pending"] is None
    assert not calls


def test_failed_upgrade_restores_previous_release_and_service(deployment, monkeypatch):
    instance, calls, properties = deployment

    def ready(release, _):
        if release == NEW:
            raise DeploymentError("candidate failed")

    monkeypatch.setattr(instance, "ready", ready)
    with pytest.raises(DeploymentError, match="previous deployment state restored"):
        instance.activate(NEW)
    assert (instance.root / "current").resolve() == instance.release_path(OLD)
    assert instance.record["current"] == OLD
    assert instance.record["pending"] is None
    assert properties["ActiveState"] == "active"
    assert ("systemctl", "start", instance.unit) in calls


def test_final_manifest_failure_still_rolls_back(deployment, monkeypatch):
    instance, _, _ = deployment
    save = instance.save
    attempts = 0

    def fail_commit():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise OSError("simulated write failure")
        save()

    monkeypatch.setattr(instance, "save", fail_commit)
    monkeypatch.setattr(instance, "ready", lambda *_: None)
    with pytest.raises(DeploymentError, match="state restored"):
        instance.activate(NEW)
    assert json.loads(instance.manifest.read_text())["current"] == OLD
    assert (instance.root / "current").resolve() == instance.release_path(OLD)


def test_upgrade_preserves_stopped_service_and_rollback_target(deployment, monkeypatch):
    instance, calls, properties = deployment
    properties["ActiveState"] = "inactive"
    monkeypatch.setattr(instance, "ready", lambda *_: pytest.fail("Stopped service was started"))
    instance.activate(NEW)
    assert properties["ActiveState"] == "inactive"
    assert instance.record["current"] == NEW
    assert instance.record["previous"] == OLD
    assert not any(call[1] in {"start", "enable"} for call in calls)


def test_recover_interrupted_switch(deployment, monkeypatch):
    instance, _, properties = deployment
    instance.record["pending"] = {"from": OLD, "to": NEW, "was_active": True}
    instance.save()
    instance.set_current(NEW)
    monkeypatch.setattr(instance, "ready", lambda *_: None)
    instance.load()
    instance.recover()
    assert instance.record["pending"] is None
    assert (instance.root / "current").resolve() == instance.release_path(OLD)
    assert properties["ActiveState"] == "active"


def test_unit_has_dedicated_account_and_child_process_containment(deployment):
    instance, _, _ = deployment
    unit = instance.unit_text()
    for line in (
        "User=open-node-agent-test",
        "KillMode=control-group",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "UMask=0077",
    ):
        assert line in unit
    assert "token" not in unit


@pytest.mark.parametrize(
    "changed",
    [
        {"pid": 99},
        {"connected": False},
        {"runtime_ready": False},
        {"observed_at": 1},
        {"agent_version": "wrong"},
        {"package_path": "/unrelated/release"},
        {"observed_at": "invalid"},
        {"observed_at": float("inf")},
        {"package_path": None},
    ],
)
def test_readiness_rejects_stale_or_incomplete_health(deployment, monkeypatch, changed):
    instance, _, _ = deployment
    clock = [100.0]
    monkeypatch.setattr(
        service,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            time=lambda: clock[0],
            sleep=lambda value: clock.__setitem__(0, clock[0] + value),
        ),
    )
    health = {
        "pid": 42,
        "agent_version": "0.1.0",
        "observed_at": 100.0,
        "connected": True,
        "runtime_ready": True,
        "package_path": str(instance.release_path(OLD) / "lib/package"),
        **changed,
    }
    (instance.state / "health.json").write_text(json.dumps(health))
    with pytest.raises(DeploymentError, match="did not become ready"):
        instance.ready(OLD, 99)


async def test_agent_health_requires_recent_auth_and_runtime_readiness(config):
    agent = Agent(config)
    agent.runtime.running = AsyncMock(return_value=False)
    try:
        report = await agent.health_report()
        assert report["connected"] is False
        assert report["runtime_ready"] is True
        agent.journal.set_desired_running(True)
        agent.control_contact()
        report = await agent.health_report()
        assert report["connected"] is True
        assert report["runtime_ready"] is False
        assert report["pid"] == os.getpid()
        assert report["agent_version"] == __version__
        assert config.token.get_secret_value() not in json.dumps(report)
        agent.last_contact = time.monotonic() - 1000
        assert (await agent.health_report())["connected"] is False
    finally:
        await agent.close()
