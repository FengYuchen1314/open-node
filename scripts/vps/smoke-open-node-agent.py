"""Run the installed Open Node agent and real Xray on disposable loopback ports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
from contextlib import ExitStack, contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx

XRAY_URL = (
    "https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip"
)
XRAY_SHA256 = "23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae"
RESPONSE_BODY = ("open-node-traffic-" + uuid4().hex).encode() * 200


def poll(description, read, ready=bool, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = read()
            if ready(value):
                print(f"PASS {description}", flush=True)
                return value
        except (httpx.TransportError, ConnectionError):
            pass
        time.sleep(0.2)
    raise TimeoutError(description)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def port_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def write_private(path, data):
    path.write_text(json.dumps(data))
    path.chmod(0o600)


@contextmanager
def process(work, name, args, **kwargs):
    with (work / f"{name}.log").open("a") as log:
        child = subprocess.Popen(
            args,
            cwd=work,
            stdout=log,
            stderr=log,
            start_new_session=True,
            **kwargs,
        )
        try:
            yield child
        finally:
            # Each fixture owns a new process group, including its Xray child.
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                pass
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(RESPONSE_BODY)))
        self.end_headers()
        self.wfile.write(RESPONSE_BODY)

    def log_message(self, *_):
        pass


def download_xray(work, archive):
    if archive is None:
        archive = work / "xray.zip"
        with httpx.stream(
            "GET", XRAY_URL, follow_redirects=True, timeout=60
        ) as response:
            response.raise_for_status()
            with archive.open("wb") as output:
                for block in response.iter_bytes():
                    output.write(block)
    with archive.open("rb") as source:
        assert hashlib.file_digest(source, "sha256").hexdigest() == XRAY_SHA256
    binary = work / "xray"
    with zipfile.ZipFile(archive) as source:
        binary.write_bytes(source.read("xray"))
    binary.chmod(0o700)
    print("PASS official Xray archive SHA-256 verified", flush=True)
    return binary


@contextmanager
def proxy_client(work, xray, server_port, user_id):
    socks_port = free_port()
    name = "client-" + uuid4().hex[:8]
    path = work / f"{name}.json"
    write_private(
        path,
        {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": socks_port,
                    "protocol": "socks",
                    "settings": {"auth": "noauth"},
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": "127.0.0.1",
                                "port": server_port,
                                "users": [{"id": user_id, "encryption": "none"}],
                            }
                        ]
                    },
                }
            ],
        },
    )
    with process(work, name, [str(xray), "run", "-config", str(path)]):
        poll("SOCKS client starts", lambda: port_open(socks_port))
        yield socks_port


def forwards(socks_port, echo_port):
    result = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--noproxy",
            "",
            "--socks5-hostname",
            f"127.0.0.1:{socks_port}",
            "--max-time",
            "2",
            f"http://127.0.0.1:{echo_port}/fixture",
        ],
        capture_output=True,
        check=False,
        timeout=5,
    )
    return result.returncode == 0 and result.stdout == RESPONSE_BODY


def exercise_mode(
    work, xray, agent_python, client, url, database, echo_port, mode, *, ca_file=None
):
    directory = work / mode
    directory.mkdir(mode=0o700)
    created_response = client.post(
        "/api/v1/servers", json={"name": "independent-" + mode}
    )
    created_response.raise_for_status()
    created = created_response.json()
    server_id = created["server"]["id"]
    base = f"/api/v1/servers/{server_id}"
    server_port, api_port = free_port(), free_port()
    bootstrap_id, provisioned_id = str(uuid4()), str(uuid4())
    xray_file = directory / "xray.json"
    write_private(
        xray_file,
        {
            "log": {"loglevel": "warning"},
            "api": {
                "tag": "api",
                "listen": f"127.0.0.1:{api_port}",
                "services": ["StatsService"],
            },
            "stats": {},
            "policy": {
                "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
                "system": {"statsInboundUplink": True, "statsInboundDownlink": True},
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
                            {"id": bootstrap_id, "email": "bootstrap", "level": 0},
                        ],
                    },
                }
            ],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        },
    )
    config_file = directory / "config.yaml"
    write_private(
        config_file,
        {
            "master_url": url,
            "token": created["agent_token"],
            "connection_mode": mode,
            "allow_insecure_http": url.startswith("http://"),
            "ca_file": str(ca_file) if ca_file else None,
            "hostname": "fixture-" + mode,
            "state_dir": str(directory / "state"),
            "xray_binary": str(xray),
            "xray_config": str(xray_file),
            "stats_address": f"127.0.0.1:{api_port}",
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
        },
    )
    agent_args = [
        str(agent_python),
        "-m",
        "open_node_agent",
        "--config",
        str(config_file),
    ]
    agent_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    subprocess.run([*agent_args, "--check"], env=agent_env, check=True, timeout=10)

    def commands():
        response = client.get(base + "/commands")
        response.raise_for_status()
        return {item["id"]: item for item in response.json()["commands"]}

    def result(command_id, expected="succeeded"):
        completed = poll(
            f"{mode} command {command_id[:8]} completes",
            lambda: commands()[command_id],
            lambda item: item["status"] in {"succeeded", "failed", "skipped"},
        )
        assert completed["status"] == expected, completed
        return completed

    def queue(path, body=None, method="POST", expected="succeeded"):
        response = client.post(
            base + "/commands",
            json={
                "method": method,
                "path": "/api/child/" + path,
                "body": body,
                "timeout_ms": 15000,
            },
        )
        response.raise_for_status()
        return result(response.json()["command"]["id"], expected)

    def recovery():
        response = client.get(base + "/xray/config-snapshots/recovery?with_config=true")
        response.raise_for_status()
        return response.json()

    with process(directory, "agent", agent_args, env=agent_env):
        poll(f"{mode} initial snapshot", recovery, lambda state: state["has_current"])
        poll(f"{mode} managed Xray starts", lambda: port_open(server_port))
        with proxy_client(directory, xray, server_port, bootstrap_id) as socks:
            poll(f"{mode} actual VLESS forwarding", lambda: forwards(socks, echo_port))
            poll(
                f"{mode} actual user traffic reaches control plane",
                lambda: client.get(base + "/telemetry/latest").json().get("latest"),
                lambda item: (
                    item
                    and (item.get("stats") or {})
                    .get("user", {})
                    .get("bootstrap", {})
                    .get("downlink", 0)
                    >= len(RESPONSE_BODY)
                ),
            )

            provision = client.post(
                base + "/operations/batch-apply",
                json={
                    "inbound_clients": [
                        {
                            "tag": "vless",
                            "client": {
                                "id": provisioned_id,
                                "email": "new-user",
                                "level": 0,
                            },
                        },
                    ]
                },
            )
            provision.raise_for_status()
            result(provision.json()["command"]["id"])
            with proxy_client(
                directory, xray, server_port, provisioned_id
            ) as new_socks:
                poll(
                    f"{mode} newly provisioned user forwards",
                    lambda: forwards(new_socks, echo_port),
                )

            healthy = xray_file.read_bytes()
            invalid = json.loads(healthy)
            invalid["inbounds"][0]["protocol"] = "open-node-invalid-protocol" * 150
            rejected = queue(
                "xray/config", {"config": invalid, "force": True}, expected="failed"
            )
            assert rejected["result_status"] == 400
            assert len(rejected["result_error"]) == 2048
            assert xray_file.read_bytes() == healthy
            poll(
                f"{mode} invalid write preserves live traffic",
                lambda: forwards(socks, echo_port),
            )

            conflict = {
                "tag": "occupied-port",
                "listen": "127.0.0.1",
                "port": echo_port,
                "protocol": "socks",
                "settings": {"auth": "noauth"},
            }
            failed = queue(
                "inbounds", {"action": "add", "inbound": conflict}, expected="failed"
            )
            assert "startup" in failed["result_error"], failed
            assert xray_file.read_bytes() == healthy
            poll(
                f"{mode} failed restart restores forwarding",
                lambda: forwards(socks, echo_port),
            )

            poll(
                f"{mode} refreshed snapshot matches healthy config",
                recovery,
                lambda state: (
                    json.loads(state["current"]["config"]) == json.loads(healthy)
                ),
            )
            applied = client.post(
                base + "/xray/config-snapshots/recovery/apply", json={}
            )
            applied.raise_for_status()
            sequence = applied.json()["commands"]
            assert len(sequence) == 3
            for entry in sequence:
                result(entry["id"])
            poll(
                f"{mode} recovery test/write/restart forwards",
                lambda: forwards(socks, echo_port),
            )

            deduplicated = queue(
                "outbounds",
                {
                    "action": "add",
                    "outbound": {
                        "tag": "durable-entry",
                        "protocol": "freedom",
                    },
                    "no_restart": True,
                },
            )
            queue("services/control", {"service": "xray", "action": "stop"})
            assert not port_open(server_port)

    # Simulate a lost controller acknowledgement only in this disposable database.
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE agent_commands SET status='pending', leased_at=NULL, completed_at=NULL, "
            "result_status=NULL, result_body=NULL, result_error=NULL WHERE id=?",
            (deduplicated["id"],),
        )
    with process(directory, "agent-restarted", agent_args, env=agent_env):
        replayed = result(deduplicated["id"])
        assert replayed["attempts"] == deduplicated["attempts"] + 1
        assert replayed["result_body"] == deduplicated["result_body"]
        assert (
            len(
                [
                    entry
                    for entry in json.loads(xray_file.read_text())["outbounds"]
                    if entry["tag"] == "durable-entry"
                ]
            )
            == 1
        )
        stopped = queue("scan")
        assert stopped["result_body"]["xray_running"] is False
        assert not port_open(server_port)
        print(
            f"PASS {mode} restart deduplicates mutations and preserves stop intent",
            flush=True,
        )
        queue("services/control", {"service": "xray", "action": "start"})
        with proxy_client(directory, xray, server_port, provisioned_id) as socks:
            poll(
                f"{mode} restart preserves provisioned users",
                lambda: forwards(socks, echo_port),
            )
            queue(
                "inbounds",
                {
                    "action": "remove-client",
                    "tag": "vless",
                    "client": {"email": "new-user"},
                },
            )
            assert not forwards(socks, echo_port)
        with proxy_client(directory, xray, server_port, bootstrap_id) as socks:
            poll(
                f"{mode} revocation preserves other users",
                lambda: forwards(socks, echo_port),
            )
        speed = queue("speed", method="GET")["result_body"]
        assert speed["upload_speed"] >= 0 and speed["download_speed"] >= 0
        poll(
            f"{mode} scan report stored",
            lambda: client.get(base + "/scan/latest").json()["scan"],
            lambda scan: scan and scan["xray_running"],
        )


def run(agent_python, archive):
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="open-node-independent-smoke-"
    ) as temporary:
        work = Path(temporary)
        try:
            xray = download_xray(work, archive)
            database = work / "backend.db"
            password = secrets.token_urlsafe(32)
            backend_env = {
                **os.environ,
                "PYTHONPATH": str(root / "backend" / "app"),
                "OPEN_NODE_DATABASE_URL": f"sqlite:///{database}",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
                "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
            }
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                input=password + "\n",
                text=True,
                env=backend_env,
                cwd=work,
                check=True,
                capture_output=True,
                timeout=30,
            )
            with ExitStack() as stack:
                echo = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
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
                    process(
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
                        env=backend_env,
                        pass_fds=(listener.fileno(),),
                    )
                )
                client = stack.enter_context(
                    httpx.Client(base_url=url, timeout=10, trust_env=False)
                )
                poll(
                    "isolated FastAPI starts",
                    lambda: client.get("/healthz").status_code == 200,
                )
                login = client.post(
                    "/api/v1/auth/login",
                    json={"username": "admin", "password": password},
                    headers={"X-Open-Node-Client": "browser"},
                )
                login.raise_for_status()
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                for mode in ("websocket", "http"):
                    exercise_mode(
                        work,
                        xray,
                        agent_python,
                        client,
                        url,
                        database,
                        echo.server_port,
                        mode,
                    )
        except BaseException:
            for path in work.rglob("*.log"):
                print(
                    f"LOG {path.relative_to(work)}\n{path.read_text(errors='replace')[-5000:]}",
                    file=sys.stderr,
                )
            raise
    print(
        "PASS independent installed agent: both transports and real runtime verified",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent-python",
        type=Path,
        required=True,
        help="Python in a separate environment with the built agent wheel installed",
    )
    parser.add_argument(
        "--xray-archive", type=Path, help="Optional local copy of the pinned archive"
    )
    args = parser.parse_args()
    run(
        args.agent_python.absolute(),
        args.xray_archive.resolve() if args.xray_archive else None,
    )
