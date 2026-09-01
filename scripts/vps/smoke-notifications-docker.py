"""Offline Docker gate for notification persistence and independent secret storage.

Run only on the disposable VPS with a newly built, explicitly identified image.
The supplied image is never built, pulled, tagged or removed. Every container
uses UID/GID 10001, a read-only root, no capabilities, no-new-privileges and
``--network none``. Only random label-owned volumes are mounted; production and
the shared candidate are fingerprinted, never mounted or changed.

HTTP runs through docker-exec stdin on container loopback. Synthetic bot tokens,
passwords, cookies, CSRF values and JSON bodies never enter argv, environment or
evidence logs. An offline queued/failed attempt is NOT Telegram acceptance.

The intentional missing/wrong-key cases affect a restored disposable volume,
not the original or backup. All owned containers and volumes are checked before
cleanup; evidence, including a failed phase, remains in --output.
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
import stat
import subprocess
import sys
import time
from http.cookies import SimpleCookie
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
OWNER_LABEL = "io.open-node.smoke.notifications.owner"
ROLE_LABEL = "io.open-node.smoke.notifications.role"
IMAGE_SOURCE = "https://github.com/FengYuchen1314/open-node"
DATA = "/var/lib/open-node"
SYNTHETIC_TOKEN = "900000001:FixtureOnlyTelegramTokenNeverARealCredential_123"
CHAT_ID = "-1001234567890"
APP_SOURCES = (
    "main.py",
    "api/router.py",
    "api/routes/notifications.py",
    "core/config.py",
    "domain/notifications.py",
    "services/notifications.py",
    "services/notification_worker.py",
    "services/telegram_transport.py",
)
PROTECTED_ROOTS = (Path("/opt/open-node"), Path("/opt/open-node/mmwx-parity-candidate"))

# This constant contains code, never credentials. Actual jobs go through stdin.
# The helper cannot choose an HTTP host/port, arbitrary filesystem root or shell.
CONTAINER_HELPER = r'''
import ast
import hashlib
import http.client
import importlib.metadata
import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import stat
import sys
from pathlib import Path

DATA = Path("/var/lib/open-node")
SOURCE = Path("/source")
KEY = DATA / "notifications/telegram.key"
MARKER = DATA / "notifications/telegram.initialized"
PURPOSE = "open-node.notifications.telegram.v1"


class FixtureFailure(Exception):
    pass


def check(condition, code):
    if not condition:
        raise FixtureFailure(code)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            value.update(block)
    return value.hexdigest()


def private(path, *, directory=False):
    check(not any(item.is_symlink() for item in (path, *path.parents)), "symlink_refused")
    mode = path.lstat()
    check(mode.st_uid == 10001 and mode.st_gid == 10001, "private_owner_mismatch")
    check(stat.S_IMODE(mode.st_mode) == (0o700 if directory else 0o600), "private_mode_mismatch")
    check(stat.S_ISDIR(mode.st_mode) if directory else stat.S_ISREG(mode.st_mode), "private_type")
    if not directory:
        check(mode.st_nlink == 1, "private_hardlink_refused")
    return {"uid": mode.st_uid, "gid": mode.st_gid, "mode": oct(stat.S_IMODE(mode.st_mode))}


def sandbox():
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                  if ":" in line)
    check(os.geteuid() == 10001 and os.getegid() == 10001, "effective_user_mismatch")
    check(int(fields["CapEff"].strip(), 16) == 0, "effective_capabilities_present")
    check(fields["NoNewPrivs"].strip() == "1", "no_new_privileges_missing")
    check(socket.if_nameindex() == [(1, "lo")], "non_loopback_interface_present")
    check(bool(os.statvfs("/").f_flag & os.ST_RDONLY), "root_filesystem_writable")
    return {"uid": os.geteuid(), "gid": os.getegid(), "capabilities": 0,
            "no_new_privileges": True, "interfaces": socket.if_nameindex(),
            "root_readonly": True, "netns": os.readlink("/proc/self/ns/net")}


def manifest(root):
    check(root in (DATA, SOURCE), "unexpected_volume_root")
    private(root, directory=True)
    values = {}
    total = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat()
        check(not stat.S_ISLNK(mode.st_mode), "backup_symlink_refused")
        check(stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode), "backup_special_file")
        check(mode.st_uid == 10001 and mode.st_gid == 10001, "backup_owner_mismatch")
        if stat.S_ISREG(mode.st_mode):
            check(mode.st_nlink == 1, "backup_hardlink_refused")
            total += mode.st_size
        check(total <= 64 * 1024 * 1024 and len(values) < 512, "backup_size_limit")
        values[str(path.relative_to(root))] = {
            "mode": oct(stat.S_IMODE(mode.st_mode)), "uid": mode.st_uid, "gid": mode.st_gid,
            "size": mode.st_size if stat.S_ISREG(mode.st_mode) else None,
            "sha256": digest(path) if stat.S_ISREG(mode.st_mode) else None,
        }
    return values


def frontend_assets():
    root = Path("/opt/open-node/frontend")
    check(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents)),
          "frontend_root_invalid")
    values = {}
    total = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat()
        check(stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode), "frontend_special_file")
        if stat.S_ISDIR(mode.st_mode):
            continue
        total += mode.st_size
        check(total <= 64 * 1024 * 1024 and len(values) < 512, "frontend_size_limit")
        values[path.relative_to(root).as_posix()] = {"size": mode.st_size, "sha256": digest(path)}
    check("index.html" in values and len(values) > 1, "frontend_build_missing")
    return values


def no_plaintext(markers):
    needles = [value.encode() for value in markers]
    scanned = 0
    for path in DATA.rglob("*"):
        check(not path.is_symlink(), "state_symlink_refused")
        if not path.is_file():
            continue
        scanned += path.stat().st_size
        check(scanned <= 64 * 1024 * 1024, "state_scan_size_limit")
        tail = b""
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                data = tail + block
                check(all(needle not in data for needle in needles), "plaintext_persisted")
                tail = data[-max((len(needle) for needle in needles), default=1):]


def state(job):
    private(DATA, directory=True)
    notification_dir = DATA / "notifications"
    files = {}
    for name, path in (("directory", notification_dir), ("key", KEY), ("marker", MARKER)):
        if path.exists():
            files[name] = {"exists": True, **private(path, directory=name == "directory")}
            if name != "directory":
                files[name]["sha256"] = digest(path)
        else:
            files[name] = {"exists": False}
    connection = sqlite3.connect("file:/var/lib/open-node/open-node.db?mode=ro", uri=True)
    try:
        # Hold a brief read transaction while scanning, so a rollback journal
        # cannot disappear midway through the plaintext assertion.
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT revision,enabled,chat_id,token_ciphertext,key_fingerprint "
            "FROM notification_settings WHERE id=1"
        ).fetchone()
        check(row is not None, "settings_row_missing")
        counts = {table: connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                  for table in ("notification_requests", "notification_deliveries",
                                "notification_attempts", "operator_sessions")}
        accepted = connection.execute(
            "SELECT count(*) FROM notification_deliveries "
            "WHERE accepted_once=1 OR message_id IS NOT NULL"
        ).fetchone()[0]
        check(accepted == 0, "offline_fixture_reported_telegram_acceptance")
        text = row[3]
        if job.get("decrypt"):
            from cryptography.fernet import Fernet
            raw_key = KEY.read_bytes()
            check(len(raw_key) <= 128, "key_size_limit")
            expected = hashlib.sha256(PURPOSE.encode() + b"\x00" + raw_key).hexdigest()
            check(row[4] == expected, "key_fingerprint_mismatch")
            decoded = json.loads(Fernet(raw_key).decrypt(text.encode()))
            check(decoded == {"purpose": PURPOSE, "token": job["token"]}, "ciphertext_roundtrip")
            marker = json.loads(MARKER.read_bytes())
            check(marker == {"purpose": PURPOSE, "key_fingerprint": expected}, "marker_mismatch")
        # Includes SQLite WAL/journal and vault files, not only the SQL column.
        no_plaintext(job.get("secret_markers", []))
    finally:
        connection.close()
    return {"files": files, "counts": counts, "accepted": accepted,
            "settings": {"revision": row[0], "enabled": bool(row[1]), "chat_id": row[2],
                         "has_ciphertext": text is not None, "key_fingerprint": row[4],
                         "ciphertext_sha256": hashlib.sha256(text.encode()).hexdigest()
                         if text else None}}


def main(job):
    proof = sandbox()
    operation = job["operation"]
    if operation == "sandbox":
        return proof
    if operation == "source":
        root = Path(importlib.util.find_spec("open_node").origin).parent
        result = {}
        for name in job["files"]:
            path = root / name
            check(path.resolve().is_relative_to(root.resolve()) and path.is_file(), "source_path")
            result[name] = digest(path)
        return result
    if operation == "runtime":
        server = Path(importlib.util.find_spec("uvicorn").origin).with_name("server.py")
        check(server.is_file() and server.stat().st_size <= 131072, "uvicorn_source_invalid")
        text = server.read_text()
        module = ast.parse(text)
        server_class = next(node for node in module.body
                            if isinstance(node, ast.ClassDef) and node.name == "Server")
        capture = next(node for node in server_class.body
                       if isinstance(node, ast.FunctionDef) and node.name == "capture_signals")
        reraises = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and isinstance(node.func.value, ast.Name) and node.func.value.id == "signal"
                       and node.func.attr == "raise_signal" for node in ast.walk(capture))
        captured_source = ast.get_source_segment(text, capture)
        return {"uvicorn_version": importlib.metadata.version("uvicorn"),
                "server_sha256": digest(server), "graceful_signal_reraise": reraises,
                "capture_signals_source": captured_source,
                "capture_signals_sha256": hashlib.sha256(captured_source.encode()).hexdigest()}
    if operation == "frontend":
        return frontend_assets()
    if operation == "spa":
        connection = http.client.HTTPConnection("127.0.0.1", 62031, timeout=5)
        try:
            connection.request("GET", "/notifications", headers={"Accept": "text/html"})
            response = connection.getresponse()
            data = response.read(131073)
            check(len(data) <= 131072, "spa_response_limit")
            check(all(value.encode() not in data for value in job.get("secret_markers", [])),
                  "secret_echoed_in_spa")
            return {"status": response.status, "content_type": response.getheader("Content-Type"),
                    "body_size": len(data), "body_sha256": hashlib.sha256(data).hexdigest()}
        finally:
            connection.close()
    if operation == "http":
        check(job["path"].startswith(("/api/v1/", "/healthz")), "http_path")
        connection = http.client.HTTPConnection("127.0.0.1", 62031, timeout=5)
        try:
            body = None if job.get("body") is None else json.dumps(job["body"]).encode()
            check(body is None or len(body) <= 8192, "http_request_limit")
            connection.request(job["method"], job["path"], body=body,
                               headers=job.get("headers", {}))
            response = connection.getresponse()
            data = response.read(131073)
            check(len(data) <= 131072, "http_response_limit")
            check(all(value.encode() not in data for value in job.get("secret_markers", [])),
                  "secret_echoed_in_http")
            return {"status": response.status, "headers": response.getheaders(),
                    "body": json.loads(data) if data else None}
        finally:
            connection.close()
    if operation == "state":
        return state(job)
    if operation == "copy":
        before = manifest(SOURCE)
        check(not list(DATA.iterdir()), "restore_destination_not_empty")
        check("open-node.db" in before and "notifications/telegram.key" in before
              and "notifications/telegram.initialized" in before, "incomplete_backup")
        shutil.copytree(SOURCE, DATA, dirs_exist_ok=True, copy_function=shutil.copy2)
        check(manifest(SOURCE) == before, "readonly_backup_changed")
        after = manifest(DATA)
        check(before == after, "cold_copy_mismatch")
        return {"source": before, "destination": after, "equal": True}
    if operation == "remove_key":
        private(KEY)
        check(digest(KEY) == job["expected_key_hash"], "key_changed_before_removal")
        KEY.unlink()
        check(not KEY.exists(), "missing_key_setup_failed")
        return {"key_absent": True}
    if operation == "wrong_key":
        from cryptography.fernet import Fernet
        private(KEY.parent, directory=True)
        check(not KEY.exists() and not KEY.is_symlink(), "wrong_key_target_present")
        descriptor = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(Fernet.generate_key())
            stream.flush()
            os.fsync(stream.fileno())
        private(KEY)
        check(digest(KEY) != job["original_key_hash"], "wrong_key_not_distinct")
        return {"key_sha256": digest(KEY)}
    if operation == "restore_key":
        original = SOURCE / "notifications/telegram.key"
        private(original)
        private(KEY)
        check(digest(original) == job["original_key_hash"], "backup_key_changed")
        check(digest(KEY) == job["expected_key_hash"], "wrong_key_changed")
        temporary = KEY.with_name("telegram.key.fixture-restore")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, KEY)
        descriptor = os.open(KEY.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        private(KEY)
        check(digest(KEY) == job["original_key_hash"], "original_key_restore_failed")
        return {"key_sha256": digest(KEY)}
    raise FixtureFailure("unknown_fixture_operation")


try:
    raw = sys.stdin.buffer.read(65537)
    check(len(raw) <= 65536, "job_size_limit")
    print(json.dumps({"result": main(json.loads(raw))}, sort_keys=True))
except FixtureFailure as error:
    print(json.dumps({"error": str(error)}))
    raise SystemExit(1) from None
except Exception:
    print(json.dumps({"error": "container_helper_failed"}))
    raise SystemExit(1) from None
'''


class SmokeFailure(Exception):
    """Only fixed fixture codes are suitable for the persisted failure report."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise SmokeFailure(code)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            value.update(block)
    return value.hexdigest()


