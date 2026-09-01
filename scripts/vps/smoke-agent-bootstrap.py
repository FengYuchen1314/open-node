"""Verify the real panel-issued Agent bootstrap command on isolated Debian 12 resources.

Run only on a root/systemd VPS. No production checkout, service, database, or
Cloudflare account is modified. Public release downloads are not mocked.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[2]


class SmokeError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def assert_private(path, *, owner=0, directory=False):
    info = path.lstat()
    require(
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid == owner
        and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
        and (directory or info.st_nlink == 1),
        "Fixture credential file or directory has unsafe permissions, ownership, or links",
    )


def scrub(text, secrets_to_redact):
    for value in sorted(secrets_to_redact, key=len, reverse=True):
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def no_secrets(content, secrets_to_check, message):
    require(not any(value.encode() in content for value in secrets_to_check if value), message)


def stop_owned(process):
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


@contextmanager
def process_log(work, name, arguments, **kwargs):
    target = work / f"{name}.log"
    with target.open("xb") as log:
        target.chmod(0o600)
        process = subprocess.Popen(
            list(map(str, arguments)), stdout=log, stderr=log,
            cwd=work, start_new_session=True, **kwargs,
        )
        try:
            yield process
        finally:
            stop_owned(process)


def capture_command(arguments, *, environment, timeout=60, data=None, check=True, cwd=None):
    process = subprocess.Popen(
        list(map(str, arguments)),
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, start_new_session=True,
        cwd=cwd,
    )
    try:
        output, error = process.communicate(data, timeout=timeout)
    except BaseException:
        stop_owned(process)
        raise
    require(not check or process.returncode == 0, "Fixture host command failed")
    return process.returncode, output, error


def wait_for(description, read, *, ready=bool, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = read()
            if ready(value):
                print("PASS " + description, flush=True)
                return value
        except (httpx.TransportError, ConnectionError, FileNotFoundError):
            pass
        time.sleep(0.2)
    raise SmokeError("Timed out: " + description)


def checked_json(response, status=200, *, no_store=False):
    require(response.status_code == status, "Unexpected fixture API status")
    if no_store:
        require("no-store" in response.headers.get("cache-control", ""), "Missing no-store header")
        require(
            response.headers.get("referrer-policy") == "no-referrer",
            "Missing no-referrer header",
        )
    require(len(response.content) <= 4 * 1024 * 1024, "Fixture API response exceeded its limit")
    return response.json()


def certificates(work, redactions):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    server_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Open Node bootstrap smoke CA")])
    ca_cert = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        ).sign(ca_key, hashes.SHA256())
    )
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
        ]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca, cert, key = work / "control-ca.pem", work / "control-cert.pem", work / "control-key.pem"
    private_write(ca, ca_cert.public_bytes(serialization.Encoding.PEM))
    private_write(cert, server_cert.public_bytes(serialization.Encoding.PEM))
    secret = server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    private_write(key, secret)
    redactions.add(secret.decode())
    return ca, cert, key


def process_tree(root_pid):
    found, pending = set(), [root_pid]
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        try:
            pending.extend(
                int(child) for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
            )
        except FileNotFoundError:
            continue
    return found


def check_process_arguments(pid, credential):
    for child in process_tree(pid):
        try:
            content = Path(f"/proc/{child}/cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        no_secrets(content, {credential}, "Long-lived token appeared in an owned process argv")


def listening_sockets(pid):
    inodes = set()
    try:
        for descriptor in Path(f"/proc/{pid}/fd").iterdir():
            with suppress(FileNotFoundError):
                target = os.readlink(descriptor)
                if re.fullmatch(r"socket:\[[0-9]+\]", target):
                    inodes.add(target[8:-1])
    except FileNotFoundError:
        return []
    listeners = []
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        for line in path.read_text().splitlines()[1:]:
            parts = line.split()
            if parts[3] == "0A" and parts[9] in inodes:
                encoded, port = parts[1].split(":")
                raw = bytes.fromhex(encoded)
                if len(raw) == 4:
                    raw = raw[::-1]
                else:
                    raw = b"".join(raw[index:index + 4][::-1] for index in range(0, 16, 4))
                listeners.append((str(ipaddress.ip_address(raw)), int(port, 16)))
    return listeners


class Fixture:
    def __init__(self, *, base, work, mode, server_id, token, ticket, command, environment):
        self.base, self.work, self.mode = base, work, mode
        self.server_id, self.token, self.ticket = server_id, token, ticket
        suffix = UUID(server_id).hex[:12]
        self.root = base / f"agent-{suffix}"
        self.unit = f"open-node-agent-{suffix}.service"
        self.user = self.unit.removesuffix(".service")
        self.job = base / "private" / (UUID(server_id).hex + "-" + sha256(ticket.encode())[:16])
        self.command, self.environment = command, environment
        self.stats_port = None
        self.vless_port = None
        self.installation_id = None
        self.executed = False
        self.cleanup_attempts = 0

    def properties(self):
        _, output, _ = capture_command(
            ["systemctl", "show", self.unit,
             "--property=ActiveState,MainPID,User,FragmentPath,DropInPaths,AmbientCapabilities,"
             "CapabilityBoundingSet,NoNewPrivileges"], environment=self.environment,
        )
        return dict(line.split("=", 1) for line in output.decode().splitlines() if "=" in line)

    def record(self):
        assert_private(self.root / "installation.json")
        record = json.loads((self.root / "installation.json").read_bytes())
        require(
            record.get("root") == str(self.root)
            and record.get("unit") == self.unit and record.get("user") == self.user,
            "Fixture installation ownership does not match",
        )
        if self.installation_id is None:
            self.installation_id = record["installation_id"]
        require(record["installation_id"] == self.installation_id, "Fixture identity changed")
        return record

    def ready(self):
        try:
            record, properties = self.record(), self.properties()
            health = json.loads((self.root / "state/health.json").read_bytes())
            return (
                record["status"] == "installed" and record["pending"] is None
                and properties.get("ActiveState") == "active"
                and health["pid"] == int(properties["MainPID"])
                and health["connected"] is True and health["runtime_ready"] is True
                and 0 <= time.time() - health["observed_at"] < 5
                and Path(health["package_path"]).is_relative_to(
                    self.root / "releases" / record["current"]
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def validate_new_resources(self):
        require(not os.path.lexists(self.root), "Fixture root already exists")
        require(not os.path.lexists(Path("/etc/systemd/system") / self.unit), "Fixture unit exists")
        try:
            pwd.getpwnam(self.user)
        except KeyError:
            pass
        else:
            raise SmokeError("Fixture account already exists")
        require(not self.properties().get("FragmentPath"), "Fixture unit is already loaded")

    def cleanup(self):
        if not self.executed:
            return
        require(
            self.root.parent == self.base
            and re.fullmatch(r"agent-[a-f0-9]{12}", self.root.name)
            and not self.base.is_symlink() and not self.root.is_symlink(),
            "Refusing cleanup outside the exact owned fixture root",
        )
        if self.root.exists():
            self.record()
            helper = self.job / "bootstrap/service.py"
            assert_private(helper)
            code, output, error = capture_command(
                [sys.executable, "-I", helper, "--root", self.root, "--unit", self.unit,
                 "uninstall", "--purge"], environment=self.environment, timeout=180, check=False,
            )
            self.cleanup_attempts += 1
            private_write(
                self.work / f"{self.mode}-cleanup-{self.cleanup_attempts}.log", output + error
            )
            require(code == 0, "Owned host cleanup failed; private recovery inputs retained")
        require(not self.root.exists(), "Owned Agent root survived cleanup")
        require(
            not (Path("/etc/systemd/system") / self.unit).exists(),
            "Owned unit survived cleanup",
        )
        try:
            pwd.getpwnam(self.user)
        except KeyError:
            pass
        else:
            raise SmokeError("Owned account survived cleanup")
        # service.py deliberately leaves lock names behind. These two exact,
        # UUID-bound files were absent before the fixture was invoked.
        for key in (self.unit, sha256(str(self.root).encode())):
            lock = Path("/run/lock") / ("open-node-deploy-" + key + ".lock")
            if os.path.lexists(lock):
                assert_private(lock)
                require(lock.stat().st_size == 0, "Unexpected data in owned deployment lock")
                lock.unlink()


def panel_command(response, *, server_id, url, command_builder, test_base):
    issued = checked_json(response, 201, no_store=True)
    require(issued["license_required"] is False, "Unexpected bootstrap license requirement")
    command = issued["command"]
    require(isinstance(command, str) and len(command) <= 16384, "Invalid panel command")
    match = re.search(r"--ticket ([A-Za-z0-9_-]{43}) --server-id ([a-f0-9-]{36}) ", command)
    require(match is not None and match[2] == server_id, "Panel command identity mismatch")
    ticket = match[1]
    require(command == command_builder(url, ticket, UUID(server_id)), "Panel command bytes differ")
    ending = " --install-dependencies; )"
    require(command.endswith(ending), "Panel command structure changed; review the smoke adapter")
    isolated = command.removesuffix(ending) + " --install-dependencies --test-directory "
    isolated += shlex.quote(str(test_base)) + "; )"
    return ticket, command, isolated


def await_install_claim(fixture, process, admin, public, redactions):
    observed = False
    deadline = time.monotonic() + 1200
    base = f"/api/v1/servers/{fixture.server_id}/bootstrap"
    while process.poll() is None:
        require(time.monotonic() < deadline, "Panel-issued installation command timed out")
        check_process_arguments(process.pid, fixture.token)
        if not observed and (fixture.job / "claim.json").exists():
            assert_private(fixture.job, directory=True)
            assert_private(fixture.job / "request.json")
            assert_private(fixture.job / "claim.json")
            saved = json.loads((fixture.job / "request.json").read_bytes())
            nonce = saved["claim_nonce"]
            redactions.add(nonce)
            require(fixture.ticket not in (fixture.job / "request.json").read_text(),
                    "Raw short ticket was persisted")
            claimed = json.loads((fixture.job / "claim.json").read_bytes())
            require(claimed["configuration"]["agent_token"] == fixture.token,
                    "Redeemed token does not match this new server")
            state = checked_json(admin.get(base), no_store=True)["bootstrap"]
            require(state["status"] == "claimed" and state["agent_registered"] is False,
                    "Claim observation missed the pre-install window; no false positive allowed")
            require(
                not (fixture.job / "success.json").exists(), "Claim alone was marked successful"
            )
            wrong = public.post("/api/v1/agents/bootstrap/redeem", json={
                "ticket": fixture.ticket, "claim_nonce": secrets.token_urlsafe(32),
            })
            checked_json(wrong, 401, no_store=True)
            no_secrets(
                wrong.content, {fixture.token, fixture.ticket, nonce}, "Replay error leaks a secret"
            )
            same = checked_json(public.post("/api/v1/agents/bootstrap/redeem", json={
                "ticket": fixture.ticket, "claim_nonce": nonce,
            }), no_store=True)
            require(same["configuration"]["agent_token"] == fixture.token,
                    "Same-nonce retry did not return the persisted credential")
            observed = True
            print(
                f"PASS {fixture.mode} claim is distinct from install; nonce replay boundary",
                flush=True,
            )
        time.sleep(0.15)
    require(process.returncode == 0, "Panel-issued Agent installation command failed")
    require(observed, "Installer completed without observing the claim replay boundary")


def remote_command(admin, base, path, body):
    queued = checked_json(admin.post(base + "/commands", json={
        "method": "POST", "path": "/api/child/" + path, "body": body, "timeout_ms": 15000,
    }), 201)["command"]

    def state():
        entries = checked_json(admin.get(base + "/commands"))["commands"]
        return next(item for item in entries if item["id"] == queued["id"])

    completed = wait_for("bootstrap runtime command completes", state,
                         ready=lambda value: value["status"] in {"succeeded", "failed", "skipped"})
    require(completed["status"] == "succeeded", "Real Agent runtime command failed")
    return completed


def verify_runtime(fixture, admin, public, ca, runtime, echo_port, release):
    wait_for(f"{fixture.mode} non-root Agent is connected and runtime-ready", fixture.ready)
    record, properties = fixture.record(), fixture.properties()
    require(record["uid"] != 0 and record["gid"] != 0, "Agent service is privileged")
    require(properties["User"] == fixture.user, "Agent systemd User does not match")
    require(properties["NoNewPrivileges"] == "yes", "Agent privilege boundary changed")
    capabilities = (
        properties.get("AmbientCapabilities", "") + properties.get("CapabilityBoundingSet", "")
    )
    require(
        "cap_net_raw" not in capabilities.lower(), "Bootstrap unexpectedly enabled raw capabilities"
    )
    require(record["network_diagnostics"] is False and not record.get("lifecycle"),
            "Bootstrap unexpectedly enabled privileged host lifecycle or diagnostics")
    assert_private(fixture.root / "config/agent.json", owner=record["uid"])
    config = json.loads((fixture.root / "config/agent.json").read_bytes())
    require(config["token"] == fixture.token and config["connection_mode"] == fixture.mode,
            "Installed credential or requested transport differs")
    require(config["runtime_mode"] == "managed" and config["allow_xray_takeover"] is False,
            "Bootstrap enabled external runtime takeover")
    require(config["ca_file"] == str(fixture.root / "config/ca.pem"), "CA was not privately copied")
    assert_private(fixture.root / "config/ca.pem", owner=record["uid"])
    require(Path(config["ca_file"]).read_bytes() == ca.read_bytes(), "Installed control CA changed")
    xray = json.loads((fixture.root / "config/xray.json").read_bytes())
    require(xray["inbounds"] == [], "Bootstrap unexpectedly opened a proxy inbound")
    require(xray["api"]["listen"] == config["stats_address"], "Loopback stats binding differs")
    fixture.stats_port = int(config["stats_address"].split(":")[1])
    pid = int(properties["MainPID"])
    check_process_arguments(pid, fixture.token)
    require(listening_sockets(pid) == [], "Agent exposed a management TCP listener")
    children = process_tree(pid) - {pid}
    xray_children = []
    for child in children:
        with suppress(FileNotFoundError):
            if Path(f"/proc/{child}/exe").resolve() == fixture.root / "runtime/xray":
                xray_children.append(child)
    require(len(xray_children) == 1, "Expected one independently owned Xray child")
    for child in (pid, *xray_children):
        status = Path(f"/proc/{child}/status").read_text()
        uids = next(line for line in status.splitlines() if line.startswith("Uid:")).split()[1:]
        require(
            all(int(uid) == record["uid"] for uid in uids), "Agent/Xray process has unexpected UID"
        )
    require(set(listening_sockets(xray_children[0])) == {("127.0.0.1", fixture.stats_port)},
            "Base runtime has a non-loopback or unexpected listener")
    agents = checked_json(admin.get("/api/v1/agents"))
    registered = next(item for item in agents if item["server_id"] == fixture.server_id)
    require(registered["connection_mode"] == fixture.mode and registered["listen_port"] == 0,
            "Registered Agent transport or management listener differs")
    require(registered["agent_version"] == "open-node/" + release["agent"]["version"],
            "Registered Agent is not the pinned wheel version")
    saved = json.loads((fixture.job / "request.json").read_bytes())
    rejection = public.post("/api/v1/agents/bootstrap/redeem", json={
        "ticket": fixture.ticket, "claim_nonce": saved["claim_nonce"],
    })
    checked_json(rejection, 401, no_store=True)
    base = f"/api/v1/servers/{fixture.server_id}"
    state = checked_json(admin.get(base + "/bootstrap"), no_store=True)["bootstrap"]
    require(
        state["agent_registered"] is True, "Panel bootstrap status does not observe registration"
    )
    checked_json(
        admin.post(base + "/bootstrap", json={"transport": fixture.mode}), 409, no_store=True
    )
    print(
        f"PASS {fixture.mode} non-root ownership, no extra listener, registered ticket rejection",
        flush=True,
    )
    user_id, port, email = str(uuid4()), runtime.free_port(), "bootstrap-smoke-" + fixture.mode
    fixture.vless_port = port
    remote_command(admin, base, "inbounds", {
        "action": "add", "inbound": {
            "tag": "bootstrap-smoke", "listen": "127.0.0.1", "port": port, "protocol": "vless",
            "settings": {"decryption": "none", "clients": [
                {"id": user_id, "email": email, "level": 0}
            ]},
        },
    })
    with runtime.proxy_client(fixture.work, fixture.root / "runtime/xray", port, user_id) as socks:
        wait_for(
            f"{fixture.mode} real VLESS/HTTP echo traffic",
            lambda: runtime.forwards(socks, echo_port),
        )

        def traffic():
            runtime.forwards(socks, echo_port)
            latest = checked_json(admin.get(base + "/telemetry/latest"))["latest"]
            stats = (latest or {}).get("stats") or {}
            return stats.get("user", {}).get(email, {}).get("downlink", 0)

        amount = wait_for(f"{fixture.mode} traffic reaches real control-plane telemetry", traffic,
                          ready=lambda value: value >= len(runtime.RESPONSE_BODY), timeout=70)
    return {"agent_uid": record["uid"], "agent_version": registered["agent_version"],
            "agent_listen_port": registered["listen_port"], "traffic_downlink": amount,
            "xray_version": release["xray"]["version"], "nonce_replay_checked": True,
            "registered_redemption_rejected": True, "runtime_ready": True}


def reject_reinstallation(fixture):
    before = {
        "pid": fixture.properties()["MainPID"],
        "record": sha256((fixture.root / "installation.json").read_bytes()),
        "config": sha256((fixture.root / "config/agent.json").read_bytes()),
        "xray": sha256((fixture.root / "config/xray.json").read_bytes()),
        "unit": sha256((Path("/etc/systemd/system") / fixture.unit).read_bytes()),
    }
    code, output, error = capture_command(
        ["bash"], environment=fixture.environment, timeout=90,
        data=(fixture.command + "\n").encode(), check=False,
    )
    private_write(fixture.work / f"{fixture.mode}-repeat.log", output + error)
    require(code != 0, "Repeated bootstrap unexpectedly adopted an installed service")
    after = {
        "pid": fixture.properties()["MainPID"],
        "record": sha256((fixture.root / "installation.json").read_bytes()),
        "config": sha256((fixture.root / "config/agent.json").read_bytes()),
        "xray": sha256((fixture.root / "config/xray.json").read_bytes()),
        "unit": sha256((Path("/etc/systemd/system") / fixture.unit).read_bytes()),
    }
    require(before == after and fixture.ready(), "Repeated bootstrap changed the installed service")
    no_secrets(
        output + error, {fixture.token, fixture.ticket}, "Repeated bootstrap printed a credential"
    )
    print(
        f"PASS {fixture.mode} repeated command refuses takeover without changing live service",
        flush=True,
    )


def collect_unit_log(fixture):
    _, output, error = capture_command(
        ["journalctl", "--unit", fixture.unit, "--no-pager", "--lines", "1000", "--output", "cat"],
        environment=fixture.environment, timeout=20, check=False,
    )
    path = fixture.work / f"{fixture.mode}-journal.log"
    if not path.exists():
        private_write(path, output + error)


def exercise(repository, output):
    sys.path.insert(0, str(repository / "backend/app"))
    from open_node.resources import agent_installer as installer
    from open_node.services.agent_bootstrap_release import installation_command

    installer.check_platform()
    installer.ensure_dependencies()
    spec = importlib.util.spec_from_file_location(
        "bootstrap_runtime_smoke", repository / "scripts/vps/smoke-open-node-agent.py"
    )
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    require(
        output.is_absolute() and len(output.parts) >= 3, "Use a dedicated absolute output directory"
    )
    require(
        not output.exists() or not any(output.iterdir()), "Smoke output directory must be empty"
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.chmod(0o700)
    work = Path(tempfile.mkdtemp(prefix="open-node-bootstrap-smoke-"))
    base = Path("/opt/open-node-bootstrap-smoke-" + secrets.token_hex(6))
    base.mkdir(mode=0o755)
    base.chmod(0o755)
    marker = secrets.token_hex(32)
    private_write(base / ".fixture-owner", marker.encode())
    password = secrets.token_urlsafe(32)
    redactions, credentials, fixtures = {password}, {password}, []
    report = {
        "schema_version": 1, "status": "failed", "repository": str(repository), "modes": {},
        "command_deviation": "Only --test-directory is appended to the final verified Python argv; "
        "all downloads, SHA checks, claim requests and service installation remain real.",
        "fixture_environment": "CURL_CA_BUNDLE and OPEN_NODE_AGENT_CA_FILE use only a private "
        "loopback control-plane CA; TMPDIR isolates the command's transient Python file. "
        "GitHub downloads retain Debian system trust.",
        "production_modified": False,
    }
    cleanup_errors = []
    try:
        ca, cert, key = certificates(work, redactions)
        private_write(work / "agent-identity.seed", secrets.token_bytes(32))
        environment = installer.command_environment()
        with ExitStack() as stack:
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"https://127.0.0.1:{listener.getsockname()[1]}"
            backend_env = {
                **environment, "PYTHONPATH": str(repository / "backend/app"),
                "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'backend.sqlite'}",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "true",
                "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
                "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL": url,
                "OPEN_NODE_CORS_ORIGINS": json.dumps([url]),
                "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "certificate-state"),
                "OPEN_NODE_AGENT_IDENTITY_FILE": str(work / "agent-identity.seed"),
            }
            capture_command(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                environment=backend_env, data=(password + "\n").encode(), timeout=45, cwd=work,
            )
            backend = stack.enter_context(process_log(
                work, "backend", [sys.executable, "-m", "uvicorn", "open_node.main:app",
                                  "--fd", listener.fileno(), "--ssl-certfile", cert,
                                  "--ssl-keyfile", key],
                env=backend_env, pass_fds=(listener.fileno(),),
            ))
            context = ssl.create_default_context(cafile=str(ca))
            admin = stack.enter_context(httpx.Client(base_url=url, verify=context, trust_env=False,
                                                    timeout=15, follow_redirects=False))
            public = stack.enter_context(httpx.Client(base_url=url, verify=context, trust_env=False,
                                                     timeout=15, follow_redirects=False))

            def healthy():
                require(backend.poll() is None, "Disposable control plane exited")
                return public.get("/healthz").status_code == 200

            wait_for("real isolated FastAPI over a verified private-CA HTTPS connection", healthy)
            login = checked_json(admin.post("/api/v1/auth/login", json={
                "username": "admin", "password": password,
            }, headers={"X-Open-Node-Client": "browser"}))
            admin.headers["X-CSRF-Token"] = login["csrf_token"]
            redactions.add(login["csrf_token"])
            redactions.update(admin.cookies.values())
            release = checked_json(public.get("/api/v1/agents/bootstrap/manifest"), no_store=True)
            installer.validate_manifest(release)
            script_response = public.get("/api/v1/agents/bootstrap/installer.py")
            require(script_response.status_code == 200, "Public installer download failed")
            require("no-store" in script_response.headers.get("cache-control", ""),
                    "Installer response is cacheable")
            require(script_response.headers.get("x-content-type-options") == "nosniff",
                    "Public installer MIME protection is missing")
            require(script_response.content == (repository / "backend/app/open_node/resources/"
                                                "agent_installer.py").read_bytes(),
                    "HTTP installer bytes differ from the bundled resource")
            report["installer_sha256"] = sha256(script_response.content)
            report["release"] = release
            echo = ThreadingHTTPServer(("127.0.0.1", 0), runtime.EchoHandler)
            echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
            echo_thread.start()
            stack.callback(echo.server_close)
            stack.callback(echo_thread.join, 5)
            stack.callback(echo.shutdown)
            for mode in ("websocket", "http"):
                created = checked_json(admin.post("/api/v1/servers", json={
                    "name": "bootstrap-" + mode, "domain": "127.0.0.1",
                }), 201)
                server_id, token = created["server"]["id"], created["agent_token"]
                credentials.add(token)
                redactions.add(token)
                issued = admin.post(
                    f"/api/v1/servers/{server_id}/bootstrap", json={"transport": mode}
                )
                ticket, original, isolated = panel_command(
                    issued, server_id=server_id, url=url, command_builder=installation_command,
                    test_base=base,
                )
                redactions.add(ticket)
                require(token not in original, "Panel command contains the long-lived Agent token")
                fixture_env = {**environment, "CURL_CA_BUNDLE": str(ca),
                               "OPEN_NODE_AGENT_CA_FILE": str(ca), "TMPDIR": str(work)}
                fixture = Fixture(
                    base=base, work=work, mode=mode, server_id=server_id, token=token,
                    ticket=ticket, command=isolated, environment=fixture_env,
                )
                fixtures.append(fixture)
                fixture.validate_new_resources()
                for key_name in (fixture.unit, sha256(str(fixture.root).encode())):
                    require(
                        not os.path.lexists(Path("/run/lock") /
                                           ("open-node-deploy-" + key_name + ".lock")),
                        "Fixture lock already exists",
                    )
                start_log = (work / "backend.log").stat().st_size
                fixture.executed = True
                with process_log(work, mode + "-installer", ["bash"], stdin=subprocess.PIPE,
                                 env=fixture.environment) as child:
                    child.stdin.write((fixture.command + "\n").encode())
                    child.stdin.close()
                    await_install_claim(fixture, child, admin, public, redactions)
                result = verify_runtime(
                    fixture, admin, public, ca, runtime, echo.server_port, release
                )
                require(
                    (fixture.job / "success.json").exists(), "Installer success evidence is absent"
                )
                reject_reinstallation(fixture)
                observed_log = (work / "backend.log").read_bytes()[start_log:]
                if mode == "websocket":
                    require(b"WebSocket /api/v1/agents/ws" in observed_log,
                            "Real WebSocket connection was not observed by the backend")
                else:
                    require(b"/api/v1/agents/commands/lease" in observed_log,
                            "Real HTTP command polling was not observed by the backend")
                collect_unit_log(fixture)
                for path in work.glob(mode + "-*.log"):
                    no_secrets(
                        path.read_bytes(), credentials | {ticket},
                        "Agent or installer log leaked a secret",
                    )
                result.update({"panel_command_sha256": sha256(original.encode()),
                               "isolated_command_sha256": sha256(isolated.encode()),
                               "repeat_install_rejected": True, "log_secret_leaks": 0})
                report["modes"][mode] = result
                fixture.cleanup()
                require(
                    not runtime.port_open(fixture.stats_port), "Owned stats port survived purge"
                )
                require(
                    not runtime.port_open(fixture.vless_port), "Owned proxy port survived purge"
                )
                result["owned_resources_cleaned"] = True
            no_secrets((work / "backend.log").read_bytes(), credentials,
                       "Control-plane request URL or log leaked a long-lived credential")
            report["status"] = "passed"
    except BaseException as error:
        report["error"] = str(error) if isinstance(error, SmokeError) else type(error).__name__
    finally:
        for fixture in fixtures:
            try:
                collect_unit_log(fixture)
                fixture.cleanup()
            except BaseException as error:
                cleanup_errors.append({"root": str(fixture.root), "unit": fixture.unit,
                                       "error": type(error).__name__})
        report["cleanup_errors"] = cleanup_errors
        if cleanup_errors:
            report["status"] = "failed"
            report["retained_private_work"] = str(work)
            report["retained_fixture_root"] = str(base)
        for path in work.rglob("*.log"):
            label = "-".join(path.relative_to(work).parts)
            text = scrub(path.read_text(errors="replace"), redactions)
            private_write(output / label, text.encode())
        if not cleanup_errors:
            # Only these two freshly created, marker-checked directories are
            # recursively removed. No user checkout or generic /tmp glob.
            require(
                base.parent == Path("/opt")
                and re.fullmatch(r"open-node-bootstrap-smoke-[a-f0-9]{12}", base.name)
                and not base.is_symlink()
                and (base / ".fixture-owner").read_bytes() == marker.encode(),
                "Refusing cleanup of an unrecognized fixture directory",
            )
            require(
                work.parent == Path(tempfile.gettempdir())
                and work.name.startswith("open-node-bootstrap-smoke-")
                and not work.is_symlink(),
                "Refusing cleanup of an unrecognized private scratch",
            )
            shutil.rmtree(base)
            shutil.rmtree(work)
        private_write(
            output / "report.json", scrub(json.dumps(report, indent=2), redactions).encode()
        )
    print(
        f"Bootstrap smoke {report['status']}; sanitized report: {output / 'report.json'}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(exercise(args.repository.resolve(), args.output.absolute()))
