"""Root-only systemd deployment CLI. Bootstrap directly with Python's standard library."""

from __future__ import annotations

import argparse
import email.parser
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

UNIT_PATTERN = r"open-node-agent(?:-[a-z0-9][a-z0-9-]{0,15})?\.service"
RELEASE_PATTERN = r"[a-zA-Z0-9_.+-]+-[a-f0-9]{16}"
MANIFEST_NAME = "installation.json"


class DeploymentError(RuntimeError):
    pass


def command(*args, timeout=120, check=True):
    result = subprocess.run(
        [str(arg) for arg in args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env={
            **os.environ,
            "PYTHONPATH": "",
            "PYTHONNOUSERSITE": "1",
        },
    )
    if check and result.returncode:
        raise DeploymentError(
            f"{Path(str(args[0])).name} failed (exit {result.returncode}): " + result.stderr[-2000:]
        )
    return result


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path: Path, content: bytes, mode=0o600, owner=None):
    if path.is_symlink():
        raise DeploymentError(f"Refusing symlink: {path}")
    fd, temporary = tempfile.mkstemp(prefix=".open-node-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            if owner:
                os.fchown(stream.fileno(), *owner)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_root(root: Path):
    if (
        not root.is_absolute()
        or len(root.parts) < 3
        or not re.fullmatch(r"/[a-zA-Z0-9_./-]+", str(root))
        or ".." in root.parts
        or root == Path("/opt/open-node")
        or not any(
            root != base and root.is_relative_to(base)
            for base in map(Path, ("/opt", "/var/lib", "/tmp"))
        )
    ):
        raise DeploymentError(
            "Use a dedicated absolute installation directory, not a system directory"
        )
    for path in [root, *root.parents]:
        if path.is_symlink():
            raise DeploymentError(f"Refusing symlink component: {path}")


def wheel_info(path: Path):
    if not path.is_file() or path.suffix != ".whl":
        raise DeploymentError("A trusted local Open Node Agent wheel is required")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1 or archive.getinfo(names[0]).file_size > 1_000_000:
            raise DeploymentError("Invalid wheel metadata")
        metadata = email.parser.BytesParser().parsebytes(archive.read(names[0]))
    version = metadata.get("Version", "")
    name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
    if name != "open-node-agent" or not re.fullmatch(r"[a-zA-Z0-9_.+-]+", version):
        raise DeploymentError("Wheel is not an Open Node Agent release")
    return {"version": version, "sha256": digest, "id": version + "-" + digest[:16]}


class Deployment:
    def __init__(self, root: Path, unit: str, *, unit_dir=Path("/etc/systemd/system"), timeout=45):
        validate_root(root)
        if not re.fullmatch(UNIT_PATTERN, unit) or len(unit.removesuffix(".service")) > 31:
            raise DeploymentError("Service name must be open-node-agent[-instance].service")
        self.root, self.unit, self.timeout = root, unit, timeout
        self.unit_file = unit_dir / unit
        self.user = unit.removesuffix(".service")
        self.manifest = root / MANIFEST_NAME
        self.config = root / "config" / "agent.json"
        self.state = root / "state"
        self.record: dict = {}

    @contextmanager
    def locked(self):
        if os.geteuid() != 0 or not Path("/run/systemd/system").is_dir():
            raise DeploymentError("Run as root on a Linux systemd host")
        keys = sorted({self.unit, hashlib.sha256(str(self.root).encode()).hexdigest()})
        with ExitStack() as stack:
            for key in keys:
                fd = os.open(
                    f"/run/lock/open-node-deploy-{key}.lock",
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                    0o600,
                )
                handle = stack.enter_context(os.fdopen(fd, "a"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise DeploymentError("Another deployment operation is running") from None
            yield

    def save(self):
        write_file(self.manifest, json.dumps(self.record, indent=2).encode())

    def load(self):
        validate_root(self.root)
        if not self.manifest.is_file() or self.manifest.is_symlink():
            raise DeploymentError("Directory is not owned by this installer")
        for path in (self.root, self.manifest):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise DeploymentError(
                    "Installation metadata must be root-owned and not writable by others"
                )
        self.record = json.loads(self.manifest.read_text())
        if (
            self.record.get("schema") != 1
            or self.record.get("root") != str(self.root)
            or self.record.get("unit") != self.unit
            or self.record.get("user") != self.user
        ):
            raise DeploymentError("Installation identity does not match")
        for release in self.record.get("releases", {}):
            self.release_path(release)
        for name in ("config", "state", "runtime", "releases"):
            path = self.root / name
            if path.is_symlink():
                raise DeploymentError(f"Refusing symlink directory: {name}")
            if name in {"runtime", "releases"} and path.exists():
                if path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
                    raise DeploymentError("Program directories must be writable only by root")
        if self.record.get("uid") is not None:
            account = pwd.getpwnam(self.user)
            if (
                account.pw_uid != self.record["uid"]
                or account.pw_gid != self.record["gid"]
                or account.pw_dir != str(self.state)
                or account.pw_uid == 0
            ):
                raise DeploymentError("Service account ownership changed")

    def release_path(self, release):
        if not isinstance(release, str) or not re.fullmatch(RELEASE_PATTERN, release):
            raise DeploymentError("Invalid release identity")
        path = self.root / "releases" / release
        if path.is_symlink():
            raise DeploymentError("Release directory cannot be a symlink")
        return path

    def unit_text(self):
        if self.record.get("unit_text"):
            return self.record["unit_text"]
        return f"""# Managed by Open Node Agent: {self.record["installation_id"]}
[Unit]
Description=Open Node Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User={self.user}
Group={self.user}
WorkingDirectory={self.state}
ExecStart={self.root}/current/bin/python -m open_node_agent --config {self.config}
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
KillMode=control-group
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
ReadWritePaths={self.root}/config {self.state}
Environment=PYTHONNOUSERSITE=1

[Install]
WantedBy=multi-user.target
"""

    def verify_unit(self, *, missing_ok=False):
        if self.unit_file.is_symlink():
            raise DeploymentError("Service unit is not owned by this installation")
        if not self.unit_file.exists():
            if missing_ok:
                return
            raise DeploymentError("Owned service unit is missing")
        if self.unit_file.read_text() != self.unit_text():
            raise DeploymentError("Service unit was modified; refusing to overwrite or remove it")
        if self.unit_file.stat().st_uid != 0 or self.unit_file.stat().st_mode & 0o022:
            raise DeploymentError("Service unit must be writable only by root")
        properties = self.properties()
        if properties.get("FragmentPath") not in {"", str(self.unit_file)}:
            raise DeploymentError("A different service definition is loaded")
        if properties.get("DropInPaths"):
            raise DeploymentError("Service has external overrides; review them before deployment")

    def properties(self):
        output = command(
            "systemctl",
            "show",
            self.unit,
            "--property=ActiveState,MainPID,FragmentPath,DropInPaths",
        ).stdout
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def account_owner(self):
        return self.record["uid"], self.record["gid"]

    def stage(self, wheel: Path):
        info = wheel_info(wheel)
        release = self.release_path(info["id"])
        if info["id"] in self.record["releases"]:
            if self.record["releases"][info["id"]].get("sha256") != info["sha256"]:
                raise DeploymentError(
                    "Release identity collision; refusing to reuse a different artifact"
                )
            if not (release / "bin" / "python").exists():
                raise DeploymentError("Recorded release is incomplete")
            return info["id"]
        if release.exists():
            raise DeploymentError(
                f"Incomplete staging directory: {release}; inspect before retrying"
            )
        release.mkdir(mode=0o755)
        try:
            archived_wheel = release / wheel.name
            shutil.copyfile(wheel, archived_wheel)
            archived_wheel.chmod(0o600)
            if wheel_info(archived_wheel)["sha256"] != info["sha256"]:
                raise DeploymentError("Wheel changed while staging")
            command(sys.executable, "-m", "venv", str(release), timeout=180)
            command(
                release / "bin/python",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(archived_wheel),
                timeout=600,
            )
            actual = command(
                release / "bin/python", "-m", "open_node_agent", "--version"
            ).stdout.strip()
            if actual != info["version"]:
                raise DeploymentError("Wheel runtime version does not match its metadata")
            self.record["releases"][info["id"]] = info
            self.save()
            return info["id"]
        except BaseException:
            self.remove_owned(release)
            raise

    def remove_owned(self, path: Path):
        validate_root(self.root)
        if (
            path == self.root
            or ".." in path.parts
            or not path.is_relative_to(self.root)
            or not path.parent.resolve().is_relative_to(self.root.resolve())
        ):
            raise DeploymentError("Refusing deletion outside the owned installation")
        for parent in path.parents:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise DeploymentError("Refusing deletion through a symlink")
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    def initialize(self):
        if self.root.exists():
            raise DeploymentError("Installation directory already exists; refusing takeover")
        if self.unit_file.exists() or self.unit_file.is_symlink():
            raise DeploymentError("Service unit already exists; refusing takeover")
        if self.properties().get("FragmentPath"):
            raise DeploymentError("Service name is already in use")
        try:
            pwd.getpwnam(self.user)
        except KeyError:
            pass
        else:
            raise DeploymentError("Service account already exists; refusing takeover")
        self.root.mkdir(mode=0o755)
        self.record = {
            "schema": 1,
            "installation_id": uuid4().hex,
            "root": str(self.root),
            "unit": self.unit,
            "user": self.user,
            "uid": None,
            "gid": None,
            "status": "preparing",
            "releases": {},
            "current": None,
            "previous": None,
            "pending": None,
        }
        self.record["unit_text"] = self.unit_text()
        self.save()
        command(
            "useradd",
            "--system",
            "--user-group",
            "--home-dir",
            self.state,
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            self.user,
        )
        account = pwd.getpwnam(self.user)
        self.record.update(uid=account.pw_uid, gid=account.pw_gid)
        self.save()
        for name in ("releases", "runtime", "config", "state"):
            path = self.root / name
            private = name in {"config", "state"}
            path.mkdir(mode=0o700 if private else 0o755)
            if private:
                os.chown(path, *self.account_owner())

    def prepare_config(self, release, source, xray_config, xray_binary, asset_dir=None):
        python = self.release_path(release) / "bin/python"
        script = (
            "import json,sys; from open_node_agent.config import load_config; "
            "c=load_config(__import__('pathlib').Path(sys.argv[1])); "
            "d=c.model_dump(mode='json'); d['token']=c.token.get_secret_value(); "
            "print(json.dumps(d))"
        )
        config = json.loads(command(python, "-c", script, source).stdout)
        if config["runtime_mode"] != "managed":
            raise DeploymentError(
                "This installer owns a managed Xray child, not an external service"
            )
        config.update(
            state_dir=str(self.state),
            xray_binary=str(self.root / "runtime/xray"),
            xray_config=str(self.root / "config/xray.json"),
        )
        if config.get("ca_file"):
            target = self.root / "config/ca.pem"
            write_file(target, Path(config["ca_file"]).read_bytes(), owner=self.account_owner())
            config["ca_file"] = str(target)
        write_file(self.root / "runtime/xray", xray_binary.read_bytes(), mode=0o755)
        if asset_dir:
            for name in ("geoip.dat", "geosite.dat"):
                if (asset_dir / name).is_file():
                    write_file(
                        self.root / "runtime" / name, (asset_dir / name).read_bytes(), mode=0o644
                    )
        write_file(
            self.root / "config/xray.json", xray_config.read_bytes(), owner=self.account_owner()
        )
        write_file(self.config, json.dumps(config, indent=2).encode(), owner=self.account_owner())

    def preflight(self, release):
        python = self.release_path(release) / "bin/python"
        command(
            "runuser",
            "-u",
            self.user,
            "--",
            python,
            "-m",
            "open_node_agent",
            "--config",
            self.config,
            "--check",
        )
        command(
            "runuser",
            "-u",
            self.user,
            "--",
            self.root / "runtime/xray",
            "run",
            "-test",
            "-config",
            self.root / "config/xray.json",
        )

    def set_current(self, release):
        current = self.root / "current"
        if current.exists() and not current.is_symlink():
            raise DeploymentError("Current release pointer is not a symlink")
        if release is None:
            current.unlink(missing_ok=True)
        else:
            target = self.release_path(release)
            temporary = self.root / (".current-" + uuid4().hex)
            try:
                temporary.symlink_to(target.relative_to(self.root))
                os.replace(temporary, current)
            finally:
                temporary.unlink(missing_ok=True)
        fsync_directory(self.root)

    def ready(self, release, started):
        deadline, stable_pid, stable_since = time.monotonic() + self.timeout, None, 0.0
        version = self.record["releases"][release]["version"]
        while time.monotonic() < deadline:
            properties = self.properties()
            pid = int(properties.get("MainPID") or 0)
            try:
                health = json.loads((self.state / "health.json").read_text())
            except (OSError, ValueError):
                health = {}
            if not isinstance(health, dict):
                health = {}
            observed = health.get("observed_at", 0)
            if not isinstance(observed, (int, float)):
                observed = 0
            package = health.get("package_path")
            if not isinstance(package, str):
                package = ""
            healthy = (
                pid > 0
                and properties.get("ActiveState") == "active"
                and health.get("pid") == pid
                and health.get("agent_version") == version
                and Path(package).resolve().is_relative_to(self.release_path(release))
                and observed >= started
                and 0 <= time.time() - observed < 5
                and health.get("connected") is True
                and health.get("runtime_ready") is True
            )
            if healthy:
                if pid != stable_pid:
                    stable_pid, stable_since = pid, time.monotonic()
                elif time.monotonic() - stable_since >= 2:
                    return
            else:
                stable_pid = None
            time.sleep(0.25)
        raise DeploymentError(
            "Agent did not become ready; inspect the service journal and node configuration"
        )

    def activate(self, release):
        if self.record.get("pending"):
            raise DeploymentError("An interrupted transaction exists; run recover first")
        self.verify_unit()
        self.preflight(release)
        old = self.record.get("current")
        self.record["pending"] = {
            "from": old,
            "to": release,
            "was_active": self.properties().get("ActiveState") == "active",
        }
        self.save()
        prepared = dict(self.record)
        try:
            command("systemctl", "stop", self.unit)
            self.set_current(release)
            started = time.time()
            command("systemctl", "reset-failed", self.unit, check=False)
            if old is None:
                command("systemctl", "enable", "--now", self.unit)
            elif prepared["pending"]["was_active"]:
                command("systemctl", "start", self.unit)
            if old is None or prepared["pending"]["was_active"]:
                self.ready(release, started)
            self.record.update(current=release, previous=old, pending=None, status="installed")
            self.save()
        except BaseException as error:
            self.record = prepared
            try:
                self.recover()
            except Exception as recovery_error:
                raise DeploymentError(
                    "Activation and rollback failed; pending transaction retained. "
                    "Run recover after correcting the service fault."
                ) from recovery_error
            raise DeploymentError(
                "Activation failed; previous deployment state restored"
            ) from error

    def recover(self):
        pending = self.record.get("pending")
        if not pending:
            return
        self.verify_unit()
        command("systemctl", "stop", self.unit)
        old = pending["from"]
        self.set_current(old)
        if old and pending["was_active"]:
            started = time.time()
            command("systemctl", "reset-failed", self.unit, check=False)
            command("systemctl", "start", self.unit)
            self.ready(old, started)
        elif old is None:
            command("systemctl", "disable", self.unit)
        self.record.update(current=old, pending=None, status="installed" if old else "failed")
        self.save()

    def install(self, wheel, source=None, xray_config=None, xray_binary=None, asset_dir=None):
        if not self.root.exists():
            if not all((source, xray_config, xray_binary)):
                raise DeploymentError(
                    "Fresh installation requires --config, --xray-config, and --xray"
                )
            wheel_info(wheel)
            self.initialize()
        else:
            self.load()
            if self.record.get("pending"):
                raise DeploymentError("An interrupted transaction exists; run recover first")
            if self.record["status"] not in {"removed", "failed", "preparing"}:
                raise DeploymentError("Already installed; use upgrade")
            if self.record["status"] == "removed" and any(
                (source, xray_config, xray_binary, asset_dir)
            ):
                raise DeploymentError(
                    "Reinstallation preserves existing configuration; omit source options"
                )
            if any((source, xray_config, xray_binary)) and not all(
                (source, xray_config, xray_binary)
            ):
                raise DeploymentError(
                    "Retry requires all three source options, or none to keep existing files"
                )
        release = self.stage(wheel)
        if source:
            self.prepare_config(release, source, xray_config, xray_binary, asset_dir)
        self.preflight(release)
        if self.unit_file.exists() or self.unit_file.is_symlink():
            self.verify_unit()
        else:
            if self.properties().get("FragmentPath"):
                raise DeploymentError("Service name was claimed by another definition")
            write_file(self.unit_file, self.unit_text().encode(), mode=0o644)
        command("systemctl", "daemon-reload")
        self.activate(release)

    def upgrade(self, wheel):
        self.load()
        self.verify_unit()
        if self.record["status"] != "installed" or self.record.get("pending"):
            raise DeploymentError("Upgrade requires an installed, recovered deployment")
        release = self.stage(wheel)
        if release != self.record["current"]:
            self.activate(release)

    def rollback(self):
        self.load()
        previous = self.record.get("previous")
        if not previous:
            raise DeploymentError("No previous release is available")
        self.activate(previous)

    def uninstall(self, *, purge=False):
        self.load()
        self.verify_unit(missing_ok=True)
        if self.unit_file.exists():
            command("systemctl", "disable", "--now", self.unit)
            self.unit_file.unlink()
            command("systemctl", "daemon-reload")
            command("systemctl", "reset-failed", self.unit, check=False)
        elif self.properties().get("FragmentPath"):
            raise DeploymentError("Service name now belongs to another definition")
        self.set_current(None)
        for release in self.record["releases"]:
            self.remove_owned(self.release_path(release))
        self.record.update(status="removed", current=None, previous=None, pending=None, releases={})
        self.save()
        if purge:
            # userdel intentionally omits -r: it must never remove an unrelated home.
            if self.record.get("uid") is not None:
                command("userdel", self.user)
            validate_root(self.root)
            shutil.rmtree(self.root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/open-node-agent"))
    parser.add_argument("--unit", default="open-node-agent.service")
    parser.add_argument("--timeout", type=int, default=45)
    actions = parser.add_subparsers(dest="action", required=True)
    install = actions.add_parser("install")
    install.add_argument("--wheel", type=Path, required=True)
    install.add_argument("--config", type=Path)
    install.add_argument("--xray-config", type=Path)
    install.add_argument("--xray", type=Path)
    install.add_argument("--asset-dir", type=Path)
    upgrade = actions.add_parser("upgrade")
    upgrade.add_argument("--wheel", type=Path, required=True)
    actions.add_parser("rollback")
    actions.add_parser("recover")
    actions.add_parser("status")
    uninstall = actions.add_parser("uninstall")
    uninstall.add_argument("--purge", action="store_true")
    args = parser.parse_args()
    os.umask(0o022)
    try:
        if not 3 <= args.timeout <= 300:
            raise DeploymentError("Readiness timeout must be between 3 and 300 seconds")
        deployment = Deployment(args.root, args.unit, timeout=args.timeout)
        with deployment.locked():
            if args.action == "install":
                deployment.install(
                    args.wheel, args.config, args.xray_config, args.xray, args.asset_dir
                )
            elif args.action == "upgrade":
                deployment.upgrade(args.wheel)
            elif args.action == "rollback":
                deployment.rollback()
            elif args.action == "uninstall":
                deployment.uninstall(purge=args.purge)
            else:
                deployment.load()
                if args.action == "recover":
                    deployment.recover()
            print(
                json.dumps(
                    {
                        "action": args.action,
                        "root": str(args.root),
                        "status": deployment.record.get("status"),
                        "current": deployment.record.get("current"),
                        "previous": deployment.record.get("previous"),
                        "pending": deployment.record.get("pending"),
                    }
                )
            )
    except (
        DeploymentError,
        OSError,
        ValueError,
        KeyError,
        zipfile.BadZipFile,
        subprocess.TimeoutExpired,
    ) as error:
        parser.exit(1, f"Deployment failed: {error}\n")


if __name__ == "__main__":
    main()
