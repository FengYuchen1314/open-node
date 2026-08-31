"""Real multifile consolidation, failure recovery and operator checks on the VPS."""

import argparse
import base64
import importlib.util
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "external_smoke", Path(__file__).with_name("smoke-external-systemd.py")
)
external = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(external)
lifecycle, service, runtime = external.lifecycle, external.service, external.runtime
run_command = external.run_command
ROOT = Path(__file__).resolve().parents[2]

# Only the disposable fixture wheel adds these deterministic crash/port-conflict hooks.
FAULT_SUFFIX = """
import signal as _takeover_signal
import time as _takeover_time
_takeover_write = atomic_write
def _takeover_fault_write(path, content):
    _takeover_write(path, content)
    marker = path.parent.parent / "state/takeover-fault"
    if not marker.is_file():
        return
    fault = marker.read_text()
    phase = json.loads(content).get("phase") if path.name == "xray-takeover.json" else None
    if fault == "pause-activating" and phase == "activating":
        marker.unlink()
        _takeover_time.sleep(3)
    elif fault == phase or (fault == "after-target" and path.name == "xray.json"):
        marker.unlink()
        os.kill(os.getpid(), _takeover_signal.SIGKILL)
atomic_write = _takeover_fault_write
"""


def browser_workflow(client, base, backend, name, output, verify):
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        context.add_cookies(
            [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "url": backend,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
                for cookie in client.cookies.jar
            ]
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        completed = None
        try:
            for width, height, view in (
                (1440, 900, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(backend + "/config")
                expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
                target = page.get_by_label("目标服务器", exact=True)
                page.locator(".ant-select").filter(has=target).click()
                page.locator(".ant-select-dropdown:visible .ant-select-item-option").get_by_text(
                    name, exact=True
                ).click()
                page.get_by_role("button", name="接管外部 Xray", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_role("list").get_by_role("listitem")).to_have_count(
                    4, timeout=60000
                )
                checksum = dialog.locator(".ant-descriptions-row").filter(
                    has=page.get_by_text("源文件校验和", exact=True)
                ).locator("code")
                expect(checksum).to_have_count(1)
                digest = checksum.inner_text()
                assert len(digest) == 64
                confirm = dialog.get_by_role("button", name="接管", exact=True)
                expect(confirm).to_be_disabled()
                lifecycle.ui.check_layout(page)
                bounds = confirm.bounding_box()
                assert (
                    bounds
                    and bounds["y"] >= 0
                    and bounds["y"] + bounds["height"] <= height - 20
                ), bounds
                assert checksum.evaluate(
                    "el => el.parentElement.scrollWidth <= el.parentElement.clientWidth + 1"
                )
                page.screenshot(path=str(output / ("takeover-" + view + ".png")))
                dialog.get_by_label(
                    "替换源配置片段，并在 Xray 正在运行时重启", exact=True
                ).check()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith(
                            base + "/operations/xray/takeover-external"
                        )
                        and response.request.method == "POST"
                    )
                ) as queued:
                    confirm.click()
                command = queued.value.json()["command"]
                assert command["method"] == "POST" and command["body"] == {
                    "confirm": True,
                    "expected_sha256": digest,
                }
                result = lifecycle.wait_command(client, base, command)["result_body"]
                completed = completed or result
                expect(dialog).not_to_be_visible(timeout=30000)
                verify()
            assert not errors, errors
        finally:
            context.close()
            browser.close()
        return completed


def crash_and_failure_checks(
    work, fixture, xray, client, base, echo_port, port, user, sources, args
):
    run_command("systemctl", "stop", fixture.agent_unit)
    fault_wheel = service.variant_wheel(
        args.wheel,
        work,
        "takeover-fault-" + fixture.suffix,
        runtime_suffix=FAULT_SUFFIX,
    )
    run_command(
        fixture.python,
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--force-reinstall",
        fault_wheel,
    )
    marker = fixture.root / "state/takeover-fault"

    def state():
        return json.loads((fixture.root / "state/xray-takeover.json").read_bytes())

    def restore_sources():
        run_command("systemctl", "stop", fixture.agent_unit)
        run_command("systemctl", "stop", fixture.xray_unit)
        for name, raw in sources.items():
            Path(name).write_bytes(raw)
        run_command("systemctl", "start", fixture.xray_unit)
        run_command("systemctl", "start", fixture.agent_unit)
        runtime.poll(
            "original multifile layout reconnects", lambda: fixture.ready(bound=False)
        )

    for phase in ("prepared", "stopping", "after-target", "activating"):
        restore_sources()
        marker.write_text(phase)
        marker.chmod(0o600)
        os.chown(marker, fixture.uid, fixture.gid)
        command = (
            client.post(
                base + "/operations/xray/takeover-external",
                json={
                    "confirm": True,
                    "command_timeout_ms": 15000,
                },
            )
            .raise_for_status()
            .json()["command"]
        )
        runtime.poll(
            "real Agent SIGKILL at " + phase,
            lambda: fixture.pid(fixture.agent_unit) == 0,
        )
        assert state()["phase"] in {"prepared", "stopping", "writing", "activating"}
        if phase == "after-target":
            assert Path(next(iter(sources))).read_bytes() != next(
                iter(sources.values())
            )
            changed = Path(list(sources)[1])
            changed.write_bytes(b'{"operator_changed":true}')
            run_command("systemctl", "start", fixture.agent_unit)
            runtime.poll(
                "recovery remains connected for host review",
                lambda: fixture.ready(bound=False),
            )
            time.sleep(6)
            assert (
                changed.read_bytes() == b'{"operator_changed":true}'
                and fixture.pid() == 0
            )
            changed.write_bytes(sources[str(changed)])
        else:
            run_command("systemctl", "start", fixture.agent_unit)
        runtime.poll(
            "durable rollback after " + phase,
            lambda: state()["phase"] == "rolled_back",
            timeout=45,
        )
        assert {name: Path(name).read_bytes() for name in sources} == sources
        with runtime.proxy_client(work, xray, port, user) as socks:
            runtime.poll(
                "original traffic restored after " + phase,
                lambda: runtime.forwards(socks, echo_port),
            )
        result = lifecycle.wait_command(client, base, command, expected="failed")
        assert result["result_status"] == 409 and result["attempts"] >= 2

    restore_sources()
    marker.write_text("pause-activating")
    marker.chmod(0o600)
    os.chown(marker, fixture.uid, fixture.gid)
    claimed, failures = threading.Event(), []

    def occupy_port():
        try:
            runtime.poll(
                "takeover reaches activation",
                lambda: state()["phase"] == "activating" and not marker.exists(),
                timeout=40,
            )
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", port))
                listener.listen()
                claimed.set()
                runtime.poll(
                    "failed start enters rollback",
                    lambda: state()["phase"] == "restoring",
                    timeout=40,
                )
        except (OSError, ValueError, AssertionError, TimeoutError) as exc:
            failures.append(exc)

    thread = threading.Thread(target=occupy_port)
    thread.start()
    try:
        command = (
            client.post(
                base + "/operations/xray/takeover-external", json={"confirm": True}
            )
            .raise_for_status()
            .json()["command"]
        )
        lifecycle.wait_command(client, base, command, expected="failed")
    finally:
        thread.join(timeout=50)
    assert not thread.is_alive() and claimed.is_set() and not failures, failures
    runtime.poll(
        "rollback retries after the occupied port is released",
        lambda: state()["phase"] == "rolled_back",
        timeout=45,
    )
    assert {name: Path(name).read_bytes() for name in sources} == sources
    assert (
        "address already in use"
        in run_command(
            "journalctl", "-u", fixture.xray_unit, "--no-pager"
        ).stdout.lower()
    )
    with runtime.proxy_client(work, xray, port, user) as socks:
        runtime.poll(
            "real bind failure restores original traffic",
            lambda: runtime.forwards(socks, echo_port),
        )
    print(
        "PASS actual SIGKILL, independent-edit guard, interrupted command replay "
        "and occupied-port rollback",
        flush=True,
    )


def exercise_mode(
    work,
    fixture,
    xray,
    client,
    echo_port,
    mode,
    endpoint,
    ca,
    backend,
    options,
    *,
    directory_only=False,
):
    created = (
        client.post(
            "/api/v1/servers",
            json={
                "name": "multifile-" + mode + ("-directory" if directory_only else "")
            },
        )
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    user, wrong_user, added_user = str(uuid4()), str(uuid4()), str(uuid4())
    port, stats_port = runtime.free_port(), runtime.free_port()
    inbound = {
        "tag": "vless",
        "listen": "127.0.0.1",
        "port": port,
        "protocol": "vless",
        "settings": {
            "decryption": "none",
            "clients": [{"id": user, "email": "multifile"}],
        },
    }
    original = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                **inbound,
                "settings": {"decryption": "none", "clients": [{"id": wrong_user}]},
            }
        ],
        "outbounds": [{"tag": "block-first", "protocol": "blackhole"}],
        "routing": {
            "rules": [
                {"type": "field", "network": "tcp,udp", "outboundTag": "block-first"}
            ]
        },
    }
    fixture.initialize(
        xray,
        original,
        {
            "master_url": endpoint,
            "token": created["agent_token"],
            "ca_file": ca,
            "connection_mode": mode,
            "allow_xray_takeover": True,
            "auto_start": True,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
        },
    )
    declared = fixture.root / "config/declared.jsonc"
    declared.write_text(
        "// Explicit JSONC input\n"
        + json.dumps(
            {
                "api": {
                    "listen": "127.0.0.1:" + str(stats_port),
                    "services": ["StatsService"],
                    "tag": "api",
                },
                "stats": {},
                "policy": {
                    "levels": {
                        "0": {"statsUserUplink": True, "statsUserDownlink": True}
                    }
                },
            }
        )
    )
    declared.chmod(0o600)
    os.chown(declared, fixture.uid, fixture.gid)
    directory = fixture.root / "config/fragments"
    directory.mkdir(mode=0o700)
    os.chown(directory, fixture.uid, fixture.gid)
    overlay = directory / "10_overlay.json"
    fixture.private(
        overlay,
        {
            "inbounds": [inbound],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            "routing": {"rules": []},
        },
    )
    tail = directory / "20_tail.json"
    fixture.private(
        tail, {"outbounds": [{"tag": "block-last", "protocol": "blackhole"}]}
    )
    ignored = directory / "ignored.txt"
    ignored.write_text("retained fixture note")
    args = [
        "-config",
        str(fixture.xray_config),
        "-c",
        str(declared),
        "-confdir",
        str(directory),
    ]
    original_command = str(fixture.binary) + " run -config " + str(fixture.xray_config)
    if directory_only:
        fixture.xray_config = directory / "00_base.json"
        fixture.private(fixture.xray_config, original)
        replacement = directory / "01_declared.jsonc"
        declared.rename(replacement)
        declared = replacement
        agent_config = json.loads(fixture.agent_config.read_bytes())
        agent_config["xray_config"] = str(fixture.xray_config)
        fixture.private(fixture.agent_config, agent_config)
        args = ["-confdir", str(directory)]
    command_line = str(fixture.binary) + " run " + " ".join(args)
    fixture.xray_path.write_text(
        fixture.xray_text.replace(
            original_command,
            command_line,
        )
    )
    run_command("systemctl", "daemon-reload")
    sources = {
        str(path): path.read_bytes()
        for path in (fixture.xray_config, declared, overlay, tail)
    }
    original_unit = fixture.xray_path.read_bytes()
    run_command("systemctl", "start", fixture.xray_unit)
    runtime.poll(
        mode + " original multifile Xray starts", lambda: runtime.port_open(port)
    )
    native = json.loads(fixture.as_user(fixture.binary, "run", *args, "-dump").stdout)
    assert [item["tag"] for item in native["outbounds"]] == [
        "direct",
        "block-first",
        "block-last",
    ]
    assert native["routing"]["rules"] == []
    fixture.access("grant", allow_takeover=True)
    run_command("systemctl", "start", fixture.agent_unit)
    runtime.poll(
        mode + " pending takeover Agent connects", lambda: fixture.ready(bound=False)
    )

    def queue(path, body=None, *, method="POST", expected="succeeded"):
        command = (
            client.post(
                base + "/commands",
                json={
                    "method": method,
                    "path": "/api/child/" + path,
                    "body": body,
                    "timeout_ms": 120000,
                },
            )
            .raise_for_status()
            .json()["command"]
        )
        return lifecycle.wait_command(client, base, command, expected=expected)

    def takeover(body, expected="succeeded"):
        command = (
            client.post(base + "/operations/xray/takeover-external", json=body)
            .raise_for_status()
            .json()["command"]
        )
        return lifecycle.wait_command(client, base, command, expected=expected)

    with runtime.proxy_client(work, xray, port, user) as socks:
        runtime.poll(
            mode + " original native merge forwards",
            lambda: runtime.forwards(socks, echo_port),
        )
        pid = fixture.pid()
        preview = takeover({"preview": True})["result_body"]
        assert preview["preview"] and len(preview["source_files"]) == 4
        assert user not in json.dumps(preview)
        assert fixture.pid() == pid
        takeover({"confirm": True, "expected_sha256": "0" * 64}, expected="failed")
        assert (
            fixture.pid() == pid
            and {name: Path(name).read_bytes() for name in sources} == sources
        )
        if mode == "websocket" and options.output and not directory_only:
            result = browser_workflow(
                client,
                base,
                backend,
                "multifile-" + mode,
                options.output,
                lambda: runtime.poll(
                    "browser takeover forwards",
                    lambda: runtime.forwards(socks, echo_port),
                ),
            )
        else:
            result = takeover(
                {"confirm": True, "expected_sha256": preview["source_sha256"]}
            )["result_body"]
        assert result["last_phase"] == "complete" and result["restarted"]
        runtime.poll(mode + " consolidated binding is ready", fixture.ready)
        runtime.poll(
            mode + " native default outbound and credential retained",
            lambda: runtime.forwards(socks, echo_port),
        )
        assert (
            json.loads(fixture.as_user(fixture.binary, "run", *args, "-dump").stdout)
            == native
        )
        assert all(
            Path(name).read_bytes() == b"{}\n"
            for name in sources
            if name != str(fixture.xray_config)
        )
        backup = json.loads(
            (
                fixture.root
                / "state/xray-takeover-backups"
                / (result["backup_id"] + ".json")
            ).read_bytes()
        )
        assert {
            name: base64.b64decode(raw) for name, raw in backup["files"].items()
        } == sources
        assert (
            fixture.xray_path.read_bytes() == original_unit
            and ignored.read_text() == "retained fixture note"
        )
        pid = fixture.pid()
        assert takeover({"confirm": True})["result_body"]["unchanged"]
        assert fixture.pid() == pid
        queue(
            "batch-apply",
            {
                "inbound_clients": [
                    {"tag": "vless", "client": {"id": added_user, "email": "added"}}
                ]
            },
        )
        with runtime.proxy_client(work, xray, port, added_user) as new_socks:
            runtime.poll(
                mode + " post-takeover user provisioning forwards",
                lambda: runtime.forwards(new_socks, echo_port),
            )
        pid = fixture.pid()
        run_command("systemctl", "restart", fixture.agent_unit)
        runtime.poll(
            mode + " Agent restart retains consolidated service", fixture.ready
        )
        assert fixture.pid() == pid
        runtime.poll(
            mode + " forwarding survives Agent restart",
            lambda: runtime.forwards(socks, echo_port),
        )
        queue("services/control", {"service": "xray", "action": "stop"})
        run_command("systemctl", "stop", fixture.agent_unit)
        for name, raw in sources.items():
            Path(name).write_bytes(raw)
        run_command("systemctl", "start", fixture.agent_unit)
        runtime.poll(
            mode + " stopped source reconnects", lambda: fixture.ready(bound=False)
        )
        assert not takeover({"confirm": True})["result_body"]["restarted"]
        runtime.poll(mode + " stopped consolidation is ready", fixture.ready)
        time.sleep(6)
        assert fixture.pid() == 0
        queue("services/control", {"service": "xray", "action": "start"})
        runtime.poll(
            mode + " explicit start after stopped takeover forwards",
            lambda: runtime.forwards(socks, echo_port),
        )
    print(
        "PASS",
        mode,
        "native multifile merge, exact backups, no-op and provisioning",
        flush=True,
    )
    if not directory_only:
        crash_and_failure_checks(
            work, fixture, xray, client, base, echo_port, port, user, sources, options
        )


