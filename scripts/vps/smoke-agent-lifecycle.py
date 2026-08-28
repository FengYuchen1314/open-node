"""Real remote Agent lifecycle with trusted fixture HTTPS and non-root Agents."""

import argparse
import base64
import csv
import email.parser
import email.policy
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import sqlite3
import ssl
import subprocess
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from playwright.sync_api import expect, sync_playwright


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


service = module("remote_service_smoke", "smoke-agent-service.py")
runtime = service.runtime
nginx_fixture = module("remote_nginx_smoke", "smoke-nginx.py")
ui = module("remote_lifecycle_ui", "smoke-operator-ui.py")
ROOT = Path(__file__).resolve().parents[2]


def release_wheel(
    source, directory, version, *, variant="good", init_suffix="", main_prefix=""
):
    directory.mkdir(parents=True)
    candidate = service.variant_wheel(source, directory, variant)
    with zipfile.ZipFile(candidate) as archive:
        original = {name: archive.read(name) for name in archive.namelist()}
    metadata_name = next(
        name for name in original if name.endswith(".dist-info/METADATA")
    )
    old_prefix = metadata_name.rsplit("/", 1)[0]
    new_prefix = "open_node_agent-" + version + ".dist-info"
    files = {
        new_prefix + name[len(old_prefix) :]
        if name.startswith(old_prefix + "/")
        else name: data
        for name, data in original.items()
    }
    metadata_name = new_prefix + "/METADATA"
    metadata = email.parser.BytesParser(policy=email.policy.compat32).parsebytes(
        files[metadata_name]
    )
    metadata.replace_header("Version", version)
    files[metadata_name] = metadata.as_bytes()
    files["open_node_agent/__init__.py"] += (
        "\n__version__ = " + repr(version) + "\n" + init_suffix
    ).encode()
    files["open_node_agent/__main__.py"] = (
        main_prefix.encode() + files["open_node_agent/__main__.py"]
    )
    record_name = new_prefix + "/RECORD"
    rows = io.StringIO()
    writer = csv.writer(rows, lineterminator="\n")
    for name, data in files.items():
        if name != record_name:
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .decode()
                .rstrip("=")
            )
            writer.writerow([name, "sha256=" + digest, len(data)])
    writer.writerow([record_name, "", ""])
    files[record_name] = rows.getvalue().encode()
    target = directory / f"open_node_agent-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return target


class Releases(BaseHTTPRequestHandler):
    def do_GET(self):
        data = self.server.assets.get(self.path)
        if data is None:
            self.send_error(404)
            return
        if isinstance(data, str):
            self.send_response(302)
            self.send_header("Location", data)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@contextmanager
def release_server(work):
    cert, key, _ = nginx_fixture.certificate()
    certificate = work / "release-ca.pem"
    private = work / "release-key.pem"
    certificate.write_text(cert)
    private.write_text(key)
    private.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Releases)
    server.assets = {}
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            server.assets,
            f"https://localhost:{server.server_port}/releases/download",
            certificate,
        )
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


@contextmanager
def gateway(work, nginx, backend):
    directory = work / "controller-proxy"
    directory.mkdir()
    (directory / "logs").mkdir()
    cert, key, _ = nginx_fixture.certificate()
    certificate = directory / "cert.pem"
    private = directory / "key.pem"
    certificate.write_text(cert)
    private.write_text(key)
    private.chmod(0o600)
    blocked = directory / "block-reports"
    port = runtime.free_port()
    configuration = directory / "nginx.conf"
    headers = f"""
        proxy_pass {backend};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    """
    configuration.write_text(f"""
user root;
pid {directory}/nginx.pid;
error_log {directory}/error.log;
events {{}}
http {{
    access_log off;
    map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
    client_body_temp_path {directory}/body;
    proxy_temp_path {directory}/proxy;
    fastcgi_temp_path {directory}/fastcgi;
    uwsgi_temp_path {directory}/uwsgi;
    scgi_temp_path {directory}/scgi;
    server {{
        listen 127.0.0.1:{port} ssl;
        ssl_certificate {certificate};
        ssl_certificate_key {private};
        location /api/v1/agents/commands/by-request/ {{
            if (-f {blocked}) {{ return 503; }}
            {headers}
        }}
        location / {{ {headers} }}
    }}
}}
""")
    subprocess.run(
        [str(nginx), "-p", str(directory), "-c", str(configuration), "-t"],
        capture_output=True,
        check=True,
        timeout=10,
    )
    with runtime.process(
        directory,
        "proxy",
        [
            str(nginx),
            "-p",
            str(directory),
            "-c",
            str(configuration),
            "-g",
            "daemon off;",
        ],
    ):
        yield f"https://localhost:{port}", certificate, blocked


