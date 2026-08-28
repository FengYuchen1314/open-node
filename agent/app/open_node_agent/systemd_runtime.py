"""Bind an independently owned system service to one Xray executable and JSON file."""

import grp
import json
import os
import pwd
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from open_node_agent.config import AgentConfig
from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure, run_command

BUS = "org.freedesktop.systemd1"
MANAGER = "/org/freedesktop/systemd1"
SERVICE_PATTERN = r"[a-zA-Z0-9_][a-zA-Z0-9_.@-]{0,239}\.service"


@dataclass
class Binding:
    running: bool
    environment: dict[str, str]
    directory: str


def root_owned(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeFailure("External Xray host files require absolute paths without traversal")
    resolved = path.resolve(strict=True)
    for original in (path, resolved):
        for item in (original, *original.parents):
            info = item.lstat()
            if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
                raise RuntimeFailure(
                    "External Xray unit files, executable and parents must be root-owned "
                    "and not writable by other users"
                )
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o6000:
        raise RuntimeFailure("External Xray host files must be regular files without set-id bits")
    return resolved


def private_config(path: Path, uid: int) -> None:
    if ".." in path.parts or any(part.is_symlink() for part in (path, *path.parents)):
        raise RuntimeFailure("External Xray config cannot use symlink or traversal paths")
    info, parent = path.stat(), path.parent.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != uid
        or info.st_mode & 0o7777 not in {0o600, 0o640}
        or parent.st_uid != uid
        or parent.st_mode & 0o022
        or parent.st_mode & 0o300 != 0o300
    ):
        raise RuntimeFailure(
            "External Xray config and writable parent must belong to the dedicated service user; "
            "use a private directory and a 0600 or 0640 regular file"
        )


def config_argument(argv: list[str], binary: Path, config: Path) -> None:
    if len(argv) < 3 or argv[:2] != [str(binary), "run"]:
        raise RuntimeFailure("External Xray ExecStart must execute the configured binary with run")
    options = {}
    iterator = iter(argv[2:])
    for argument in iterator:
        flag, separator, value = argument.partition("=")
        name = {
            "-c": "config",
            "-config": "config",
            "--config": "config",
            "-format": "format",
            "--format": "format",
        }.get(flag)
        if name is None or name in options:
            raise RuntimeFailure(
                "External Xray requires one explicit JSON config, without extra flags"
            )
        options[name] = value if separator else next(iterator, "")
    if options.get("config") != str(config) or options.get("format", "json") != "json":
        raise RuntimeFailure("External Xray ExecStart does not match the configured JSON file")