def run(args):
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    environment = Path("/opt") / ("open-node-takeover-venv-" + uuid4().hex[:8])
    environment.mkdir(mode=0o755)
    try:
        run_command("python3", "-m", "venv", environment)
        run_command(environment / "bin/pip", "install", str(args.wheel))

        def exercise(work, unused, wheel, xray, client, backend, echo_port):
            with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
                for mode, directory_only in (
                    ("websocket", False),
                    ("http", False),
                    ("http", True),
                ):
                    fixture = external.Fixture(environment / "bin/python")
                    try:
                        exercise_mode(
                            work,
                            fixture,
                            xray,
                            client,
                            echo_port,
                            mode,
                            endpoint,
                            ca,
                            backend,
                            args,
                            directory_only=directory_only,
                        )
                    except BaseException:
                        print("HEALTH", fixture.health(), flush=True)
                        for unit in fixture.units:
                            print(
                                run_command(
                                    "journalctl",
                                    "-u",
                                    unit.name,
                                    "-n",
                                    "40",
                                    "--no-pager",
                                    check=False,
                                ).stdout,
                                flush=True,
                            )
                        raise
                    finally:
                        fixture.cleanup()
                run_command(
                    environment / "bin/pip",
                    "install",
                    "--no-deps",
                    "--force-reinstall",
                    args.wheel,
                )
                for mode in ("websocket", "http"):
                    fixture = external.Fixture(environment / "bin/python")
                    try:
                        external.exercise_mode(
                            work, fixture, xray, client, echo_port, mode, endpoint, ca
                        )
                    finally:
                        fixture.cleanup()

        service.exercise = exercise
        service.run(args.wheel, args.xray_archive)
    finally:
        assert environment.parent == Path("/opt") and environment.name.startswith(
            "open-node-takeover-venv-"
        )
        shutil.rmtree(environment)
    print("PASS independent Xray multifile takeover", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--output", type=Path)
    run(parser.parse_args())
