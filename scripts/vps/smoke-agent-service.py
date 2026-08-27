"""Exercise real systemd installation, upgrades, recovery, and removal on a Linux VPS."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import pwd
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "runtime_smoke", Path(__file__).with_name("smoke-open-node-agent.py")
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def variant_wheel(source, work, variant):
    directory = work / variant
    directory.mkdir()
    target = directory / source.name
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if variant == "good":
        files["open_node_agent/__init__.py"] += b"\nDEPLOYMENT_SMOKE_VARIANT = True\n"
    else:
        condition = (
            '"--check" in sys.argv'
            if variant == "preflight-failure"
            else ('"--check" not in sys.argv and "--version" not in sys.argv')
        )
        prefix = f"import sys\nif {condition}:\n    raise SystemExit('intentional smoke failure')\n"
        files["open_node_agent/__main__.py"] = (
            prefix.encode() + files["open_node_agent/__main__.py"]
        )
    record_name = next(name for name in files if name.endswith(".dist-info/RECORD"))
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    for name, body in files.items():
        if name == record_name:
            continue
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
        )
        writer.writerow([name, "sha256=" + digest, len(body)])
    writer.writerow([record_name, "", ""])
    files[record_name] = output.getvalue().encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return target


class Fixture:
    def __init__(self, work):
        suffix = uuid4().hex[:8]
        self.root = Path("/opt") / ("open-node-agent-smoke-" + suffix)
        self.unit = "open-node-agent-" + suffix + ".service"
        self.user = self.unit.removesuffix(".service")
        self.work = work
        self.arguments = [
            sys.executable,
            str(ROOT / "agent/app/open_node_agent/service.py"),
            "--root",
            str(self.root),
            "--unit",
            self.unit,
            "--timeout",
            "12",
        ]

    def cli(self, *args, check=True):
        result = subprocess.run(
            [*self.arguments, *map(str, args)],
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        if check and result.returncode:
            raise AssertionError(result.stderr)
        return result

    def record(self):
        return json.loads((self.root / "installation.json").read_text())

    def properties(self):
        result = subprocess.run(
            [
                "systemctl",
                "show",
                self.unit,
                "--property=ActiveState,MainPID,User,KillMode,NoNewPrivileges,FragmentPath",
            ],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

    def ready(self):
        try:
            health = json.loads((self.root / "state/health.json").read_text())
            properties = self.properties()
            return (
                properties["ActiveState"] == "active"
                and health["pid"] == int(properties["MainPID"])
                and health["connected"]
                and health["runtime_ready"]
                and time.time() - health["observed_at"] < 5
                and Path(health["package_path"]).is_relative_to(
                    self.root / "releases" / self.record()["current"]
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def cleanup(self):
        if (self.root / "installation.json").exists():
            result = self.cli("uninstall", "--purge", check=False)
            if result.returncode:
                print(
                    f"Cleanup needs attention for {self.root}: {result.stderr}",
                    file=sys.stderr,
                )


def exercise(work, fixture, wheel, xray, client, url, echo_port):
    response = client.post("/api/v1/servers", json={"name": "systemd-lifecycle-smoke"})
    response.raise_for_status()
    created = response.json()
    token = created["agent_token"]
    base = f"/api/v1/servers/{created['server']['id']}"
    server_port, api_port = runtime.free_port(), runtime.free_port()
    user_id = str(uuid4())
    config = work / "agent-input.json"
    runtime.write_private(
        config,
        {
            "master_url": url,
            "token": token,
            "allow_insecure_http": True,
            "connection_mode": "websocket",
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "stats_address": f"127.0.0.1:{api_port}",
        },
    )
    xray_config = work / "xray-input.json"
    runtime.write_private(
        xray_config,
        {
            "log": {"loglevel": "warning"},
            "api": {
                "tag": "api",
                "listen": f"127.0.0.1:{api_port}",
                "services": ["StatsService"],
            },
            "stats": {},
            "policy": {
                "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}
            },
            "inbounds": [
                {
                    "tag": "vless",
                    "listen": "127.0.0.1",
                    "port": server_port,
                    "protocol": "vless",
                    "settings": {
                        "decryption": "none",
                        "clients": [
                            {"id": user_id, "email": "lifecycle-user", "level": 0},
                        ],
                    },
                }
            ],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        },
    )
    source_config, source_xray = config.read_bytes(), xray_config.read_bytes()
    bad_input = work / "invalid-token-input.json"
    runtime.write_private(
        bad_input, {**json.loads(source_config), "token": "invalid-smoke-token"}
    )
    rejected = fixture.cli(
        "install",
        "--wheel",
        wheel,
        "--config",
        bad_input,
        "--xray-config",
        xray_config,
        "--xray",
        xray,
        check=False,
    )
    assert rejected.returncode != 0
    assert fixture.record()["status"] == "failed"
    assert not runtime.port_open(server_port)
    assert "invalid-smoke-token" not in rejected.stdout + rejected.stderr
    print("PASS failed first installation stops its owned runtime", flush=True)
    installed = fixture.cli(
        "install",
        "--wheel",
        wheel,
        "--config",
        config,
        "--xray-config",
        xray_config,
        "--xray",
        xray,
    )
    assert token not in installed.stdout + installed.stderr
    old = fixture.record()["current"]
    assert fixture.ready()
    properties = fixture.properties()
    assert properties["User"] == fixture.user
    assert properties["KillMode"] == "control-group"
    assert properties["NoNewPrivileges"] == "yes"
    account = pwd.getpwnam(fixture.user)
    assert account.pw_uid != 0
    assert (fixture.root / "config/agent.json").stat().st_mode & 0o777 == 0o600
    assert (fixture.root / "state").stat().st_uid == account.pw_uid
    subprocess.run(
        ["systemd-analyze", "verify", f"/etc/systemd/system/{fixture.unit}"],
        check=True,
        capture_output=True,
        timeout=20,
    )
    print(
        "PASS fresh non-root systemd installation, hardening, and readiness", flush=True
    )

    def queue(body):
        response = client.post(
            base + "/commands",
            json={"method": "POST", "path": "/api/child/outbounds", "body": body},
        )
        response.raise_for_status()
        command_id = response.json()["command"]["id"]

        def result():
            return next(
                item
                for item in client.get(base + "/commands").json()["commands"]
                if item["id"] == command_id
            )

        completed = runtime.poll(
            "non-root agent applies runtime command",
            result,
            lambda item: item["status"] in {"succeeded", "failed"},
        )
        assert completed["status"] == "succeeded", completed
        return completed["request_id"]

    request_id = queue(
        {"action": "add", "outbound": {"tag": "preserved", "protocol": "freedom"}}
    )
    saved_config = (fixture.root / "config/agent.json").read_bytes()
    saved_xray = (fixture.root / "config/xray.json").read_bytes()

    def assert_data():
        assert (fixture.root / "config/agent.json").read_bytes() == saved_config
        assert (fixture.root / "config/xray.json").read_bytes() == saved_xray
        with sqlite3.connect(fixture.root / "state/commands.sqlite") as db:
            assert db.execute(
                "SELECT result FROM commands WHERE request_id=?", (request_id,)
            ).fetchone()[0]
        assert config.read_bytes() == source_config
        assert xray_config.read_bytes() == source_xray

    good = variant_wheel(wheel, work, "good")
    invalid = variant_wheel(wheel, work, "preflight-failure")
    broken = variant_wheel(wheel, work, "startup-failure")
    with runtime.proxy_client(work, xray, server_port, user_id) as socks:
        runtime.poll(
            "installed service forwards real traffic",
            lambda: runtime.forwards(socks, echo_port),
        )
        first_pid = fixture.properties()["MainPID"]
        fixture.cli("upgrade", "--wheel", good)
        upgraded = fixture.record()["current"]
        assert upgraded != old and fixture.record()["previous"] == old
        assert fixture.properties()["MainPID"] != first_pid
        assert fixture.ready()
        assert_data()
        runtime.poll(
            "upgraded service preserves traffic and data",
            lambda: runtime.forwards(socks, echo_port),
        )
        fixture.cli("rollback")
        assert fixture.record()["current"] == old
        assert_data()
        runtime.poll(
            "explicit rollback restores traffic",
            lambda: runtime.forwards(socks, echo_port),
        )

        before = fixture.properties()["MainPID"]
        rejected = fixture.cli("upgrade", "--wheel", invalid, check=False)
        assert rejected.returncode != 0
        assert fixture.properties()["MainPID"] == before
        assert fixture.record()["current"] == old
        print("PASS failed preflight leaves running service untouched", flush=True)

        rejected = fixture.cli("upgrade", "--wheel", broken, check=False)
        assert rejected.returncode != 0 and "state restored" in rejected.stderr
        assert (
            fixture.record()["current"] == old and fixture.record()["pending"] is None
        )
        assert fixture.ready()
        assert_data()
        runtime.poll(
            "failed startup automatically restores forwarding",
            lambda: runtime.forwards(socks, echo_port),
        )

        with runtime.process(
            work,
            "interrupted-upgrade",
            [*fixture.arguments, "upgrade", "--wheel", str(good)],
        ) as upgrader:

            def switched():
                record = fixture.record()
                return (
                    record["pending"] is not None
                    and (fixture.root / "current").resolve().name == upgraded
                )

            runtime.poll("upgrade transaction records an interrupted switch", switched)
            os.killpg(upgrader.pid, signal.SIGKILL)
            upgrader.wait(timeout=5)
        assert fixture.record()["pending"] is not None
        fixture.cli("recover")
        assert (
            fixture.record()["current"] == old and fixture.record()["pending"] is None
        )
        assert_data()
        runtime.poll(
            "interrupted upgrade recovery restores forwarding",
            lambda: runtime.forwards(socks, echo_port),
        )

        pid = int(fixture.properties()["MainPID"])
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        xray_children = []
        for child in children:
            try:
                if (
                    Path(f"/proc/{child}/exe").resolve()
                    == fixture.root / "runtime/xray"
                ):
                    xray_children.append(child)
            except FileNotFoundError:
                pass
        assert xray_children
        os.kill(pid, signal.SIGKILL)
        runtime.poll(
            "systemd restarts the failed agent",
            lambda: fixture.properties()["MainPID"],
            lambda current: current not in {str(pid), "0"},
        )
        runtime.poll("restarted service becomes ready", fixture.ready)
        assert all(not Path(f"/proc/{child}").exists() for child in xray_children)
        runtime.poll(
            "process-group containment leaves no orphan runtime",
            lambda: runtime.forwards(socks, echo_port),
        )
        assert_data()

    fixture.cli("uninstall")
    assert not Path(f"/etc/systemd/system/{fixture.unit}").exists()
    assert not runtime.port_open(server_port)
    assert fixture.record()["status"] == "removed"
    assert_data()
    print(
        "PASS uninstall removes service and packages while preserving state", flush=True
    )
    fixture.cli("install", "--wheel", wheel)
    assert fixture.ready()
    assert_data()
    with runtime.proxy_client(work, xray, server_port, user_id) as socks:
        runtime.poll(
            "reinstallation reuses preserved configuration and journal",
            lambda: runtime.forwards(socks, echo_port),
        )
    fixture.cli("uninstall", "--purge")
    assert not fixture.root.exists()
    assert not Path(f"/etc/systemd/system/{fixture.unit}").exists()
    try:
        pwd.getpwnam(fixture.user)
    except KeyError:
        pass
    else:
        raise AssertionError("Owned service account survived explicit purge")
    assert (
        config.read_bytes() == source_config and xray_config.read_bytes() == source_xray
    )
    print("PASS explicit purge removes only the owned installation/account", flush=True)


def run(wheel, archive):
    if os.geteuid() != 0 or not Path("/run/systemd/system").is_dir():
        raise RuntimeError("This smoke requires root on a Linux systemd test host")
    with tempfile.TemporaryDirectory(prefix="open-node-service-smoke-") as temporary:
        work = Path(temporary)
        fixture = Fixture(work)
        try:
            xray = runtime.download_xray(work, archive)
            password = secrets.token_urlsafe(32)
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "backend/app"),
                "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'backend.db'}",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            }
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                input=password + "\n",
                text=True,
                env=env,
                cwd=work,
                capture_output=True,
                check=True,
                timeout=30,
            )
            with ExitStack() as stack:
                echo = runtime.ThreadingHTTPServer(
                    ("127.0.0.1", 0), runtime.EchoHandler
                )
                echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
                echo_thread.start()
                stack.callback(echo.server_close)
                stack.callback(echo_thread.join, 5)
                stack.callback(echo.shutdown)
                listener = stack.enter_context(socket.socket())
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                url = f"http://127.0.0.1:{listener.getsockname()[1]}"
                stack.enter_context(
                    runtime.process(
                        work,
                        "backend",
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "open_node.main:app",
                            "--fd",
                            str(listener.fileno()),
                        ],
                        env=env,
                        pass_fds=(listener.fileno(),),
                    )
                )
                client = stack.enter_context(
                    httpx.Client(base_url=url, timeout=10, trust_env=False)
                )
                runtime.poll(
                    "isolated backend starts",
                    lambda: client.get("/healthz").status_code == 200,
                )
                login = client.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": password},
                    headers={"X-Open-Node-Client": "browser"},
                )
                login.raise_for_status()
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                exercise(work, fixture, wheel, xray, client, url, echo.server_port)
        except BaseException:
            journal = subprocess.run(
                ["journalctl", "-u", fixture.unit, "-n", "60", "--no-pager"],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            print(journal.stdout, file=sys.stderr)
            raise
        finally:
            fixture.cleanup()
    print("PASS real systemd lifecycle smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    args = parser.parse_args()
    run(
        args.wheel.resolve(), args.xray_archive.resolve() if args.xray_archive else None
    )
