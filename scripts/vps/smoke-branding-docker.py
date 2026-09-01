"""Isolated, offline Docker persistence gate for public site and brand text.

Only explicitly identified existing images are used: never build, pull, tag or
remove an image. Every resource has a random branding-specific ownership label.
Containers use UID/GID 10001, a read-only root, no capabilities, no-new-privileges
and network none. Credentials and HTTP jobs go only through subprocess stdin.

The sibling notification fixture supplies explicitly selected generic Docker
utilities, not its notification exercise, helper, ownership globals or results.
This gate has its own source/asset manifests, cold-copy checks and cleanup report.
The disabled synthetic notification vault is an unchanged-data sentinel only.
No Telegram delivery or browser rendering claim is made by this fixture.

Run --self-test on the disposable VPS before the real --image/--revision gate.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
INFRA_PATH = Path(__file__).with_name("smoke-notifications-docker.py")
_spec = importlib.util.spec_from_file_location("_branding_docker_infrastructure", INFRA_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError("branding_infrastructure_unavailable")
infra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(infra)

SmokeFailure = infra.SmokeFailure
require = infra.require
digest = infra.digest
write_json = infra.write_json
OWNER_LABEL = "io.open-node.smoke.branding.owner"
ROLE_LABEL = "io.open-node.smoke.branding.role"
DATA = "/var/lib/open-node"
PUBLIC = "/api/v1/branding"
PRIVATE = "/api/v1/system-settings/branding"
SYNTHETIC_TOKEN = "900000002:FixtureBrandingTelegramNeverUseAsRealCredential_42"
CHAT_ID = "-1001234567891"
INVALID_MARKER = "branding-fixture-input-must-not-be-reflected"
ERRORS = {
    "branding_invalid_request": "Invalid branding settings request.",
    "branding_revision_conflict": "Branding settings changed; reload before saving.",
    "branding_storage_unavailable": "Branding settings storage is temporarily unavailable.",
}

# Code only: passwords, tokens, cookies, CSRF and request bodies are stdin jobs.
CONTAINER_HELPER = r"""
import hashlib
import http.client
import importlib.util
import json
import os
import re
import shutil
import socket
import sqlite3
import stat
import sys
from pathlib import Path

DATA = Path("/var/lib/open-node")
SOURCE = Path("/source")
PURPOSE = "open-node.notifications.telegram.v1"


class Failure(Exception):
    pass


def check(condition, code):
    if not condition:
        raise Failure(code)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def private(path, directory=False):
    check(not any(p.is_symlink() for p in (path, *path.parents)), "private_symlink")
    mode = path.lstat()
    check(mode.st_uid == 10001 and mode.st_gid == 10001, "private_owner")
    check(stat.S_IMODE(mode.st_mode) == (0o700 if directory else 0o600), "private_mode")
    check(stat.S_ISDIR(mode.st_mode) if directory else stat.S_ISREG(mode.st_mode), "private_type")
    check(directory or mode.st_nlink == 1, "private_hardlink")


def sandbox():
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                  if ":" in line)
    check(os.geteuid() == 10001 and os.getegid() == 10001, "effective_user")
    check(int(fields["CapEff"].strip(), 16) == 0, "effective_capabilities")
    check(fields["NoNewPrivs"].strip() == "1", "no_new_privileges")
    check(socket.if_nameindex() == [(1, "lo")], "external_interface")
    check(bool(os.statvfs("/").f_flag & os.ST_RDONLY), "root_writable")
    return {"uid": 10001, "gid": 10001, "capabilities": 0, "root_readonly": True,
            "no_new_privileges": True, "network": "loopback_only",
            "netns": os.readlink("/proc/self/ns/net")}


def manifest(root, *, volume=False, python_only=False):
    check(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "manifest_root")
    if volume:
        private(root, True)
    result, total = {}, 0
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        check(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), "manifest_special_file")
        if python_only and ("__pycache__" in path.parts or path.suffix != ".py"):
            continue
        if volume:
            check(info.st_uid == 10001 and info.st_gid == 10001, "volume_owner")
        if stat.S_ISREG(info.st_mode):
            check(not volume or info.st_nlink == 1, "volume_hardlink")
            total += info.st_size
        check(total <= 64 * 1024 * 1024 and len(result) < 1024, "manifest_size")
        if not volume and stat.S_ISDIR(info.st_mode):
            continue
        value = {"size": info.st_size, "sha256": digest(path)} if path.is_file() else {
            "size": None, "sha256": None}
        if volume:
            value.update(mode=oct(stat.S_IMODE(info.st_mode)), uid=info.st_uid, gid=info.st_gid)
        result[path.relative_to(root).as_posix()] = value
    check(bool(result), "manifest_empty")
    return result