class SystemdRuntime:
    def __init__(self, config: AgentConfig, *, uid: int | None = None, gid: int | None = None):
        self.config = config
        self.uid = os.geteuid() if uid is None else uid
        self.gid = os.getegid() if gid is None else gid

    def read_config(self) -> bytes:
        path = self.config.xray_config
        private_config(path, self.uid)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != self.uid
                or info.st_nlink != 1
                or info.st_mode & 0o7777 not in {0o600, 0o640}
            ):
                raise RuntimeFailure("External Xray config changed while opening its private file")
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise RuntimeFailure("Xray configuration exceeds 2 MiB")
        return raw

    async def bus(self, *args: str) -> dict:
        try:
            code, output = await run_command(
                "busctl", "--system", "--json=short", "--timeout=5", *args, timeout=6
            )
        except (OSError, TimeoutError) as exc:
            raise RuntimeFailure(
                "Cannot inspect the system service; busctl and system D-Bus are required"
            ) from exc
        if code:
            raise RuntimeFailure("Cannot inspect the configured system service via D-Bus")
        try:
            value = json.loads(output)
            if not isinstance(value, dict) or "data" not in value:
                raise ValueError
            return value
        except ValueError as exc:
            raise RuntimeFailure("Invalid systemd D-Bus response") from exc

    async def properties(self, path: str, interface: str) -> dict:
        value = await self.bus(
            "call", BUS, path, "org.freedesktop.DBus.Properties", "GetAll", "s", BUS + interface
        )
        try:
            if value["type"] != "a{sv}":
                raise ValueError
            return {key: variant["data"] for key, variant in value["data"][0].items()}
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
            raise RuntimeFailure("Invalid systemd properties response") from exc

    async def inspect(self) -> Binding:
        try:
            return await self._inspect()
        except (OSError, KeyError, IndexError, TypeError, AttributeError, RuntimeError) as exc:
            raise RuntimeFailure(
                "Cannot verify the external Xray service binding or host files"
            ) from exc

    async def _inspect(self) -> Binding:
        config = self.config
        if not re.fullmatch(SERVICE_PATTERN, config.xray_service):
            raise RuntimeFailure("Use an exact system service name, not an option or pattern")
        loaded = await self.bus(
            "call", BUS, MANAGER, BUS + ".Manager", "LoadUnit", "s", config.xray_service
        )
        if loaded.get("type") != "o" or not isinstance(loaded["data"][0], str):
            raise RuntimeFailure("Invalid systemd service identity")
        path = loaded["data"][0]
        unit = await self.properties(path, ".Unit")
        service = await self.properties(path, ".Service")
        if unit["Id"] != config.xray_service or unit["LoadState"] != "loaded":
            raise RuntimeFailure("External Xray must name a loaded canonical service, not an alias")
        if unit["NeedDaemonReload"] or unit["Transient"] or not unit["FragmentPath"]:
            raise RuntimeFailure(
                "External Xray needs a persistent unit and a completed daemon-reload"
            )
        for filename in (unit["FragmentPath"], *unit["DropInPaths"]):
            root_owned(Path(filename))
        if self.uid in {0, 65534}:
            raise RuntimeFailure("External Xray requires a dedicated non-root account, not nobody")
        user = pwd.getpwuid(self.uid)
        if service["User"] not in {user.pw_name, str(self.uid)} or service["DynamicUser"]:
            raise RuntimeFailure("External Xray and Agent must use the same dedicated static User")
        group = service["Group"]
        gid = (
            (int(group) if group.isdecimal() else grp.getgrnam(group).gr_gid)
            if group
            else user.pw_gid
        )
        if gid != self.gid or service["SupplementaryGroups"]:
            raise RuntimeFailure(
                "External Xray and Agent must use the same primary Group without extra groups"
            )
        if service["Type"] not in {"simple", "exec"} or service["RemainAfterExit"]:
            raise RuntimeFailure("External Xray requires a simple or exec foreground service")
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
            if service[field]:
                raise RuntimeFailure(
                    f"External Xray binding does not support {field}; simplify the dedicated unit"
                )
        if service.get("ImportCredential"):
            raise RuntimeFailure("External Xray binding does not support imported credentials")
        if service["StandardInput"] != "null":
            raise RuntimeFailure("External Xray requires StandardInput=null")
        commands = service["ExecStartEx"]
        if len(commands) != 1 or commands[0][0] != str(config.xray_binary) or commands[0][2]:
            raise RuntimeFailure(
                "External Xray requires one unprefixed ExecStart using the configured binary"
            )
        argv = commands[0][1]
        config_argument(argv, config.xray_binary, config.xray_config)
        private_config(config.xray_config, self.uid)
        binary = root_owned(config.xray_binary)
        if not binary.stat().st_mode & 0o111:
            raise RuntimeFailure("External Xray binary must be executable")
        directory = service["WorkingDirectory"] or "/"
        if not Path(directory).is_absolute() or directory.startswith("-"):
            raise RuntimeFailure("External Xray needs an explicit absolute WorkingDirectory")
        environment = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "USER": user.pw_name,
            "LOGNAME": user.pw_name,
            "HOME": user.pw_dir,
            "SHELL": user.pw_shell,
        }
        for entry in service["Environment"]:
            key, separator, value = entry.partition("=")
            if not separator or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", key):
                raise RuntimeFailure("External Xray has an invalid environment assignment")
            environment[key] = value
        running = unit["ActiveState"] == "active" and unit["SubState"] == "running"
        if running:
            pid = service["MainPID"]
            if not isinstance(pid, int) or pid <= 0:
                raise RuntimeFailure("External Xray service has no live main process")
            proc = Path("/proc") / str(pid)
            if proc.stat().st_uid != self.uid or not (proc / "exe").samefile(binary):
                raise RuntimeFailure(
                    "External Xray running process differs from its configured binary or user"
                )
            with (proc / "cmdline").open("rb") as stream:
                actual = stream.read(65537)
            if len(actual) > 65536 or actual.rstrip(b"\0").decode().split("\0") != argv:
                raise RuntimeFailure(
                    "External Xray running arguments differ; restart the service on the host"
                )
        return Binding(running, environment, directory)

    async def control(self, action: str) -> None:
        if action not in {"start", "stop", "restart"}:
            raise RuntimeFailure("Unsupported external Xray service action")
        await self.inspect()
        code, _ = await run_command(
            "systemctl", "--system", "--no-ask-password", action, self.config.xray_service
        )
        if code:
            raise RuntimeFailure(
                f"External Xray {action} failed; check the unit journal "
                "and the host's scoped polkit grant"
            )
