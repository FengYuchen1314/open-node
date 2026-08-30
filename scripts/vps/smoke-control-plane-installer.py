"""Destructively exercise the public installer in an isolated Docker project.

This smoke test creates a private Git remote with several real commits. It
never points the installer at the repository's normal branch, Docker project,
image repository, or volume. The scenarios deliberately cover recovery paths
which a happy-path install cannot prove:

* independent candidate Compose rendering before every build/start;
* fresh install and administrator persistence;
* same-revision no-op and concurrent invocation locking;
* updates from a stopped container and from a volume-only deployment;
* backup failure and process interruption before and after activation;
* immutable backup restoration in an independent project;
* active environment, tag, container, and mount drift refusal;
* a database-mutating unhealthy candidate with Compose-down failure; and
* refusal to update an installation whose named data volume disappeared.

Run this only on the disposable project VPS as root. All Docker resources use
a cryptographically random test identity and are checked again after cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
COMMAND_TIMEOUT = 1_800
EXPECTED_FAILURE_TIMEOUT = 180
SHA256_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)$")


class SmokeFailure(AssertionError):
    """An installer invariant failed."""


def format_command(args: Sequence[str]) -> str:
    return " ".join(repr(part) for part in args)


def command(
    *args: str | Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    timeout: float = COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    normalized = tuple(str(arg) for arg in args)
    result = subprocess.run(
        normalized,
        env=None if env is None else dict(env),
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )
    if check and result.returncode:
        details = ""
        if capture:
            details = f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        raise SmokeFailure(
            f"command failed with exit {result.returncode}: "
            f"{format_command(normalized)}{details}"
        )
    return result


def output(*args: str | Path, env: Mapping[str, str] | None = None) -> str:
    return command(*args, env=env, capture=True).stdout.strip()


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_until(predicate, *, timeout: float, description: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {description}")


def health_passes(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=3) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_health(url: str, timeout: float = 90) -> None:
    wait_until(
        lambda: health_passes(url),
        timeout=timeout,
        description=f"healthy response from {url}",
    )


def login(url: str, username: str, password: str) -> None:
    request = urllib.request.Request(
        url + "/api/v1/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json", "X-Open-Node-Client": "browser"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if not payload.get("authenticated") or payload.get("username") != username:
        raise SmokeFailure(f"administrator login failed: {payload}")


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SmokeFailure(f"invalid installer env line {number}: {raw_line!r}")
        key, value = line.split("=", 1)
        if key in values:
            raise SmokeFailure(f"duplicate installer env key: {key}")
        values[key] = value
    return values


def replace_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines()
    matches = [index for index, line in enumerate(lines) if line.startswith(key + "=")]
    if len(matches) != 1:
        raise SmokeFailure(f"expected exactly one {key} entry in {path}, got {matches}")
    lines[matches[0]] = f"{key}={value}"
    path.write_text("\n".join(lines) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_private_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise SmokeFailure(f"expected a regular non-symlink file: {path}")
    if path.stat().st_mode & 0o077:
        raise SmokeFailure(f"file grants group/other permissions: {path}")
    if path.stat().st_uid != 0:
        raise SmokeFailure(f"file is not owned by root: {path}")


def assert_private_directory(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        raise SmokeFailure(f"expected a real directory: {path}")
    if path.stat().st_mode & 0o077:
        raise SmokeFailure(f"directory grants group/other permissions: {path}")
    if path.stat().st_uid != 0:
        raise SmokeFailure(f"directory is not owned by root: {path}")


def assert_valid_backup(path: Path) -> None:
    assert_private_file(path)
    if path.stat().st_size <= 0:
        raise SmokeFailure(f"backup is empty: {path}")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise SmokeFailure(f"backup contains no members: {path}")
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise SmokeFailure(f"backup contains unsafe member {member.name!r}: {path}")
            if not (member.isfile() or member.isdir()):
                raise SmokeFailure(
                    f"backup contains link/device/special member {member.name!r}: {path}"
                )
        normalized = {
            member.name.removeprefix("./"): member for member in members
        }
        if "open-node.db" not in normalized or not normalized["open-node.db"].isfile():
            raise SmokeFailure(f"backup does not contain open-node.db: {path}")


def assert_backup_checksums(backup: Path) -> None:
    checksum_file = backup / "SHA256SUMS"
    assert_private_file(checksum_file)
    expected_files = {
        path.name
        for path in backup.iterdir()
        if path.is_file() and path.name != checksum_file.name
    }
    recorded: dict[str, str] = {}
    for number, line in enumerate(checksum_file.read_text().splitlines(), 1):
        match = SHA256_LINE.fullmatch(line)
        if match is None:
            raise SmokeFailure(
                f"invalid SHA256SUMS line {number} in {checksum_file}: {line!r}"
            )
        digest, name = match.groups()
        if name in recorded:
            raise SmokeFailure(f"duplicate checksum entry {name!r} in {checksum_file}")
        recorded[name] = digest
    if set(recorded) != expected_files:
        raise SmokeFailure(
            "backup checksum coverage is not exact: "
            f"recorded={sorted(recorded)}, files={sorted(expected_files)}"
        )
    mismatches = {
        name: (recorded[name], sha256(backup / name))
        for name in sorted(recorded)
        if recorded[name] != sha256(backup / name)
    }
    if mismatches:
        raise SmokeFailure(f"backup checksum mismatch: {mismatches}")


def rendered_compose_json(
    docker: Path,
    compose_file: Path,
    *,
    project: str,
    environment: Mapping[str, str],
) -> dict[str, object]:
    rendered = output(
        docker,
        "compose",
        "--project-name",
        project,
        "--file",
        compose_file,
        "config",
        "--format",
        "json",
        env=environment,
    )
    try:
        value = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Compose did not render JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SmokeFailure("rendered Compose root is not an object")
    return value


def assert_rendered_fixture_namespace(
    rendered: Mapping[str, object],
    *,
    project: str,
    image: str,
    source_root: Path,
    volume: str,
    port: int,
) -> None:
    """Independently bound every candidate resource before installer build/up."""

    unexpected_top_level = set(rendered).difference(
        {"name", "services", "networks", "volumes"}
    )
    if unexpected_top_level:
        raise SmokeFailure(
            f"candidate rendered unexpected top-level resources: {sorted(unexpected_top_level)}"
        )
    services = rendered.get("services")
    volumes = rendered.get("volumes")
    networks = rendered.get("networks")
    if not isinstance(services, dict) or set(services) != {"open-node"}:
        raise SmokeFailure(f"candidate rendered unexpected services: {services!r}")
    if not isinstance(volumes, dict) or set(volumes) != {"data"}:
        raise SmokeFailure(f"candidate rendered unexpected volumes: {volumes!r}")
    if not isinstance(networks, dict) or set(networks) != {"default"}:
        raise SmokeFailure(f"candidate rendered unexpected networks: {networks!r}")
    service = services["open-node"]
    data_volume = volumes["data"]
    default_network = networks["default"]
    if not all(isinstance(item, dict) for item in (service, data_volume, default_network)):
        raise SmokeFailure("candidate service, volume, or network is not an object")
    build = service.get("build")
    mounts = service.get("volumes")
    ports = service.get("ports")
    service_networks = service.get("networks")
    expected_context = str(source_root.resolve())
    unsafe_runtime_fields = {
        "container_name",
        "devices",
        "ipc",
        "network_mode",
        "pid",
        "userns_mode",
    }
    present_unsafe = sorted(unsafe_runtime_fields.intersection(service))
    if present_unsafe:
        raise SmokeFailure(f"candidate rendered unsafe runtime fields: {present_unsafe}")
    allowed_service_fields = {
        "build",
        "cap_drop",
        "environment",
        "image",
        "init",
        "logging",
        "networks",
        "ports",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "stop_grace_period",
        "tmpfs",
        "volumes",
    }
    unexpected_service_fields = set(service).difference(allowed_service_fields)
    if unexpected_service_fields:
        raise SmokeFailure(
            f"candidate rendered unexpected service fields: {sorted(unexpected_service_fields)}"
        )
    if service.get("image") != image:
        raise SmokeFailure(f"candidate rendered non-fixture image: {service.get('image')!r}")
    if not isinstance(build, dict) or build.get("context") != expected_context:
        raise SmokeFailure(
            f"candidate build context escaped fixture checkout: {build!r} != {expected_context!r}"
        )
    if build.get("dockerfile") != "Dockerfile" or set(build).difference(
        {"args", "context", "dockerfile"}
    ):
        raise SmokeFailure(f"candidate rendered build controls outside policy: {build!r}")
    arguments = build.get("args")
    if not isinstance(arguments, dict) or set(arguments) != {"VCS_REF"}:
        raise SmokeFailure(f"candidate rendered unexpected build arguments: {arguments!r}")
    expected_revision = image.rsplit(":preflight-", 1)[-1]
    if arguments.get("VCS_REF") != expected_revision:
        raise SmokeFailure("candidate build revision does not match its isolated image tag")
    if service.get("privileged", False) or service.get("cap_add"):
        raise SmokeFailure("candidate requested privileged mode or added capabilities")
    if service.get("read_only") is not True or service.get("cap_drop") != ["ALL"]:
        raise SmokeFailure("candidate weakened the read-only/capability sandbox")
    if service.get("security_opt") != ["no-new-privileges:true"]:
        raise SmokeFailure("candidate weakened no-new-privileges")
    if service.get("init") is not True or service.get("restart") != "unless-stopped":
        raise SmokeFailure("candidate changed the bounded runtime lifecycle")
    if not isinstance(mounts, list) or len(mounts) != 1:
        raise SmokeFailure(f"candidate rendered unexpected mounts: {mounts!r}")
    mount = mounts[0]
    if not isinstance(mount, dict) or {
        "type": mount.get("type"),
        "source": mount.get("source"),
        "target": mount.get("target"),
    } != {"type": "volume", "source": "data", "target": "/var/lib/open-node"}:
        raise SmokeFailure(f"candidate mount escaped fixture data volume: {mount!r}")
    if mount.get("bind") or mount.get("volume"):
        raise SmokeFailure(f"candidate rendered mount options outside policy: {mount!r}")
    if data_volume.get("name") != volume or data_volume.get("external", False):
        raise SmokeFailure(f"candidate volume escaped fixture namespace: {data_volume!r}")
    if data_volume.get("driver", "local") != "local" or data_volume.get("driver_opts"):
        raise SmokeFailure(f"candidate volume requested host-specific options: {data_volume!r}")
    if default_network.get("name") != f"{project}_default" or default_network.get(
        "external", False
    ):
        raise SmokeFailure(f"candidate network escaped fixture namespace: {default_network!r}")
    if default_network.get("driver", "bridge") != "bridge" or default_network.get(
        "driver_opts"
    ):
        raise SmokeFailure(
            f"candidate network requested host-specific options: {default_network!r}"
        )
    if service_networks not in ({"default": None}, {"default": {}}):
        raise SmokeFailure(f"candidate joined unexpected networks: {service_networks!r}")
    if not isinstance(ports, list) or len(ports) != 1:
        raise SmokeFailure(f"candidate rendered unexpected published ports: {ports!r}")
    published = ports[0]
    if not isinstance(published, dict) or {
        "host_ip": published.get("host_ip"),
        "published": str(published.get("published")),
        "target": published.get("target"),
        "protocol": published.get("protocol", "tcp"),
    } != {
        "host_ip": "127.0.0.1",
        "published": str(port),
        "target": 8080,
        "protocol": "tcp",
    }:
        raise SmokeFailure(f"candidate published outside the fixture endpoint: {published!r}")


def directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        relative = child.relative_to(path).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(child.stat().st_mode & 0o7777).encode())
        digest.update(b"\0")
        if child.is_file() and not child.is_symlink():
            digest.update(sha256(child).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def scrub_open_node_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OPEN_NODE_")
    }


@dataclass
class GitFixture:
    source_repository: Path
    root: Path
    branch: str
    nonce: str
    remote: Path = field(init=False)
    work: Path = field(init=False)
    revisions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.remote = self.root / "fixture-remote.git"
        self.work = self.root / "fixture-work"

    def create(self) -> str:
        command(
            "git",
            "clone",
            "--bare",
            "--no-local",
            self.source_repository,
            self.remote,
        )
        command("git", "clone", "--no-local", self.remote, self.work)
        command("git", "-C", self.work, "checkout", "-b", self.branch)
        command("git", "-C", self.work, "config", "user.name", "Installer Smoke")
        command(
            "git",
            "-C",
            self.work,
            "config",
            "user.email",
            "installer-smoke@invalid.example",
        )
        revision = self.advance("a-initial")
        command("git", "-C", self.work, "push", "-u", "origin", self.branch)
        return revision

    def advance(
        self,
        label: str,
        *,
        unhealthy: bool = False,
        mutate_then_fail: bool = False,
    ) -> str:
        if unhealthy and mutate_then_fail:
            raise SmokeFailure("fixture revision cannot use two unhealthy modes")
        marker = self.work / ".installer-smoke-revision"
        marker.write_text(f"{self.nonce}:{label}\n")
        command("git", "-C", self.work, "add", marker)
        if unhealthy or mutate_then_fail:
            dockerfile = self.work / "Dockerfile"
            dockerfile_source = dockerfile.read_text()
            expected = (
                'CMD ["uvicorn", "open_node.main:app", "--host", "0.0.0.0", '
                '"--port", "8080", "--proxy-headers", "--no-access-log"]'
            )
            if expected not in dockerfile_source:
                raise SmokeFailure("fixture could not locate the production Docker CMD")
            main_module = self.work / "backend/app/open_node/main.py"
            main_source = main_module.read_text()
            app_start = "app = create_app()\n"
            if main_source.count(app_start) != 1:
                raise SmokeFailure("fixture could not locate the normal application startup")
            if mutate_then_fail:
                startup = """def _installer_smoke_mutate_then_fail() -> None:
    import sqlite3
    from pathlib import Path

    database = Path("/var/lib/open-node/open-node.db")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS installer_smoke_mutation "
            "(marker TEXT PRIMARY KEY NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO installer_smoke_mutation(marker) VALUES (?)",
            ("candidate-mutated-live-volume",),
        )
    raise SystemExit(86)