def snapshot(job):
    private(DATA, True)
    key = DATA / "notifications/telegram.key"
    marker = DATA / "notifications/telegram.initialized"
    private(key.parent, True)
    private(key)
    private(marker)
    connection = sqlite3.connect("file:/var/lib/open-node/open-node.db?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        business = {}
        for (name,) in tables:
            check(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name), "unexpected_table_name")
            if name == "site_branding_settings":
                continue
            columns = [row[1] for row in connection.execute('PRAGMA table_info("' + name + '")')]
            # Normal authentication refreshes precisely this timestamp. Every
            # other session field, table and column remains in the comparison.
            if name == "operator_sessions":
                check("last_seen_at" in columns, "session_schema_changed")
                columns.remove("last_seen_at")
            check(all(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", c) for c in columns), "column_name")
            sql = 'SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM "' + name + '"'
            rows = list(connection.execute(sql))
            check(len(rows) < 10000, "snapshot_row_limit")
            encoded = json.dumps(sorted(rows, key=repr), sort_keys=True, ensure_ascii=True,
                                 default=lambda value: {"blob_hex": value.hex()}).encode()
            business[name] = {"columns": columns, "rows": len(rows),
                              "sha256": hashlib.sha256(encoded).hexdigest()}
        row = connection.execute(
            "SELECT enabled,token_ciphertext,key_fingerprint FROM notification_settings WHERE id=1"
        ).fetchone()
        check(row and row[0] == 0 and row[1], "notification_sentinel_not_disabled")
        from cryptography.fernet import Fernet
        raw_key = key.read_bytes()
        check(len(raw_key) <= 128, "key_size_limit")
        fingerprint = hashlib.sha256(PURPOSE.encode() + b"\x00" + raw_key).hexdigest()
        check(row[2] == fingerprint, "notification_key_fingerprint")
        check(json.loads(Fernet(raw_key).decrypt(row[1].encode()))
              == {"purpose": PURPOSE, "token": job["token"]}, "notification_sentinel_decrypt")
        check(json.loads(marker.read_bytes())
              == {"purpose": PURPOSE, "key_fingerprint": fingerprint}, "notification_marker")
        for name in ("notification_requests", "notification_deliveries", "notification_attempts"):
            check(business[name]["rows"] == 0, "branding_created_notification_work")
        needles = [value.encode() for value in job["secret_markers"] if value]
        total = 0
        for path in DATA.rglob("*"):
            check(not path.is_symlink(), "state_symlink")
            if not path.is_file():
                continue
            total += path.stat().st_size
            check(total <= 64 * 1024 * 1024, "state_size_limit")
            tail = b""
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    data = tail + block
                    check(all(needle not in data for needle in needles),
                          "plaintext_secret_persisted")
                    tail = data[-max((len(n) for n in needles), default=1):]
        return {"business_tables": business,
                "volatile_exclusion": {"operator_sessions": ["last_seen_at"]},
                "notification_key_sha256": digest(key),
                "notification_marker_sha256": digest(marker),
                "notification_decryption_verified": True, "notification_disabled": True}
    finally:
        connection.close()


def main(job):
    proof = sandbox()
    operation = job["operation"]
    if operation == "sandbox":
        return proof
    if operation == "source":
        root = Path(importlib.util.find_spec("open_node").origin).parent
        return manifest(root, python_only=True)
    if operation == "frontend":
        return manifest(Path("/opt/open-node/frontend"))
    if operation == "snapshot":
        return snapshot(job)
    if operation == "copy":
        private(DATA, True)
        before = manifest(SOURCE, volume=True)
        check(not list(DATA.iterdir()), "copy_destination_not_empty")
        check({"open-node.db", "notifications/telegram.key", "notifications/telegram.initialized"}
              <= set(before), "cold_backup_incomplete")
        shutil.copytree(SOURCE, DATA, dirs_exist_ok=True, copy_function=shutil.copy2)
        after = manifest(DATA, volume=True)
        check(manifest(SOURCE, volume=True) == before == after, "cold_copy_changed_bytes_or_modes")
        return {"source": before, "destination": after, "equal": True}
    if operation in {"http", "spa"}:
        path = "/system-settings" if operation == "spa" else job["path"]
        check(path.startswith(("/api/v1/", "/healthz", "/system-settings")), "http_path")
        connection = http.client.HTTPConnection("127.0.0.1", 62031, timeout=5)
        try:
            body = None if job.get("body") is None else json.dumps(job["body"]).encode()
            check(body is None or len(body) <= 8192, "http_request_limit")
            connection.request(job.get("method", "GET"), path, body=body,
                               headers=job.get("headers", {}))
            response = connection.getresponse()
            data = response.read(131073)
            check(len(data) <= 131072, "http_response_limit")
            check(all(v.encode() not in data for v in job.get("secret_markers", []) if v),
                  "secret_reflected_in_http")
            result = {"status": response.status, "headers": response.getheaders()}
            if operation == "spa":
                result.update(body_size=len(data), body_sha256=hashlib.sha256(data).hexdigest())
            else:
                result["body"] = json.loads(data) if data else None
            return result
        finally:
            connection.close()
    raise Failure("unknown_branding_helper_operation")


try:
    raw = sys.stdin.buffer.read(65537)
    check(len(raw) <= 65536, "job_size_limit")
    print(json.dumps({"result": main(json.loads(raw))}, sort_keys=True))
except Failure as error:
    print(json.dumps({"error": str(error)}))
    raise SystemExit(1) from None
except Exception:
    print(json.dumps({"error": "branding_container_helper_failed"}))
    raise SystemExit(1) from None
"""


def validate_arguments(image: str, revision: str) -> None:
    require(
        isinstance(image, str) and re.fullmatch(r"[a-z0-9][a-z0-9._/:@-]{0,255}", image),
        "invalid_image_reference",
    )
    require(
        isinstance(revision, str)
        and re.fullmatch(
            r"(?:[0-9a-f]{40}|working-tree[-:][A-Za-z0-9._-]{1,100})",
            revision,
        ),
        "explicit_revision_required",
    )


def source_manifest() -> dict:
    root = ROOT / "backend/app/open_node"
    require(
        root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
        "source_root_invalid",
    )
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "source_symlink")
        if path.is_file() and path.suffix == ".py" and "__pycache__" not in path.parts:
            result[path.relative_to(root).as_posix()] = {
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
    require(
        {"main.py", "domain/branding.py", "services/branding.py", "api/routes/branding.py"}
        <= set(result),
        "branding_source_missing",
    )
    return result


def branding_value(value: object, site: str, brand: str, revision: int | None = None) -> None:
    expected = {"site_title": site, "brand_title": brand, "license_required": False}
    if revision is not None:
        expected["revision"] = revision
    require(
        isinstance(value, dict)
        and value == expected
        and value.get("license_required") is False
        and (revision is None or type(value.get("revision")) is int),
        "branding_dto_mismatch",
    )


def branding_error(value: object, code: str) -> None:
    require(
        code in ERRORS
        and isinstance(value, dict)
        and value
        == {
            "code": code,
            "detail": ERRORS[code],
            "license_required": False,
        }
        and value.get("license_required") is False,
        "branding_error_not_fixed",
    )


def spa_job(markers: list[str]) -> dict:
    return {
        "operation": "spa", "headers": {"Accept": "text/html"}, "secret_markers": markers,
    }


def cas_winner(results: list[dict], pairs: list[tuple[str, str]], revision: int) -> dict:
    require(len(results) == len(pairs) == 2, "cas_result_count")
    require(sorted(r["status"] for r in results) == [200, 409], "cas_did_not_have_one_winner")
    winner = None
    for result, (site, brand) in zip(results, pairs, strict=True):
        if result["status"] == 200:
            branding_value(result["body"], site, brand, revision + 1)
            winner = result["body"]
        else:
            branding_error(result["body"], "branding_revision_conflict")
    require(winner is not None, "cas_winner_missing")
    return winner


class Fixture:
    # Reuse only generic infrastructure with self-owned implementations beneath.
    command = infra.Fixture.command
    inspect = infra.Fixture.inspect
    protected = infra.Fixture.protected
    persist = infra.Fixture.persist
    phase = infra.Fixture.phase
    assert_hardening = infra.Fixture.assert_hardening
    wait_ready = infra.Fixture.wait_ready
    start = infra.Fixture.start
    stop = infra.Fixture.stop
    decode_helper = staticmethod(infra.Fixture.decode_helper)

    def __init__(self, image: str, revision: str, output: Path):
        validate_arguments(image, revision)
        require(infra.ROOT == ROOT, "infrastructure_source_root_mismatch")
        self.image_argument, self.revision, self.output = image, revision, output
        self.owner = secrets.token_hex(16)
        self.prefix = "open-node-branding-smoke-" + self.owner[:16]
        self.docker = shutil.which("docker")
        require(self.docker is not None, "docker_missing")
        self.password, self.cookie, self.csrf = secrets.token_urlsafe(32), "", ""
        self.image_id = ""
        self.volumes: dict[str, str] = {}
        self.containers: dict[str, dict] = {}
        self.app_files, self.frontend_files = {}, {}
        self.report = {
            "feature": "site-branding",
            "status": "running",
            "phases": [],
            "owner": self.owner,
            "owner_label": OWNER_LABEL,
            "resource_prefix": self.prefix,
            "containers": self.containers,
            "volumes": self.volumes,
            "revision": revision,
            "revision_kind": "commit"
            if re.fullmatch(r"[0-9a-f]{40}", revision)
            else "working-tree",
            "fixture_sha256": digest(Path(__file__)),
            "infrastructure_sha256": digest(INFRA_PATH),
            "network": "none; container-loopback HTTP jobs and secrets only through stdin",
            "telegram_contact": False,
            "telegram_acceptance_verified": False,
            "browser_rendering_verified": False,
        }
        self.persist()

    def preflight(self) -> None:
        require(
            not os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"),
            "docker_remote_override_refused",
        )
        context = json.loads(self.command("context", "inspect").stdout)
        require(
            isinstance(context, list)
            and len(context) == 1
            and context[0].get("Endpoints", {}).get("docker", {}).get("Host")
            in {"unix:///var/run/docker.sock", "unix:///run/docker.sock"},
            "docker_context_not_local_socket",
        )
        details = self.inspect("image", self.image_argument)
        self.image_id = details["Id"]
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id), "image_id_not_immutable")
        labels = details["Config"].get("Labels") or {}
        require(
            labels.get("org.opencontainers.image.revision") == self.revision,
            "image_revision_mismatch",
        )
        require(
            labels.get("org.opencontainers.image.source") == infra.IMAGE_SOURCE,
            "image_source_mismatch",
        )
        require(details["Config"].get("User") == "10001:10001", "image_not_nonroot")
        require(details.get("Os") == "linux", "linux_image_required")
        require(set(details["Config"].get("Volumes") or {}) == {DATA}, "unowned_volume_targets")
        require(
            details["Config"].get("StopSignal") in (None, "", "SIGTERM", "15"),
            "image_stop_signal_not_sigterm",
        )
        self.app_files, self.frontend_files = source_manifest(), infra.frontend_manifest()
        write_json(self.output / "app-source-manifest.json", self.app_files)
        write_json(self.output / "frontend-source-manifest.json", self.frontend_files)
        self.report["image"] = {
            "id": self.image_id,
            "revision": self.revision,
            "source": infra.IMAGE_SOURCE,
            "user": "10001:10001",
        }
        self.report["source_manifests"] = {
            "app_files": len(self.app_files),
            "frontend_files": len(self.frontend_files),
            "app_sha256": digest(self.output / "app-source-manifest.json"),
            "frontend_sha256": digest(self.output / "frontend-source-manifest.json"),
        }
        require(
            not self.command(
                "ps", "-aq", "--filter", f"label={OWNER_LABEL}={self.owner}"
            ).stdout.strip(),
            "owner_container_collision",
        )
        require(
            not self.command(
                "volume", "ls", "-q", "--filter", f"label={OWNER_LABEL}={self.owner}"
            ).stdout.strip(),
            "owner_volume_collision",
        )
        self.persist()

    def owned_volume(self, name: str) -> dict:
        require(
            name in self.volumes and name == f"{self.prefix}-{self.volumes[name]}",
            "volume_not_recorded",
        )
        value = self.inspect("volume", name)
        labels = value.get("Labels") or {}
        require(
            value["Name"] == name
            and labels.get(OWNER_LABEL) == self.owner
            and labels.get(ROLE_LABEL) == self.volumes[name],
            "volume_owner_mismatch",
        )
        require(
            value["Driver"] == value["Scope"] == "local" and value.get("Options") in (None, {}),
            "volume_driver_not_private_local",
        )
        return value

    def create_volume(self, role: str) -> str:
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,39}", role), "invalid_resource_role")
        name = f"{self.prefix}-{role}"
        require(self.inspect("volume", name, optional=True) is None, "volume_name_collision")
        self.volumes[name] = role
        self.persist()
        result = self.command(
            "volume",
            "create",
            "--label",
            f"{OWNER_LABEL}={self.owner}",
            "--label",
            f"{ROLE_LABEL}={role}",
            name,
        )
        require(result.stdout.decode().strip() == name, "created_volume_name_mismatch")
        self.owned_volume(name)
        return name

    def owned_container(self, name: str) -> dict:
        require(
            name in self.containers and name == f"{self.prefix}-{self.containers[name]['role']}",
            "container_not_recorded",
        )
        value = self.inspect("container", name)
        labels = value["Config"].get("Labels") or {}
        require(
            value["Name"] == "/" + name
            and labels.get(OWNER_LABEL) == self.owner
            and labels.get(ROLE_LABEL) == self.containers[name]["role"],
            "container_owner_mismatch",
        )
        expected = self.containers[name].get("id")
        require(expected is None or value["Id"] == expected, "container_id_changed")
        require(value["Image"] == self.image_id, "container_image_changed")
        return value

    def create_container(self, role: str, volume: str, *, source=None, job=None) -> str:
        require(re.fullmatch(r"[a-z][a-z0-9-]{0,39}", role), "invalid_resource_role")
        self.owned_volume(volume)
        if source is not None:
            self.owned_volume(source)
            require(source != volume, "copy_uses_same_volume")
            # All consumers of a cold source must already be stopped.
            for name, record in self.containers.items():
                if source in record["mounts"].values():
                    require(
                        not self.owned_container(name)["State"]["Running"], "copy_source_is_live"
                    )
        name = f"{self.prefix}-{role}"
        require(self.inspect("container", name, optional=True) is None, "container_name_collision")
        mounts = {DATA: volume, **({"/source": source} if source else {})}
        self.containers[name] = {"role": role, "mounts": mounts}
        self.persist()
        arguments = [
            "create",
            "--pull",
            "never",
            "--name",
            name,
            "--label",
            f"{OWNER_LABEL}={self.owner}",
            "--label",
            f"{ROLE_LABEL}={role}",
            "--user",
            "10001:10001",
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--network",
            "none",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=1m",
            "--log-opt",
            "max-file=1",
            "--log-opt",
            "compress=false",
            "--mount",
            f"type=volume,source={volume},target={DATA}",
        ]
        if source:
            arguments.extend(["--mount", f"type=volume,source={source},target=/source,readonly"])
        if job is not None:
            arguments.extend(
                ["-i", "--entrypoint", "python", self.image_id, "-c", CONTAINER_HELPER]
            )
        else:
            arguments.append(self.image_id)
        self.command(*arguments)
        self.containers[name]["id"] = self.owned_container(name)["Id"]
        self.assert_hardening(name)
        self.persist()
        if job is None:
            self.command("start", name)
            self.wait_ready(name)
            self.phase(role + "-sandbox", self.helper(name, {"operation": "sandbox"}))
        else:
            response = self.command(
                "start", "--attach", "--interactive", name, data=json.dumps(job).encode()
            )
            result = self.decode_helper(response.stdout)
            require(self.owned_container(name)["State"]["ExitCode"] == 0, "copy_helper_failed")
            self.phase(role, result)
        return name

    def helper(self, name: str, job: dict):
        self.owned_container(name)
        response = self.command(
            "exec",
            "-i",
            "--user",
            "10001:10001",
            name,
            "python",
            "-c",
            CONTAINER_HELPER,
            data=json.dumps(job).encode(),
        )
        return self.decode_helper(response.stdout)

    def http(
        self,
        name: str,
        method: str,
        path: str,
        body=None,
        *,
        status=200,
        anonymous=False,
        csrf=True,
        origin="http://127.0.0.1:62031",
    ) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Origin": origin,
            "X-Open-Node-Client": "browser",
        }
        if not anonymous and self.cookie:
            headers["Cookie"] = "open_node_session=" + self.cookie
            if csrf:
                headers["X-CSRF-Token"] = self.csrf
        response = self.helper(
            name,
            {
                "operation": "http",
                "method": method,
                "path": path,
                "body": body,
                "headers": headers,
                "secret_markers": [SYNTHETIC_TOKEN, self.password, INVALID_MARKER],
            },
        )
        require(status is None or response["status"] == status, "unexpected_http_status")
        values = {key.lower(): value for key, value in response["headers"]}
        if path in {PUBLIC, PRIVATE} or path.startswith("/api/v1/notifications"):
            require(values.get("cache-control") == "no-store", "private_cache_policy")
            require(values.get("referrer-policy") == "no-referrer", "private_referrer_policy")
        if path in {PUBLIC, PRIVATE}:
            require(
                values.get("content-type", "").split(";", 1)[0] == "application/json",
                "branding_response_not_json",
            )
        return response

    def snapshot(self, name: str) -> dict:
        return self.helper(
            name,
            {
                "operation": "snapshot",
                "token": SYNTHETIC_TOKEN,
                "secret_markers": [
                    SYNTHETIC_TOKEN,
                    self.password,
                    SYNTHETIC_TOKEN.split(":", 1)[1],
                ],
            },
        )

    def verify_saved(self, name: str, saved: dict, notification: dict, baseline: dict) -> None:
        session = self.http(name, "GET", "/api/v1/auth/session")["body"]
        require(
            session.get("authenticated") is True
            and session.get("username") == "admin"
            and session.get("csrf_token") == self.csrf,
            "original_session_or_csrf_changed",
        )
        branding_value(
            self.http(name, "GET", PRIVATE)["body"],
            saved["site_title"],
            saved["brand_title"],
            saved["revision"],
        )
        branding_value(
            self.http(name, "GET", PUBLIC, anonymous=True)["body"],
            saved["site_title"],
            saved["brand_title"],
        )
        require(
            self.http(name, "GET", "/api/v1/notifications/settings")["body"] == notification,
            "notification_settings_changed",
        )
        require(
            self.http(name, "GET", "/healthz")["body"]["service"] == "Open Node",
            "technical_service_identity_changed",
        )
        require(self.snapshot(name) == baseline, "non_branding_business_data_changed")

    def exercise(self) -> None:
        live_volume, backup_volume, restored_volume = [
            self.create_volume(role) for role in ("live-data", "backup-data", "restored-data")
        ]
        live = self.create_container("live", live_volume)
        app, assets = (
            self.helper(live, {"operation": "source"}),
            self.helper(live, {"operation": "frontend"}),
        )
        write_json(self.output / "app-image-manifest.json", app)
        write_json(self.output / "frontend-image-manifest.json", assets)
        require(
            app == self.app_files and assets == self.frontend_files,
            "image_source_or_assets_mismatch",
        )
        spa = self.helper(live, spa_job([self.password, SYNTHETIC_TOKEN]))
        headers = {key.lower(): value for key, value in spa["headers"]}
        self.report["spa_probe"] = {
            "status": spa["status"], "content_type": headers.get("content-type"),
            "body_size": spa["body_size"], "body_sha256": spa["body_sha256"],
        }
        self.persist()
        require(
            spa["status"] == 200
            and headers.get("content-type", "").startswith("text/html")
            and spa["body_sha256"] == assets["index.html"]["sha256"]
            and spa["body_size"] == assets["index.html"]["size"],
            "branding_spa_not_exact_build",
        )
        self.phase(
            "image-full-app-main-assets-and-spa",
            {"all_bytes_equal": True, "app_files": len(app), "asset_files": len(assets)},
        )
        branding_value(
            self.http(live, "GET", PUBLIC, anonymous=True)["body"], "Open Node", "Open Node"
        )
        for method in ("GET", "PUT"):
            self.http(live, method, PRIVATE, {"secret": INVALID_MARKER}, anonymous=True, status=401)
        self.command(
            "exec",
            "-i",
            "--user",
            "10001:10001",
            live,
            "open-node-admin",
            "create",
            "--username",
            "admin",
            "--password-stdin",
            data=(self.password + "\n").encode(),
        )
        login = self.http(
            live, "POST", "/api/v1/auth/login", {"username": "admin", "password": self.password}
        )
        require(login["body"].get("authenticated") is True, "administrator_login_failed")
        cookies = SimpleCookie()
        for key, value in login["headers"]:
            if key.lower() == "set-cookie":
                cookies.load(value)
        cookie = cookies.get("open_node_session")
        require(
            cookie
            and cookie["secure"]
            and cookie["httponly"]
            and cookie["samesite"].lower() == "strict",
            "session_cookie_weakened",
        )
        self.cookie, self.csrf = cookie.value, login["body"]["csrf_token"]
        branding_value(self.http(live, "GET", PRIVATE)["body"], "Open Node", "Open Node", 0)
        for options in ({"csrf": False}, {"origin": "https://fixture-attacker.invalid"}):
            self.http(live, "PUT", PRIVATE, {"secret": INVALID_MARKER}, status=403, **options)
        branding_value(self.http(live, "GET", PRIVATE)["body"], "Open Node", "Open Node", 0)
        self.phase(
            "default-exact-projection-admin-auth-and-csrf",
            {
                "public_fields": 3,
                "default_revision": 0,
                "anonymous_status": 401,
                "csrf_origin_status": 403,
            },
        )
        initial = self.http(live, "GET", "/api/v1/notifications/settings")["body"]
        require(
            initial["revision"] == 0 and initial["enabled"] is False and not initial["has_token"],
            "sentinel_not_fresh",
        )
        notification = self.http(
            live,
            "PUT",
            "/api/v1/notifications/settings",
            {
                "expected_revision": 0,
                "enabled": False,
                "chat_id": CHAT_ID,
                "advance_days": 7,
                "timezone": "Asia/Shanghai",
                "local_time": "09:00",
                "token_action": "replace",
                "token": SYNTHETIC_TOKEN,
            },
        )["body"]
        require(
            notification["revision"] == 1
            and notification["enabled"] is False
            and notification["has_token"]
            and notification["storage_ready"],
            "sentinel_save_failed",
        )
        baseline = self.snapshot(live)
        write_json(self.output / "other-business-before.json", baseline)
        site, brand = "站" * 76 + "🌕👩\u200d💻", "<b>品牌🌙</b>" * 4
        require(len(site) == 80 and len(brand) == 40, "fixture_unicode_boundary_miscalculated")
        saved = self.http(
            live,
            "PUT",
            PRIVATE,
            {
                "expected_revision": 0,
                "site_title": " " + site + " ",
                "brand_title": " " + brand + " ",
            },
        )["body"]
        branding_value(saved, site, brand, 1)
        self.verify_saved(live, saved, notification, baseline)
        self.phase(
            "unicode-codepoint-limits-html-json-and-atomic-pair",
            {
                "site_codepoints": len(site),
                "brand_codepoints": len(brand),
                "outer_spaces_trimmed": True,
                "html_is_json_text": True,
                "all_other_business_columns_unchanged_except_session_last_seen": True,
            },
        )
        pairs = [("并发甲🌙", "原子甲"), ("并发乙🧪", "原子乙")]
        barrier = threading.Barrier(2)

        def save(pair):
            barrier.wait(timeout=10)
            return self.http(
                live,
                "PUT",
                PRIVATE,
                {
                    "expected_revision": 1,
                    "site_title": pair[0],
                    "brand_title": pair[1],
                },
                status=None,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, pairs))
        saved = cas_winner(results, pairs, 1)
        self.verify_saved(live, saved, notification, baseline)
        stale = self.http(
            live,
            "PUT",
            PRIVATE,
            {
                "expected_revision": 1,
                "site_title": "不得覆盖",
                "brand_title": "旧版本",
            },
            status=409,
        )
        branding_error(stale["body"], "branding_revision_conflict")
        invalid = self.http(
            live,
            "PUT",
            PRIVATE,
            {
                "expected_revision": 2,
                "site_title": INVALID_MARKER + "\n",
                "brand_title": "无效",
            },
            status=422,
        )
        branding_error(invalid["body"], "branding_invalid_request")
        self.verify_saved(live, saved, notification, baseline)
        self.phase(
            "real-concurrent-cas-and-fixed-non-echo-errors",
            {"statuses": [200, 409], "saved_revision": 2},
        )
        final_site = '<img src="https://fixture.invalid/x" onerror="alert(1)">🌙'
        saved = self.http(
            live,
            "PUT",
            PRIVATE,
            {
                "expected_revision": 2,
                "site_title": final_site,
                "brand_title": brand,
            },
        )["body"]
        branding_value(saved, final_site, brand, 3)
        self.verify_saved(live, saved, notification, baseline)
        self.stop(live)
        self.start(live)
        self.verify_saved(live, saved, notification, baseline)
        self.phase(
            "same-volume-restart-original-session-and-sentinel",
            {
                "original_session": True,
                "original_csrf": True,
                "revision": 3,
                "notification_still_disabled": True,
            },
        )
        self.stop(live)
        self.create_container(
            "cold-backup", backup_volume, source=live_volume, job={"operation": "copy"}
        )
        self.create_container(
            "cold-restore", restored_volume, source=backup_volume, job={"operation": "copy"}
        )
        restored = self.create_container("restored", restored_volume)
        require(
            self.containers[live]["id"] != self.containers[restored]["id"],
            "restore_reused_container",
        )
        self.verify_saved(restored, saved, notification, baseline)
        write_json(self.output / "other-business-restored.json", self.snapshot(restored))
        self.phase(
            "independent-whole-volume-cold-restore",
            {
                "original_session": True,
                "original_csrf": True,
                "all_business_tables_compared": True,
                "notification_key_decryption_verified": True,
                "saved_revision": 3,
            },
        )
        self.stop(restored)

    def cleanup(self) -> dict:
        removed_containers, removed_volumes, errors = [], [], []
        markers = [
            v.encode() for v in (SYNTHETIC_TOKEN, self.password, self.cookie, self.csrf) if v
        ]
        for name in reversed(list(self.containers)):
            try:
                if self.inspect("container", name, optional=True) is None:
                    continue
                current = self.owned_container(name)
                if current["State"]["Running"]:
                    try:
                        self.stop(name)
                    except Exception:
                        errors.append("owned_container_shutdown_not_clean")
                require(
                    not self.owned_container(name)["State"]["Running"],
                    "cleanup_container_still_live",
                )
                logs = self.command("logs", "--tail", "1000", name, check=False)
                raw = logs.stdout + logs.stderr
                if any(marker in raw for marker in markers):
                    errors.append("secret_in_container_log")
                for marker in markers:
                    raw = raw.replace(marker, b"<redacted-fixture-secret>")
                (self.output / (self.containers[name]["role"] + ".log")).write_bytes(raw)
                self.owned_container(name)
                self.command("rm", name)
                removed_containers.append(name)
            except Exception:
                errors.append("owned_container_cleanup_failed")
        for name in reversed(self.volumes):
            try:
                if self.inspect("volume", name, optional=True) is None:
                    continue
                self.owned_volume(name)
                self.command("volume", "rm", name)
                removed_volumes.append(name)
            except Exception:
                errors.append("owned_volume_cleanup_failed")
        left_containers = (
            self.command("ps", "-aq", "--filter", f"label={OWNER_LABEL}={self.owner}")
            .stdout.decode()
            .split()
        )
        left_volumes = (
            self.command("volume", "ls", "-q", "--filter", f"label={OWNER_LABEL}={self.owner}")
            .stdout.decode()
            .split()
        )
        return {
            "complete": not errors and not left_containers and not left_volumes,
            "removed_containers": removed_containers,
            "removed_volumes": removed_volumes,
            "remaining_containers": left_containers,
            "remaining_volumes": left_volumes,
            "errors": errors,
        }

    def run(self) -> bool:
        before, failure = None, None
        try:
            self.preflight()
            before = self.protected()
            write_json(self.output / "protected-before.json", before)
            self.exercise()
        except KeyboardInterrupt:
            failure = "fixture_interrupted"
        except SmokeFailure as error:
            failure = str(error)
        except Exception:
            failure = "unexpected_branding_fixture_failure"
        finally:
            try:
                self.report["cleanup"] = self.cleanup()
            except Exception:
                self.report["cleanup"] = {"complete": False, "errors": ["cleanup_incomplete"]}
            self.report["protected_unchanged"] = self.report["source_unchanged"] = False
            if before is not None:
                try:
                    after = self.protected()
                    write_json(self.output / "protected-after.json", after)
                    self.report["protected_unchanged"] = before == after
                    self.report["source_unchanged"] = (
                        self.app_files == source_manifest()
                        and self.frontend_files == infra.frontend_manifest()
                        and self.report["fixture_sha256"] == digest(Path(__file__))
                        and self.report["infrastructure_sha256"] == digest(INFRA_PATH)
                    )
                except Exception:
                    pass
            passed = (
                failure is None
                and self.report["cleanup"]["complete"]
                and (self.report["protected_unchanged"] and self.report["source_unchanged"])
            )
            self.report.update(status="passed" if passed else "failed", failure_code=failure)
            self.persist()
        return passed


def self_test() -> int:
    """No Docker invocation, app import, network or real resource creation."""
    checks = 0

    def accepts(call):
        nonlocal checks
        call()
        checks += 1

    def refuses(call):
        nonlocal checks
        try:
            call()
        except SmokeFailure:
            checks += 1
            return
        raise SmokeFailure("self_test_expected_refusal")

    for revision in ("a" * 40, "working-tree-branding-r1", "working-tree:branding-r1"):
        accepts(lambda revision=revision: validate_arguments("sha256:" + "b" * 64, revision))
    for revision in ("main", "a" * 39, "working-tree-", "a" * 41, None, True):
        refuses(lambda revision=revision: validate_arguments("fixture:local", revision))
    for image in ("--privileged", "image;command", "https://bad image", None, True):
        refuses(lambda image=image: validate_arguments(image, "a" * 40))
    defaults = {"site_title": "Open Node", "brand_title": "Open Node", "license_required": False}
    accepts(lambda: branding_value(defaults, "Open Node", "Open Node"))
    accepts(lambda: branding_value({**defaults, "revision": 0}, "Open Node", "Open Node", 0))
    for change in (
        {"revision": 0},
        {"license_required": 0},
        {"secret": "unexpected"},
        {"site_title": "Other"},
    ):
        refuses(
            lambda change=change: branding_value({**defaults, **change}, "Open Node", "Open Node")
        )
    for revision in (False, 0.0):
        refuses(
            lambda revision=revision: branding_value(
                {**defaults, "revision": revision}, "Open Node", "Open Node", 0
            )
        )
    conflict = {
        "code": "branding_revision_conflict",
        "detail": ERRORS["branding_revision_conflict"],
        "license_required": False,
    }
    accepts(lambda: branding_error(conflict, "branding_revision_conflict"))
    refuses(lambda: branding_error({**conflict, "detail": "raw SQL"}, "branding_revision_conflict"))
    pairs = [("甲", "A"), ("乙", "B")]
    result = {"site_title": "甲", "brand_title": "A", "revision": 2, "license_required": False}
    accepted = {"status": 200, "body": result}
    rejected = {"status": 409, "body": conflict}
    accepts(lambda: cas_winner([accepted, rejected], pairs, 1))
    refuses(lambda: cas_winner([accepted, accepted], pairs, 1))
    refuses(lambda: cas_winner([rejected, rejected], pairs, 1))
    refuses(lambda: cas_winner([accepted, rejected], pairs, 0))
    fixture = object.__new__(Fixture)
    fixture.owner, fixture.prefix, fixture.image_id = (
        "owner",
        "open-node-branding-smoke-test",
        "image-id",
    )
    volume = fixture.prefix + "-live-data"
    fixture.volumes = {volume: "live-data"}
    value = {
        "Name": volume,
        "Labels": {OWNER_LABEL: "owner", ROLE_LABEL: "live-data"},
        "Driver": "local",
        "Scope": "local",
        "Options": None,
    }
    fixture.inspect = lambda *_args, **_kwargs: value
    accepts(lambda: fixture.owned_volume(volume))
    refuses(lambda: fixture.owned_volume("foreign-volume"))
    for field, change in (
        ("Labels", {OWNER_LABEL: "other", ROLE_LABEL: "live-data"}),
        ("Labels", {OWNER_LABEL: "owner", ROLE_LABEL: "other"}),
        ("Driver", "nfs"),
        ("Options", {"device": "/production"}),
    ):
        previous = value[field]
        value[field] = change
        refuses(lambda: fixture.owned_volume(volume))
        value[field] = previous
    container = fixture.prefix + "-live"
    fixture.containers = {
        container: {"role": "live", "id": "container-id", "mounts": {DATA: volume}}
    }
    value = {
        "Name": "/" + container, "Id": "container-id", "Image": "image-id",
        "Config": {"Labels": {OWNER_LABEL: "owner", ROLE_LABEL: "live"}},
    }
    accepts(lambda: fixture.owned_container(container))
    refuses(lambda: fixture.owned_container("foreign-container"))
    for field, change in (
        ("Name", "/foreign-container"), ("Id", "different-container"), ("Image", "other-image"),
        ("Config", {"Labels": {OWNER_LABEL: "other", ROLE_LABEL: "live"}}),
        ("Config", {"Labels": {OWNER_LABEL: "owner", ROLE_LABEL: "other"}}),
    ):
        previous = value[field]
        value[field] = change
        refuses(lambda: fixture.owned_container(container))
        value[field] = previous
    fixture.cookie, fixture.csrf, fixture.password = (
        "fixture-cookie",
        "fixture-csrf",
        "fixture-password",
    )
    jobs = []

    def capture(_name, job):
        jobs.append(job)
        return {
            "status": 200,
            "headers": [
                ["Cache-Control", "no-store"],
                ["Referrer-Policy", "no-referrer"],
                ["Content-Type", "application/json"],
            ],
            "body": defaults,
        }

    fixture.helper = capture
    fixture.http("private", "GET", PUBLIC, anonymous=True)
    require(
        "Cookie" not in jobs[-1]["headers"] and "X-CSRF-Token" not in jobs[-1]["headers"],
        "anonymous_job_has_credentials",
    )
    checks += 1
    fixture.http("private", "PUT", PRIVATE, {"site_title": "input"}, csrf=False)
    require(
        jobs[-1]["headers"]["Cookie"] == "open_node_session=fixture-cookie"
        and "X-CSRF-Token" not in jobs[-1]["headers"],
        "csrf_negative_job_incorrect",
    )
    checks += 1
    commands = []

    def capture_command(*arguments, **options):
        commands.append((arguments, options))
        return SimpleNamespace(stdout=b'{"result":{"ok":true}}')

    fixture.command = capture_command
    credential_job = {"body": {"token": SYNTHETIC_TOKEN, "password": fixture.password},
                      "headers": {"Cookie": fixture.cookie, "X-CSRF-Token": fixture.csrf}}
    Fixture.helper(fixture, container, credential_job)
    arguments, options = commands[-1]
    require(all(secret not in " ".join(arguments) for secret in
                (SYNTHETIC_TOKEN, fixture.password, fixture.cookie, fixture.csrf)),
            "secret_entered_docker_argv")
    require(json.loads(options["data"]) == credential_job and len(commands) == 1,
            "secret_job_not_exact_stdin")
    checks += 2
    accepts(lambda: compile(CONTAINER_HELPER, "branding-container-helper", "exec"))
    require(spa_job(["fixture-marker"]) == {
        "operation": "spa", "headers": {"Accept": "text/html"},
        "secret_markers": ["fixture-marker"],
    }, "spa_request_missing_explicit_html_accept")
    checks += 1
    tree = ast.parse(CONTAINER_HELPER)
    require(
        not any(
            isinstance(node, ast.Attribute) and node.attr in {"system", "popen"}
            for node in ast.walk(tree)
        ),
        "container_helper_shell_execution",
    )
    require(
        SYNTHETIC_TOKEN not in CONTAINER_HELPER and fixture.password not in CONTAINER_HELPER,
        "credential_in_helper_argv",
    )
    checks += 2
    print(
        json.dumps(
            {
                "status": "passed",
                "self_test_checks": checks,
                "docker_invocations": 0,
                "integration_verified": False,
            }
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument(
        "--revision", help="Full 40-hex Git revision or explicit working-tree-... label"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    require(sys.platform == "linux", "run_only_on_isolated_linux_vps")
    if args.self_test:
        require(
            args.image is None and args.revision is None and args.output is None,
            "self_test_cannot_start_docker_gate",
        )
        return self_test()
    validate_arguments(args.image, args.revision)
    require(args.output is not None, "evidence_output_required")
    output = args.output.absolute()
    require(
        len(output.parts) >= 3
        and not output.is_relative_to(ROOT)
        and not any(output.is_relative_to(root) for root in infra.PROTECTED_ROOTS)
        and not any(path.is_symlink() for path in (output, *output.parents)),
        "unsafe_evidence_path",
    )
    if output.exists():
        require(output.is_dir() and not any(output.iterdir()), "evidence_directory_not_empty")
    else:
        output.mkdir(mode=0o700)
    output.chmod(0o700)
    os.umask(0o077)

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    fixture = Fixture(args.image, args.revision, output)
    passed = fixture.run()
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "feature": "site-branding",
                "report": str(output / "report.json"),
                "telegram_acceptance_verified": False,
            }
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as error:
        print(
            json.dumps({"status": "failed", "feature": "site-branding", "failure_code": str(error)})
        )
        raise SystemExit(1) from None
