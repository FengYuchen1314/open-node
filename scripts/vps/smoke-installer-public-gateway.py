"""Validate the managed gateway's exact Docker policy without starting it."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

IMAGE = (
    "caddy:2.11.4-alpine@sha256:"
    "5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
)


class SmokeFailure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def execute(arguments, *, check=True, **kwargs):
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
        **kwargs,
    )
    if check and result.returncode != 0:
        raise SmokeFailure(f"Command failed: {arguments[0]} {arguments[1]}")
    return result


def inspect(kind, name):
    return json.loads(execute(["docker", kind, "inspect", name]).stdout)[0]


def namespace_is_unused(container, volume):
    return (
        execute(["docker", "inspect", container], check=False).returncode != 0
        and execute(["docker", "volume", "inspect", volume], check=False).returncode != 0
    )


def cleanup_owned(container, volume, project, *, container_created, volume_created):
    if container_created:
        details = inspect("container", container)
        labels = details["Config"].get("Labels") or {}
        require(
            details["Name"] == f"/{container}"
            and labels.get("com.open-node.installer.project") == project
            and labels.get("com.open-node.installer.purpose") == "public-gateway",
            "Refusing to remove a container outside the UUID fixture policy",
        )
        execute(["docker", "rm", "-f", "--", container])
    if volume_created:
        details = inspect("volume", volume)
        labels = details.get("Labels") or {}
        require(
            details["Name"] == volume
            and labels.get("com.open-node.installer.project") == project
            and labels.get("com.open-node.installer.purpose") == "public-gateway-data",
            "Refusing to remove a volume outside the UUID fixture policy",
        )
        execute(["docker", "volume", "rm", "--", volume])


def write_private(path, content):
    path.write_text(content)
    path.chmod(0o600)


def prepare_environment(repository, destination):
    content = (repository / "deploy/.env.example").read_text()
    replacements = {
        "OPEN_NODE_TRUSTED_PROXIES=": "OPEN_NODE_TRUSTED_PROXIES=*",
        "OPEN_NODE_PUBLIC_IP=auto": "OPEN_NODE_PUBLIC_IP=1.1.1.1",
        "OPEN_NODE_PUBLIC_HOSTNAME=": "OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com",
        "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=": (
            "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL=https://panel.example.com"
        ),
    }
    for old, new in replacements.items():
        require(old in content, f"Missing environment fixture key: {old}")
        content = content.replace(old, new)
    if "OPEN_NODE_SESSION_COOKIE_SECURE=false" in content:
        content = content.replace(
            "OPEN_NODE_SESSION_COOKIE_SECURE=false",
            "OPEN_NODE_SESSION_COOKIE_SECURE=true",
        )
    require(
        "OPEN_NODE_SESSION_COOKIE_SECURE=true" in content,
        "Missing secure-cookie environment fixture key",
    )
    write_private(destination, content)


def prepare_definitions(repository, destination):
    source = (repository / "install.sh").read_text()
    marker = '\nmain "$@"'
    require(marker in source, "Installer entry point was not found")
    write_private(destination, source[: source.rindex(marker)])


def fixture_container_command(repository, project, container, volume, environment):
    require(environment.is_file(), "Private fixture environment disappeared")
    return [
        "docker",
        "create",
        "--name",
        container,
        "--network",
        "host",
        "--restart",
        "unless-stopped",
        "--read-only",
        "--init",
        "--cap-drop",
        "ALL",
        "--cap-add",
        "NET_BIND_SERVICE",
        "--security-opt",
        "no-new-privileges:true",
        "--stop-timeout",
        "30",
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=10m",
        "--log-opt",
        "max-file=5",
        "--label",
        f"com.open-node.installer.project={project}",
        "--label",
        "com.open-node.installer.purpose=public-gateway",
        "--env",
        "OPEN_NODE_GATEWAY_MODE=dual",
        "--env",
        "OPEN_NODE_PUBLIC_HOSTNAME=panel.example.com",
        "--env",
        "OPEN_NODE_PUBLIC_IP_AUTHORITY=1.1.1.1",
        "--env",
        "OPEN_NODE_PUBLIC_HTTPS_PORT=58090",
        "--env",
        "OPEN_NODE_UPSTREAM_PORT=62031",
        "--mount",
        f"type=volume,src={volume},dst=/data",
        "--mount",
        f"type=bind,src={repository / 'deploy/Caddyfile.dual'},dst=/etc/caddy/Caddyfile,readonly",
        "--tmpfs",
        "/config:rw,nosuid,noexec,size=16m,mode=0700",
        "--tmpfs",
        "/tmp:rw,nosuid,noexec,size=16m,mode=1777",
        IMAGE,
    ]


def validate_with_installer(
    definitions, repository, environment, project, container, volume, operation="current"
):
    harness = r'''
source "$1"
trap - EXIT INT TERM HUP
INSTALL_DIR="$2"
ENV_FILE="$3"
PROJECT_NAME="$4"
PUBLIC_GATEWAY_CONTAINER="$5"
PUBLIC_GATEWAY_VOLUME="$6"
public_gateway_volume_is_safe
case "$7" in
  current) public_gateway_container_is_safe 0 ;;
  removal) public_gateway_container_is_safe 0 0 ;;
  *) exit 97 ;;
esac
'''
    result = execute(
        [
            "bash",
            "-c",
            harness,
            "gateway-smoke",
            str(definitions),
            str(repository),
            str(environment),
            project,
            container,
            volume,
            operation,
        ],
        check=False,
    )
    require(result.returncode == 0, "Installer rejected its exact Docker create policy")


def summarized_inspect(container, volume):
    details = inspect("container", container)
    storage = inspect("volume", volume)
    return {
        "name": details["Name"],
        "container_id": details["Id"],
        "container_running": details["State"]["Running"],
        "image_id": details["Image"],
        "config_image": details["Config"]["Image"],
        "entrypoint": details["Config"]["Entrypoint"],
        "command": details["Config"]["Cmd"],
        "user": details["Config"]["User"],
        "working_directory": details["Config"]["WorkingDir"],
        "security_options": details["HostConfig"]["SecurityOpt"],
        "init": details["HostConfig"]["Init"],
        "restart": details["HostConfig"]["RestartPolicy"],
        "logging": details["HostConfig"]["LogConfig"],
        "network_mode": details["HostConfig"]["NetworkMode"],
        "read_only": details["HostConfig"]["ReadonlyRootfs"],
        "cap_add": details["HostConfig"]["CapAdd"],
        "cap_drop": details["HostConfig"]["CapDrop"],
        "mounts": details["Mounts"],
        "tmpfs": details["HostConfig"]["Tmpfs"],
        "volume_driver": storage["Driver"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repository.resolve()
    output = args.output.absolute()
    require(os.geteuid() == 0, "Run this policy smoke as root")
    require(
        repository.is_dir()
        and not repository.is_symlink()
        and (repository / "deploy/Caddyfile").is_file()
        and (repository / "deploy/Caddyfile.ip").is_file()
        and (repository / "deploy/Caddyfile.dual").is_file(),
        "Repository does not contain the public gateway deployment asset",
    )
    require(not output.is_symlink(), "Output must not be a symbolic link")
    require(not output.exists() or not any(output.iterdir()), "Use a new empty output")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    nonce = secrets.token_hex(6)
    project = f"open-node-gateway-smoke-{nonce}"
    container = f"{project}-public-gateway"
    volume = f"{project}_caddy_data"
    require(namespace_is_unused(container, volume), "UUID fixture namespace already exists")
    work = Path(tempfile.mkdtemp(prefix="open-node-public-gateway-inspect-", dir="/root"))
    definitions, environment = work / "definitions.sh", work / "open-node.env"
    container_created = volume_created = False
    report = {"status": "failed", "project": project}
    try:
        prepare_definitions(repository, definitions)
        prepare_environment(repository, environment)
        execute(
            [
                "docker",
                "volume",
                "create",
                "--driver",
                "local",
                "--label",
                f"com.open-node.installer.project={project}",
                "--label",
                "com.open-node.installer.purpose=public-gateway-data",
                volume,
            ]
        )
        volume_created = True
        created = execute(
            fixture_container_command(
                repository, project, container, volume, environment
            ),
            check=False,
        )
        container_created = (
            execute(["docker", "inspect", container], check=False).returncode == 0
        )
        require(
            created.returncode == 0 and container_created,
            "Docker could not create the stopped gateway fixture",
        )
        report["inspect"] = summarized_inspect(container, volume)
        validate_with_installer(
            definitions, repository, environment, project, container, volume
        )
        environment.write_text(
            environment.read_text().replace("panel.example.com", "other.example.com")
        )
        environment.chmod(0o600)
        mismatch = execute(
            [
                "bash",
                "-c",
                r'''
source "$1"
trap - EXIT INT TERM HUP
INSTALL_DIR="$2" ENV_FILE="$3" PROJECT_NAME="$4"
PUBLIC_GATEWAY_CONTAINER="$5" PUBLIC_GATEWAY_VOLUME="$6"
public_gateway_container_is_safe 0
''',
                "gateway-smoke",
                str(definitions),
                str(repository),
                str(environment),
                project,
                container,
                volume,
            ],
            check=False,
        )
        require(
            mismatch.returncode != 0,
            "Current-state policy accepted a gateway for the previous hostname",
        )
        validate_with_installer(
            definitions,
            repository,
            environment,
            project,
            container,
            volume,
            operation="removal",
        )
        report["hostname_change_removal_policy"] = True
        report["status"] = "passed"
    except BaseException as error:
        report["error"] = str(error) if isinstance(error, SmokeFailure) else type(error).__name__
    finally:
        try:
            cleanup_owned(
                container,
                volume,
                project,
                container_created=container_created,
                volume_created=volume_created,
            )
            report["owned_resources_cleaned"] = True
        except BaseException as error:
            report["owned_resources_cleaned"] = False
            report["cleanup_error"] = type(error).__name__
            report["status"] = "failed"
        require(
            work.parent == Path("/root")
            and not work.is_symlink()
            and re.fullmatch(r"open-node-public-gateway-inspect-[A-Za-z0-9_-]+", work.name),
            "Refusing to remove an unrecognized private fixture directory",
        )
        shutil.rmtree(work)
        write_private(output / "report.json", json.dumps(report, indent=2))
    print(f"Public gateway Docker policy smoke {report['status']}: {output / 'report.json'}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