_installer_smoke_mutate_then_fail()
app = create_app()
"""
            else:
                startup = """def _installer_smoke_fail_during_startup() -> None:
    raise SystemExit(86)


_installer_smoke_fail_during_startup()
app = create_app()
"""
            main_module.write_text(
                main_source.replace(app_start, startup, 1)
            )
            command("git", "-C", self.work, "add", main_module)
        command("git", "-C", self.work, "commit", "-m", f"fixture: {label}")
        revision = output("git", "-C", self.work, "rev-parse", "HEAD")
        if len(revision) != 40 or revision in self.revisions:
            raise SmokeFailure(f"fixture did not create a unique full revision: {revision}")
        self.revisions.append(revision)
        if len(self.revisions) > 1:
            command("git", "-C", self.work, "push", "origin", self.branch)
        return revision


@dataclass(frozen=True)
class DeploymentIdentity:
    revision: str
    image_tag: str
    image_repository: str
    env_digest: str
    manifest_digest: str
    source_revision: str
    container_id: str
    container_image: str


@dataclass
class InstallerFixture:
    repository: Path
    temporary: Path
    git: GitFixture
    nonce: str
    port: int = field(default_factory=free_port)
    password: str = field(default_factory=lambda: "installer-smoke-" + secrets.token_hex(12))
    username: str = "installer-admin"

    def __post_init__(self) -> None:
        self.temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.install_dir = self.temporary / "source"
        self.config_dir = self.temporary / "config"
        self.backup_dir = self.temporary / "backups"
        self.password_file = self.temporary / "admin-password"
        self.shim_dir = self.temporary / "shim-bin"
        self.project = "on-inst-" + self.nonce
        self.image_repository = "open-node-installer-" + self.nonce
        self.volume = self.project + "_data"
        self.url = f"http://127.0.0.1:{self.port}"
        self.env_file = self.config_dir / "open-node.env"
        self.manifest_file: Path | None = None
        self.tracked_image_references: set[str] = set()
        self.preflighted_revisions: set[str] = set()
        self.password_file.write_text(self.password + "\n")
        self.password_file.chmod(0o600)
        self.shim_dir.mkdir(mode=0o700)
        self.real_docker = Path(shutil.which("docker") or "").resolve()
        if not self.real_docker.is_file():
            raise SmokeFailure("docker executable was not found")
        self._write_docker_shim()

    @property
    def installer(self) -> Path:
        return self.repository / "install.sh"

    @property
    def compose(self) -> tuple[str, ...]:
        return (
            str(self.real_docker),
            "compose",
            "--env-file",
            str(self.env_file),
            "--project-name",
            self.project,
            "--file",
            str(self.install_dir / "deploy/compose.yaml"),
        )

    def environment(self, **overrides: str) -> dict[str, str]:
        environment = {
            **scrub_open_node_environment(),
            "OPEN_NODE_REPOSITORY": str(self.git.remote),
            "OPEN_NODE_REF": self.git.branch,
            "OPEN_NODE_INSTALL_DIR": str(self.install_dir),
            "OPEN_NODE_CONFIG_DIR": str(self.config_dir),
            "OPEN_NODE_BACKUP_DIR": str(self.backup_dir),
            "OPEN_NODE_PROJECT_NAME": self.project,
            "OPEN_NODE_IMAGE_REPOSITORY": self.image_repository,
            "OPEN_NODE_HTTP_PORT": str(self.port),
            "OPEN_NODE_AUTO_INSTALL_DEPENDENCIES": "0",
            "OPEN_NODE_BUILD_PULL": "0",
            "OPEN_NODE_CREATE_ADMIN": "1",
            "OPEN_NODE_ADMIN_USERNAME": self.username,
            "OPEN_NODE_ADMIN_PASSWORD_FILE": str(self.password_file),
            "OPEN_NODE_SMOKE_REAL_DOCKER": str(self.real_docker),
        }
        environment.update(overrides)
        if any("\n" in value or "\r" in value for value in environment.values()):
            raise SmokeFailure("test environment unexpectedly contains a newline")
        return environment

    def run(
        self,
        action: str,
        *,
        check: bool = True,
        capture: bool = True,
        overrides: Mapping[str, str] | None = None,
        timeout: float = COMMAND_TIMEOUT,
        preflight: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if preflight and action in {"install", "update"}:
            self.assert_candidate_compose_isolated()
        result = command(
            "bash",
            self.installer,
            action,
            env=self.environment(**dict(overrides or {})),
            check=check,
            capture=capture,
            timeout=timeout,
        )
        self.track_images()
        return result

    def candidate_preflight_environment(self, revision: str) -> dict[str, str]:
        return self.environment(
            OPEN_NODE_IMAGE_TAG="preflight-" + revision,
            OPEN_NODE_REVISION=revision,
            OPEN_NODE_BIND_ADDRESS="127.0.0.1",
            OPEN_NODE_SESSION_COOKIE_SECURE="false",
        )

    def render_candidate_compose(
        self, *, compose_file: Path | None = None, source_root: Path | None = None
    ) -> tuple[dict[str, object], str, Path]:
        revision = output("git", "-C", self.git.work, "rev-parse", "HEAD")
        checkout = self.git.work if source_root is None else source_root
        candidate_compose = (
            self.git.work / "deploy/compose.yaml" if compose_file is None else compose_file
        )
        rendered = rendered_compose_json(
            self.real_docker,
            candidate_compose,
            project=self.project,
            environment=self.candidate_preflight_environment(revision),
        )
        return rendered, revision, checkout

    def assert_candidate_compose_isolated(self) -> None:
        status = output("git", "-C", self.git.work, "status", "--porcelain")
        if status:
            raise SmokeFailure(f"fixture candidate checkout is dirty before preflight:\n{status}")
        rendered, revision, source_root = self.render_candidate_compose()
        assert_rendered_fixture_namespace(
            rendered,
            project=self.project,
            image=f"{self.image_repository}:preflight-{revision}",
            source_root=source_root,
            volume=self.volume,
            port=self.port,
        )
        self.preflighted_revisions.add(revision)

    def assert_preflight_rejects_host_bind(self) -> None:
        compose = self.git.work / "deploy/compose.yaml"
        original = compose.read_text()
        expected = "      - data:/var/lib/open-node\n"
        replacement = "      - /:/installer-smoke-host-root:ro\n"
        if expected not in original:
            raise SmokeFailure("fixture could not locate the managed Compose mount")
        try:
            compose.write_text(original.replace(expected, replacement, 1))
            rendered, revision, source_root = self.render_candidate_compose()
            try:
                assert_rendered_fixture_namespace(
                    rendered,
                    project=self.project,
                    image=f"{self.image_repository}:preflight-{revision}",
                    source_root=source_root,
                    volume=self.volume,
                    port=self.port,
                )
            except SmokeFailure:
                pass
            else:
                raise SmokeFailure("fixture preflight accepted a host-root bind mount")
        finally:
            compose.write_text(original)
        if output("git", "-C", self.git.work, "status", "--porcelain"):
            raise SmokeFailure("malicious preflight probe did not restore the fixture checkout")

    def run_with_manifest_defaults(
        self, action: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        environment = scrub_open_node_environment()
        environment["OPEN_NODE_CONFIG_DIR"] = str(self.config_dir)
        result = command(
            "bash",
            self.installer,
            action,
            env=environment,
            check=check,
            capture=True,
            timeout=EXPECTED_FAILURE_TIMEOUT,
        )
        self.track_images()
        return result

    def _write_docker_shim(self) -> None:
        shim = self.shim_dir / "docker"
        shim.write_text(
            """#!/usr/bin/env bash
