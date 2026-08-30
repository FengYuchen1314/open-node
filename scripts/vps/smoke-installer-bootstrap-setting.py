"""Real Compose preflight for the optional Agent bootstrap setting.

The default mode never starts a container. --guarded-update additionally creates
one UUID-scoped Docker deployment, exercises same-source enable/disable updates
through the real public installer, and removes only its owned Docker resources.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
KEY = "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL"
CONFIGURED = "https://panel.example.test/prefix"
REVISION = "a" * 40
ABSENT = object()
SNAPSHOT_SKIPPED_DIRECTORIES = {
    ".git", ".venv", "node_modules", "dist", "dist-probe", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".wrangler", "test-results", "playwright-report",
}

HARNESS = r"""set -Eeuo pipefail
definitions="$1"
operation="$2"
source_dir="$3"
environment_file="$4"
extra="${5:-}"
source "$definitions"
trap - EXIT INT TERM HUP
COMPOSE=(docker compose)
PROJECT_NAME="$SMOKE_PROJECT"
DATA_VOLUME="${PROJECT_NAME}_data"
IMAGE_REPOSITORY="$SMOKE_IMAGE"
ENV_FILE="$environment_file"
case "$operation" in
  validate)
    validate_candidate_compose "$source_dir" "$environment_file" "$IMAGE_REPOSITORY:fixture"
    ;;
  render)
    compose_with "$source_dir" "$environment_file" config --format json
    ;;
  expected)
    rendered="$(compose_with "$source_dir" "$environment_file" config --format json)"
    value="$(read_key "$environment_file" OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL || true)"
    bootstrap_environment_from_config "$rendered" "$value"
    ;;
  runtime)
    rendered="$(compose_with "$source_dir" "$environment_file" config --format json)"
    value="$(read_key "$environment_file" OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL || true)"
    expected="$(bootstrap_environment_from_config "$rendered" "$value")"
    runtime_bootstrap_environment_matches "$expected" "$(<"$extra")"
    ;;
  create)
    create_candidate_environment "$source_dir" "$environment_file" "$extra"
    ;;
  requested)
    requested_bootstrap_change
    ;;
  value)
    validate_agent_bootstrap_value "$extra"
    ;;
  *) exit 97 ;;
