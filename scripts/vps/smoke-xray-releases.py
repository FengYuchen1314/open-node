"""Verify pinned Xray upgrades on non-root systemd Agents over both transports."""

import argparse
import importlib.util
import json
import os
import pwd
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


service = module("release_service", "smoke-agent-service.py")
runtime = service.runtime
ui = module("release_ui", "smoke-operator-ui.py")
ROOT = Path(__file__).resolve().parents[2]
VERSIONS = {
    "v26.3.27": runtime.XRAY_SHA256,
    "v26.2.6": "29ce535b56e207a406ffa1c2d4842dcc410be003eff8ec508bb732abc9f8e385",
}

# This fixture-only wheel retains the real runtime; the hook supplies deterministic
# occupied-port and interruption faults without modifying either Xray binary.
FAULT_SUFFIX = """

_release_smoke_start = XrayRuntime.start
async def _release_smoke_fault(self):
    marker = self.config.state_dir / "release-fault.json"
    if marker.exists():
        fault = json.loads(marker.read_text())
        if self.binary.parent.name == fault["sha256"]:
            marker.unlink()
            (self.config.state_dir / "release-fault-entered").touch()
            if fault["kind"] == "pause":
                await asyncio.Event().wait()
            else:
                import socket
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", fault["port"]))
                    listener.listen()
                    return await _release_smoke_start(self)
    return await _release_smoke_start(self)
XrayRuntime.start = _release_smoke_fault
"""


def wait_command(client, base, command, expected="succeeded"):
    identifier = command["id"]
    result = runtime.poll(
        "release command " + identifier[:8],
        lambda: next(
            row
            for row in client.get(base + "/commands")
            .raise_for_status()
            .json()["commands"]
            if row["id"] == identifier
        ),
        lambda row: row["status"] in {"succeeded", "failed", "skipped"},
        timeout=240,
    )
    assert result["status"] == expected, result
    return result


def operation(client, base, name, body=None, expected="succeeded"):
    response = client.post(base + "/operations/xray/" + name, json=body)
    response.raise_for_status()
    return wait_command(client, base, response.json()["command"], expected)


def traffic(work, xray, port, user, echo):
    with runtime.proxy_client(work, xray, port, user) as socks:
        runtime.poll(
            "VLESS forwards through the selected Xray",
            lambda: runtime.forwards(socks, echo),
        )


