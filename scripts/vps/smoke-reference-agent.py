"""Exercise the unmodified reference agent against a disposable Open Node backend."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

REFERENCE_IMAGE = (
    "ghcr.io/iluobei/mmw-agent@sha256:"
    "d9ff8cd1525947e1e535ca49d6b22f1b63ff28d393c46efea6f88eeb40e8840d"
)


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip())
    return (result.stdout if check else result.stdout + result.stderr).strip()


def poll(description, read, ready, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = read()
            if ready(value):
                print(f"PASS {description}", flush=True)
                return value
        except httpx.TransportError:
            pass
        time.sleep(0.25)
    raise TimeoutError(description)


def run(image: str) -> None:
    root = Path(__file__).resolve().parents[2]
    network = f"open-node-smoke-{uuid4().hex[:10]}"
    container = None
    backend = None
    token = ""
    docker("image", "inspect", image)
    docker("network", "create", "--internal", network)
    try:
        gateway = json.loads(docker("network", "inspect", network))[0]["IPAM"]["Config"][0][
            "Gateway"
        ]
        with tempfile.TemporaryDirectory(prefix="open-node-agent-smoke-") as temporary:
            work = Path(temporary)
            agent_dir = work / "agent"
            xray_dir = work / "xray"
            agent_dir.mkdir()
            xray_dir.mkdir()
            xray_file = xray_dir / "config.json"
            xray_file.write_text(
                json.dumps(
                    {
                        "log": {"loglevel": "warning"},
                        "inbounds": [],
                        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
                    }
                )
            )
            with socket.socket() as listener, (work / "backend.log").open("w+") as log:
                listener.bind((gateway, 0))
                listener.listen()
                url = f"http://{gateway}:{listener.getsockname()[1]}"
                backend = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "open_node.main:app",
                        "--fd",
                        str(listener.fileno()),
                    ],
                    cwd=work,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(root / "backend" / "app"),
                        "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'open-node.db'}",
                    },
                    pass_fds=(listener.fileno(),),
                    stdout=log,
                    stderr=log,
                )
                with httpx.Client(base_url=url, timeout=5, trust_env=False) as client:
                    poll(
                        "backend starts on the isolated bridge",
                        lambda: client.get("/healthz"),
                        lambda response: response.status_code == 200,
                    )
                    response = client.post(
                        "/api/v1/servers", json={"name": "reference-agent-smoke"}
                    )
                    response.raise_for_status()
                    created = response.json()
                    token = created["agent_token"]
                    server_id = created["server"]["id"]
                    config = {
                        "master_url": url,
                        "token": token,
                        "connection_mode": "websocket",
                        "listen_port": "23889",
                        "xray_mode": "external",
                        "log_path": "/etc/mmw-agent/agent.log",
                        "xray_servers": [
                            {
                                "name": "smoke",
                                "config_path": "/usr/local/etc/xray/config.json",
                            }
                        ],
                    }
                    (agent_dir / "config.yaml").write_text(json.dumps(config))
                    container = docker(
                        "run",
                        "-d",
                        "--network",
                        network,
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--memory",
                        "256m",
                        "--pids-limit",
                        "128",
                        "--entrypoint",
                        "/usr/local/bin/mmw-agent",
                        "-e",
                        "MMWX_XRAY_MODE=external",
                        "--mount",
                        f"type=bind,src={agent_dir},dst=/etc/mmw-agent",
                        "--mount",
                        f"type=bind,src={xray_dir},dst=/usr/local/etc/xray",
                        image,
                        "-config",
                        "/etc/mmw-agent/config.yaml",
                    )
                    recovery_url = (
                        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery?with_config=true"
                    )
                    commands_url = f"/api/v1/servers/{server_id}/commands"

                    def recovery():
                        response = client.get(recovery_url)
                        response.raise_for_status()
                        return response.json()

                    first = poll(
                        "reference agent authenticates and sends its first config",
                        recovery,
                        lambda state: state["has_current"],
                    )
                    assert json.loads(first["current"]["config"]) == json.loads(
                        xray_file.read_text()
                    )
                    changed = json.loads(first["current"]["config"])
                    changed["log"]["loglevel"] = "error"
                    response = client.post(
                        f"/api/v1/servers/{server_id}/operations/xray/config/write",
                        json={"config": changed},
                    )
                    response.raise_for_status()
                    write_id = response.json()["command"]["id"]

                    def command_result():
                        commands = client.get(commands_url).json()["commands"]
                        return next(command for command in commands if command["id"] == write_id)

                    result = poll(
                        "reference agent validates and writes the requested config",
                        command_result,
                        lambda command: command["status"] in {"succeeded", "failed"},
                    )
                    assert result["status"] == "succeeded", result

                    def refresh_result():
                        commands = client.get(commands_url).json()["commands"]
                        return next(
                            (
                                command
                                for command in commands
                                if command["path"] == "/api/child/xray/config"
                                and command["query"] == "snapshot_source=master_write"
                            ),
                            None,
                        )

                    refresh = poll(
                        "write completion pushes a config refresh over WebSocket",
                        refresh_result,
                        lambda command: (
                            command is not None and command["status"] in {"succeeded", "failed"}
                        ),
                    )
                    assert refresh["status"] == "succeeded", refresh
                    assert refresh["attempts"] == 1
                    assert json.loads(refresh["result_body"]["config"]) == changed
                    refreshed = recovery()
                    assert json.loads(refreshed["current"]["config"]) == changed
                    assert json.loads(xray_file.read_text()) == changed
                    assert refreshed["has_pending"] is False

                    docker("stop", "--time", "3", container)
                    drift = json.loads(json.dumps(changed))
                    drift["outbounds"].append({"tag": "agent-only", "protocol": "freedom"})
                    xray_file.write_text(json.dumps(drift))
                    docker("start", container)
                    pending = poll(
                        "agent restart detects drift without replacing the current snapshot",
                        recovery,
                        lambda state: state["has_pending"],
                    )
                    assert json.loads(pending["current"]["config"]) == changed
                    assert any(
                        item.get("tag") == "agent-only"
                        for item in json.loads(pending["pending"]["config"])["outbounds"]
                    )
                    accepted = client.post(
                        f"/api/v1/servers/{server_id}/xray/config-snapshots/recovery/accept",
                    )
                    accepted.raise_for_status()
                    final = recovery()
                    assert final["has_pending"] is False
                    assert final["current"]["source"] == "manual_accept"
                    assert json.loads(final["current"]["config"]) == json.loads(
                        xray_file.read_text()
                    )
                    print(
                        "PASS operator accepts the recovered agent configuration",
                        flush=True,
                    )
                    print(
                        json.dumps({"status": "passed", "reference_image": image}),
                        flush=True,
                    )
    except Exception:
        if container:
            output = docker("logs", "--tail", "35", container, check=False)
            print(
                output.replace(token, "[redacted]") if token else output,
                file=sys.stderr,
            )
        raise
    finally:
        if container:
            docker("rm", "-f", "-v", container, check=False)
        if backend:
            backend.terminate()
            try:
                backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait(timeout=5)
        docker("network", "rm", network, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=REFERENCE_IMAGE)
    run(parser.parse_args().image)