set -Eeuo pipefail
real_docker="${OPEN_NODE_SMOKE_REAL_DOCKER:?}"

contains_argument() {
  local wanted="$1" argument
  shift
  for argument in "$@"; do
    [[ "$argument" == "$wanted" ]] && return 0
  done
  return 1
}

if [[ "${OPEN_NODE_SMOKE_FAIL_BACKUP:-0}" == "1" ]] \
  && contains_argument run "$@" \
  && contains_argument --entrypoint "$@" \
  && contains_argument tar "$@"; then
  printf 'injected installer smoke backup failure\n' >&2
  exit 86
fi

if [[ "${OPEN_NODE_SMOKE_FAIL_COMPOSE_DOWN:-0}" == "1" ]] \
  && contains_argument compose "$@" \
  && contains_argument down "$@"; then
  if [[ -n "${OPEN_NODE_SMOKE_DOWN_MARKER:-}" ]]; then
    : > "$OPEN_NODE_SMOKE_DOWN_MARKER"
  fi
  printf 'injected installer smoke Compose-down failure\n' >&2
  exit 87
fi

if [[ "${OPEN_NODE_SMOKE_FAIL_PROJECT_RM:-0}" == "1" ]] \
  && [[ "${1:-}" == "rm" ]] \
  && contains_argument -f "$@"; then
  if [[ -n "${OPEN_NODE_SMOKE_RM_MARKER:-}" ]]; then
    : > "$OPEN_NODE_SMOKE_RM_MARKER"
  fi
  printf 'injected installer smoke direct cleanup failure\n' >&2
  exit 88
fi

if [[ -n "${OPEN_NODE_SMOKE_PAUSE_FILE:-}" ]] \
  && [[ -n "${OPEN_NODE_SMOKE_RELEASE_FILE:-}" ]]; then
  should_pause=0
  case "${OPEN_NODE_SMOKE_PAUSE_PHASE:-}" in
    docker-info)
      [[ "${1:-}" == "info" ]] && should_pause=1
      ;;
    image-build)
      contains_argument build "$@" && should_pause=1
      ;;
    compose-up-before)
      contains_argument compose "$@" && contains_argument up "$@" && should_pause=1
      ;;
  esac
  if [[ "$should_pause" == "1" && ! -e "${OPEN_NODE_SMOKE_PAUSE_FILE}.used" ]]; then
    : > "${OPEN_NODE_SMOKE_PAUSE_FILE}.used"
    : > "$OPEN_NODE_SMOKE_PAUSE_FILE"
    while [[ ! -e "$OPEN_NODE_SMOKE_RELEASE_FILE" ]]; do
      sleep 0.1
    done
  fi
fi


if [[ "${OPEN_NODE_SMOKE_PAUSE_PHASE:-}" == "compose-up-after" ]] \
  && contains_argument compose "$@" \
  && contains_argument up "$@"; then
  set +e
  "$real_docker" "$@"
  result="$?"
  set -e
  if [[ ! -e "${OPEN_NODE_SMOKE_PAUSE_FILE}.used" ]]; then
    : > "${OPEN_NODE_SMOKE_PAUSE_FILE}.used"
    : > "$OPEN_NODE_SMOKE_PAUSE_FILE"
    while [[ ! -e "$OPEN_NODE_SMOKE_RELEASE_FILE" ]]; do
      sleep 0.1
    done
  fi
  exit "$result"
fi

