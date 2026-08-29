"""Verify subscriber-private routed nodes against two real Xray instances."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


changes = module("private_route_changes", "smoke-change-sets.py")
runtime = changes.runtime


def wait_command(client, command):
    server_id = command["server_id"]
    identifier = command["id"]
    result = runtime.poll(
        "command " + identifier[:8],
        lambda: next(
            item
            for item in client.get(f"/api/v1/servers/{server_id}/commands")
            .raise_for_status()
            .json()["commands"]
            if item["id"] == identifier
        ),
        lambda item: item["status"] in {"succeeded", "failed", "skipped"},
        timeout=120,
    )
    assert result["status"] == "succeeded", result
    return result


def managed_node(client, target, name):
    return changes.request(
        client,
        "/api/v1/nodes",
        {
            "name": name,
            "server_id": target["id"],
            "protocol": "vless",
            "node_type": "physical",
            "inbound_tag": "vless",
            "config": {
                "name": name,
                "type": "vless",
                "server": "127.0.0.1",
                "port": target["port"],
                "uuid": "managed-at-provisioning",
                "tls": False,
            },
        },
    )["node"]


def subscriber(client, url, username, password):
    account = httpx.Client(base_url=url, trust_env=False, timeout=8)
    response = account.post(
        "/api/v1/account/login",
        headers={"X-Open-Node-Client": "browser"},
        json={"username": username, "password": password},
    )
    response.raise_for_status()
    session = response.json()
    assert session["authenticated"], session
    account.headers["X-CSRF-Token"] = session["csrf_token"]
    return account


def provision_account(client, username, password):
    status = client.get(
        "/api/v1/subscriber-accounts", params={"username": username}
    ).raise_for_status().json()
    client.put(
        "/api/v1/subscriber-accounts",
        params={"username": username},
        json={
            "expected_revision": status["revision"],
            "new_password": password,
            "reset_totp": False,
        },
    ).raise_for_status()


def private_client(client, token, node_id):
    config = client.get(
        f"/api/v1/subscribe/{token}?format=xray&node_id={node_id}"
    ).raise_for_status().json()
    outbound = config["outbounds"][0]
    return outbound["settings"]["vnext"][0]["users"][0]["id"]


def assert_private_config(target, expected_port, present):
    config = json.loads((target["directory"] / "xray.json").read_text())
    outbounds = [
        item for item in config["outbounds"] if item.get("tag", "").startswith("private:")
    ]
    rules = [
        item
        for item in config.get("routing", {}).get("rules", [])
        if item.get("outboundTag", "").startswith("private:")
    ]
    if not present:
        assert outbounds == [] and rules == [], config
        return
    assert len(outbounds) == len(rules) == 1, config
    endpoint = outbounds[0]["settings"]["vnext"][0]
    assert endpoint["address"] == "127.0.0.1" and endpoint["port"] == expected_port
    assert rules[0]["outboundTag"] == outbounds[0]["tag"]
    assert rules[0]["inboundTag"] == ["vless"] and len(rules[0]["user"]) == 1


def run(agent_python, xray):
    with (
        tempfile.TemporaryDirectory(prefix="open-node-private-route-smoke-") as temporary,
        ExitStack() as stack,
    ):
        work = Path(temporary)
        try:
            echo = runtime.ThreadingHTTPServer(("127.0.0.1", 0), runtime.EchoHandler)
            thread = threading.Thread(target=echo.serve_forever, daemon=True)
            thread.start()
            stack.callback(echo.server_close)
            stack.callback(thread.join, 5)
            stack.callback(echo.shutdown)

            admin_password = secrets.token_urlsafe(24)
            subscriber_password = secrets.token_urlsafe(24)
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("OPEN_NODE_")
            }
            env.update(
                PYTHONPATH=str(ROOT / "backend/app"),
                OPEN_NODE_DATABASE_URL=f"sqlite:///{work / 'backend.db'}",
                OPEN_NODE_SESSION_COOKIE_SECURE="false",
                OPEN_NODE_FRONTEND_DIR=str(ROOT / "frontend/dist"),
            )
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                input=admin_password + "\n",
                text=True,
                env=env,
                check=True,
                capture_output=True,
                timeout=20,
            )
            port = runtime.free_port()
            url = f"http://127.0.0.1:{port}"
            stack.enter_context(
                runtime.process(
                    work,
                    "backend",
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "open_node.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    env=env,
                    stdin=subprocess.DEVNULL,
                )
            )
            client = stack.enter_context(
                httpx.Client(base_url=url, trust_env=False, timeout=8)
            )
            runtime.poll(
                "controller starts", lambda: client.get("/healthz").status_code == 200
            )
            login = client.post(
                "/api/v1/auth/login",
                headers={"X-Open-Node-Client": "browser"},
                json={"username": "admin", "password": admin_password},
            )
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

            nodes = [
                changes.node(
                    work,
                    stack,
                    client,
                    url,
                    xray,
                    agent_python,
                    "private-entry" if index == 0 else "private-exit",
                    "websocket" if index == 0 else "http",
                )
                for index in range(2)
            ]
            entry, target = [
                managed_node(client, node, name)
                for node, name in zip(nodes, ("Entry", "Exit"), strict=True)
            ]
            changes.request(client, "/api/v1/users", {"username": "alice"})
            plan = changes.request(
                client,
                "/api/v1/plans",
                {
                    "name": "Private route smoke",
                    "traffic_limit_gb": 100,
                    "node_ids": [entry["id"], target["id"]],
                },
            )["plan"]
            assigned = changes.request(
                client,
                "/api/v1/users/alice/plan",
                {"plan_id": plan["id"], "queue_agent_commands": True},
            )
            for command in assigned["commands"]:
                wait_command(client, command)
            provision_account(client, "alice", subscriber_password)
            client.put(
                "/api/v1/private-routed-nodes/policy",
                json={"enabled": True, "max_nodes": 2, "daily_limit": 5},
            ).raise_for_status()

            account = subscriber(client, url, "alice", subscriber_password)
            stack.callback(account.close)
            created = changes.request(
                account,
                "/api/v1/account/private-routed-nodes",
                {
                    "label": "Real-Exit",
                    "parent_id": entry["id"],
                    "target_node_id": target["id"],
                },
            )
            private = created["node"]
            changes.wait_state(client, private["change_set_id"], "succeeded")
            active = account.get(
                "/api/v1/account/private-routed-nodes"
            ).raise_for_status().json()
            assert active["nodes"][0]["status"] == "active", active
            assert_private_config(nodes[0], nodes[1]["port"], present=True)

            token = changes.request(
                client, "/api/v1/users/alice/subscription-token"
            )["subscription"]["token"]
            credential = private_client(client, token, private["id"])
            with runtime.proxy_client(
                nodes[0]["directory"], xray, nodes[0]["port"], credential
            ) as socks:
                runtime.poll(
                    "private route forwards through target Xray",
                    lambda: runtime.forwards(socks, echo.server_port),
                )

            deleting = account.delete(
                f"/api/v1/account/private-routed-nodes/{private['id']}"
            ).raise_for_status().json()["node"]
            changes.wait_state(client, deleting["change_set_id"], "succeeded")
            assert account.get(
                "/api/v1/account/private-routed-nodes"
            ).raise_for_status().json()["nodes"] == []
            assert_private_config(nodes[0], nodes[1]["port"], present=False)
            with runtime.proxy_client(
                nodes[0]["directory"], xray, nodes[0]["port"], credential
            ) as socks:
                assert not runtime.forwards(socks, echo.server_port)
            print(
                "PASS private route creation, real forwarding, deletion and credential revocation",
                flush=True,
            )
        except BaseException:
            for path in work.rglob("*.log"):
                if path.name in {"backend.log", "agent.log"}:
                    print(
                        f"LOG {path.relative_to(work)}\n{path.read_text(errors='replace')[-3500:]}",
                        file=sys.stderr,
                    )
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--xray", type=Path, required=True)
    args = parser.parse_args()
    run(args.agent_python.absolute(), args.xray.absolute())
