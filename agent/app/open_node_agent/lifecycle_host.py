"""Opt-in, root-owned Agent deployment helper; no public network listener."""

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import signal
import socket
import sqlite3
import ssl
import stat
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit


def sibling(name):
    spec = importlib.util.spec_from_file_location(
        "open_node_host_" + name, Path(__file__).with_name(name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service = sibling("service")
protocol = sibling("lifecycle_protocol")
SOURCE_FILES = ("service.py", "lifecycle_protocol.py", "lifecycle_host.py", "lifecycle_report.py")
MAX_WHEEL_BYTES = 32 * 1024 * 1024
JOB_TIMEOUT = 900


def owned(path, *, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise service.DeploymentError(
            "Lifecycle files must be root-owned and not writable by others"
        )
    if not directory and info.st_nlink != 1:
        raise service.DeploymentError("Lifecycle files cannot be hard-linked")


def unit_names(deployment):
    name = deployment.unit.removesuffix(".service") + "-lifecycle"
    return name + ".socket", name + ".service"


def unit_texts(deployment):
    socket_unit, worker_unit = unit_names(deployment)
    directory = deployment.root / "lifecycle"
    marker = deployment.record["installation_id"]
    start = (
        f"/usr/bin/python3 -I {directory}/lifecycle_host.py "
        f"--root {deployment.root} --unit {deployment.unit} serve"
    )
    return {
        socket_unit: f"""# Managed by Open Node Agent: {marker}
[Unit]
Description=Open Node Agent lifecycle socket

[Socket]
ListenStream={directory}/control.sock
SocketUser=root
SocketGroup={deployment.user}
SocketMode=0660
RemoveOnStop=true
Service={worker_unit}

[Install]
WantedBy=sockets.target
""",
        worker_unit: f"""# Managed by Open Node Agent: {marker}
[Unit]
Description=Open Node Agent lifecycle worker
Requires={socket_unit}
After={socket_unit} network-online.target
Wants=network-online.target

[Service]
ExecStart={start}
WorkingDirectory={directory}
Sockets={socket_unit}
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
KillMode=control-group
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateDevices=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
ReadWritePaths={deployment.root} {deployment.unit_file.parent} /run/lock
Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1

[Install]
WantedBy=multi-user.target
""",
    }


def verify_helper(deployment, *, verify_units=True):
    policy = deployment.record.get("lifecycle")
    if not isinstance(policy, dict) or policy.get("schema") != 1:
        raise service.DeploymentError("Remote Agent lifecycle is not enabled")
    directory = deployment.root / "lifecycle"
    owned(directory, directory=True)
    owned(directory / "private", directory=True)
    if set(policy.get("files", {})) != set(SOURCE_FILES):
        raise service.DeploymentError("Incomplete lifecycle helper identity")
    if set(policy.get("units", {})) != set(unit_names(deployment)):
        raise service.DeploymentError("Incomplete lifecycle unit identity")
    for name, digest in policy["files"].items():
        path = directory / name
        owned(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise service.DeploymentError("Lifecycle helper code changed; inspect the installation")
    if verify_units:
        for name, text in policy["units"].items():
            path = deployment.unit_file.parent / name
            owned(path)
            if path.read_text() != text:
                raise service.DeploymentError("Lifecycle unit was modified")
            properties = service.command(
                "systemctl", "show", name, "--property=FragmentPath,DropInPaths"
            ).stdout
            properties = dict(line.split("=", 1) for line in properties.splitlines() if "=" in line)
            if properties.get("FragmentPath") not in {"", str(path)} or properties.get(
                "DropInPaths"
            ):
                raise service.DeploymentError("Lifecycle unit has external overrides")
    return policy


def validate_base_url(value):
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or len(value) > 1024
    ):
        raise service.DeploymentError("Release source must be an explicit HTTPS base URL")
    return value.rstrip("/")


def start_helper(deployment):
    verify_helper(deployment)
    service.command("systemctl", "enable", "--now", *unit_names(deployment))


def stop_helper(deployment, *, remove_units=False):
    policy = verify_helper(deployment)
    service.command("systemctl", "disable", "--now", *unit_names(deployment))
    if remove_units:
        for name in policy["units"]:
            (deployment.unit_file.parent / name).unlink()
        service.command("systemctl", "daemon-reload")
        service.command("systemctl", "reset-failed", *unit_names(deployment), check=False)


def enable_helper(deployment, base_url, ca_file=None):
    deployment.verify_unit()
    if (
        deployment.record["status"] != "installed"
        or deployment.record.get("pending")
        or deployment.record.get("staging")
        or deployment.record.get("policy_restore")
        or deployment.record.get("lifecycle")
    ):
        raise service.DeploymentError(
            "Enable remote lifecycle on an installed, recovered host only once"
        )
    directory = deployment.root / "lifecycle"
    if directory.exists() or len(str(directory / "control.sock").encode()) >= 108:
        raise service.DeploymentError(
            "Lifecycle directory already exists or its socket path is too long"
        )
    base_url = validate_base_url(base_url)
    texts = unit_texts(deployment)
    for name in texts:
        if (deployment.unit_file.parent / name).exists():
            raise service.DeploymentError("Lifecycle service name is already in use")
        loaded = service.command(
            "systemctl", "show", name, "--property=FragmentPath", "--value"
        ).stdout.strip()
        if loaded:
            raise service.DeploymentError("Lifecycle service name is already in use")
    current = deployment.record["current"]
    service.command(
        "runuser",
        "-u",
        deployment.user,
        "--",
        deployment.release_path(current) / "bin/python",
        "-c",
        "import open_node_agent.lifecycle_protocol",
    )
    original = deployment.config.read_bytes()
    was_active = deployment.properties().get("ActiveState") == "active"
    directory.mkdir(mode=0o750)
    os.chown(directory, 0, deployment.record["gid"])
    private = directory / "private"
    private.mkdir(mode=0o700)
    hashes = {}
    try:
        for name in SOURCE_FILES:
            content = Path(__file__).with_name(name).read_bytes()
            service.write_file(directory / name, content, mode=0o644)
            hashes[name] = hashlib.sha256(content).hexdigest()
        ca = None
        if ca_file:
            content = ca_file.read_bytes()
            ssl.create_default_context(cadata=content.decode())
            ca = str(private / "release-ca.pem")
            service.write_file(Path(ca), content)
        deployment.record["lifecycle"] = {
            "schema": 1,
            "base_url": base_url,
            "ca_file": ca,
            "files": hashes,
            "units": texts,
        }
        deployment.save()
        for name, text in texts.items():
            service.write_file(deployment.unit_file.parent / name, text.encode(), mode=0o644)
        config = json.loads(original)
        config["lifecycle_socket"] = str(directory / "control.sock")
        service.write_file(
            deployment.config, json.dumps(config).encode(), owner=deployment.account_owner()
        )
        deployment.preflight(current)
        service.command("systemctl", "daemon-reload")
        start_helper(deployment)
        if was_active:
            started = time.time()
            service.command("systemctl", "restart", deployment.unit)
            deployment.ready(current, started)
    except BaseException:
        service.write_file(deployment.config, original, owner=deployment.account_owner())
        service.command("systemctl", "disable", "--now", *texts, check=False)
        for name, text in texts.items():
            path = deployment.unit_file.parent / name
            if path.is_file() and not path.is_symlink() and path.read_text() == text:
                path.unlink()
        service.command("systemctl", "daemon-reload")
        deployment.record.pop("lifecycle", None)
        deployment.save()
        deployment.remove_owned(directory)
        if was_active:
            service.command("systemctl", "restart", deployment.unit)
        raise


class JobStore:
    def __init__(self, directory):
        owned(directory, directory=True)
        self.path = directory / "jobs.sqlite"
        if self.path.exists() or self.path.is_symlink():
            owned(self.path)
        with self.connection() as database:
            database.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    command TEXT NOT NULL, action TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, reported INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def connection(self):
        with contextlib.closing(sqlite3.connect(self.path, timeout=5)) as database:
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA synchronous=FULL")
            with database:
                yield database

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        result["command"] = json.loads(result["command"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def get(self, request_id):
        with self.connection() as database:
            return self.decode(
                database.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            )

    def submit(self, command):
        action = protocol.validate_command(command)
        digest = protocol.fingerprint(command)
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            existing = database.execute(
                "SELECT * FROM jobs WHERE request_id=?", (command["request_id"],)
            ).fetchone()
            if existing:
                if existing["fingerprint"] != digest:
                    raise ValueError("Lifecycle request ID reused with different content")
                return self.decode(existing)
            if database.execute(
                "SELECT 1 FROM jobs WHERE status IN ('queued', 'running') LIMIT 1"
            ).fetchone():
                raise ValueError("Another Agent deployment is active")
            now = time.time()
            database.execute(
                "INSERT INTO jobs(request_id,fingerprint,command,action,status,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (command["request_id"], digest, json.dumps(command), action, "queued", now, now),
            )
        return self.get(command["request_id"])

    def rows(self, condition="1=1"):
        with self.connection() as database:
            return [
                self.decode(row)
                for row in database.execute(
                    "SELECT * FROM jobs WHERE " + condition + " ORDER BY created_at"
                )
            ]

    def started(self, request_id):
        with self.connection() as database:
            database.execute(
                "UPDATE jobs SET status='running', updated_at=? "
                "WHERE request_id=? AND status='queued'",
                (time.time(), request_id),
            )

    def finish(self, request_id, result):
        state = "failed" if result.get("error") or result["status"] >= 400 else "succeeded"
        with self.connection() as database:
            database.execute(
                "UPDATE jobs SET status=?, result=?, updated_at=? "
                "WHERE request_id=? AND result IS NULL",
                (state, json.dumps(result), time.time(), request_id),
            )

    def acknowledge(self, request_id):
        with self.connection() as database:
            database.execute(
                "UPDATE jobs SET reported=1, updated_at=? "
                "WHERE request_id=? AND result IS NOT NULL",
                (time.time(), request_id),
            )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def download_wheel(deployment, body):
    policy = verify_helper(deployment)
    base = validate_base_url(policy["base_url"])
    origin = urlsplit(base)
    allowed = {(origin.hostname, origin.port or 443)}
    if origin.hostname == "github.com" and origin.port in {None, 443}:
        allowed.update(
            {
                ("release-assets.githubusercontent.com", 443),
                ("objects.githubusercontent.com", 443),
            }
        )
    if policy["ca_file"]:
        owned(Path(policy["ca_file"]))
    context = ssl.create_default_context(cafile=policy["ca_file"])
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        NoRedirect(),
    )
    directory = deployment.root / "lifecycle/private/wheels"
    directory.mkdir(mode=0o700, exist_ok=True)
    owned(directory, directory=True)
    filename = f"open_node_agent-{body['version']}-py3-none-any.whl"
    target_dir = directory / body["sha256"]
    target_dir.mkdir(mode=0o700, exist_ok=True)
    owned(target_dir, directory=True)
    target = target_dir / filename
    if target.exists() or target.is_symlink():
        owned(target)
        if target.stat().st_size > MAX_WHEEL_BYTES:
            raise service.DeploymentError("Cached Agent wheel exceeds the download limit")
        info = service.wheel_info(target)
        if info["sha256"] != body["sha256"] or info["version"] != body["version"]:
            raise service.DeploymentError("Cached Agent wheel integrity check failed")
        return target
    url = base + "/agent-v" + body["version"] + "/" + filename
    with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
        archive = Path(temporary) / filename
        for _ in range(5):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or (parsed.hostname, parsed.port or 443) not in allowed
            ):
                raise service.DeploymentError("Release redirect left the host-approved source")
            try:
                response = client.open(url, timeout=30)
            except urllib.error.HTTPError as error:
                if error.code in {301, 302, 303, 307, 308} and error.headers.get("Location"):
                    url = urljoin(url, error.headers["Location"])
                    error.close()
                    continue
                error.close()
                raise service.DeploymentError("Agent release download failed") from None
            with response, archive.open("xb") as output:
                size = 0
                digest = hashlib.sha256()
                while block := response.read(64 * 1024):
                    size += len(block)
                    if size > MAX_WHEEL_BYTES:
                        raise service.DeploymentError("Agent wheel exceeds the download limit")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != body["sha256"]:
                raise service.DeploymentError("Agent wheel SHA-256 mismatch")
            info = service.wheel_info(archive)
            if info["version"] != body["version"]:
                raise service.DeploymentError("Agent wheel version does not match its request")
            os.replace(archive, target)
            service.fsync_directory(target_dir)
            return target
    raise service.DeploymentError("Too many Agent release redirects")


def snapshot(deployment):
    deployment.load()
    record = deployment.record
    return {
        "installation_status": record["status"],
        "current": record["releases"].get(record["current"]),
        "previous": record["releases"].get(record.get("previous")),
        "recovery_required": bool(
            record.get("pending")
            or record.get("staging")
            or record.get("policy_restore")
            or record["status"] == "removing"
        ),
    }


def execute_job(deployment, job, *, recovering=False):
    result = {"request_id": job["request_id"], "status": 200}
    try:
        with deployment.locked():
            deployment.load()
            verify_helper(deployment)
            if recovering:
                deployment.recover()
                result.update(
                    status=409,
                    error=(
                        "Host deployment was interrupted; recovery completed, "
                        "inspect the reported selection"
                    ),
                )
            elif job["action"] == "upgrade":
                wheel = download_wheel(deployment, job["command"]["body"])
                deployment.upgrade(wheel)
            elif job["action"] == "rollback":
                deployment.rollback()
            elif job["action"] == "uninstall":
                deployment.uninstall(keep_lifecycle=True)
            else:
                raise ValueError("Unsupported lifecycle job")
    except Exception as error:
        result.update(
            status=500,
            error=(
                str(error)[:2048]
                if isinstance(error, (service.DeploymentError, ValueError))
                else "Host deployment failed: " + type(error).__name__
            ),
        )
    try:
        state = snapshot(deployment)
    except Exception:
        state = {"recovery_required": True}
    result["body"] = {
        "success": result["status"] < 400,
        "action": job["action"],
        "data_preserved": True,
        **state,
    }
    return result


def subprocess_result(arguments, *, timeout, payload=None):
    process = subprocess.Popen(
        list(map(str, arguments)),
        stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        output, error = process.communicate(
            json.dumps(payload).encode() if payload is not None else None, timeout=timeout
        )
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        raise
    if process.returncode or len(output) > protocol.MAX_MESSAGE_BYTES:
        raise service.DeploymentError("Lifecycle subprocess failed without an acknowledged result")
    return json.loads(output)


class Host:
    def __init__(self, deployment):
        self.deployment = deployment
        deployment.load()
        verify_helper(deployment)
        self.jobs = JobStore(deployment.root / "lifecycle/private")
        self.wake = threading.Event()

    def child(self, action, request_id):
        result = subprocess_result(
            [
                "/usr/bin/python3",
                "-I",
                self.deployment.root / "lifecycle/lifecycle_host.py",
                "--root",
                self.deployment.root,
                "--unit",
                self.deployment.unit,
                action,
                "--request-id",
                request_id,
            ],
            timeout=120 if action == "recover-job" else JOB_TIMEOUT,
        )
        if (
            not isinstance(result, dict)
            or result.get("request_id") != request_id
            or type(result.get("status")) is not int
            or not 100 <= result["status"] <= 599
        ):
            raise service.DeploymentError("Lifecycle subprocess returned an invalid result")
        return result

    def recover(self, job):
        try:
            result = self.child("recover-job", job["request_id"])
        except Exception:
            result = {
                "request_id": job["request_id"],
                "status": 500,
                "error": "Host deployment recovery requires operator review",
                "body": {"success": False, "recovery_required": True},
            }
        self.jobs.finish(job["request_id"], result)

    def worker(self):
        for job in self.jobs.rows("status='running'"):
            self.recover(job)
        while True:
            queued = self.jobs.rows("status='queued'")
            if queued:
                job = queued[0]
                self.jobs.started(job["request_id"])
                try:
                    result = self.child("run-job", job["request_id"])
                    self.jobs.finish(job["request_id"], result)
                except Exception:
                    self.recover(job)
            for job in self.jobs.rows("result IS NOT NULL AND reported=0")[:1]:
                try:
                    reply = subprocess_result(
                        [
                            "runuser",
                            "-u",
                            self.deployment.user,
                            "--",
                            "/usr/bin/python3",
                            "-I",
                            self.deployment.root / "lifecycle/lifecycle_report.py",
                            "--config",
                            self.deployment.config,
                        ],
                        timeout=20,
                        payload=job,
                    )
                    if reply.get("delivered") is True:
                        self.jobs.acknowledge(job["request_id"])
                except Exception:
                    pass
            self.deployment.load()
            if self.deployment.record["status"] == "removed" and not self.jobs.rows("reported=0"):
                service.command("systemctl", "disable", *unit_names(self.deployment))
                service.command("systemctl", "--no-block", "stop", *unit_names(self.deployment))
                return
            self.wake.wait(2)
            self.wake.clear()

    def handle(self, message):
        if not isinstance(message, dict):
            raise ValueError("Invalid lifecycle message")
        self.deployment.load()
        verify_helper(self.deployment, verify_units=False)
        if set(message) == {"op"} and message["op"] == "status":
            jobs = self.jobs.rows()[-10:]
            return {
                "ok": True,
                "status": {
                    "success": True,
                    "enabled": True,
                    "release_base_url": self.deployment.record["lifecycle"]["base_url"],
                    **snapshot(self.deployment),
                    "jobs": [
                        {
                            key: job[key]
                            for key in ("request_id", "action", "status", "reported", "result")
                        }
                        for job in jobs
                    ],
                },
            }
        if set(message) != {"op", "command"} or message["op"] != "submit":
            raise ValueError("Unsupported lifecycle message")
        protocol.validate_command(message["command"])
        existing = self.jobs.get(message["command"]["request_id"])
        if not existing and self.deployment.record["status"] != "installed":
            raise ValueError("Remote lifecycle requires an installed Agent")
        job = self.jobs.submit(message["command"])
        self.wake.set()
        return {"ok": True, "result": job["result"]}

    def serve(self):
        if os.geteuid() != 0:
            raise service.DeploymentError("Lifecycle service must run as root")
        if (
            int(os.environ.get("LISTEN_PID", "0")) != os.getpid()
            or os.environ.get("LISTEN_FDS") != "1"
        ):
            raise service.DeploymentError("Lifecycle service requires its systemd socket")
        listener = socket.socket(fileno=3)
        if listener.family != socket.AF_UNIX or listener.type != socket.SOCK_STREAM:
            raise service.DeploymentError("Invalid lifecycle listening socket")
        worker = threading.Thread(target=self.worker, daemon=True)
        worker.start()
        listener.settimeout(2)
        while worker.is_alive():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(3)
                _, uid, _ = struct.unpack(
                    "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                )
                if uid not in {0, self.deployment.record["uid"]}:
                    continue
                try:
                    with connection.makefile("rb") as source:
                        raw = source.readline(protocol.MAX_MESSAGE_BYTES + 1)
                    if not raw.endswith(b"\n") or len(raw) > protocol.MAX_MESSAGE_BYTES:
                        raise ValueError("Lifecycle request exceeds its message boundary")
                    response = self.handle(json.loads(raw))
                except (ValueError, TypeError, KeyError, OSError, service.DeploymentError) as error:
                    response = {"ok": False, "error": str(error)[:2048]}
                with contextlib.suppress(OSError):
                    connection.sendall(json.dumps(response).encode() + b"\n")
        if self.deployment.record["status"] != "removed":
            raise service.DeploymentError("Lifecycle worker stopped unexpectedly")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("serve")
    for action in ("run-job", "recover-job"):
        child = actions.add_parser(action)
        child.add_argument("--request-id", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    deployment = service.Deployment(args.root, args.unit, timeout=30)
    if args.action == "serve":
        Host(deployment).serve()
        return
    deployment.load()
    verify_helper(deployment)
    jobs = JobStore(deployment.root / "lifecycle/private")
    job = jobs.get(args.request_id)
    if not job or job["status"] != "running":
        raise service.DeploymentError("No running lifecycle job matches this request")
    print(json.dumps(execute_job(deployment, job, recovering=args.action == "recover-job")))


if __name__ == "__main__":
    main()