exec "$real_docker" "$@"
"""
        )
        shim.chmod(0o700)

    def shim_environment(self, *, phase: str, pause: Path, release: Path) -> dict[str, str]:
        environment = self.environment(
            OPEN_NODE_SMOKE_PAUSE_PHASE=phase,
            OPEN_NODE_SMOKE_PAUSE_FILE=str(pause),
            OPEN_NODE_SMOKE_RELEASE_FILE=str(release),
        )
        environment["PATH"] = str(self.shim_dir) + os.pathsep + environment.get("PATH", "")
        return environment

    def track_images(self) -> None:
        repository_images = command(
            self.real_docker,
            "image",
            "ls",
            "--filter",
            f"reference={self.image_repository}:*",
            "--format",
            "{{.Repository}}:{{.Tag}}",
            capture=True,
            check=False,
        )
        if repository_images.returncode == 0:
            references = set(repository_images.stdout.split())
            if any(
                not reference.startswith(self.image_repository + ":")
                for reference in references
            ):
                raise SmokeFailure(f"Docker returned non-fixture image references: {references}")
            self.tracked_image_references.update(references)

    def container_ids(self, *, all_containers: bool = True) -> list[str]:
        args: list[str | Path] = [self.real_docker, "ps"]
        if all_containers:
            args.append("-a")
        args.extend(
            [
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.ID}}",
            ]
        )
        return output(*args).split()

    def running_container_ids(self) -> list[str]:
        return self.container_ids(all_containers=False)

    def network_names(self) -> list[str]:
        names = set(
            output(
                self.real_docker,
                "network",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.Name}}",
            ).split()
        )
        exact = self.project + "_default"
        if command(
            self.real_docker,
            "network",
            "inspect",
            exact,
            check=False,
            capture=True,
        ).returncode == 0:
            names.add(exact)
        return sorted(names)

    def volume_names(self) -> list[str]:
        names = set(
            output(
                self.real_docker,
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{.Name}}",
            ).split()
        )
        if command(
            self.real_docker,
            "volume",
            "inspect",
            self.volume,
            check=False,
            capture=True,
        ).returncode == 0:
            names.add(self.volume)
        return sorted(names)

    def backup_container_ids(self) -> list[str]:
        prefix = self.project + "-backup-"
        rows = output(
            self.real_docker,
            "ps",
            "-a",
            "--format",
            "{{.ID}}\t{{.Names}}",
        ).splitlines()
        return [
            identifier
            for row in rows
            if "\t" in row
            for identifier, name in [row.split("\t", 1)]
            if name.startswith(prefix)
        ]

    def inspect_expected_backup_container(self, identifier: str) -> dict[str, object]:
        details = json.loads(output(self.real_docker, "inspect", identifier))[0]
        name = str(details.get("Name", "")).removeprefix("/")
        labels = (details.get("Config") or {}).get("Labels") or {}
        if not name.startswith(self.project + "-backup-"):
            raise SmokeFailure(
                f"refusing to clean unexpected backup container name: {name!r}"
            )
        if labels.get("com.open-node.installer.project") != self.project:
            raise SmokeFailure(
                "refusing to clean backup container without exact installer project label"
            )
        if labels.get("com.open-node.installer.purpose") != "backup-helper":
            raise SmokeFailure(
                "refusing to clean backup container without exact backup-helper label"
            )
        return details

    def backup_verify_volume_names(self) -> list[str]:
        prefix = self.project + "-backup-verify-"
        return sorted(
            name
            for name in output(
                self.real_docker, "volume", "ls", "--format", "{{.Name}}"
            ).split()
            if name.startswith(prefix)
        )

    def inspect_expected_container(self, identifier: str) -> dict[str, object]:
        details = json.loads(output(self.real_docker, "inspect", identifier))[0]
        expected_names = {
            f"/{self.project}-open-node-1",
            f"/{self.project}_open-node_1",
        }
        labels = details.get("Config", {}).get("Labels", {})
        if details.get("Name") not in expected_names:
            raise SmokeFailure(
                f"refusing to clean unexpected project container name: {details.get('Name')!r}"
            )
        if labels.get("com.docker.compose.project") != self.project:
            raise SmokeFailure("refusing to clean container without exact fixture project label")
        if labels.get("com.docker.compose.service") != "open-node":
            raise SmokeFailure("refusing to clean container without exact fixture service label")
        return details

    def inspect_expected_network(self, name: str) -> dict[str, object]:
        details = json.loads(output(self.real_docker, "network", "inspect", name))[0]
        labels = details.get("Labels") or {}
        if name != f"{self.project}_default" or details.get("Name") != name:
            raise SmokeFailure(f"refusing to clean unexpected fixture network: {name!r}")
        if labels.get("com.docker.compose.project") != self.project:
            raise SmokeFailure("refusing to clean network without exact fixture project label")
        if labels.get("com.docker.compose.network") != "default":
            raise SmokeFailure("refusing to clean network without exact fixture network label")
        return details

    def inspect_expected_volume(self, name: str) -> dict[str, object]:
        details = json.loads(output(self.real_docker, "volume", "inspect", name))[0]
        labels = details.get("Labels") or {}
        if name != self.volume or details.get("Name") != name:
            raise SmokeFailure(f"refusing to clean unexpected fixture data volume: {name!r}")
        if details.get("Driver") != "local" or details.get("Scope") != "local":
            raise SmokeFailure(f"refusing to clean non-local fixture volume: {details!r}")
        if details.get("Options") not in (None, {}):
            raise SmokeFailure(f"refusing to clean fixture volume with driver options: {details!r}")
        if labels.get("com.docker.compose.project") != self.project:
            raise SmokeFailure("refusing to clean volume without exact fixture project label")
        if labels.get("com.docker.compose.volume") != "data":
            raise SmokeFailure("refusing to clean volume without exact fixture volume label")
        return details

    def assert_isolated_resources(self) -> None:
        containers = self.container_ids()
        if len(containers) != 1:
            raise SmokeFailure(f"expected exactly one fixture container, got {containers}")
        volumes = self.volume_names()
        if volumes != [self.volume]:
            raise SmokeFailure(
                f"fixture did not get its unique named volume {self.volume!r}: {volumes}"
            )
        inspect = json.loads(output(self.real_docker, "inspect", containers[0]))[0]
        image = inspect["Config"]["Image"]
        if not image.startswith(self.image_repository + ":"):
            raise SmokeFailure(
                f"fixture container uses non-isolated image {image!r}; "
                f"expected repository {self.image_repository!r}"
            )
        mounts = [mount["Name"] for mount in inspect["Mounts"] if mount["Type"] == "volume"]
        if mounts != [self.volume]:
            raise SmokeFailure(f"fixture container has unexpected volume mounts: {mounts}")

    def assert_namespace_unused(self) -> None:
        images = output(
            self.real_docker,
            "image",
            "ls",
            "--filter",
            f"reference={self.image_repository}:*",
            "--format",
            "{{.ID}}",
        ).split()
        resources = {
            "containers": self.container_ids(),
            "backup_containers": self.backup_container_ids(),
            "networks": self.network_names(),
            "volumes": self.volume_names(),
            "backup_verify_volumes": self.backup_verify_volume_names(),
            "images": images,
        }
        collisions = {kind: values for kind, values in resources.items() if values}
        if collisions:
            raise SmokeFailure(f"random fixture namespace was already in use: {collisions}")

    def find_manifest(self) -> Path:
        if self.manifest_file is not None:
            return self.manifest_file
        self.manifest_file = self.config_dir / "installer.manifest"
        assert_private_file(self.manifest_file)
        return self.manifest_file

    def identity(self) -> DeploymentIdentity:
        values = parse_env(self.env_file)
        revision = values.get("OPEN_NODE_REVISION", "")
        image_tag = values.get("OPEN_NODE_IMAGE_TAG", "")
        image_repository = values.get("OPEN_NODE_IMAGE_REPOSITORY", "")
        if len(revision) != 40:
            raise SmokeFailure(f"environment has no full active revision: {revision!r}")
        if image_repository != self.image_repository:
            raise SmokeFailure(
                f"environment image repository is {image_repository!r}, "
                f"expected {self.image_repository!r}"
            )
        manifest = parse_env(self.find_manifest())
        expected_manifest = {
            "DEPLOYED_REVISION": revision,
            "DEPLOYED_IMAGE_TAG": image_tag,
            "IMAGE_REPOSITORY": image_repository,
            "PROJECT_NAME": self.project,
            "INSTALL_DIR": str(self.install_dir),
            "CONFIG_DIR": str(self.config_dir),
            "BACKUP_DIR": str(self.backup_dir),
        }
        mismatches = {
            key: (manifest.get(key), expected)
            for key, expected in expected_manifest.items()
            if manifest.get(key) != expected
        }
        if mismatches:
            raise SmokeFailure(f"manifest and active environment disagree: {mismatches}")
        manifest_image_id = manifest.get("DEPLOYED_IMAGE_ID", "")
        inspected_image_id = output(
            self.real_docker,
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            manifest_image_id,
        )
        if inspected_image_id != manifest_image_id:
            raise SmokeFailure(
                f"manifest image ID is not an exact local image identity: {manifest_image_id!r}"
            )
        source_revision = output("git", "-C", self.install_dir, "rev-parse", "HEAD")
        containers = self.container_ids()
        container_id = containers[0] if len(containers) == 1 else ""
        container_image = ""
        if container_id:
            container_image = output(
                self.real_docker,
                "inspect",
                "--format",
                "{{.Config.Image}}",
                container_id,
            )
            container_image_id = output(
                self.real_docker,
                "inspect",
                "--format",
                "{{.Image}}",
                container_id,
            )
            if manifest.get("DEPLOYED_IMAGE_ID") != container_image_id:
                raise SmokeFailure(
                    "manifest image ID does not match the existing project container: "
                    f"{manifest.get('DEPLOYED_IMAGE_ID')!r} != {container_image_id!r}"
                )
        return DeploymentIdentity(
            revision=revision,
            image_tag=image_tag,
            image_repository=image_repository,
            env_digest=sha256(self.env_file),
            manifest_digest=sha256(self.find_manifest()),
            source_revision=source_revision,
            container_id=container_id,
            container_image=container_image,
        )

    def backup_directories(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        entries = list(self.backup_dir.iterdir())
        backups = sorted(
            path for path in entries if path.is_dir() and path.name.startswith("open-node-")
        )
        unexpected = [path for path in entries if path not in backups]
        if unexpected:
            raise SmokeFailure(f"installer left partial/unexpected backup entries: {unexpected}")
        return backups

    def backup_snapshot(self) -> dict[str, str]:
        return {path.name: directory_digest(path) for path in self.backup_directories()}

    def assert_new_backup(
        self, previous: Mapping[str, str], *, expected_revision: str
    ) -> Path:
        current = self.backup_snapshot()
        if any(current.get(name) != digest for name, digest in previous.items()):
            raise SmokeFailure("an update modified an existing immutable backup")
        new_names = sorted(set(current) - set(previous))
        if len(new_names) != 1:
            raise SmokeFailure(f"expected exactly one new backup, got {new_names}")
        backup = self.backup_dir / new_names[0]
        assert_private_directory(backup)
        expected_names = {
            "volume.tar.gz",
            "open-node.env",
            "installer.manifest",
            "compose.yaml",
            "deployment.meta",
            "SHA256SUMS",
        }
        actual_names = {path.name for path in backup.iterdir()}
        if not expected_names.issubset(actual_names):
            raise SmokeFailure(
                f"backup contents {actual_names} do not contain {expected_names}: {backup}"
            )
        for child in backup.iterdir():
            assert_private_file(child)
        assert_valid_backup(backup / "volume.tar.gz")
        assert_backup_checksums(backup)
        old_env = parse_env(backup / "open-node.env")
        if old_env.get("OPEN_NODE_REVISION") != expected_revision:
            raise SmokeFailure(
                f"backup env does not identify old revision {expected_revision}: {old_env}"
            )
        old_manifest = parse_env(backup / "installer.manifest")
        metadata = parse_env(backup / "deployment.meta")
        expected_metadata = {
            "REVISION": expected_revision,
            "IMAGE_TAG": old_env.get("OPEN_NODE_IMAGE_TAG", ""),
            "IMAGE_ID": old_manifest.get("DEPLOYED_IMAGE_ID", ""),
            "PROJECT_NAME": self.project,
            "DATA_VOLUME": self.volume,
        }
        metadata_mismatches = {
            key: (metadata.get(key), expected)
            for key, expected in expected_metadata.items()
            if not expected or metadata.get(key) != expected
        }
        if metadata_mismatches:
            raise SmokeFailure(
                f"deployment.meta does not preserve old deployment identity: "
                f"{metadata_mismatches}"
            )
        if old_manifest.get("DEPLOYED_REVISION") != expected_revision:
            raise SmokeFailure("backup manifest does not identify the old full revision")
        if old_manifest.get("DEPLOYED_IMAGE_TAG") != old_env.get("OPEN_NODE_IMAGE_TAG"):
            raise SmokeFailure("backup manifest and environment disagree on the old image tag")
        rollback_image = metadata.get("ROLLBACK_IMAGE", "")
        rollback_image_id = output(
            self.real_docker,
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            rollback_image,
        )
        if rollback_image_id != metadata["IMAGE_ID"]:
            raise SmokeFailure(
                "rollback tag does not resolve to the immutable old image ID: "
                f"{rollback_image_id!r} != {metadata['IMAGE_ID']!r}"
            )
        if expected_revision[:12] not in backup.name:
            raise SmokeFailure("backup directory name does not identify the old revision")
        return backup

    def sqlite_volume_probe(self, volume: str, image: str) -> dict[str, object]:
        script = """import hashlib
import json
import sqlite3
from pathlib import Path

path = Path('/var/lib/open-node/open-node.db')
if not path.is_file() or path.stat().st_size <= 0:
    raise SystemExit('open-node.db is missing or empty')
with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
    integrity = [row[0] for row in connection.execute('PRAGMA integrity_check')]
    mutation = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'table' AND name = 'installer_smoke_mutation'"
    ).fetchone()[0]