def wait_command(client, base, command, expected="succeeded"):
    result = runtime.poll(
        "lifecycle command " + command["id"][:8],
        lambda: next(
            row
            for row in client.get(base + "/commands")
            .raise_for_status()
            .json()["commands"]
            if row["id"] == command["id"]
        ),
        lambda row: row["status"] in {"succeeded", "failed", "skipped"},
        timeout=240,
    )
    assert result["status"] == expected, result
    return result


def queue(client, base, operation, payload=None):
    return (
        client.post(base + "/operations/agent/" + operation, json=payload)
        .raise_for_status()
        .json()["command"]
    )


def helper_properties(fixture):
    unit = fixture.unit.removesuffix(".service") + "-lifecycle.service"
    output = subprocess.check_output(
        ["systemctl", "show", unit, "--property=ActiveState,MainPID"], text=True
    )
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def crash_helper(fixture):
    unit = fixture.unit.removesuffix(".service") + "-lifecycle.service"
    before = helper_properties(fixture)["MainPID"]
    subprocess.run(
        ["systemctl", "kill", "--kill-whom=all", "--signal=SIGKILL", unit], check=True
    )
    runtime.poll(
        "host helper restarts after a cgroup crash",
        lambda: helper_properties(fixture),
        lambda row: (
            row["ActiveState"] == "active" and row["MainPID"] not in {"0", before}
        ),
    )


def command_row(client, base, command):
    return next(
        row
        for row in client.get(base + "/commands").raise_for_status().json()["commands"]
        if row["id"] == command["id"]
    )