esac
"""


class SmokeFailure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeFailure(message)


def private_write(path, content):
    path.write_bytes(content)
    path.chmod(0o600)


def git_environment():
    return {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
    }


@contextmanager
def isolated_git_environment():
    """Also protect imported fixture helpers that deliberately inherit their parent env."""
    previous = {key: value for key, value in os.environ.items() if key.startswith("GIT_")}
    safe = {key: value for key, value in git_environment().items() if key.startswith("GIT_")}
    for key in previous:
        del os.environ[key]
    os.environ.update(safe)
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("GIT_"):
                del os.environ[key]
        os.environ.update(previous)


def read_command(arguments):
    executed = subprocess.run(
        arguments, capture_output=True, check=False, timeout=40, env=git_environment(),
    )
    require(
        executed.returncode == 0 and len(executed.stdout) <= 4 * 1024 * 1024,
        "Read-only fixture inspection failed",
    )
    return executed.stdout


def production_snapshot(project):
    """Inspect only metadata; never execute or write inside the production container."""
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project), "Invalid production project")
    selector = "label=com.docker.compose.project=" + project
    ids = read_command(["docker", "ps", "-aq", "--filter", selector]).decode().split()
    require(ids, "Production project is absent; cannot prove its identity was preserved")
    containers = json.loads(read_command(["docker", "inspect", *ids]))
    result = {"project": project, "containers": [], "networks": [], "volumes": []}
    for item in sorted(containers, key=lambda value: value["Id"]):
        require(
            item["Config"]["Labels"].get("com.docker.compose.project") == project,
            "Unexpected production container identity",
        )
        result["containers"].append({
            "id": item["Id"], "name": item["Name"], "image_id": item["Image"],
            "status": item["State"]["Status"], "pid": item["State"]["Pid"],
            "started_at": item["State"]["StartedAt"],
            "environment_sha256": hashlib.sha256(
                json.dumps(item["Config"].get("Env", []), sort_keys=True).encode()
            ).hexdigest(),
            "mounts": item["Mounts"],
        })
    for kind in ("network", "volume"):
        names = read_command([
            "docker", kind, "ls", "--filter", selector, "--format", "{{.Name}}",
        ]).decode().split()
        require(names, "Production network or volume identity is absent")
        items = json.loads(read_command(["docker", kind, "inspect", *names]))
        for item in sorted(items, key=lambda value: value["Name"]):
            require(
                (item.get("Labels") or {}).get("com.docker.compose.project") == project,
                "Unexpected production storage or network identity",
            )
            result[kind + "s"].append({
                "name": item["Name"], "id": item.get("Id"), "driver": item["Driver"],
                "mountpoint": item.get("Mountpoint"),
                "containers": sorted((item.get("Containers") or {}).keys()),
            })
    return result


def image_api_resources(fixture, repository, *, value, server_id=None):
    fixture.assert_isolated_resources()
    container = fixture.identity().container_id
    probe = (
        "import hashlib,json; from open_node.services.agent_bootstrap_release "
        "import installer_bytes,release_manifest; "
        "print(json.dumps({'installer_sha256':hashlib.sha256(installer_bytes()).hexdigest(),"
        "'release':release_manifest()}))"
    )
    packaged = json.loads(read_command(["docker", "exec", container, "python", "-c", probe]))
    resources = repository / "backend/app/open_node/resources"
    expected = json.loads((resources / "agent-release.json").read_bytes())
    checksum = hashlib.sha256((resources / "agent_installer.py").read_bytes()).hexdigest()
    require(
        packaged == {"release": expected, "installer_sha256": checksum},
        "Installed image did not package the exact bootstrap resources",
    )

    def checked(response, *, status=200, no_store=False):
        require(response.status_code == status, "Unexpected installed bootstrap API status")
        require(len(response.content) <= 262144, "Installed bootstrap API body is too large")
        if no_store:
            require(
                "no-store" in response.headers.get("cache-control", "")
                and response.headers.get("referrer-policy") == "no-referrer",
                "Installed bootstrap API is missing private-response headers",
            )
        return response

    with httpx.Client(base_url=fixture.url, trust_env=False, timeout=15) as client:
        manifest = checked(client.get("/api/v1/agents/bootstrap/manifest"), no_store=True)
        require(manifest.json() == expected, "Image manifest HTTP response differs")
        installer = checked(client.get("/api/v1/agents/bootstrap/installer.py"), no_store=True)
        require(
            hashlib.sha256(installer.content).hexdigest() == checksum,
            "Image installer HTTP response differs",
        )
        session = checked(client.post("/api/v1/auth/login", json={
            "username": fixture.username, "password": fixture.password,
        }, headers={"X-Open-Node-Client": "browser"})).json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        if server_id is None:
            created = checked(client.post("/api/v1/servers", json={
                "name": "bootstrap-configuration-smoke", "domain": "127.0.0.1",
            }), status=201).json()
            server_id = created["server"]["id"]
        state = checked(
            client.get(f"/api/v1/servers/{server_id}/bootstrap"), no_store=True
        ).json()
        require(
            state["configured"] is bool(value)
            and state["control_url"] == (value or None)
            and state["release"]["agent_version"] == expected["agent"]["version"],
            "Committed environment was not reflected in bootstrap availability",
        )
    return server_id, {
        "image_resources_readable": True, "installer_sha256": checksum,
        "public_manifest_matches": True, "public_installer_matches": True,
        "configured": bool(value), "control_url": value or None,
    }


def parse_environment(path):
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    }


def source_library(installer, target):
    text = installer.read_text()
    boundary = '\nmain "$@"\n'
    require(text.endswith(boundary) and text.count(boundary) == 1,
            "Installer main invocation boundary changed; refusing to source the entry point")
    require(text.count("\nmain() {\n") == 1, "Installer main definition boundary changed")
    definitions = text.removesuffix(boundary) + "\n"
    private_write(target, definitions.encode())
    syntax = subprocess.run(["bash", "-n", target], capture_output=True, check=False, timeout=10)
    require(syntax.returncode == 0, "Installer function library is not valid Bash")
    return target


def preflight_matrix(repository, work):
    require(os.geteuid() == 0, "This private-file fixture requires root on the test VPS")
    for executable in ("docker", "bash", "jq", "install", "sync"):
        require(shutil.which(executable), "Missing Compose preflight prerequisite")
    library = source_library(repository / "install.sh", work / "installer-functions.sh")
    project, image = "on-bootstrap-config-" + secrets.token_hex(6), "on-bootstrap-config-fixture"
    environment = {
        "PATH": os.environ.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "LANG": "C.UTF-8", "SMOKE_PROJECT": project, "SMOKE_IMAGE": image,
    }
    compose = (repository / "deploy/compose.yaml").read_text()
    line = "      OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL: ${OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL:-}\n"
    require(compose.count(line) == 1, "Expected one optional bootstrap Compose field")
    source = {}
    for name, text in {
        "new": compose,
        "legacy": compose.replace(line, ""),
        "mismatch": compose.replace(line, "      OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL: https://other.test\n"),
        "unexpected": compose.replace(line, line + "      UNREVIEWED_SETTING: unexpected\n"),
    }.items():
        directory = work / name
        (directory / "deploy").mkdir(parents=True, mode=0o700)
        private_write(directory / "deploy/compose.yaml", text.encode())
        shutil.copyfile(repository / "Dockerfile", directory / "Dockerfile")
        shutil.copyfile(repository / "deploy/.env.example", directory / "deploy/.env.example")
        source[name] = directory

    def env_file(name, value=ABSENT):
        entries = {
            "OPEN_NODE_IMAGE_REPOSITORY": image, "OPEN_NODE_IMAGE_TAG": "fixture",
            "OPEN_NODE_REVISION": REVISION, "OPEN_NODE_BIND_ADDRESS": "127.0.0.1",
            "OPEN_NODE_HTTP_PORT": "38080", "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_SHORT_LINKS_ENABLED": "false", "OPEN_NODE_TRUSTED_PROXIES": "",
            "OPEN_NODE_AGENT_IDENTITY_FILE": "", "OPEN_NODE_SUBSCRIBER_TOTP_KEY": "",
        }
        if value is not ABSENT:
            entries[KEY] = value
        path = work / (name + ".env")
        content = "".join(key + "=" + value + "\n" for key, value in entries.items())
        private_write(path, content.encode())
        return path

    blank = env_file("blank", "")
    configured = env_file("configured", CONFIGURED)
    legacy = env_file("old")
    checks = []

    def call(name, operation, src, config, *, extra="", override=ABSENT, accepted=True):
        environ = dict(environment)
        if override is not ABSENT:
            environ[KEY] = override
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-s", "--", library, operation, src, config, extra],
            input=HARNESS, text=True, capture_output=True, check=False, env=environ, timeout=40,
            cwd=work,
        )
        require((result.returncode == 0) == accepted, "Compose preflight scenario failed: " + name)
        checks.append(name)
        print("PASS " + name, flush=True)
        return result.stdout

    for src, config, label in [
        ("new", blank, "new-blank"), ("new", configured, "new-configured"),
        ("new", legacy, "new-with-old-environment"), ("legacy", legacy, "legacy-absent"),
        ("legacy", configured, "legacy-rollback-retains-unused-value"),
    ]:
        call(label, "validate", source[src], config)
    call("extra-environment-key-rejected", "validate", source["unexpected"], blank, accepted=False)
    call("rendered-value-mismatch-rejected", "validate", source["mismatch"], blank, accepted=False)
    call("shell-pollution-does-not-override-private-env", "validate", source["new"], configured,
         override="https://inherited.example.test")
    actual = json.loads(call("blank-env-ignores-inherited-value", "render", source["new"], blank,
                             override="https://inherited.example.test"))
    require(
        actual["services"]["open-node"]["environment"][KEY] == "",
        "Inherited value was rendered",
    )
    for src, config, expected in [
        ("new", blank, [KEY + "="]), ("new", configured, [KEY + "=" + CONFIGURED]),
        ("legacy", legacy, []), ("legacy", configured, []),
    ]:
        derived = json.loads(call(
            f"expected-runtime-{src}-{config.stem}", "expected", source[src], config
        ))
        require(derived == expected, "Runtime bootstrap expectation differs from source Compose")
        actual_file = work / f"inspect-{src}-{config.stem}.json"
        details = [{"Config": {"Env": ["PATH=/usr/bin", *expected]}}]
        private_write(actual_file, json.dumps(details).encode())
        call(f"matching-runtime-{src}-{config.stem}", "runtime", source[src], config,
             extra=actual_file)
        unexpected = [KEY + "=https://unexpected.example.test"]
        private_write(actual_file, json.dumps([{"Config": {"Env": unexpected}}]).encode())
        call(f"unexpected-runtime-{src}-{config.stem}", "runtime", source[src], config,
             extra=actual_file, accepted=False)
        if expected:
            private_write(actual_file, json.dumps([{"Config": {"Env": expected * 2}}]).encode())
            call(f"duplicate-runtime-{src}-{config.stem}", "runtime", source[src], config,
                 extra=actual_file, accepted=False)
    for name, baseline, override, expected, src in [
        ("old-env-gains-disabled-default", legacy, ABSENT, "", "new"),
        ("configured-env-is-preserved", configured, ABSENT, CONFIGURED, "new"),
        ("explicit-enable", blank, CONFIGURED, CONFIGURED, "new"),
        ("explicit-disable", configured, "", "", "new"),
        ("legacy-default-remains-disabled", legacy, ABSENT, "", "legacy"),
    ]:
        destination = work / (name + ".candidate")
        call(name, "create", source[src], destination, extra=baseline, override=override)
        require(
            parse_environment(destination)[KEY] == expected, "Candidate setting was not preserved"
        )
        require(destination.stat().st_mode & 0o077 == 0, "Candidate environment is not private")
    call("legacy-explicit-enable-rejected", "create", source["legacy"], work / "rejected.candidate",
         extra=legacy, override=CONFIGURED, accepted=False)
    for name, baseline, override, changed in [
        ("no-request-is-noop", configured, ABSENT, False),
        ("same-request-is-noop", configured, CONFIGURED, False),
        ("same-empty-request-is-noop", legacy, "", False),
        ("enable-request-triggers-transaction", blank, CONFIGURED, True),
        ("disable-request-triggers-transaction", configured, "", True),
    ]:
        call(name, "requested", source["new"], baseline, override=override, accepted=changed)
    for index, value in enumerate([
        "http://panel.example.test", "https://user:secret@panel.example.test",
        "https://panel.example.test?token=secret", "https://panel.example.test/#secret",
        "https://panel.example.test:0", "https://panel.example.test:65536",
        "https://panel.example.test:", "https://panel.example.test/../prefix",
        "https://panel.example.test/$INHERITED", "https://panel.example.test/`command`",
        "https://panel.example.test/\\escape", "https://panel.example.test/white space",
        "https://panel.example.test/\nsecond", "https://panel.example.test/\rsecond",
        "https://[:::1]", "https://[::1::2]", "https://panel.example.test/\x7f",
    ]):
        call(f"unsafe-setting-{index}-rejected", "value", source["new"], blank,
             extra=value, accepted=False)
    call("valid-ipv6-prefix", "value", source["new"], blank, extra="https://[::1]:8443/prefix")
    call("new-install-default-remains-disabled", "create", source["new"], work / "new.candidate")
    require(
        parse_environment(work / "new.candidate")[KEY] == "",
        "New install enabled Agent bootstrap",
    )
    return checks


def source_path_allowed(relative):
    return (
        not any(part in SNAPSHOT_SKIPPED_DIRECTORIES for part in relative.parts)
        and relative.parts[:2] != ("backend", "data")
        and relative.parts[0] != "data"
        and not re.search(
            r"\.(?:log|tar|tgz|tar\.gz|zip|db|sqlite3?)(?:-wal|-shm)?$", relative.name
        )
        and not (relative.name.startswith(".env") and relative.name != ".env.example")
    )


def snapshot_repository(repository, destination):
    """Commit only a disposable fixture snapshot, including current uncommitted task files."""
    destination.mkdir(mode=0o700)
    probe = subprocess.run(["git", "-C", repository, "ls-files", "--cached", "--others",
                            "--exclude-standard", "-z"], capture_output=True, check=False,
                           env=git_environment(), timeout=40)
    if probe.returncode == 0:
        relatives = list(dict.fromkeys(
            Path(name.decode()) for name in probe.stdout.split(b"\0") if name
        ))
    else:
        relatives = []
        for root, directories, files in os.walk(repository, followlinks=False):
            directory = Path(root)
            directories[:] = [
                name for name in directories
                if source_path_allowed((directory / name).relative_to(repository))
            ]
            require(
                not any((directory / name).is_symlink() for name in directories),
                "Snapshot contains a linked directory",
            )
            relatives.extend((directory / name).relative_to(repository) for name in files)
    for relative in relatives:
        if not source_path_allowed(relative):
            continue
        source = repository / relative
        require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe snapshot path")
        require(
            source.is_file() and not source.is_symlink(),
            "Snapshot contains a non-regular file",
        )
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(source, target)
    env = git_environment()
    for arguments in [
        ["git", "init", "-b", "bootstrap-fixture"],
        ["git", "config", "user.name", "Bootstrap Config Smoke"],
        ["git", "config", "user.email", "bootstrap-config@invalid.example"],
        ["git", "add", "--all"],
        ["git", "commit", "--quiet", "-m", "fixture: current bootstrap setting implementation"],
    ]:
        result = subprocess.run(arguments, cwd=destination, env=env, capture_output=True,
                                check=False, timeout=60)
        require(result.returncode == 0, "Could not create the private Git fixture snapshot")
    tracked = read_command(["git", "-C", destination, "ls-files", "-z"])
    paths = [Path(name.decode()) for name in tracked.split(b"\0") if name]
    require(
        paths and all(source_path_allowed(path) for path in paths),
        "Generated archive, log, database, cache, or private environment entered fixture Git",
    )
    return {
        "tracked_file_count": len(paths),
        "tracked_paths_sha256": hashlib.sha256(tracked).hexdigest(),
        "generated_inputs_excluded": True,
    }


def guarded_update(repository, work, output, production_project):
    with isolated_git_environment():
        return _guarded_update(repository, work, output, production_project)


def _guarded_update(repository, work, output, production_project):
    production_before = production_snapshot(production_project)
    private_write(
        output / "production-before.json", json.dumps(production_before, indent=2).encode()
    )
    spec = importlib.util.spec_from_file_location(
        "bootstrap_control_installer_smoke",
        repository / "scripts/vps/smoke-control-plane-installer.py",
    )
    previous = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = previous
    spec.loader.exec_module(previous)
    seed = work / "seed"
    source_manifest = snapshot_repository(repository, seed)
    previous.assert_fixture_prerequisites(seed)
    nonce = secrets.token_hex(6)
    git = previous.GitFixture(seed, work, "bootstrap-setting-" + nonce, nonce)
    revision = git.create()
    fixture = previous.InstallerFixture(seed, work / "deployment", git, nonce)
    result = {
        "status": "failed", "fixture_revision": revision, "project": fixture.project,
        "source_snapshot": source_manifest, "api_checks": {},
    }
    secrets_to_remove = {fixture.password}
    namespace_owned = False

    def run(action, label, **overrides):
        print("RUN " + label, flush=True)
        executed = fixture.run(action, check=False, overrides=overrides)
        text = executed.stdout + executed.stderr
        for value in secrets_to_remove:
            text = text.replace(value, "[REDACTED]")
        private_write(output / (label + ".log"), text.encode())
        require(executed.returncode == 0, "Guarded installer scenario failed: " + label)
        print("PASS " + label, flush=True)
        return executed

    try:
        fixture.assert_namespace_unused()
        namespace_owned = True
        run("install", "fresh-disabled")
        previous.wait_health(fixture.url)
        previous.login(fixture.url, fixture.username, fixture.password)
        initial = fixture.assert_revision(revision)
        require(previous.parse_env(fixture.env_file)[KEY] == "", "Fresh install enabled bootstrap")
        server_id, result["api_checks"]["fresh"] = image_api_resources(fixture, seed, value="")
        unchanged = run("update", "same-source-default-noop")
        require("no image was rebuilt" in unchanged.stdout and fixture.identity() == initial,
                "Default same-source update no longer preserves exact identity")
        backups = fixture.backup_snapshot()
        run("update", "same-source-enable", **{KEY: CONFIGURED})
        enabled = fixture.assert_revision(revision)
        first = fixture.assert_new_backup(backups, expected_revision=revision)
        require(
            previous.parse_env(fixture.env_file)[KEY] == CONFIGURED,
            "Enable request was not committed",
        )
        require(
            previous.parse_env(first / "open-node.env")[KEY] == "",
            "Enable backup lost old config",
        )
        require(
            enabled.image_tag != initial.image_tag and enabled.container_id != initial.container_id,
            "Explicit configuration change bypassed the candidate transaction",
        )
        previous.login(fixture.url, fixture.username, fixture.password)
        _, result["api_checks"]["enabled"] = image_api_resources(
            fixture, seed, value=CONFIGURED, server_id=server_id
        )
        baseline = fixture.backup_snapshot()
        run("update", "same-source-same-setting-noop", **{KEY: CONFIGURED})
        require(fixture.identity() == enabled and fixture.backup_snapshot() == baseline,
                "Unchanged explicit setting unexpectedly rebuilt or backed up")
        run("update", "same-source-disable", **{KEY: ""})
        disabled = fixture.assert_revision(revision)
        second = fixture.assert_new_backup(baseline, expected_revision=revision)
        require(
            previous.parse_env(fixture.env_file)[KEY] == "", "Disable request was not committed"
        )
        require(previous.parse_env(second / "open-node.env")[KEY] == CONFIGURED,
                "Disable backup lost the enabled setting")
        require(disabled.image_tag != enabled.image_tag, "Disable request was treated as a no-op")
        previous.login(fixture.url, fixture.username, fixture.password)
        _, result["api_checks"]["disabled"] = image_api_resources(
            fixture, seed, value="", server_id=server_id
        )
        fixture.run_with_manifest_defaults("status")
        fixture.assert_isolated_resources()
        result.update(status="passed", same_revision_enable=True, same_revision_disable=True,
                      immutable_backups=2, no_op_preserved=True, administrator_preserved=True)
    except BaseException as error:
        result["error"] = str(error) if isinstance(error, SmokeFailure) else type(error).__name__
    finally:
        if not namespace_owned:
            result["owned_resources_cleaned"] = False
            result["cleanup_skipped_no_namespace_ownership"] = True
        else:
            try:
                fixture.cleanup()
                result["owned_resources_cleaned"] = True
            except BaseException as error:
                result["owned_resources_cleaned"] = False
                result["cleanup_error"] = type(error).__name__
                result["status"] = "failed"
        try:
            production_after = production_snapshot(production_project)
            private_write(
                output / "production-after.json", json.dumps(production_after, indent=2).encode()
            )
            result["production_unchanged"] = production_before == production_after
            if not result["production_unchanged"]:
                result["status"] = "failed"
        except BaseException as error:
            result["production_unchanged"] = False
            result["production_check_error"] = type(error).__name__
            result["status"] = "failed"
        private_write(output / "guarded-update.json", json.dumps(result, indent=2).encode())
    return result


def safety_negative_controls(repository, work):
    """Private Git redirection test and a mocked collision; never create Docker resources."""
    from types import SimpleNamespace
    from unittest.mock import patch

    root = work / "safety-negative-controls"
    root.mkdir(mode=0o700)
    external, source, destination = root / "external", root / "source", root / "snapshot"
    external.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    private_write(external / "sentinel.txt", b"private external repository must not change\n")
    for command in (
        ["git", "-C", external, "init", "-b", "sentinel"],
        ["git", "-C", external, "config", "user.name", "Safety Sentinel"],
        ["git", "-C", external, "config", "user.email", "safety@invalid.example"],
        ["git", "-C", external, "add", "sentinel.txt"],
        ["git", "-C", external, "commit", "--quiet", "-m", "private sentinel"],
    ):
        read_command(command)

    def external_identity():
        return {
            str(path.relative_to(external)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in external.rglob("*") if path.is_file()
        }

    before = external_identity()
    private_write(source / "sentinel.txt", b"disposable source content\n")
    private_write(source / "module.py", b"VALUE = 1\n")
    for name in ("overlay.tar", "backend-suite.log", "fixture.sqlite", ".env"):
        private_write(source / name, b"excluded generated input\n")
    (source / "backend/data").mkdir(mode=0o700, parents=True)
    private_write(source / "backend/data/private.txt", b"excluded database directory\n")
    poisoned = {
        "GIT_DIR": str(external / ".git"), "GIT_COMMON_DIR": str(external / ".git"),
        "GIT_WORK_TREE": str(external), "GIT_INDEX_FILE": str(external / ".git/index"),
        "GIT_OBJECT_DIRECTORY": str(external / ".git/objects"),
        "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": str(external), "GIT_CONFIG_KEY_1": "core.bare",
        "GIT_CONFIG_VALUE_1": "true", "GIT_CONFIG_GLOBAL": str(external / ".git/config"),
        "GIT_CONFIG_SYSTEM": str(external / ".git/config"),
        "GIT_CONFIG_PARAMETERS": "'core.bare=true'", "GIT_NAMESPACE": "untrusted",
        "GIT_TEMPLATE_DIR": str(external / "untrusted-template"),
    }
    with patch.dict(os.environ, poisoned):
        manifest = snapshot_repository(source, destination)
        with isolated_git_environment():
            spec = importlib.util.spec_from_file_location(
                "bootstrap_git_safety_smoke",
                repository / "scripts/vps/smoke-control-plane-installer.py",
            )
            previous = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = previous
            try:
                spec.loader.exec_module(previous)
                fixture_root = root / "helper"
                fixture_root.mkdir(mode=0o700)
                helper = previous.GitFixture(destination, fixture_root, "safety-helper", "safe")
                helper.create()
            finally:
                sys.modules.pop(spec.name, None)
        require(
            all(os.environ.get(key) == value for key, value in poisoned.items()),
            "Git isolation did not restore the caller environment",
        )
    require(
        before == external_identity(), "Git redirection changed the private sentinel repository"
    )
    require(
        manifest["tracked_file_count"] == 2, "Generated fixture inputs entered the Git snapshot"
    )
    print("PASS Git redirection leaves external index/config/refs/objects unchanged", flush=True)

    class CollisionFixture:
        project = "preexisting-private-collision"
        password = "private-test-placeholder"
        cleanup_calls = 0

        def assert_namespace_unused(self):
            raise SmokeFailure("Injected namespace collision")

        def cleanup(self):
            self.cleanup_calls += 1

    collision = CollisionFixture()
    fake_previous = SimpleNamespace(
        assert_fixture_prerequisites=lambda _seed: None,
        GitFixture=lambda *_args: SimpleNamespace(create=lambda: REVISION),
        InstallerFixture=lambda *_args: collision,
    )
    fake_spec = SimpleNamespace(
        name="bootstrap_collision_safety_smoke",
        loader=SimpleNamespace(exec_module=lambda _module: None),
    )
    collision_work, collision_output = root / "collision", root / "collision-output"
    collision_work.mkdir(mode=0o700)
    collision_output.mkdir(mode=0o700)
    with (
        patch.dict(_guarded_update.__globals__, {
            "production_snapshot": lambda _project: {"mocked_negative_control": True},
            "snapshot_repository": lambda *_args: {"mocked_negative_control": True},
        }),
        patch.object(importlib.util, "spec_from_file_location", return_value=fake_spec),
        patch.object(importlib.util, "module_from_spec", return_value=fake_previous),
    ):
        try:
            result = guarded_update(repository, collision_work, collision_output, "not-production")
        finally:
            sys.modules.pop(fake_spec.name, None)
    require(
        result["status"] == "failed" and collision.cleanup_calls == 0
        and result.get("cleanup_skipped_no_namespace_ownership") is True,
        "Namespace collision invoked cleanup without acquiring ownership",
    )
    print("PASS namespace collision performs zero resource cleanup calls", flush=True)
    return {
        "git_redirection_environment_variables": len(poisoned),
        "external_index_config_refs_objects_unchanged": True,
        "imported_git_helper_isolated": True, "generated_inputs_excluded": True,
        "namespace_collision_cleanup_calls": collision.cleanup_calls,
        "docker_resources_created": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guarded-update", action="store_true")
    parser.add_argument("--production-project", default="open-node")
    parser.add_argument("--safety-negative-controls", action="store_true")
    args = parser.parse_args()
    repository, output = args.repository.resolve(), args.output.absolute()
    require(not output.is_symlink(), "Output must not be a symbolic link")
    require(not output.exists() or not any(output.iterdir()), "Use a new, empty output directory")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.chmod(0o700)
    report = {"status": "failed", "containers_started": args.guarded_update}
    work = Path(tempfile.mkdtemp(prefix="open-node-bootstrap-setting-", dir="/root"))
    cleanup_allowed = not args.guarded_update
    try:
        report["preflight_checks"] = preflight_matrix(repository, work)
        if args.safety_negative_controls:
            report["safety_negative_controls"] = safety_negative_controls(repository, work)
        if args.guarded_update:
            guarded = guarded_update(repository, work, output, args.production_project)
            report["guarded_update"] = guarded
            cleanup_allowed = guarded.get("owned_resources_cleaned") is True
            require(
                guarded["status"] == "passed",
                "Guarded update failed; inspect the private sanitized report",
            )
        report["status"] = "passed"
    except BaseException as error:
        report["error"] = (
            str(error) if isinstance(error, SmokeFailure) else type(error).__name__
        )
    finally:
        if cleanup_allowed:
            require(
                work.parent == Path("/root") and not work.is_symlink()
                and re.fullmatch(r"open-node-bootstrap-setting-[A-Za-z0-9_-]+", work.name),
                "Refusing cleanup of an unrecognized fixture directory",
            )
            shutil.rmtree(work)
        else:
            report["retained_private_work"] = str(work)
        private_write(output / "report.json", json.dumps(report, indent=2).encode())
    print(
        f"Installer bootstrap setting smoke {report['status']}: {output / 'report.json'}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