def browser(client, base, url, name, output, verify):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies(
            [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "url": url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
                for cookie in client.cookies.jar
            ]
        )
        page = context.new_page()
        for view, viewport in (
            ("desktop", {"width": 1440, "height": 900}),
            ("mobile", {"width": 390, "height": 844}),
            ("mobile-narrow", {"width": 320, "height": 740}),
        ):
            page.set_viewport_size(viewport)
            page.goto(url)
            target = page.get_by_label("Target server", exact=True)
            target.scroll_into_view_if_needed()
            target.press("Enter")
            page.get_by_role("option", name=re.compile(re.escape(name))).click()
            page.get_by_role("button", name="Install Xray", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible()
            version = dialog.get_by_label("Xray version", exact=True)
            version.fill("../../invalid")
            dialog.get_by_text("Install / Upgrade Xray", exact=True).click()
            expect(
                dialog.get_by_role("button", name="Install", exact=True)
            ).to_be_disabled()
            version.fill("v26.2.6")
            dialog.get_by_text("Install / Upgrade Xray", exact=True).click()
            checksum = dialog.get_by_label("Archive SHA-256", exact=True)
            checksum.fill(VERSIONS["v26.2.6"])
            page.wait_for_function(
                "el => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1",
                arg=checksum.element_handle(),
            )
            if view != "desktop":
                dialog.get_by_label("Runtime state", exact=True).press("Enter")
                page.get_by_role("option", name="Running", exact=True).click()
            ui.check_layout(page)
            page.screenshot(path=str(output / (view + "-xray-install.png")))
            with page.expect_response(
                lambda response: response.url.endswith(
                    base + "/operations/xray/install"
                )
            ) as queued:
                dialog.get_by_role("button", name="Install", exact=True).click()
            command = queued.value.json()["command"]
            expected = {"version": "v26.2.6", "sha256": VERSIONS["v26.2.6"]}
            if view != "desktop":
                expected["start"] = True
            assert command["body"] == expected
            wait_command(client, base, command)
            expect(dialog).not_to_be_visible()
            verify()
            page.get_by_role("button", name="Roll back Xray", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(
                dialog.get_by_role("button", name="Confirm", exact=True)
            ).to_be_disabled()
            dialog.get_by_label("Confirm runtime change", exact=True).check()
            ui.check_layout(page)
            page.screenshot(path=str(output / (view + "-xray-rollback.png")))
            with page.expect_response(
                lambda response: response.url.endswith(
                    base + "/operations/xray/rollback"
                )
            ) as queued:
                dialog.get_by_role("button", name="Confirm", exact=True).click()
            wait_command(client, base, queued.value.json()["command"])
            verify()
        context.close()
        browser.close()
    print(
        "PASS desktop/mobile version pins, invalid input rejection and confirmed rollback",
        flush=True,
    )


def exercise_mode(work, fixture, wheel, xray, client, url, echo, output, mode):
    name = "xray-release-" + mode
    created = (
        client.post("/api/v1/servers", json={"name": name}).raise_for_status().json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    config = work / (mode + "-agent.json")
    xray_config = work / (mode + "-xray.json")
    port, user = runtime.free_port(), str(uuid4())
    runtime.write_private(
        config,
        {
            "master_url": url,
            "token": created["agent_token"],
            "allow_insecure_http": True,
            "connection_mode": mode,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
        },
    )
    runtime.write_private(
        xray_config,
        {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "vless",
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "vless",
                    "settings": {
                        "decryption": "none",
                        "clients": [{"id": user, "email": "release-user"}],
                    },
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        },
    )
    original_config = xray_config.read_bytes()
    fixture.cli(
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
    account = pwd.getpwnam(fixture.user)
    assert account.pw_uid != 0 and fixture.ready()
    bootstrap = fixture.root / "runtime/xray"
    original_binary = bootstrap.read_bytes()
    node_config = fixture.root / "config/xray.json"
    state = fixture.root / "state"

    def verify():
        assert bootstrap.read_bytes() == original_binary
        assert node_config.read_bytes() == original_config
        assert bootstrap.stat().st_uid == 0
        selection_file = state / "xray-release.json"
        selected = (
            json.loads(selection_file.read_text())["current"]["release"]
            if selection_file.exists()
            else None
        )
        expected_binary = (
            state / "xray-releases" / selected["sha256"] / "xray"
            if selected
            else bootstrap
        )
        pid = fixture.properties()["MainPID"]
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        assert any(
            Path(f"/proc/{child}/exe").resolve() == expected_binary
            for child in children
        )
        traffic(work, xray, port, user, echo)

    verify()
    for version in ("v26.2.6", "v26.3.27"):
        result = operation(client, base, "install", {"version": version})
        assert result["result_body"]["release"] == {
            "version": version,
            "sha256": VERSIONS[version],
        }
        binary = state / "xray-releases" / VERSIONS[version] / "xray"
        assert (
            binary.stat().st_uid == account.pw_uid
            and binary.stat().st_mode & 0o777 == 0o700
        )
        verify()
    operation(
        client, base, "install", {"version": "v26.3.27", "sha256": "0" * 64}, "failed"
    )
    verify()
    node_config.write_bytes(b'{"inbounds":[{"protocol":"invalid-release-fixture"}]}')
    try:
        operation(client, base, "install", {"version": "v26.2.6"}, "failed")
        traffic(work, xray, port, user, echo)
    finally:
        node_config.write_bytes(original_config)
    geo_config = json.loads(original_config)
    geo_config["routing"] = {
        "rules": [{"type": "field", "domain": ["geosite:cn"], "outboundTag": "direct"}]
    }
    checked = (
        client.post(
            base + "/operations/xray/test-config",
            json={"config": json.dumps(geo_config)},
        )
        .raise_for_status()
        .json()["command"]
    )
    assert wait_command(client, base, checked)["result_body"]["ok"] is True
    print(
        "PASS "
        + mode
        + " official packages, geodata, non-root selection and validation-before-stop",
        flush=True,
    )

    variant = service.variant_wheel(
        wheel, work, "runtime-faults-" + mode, runtime_suffix=FAULT_SUFFIX
    )
    fixture.cli("upgrade", "--wheel", variant)
    verify()

    def fault(kind):
        (state / "release-fault-entered").unlink(missing_ok=True)
        marker = state / "release-fault.json"
        runtime.write_private(
            marker, {"kind": kind, "port": port, "sha256": VERSIONS["v26.2.6"]}
        )
        os.chown(marker, account.pw_uid, account.pw_gid)

    fault("bind")
    operation(client, base, "install", {"version": "v26.2.6"}, "failed")
    assert (state / "release-fault-entered").exists()
    verify()
    for kind in ("timeout", "crash"):
        fault("pause")
        command = (
            client.post(
                base + "/commands",
                json={
                    "method": "POST",
                    "path": "/api/child/xray/install",
                    "body": {"version": "v26.2.6"},
                    "timeout_ms": 4000,
                },
            )
            .raise_for_status()
            .json()["command"]
        )
        runtime.poll(
            "candidate entered " + kind + " fault",
            lambda: (state / "release-fault-entered").exists(),
        )
        if kind == "crash":
            subprocess.run(
                [
                    "systemctl",
                    "kill",
                    "--kill-whom=all",
                    "--signal=SIGKILL",
                    fixture.unit,
                ],
                check=True,
                timeout=10,
            )
            runtime.poll(
                "Agent recovers interrupted Xray selection", fixture.ready, timeout=45
            )
        result = wait_command(client, base, command, "failed")
        assert ("interrupted" if kind == "crash" else "timed out") in result[
            "result_error"
        ]
        assert not (state / "xray-release-transaction.json").exists()
        assert (
            json.loads((state / "xray-release.json").read_text())["current"]["release"][
                "version"
            ]
            == "v26.3.27"
        )
        verify()
    fixture.cli("rollback")
    verify()
    print(
        "PASS "
        + mode
        + " real bind failure, timeout, crash recovery and Agent-wheel rollback",
        flush=True,
    )

    operation(client, base, "remove")
    runtime.poll("removed Xray stays stopped", lambda: not runtime.port_open(port))
    assert node_config.read_bytes() == original_config
    operation(client, base, "install", {"version": "v26.3.27", "start": False})
    assert not runtime.port_open(port)
    started = (
        client.post(
            base + "/operations/services/control",
            json={"service": "xray", "action": "start"},
        )
        .raise_for_status()
        .json()["command"]
    )
    wait_command(client, base, started)
    verify()
    if mode == "http":
        browser(client, base, url, name, output, verify)
    fixture.cleanup()
    assert not fixture.root.exists()


def exercise(work, fixture, wheel, xray, client, url, echo, output):
    for mode in ("websocket", "http"):
        exercise_mode(work, fixture, wheel, xray, client, url, echo, output, mode)
    print("PASS pinned Xray lifecycle on both native Agent transports", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for key in list(os.environ):
        if key.startswith("OPEN_NODE_"):
            del os.environ[key]
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    service.exercise = lambda *arguments: exercise(*arguments, output)
    service.run(
        args.wheel.resolve(), args.xray_archive.resolve() if args.xray_archive else None
    )