with path.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
print(json.dumps({
    'integrity': integrity,
    'mutation_table': bool(mutation),
    'sha256': digest,
}))
"""
        raw = output(
            self.real_docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=volume,src={volume},dst=/var/lib/open-node,readonly",
            "--entrypoint",
            "python",
            image,
            "-c",
            script,
        )
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"SQLite verification did not return JSON: {raw!r}") from exc
        if result.get("integrity") != ["ok"]:
            raise SmokeFailure(f"SQLite integrity_check failed: {result!r}")
        return result

    def assert_live_volume_mutated(self, backup: Path) -> None:
        metadata = parse_env(backup / "deployment.meta")
        probe = self.sqlite_volume_probe(self.volume, metadata["IMAGE_ID"])
        if probe.get("mutation_table") is not True:
            raise SmokeFailure("unhealthy candidate did not mutate the live SQLite volume")

    def verify_backup_restore(self, backup: Path, *, expected_revision: str) -> None:
        """Restore, boot, and authenticate an immutable bundle outside the active project."""

        assert_backup_checksums(backup)
        backup_digest_before = directory_digest(backup)
        metadata = parse_env(backup / "deployment.meta")
        old_environment = parse_env(backup / "open-node.env")
        rollback_image = metadata.get("ROLLBACK_IMAGE", "")
        expected_image_id = metadata.get("IMAGE_ID", "")
        if metadata.get("REVISION") != expected_revision:
            raise SmokeFailure("restore bundle does not identify the expected revision")
        if not rollback_image.startswith(self.image_repository + ":rollback-"):
            raise SmokeFailure(f"restore bundle has non-fixture rollback image: {rollback_image!r}")
        resolved_image_id = output(
            self.real_docker,
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            rollback_image,
        )
        if not expected_image_id or resolved_image_id != expected_image_id:
            raise SmokeFailure(
                "restore rollback tag is not the immutable recorded image: "
                f"{resolved_image_id!r} != {expected_image_id!r}"
            )

        restore_nonce = secrets.token_hex(5)
        restore_project = "on-restore-" + restore_nonce
        restore_volume = restore_project + "_data"
        restore_network = restore_project + "_default"
        restore_container = restore_project + "-open-node-1"
        restore_port = free_port()
        restore_url = f"http://127.0.0.1:{restore_port}"
        namespace_confirmed_unused = False
        primary_error: BaseException | None = None
        cleanup_failures: list[str] = []

        def remove_restore_container() -> None:
            identifiers = output(
                self.real_docker,
                "ps",
                "-a",
                "--filter",
                f"name=^/{restore_container}$",
                "-q",
            ).split()
            if not identifiers:
                return
            if len(identifiers) != 1:
                raise SmokeFailure(
                    f"restore container name resolved ambiguously: {identifiers!r}"
                )
            details = json.loads(
                output(self.real_docker, "inspect", identifiers[0])
            )[0]
            labels = (details.get("Config") or {}).get("Labels") or {}
            if details.get("Name") != f"/{restore_container}":
                raise SmokeFailure("refusing to remove an unexpected restore container")
            if labels.get("com.docker.compose.project") != restore_project:
                raise SmokeFailure(
                    "refusing to remove restore container without exact project label"
                )
            if labels.get("com.docker.compose.service") != "open-node":
                raise SmokeFailure(
                    "refusing to remove restore container without exact service label"
                )
            if (
                labels.get("com.open-node.installer.purpose")
                != "backup-restore-smoke"
            ):
                raise SmokeFailure(
                    "refusing to remove restore container without exact purpose label"
                )
            result = command(
                self.real_docker,
                "rm",
                "-f",
                identifiers[0],
                check=False,
                capture=True,
            )
            if result.returncode:
                raise SmokeFailure(result.stderr[-2000:])

        def remove_restore_network() -> None:
            names = output(
                self.real_docker, "network", "ls", "--format", "{{.Name}}"
            ).splitlines()
            if restore_network not in names:
                return
            details = json.loads(
                output(self.real_docker, "network", "inspect", restore_network)
            )[0]
            labels = details.get("Labels") or {}
            if details.get("Name") != restore_network:
                raise SmokeFailure("refusing to remove an unexpected restore network")
            if labels.get("com.docker.compose.project") != restore_project:
                raise SmokeFailure(
                    "refusing to remove restore network without exact project label"
                )
            if labels.get("com.docker.compose.network") != "default":
                raise SmokeFailure(
                    "refusing to remove restore network without exact network label"
                )
            if (
                labels.get("com.open-node.installer.purpose")
                != "backup-restore-smoke"
            ):
                raise SmokeFailure(
                    "refusing to remove restore network without exact purpose label"
                )
            result = command(
                self.real_docker,
                "network",
                "rm",
                restore_network,
                check=False,
                capture=True,
            )
            if result.returncode:
                raise SmokeFailure(result.stderr[-2000:])

        def remove_restore_volume() -> None:
            names = output(
                self.real_docker, "volume", "ls", "--format", "{{.Name}}"
            ).splitlines()
            if restore_volume not in names:
                return
            details = json.loads(
                output(self.real_docker, "volume", "inspect", restore_volume)
            )[0]
            labels = details.get("Labels") or {}
            if details.get("Name") != restore_volume:
                raise SmokeFailure("refusing to remove an unexpected restore volume")
            if labels.get("com.docker.compose.project") != restore_project:
                raise SmokeFailure(
                    "refusing to remove restore volume without exact project label"
                )
            if labels.get("com.docker.compose.volume") != "data":
                raise SmokeFailure(
                    "refusing to remove restore volume without exact volume label"
                )
            if (
                labels.get("com.open-node.installer.purpose")
                != "backup-restore-smoke"
            ):
                raise SmokeFailure(
                    "refusing to remove restore volume without exact purpose label"
                )
            result = command(
                self.real_docker,
                "volume",
                "rm",
                restore_volume,
                check=False,
                capture=True,
            )
            if result.returncode:
                raise SmokeFailure(result.stderr[-2000:])

        try:
            if any(
                (
                    command(
                        self.real_docker,
                        kind,
                        "inspect",
                        name,
                        check=False,
                        capture=True,
                    ).returncode
                    == 0
                )
                for kind, name in (
                    ("volume", restore_volume),
                    ("network", restore_network),
                )
            ):
                raise SmokeFailure("random restore namespace unexpectedly already exists")
            if command(
                self.real_docker,
                "ps",
                "-a",
                "--filter",
                f"name=^/{restore_container}$",
                "-q",
                capture=True,
            ).stdout.strip():
                raise SmokeFailure("random restore container unexpectedly already exists")
            namespace_confirmed_unused = True
            command(
                self.real_docker,
                "volume",
                "create",
                "--label",
                f"com.docker.compose.project={restore_project}",
                "--label",
                "com.docker.compose.volume=data",
                "--label",
                "com.open-node.installer.purpose=backup-restore-smoke",
                restore_volume,
            )
            with (backup / "volume.tar.gz").open("rb") as archive:
                extraction = subprocess.run(
                    [
                        str(self.real_docker),
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges:true",
                        "--mount",
                        f"type=volume,src={restore_volume},dst=/var/lib/open-node",
                        "--entrypoint",
                        "tar",
                        rollback_image,
                        "-C",
                        "/var/lib/open-node",
                        "-xzf",
                        "-",
                    ],
                    stdin=archive,
                    text=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=EXPECTED_FAILURE_TIMEOUT,
                )
            if extraction.returncode:
                raise SmokeFailure(
                    "backup extraction failed: "
                    + extraction.stderr.decode(errors="replace")[-2000:]
                )
            probe = self.sqlite_volume_probe(restore_volume, rollback_image)
            if probe.get("mutation_table") is not False:
                raise SmokeFailure("pre-update backup contains candidate mutation state")
            if probe.get("sha256") != metadata.get("DATABASE_SHA256"):
                raise SmokeFailure(
                    "restored SQLite bytes do not match deployment.meta: "
                    f"{probe.get('sha256')!r} != {metadata.get('DATABASE_SHA256')!r}"
                )
            old_manifest = parse_env(backup / "installer.manifest")
            if metadata.get("DOCKER_DAEMON_ID") != old_manifest.get("DOCKER_DAEMON_ID"):
                raise SmokeFailure("backup lost the Docker daemon identity")

            command(
                self.real_docker,
                "network",
                "create",
                "--label",
                f"com.docker.compose.project={restore_project}",
                "--label",
                "com.docker.compose.network=default",
                "--label",
                "com.open-node.installer.purpose=backup-restore-smoke",
                restore_network,
            )
            command(
                self.real_docker,
                "run",
                "-d",
                "--name",
                restore_container,
                "--label",
                f"com.docker.compose.project={restore_project}",
                "--label",
                "com.docker.compose.service=open-node",
                "--label",
                "com.open-node.installer.purpose=backup-restore-smoke",
                "--network",
                restore_network,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp:rw,nosuid,noexec,size=64m,mode=1777",
                "--env-file",
                backup / "open-node.env",
                "--mount",
                f"type=volume,src={restore_volume},dst=/var/lib/open-node",
                "--publish",
                f"127.0.0.1:{restore_port}:8080",
                rollback_image,
            )
            wait_health(restore_url)
            login(restore_url, self.username, self.password)
            container_details = json.loads(
                output(self.real_docker, "inspect", restore_container)
            )[0]
            if container_details.get("Image") != expected_image_id:
                raise SmokeFailure("restored container did not use immutable rollback image ID")
            mounts = [
                mount
                for mount in container_details.get("Mounts", [])
                if mount.get("Destination") == "/var/lib/open-node"
            ]
            if len(mounts) != 1 or mounts[0].get("Name") != restore_volume:
                raise SmokeFailure(f"restored container used an unexpected mount: {mounts!r}")
            if old_environment.get("OPEN_NODE_REVISION") != expected_revision:
                raise SmokeFailure("restored environment lost the original revision identity")
        except BaseException as exc:
            primary_error = exc
        if namespace_confirmed_unused:
            for resource, cleanup in (
                ("restore container", remove_restore_container),
                ("restore network", remove_restore_network),
                ("restore volume", remove_restore_volume),
            ):
                try:
                    cleanup()
                except BaseException as exc:
                    cleanup_failures.append(f"{resource}: {exc}")
        if directory_digest(backup) != backup_digest_before:
            mutation_error = SmokeFailure("backup restore modified the immutable bundle")
            if primary_error is None:
                primary_error = mutation_error
            else:
                primary_error = ExceptionGroup(
                    "backup restore failed and mutated its source bundle",
                    [primary_error, mutation_error],
                )
        if primary_error is not None and cleanup_failures:
            raise ExceptionGroup(
                "backup restore and cleanup both failed",
                [primary_error, SmokeFailure("; ".join(cleanup_failures))],
            )
        if cleanup_failures:
            raise SmokeFailure("backup restore cleanup failed: " + "; ".join(cleanup_failures))
        if primary_error is not None:
            raise primary_error

    def assert_revision(self, revision: str, *, running: bool = True) -> DeploymentIdentity:
        identity = self.identity()
        if identity.revision != revision or identity.source_revision != revision:
            raise SmokeFailure(f"active identity is not revision {revision}: {identity}")
        if revision not in identity.image_tag:
            raise SmokeFailure(
                f"image tag does not contain the full active revision: {identity.image_tag!r}"
            )
        expected_image = f"{self.image_repository}:{identity.image_tag}"
        if running:
            if len(self.running_container_ids()) != 1:
                raise SmokeFailure("expected exactly one running fixture container")
            if identity.container_image != expected_image:
                raise SmokeFailure(
                    f"container image {identity.container_image!r} != {expected_image!r}"
                )
        return identity

    def assert_failed_result(
        self,
        result: subprocess.CompletedProcess[str],
        phrase: str | tuple[str, ...],
    ) -> None:
        phrases = (phrase,) if isinstance(phrase, str) else phrase
        if result.returncode == 0:
            raise SmokeFailure(
                f"installer unexpectedly succeeded; expected one of {phrases} in failure"
            )
        combined = (result.stdout + "\n" + result.stderr).lower()
        if not any(expected.lower() in combined for expected in phrases):
            raise SmokeFailure(
                f"installer failure did not explain one of {phrases!r}:\n"
                f"{combined[-4000:]}"
            )

    def remove_project_containers(self) -> None:
        for container in self.container_ids():
            self.inspect_expected_container(container)
            command(self.real_docker, "rm", "-f", container)

    def cleanup(self) -> None:
        failures: list[str] = []
        # Never execute the candidate's Compose model while cleaning up. Every
        # destructive target must match the random name and Docker labels that
        # this smoke generated before it can be removed.
        for container in self.container_ids():
            try:
                self.inspect_expected_container(container)
            except SmokeFailure as exc:
                failures.append(str(exc))
                continue
            result = command(
                self.real_docker, "rm", "-f", container, check=False, capture=True
            )
            if result.returncode:
                failures.append(
                    f"container cleanup failed for {container}: {result.stderr[-2000:]}"
                )
        for container in self.backup_container_ids():
            try:
                self.inspect_expected_backup_container(container)
            except SmokeFailure as exc:
                failures.append(str(exc))
                continue
            result = command(
                self.real_docker, "rm", "-f", container, check=False, capture=True
            )
            if result.returncode:
                failures.append(
                    f"backup container cleanup failed for {container}: {result.stderr[-2000:]}"
                )
        for network in self.network_names():
            try:
                self.inspect_expected_network(network)
            except SmokeFailure as exc:
                failures.append(str(exc))
                continue
            result = command(
                self.real_docker,
                "network",
                "rm",
                network,
                check=False,
                capture=True,
            )
            if result.returncode:
                failures.append(f"network cleanup failed for {network}: {result.stderr[-2000:]}")
        for volume in self.volume_names():
            try:
                self.inspect_expected_volume(volume)
            except SmokeFailure as exc:
                failures.append(str(exc))
                continue
            result = command(
                self.real_docker,
                "volume",
                "rm",
                volume,
                check=False,
                capture=True,
            )
            if result.returncode:
                failures.append(f"volume cleanup failed for {volume}: {result.stderr[-2000:]}")
        for volume in self.backup_verify_volume_names():
            details = json.loads(output(self.real_docker, "volume", "inspect", volume))[0]
            labels = details.get("Labels") or {}
            if (
                details.get("Name") != volume
                or details.get("Driver") != "local"
                or details.get("Scope") != "local"
                or details.get("Options") not in (None, {})
                or labels.get("com.open-node.installer.project") != self.project
                or labels.get("com.open-node.installer.purpose")
                != "backup-restore-verification"
            ):
                failures.append(
                    f"refusing to clean unexpected backup verification volume: {details!r}"
                )
                continue
            result = command(
                self.real_docker,
                "volume",
                "rm",
                volume,
                check=False,
                capture=True,
            )
            if result.returncode:
                failures.append(
                    f"backup verification volume cleanup failed for {volume}: "
                    f"{result.stderr[-2000:]}"
                )

        self.track_images()
        for image_reference in sorted(self.tracked_image_references):
            if not image_reference.startswith(self.image_repository + ":"):
                failures.append(
                    f"refusing to clean non-fixture image reference: {image_reference!r}"
                )
                continue
            result = command(
                self.real_docker,
                "image",
                "rm",
                image_reference,
                check=False,
                capture=True,
            )
            if result.returncode and "No such image" not in result.stderr:
                failures.append(
                    f"image cleanup failed for {image_reference}: {result.stderr[-2000:]}"
                )

        residues: dict[str, list[str]] = {
            "containers": self.container_ids(),
            "networks": self.network_names(),
            "volumes": self.volume_names(),
        }
        repository_residues = output(
            self.real_docker,
            "image",
            "ls",
            "--filter",
            f"reference={self.image_repository}:*",
            "--format",
            "{{.Repository}}:{{.Tag}}",
        ).split()
        residues["images"] = sorted(set(repository_residues))
        residues["backup_containers"] = self.backup_container_ids()
        residues["backup_verify_volumes"] = self.backup_verify_volume_names()
        remaining = {kind: values for kind, values in residues.items() if values}
        if remaining:
            failures.append(f"fixture resources remain after cleanup: {remaining}")
        if failures:
            raise SmokeFailure("installer smoke cleanup failed:\n- " + "\n- ".join(failures))


def assert_fixture_prerequisites(repository: Path) -> None:
    if os.geteuid() != 0:
        raise SmokeFailure("installer smoke must run as root on the disposable VPS")
    for executable in ("bash", "docker", "git"):
        if shutil.which(executable) is None:
            raise SmokeFailure(f"required executable is missing: {executable}")
    if not (repository / "install.sh").is_file():
        raise SmokeFailure("--repository does not contain install.sh")
    inside = command(
        "git",
        "-C",
        repository,
        "rev-parse",
        "--is-inside-work-tree",
        check=False,
        capture=True,
    )
    if inside.returncode or inside.stdout.strip() != "true":
        raise SmokeFailure("--repository must be a Git checkout")
    command("docker", "info", capture=True)
    command("docker", "compose", "version", capture=True)


def run_concurrent_lock_scenario(fixture: InstallerFixture) -> None:
    pause = fixture.temporary / "lock-pause"
    release = fixture.temporary / "lock-release"
    environment = fixture.shim_environment(
        phase="docker-info", pause=pause, release=release
    )
    first = subprocess.Popen(
        ["bash", str(fixture.installer), "status"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        wait_until(pause.exists, timeout=15, description="first installer lock holder")
        started = time.monotonic()
        second = fixture.run(
            "status", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
        )
        elapsed = time.monotonic() - started
        fixture.assert_failed_result(second, ("lock", "already running", "busy"))
        if elapsed > 10:
            raise SmokeFailure(
                f"concurrent installer waited {elapsed:.1f}s instead of failing fast"
            )
    finally:
        release.touch()
        try:
            stdout, stderr = first.communicate(timeout=EXPECTED_FAILURE_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(first.pid, signal.SIGKILL)
            stdout, stderr = first.communicate()
            raise SmokeFailure("lock-holder installer did not finish after release")
    if first.returncode:
        raise SmokeFailure(
            f"lock-holder installer failed with {first.returncode}:\n{stdout}\n{stderr}"
        )


def run_interruption_scenario(
    fixture: InstallerFixture, active_revision: str, expected_backups: Mapping[str, str]
) -> None:
    fixture.assert_candidate_compose_isolated()
    pause = fixture.temporary / "interrupt-pause"
    release = fixture.temporary / "interrupt-release"
    environment = fixture.shim_environment(
        phase="image-build", pause=pause, release=release
    )
    before = fixture.identity()
    process = subprocess.Popen(
        ["bash", str(fixture.installer), "update"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        wait_until(pause.exists, timeout=30, description="candidate image build pause")
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=EXPECTED_FAILURE_TIMEOUT)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    if process.returncode == 0:
        raise SmokeFailure("interrupted installer unexpectedly exited successfully")
    after = fixture.assert_revision(active_revision)
    if after != before:
        raise SmokeFailure(
            "interruption changed the active deployment identity before activation: "
            f"before={before}, after={after}, stdout={stdout}, stderr={stderr}"
        )
    if fixture.backup_snapshot() != dict(expected_backups):
        raise SmokeFailure("pre-activation interruption unexpectedly changed backups")
    candidate_directories = list(
        fixture.install_dir.parent.glob(".open-node-candidate.*")
    )
    candidate_environments = list(
        fixture.config_dir.glob(".open-node.env.candidate.*")
    )
    if candidate_directories or candidate_environments:
        raise SmokeFailure(
            "pre-activation interruption left transaction files: "
            f"sources={candidate_directories}, envs={candidate_environments}"
        )
    worktrees = output(
        "git", "-C", fixture.install_dir, "worktree", "list", "--porcelain"
    )
    if worktrees.count("worktree ") != 1:
        raise SmokeFailure(f"interruption left a registered candidate worktree:\n{worktrees}")
    if (fixture.config_dir / "installer.recovery").exists():
        raise SmokeFailure("pre-activation interruption incorrectly gated a healthy deployment")
    fixture.run("status")
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)


def run_post_activation_interruption_scenario(
    repository: Path, temporary: Path, git: GitFixture, nonce: str
) -> None:
    fixture = InstallerFixture(
        repository=repository,
        temporary=temporary,
        git=git,
        nonce=nonce,
    )
    scenario_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        fixture.assert_namespace_unused()
        active_revision = git.revisions[-1]
        fixture.run("install")
        wait_health(fixture.url)
        login(fixture.url, fixture.username, fixture.password)
        identity_before = fixture.assert_revision(active_revision)
        backups_before = fixture.backup_snapshot()
        candidate_revision = git.advance("post-activation-interruption")
        fixture.assert_candidate_compose_isolated()
        pause = fixture.temporary / "post-activation-pause"
        release = fixture.temporary / "post-activation-release"
        environment = fixture.shim_environment(
            phase="compose-up-after", pause=pause, release=release
        )
        process = subprocess.Popen(
            ["bash", str(fixture.installer), "update"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            wait_until(
                pause.exists,
                timeout=COMMAND_TIMEOUT,
                description="post-activation Compose-up pause",
            )
            candidate_ids = fixture.running_container_ids()
            if len(candidate_ids) != 1:
                raise SmokeFailure(
                    f"post-activation pause did not leave one candidate: {candidate_ids}"
                )
            candidate_image_revision = output(
                fixture.real_docker,
                "inspect",
                "--format",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
                candidate_ids[0],
            )
            if candidate_image_revision != candidate_revision:
                raise SmokeFailure(
                    "post-activation pause did not reach the candidate image: "
                    f"{candidate_image_revision!r}"
                )
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=EXPECTED_FAILURE_TIMEOUT)
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise
        if process.returncode == 0:
            raise SmokeFailure("post-activation interrupted installer exited successfully")
        backup = fixture.assert_new_backup(
            backups_before, expected_revision=active_revision
        )
        identity_after = fixture.assert_revision(active_revision, running=False)
        if (
            identity_after.revision != identity_before.revision
            or identity_after.image_tag != identity_before.image_tag
            or identity_after.env_digest != identity_before.env_digest
            or identity_after.manifest_digest != identity_before.manifest_digest
            or identity_after.source_revision != identity_before.source_revision
        ):
            raise SmokeFailure(
                "post-activation interruption changed committed identity: "
                f"before={identity_before}, after={identity_after}, "
                f"stdout={stdout}, stderr={stderr}"
            )
        if fixture.container_ids():
            raise SmokeFailure("post-activation interruption did not quarantine containers")
        candidate_directories = list(
            fixture.install_dir.parent.glob(".open-node-candidate.*")
        )
        candidate_environments = list(
            fixture.config_dir.glob(".open-node.env.candidate.*")
        )
        if candidate_directories or candidate_environments:
            raise SmokeFailure(
                "contained interruption left candidate transaction artifacts: "
                f"sources={candidate_directories}, envs={candidate_environments}"
            )
        worktrees = output(
            "git", "-C", fixture.install_dir, "worktree", "list", "--porcelain"
        )
        if worktrees.count("worktree ") != 1:
            raise SmokeFailure(
                f"contained interruption left a registered worktree:\n{worktrees}"
            )
        recovery_file = fixture.config_dir / "installer.recovery"
        assert_private_file(recovery_file)
        recovery = parse_env(recovery_file)
        if not recovery.get("PHASE", "").startswith("interrupted-"):
            raise SmokeFailure(f"interruption recovery phase is not explicit: {recovery}")
        if recovery.get("CANDIDATE_REVISION") != candidate_revision:
            raise SmokeFailure("interruption recovery marker lost candidate revision")
        if recovery.get("BACKUP") != str(backup):
            raise SmokeFailure("interruption recovery marker lost immutable backup path")
        fixture.verify_backup_restore(backup, expected_revision=active_revision)
        blocked = fixture.run(
            "update",
            check=False,
            capture=True,
            timeout=EXPECTED_FAILURE_TIMEOUT,
        )
        fixture.assert_failed_result(blocked, "recovery")
    except BaseException as exc:
        scenario_error = exc
    try:
        fixture.cleanup()
    except BaseException as exc:
        cleanup_error = exc
    if scenario_error is not None and cleanup_error is not None:
        raise ExceptionGroup(
            "post-activation interruption and cleanup both failed",
            [scenario_error, cleanup_error],
        )
    if cleanup_error is not None:
        raise cleanup_error
    if scenario_error is not None:
        raise scenario_error
    print(
        "PASS post-activation interruption quarantined candidate and restored backup identity",
        flush=True,
    )


def run_missing_volume_scenario(
    repository: Path, temporary: Path, git: GitFixture, nonce: str
) -> None:
    fixture = InstallerFixture(
        repository=repository,
        temporary=temporary,
        git=git,
        nonce=nonce,
    )
    scenario_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        active_revision = git.revisions[-1]
        fixture.assert_namespace_unused()
        fixture.run("install")
        wait_health(fixture.url)
        fixture.assert_revision(active_revision)
        fixture.run("uninstall")
        if fixture.container_ids():
            raise SmokeFailure("missing-volume fixture uninstall left containers")
        command(fixture.real_docker, "volume", "rm", fixture.volume)
        identity_before = fixture.identity()
        backups_before = fixture.backup_snapshot()
        result = fixture.run(
            "update",
            check=False,
            capture=True,
            timeout=EXPECTED_FAILURE_TIMEOUT,
        )
        fixture.assert_failed_result(result, "volume")
        if fixture.volume_names() or fixture.container_ids():
            raise SmokeFailure("missing-volume refusal recreated Docker resources")
        if fixture.identity() != identity_before:
            raise SmokeFailure("missing-volume refusal changed the deployment identity")
        if fixture.backup_snapshot() != backups_before:
            raise SmokeFailure("missing-volume refusal changed backups")
    except BaseException as exc:
        scenario_error = exc
    try:
        fixture.cleanup()
    except BaseException as exc:
        cleanup_error = exc
    if scenario_error is not None and cleanup_error is not None:
        raise ExceptionGroup(
            "missing-volume scenario and cleanup both failed",
            [scenario_error, cleanup_error],
        )
    if cleanup_error is not None:
        raise cleanup_error
    if scenario_error is not None:
        raise scenario_error
    print("PASS update refused an installation with a missing named data volume", flush=True)


def run_cleanup_failure_reporting_scenario(
    repository: Path, temporary: Path, git: GitFixture, nonce: str
) -> None:
    """Prove cleanup refuses exact random names when ownership labels drift."""

    fixture = InstallerFixture(
        repository=repository,
        temporary=temporary,
        git=git,
        nonce=nonce,
    )
    fixture.assert_namespace_unused()
    command(
        fixture.real_docker,
        "network",
        "create",
        "--label",
        f"com.docker.compose.project={fixture.project}",
        fixture.project + "_default",
    )
    command(
        fixture.real_docker,
        "volume",
        "create",
        "--label",
        f"com.docker.compose.project={fixture.project}",
        fixture.volume,
    )
    try:
        fixture.cleanup()
    except SmokeFailure as exc:
        message = str(exc)
        if "exact fixture" not in message and "without exact fixture" not in message:
            raise SmokeFailure(
                f"cleanup refusal did not report the label mismatch: {message}"
            ) from exc
    else:
        raise SmokeFailure("label-drift cleanup unexpectedly reported success")
    command(fixture.real_docker, "network", "rm", fixture.project + "_default")
    command(fixture.real_docker, "volume", "rm", fixture.volume)
    if fixture.container_ids() or fixture.network_names() or fixture.volume_names():
        raise SmokeFailure("manual cleanup of the deliberate label drift left resources")
    print("PASS cleanup refused exact names whose ownership labels drifted", flush=True)


def restore_active_container(fixture: InstallerFixture, revision: str) -> None:
    fixture.assert_candidate_compose_isolated()
    command(
        *fixture.compose,
        "up",
        "-d",
        "--no-build",
        env=scrub_open_node_environment(),
    )
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)
    fixture.assert_revision(revision)


def run_active_identity_drift_scenarios(
    fixture: InstallerFixture, *, active_revision: str, old_backup: Path
) -> None:
    baseline = fixture.assert_revision(active_revision)
    backups = fixture.backup_snapshot()
    manifest_path = fixture.find_manifest()
    manifest = parse_env(manifest_path)
    old_metadata = parse_env(old_backup / "deployment.meta")
    old_image_id = old_metadata["IMAGE_ID"]
    current_image_id = manifest["DEPLOYED_IMAGE_ID"]
    active_reference = (
        f"{fixture.image_repository}:{manifest['DEPLOYED_IMAGE_TAG']}"
    )

    original_env = fixture.env_file.read_bytes()
    try:
        replace_env_value(fixture.env_file, "OPEN_NODE_REVISION", "0" * 40)
        result = fixture.run(
            "status", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
        )
        fixture.assert_failed_result(result, ("environment", "revision", "identity"))
    finally:
        fixture.env_file.write_bytes(original_env)
        fixture.env_file.chmod(0o600)
    fixture.assert_revision(active_revision)

    command(fixture.real_docker, "image", "tag", old_image_id, active_reference)
    try:
        result = fixture.run(
            "status", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
        )
        fixture.assert_failed_result(result, ("image tag", "recorded image", "identity"))
    finally:
        command(fixture.real_docker, "image", "tag", current_image_id, active_reference)
    fixture.assert_revision(active_revision)

    original_manifest = manifest_path.read_bytes()
    try:
        replace_env_value(manifest_path, "DEPLOYED_IMAGE_ID", old_image_id)
        result = fixture.run(
            "status", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
        )
        fixture.assert_failed_result(result, ("image", "manifest", "identity"))
    finally:
        manifest_path.write_bytes(original_manifest)
        manifest_path.chmod(0o600)
    fixture.assert_revision(active_revision)

    fixture.remove_project_containers()
    command(fixture.real_docker, "image", "tag", old_image_id, active_reference)
    try:
        command(
            *fixture.compose,
            "create",
            "--no-build",
            "open-node",
            env=scrub_open_node_environment(),
        )
        command(fixture.real_docker, "image", "tag", current_image_id, active_reference)
        result = fixture.run(
            "status", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
        )
        fixture.assert_failed_result(result, ("container image", "manifest", "identity"))
    finally:
        command(fixture.real_docker, "image", "tag", current_image_id, active_reference)
        fixture.remove_project_containers()
    restore_active_container(fixture, active_revision)

    drift_volume = fixture.project + "-mount-drift-" + secrets.token_hex(4)
    fixture.remove_project_containers()
    active_compose = fixture.install_dir / "deploy/compose.yaml"
    original_compose = active_compose.read_bytes()
    try:
        text = original_compose.decode()
        expected = "volumes:\n  data:\n"
        if expected not in text:
            raise SmokeFailure("could not locate active Compose data volume for drift probe")
        active_compose.write_text(
            text.replace(expected, f"volumes:\n  data:\n    name: {drift_volume}\n", 1)
        )
        command(
            *fixture.compose,
            "create",
            "--no-build",
            "open-node",
            env=scrub_open_node_environment(),
        )
        active_compose.write_bytes(original_compose)
        result = fixture.run(
            "status", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
        )
        fixture.assert_failed_result(result, ("mount", "volume", "identity", "health"))
    finally:
        active_compose.write_bytes(original_compose)
        fixture.remove_project_containers()
        if command(
            fixture.real_docker,
            "volume",
            "inspect",
            drift_volume,
            check=False,
            capture=True,
        ).returncode == 0:
            command(fixture.real_docker, "volume", "rm", drift_volume)
    restore_active_container(fixture, active_revision)

    after = fixture.assert_revision(active_revision)
    if (
        after.revision != baseline.revision
        or after.image_tag != baseline.image_tag
        or after.env_digest != baseline.env_digest
        or after.manifest_digest != baseline.manifest_digest
        or after.source_revision != baseline.source_revision
    ):
        raise SmokeFailure(
            f"identity drift probes changed committed state: before={baseline}, after={after}"
        )
    if fixture.backup_snapshot() != backups:
        raise SmokeFailure("identity drift refusals changed immutable backups")
    print(
        "PASS environment, tag, manifest, container, and mount drift were refused",
        flush=True,
    )


def run_scenarios(fixture: InstallerFixture) -> None:
    revision_a = fixture.git.revisions[-1]
    fixture.assert_namespace_unused()
    fixture.assert_preflight_rejects_host_bind()
    print("PASS independent preflight rejected a host-root candidate mount", flush=True)
    fixture.run("install")
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)
    fixture.assert_isolated_resources()
    identity_a = fixture.assert_revision(revision_a)
    assert_private_file(fixture.env_file)
    fixture.run_with_manifest_defaults("status")
    print("PASS fresh isolated install and administrator login", flush=True)

    run_concurrent_lock_scenario(fixture)
    print("PASS concurrent installer invocation fails under the deployment lock", flush=True)

    backups_before_noop = fixture.backup_snapshot()
    fixture.run("update")
    identity_after_noop = fixture.assert_revision(revision_a)
    if identity_after_noop != identity_a:
        raise SmokeFailure(
            "same-revision update overwrote the deployed/rollback identity: "
            f"before={identity_a}, after={identity_after_noop}"
        )
    if fixture.backup_snapshot() != backups_before_noop:
        raise SmokeFailure("same-revision no-op unexpectedly created or changed a backup")
    print("PASS same-revision update preserves exact deployment identity", flush=True)

    revision_b = fixture.git.advance("b-stopped-container")
    containers = fixture.running_container_ids()
    if len(containers) != 1:
        raise SmokeFailure(f"expected one running container before stop: {containers}")
    command(fixture.real_docker, "stop", containers[0])
    if fixture.running_container_ids():
        raise SmokeFailure("fixture container did not stop")
    backups_before_stopped = fixture.backup_snapshot()
    fixture.run("update")
    stopped_backup = fixture.assert_new_backup(
        backups_before_stopped, expected_revision=revision_a
    )
    fixture.verify_backup_restore(stopped_backup, expected_revision=revision_a)
    fixture.assert_revision(revision_b)
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)
    print(
        f"PASS stopped-container update created immutable backup {stopped_backup.name}",
        flush=True,
    )

    run_active_identity_drift_scenarios(
        fixture,
        active_revision=revision_b,
        old_backup=stopped_backup,
    )

    identity_b = fixture.identity()
    backups_with_rollback = fixture.backup_snapshot()
    fixture.run("update")
    if fixture.identity() != identity_b:
        raise SmokeFailure("same-revision update replaced the active transaction image identity")
    if fixture.backup_snapshot() != backups_with_rollback:
        raise SmokeFailure("same-revision update overwrote the retained rollback bundle")
    print("PASS same-revision no-op retained the prior rollback bundle and tag", flush=True)

    fixture.run("uninstall")
    if fixture.container_ids():
        raise SmokeFailure("uninstall left fixture containers")
    if fixture.volume_names() != [fixture.volume]:
        raise SmokeFailure("uninstall did not preserve exactly the fixture volume")
    backups_before_reinstall = fixture.backup_snapshot()
    fixture.run("install")
    fixture.assert_revision(revision_b)
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)
    if fixture.backup_snapshot() != backups_before_reinstall:
        raise SmokeFailure("same-revision reinstall unexpectedly changed backups")
    fixture.run("uninstall")
    if fixture.container_ids() or fixture.volume_names() != [fixture.volume]:
        raise SmokeFailure("second uninstall did not return to volume-only state")
    print("PASS uninstall/reinstall retained administrator data and rollback state", flush=True)

    revision_c = fixture.git.advance("c-volume-only")
    backups_before_volume_only = fixture.backup_snapshot()
    fixture.run("update")
    volume_backup = fixture.assert_new_backup(
        backups_before_volume_only, expected_revision=revision_b
    )
    fixture.assert_revision(revision_c)
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)
    print(
        f"PASS volume-only update created immutable backup {volume_backup.name}",
        flush=True,
    )

    fixture.git.advance("d-backup-failure")
    identity_before_backup_failure = fixture.identity()
    backups_before_failure = fixture.backup_snapshot()
    shim_path = str(fixture.shim_dir) + os.pathsep + fixture.environment().get("PATH", "")
    failure = fixture.run(
        "update",
        check=False,
        capture=True,
        overrides={"PATH": shim_path, "OPEN_NODE_SMOKE_FAIL_BACKUP": "1"},
        timeout=EXPECTED_FAILURE_TIMEOUT,
    )
    fixture.assert_failed_result(failure, "backup")
    if fixture.identity() != identity_before_backup_failure:
        raise SmokeFailure("backup failure changed the active deployment identity")
    if fixture.backup_snapshot() != backups_before_failure:
        raise SmokeFailure("failed backup was published or overwrote an existing backup")
    fixture.assert_revision(revision_c)
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)
    print("PASS injected backup failure retained the healthy active deployment", flush=True)

    revision_d = fixture.git.revisions[-1]
    fixture.run("update")
    fixture.assert_new_backup(backups_before_failure, expected_revision=revision_c)
    fixture.assert_revision(revision_d)
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)

    revision_e = fixture.git.advance("e-interrupted-build")
    backups_before_interrupt = fixture.backup_snapshot()
    run_interruption_scenario(fixture, revision_d, backups_before_interrupt)
    print("PASS interrupted pre-activation update retained exact active state", flush=True)

    fixture.run("update")
    fixture.assert_new_backup(backups_before_interrupt, expected_revision=revision_d)
    fixture.assert_revision(revision_e)
    wait_health(fixture.url)
    login(fixture.url, fixture.username, fixture.password)

    revision_f = fixture.git.advance(
        "f-mutating-unhealthy-candidate", mutate_then_fail=True
    )
    identity_before_health_failure = fixture.identity()
    backups_before_health_failure = fixture.backup_snapshot()
    down_failure_marker = fixture.temporary / "compose-down-failed"
    rm_failure_marker = fixture.temporary / "direct-cleanup-failed"
    health_failure = fixture.run(
        "update",
        check=False,
        capture=True,
        overrides={
            "PATH": str(fixture.shim_dir)
            + os.pathsep
            + fixture.environment().get("PATH", ""),
            "OPEN_NODE_SMOKE_FAIL_COMPOSE_DOWN": "1",
            "OPEN_NODE_SMOKE_DOWN_MARKER": str(down_failure_marker),
            "OPEN_NODE_SMOKE_FAIL_PROJECT_RM": "1",
            "OPEN_NODE_SMOKE_RM_MARKER": str(rm_failure_marker),
        },
        timeout=COMMAND_TIMEOUT,
    )
    fixture.assert_failed_result(
        health_failure, ("containment", "candidate failed", "recovery")
    )
    if not down_failure_marker.exists():
        raise SmokeFailure("mutating candidate did not exercise the Compose-down failure")
    if not rm_failure_marker.exists():
        raise SmokeFailure("mutating candidate did not exercise direct cleanup failure")
    health_backup = fixture.assert_new_backup(
        backups_before_health_failure, expected_revision=revision_e
    )
    remaining_candidates = fixture.container_ids()
    if len(remaining_candidates) != 1:
        raise SmokeFailure(
            "double containment failure did not preserve exactly one candidate container: "
            f"{remaining_candidates}"
        )
    candidate_details = fixture.inspect_expected_container(remaining_candidates[0])
    candidate_revision = (candidate_details.get("Config") or {}).get("Labels", {}).get(
        "org.opencontainers.image.revision"
    )
    if candidate_revision != revision_f:
        raise SmokeFailure(
            f"containment residue is not the failed candidate: {candidate_revision!r}"
        )
    candidate_directories = list(
        fixture.install_dir.parent.glob(".open-node-candidate.*")
    )
    candidate_environments = list(
        fixture.config_dir.glob(".open-node.env.candidate.*")
    )
    if len(candidate_directories) != 1 or len(candidate_environments) != 1:
        raise SmokeFailure(
            "containment failure did not preserve candidate diagnostics: "
            f"sources={candidate_directories}, envs={candidate_environments}"
        )
    recovery_file = fixture.config_dir / "installer.recovery"
    assert_private_file(recovery_file)
    recovery = parse_env(recovery_file)
    active_manifest = parse_env(fixture.find_manifest())
    expected_recovery = {
        "ACTIVE_REVISION": revision_e,
        "ACTIVE_IMAGE_ID": active_manifest["DEPLOYED_IMAGE_ID"],
        "CANDIDATE_REVISION": revision_f,
        "BACKUP": str(health_backup),
    }
    recovery_mismatches = {
        key: (recovery.get(key), expected)
        for key, expected in expected_recovery.items()
        if recovery.get(key) != expected
    }
    if not recovery.get("PHASE", "").startswith("containment-failed-"):
        recovery_mismatches["PHASE"] = (
            recovery.get("PHASE"),
            "containment-failed-*",
        )
    if recovery_mismatches:
        raise SmokeFailure(
            "containment marker does not identify active/candidate/backup state: "
            f"{recovery_mismatches}"
        )
    fixture.remove_project_containers()
    fixture.assert_live_volume_mutated(health_backup)
    fixture.verify_backup_restore(health_backup, expected_revision=revision_e)
    identity_after_health_failure = fixture.identity()
    if identity_after_health_failure.revision != revision_e:
        raise SmokeFailure(
            "unhealthy candidate replaced the active revision identity: "
            f"{identity_after_health_failure}"
        )
    if (
        identity_after_health_failure.env_digest != identity_before_health_failure.env_digest
        or identity_after_health_failure.manifest_digest
        != identity_before_health_failure.manifest_digest
        or identity_after_health_failure.source_revision != revision_e
    ):
        raise SmokeFailure(
            "health failure did not restore the exact active source/env/manifest identity"
        )
    worktrees = output(
        "git", "-C", fixture.install_dir, "worktree", "list", "--porcelain"
    )
    if worktrees.count("worktree ") != 2:
        raise SmokeFailure(f"containment failure did not preserve candidate worktree:\n{worktrees}")
    blocked = fixture.run(
        "update", check=False, capture=True, timeout=EXPECTED_FAILURE_TIMEOUT
    )
    fixture.assert_failed_result(blocked, "recovery")
    print(
        "PASS mutating candidate exposed double-containment failure and backup "
        "restored original login",
        flush=True,
    )

    if len(set(fixture.git.revisions)) < 2:
        raise SmokeFailure("smoke did not exercise at least two real Git revisions")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    args = parser.parse_args()
    repository = args.repository.resolve()
    assert_fixture_prerequisites(repository)

    nonce = secrets.token_hex(6)
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    with tempfile.TemporaryDirectory(
        prefix=f"open-node-installer-{nonce}-", dir="/root"
    ) as raw:
        temporary = Path(raw)
        if temporary.stat().st_uid != 0 or temporary.stat().st_mode & 0o077:
            raise SmokeFailure(f"temporary root is not root-owned mode 0700: {temporary}")
        git_fixture = GitFixture(
            source_repository=repository,
            root=temporary,
            branch="installer-smoke-" + nonce,
            nonce=nonce,
        )
        git_fixture.create()
        run_missing_volume_scenario(
            repository,
            temporary / "missing-volume",
            git_fixture,
            nonce + "-missing",
        )
        run_cleanup_failure_reporting_scenario(
            repository,
            temporary / "cleanup-failure",
            git_fixture,
            nonce + "-cleanup",
        )
        run_post_activation_interruption_scenario(
            repository,
            temporary / "post-activation-interruption",
            git_fixture,
            nonce + "-post-activation",
        )
        fixture = InstallerFixture(
            repository=repository,
            temporary=temporary / "lifecycle",
            git=git_fixture,
            nonce=nonce,
        )
        try:
            run_scenarios(fixture)
        except BaseException as exc:
            primary_error = exc
        try:
            fixture.cleanup()
        except BaseException as exc:
            cleanup_error = exc

    if primary_error is not None and cleanup_error is not None:
        raise ExceptionGroup(
            "installer smoke and mandatory cleanup both failed",
            [primary_error, cleanup_error],
        )
    if cleanup_error is not None:
        raise cleanup_error
    if primary_error is not None:
        raise primary_error
    print("PASS cleanup removed every isolated container, network, volume, and image", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
