"""Explicit host authorization for a dedicated external Xray system service."""

import argparse
import asyncio
import hashlib
import json
import os
import pwd
import re
import stat
import sys
from pathlib import Path

from open_node_agent.config import AgentConfig
from open_node_agent.runtime import RuntimeFailure
from open_node_agent.service import DeploymentError, fsync_directory, read_owned, write_file
from open_node_agent.systemd_runtime import SERVICE_PATTERN, SystemdRuntime

RULES = Path("/etc/polkit-1/rules.d")
USER_PATTERN = r"[a-z_][a-z0-9_-]{0,31}"


def rule(user: str, service: str) -> bytes:
    if not re.fullmatch(USER_PATTERN, user) or not re.fullmatch(SERVICE_PATTERN, service):
        raise DeploymentError("Use an exact local account and canonical .service name")
    if user in {"root", "nobody"}:
        raise DeploymentError("Use a dedicated non-root service account")
    return (
        "// Open Node external Xray authorization. Revoke before deleting the account.\n"
        "polkit.addRule(function(action, subject) {\n"
        '    if (action.id === "org.freedesktop.systemd1.manage-units" &&\n'
        f"        subject.user === {json.dumps(user)} &&\n"
        f'        action.lookup("unit") === {json.dumps(service)} &&\n'
        '        ["start", "stop", "restart"].indexOf(action.lookup("verb")) !== -1) {\n'
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    ).encode()


def rule_path(user: str, service: str, directory: Path = RULES) -> Path:
    rule(user, service)
    identity = hashlib.sha256((user + "\0" + service).encode()).hexdigest()[:24]
    return directory / ("50-open-node-xray-" + identity + ".rules")


def change_rule(user: str, service: str, *, grant: bool, directory: Path = RULES) -> Path:
    if os.geteuid() != 0:
        raise DeploymentError("Only a host administrator can grant or revoke system service access")
    content = rule(user, service)
    try:
        polkit_uid = pwd.getpwnam("polkitd").pw_uid
    except KeyError:
        polkit_uid = 0
    for item in (directory, *directory.parents):
        info = item.lstat()
        owners = {0, polkit_uid} if item == directory else {0}
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
            raise DeploymentError(
                "The polkit rules directory must be trusted and not publicly writable"
            )
    path = rule_path(user, service, directory)
    existing = read_owned(path, missing_ok=True, limit=8192)
    if existing is not None and existing != content:
        raise DeploymentError("Refusing to replace or remove a modified polkit rule")
    if grant:
        if existing is None:
            write_file(path, content, mode=0o644, owner=(0, 0))
        elif path.stat().st_mode & 0o777 != 0o644:
            raise DeploymentError("The existing polkit rule must have mode 0644")
    elif existing is not None:
        path.unlink()
        fsync_directory(directory)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("grant", "revoke"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--xray-binary", type=Path)
    parser.add_argument("--xray-config", type=Path)
    parser.add_argument("--allow-takeover", action="store_true")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise DeploymentError("Run this command as the host administrator")
        rule(args.user, args.service)
        if args.action == "grant":
            if args.xray_binary is None or args.xray_config is None:
                raise DeploymentError("Grant requires --xray-binary and --xray-config")
            user = pwd.getpwnam(args.user)
            if user.pw_uid in {0, 65534} or Path(user.pw_shell).name not in {"nologin", "false"}:
                raise DeploymentError(
                    "Choose a dedicated non-root account with a nologin or false shell"
                )
            config = AgentConfig(
                master_url="https://unused.invalid",
                token="unused",
                runtime_mode="systemd",
                xray_service=args.service,
                xray_binary=args.xray_binary,
                xray_config=args.xray_config,
                allow_xray_takeover=args.allow_takeover,
            )
            asyncio.run(
                SystemdRuntime(config, uid=user.pw_uid, gid=user.pw_gid).inspect(
                    allow_multifile=args.allow_takeover
                )
            )
        path = change_rule(args.user, args.service, grant=args.action == "grant")
        print(
            json.dumps(
                {
                    "action": args.action,
                    "rule": str(path),
                    "service": args.service,
                    "user": args.user,
                }
            )
        )
    except (DeploymentError, RuntimeFailure, OSError, KeyError, ValueError) as exc:
        print(f"System service authorization failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