def peer_boundary(fixture):
    directory = fixture.root / "lifecycle"
    endpoint = directory / "control.sock"
    try:
        # Widen only the disposable fixture's DAC boundary to exercise SO_PEERCRED separately.
        directory.chmod(0o755)
        endpoint.chmod(0o666)
        result = subprocess.run(
            [
                "runuser",
                "-u",
                "nobody",
                "--",
                "/usr/bin/python3",
                "-c",
                """
import socket, sys
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    connection.settimeout(3)
    connection.connect(sys.argv[1])
    try:
        connection.sendall(b'{"op":"status"}\\n')
        assert connection.recv(4096) == b''
    except (BrokenPipeError, ConnectionResetError):
        pass
""",
                str(endpoint),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    finally:
        endpoint.chmod(0o660)
        directory.chmod(0o750)
    print("PASS foreign Unix peer cannot use the privileged helper", flush=True)


def interrupted_upgrades(directory, fixture, wheel, client, base, assets, verify):
    selected = fixture.record()["current"]
    for version, phase in (("0.2.3", "staging"), ("0.2.4", "switching")):
        private = fixture.root / (
            "lifecycle/private" if phase == "staging" else "state"
        )
        marker, reached = private / (phase + "-pause"), private / (phase + "-reached")
        marker.touch(mode=0o600)
        if phase != "staging":
            os.chown(marker, fixture.record()["uid"], fixture.record()["gid"])
        condition = (
            "'--version' in sys.argv"
            if phase == "staging"
            else ("'--version' not in sys.argv and '--check' not in sys.argv")
        )
        pause = f"""
import sys, time
from pathlib import Path
if {condition} and Path({str(marker)!r}).exists():
    Path({str(marker)!r}).unlink()
    Path({str(reached)!r}).touch()
    time.sleep(120)
"""
        package = release_wheel(
            wheel,
            directory / version,
            version,
            init_suffix=pause if phase == "staging" else "",
            main_prefix=pause if phase == "switching" else "",
        )
        assets[f"/releases/download/agent-v{version}/{package.name}"] = (
            package.read_bytes()
        )
        payload = {
            "version": version,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        }
        dependent = None
        if phase == "staging":
            from open_node.domain.inventory import AgentCommandCreate
            from open_node.services.inventory import InventoryStore

            store = InventoryStore(f"sqlite:///{directory.parent / 'backend.db'}")
            commands = store.create_command_sequence(
                UUID(base.rsplit("/", 1)[1]),
                [
                    AgentCommandCreate(
                        method="POST",
                        path="/api/child/agent/upgrade-stream",
                        body=payload,
                        timeout_ms=300000,
                        stream=True,
                    ),
                    AgentCommandCreate(method="GET", path="/api/child/system/info"),
                ],
            )
            queued, dependent = [
                command.model_dump(mode="json") for command in commands
            ]
            store._engine.dispose()
        else:
            queued = queue(client, base, "upgrade", payload)
        runtime.poll(
            phase + " interrupted at the actual process boundary",
            reached.exists,
            timeout=120,
        )
        assert command_row(client, base, queued)["status"] == "leased"
        assert fixture.record()["staging" if phase == "staging" else "pending"]
        if phase == "staging":
            assert command_row(client, base, dependent)["status"] == "waiting"
            with sqlite3.connect(directory.parent / "backend.db") as database:
                database.execute(
                    "UPDATE agent_commands SET leased_at='2000-01-01 00:00:00' WHERE id=?",
                    (queued["id"],),
                )
            runtime.poll(
                "expired lifecycle lease is redelivered without completion",
                lambda command=queued: command_row(client, base, command)["attempts"] >= 2,
            )
            assert command_row(client, base, dependent)["status"] == "waiting"
            wire = {
                key: queued[key]
                for key in ("request_id", "method", "path", "query", "body", "stream")
            }
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(str(fixture.root / "lifecycle/control.sock"))
                connection.sendall(
                    json.dumps({"op": "submit", "command": wire}).encode() + b"\n"
                )
                with connection.makefile("rb") as source:
                    assert json.loads(source.readline()) == {"ok": True, "result": None}
            with sqlite3.connect(
                fixture.root / "lifecycle/private/jobs.sqlite"
            ) as database:
                count = database.execute(
                    "SELECT count(*) FROM jobs WHERE request_id=?",
                    (queued["request_id"],),
                ).fetchone()[0]
            assert count == 1
            verify()
        crash_helper(fixture)
        outcome = wait_command(client, base, queued, "failed")
        assert "interrupted" in outcome["result_error"]
        assert not outcome["result_body"]["recovery_required"]
        assert fixture.record()["current"] == selected
        assert not fixture.record().get("staging") and not fixture.record()["pending"]
        if dependent:
            wait_command(client, base, dependent, "skipped")
            assert command_row(client, base, dependent)["attempts"] == 0
        verify()
        if phase == "staging":
            wait_command(client, base, queue(client, base, "upgrade", payload))
            verify()
            wait_command(
                client, base, queue(client, base, "rollback", {"confirm": True})
            )
            assert fixture.record()["current"] == selected
    print(
        "PASS staging/switch crashes recover, deduplicate and permit an explicit retry",
        flush=True,
    )


def interrupted_removal(directory, fixture, wheel, client, base, assets, verify):
    marker, reached = (
        fixture.root / "state/stop-pause",
        fixture.root / "state/stop-reached",
    )
    prefix = f"""
import sys
if '--check' not in sys.argv and '--version' not in sys.argv:
    import asyncio
    from pathlib import Path
    from open_node_agent.client import Agent
    original_close = Agent.close
    async def delayed_close(self):
        if Path({str(marker)!r}).exists():
            Path({str(marker)!r}).unlink()
            Path({str(reached)!r}).touch()
            await asyncio.sleep(8)
        await original_close(self)
    Agent.close = delayed_close
"""
    package = release_wheel(wheel, directory / "0.2.5", "0.2.5", main_prefix=prefix)
    assets[f"/releases/download/agent-v0.2.5/{package.name}"] = package.read_bytes()
    wait_command(
        client,
        base,
        queue(
            client,
            base,
            "upgrade",
            {
                "version": "0.2.5",
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            },
        ),
    )
    verify()
    marker.touch(mode=0o600)
    os.chown(marker, fixture.record()["uid"], fixture.record()["gid"])
    removal = queue(client, base, "uninstall", {"confirm": True})
    runtime.poll("removal is durably recorded before the Agent stops", reached.exists)
    assert fixture.record()["status"] == "removing"
    assert command_row(client, base, removal)["status"] == "leased"
    crash_helper(fixture)
    result = wait_command(client, base, removal, "failed")
    assert "interrupted" in result["result_error"]
    assert result["result_body"]["installation_status"] == "removed"
    assert not result["result_body"]["recovery_required"]
    runtime.poll(
        "recovered uninstall report is acknowledged",
        lambda: helper_properties(fixture)["ActiveState"] == "inactive",
    )
    fixture.cli("install", "--wheel", wheel)
    verify()
    print(
        "PASS interrupted removal completes cleanup with an explicit recovered outcome",
        flush=True,
    )


@contextmanager
def browser_panel(client, endpoint, ca, name):
    certificate = x509.load_pem_x509_certificate(ca.read_bytes())
    public = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    pin = base64.b64encode(hashlib.sha256(public).digest()).decode()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--ignore-certificate-errors-spki-list=" + pin]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            context.add_cookies(
                [
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "url": endpoint,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for cookie in client.cookies.jar
                ]
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(endpoint)
            target = page.get_by_label("Target server", exact=True)
            target.scroll_into_view_if_needed()
            target.press("Enter")
            page.get_by_role("option", name=re.compile(re.escape(name))).click()
            yield page
            assert not errors, errors
        finally:
            context.close()
            browser.close()


def browser_upgrades(page, client, base, payload, output, mode, verify):
    for label, width, height in (
        ("desktop", 1440, 900),
        ("mobile", 390, 844),
        ("narrow", 320, 740),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.get_by_role("button", name="Upgrade Agent", exact=True).click()
        dialog = page.get_by_role("dialog")
        version = dialog.get_by_label("Agent version", exact=True)
        expect(version).to_be_visible(timeout=30000)
        version.fill("../../invalid")
        checksum = dialog.get_by_label("Wheel SHA-256", exact=True)
        checksum.fill(payload["sha256"])
        confirmed = dialog.get_by_label("Confirm Agent restart", exact=True)
        confirmed.check()
        submit = dialog.get_by_role("button", name="Upgrade", exact=True)
        expect(submit).to_be_disabled()
        version.fill(payload["version"])
        confirmed.uncheck()
        expect(submit).to_be_disabled()
        confirmed.check()
        ui.check_layout(page)
        page.wait_for_function(
            "el => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1",
            arg=checksum.element_handle(),
        )
        page.screenshot(
            path=output / f"{mode}-{label}-upgrade.png", animations="disabled"
        )
        with page.expect_response(
            lambda response: response.url.endswith(base + "/operations/agent/upgrade")
        ) as response:
            submit.click()
        command = response.value.json()["command"]
        assert command["body"] == payload
        if label == "desktop":
            dialog.get_by_role("button", name="Close", exact=True).click()
            page.get_by_role("button", name="Upgrade Agent", exact=True).click()
        wait_command(client, base, command)
        expect(dialog.get_by_text("Completed", exact=True)).to_be_visible(timeout=30000)
        dialog.get_by_role("button", name="Close", exact=True).click()
        verify()
        page.get_by_role("button", name="Roll back Agent", exact=True).click()
        confirm = dialog.get_by_label("Confirm Agent restart", exact=True)
        expect(confirm).to_be_visible(timeout=30000)
        rollback = dialog.get_by_role("button", name="Roll back", exact=True)
        expect(rollback).to_be_disabled()
        confirm.check()
        ui.check_layout(page)
        page.screenshot(
            path=output / f"{mode}-{label}-rollback.png", animations="disabled"
        )
        with page.expect_response(
            lambda response: response.url.endswith(base + "/operations/agent/rollback")
        ) as response:
            rollback.click()
        wait_command(client, base, response.value.json()["command"])
        expect(dialog.get_by_text("Completed", exact=True)).to_be_visible(timeout=30000)
        dialog.get_by_role("button", name="Close", exact=True).click()
        verify()
    print(
        "PASS "
        + mode
        + " desktop/mobile confirmed upgrades, rollback and reopened progress",
        flush=True,
    )


def exercise_mode(
    work,
    fixture,
    wheel,
    xray,
    client,
    echo,
    mode,
    endpoint,
    ca,
    assets,
    release_base,
    release_ca,
    blocked,
    output,
):
    directory = work / mode
    directory.mkdir()
    name = "remote-agent-" + mode
    created = (
        client.post("/api/v1/servers", json={"name": name}).raise_for_status().json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    port, user = runtime.free_port(), str(uuid4())
    config = directory / "agent.json"
    xray_config = directory / "xray.json"
    runtime.write_private(
        config,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
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
                        "clients": [{"id": user, "email": "lifecycle-user"}],
                    },
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        },
    )
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
    original = fixture.record()["current"]
    original_xray = (fixture.root / "config/xray.json").read_bytes()
    fixture.cli(
        "enable-remote", "--release-base-url", release_base, "--release-ca", release_ca
    )
    assert fixture.ready()
    assert helper_properties(fixture)["ActiveState"] == "active"
    account = service.pwd.getpwnam(fixture.user)
    socket_path = fixture.root / "lifecycle/control.sock"
    assert socket_path.stat().st_uid == 0
    assert socket_path.stat().st_gid == account.pw_gid
    assert socket_path.stat().st_mode & 0o777 == 0o660
    assert (fixture.root / "lifecycle/private").stat().st_mode & 0o777 == 0o700
    source_config = (fixture.root / "config/agent.json").read_bytes()
    peer_boundary(fixture)

    def verify():
        assert fixture.ready()
        assert (fixture.root / "config/xray.json").read_bytes() == original_xray
        assert (fixture.root / "config/agent.json").read_bytes() == source_config
        assert (fixture.root / "runtime/xray").stat().st_uid == 0
        with runtime.proxy_client(directory, xray, port, user) as socks:
            runtime.poll(
                "VLESS forwards after remote Agent deployment",
                lambda: runtime.forwards(socks, echo),
            )

    packages = {}
    for version, variant in (
        ("0.2.0", "good"),
        ("0.2.1", "preflight-failure"),
        ("0.2.2", "startup-failure"),
    ):
        package = release_wheel(wheel, directory / version, version, variant=variant)
        assets[f"/releases/download/agent-v{version}/{package.name}"] = (
            package.read_bytes()
        )
        packages[version] = {
            "version": version,
            "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        }
    status = wait_command(client, base, queue(client, base, "lifecycle"))
    assert status["result_body"]["current"]["id"] == original
    verify()
    upgraded = wait_command(
        client, base, queue(client, base, "upgrade", packages["0.2.0"])
    )
    assert upgraded["result_body"]["current"]["version"] == "0.2.0"
    verify()
    wait_command(client, base, queue(client, base, "rollback", {"confirm": True}))
    assert fixture.record()["current"] == original
    verify()
    wait_command(client, base, queue(client, base, "upgrade", packages["0.2.0"]))
    selected = fixture.record()["current"]
    for payload in (
        {**packages["0.2.0"], "sha256": "0" * 64},
        packages["0.2.1"],
        packages["0.2.2"],
    ):
        wait_command(client, base, queue(client, base, "upgrade", payload), "failed")
        assert fixture.record()["current"] == selected
        assert fixture.record()["pending"] is None
        verify()
    for version, asset, message in (
        (
            "0.2.8",
            assets[
                "/releases/download/agent-v0.2.0/open_node_agent-0.2.0-py3-none-any.whl"
            ],
            "version does not match",
        ),
        ("0.2.9", "https://not-approved.invalid/agent.whl", "redirect left"),
    ):
        assets[
            f"/releases/download/agent-v{version}/open_node_agent-{version}-py3-none-any.whl"
        ] = asset
        digest = (
            hashlib.sha256(asset).hexdigest() if isinstance(asset, bytes) else "b" * 64
        )
        result = wait_command(
            client,
            base,
            queue(
                client,
                base,
                "upgrade",
                {
                    "version": version,
                    "sha256": digest,
                },
            ),
            "failed",
        )
        assert message in result["result_error"], result
        assert fixture.record()["current"] == selected
        verify()
    print(
        "PASS "
        + mode
        + " checksum-pinned upgrade, explicit rollback and failed-candidate recovery",
        flush=True,
    )

    interrupted_upgrades(directory, fixture, wheel, client, base, assets, verify)

    package = directory / "0.2.3/open_node_agent-0.2.3-py3-none-any.whl"
    with browser_panel(client, endpoint, ca, name) as page:
        browser_upgrades(
            page,
            client,
            base,
            {
                "version": "0.2.3",
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            },
            output,
            mode,
            verify,
        )

    with browser_panel(client, endpoint, ca, name) as page:
        page.set_viewport_size({"width": 320, "height": 740})
        page.get_by_role("button", name="Uninstall Agent", exact=True).click()
        dialog = page.get_by_role("dialog")
        confirm = dialog.get_by_label("Confirm Agent removal", exact=True)
        expect(confirm).to_be_visible(timeout=30000)
        submit = dialog.get_by_role("button", name="Uninstall", exact=True)
        expect(submit).to_be_disabled()
        confirm.check()
        ui.check_layout(page)
        page.screenshot(
            path=output / f"{mode}-narrow-uninstall.png", animations="disabled"
        )
        blocked.touch()
        with page.expect_response(
            lambda response: response.url.endswith(base + "/operations/agent/uninstall")
        ) as response:
            submit.click()
        removal = response.value.json()["command"]
        assert removal["body"] == {"confirm": True}
        runtime.poll(
            "Agent service is removed before final callback",
            lambda: fixture.record()["status"] == "removed",
        )
        assert not runtime.port_open(port)
        assert (fixture.root / "config/xray.json").read_bytes() == original_xray
        assert (fixture.root / "state/commands.sqlite").is_file()
        assert helper_properties(fixture)["ActiveState"] == "active"
        assert command_row(client, base, removal)["status"] == "leased"
        expect(dialog.get_by_text("Completed", exact=True)).not_to_be_visible()
        dialog.get_by_role("button", name="Close", exact=True).click()
        page.get_by_role("button", name="Uninstall Agent", exact=True).click()
        expect(dialog.get_by_text("Running", exact=True)).to_be_visible(timeout=30000)
        page.screenshot(
            path=output / f"{mode}-uninstall-awaiting-report.png", animations="disabled"
        )
        crash_helper(fixture)
        assert command_row(client, base, removal)["status"] == "leased"
        blocked.unlink()
        wait_command(client, base, removal)
        expect(dialog.get_by_text("Completed", exact=True)).to_be_visible(timeout=30000)
        page.screenshot(
            path=output / f"{mode}-uninstall-completed.png", animations="disabled"
        )
    servers = client.get("/api/v1/servers").raise_for_status().json()
    assert (
        next(row for row in servers if row["id"] == created["server"]["id"])["status"]
        == "offline"
    )
    runtime.poll(
        "lifecycle helper stops after removal acknowledgment",
        lambda: helper_properties(fixture)["ActiveState"] == "inactive",
    )
    fixture.cli("install", "--wheel", wheel)
    verify()
    assert helper_properties(fixture)["ActiveState"] == "active"
    wait_command(client, base, queue(client, base, "lifecycle"))
    print(
        "PASS "
        + mode
        + " durable final callback, stopped helper and data-preserving reinstallation",
        flush=True,
    )
    interrupted_removal(directory, fixture, wheel, client, base, assets, verify)


def run(wheel, nginx, archive, output):
    output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")

    def exercise(work, first, wheel, xray, client, backend, echo):
        with (
            release_server(work) as (assets, release_base, release_ca),
            gateway(work, nginx, backend) as (endpoint, ca, blocked),
        ):
            for mode in ("websocket", "http"):
                fixture = first if mode == "websocket" else service.Fixture(work)
                try:
                    exercise_mode(
                        work,
                        fixture,
                        wheel,
                        xray,
                        client,
                        echo,
                        mode,
                        endpoint,
                        ca,
                        assets,
                        release_base,
                        release_ca,
                        blocked,
                        output,
                    )
                except BaseException:
                    unit = fixture.unit.removesuffix(".service") + "-lifecycle.service"
                    result = subprocess.run(
                        ["journalctl", "-u", unit, "-n", "80", "--no-pager"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    print(result.stdout, flush=True)
                    raise
                finally:
                    fixture.cleanup()

    service.exercise = exercise
    service.run(wheel, archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(
        args.wheel.resolve(),
        args.nginx.resolve(),
        args.xray_archive.resolve() if args.xray_archive else None,
        args.output.resolve(),
    )
