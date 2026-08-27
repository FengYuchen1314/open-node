"""Verify ordered multi-node changes against installed Agents and real Xray."""

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
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


runtime = module("changes_runtime", "smoke-open-node-agent.py")
ui = module("changes_ui", "smoke-operator-ui.py")


def request(client, path, body=None):
    response = client.post(path, json=body)
    response.raise_for_status()
    return response.json()


def change(client, identifier):
    return (
        client.get(f"/api/v1/change-sets/{identifier}")
        .raise_for_status()
        .json()["change_set"]
    )


def wait_state(client, identifier, expected):
    return runtime.poll(
        expected,
        lambda: change(client, identifier),
        lambda state: state["status"] == expected,
    )


def node(work, stack, client, url, xray, agent_python, name, mode):
    directory = work / name
    directory.mkdir(mode=0o700)
    created = request(client, "/api/v1/servers", {"name": name})
    identifier = created["server"]["id"]
    port, bootstrap, user = runtime.free_port(), str(uuid4()), str(uuid4())
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [{"id": bootstrap, "email": "bootstrap"}],
                },
            }
        ],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }
    wrapper = directory / "xray-wrapper"
    wrapper.write_text(f"""#!/usr/bin/python3
import os, sys, time
from pathlib import Path
root = Path({str(directory)!r})
if '-test' in sys.argv:
    if (root / 'gate').exists():
        (root / 'ready').touch()
        while (root / 'gate').exists():
            time.sleep(0.02)
    if (root / 'fail').exists():
        raise SystemExit('fixture-only transient validator failure')
os.execv({str(xray)!r}, [{str(xray)!r}, *sys.argv[1:]])
""")
    wrapper.chmod(0o700)
    runtime.write_private(directory / "xray.json", config)
    runtime.write_private(
        directory / "agent.json",
        {
            "master_url": url,
            "token": created["agent_token"],
            "connection_mode": mode,
            "allow_insecure_http": True,
            "hostname": name,
            "state_dir": str(directory / "state"),
            "xray_binary": str(wrapper),
            "xray_config": str(directory / "xray.json"),
            "heartbeat_seconds": 300,
            "telemetry_seconds": 300,
            "poll_seconds": 0.2,
        },
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    stack.enter_context(
        runtime.process(
            directory,
            "agent",
            [
                str(agent_python),
                "-m",
                "open_node_agent",
                "--config",
                str(directory / "agent.json"),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
        )
    )
    runtime.poll(
        name + " online",
        lambda: client.get(f"/api/v1/servers/{identifier}/commands").json()["commands"],
        lambda commands: commands and all(c["status"] == "succeeded" for c in commands),
    )
    runtime.poll(name + " Xray", lambda: runtime.port_open(port))
    return {
        "id": identifier,
        "directory": directory,
        "port": port,
        "user": user,
        "bootstrap": bootstrap,
        "config": config,
    }


def gate(target):
    (target["directory"] / "ready").unlink(missing_ok=True)
    (target["directory"] / "gate").touch()


def gated(target):
    runtime.poll(
        "validator gate entered", lambda: (target["directory"] / "ready").exists()
    )


def release(target):
    (target["directory"] / "gate").unlink()


def plan(client, nodes, name, *, invalid=False, partial=False):
    steps = []
    for index, target in enumerate(nodes):
        forward = {
            "method": "POST",
            "path": "/api/child/inbounds",
            "timeout_ms": 60000,
            "body": {
                "action": "add-client",
                "tag": "vless",
                "client": {"id": target["user"], "email": "rollout"},
            },
        }
        reverse = {
            "method": "POST",
            "path": "/api/child/inbounds",
            "timeout_ms": 60000,
            "body": {
                "action": "remove-client",
                "tag": "vless",
                "client": {"email": "rollout"},
            },
        }
        if invalid and index == 1:
            bad = json.loads(json.dumps(target["config"]))
            bad["inbounds"][0]["protocol"] = "invalid-change-set-protocol"
            forward["path"] = reverse["path"] = "/api/child/xray/config"
            forward["body"] = {"config": bad, "force": True}
            reverse["body"] = {"config": target["config"], "force": True}
        steps.append(
            {
                "server_id": target["id"],
                "label": f"Node {index + 1}",
                "forward": forward,
                "rollback": None if partial and index == 0 else reverse,
            }
        )
    return request(client, "/api/v1/change-sets", {"name": name, "steps": steps})[
        "change_set"
    ]["id"]


def traffic(target, xray, echo_port, *, new=True, allowed=True):
    user = target["user"] if new else target["bootstrap"]
    with runtime.proxy_client(target["directory"], xray, target["port"], user) as socks:
        if allowed:
            runtime.poll("VLESS traffic", lambda: runtime.forwards(socks, echo_port))
        else:
            assert not runtime.forwards(socks, echo_port)


def exercise(client, nodes, xray, echo_port, name):
    first, second = nodes
    identifier = plan(client, nodes, name + " success")
    gate(first)
    request(client, f"/api/v1/change-sets/{identifier}/dispatch")
    gated(first)
    state = change(client, identifier)
    assert state["steps"][1]["forward_command"]["status"] == "waiting"
    assert state["steps"][1]["forward_command"]["attempts"] == 0
    release(first)
    wait_state(client, identifier, "succeeded")
    for target in nodes:
        traffic(target, xray, echo_port)
    gate(second)
    request(
        client,
        f"/api/v1/change-sets/{identifier}/rollback",
        {"reason": "Restore both nodes"},
    )
    gated(second)
    state = change(client, identifier)
    assert state["steps"][0]["rollback_command"]["status"] == "waiting"
    traffic(first, xray, echo_port)
    release(second)
    wait_state(client, identifier, "rolled_back")
    for target in nodes:
        traffic(target, xray, echo_port, allowed=False)
        traffic(target, xray, echo_port, new=False)

    identifier = plan(client, nodes, name + " cancelled in flight")
    gate(first)
    request(client, f"/api/v1/change-sets/{identifier}/dispatch")
    gated(first)
    response = request(client, f"/api/v1/change-sets/{identifier}/rollback")
    assert response["commands"] == [] and response["change_set"]["blocking_command_ids"]
    assert response["change_set"]["steps"][1]["forward_command"]["status"] == "skipped"
    release(first)
    final = wait_state(client, identifier, "rolled_back")
    assert final["steps"][1]["forward_command"]["attempts"] == 0
    assert final["steps"][1]["rollback_command"] is None
    traffic(first, xray, echo_port, allowed=False)

    identifier = plan(client, nodes, name + " automatic compensation", invalid=True)
    gate(second)
    request(client, f"/api/v1/change-sets/{identifier}/dispatch")
    gated(second)
    traffic(first, xray, echo_port)
    release(second)
    wait_state(client, identifier, "rolled_back")
    for target in nodes:
        traffic(target, xray, echo_port, allowed=False)
        traffic(target, xray, echo_port, new=False)
    print(
        f"PASS {name}: ordered apply, reverse traffic recovery, in-flight cancellation and automatic compensation",
        flush=True,
    )


def screenshot(page, output, name):
    page.screenshot(path=output / f"{name}.png", full_page=True, animations="disabled")
    page.screenshot(path=output / f"{name}-viewport.png", animations="disabled")
    try:
        ui.check_layout(page)
    except Exception:
        overflow = page.evaluate("""() => [...document.querySelectorAll('body *')]
            .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
            .map(el => ({tag: el.tagName, class: String(el.className),
                left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right}))
            .filter(box => box.left < -1 || box.right > innerWidth + 1)""")
        (output / f"{name}-overflow.json").write_text(json.dumps(overflow, indent=2))
        print(f"Layout failed: {name}; diagnostics in {output}", file=sys.stderr)
        raise


def browser(client, nodes, url, password, output, xray, echo_port):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/changes")
        ui.sign_in(page, password)
        expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_visible()
        identifier = plan(client, nodes, "Browser retry")
        request(client, f"/api/v1/change-sets/{identifier}/dispatch")
        wait_state(client, identifier, "succeeded")
        failure = nodes[1]["directory"] / "fail"
        failure.touch()
        request(client, f"/api/v1/change-sets/{identifier}/rollback")
        wait_state(client, identifier, "rollback_failed")
        page.reload()
        page.locator(".change-run-list .v-list-item").filter(
            has_text="Browser retry"
        ).click()
        retry = page.get_by_role("button", name="Retry rollback", exact=True)
        expect(retry).to_be_enabled()
        expect(page.get_by_role("button", name="Dispatch", exact=True)).to_be_disabled()
        page.locator(".change-step-item").last.locator(
            ".command-inspector .v-expansion-panel-title"
        ).last.click()
        for width, height, label in ((1440, 900, "desktop"), (390, 844, "mobile")):
            page.set_viewport_size({"width": width, "height": height})
            retry.scroll_into_view_if_needed()
            screenshot(page, output, f"change-failed-{label}")
        delayed = {}

        def hold_list(route):
            delayed["response"] = route.fetch()
            delayed["route"] = route

        page.route("**/api/v1/change-sets", hold_list)
        page.get_by_role("button", name="Refresh change sets", exact=True).click()
        runtime.poll(
            "list response held", lambda: page.wait_for_timeout(50) or bool(delayed)
        )
        failure.unlink()
        retry.click()
        expect(
            page.get_by_text("Queued 2 rollback commands.", exact=True)
        ).to_be_visible()
        delayed["route"].fulfill(response=delayed["response"])
        page.unroute("**/api/v1/change-sets", hold_list)
        wait_state(client, identifier, "rolled_back")
        expect(
            page.locator(".change-detail").get_by_text("rolled back", exact=True)
        ).to_be_visible(timeout=10000)
        assert change(client, identifier)["steps"][1]["rollback_history"]

        identifier = plan(
            client, nodes, "Browser partial recovery", invalid=True, partial=True
        )
        request(client, f"/api/v1/change-sets/{identifier}/dispatch")
        wait_state(client, identifier, "rollback_incomplete")
        page.reload()
        page.locator(".change-run-list .v-list-item").filter(
            has_text="Browser partial recovery"
        ).click()
        page.get_by_role("button", name="Accept current state", exact=True).click()
        dialog = page.get_by_role("dialog")
        accept = dialog.get_by_role("button", name="Accept state", exact=True)
        expect(accept).to_be_disabled()
        dialog.get_by_label("Resolution reason", exact=True).fill(
            "Verified remaining client on node A"
        )
        expect(accept).to_be_disabled()
        dialog.get_by_role("checkbox").check()
        for width, height, label in ((1440, 900, "desktop"), (390, 844, "mobile")):
            page.set_viewport_size({"width": width, "height": height})
            screenshot(page, output, f"change-accept-{label}")
        accept.click()
        wait_state(client, identifier, "accepted")
        assert not change(client, identifier)["held_server_ids"]
        traffic(nodes[0], xray, echo_port)
        assert not errors, errors
        browser.close()
    print(
        "PASS desktop/mobile rollback retry, stale refresh, explicit acceptance and live status",
        flush=True,
    )


def run(agent_python, archive, output):
    output.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="open-node-change-smoke-") as temporary,
        ExitStack() as stack,
    ):
        work = Path(temporary)
        try:
            xray = runtime.download_xray(work, archive)
            echo = runtime.ThreadingHTTPServer(("127.0.0.1", 0), runtime.EchoHandler)
            thread = threading.Thread(target=echo.serve_forever, daemon=True)
            thread.start()
            stack.callback(echo.server_close)
            stack.callback(thread.join, 5)
            stack.callback(echo.shutdown)
            password = secrets.token_urlsafe(24)
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
                input=password + "\n",
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
                json={"username": "admin", "password": password},
            )
            login.raise_for_status()
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            for index, modes in enumerate(
                (("websocket", "websocket"), ("http", "http"), ("websocket", "http"))
            ):
                with ExitStack() as agents:
                    nodes = [
                        node(
                            work,
                            agents,
                            client,
                            url,
                            xray,
                            agent_python,
                            f"pair-{index}-{role}",
                            mode,
                        )
                        for role, mode in zip(("a", "b"), modes, strict=True)
                    ]
                    exercise(client, nodes, xray, echo.server_port, "/".join(modes))
                    if index == 2:
                        browser(
                            client, nodes, url, password, output, xray, echo.server_port
                        )
        except BaseException:
            for path in work.rglob("*.log"):
                if path.name not in {"backend.log", "agent.log"}:
                    continue
                print(
                    f"LOG {path.relative_to(work)}\n{path.read_text(errors='replace')[-3500:]}",
                    file=sys.stderr,
                )
            raise
    print("PASS multi-node change-set lifecycle", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.agent_python.absolute(), args.xray_archive, args.output.resolve())