def frontend_manifest() -> dict:
    root = ROOT / "frontend/dist"
    require(root.is_dir() and not any(path.is_symlink() for path in (root, *root.parents)),
            "source_frontend_root_invalid")
    values = {}
    total = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat()
        require(stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode),
                "source_frontend_special_file")
        if stat.S_ISDIR(mode.st_mode):
            continue
        total += mode.st_size
        require(total <= 64 * 1024 * 1024 and len(values) < 512, "source_frontend_size_limit")
        values[path.relative_to(root).as_posix()] = {"size": mode.st_size, "sha256": digest(path)}
    require("index.html" in values and len(values) > 1, "source_frontend_build_missing")
    return values


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


class Fixture:
    def __init__(self, image: str, revision: str, output: Path):
        self.image_argument = image
        self.revision = revision
        self.output = output
        self.owner = secrets.token_hex(16)
        self.prefix = "open-node-notify-smoke-" + self.owner[:16]
        self.docker = shutil.which("docker")
        require(self.docker is not None, "docker_missing")
        self.password = secrets.token_urlsafe(32)
        self.cookie = ""
        self.csrf = ""
        self.image_id = ""
        self.frontend_files: dict = {}
        self.volumes: list[str] = []
        self.containers: dict[str, dict] = {}
        self.report = {
            "status": "running", "owner": self.owner, "phases": [],
            "containers": self.containers, "volumes": self.volumes,
            "telegram_contact": False, "telegram_acceptance_verified": False,
            "network": "none; HTTP fixture uses container loopback via docker exec stdin",
            "revision": revision, "fixture_sha256": digest(Path(__file__)),
        }
        self.persist()

    def persist(self) -> None:
        write_json(self.output / "report.json", self.report)

    def phase(self, name: str, evidence: object) -> None:
        self.report["phases"].append({"name": name, "status": "passed", "evidence": evidence})
        self.persist()

    def command(self, *arguments: str, data: bytes | None = None, timeout=45, check=True):
        try:
            result = subprocess.run(
                [self.docker, *arguments], input=data, capture_output=True,
                timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SmokeFailure("docker_command_unavailable_or_timed_out") from None
        if check and result.returncode:
            # Never print argv, input, stdout/stderr or a CalledProcessError.
            raise SmokeFailure("docker_command_failed")
        return result

    def inspect(self, kind: str, name: str, *, optional=False):
        result = self.command(kind, "inspect", name, check=False)
        if optional and result.returncode:
            return None
        require(result.returncode == 0, "docker_inspect_failed")
        values = json.loads(result.stdout)
        require(isinstance(values, list) and len(values) == 1, "docker_inspect_ambiguous")
        return values[0]

    def protected(self) -> dict:
        product = self.inspect("container", "open-node-open-node-1", optional=True)
        production = None if product is None else {
            "id": product["Id"], "image": product["Image"],
            "started_at": product["State"]["StartedAt"],
            "status": product["State"]["Status"], "restart_count": product["RestartCount"],
        }
        manifests = {}
        for root in PROTECTED_ROOTS:
            files = []
            for suffix in ("backend/app", "frontend/src", "frontend/dist", "frontend/probe-dist"):
                directory = root / suffix
                if directory.is_dir():
                    files.extend(path for path in directory.rglob("*") if path.is_file())
            for name in ("Dockerfile", "backend/pyproject.toml", "frontend/package-lock.json"):
                if (root / name).is_file():
                    files.append(root / name)
            manifests[str(root)] = {
                str(path.relative_to(root)): digest(path) for path in sorted(set(files))
                if "__pycache__" not in path.parts and "node_modules" not in path.parts
            }
        return {"production": production, "protected_files": manifests,
                "resolv_conf_sha256": digest(Path("/etc/resolv.conf"))}

    def preflight(self) -> None:
        details = self.inspect("image", self.image_argument)
        self.image_id = details["Id"]
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id), "image_id_not_immutable")
        labels = details["Config"].get("Labels") or {}
        require(labels.get("org.opencontainers.image.revision") == self.revision,
                "image_revision_mismatch")
        require(labels.get("org.opencontainers.image.source") == IMAGE_SOURCE,
                "image_source_mismatch")
        require(details["Config"].get("User") == "10001:10001", "image_not_nonroot")
        require(details.get("Os") == "linux", "linux_image_required")
        require(set(details["Config"].get("Volumes") or {}) == {DATA},
                "image_has_unowned_volume_targets")
        require(details["Config"].get("StopSignal") in (None, "", "SIGTERM", "15"),
                "image_stop_signal_not_sigterm")
        self.report["image"] = {
            "id": self.image_id, "revision": self.revision,
            "source": labels["org.opencontainers.image.source"],
            "created": details["Created"], "user": details["Config"]["User"],
        }
        self.report["source_sha256"] = {
            name: digest(ROOT / "backend/app/open_node" / name) for name in APP_SOURCES
        }
        self.frontend_files = frontend_manifest()
        manifest_path = self.output / "frontend-source-manifest.json"
        write_json(manifest_path, self.frontend_files)
        self.report["frontend_assets"] = {
            "source_directory": str(ROOT / "frontend/dist"),
            "image_directory": "/opt/open-node/frontend", "image_id": self.image_id,
            "file_count": len(self.frontend_files),
            "total_bytes": sum(value["size"] for value in self.frontend_files.values()),
            "manifest_file": manifest_path.name, "manifest_sha256": digest(manifest_path),
            "index_sha256": self.frontend_files["index.html"]["sha256"],
        }
        require(not self.command("ps", "-aq", "--filter", f"label={OWNER_LABEL}={self.owner}")
                .stdout.strip(), "owner_container_collision")
        require(not self.command("volume", "ls", "-q", "--filter",
                                 f"label={OWNER_LABEL}={self.owner}").stdout.strip(),
                "owner_volume_collision")
        self.persist()

    def owned_volume(self, name: str):
        require(name in self.volumes, "volume_not_recorded")
        value = self.inspect("volume", name)
        require(value["Name"] == name
                and (value.get("Labels") or {}).get(OWNER_LABEL) == self.owner,
                "volume_owner_mismatch")
        require(value["Driver"] == "local" and value["Scope"] == "local"
                and value.get("Options") in (None, {}), "volume_driver_not_private_local")
        return value

    def create_volume(self, role: str) -> str:
        name = f"{self.prefix}-{role}"
        require(self.inspect("volume", name, optional=True) is None, "volume_name_collision")
        self.volumes.append(name)
        self.persist()
        result = self.command("volume", "create", "--label", f"{OWNER_LABEL}={self.owner}",
                              "--label", f"{ROLE_LABEL}={role}", name)
        require(result.stdout.decode().strip() == name, "created_volume_name_mismatch")
        self.owned_volume(name)
        return name

    def owned_container(self, name: str):
        require(name in self.containers, "container_not_recorded")
        value = self.inspect("container", name)
        labels = value["Config"].get("Labels") or {}
        require(value["Name"] == "/" + name and labels.get(OWNER_LABEL) == self.owner,
                "container_owner_mismatch")
        expected = self.containers[name].get("id")
        require(expected is None or value["Id"] == expected, "container_id_changed")
        require(value["Image"] == self.image_id, "container_image_changed")
        return value

    def assert_hardening(self, name: str) -> dict:
        value = self.owned_container(name)
        host = value["HostConfig"]
        require(value["Config"]["User"] == "10001:10001", "container_user_changed")
        require(host["ReadonlyRootfs"] is True and not host["Privileged"], "rootfs_or_privileged")
        require(host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"), "capabilities_changed")
        require("no-new-privileges:true" in (host.get("SecurityOpt") or []), "no_new_privileges")
        require(host["NetworkMode"] == "none" and not host.get("PortBindings"), "network_exposed")
        require(not host.get("Binds"), "host_bind_mount_refused")
        mounts = {mount["Destination"]: mount for mount in value["Mounts"]
                  if mount["Type"] == "volume"}
        expected = self.containers[name]["mounts"]
        require(set(mounts) == set(expected), "unexpected_named_or_anonymous_volume")
        for destination, volume in expected.items():
            self.owned_volume(volume)
            require(mounts[destination]["Name"] == volume, "mounted_volume_mismatch")
            require(mounts[destination]["RW"] is (destination != "/source"), "volume_access_mode")
        require(all(mount["Type"] in {"volume", "tmpfs"} for mount in value["Mounts"]),
                "unexpected_mount_type")
        return {"id": value["Id"], "image": value["Image"], "user": "10001:10001",
                "read_only": True, "cap_drop": ["ALL"], "no_new_privileges": True,
                "network": "none", "mounts": expected}

    def create_container(self, role: str, volume: str, *, source: str | None = None,
                         helper_job: dict | None = None) -> str:
        self.owned_volume(volume)
        if source is not None:
            self.owned_volume(source)
            require(source != volume, "source_equals_destination_volume")
        name = f"{self.prefix}-{role}"
        require(self.inspect("container", name, optional=True) is None, "container_name_collision")
        mounts = {DATA: volume}
        if source:
            mounts["/source"] = source
        self.containers[name] = {"role": role, "mounts": mounts}
        self.persist()
        arguments = [
            "create", "--pull", "never", "--name", name, "--label", f"{OWNER_LABEL}={self.owner}",
            "--label", f"{ROLE_LABEL}={role}", "--user", "10001:10001", "--init",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--network", "none", "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--log-driver", "local", "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
            "--log-opt", "compress=false",
            "--mount", f"type=volume,source={volume},target={DATA}",
        ]
        if source:
            arguments.extend(["--mount", f"type=volume,source={source},target=/source,readonly"])
        if helper_job is not None:
            arguments.extend(["-i", "--entrypoint", "python", self.image_id,
                              "-c", CONTAINER_HELPER])
        else:
            arguments.append(self.image_id)
        self.command(*arguments)
        value = self.owned_container(name)
        self.containers[name]["id"] = value["Id"]
        self.assert_hardening(name)
        self.persist()
        if helper_job is not None:
            response = self.command("start", "--attach", "--interactive", name,
                                    data=json.dumps(helper_job).encode())
            decoded = self.decode_helper(response.stdout)
            require(self.owned_container(name)["State"]["ExitCode"] == 0, "helper_exit_failed")
            self.phase(role, decoded)
        else:
            self.command("start", name)
            self.wait_ready(name)
            self.phase(role + "-sandbox", self.helper(name, {"operation": "sandbox"}))
        return name

    @staticmethod
    def decode_helper(data: bytes):
        require(len(data) <= 2 * 1024 * 1024, "helper_output_limit")
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise SmokeFailure("helper_output_invalid") from None
        require(isinstance(value, dict) and set(value) == {"result"}, "helper_operation_failed")
        return value["result"]

    def helper(self, name: str, job: dict):
        self.owned_container(name)
        response = self.command("exec", "-i", "--user", "10001:10001", name, "python", "-c",
                                CONTAINER_HELPER, data=json.dumps(job).encode())
        return self.decode_helper(response.stdout)

    def http(self, name: str, method: str, path: str, body=None, *, status=200):
        headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:62031",
                   "X-Open-Node-Client": "browser"}
        if self.cookie:
            headers.update({"Cookie": "open_node_session=" + self.cookie,
                            "X-CSRF-Token": self.csrf})
        result = self.helper(name, {"operation": "http", "method": method, "path": path,
                                   "body": body, "headers": headers,
                                   "secret_markers": [SYNTHETIC_TOKEN, self.password]})
        require(result["status"] == status, "unexpected_http_status")
        values = {key.lower(): value for key, value in result["headers"]}
        if path.startswith("/api/v1/notifications"):
            require(values.get("cache-control") == "no-store", "notification_cache_policy")
            require(values.get("referrer-policy") == "no-referrer", "notification_referrer_policy")
            require(result["body"].get("license_required") is False, "unexpected_license_gate")
        return result

    def wait_ready(self, name: str) -> None:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            require(self.owned_container(name)["State"]["Running"],
                    "container_stopped_before_ready")
            try:
                self.http(name, "GET", "/healthz")
                return
            except SmokeFailure:
                time.sleep(0.25)
        raise SmokeFailure("application_not_ready")

    def state(self, name: str, *, decrypt=False):
        return self.helper(name, {"operation": "state", "decrypt": decrypt,
                                  "token": SYNTHETIC_TOKEN,
                                  "secret_markers": [SYNTHETIC_TOKEN, self.password,
                                                     SYNTHETIC_TOKEN.split(":", 1)[1]]})

    def settings(self, name: str):
        return self.http(name, "GET", "/api/v1/notifications/settings")["body"]

    def update(self, name: str, revision: int, *, enabled: bool, replace=False, status=200):
        payload = {"expected_revision": revision, "enabled": enabled, "chat_id": CHAT_ID,
                   "advance_days": 7, "timezone": "Asia/Shanghai", "local_time": "09:00",
                   "token_action": "replace" if replace else "keep"}
        if replace:
            payload["token"] = SYNTHETIC_TOKEN
        response = self.http(name, "PUT", "/api/v1/notifications/settings", payload, status=status)
        return response["body"]

    def verify_identity(self, name: str, request_id: str, delivery_id: str) -> None:
        session = self.http(name, "GET", "/api/v1/auth/session")["body"]
        require(session.get("authenticated") is True and session.get("username") == "admin",
                "original_session_not_preserved")
        require(session.get("csrf_token") == self.csrf, "original_csrf_not_preserved")
        receipt = self.http(name, "GET", "/api/v1/notifications/requests/" + request_id)["body"]
        require(receipt["id"] == delivery_id and receipt["request_id"] == request_id,
                "request_reconciliation_lost")
        require(receipt["state"] != "accepted" and receipt["message_id"] is None,
                "offline_delivery_misrepresented")

    def stop(self, name: str) -> None:
        before = self.owned_container(name)
        require(before["State"]["Running"], "container_not_running_before_stop")
        started_at = before["State"]["StartedAt"]
        started = time.monotonic()
        self.command("stop", "--time", "30", name, timeout=40)
        elapsed = time.monotonic() - started
        value = self.owned_container(name)
        state = value["State"]
        logs = self.command("logs", "--since", started_at, "--tail", "1000", name)
        raw = logs.stdout + logs.stderr
        require(len(raw) <= 2 * 1024 * 1024, "shutdown_log_size_limit")
        started_pids = re.findall(rb"(?m)^INFO:\s+Started server process \[(\d+)\]\r?$", raw)
        finished_pids = re.findall(rb"(?m)^INFO:\s+Finished server process \[(\d+)\]\r?$", raw)
        completed = (
            len(started_pids) == 1 and finished_pids == started_pids
            and raw.count(b"Application shutdown complete.") == 1
            and b"Application shutdown failed" not in raw
            and b"timeout graceful shutdown exceeded" not in raw
        )
        proof = {
            "container": name, "exit_code": state["ExitCode"], "running": state["Running"],
            "oom_killed": state["OOMKilled"], "started_at": state["StartedAt"],
            "finished_at": state["FinishedAt"], "elapsed_seconds": round(elapsed, 6),
            "grace_budget_seconds": 30, "requested_signal": "SIGTERM",
            "sigterm_exit": state["ExitCode"] == 143,
            "current_start_shutdown_complete": completed,
            "log_window_started_at": started_at, "log_sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.report.setdefault("shutdowns", []).append(proof)
        self.persist()
        require(not state["Running"] and state["Status"] == "exited"
                and state["StartedAt"] == started_at and not state["OOMKilled"]
                and not state["Error"] and state["ExitCode"] in (0, 143)
                and elapsed < 30 and completed, "shutdown_not_clean")

    def start(self, name: str) -> None:
        self.assert_hardening(name)
        self.command("start", name)
        self.wait_ready(name)

    def stable_cipher(self, state: dict, baseline: dict, *, key=True) -> None:
        for field in ("ciphertext_sha256", "key_fingerprint"):
            require(state["settings"][field] == baseline["settings"][field], "saved_cipher_changed")
        require(state["files"]["marker"] == baseline["files"]["marker"],
                "initialized_fence_changed")
        if key:
            require(state["files"]["key"] == baseline["files"]["key"], "original_key_changed")
        require(state["counts"]["notification_requests"] == 1, "request_rows_duplicated")
        require(state["counts"]["notification_deliveries"] == 1, "delivery_rows_duplicated")

    def blocked_key(self, name: str, revision: int, error: str, baseline: dict, *, missing: bool):
        current = self.settings(name)
        require(current["has_token"] and not current["storage_ready"]
                and current["storage_error"] == error, "key_failure_not_reported")
        response = self.http(name, "POST", "/api/v1/notifications/test",
                             {"expected_revision": revision, "request_id": str(uuid4())},
                             status=503)
        require(response["body"]["code"] == error, "broken_key_did_not_block_send")
        response = self.update(name, revision, enabled=False, replace=True, status=503)
        require(response["code"] == error, "broken_key_permitted_rekey")
        response = self.update(name, revision, enabled=True, status=503)
        require(response["code"] == error, "broken_key_permitted_enable")
        clear = {"expected_revision": revision, "enabled": False, "chat_id": CHAT_ID,
                 "advance_days": 7, "timezone": "Asia/Shanghai", "local_time": "09:00",
                 "token_action": "clear"}
        response = self.http(name, "PUT", "/api/v1/notifications/settings", clear, status=503)
        require(response["body"]["code"] == error, "broken_key_permitted_clear")
        self.http(name, "POST", "/api/v1/notifications/preview", {"expected_revision": revision})
        state = self.state(name)
        self.stable_cipher(state, baseline, key=False)
        require(state["files"]["key"]["exists"] is not missing, "missing_key_recreated")
        require(state["settings"]["revision"] == revision, "failed_request_changed_revision")
        return state

    def exercise(self) -> None:
        live_volume = self.create_volume("live-data")
        backup_volume = self.create_volume("backup-data")
        restored_volume = self.create_volume("restored-data")
        live = self.create_container("live", live_volume)
        require(self.helper(live, {"operation": "source", "files": list(APP_SOURCES)})
                == self.report["source_sha256"], "image_does_not_match_current_source")
        runtime = self.helper(live, {"operation": "runtime"})
        write_json(self.output / "image-runtime.json", runtime)
        require(runtime["graceful_signal_reraise"] is True, "uvicorn_signal_contract_changed")
        self.report["image_runtime"] = {
            name: value for name, value in runtime.items() if name != "capture_signals_source"
        }
        image_assets = self.helper(live, {"operation": "frontend"})
        write_json(self.output / "frontend-image-manifest.json", image_assets)
        require(image_assets == self.frontend_files, "image_does_not_match_frontend_assets")
        spa = self.helper(live, {"operation": "spa",
                                 "secret_markers": [SYNTHETIC_TOKEN, self.password]})
        require(spa["status"] == 200
                and isinstance(spa["content_type"], str)
                and spa["content_type"].split(";", 1)[0].strip().lower() == "text/html",
                "notifications_spa_not_served")
        require(spa["body_sha256"] == self.frontend_files["index.html"]["sha256"]
                and spa["body_size"] == self.frontend_files["index.html"]["size"],
                "notifications_spa_index_mismatch")
        self.phase("image-frontend-assets-and-notifications-spa", {
            **self.report["frontend_assets"], "all_file_bytes_match": True, "spa": spa,
        })
        self.command("exec", "-i", "--user", "10001:10001", live, "open-node-admin", "create",
                     "--username", "admin", "--password-stdin",
                     data=(self.password + "\n").encode())
        result = self.http(live, "POST", "/api/v1/auth/login",
                           {"username": "admin", "password": self.password})
        require(result["body"].get("authenticated") is True, "administrator_login_failed")
        cookies = SimpleCookie()
        for name, value in result["headers"]:
            if name.lower() == "set-cookie":
                cookies.load(value)
        cookie = cookies.get("open_node_session")
        require(cookie and cookie["secure"] and cookie["httponly"]
                and cookie["samesite"].lower() == "strict", "session_cookie_weakened")
        self.cookie = cookie.value
        self.csrf = result["body"]["csrf_token"]
        defaults = self.settings(live)
        require(defaults["revision"] == 0 and defaults["enabled"] is False
                and defaults["has_token"] is False, "notifications_not_disabled_by_default")
        self.http(live, "POST", "/api/v1/notifications/preview", {"expected_revision": 0})
        fresh = self.state(live)
        require(not fresh["files"]["key"]["exists"] and not fresh["files"]["marker"]["exists"],
                "read_or_preview_created_key")
        require(fresh["counts"]["notification_deliveries"] == 0
                and fresh["counts"]["notification_attempts"] == 0, "preview_enqueued_delivery")
        self.phase("disabled-default-and-preview-no-key", fresh)
        saved = self.update(live, 0, enabled=True, replace=True)
        require(saved["revision"] == 1 and saved["has_token"] and saved["storage_ready"],
                "settings_save_failed")
        baseline = self.state(live, decrypt=True)
        require(baseline["counts"]["notification_deliveries"] == 0, "save_enqueued_delivery")
        self.phase("private-key-and-encrypted-token", baseline)
        request_id = str(uuid4())
        payload = {"expected_revision": 1, "request_id": request_id}
        first = self.http(live, "POST", "/api/v1/notifications/test", payload, status=202)["body"]
        delivery_id = first["delivery"]["id"]
        again = self.http(live, "POST", "/api/v1/notifications/test", payload, status=202)["body"]
        require(again["delivery"]["id"] == delivery_id, "duplicate_test_not_idempotent")
        self.verify_identity(live, request_id, delivery_id)
        self.stable_cipher(self.state(live, decrypt=True), baseline)
        self.phase("explicit-offline-test-idempotency", {"request_id": request_id,
                   "delivery_id": delivery_id, "telegram_acceptance_verified": False})
        self.command("restart", "--time", "30", live, timeout=40)
        self.wait_ready(live)
        self.assert_hardening(live)
        self.verify_identity(live, request_id, delivery_id)
        require(self.settings(live) == saved, "settings_not_preserved_after_restart")
        self.stable_cipher(self.state(live, decrypt=True), baseline)
        self.phase("same-volume-restart", {"original_session": True, "request_preserved": True})
        self.stop(live)
        self.create_container("cold-backup", backup_volume, source=live_volume,
                              helper_job={"operation": "copy"})
        self.create_container("cold-restore", restored_volume, source=backup_volume,
                              helper_job={"operation": "copy"})
        restored = self.create_container("restored", restored_volume)
        require(self.containers[restored]["id"] != self.containers[live]["id"],
                "restore_reused_original_container")
        self.verify_identity(restored, request_id, delivery_id)
        require(self.settings(restored) == saved, "cold_restored_settings_changed")
        self.stable_cipher(self.state(restored, decrypt=True), baseline)
        duplicate = self.http(restored, "POST", "/api/v1/notifications/test", payload, status=202)
        require(duplicate["body"]["delivery"]["id"] == delivery_id, "restored_request_duplicated")
        self.phase("independent-cold-restore", {"original_session": True, "original_csrf": True,
                   "request_preserved": True, "database_and_key_copied": True})
        original_key_hash = baseline["files"]["key"]["sha256"]
        self.stop(restored)
        self.create_container("remove-key", restored_volume, helper_job={
            "operation": "remove_key", "expected_key_hash": original_key_hash,
        })
        self.start(restored)
        missing = self.blocked_key(restored, 1, "notification_storage_key_missing", baseline,
                                   missing=True)
        disabled = self.update(restored, 1, enabled=False)
        require(disabled["revision"] == 2 and disabled["enabled"] is False
                and disabled["has_token"] is True, "explicit_disable_did_not_preserve_token")
        self.stable_cipher(self.state(restored), baseline, key=False)
        self.stop(restored)
        self.start(restored)
        self.blocked_key(restored, 2, "notification_storage_key_missing", baseline, missing=True)
        self.phase("missing-key-fails-closed-across-restart", missing)
        self.stop(restored)
        wrong = self.create_container("wrong-key", restored_volume, helper_job={
            "operation": "wrong_key", "original_key_hash": original_key_hash,
        })
        wrong_result = self.report["phases"][-1]["evidence"]
        wrong_hash = wrong_result["key_sha256"]
        require(wrong in self.containers, "wrong_key_helper_missing")
        self.start(restored)
        invalid = self.blocked_key(restored, 2, "notification_storage_key_invalid", baseline,
                                   missing=False)
        require(invalid["files"]["key"]["sha256"] == wrong_hash, "wrong_key_replaced")
        self.stop(restored)
        self.start(restored)
        invalid = self.blocked_key(restored, 2, "notification_storage_key_invalid", baseline,
                                   missing=False)
        require(invalid["files"]["key"]["sha256"] == wrong_hash, "wrong_key_recreated_on_restart")
        self.phase("wrong-key-fails-closed-across-restart", invalid)
        self.stop(restored)
        self.create_container("restore-original-key", restored_volume, source=backup_volume,
                              helper_job={"operation": "restore_key",
                                          "original_key_hash": original_key_hash,
                                          "expected_key_hash": wrong_hash})
        self.start(restored)
        self.verify_identity(restored, request_id, delivery_id)
        current = self.settings(restored)
        require(current["revision"] == 2 and current["enabled"] is False
                and current["has_token"] and current["storage_ready"]
                and current["storage_error"] is None, "original_key_did_not_restore_access")
        self.stable_cipher(self.state(restored, decrypt=True), baseline)
        enabled = self.update(restored, 2, enabled=True)
        require(enabled["revision"] == 3 and enabled["storage_ready"], "restored_key_cannot_enable")
        self.stable_cipher(self.state(restored, decrypt=True), baseline)
        self.phase("original-key-restored-without-ciphertext-loss", {
            "disabled_state_retained": True, "original_session": True,
            "request_preserved": True, "can_enable_after_key_restore": True,
        })

    def cleanup(self) -> dict:
        removed_containers, removed_volumes, errors = [], [], []
        markers = [SYNTHETIC_TOKEN, self.password, self.cookie, self.csrf]
        markers = [marker.encode() for marker in markers if marker]
        for name in reversed(list(self.containers)):
            try:
                value = self.inspect("container", name, optional=True)
                if value is None:
                    continue
                self.owned_container(name)
                logs = self.command("logs", "--tail", "1000", name, check=False)
                raw = logs.stdout + logs.stderr
                leaked = any(marker in raw for marker in markers)
                if leaked:
                    errors.append("secret_present_in_container_logs")
                for marker in markers:
                    raw = raw.replace(marker, b"<redacted-fixture-secret>")
                (self.output / (self.containers[name]["role"] + ".log")).write_bytes(raw)
                if value["State"]["Running"]:
                    self.command("stop", "--time", "30", name, timeout=40)
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
        left_containers = self.command("ps", "-aq", "--filter",
                                       f"label={OWNER_LABEL}={self.owner}").stdout.decode().split()
        left_volumes = self.command("volume", "ls", "-q", "--filter",
                                    f"label={OWNER_LABEL}={self.owner}").stdout.decode().split()
        return {"removed_containers": removed_containers, "removed_volumes": removed_volumes,
                "remaining_containers": left_containers, "remaining_volumes": left_volumes,
                "errors": errors,
                "complete": not errors and not left_containers and not left_volumes}

    def run(self) -> bool:
        before = None
        failed = None
        try:
            self.preflight()
            before = self.protected()
            write_json(self.output / "protected-before.json", before)
            self.exercise()
        except KeyboardInterrupt:
            failed = "fixture_interrupted"
        except SmokeFailure as error:
            failed = str(error)
        except Exception:
            failed = "unexpected_fixture_failure"
        finally:
            try:
                cleanup = self.cleanup()
            except Exception:
                cleanup = {"complete": False, "errors": ["cleanup_incomplete"]}
            self.report["cleanup"] = cleanup
            if before is not None:
                try:
                    after = self.protected()
                    write_json(self.output / "protected-after.json", after)
                    self.report["protected_unchanged"] = before == after
                    self.report["source_unchanged"] = (
                        self.report["source_sha256"] == {
                            name: digest(ROOT / "backend/app/open_node" / name)
                            for name in APP_SOURCES
                        }
                        and self.report["fixture_sha256"] == digest(Path(__file__))
                        and self.frontend_files == frontend_manifest()
                    )
                except Exception:
                    self.report["protected_unchanged"] = False
            complete = (failed is None and cleanup["complete"]
                        and self.report.get("protected_unchanged") is True
                        and self.report.get("source_unchanged") is True)
            self.report["status"] = "passed" if complete else "failed"
            self.report["failure_code"] = failed
            self.persist()
        return complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True,
                        help="Exact full Git SHA or an explicit working-tree-... image label")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(sys.platform == "linux", "run_only_on_isolated_linux_vps")
    require(re.fullmatch(r"[a-z0-9][a-z0-9._/:@-]{0,255}", args.image), "invalid_image_reference")
    require(re.fullmatch(r"(?:[0-9a-f]{40}|working-tree[-:][A-Za-z0-9._-]{1,100})", args.revision),
            "explicit_revision_required")
    output = args.output.absolute()
    require(len(output.parts) >= 3
            and not any(path.is_symlink() for path in (output, *output.parents)),
            "unsafe_evidence_path")
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
    print(json.dumps({"status": "passed" if passed else "failed",
                      "report": str(output / "report.json"),
                      "telegram_acceptance_verified": False}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as error:
        print(json.dumps({"status": "failed", "failure_code": str(error)}))
        raise SystemExit(1) from None
