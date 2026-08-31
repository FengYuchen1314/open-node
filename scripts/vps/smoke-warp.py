"""VPS-only WARP lifecycle with a TLS provider fixture and real WireGuard packets."""

import argparse
import base64
import importlib.util
import json
import os
import re
import ssl
import subprocess
import threading
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from playwright.sync_api import expect

SPEC = importlib.util.spec_from_file_location(
    "warp_lifecycle_fixture", Path(__file__).with_name("smoke-agent-lifecycle.py")
)
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)
service, runtime = lifecycle.service, lifecycle.runtime
ROOT = Path(__file__).resolve().parents[2]


def network_state():
    commands = (
        ["ip", "-j", "route", "show", "table", "all"],
        ["ip", "-j", "-6", "route", "show", "table", "all"],
        ["ip", "-j", "link", "show"],
    )
    results = []
    for command in commands:
        value = json.loads(subprocess.check_output(command, text=True, timeout=10))
        if "link" in command:
            value = [item["ifname"] for item in value]
        results.append(value)
    return results


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def answer(self, code, body=None):
        data = json.dumps(body or {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return self.headers.get("Authorization") == "Bearer " + self.server.token

    def do_POST(self):
        if self.path != "/reg":
            return self.answer(404)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        assert body["tos"] and body["model"] == "open-node-agent"
        assert "license" not in body
        self.server.registrations += 1
        self.server.client_key = body["key"]
        self.server.device = uuid4().hex
        self.server.token = "fixture-provider-" + uuid4().hex
        self.server.start_peer()
        self.answer(200, self.server.response())

    def do_GET(self):
        if not self.authorized():
            return self.answer(403, {"error": self.server.token})
        self.answer(200, self.server.response())

    def do_PUT(self):
        if not self.authorized():
            return self.answer(403)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        assert body == {"license": "fixture-optional-plus"}
        self.server.plus = True
        self.server.start_peer()
        self.answer(200, self.server.response())

    def do_DELETE(self):
        if not self.authorized():
            return self.answer(403)
        if self.server.fail_delete:
            return self.answer(503, {"error": self.server.token})
        self.server.deletions += 1
        self.answer(204)


@contextmanager
def provider(work, xray, echo):
    cert, key, _ = lifecycle.nginx_fixture.certificate()
    certificate, private = work / "provider.pem", work / "provider.key"
    certificate.write_text(cert)
    private.write_text(key)
    private.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.registrations = server.deletions = 0
    server.plus = server.fail_delete = False
    server.token = ""
    with ExitStack() as stack:

        def start_peer():
            key = X25519PrivateKey.generate()
            server.peer_key = base64.b64encode(
                key.public_key().public_bytes_raw()
            ).decode()
            server.peer_port = runtime.free_port()
            path = work / ("peer-" + uuid4().hex + ".json")
            runtime.write_private(
                path,
                {
                    "log": {"loglevel": "warning"},
                    "inbounds": [
                        {
                            "tag": "wireguard",
                            "listen": "127.0.0.1",
                            "port": server.peer_port,
                            "protocol": "wireguard",
                            "settings": {
                                "secretKey": base64.b64encode(
                                    key.private_bytes_raw()
                                ).decode(),
                                "address": ["172.16.0.1/32", "fd00::1/128"],
                                "mtu": 1280,
                                "peers": [
                                    {
                                        "publicKey": server.client_key,
                                        "allowedIPs": ["172.16.0.2/32", "fd00::2/128"],
                                    }
                                ],
                            },
                        }
                    ],
                    "outbounds": [
                        {
                            "protocol": "freedom",
                            "settings": {"redirect": f"127.0.0.1:{echo}"},
                        }
                    ],
                },
            )
            stack.enter_context(
                runtime.process(
                    work, path.stem, [str(xray), "run", "-config", str(path)]
                )
            )

        server.start_peer = start_peer
        server.response = lambda: {
            "id": server.device,
            "token": server.token,
            "account": {
                "license": "free-issued-fixture",
                "account_type": "unlimited" if server.plus else "free",
            },
            "config": {
                "client_id": "AQID",
                "interface": {"addresses": {"v4": "172.16.0.2", "v6": "fd00::2"}},
                "peers": [
                    {
                        "public_key": server.peer_key,
                        "endpoint": {"host": f"127.0.0.1:{server.peer_port}"},
                    }
                ],
            },
        }
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, f"https://localhost:{server.server_port}", cert
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()


def operation(client, base, action, body=None, expected="succeeded"):
    queued = (
        client.post(base + "/operations/" + action, json=body or {})
        .raise_for_status()
        .json()
    )
    return lifecycle.wait_command(client, base, queued["command"], expected=expected)


def command(client, base, path, body=None, expected="succeeded"):
    queued = (
        client.post(
            base + "/commands",
            json={
                "method": "POST",
                "path": path,
                "body": body or {},
                "timeout_ms": 60000,
            },
        )
        .raise_for_status()
        .json()
    )
    return lifecycle.wait_command(client, base, queued["command"], expected=expected)


def forwards(socks, address):
    response = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--noproxy",
            "",
            "--socks5-hostname",
            f"127.0.0.1:{socks}",
            "--max-time",
            "4",
            f"http://{address}:18080/fixture",
        ],
        capture_output=True,
        check=False,
        timeout=7,
    )
    return response.returncode == 0 and response.stdout == runtime.RESPONSE_BODY


def browser_install(client, base, endpoint, ca, name, output, mode):
    with lifecycle.browser_panel(client, endpoint, ca, name) as page:
        for label, width, height in (
            ("desktop", 1440, 1000),
            ("mobile", 390, 844),
            ("narrow", 320, 780),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_role("button", name="安装 WARP", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(
                dialog.get_by_role("button", name="安装", exact=True)
            ).to_be_disabled()
            expect(
                dialog.get_by_role("link", name="Cloudflare 应用条款")
            ).to_have_attribute("href", "https://www.cloudflare.com/application/terms/")
            lifecycle.ui.check_layout(page)
            page.screenshot(
                path=output / f"{mode}-{label}-consent.png", animations="disabled"
            )
            if label != "narrow":
                dialog.get_by_role("button", name="取消", exact=True).click()
            else:
                dialog.get_by_role("checkbox").check()
                with page.expect_response(
                    lambda r: (
                        r.request.method == "POST" and r.url.endswith("/warp/install")
                    )
                ) as reply:
                    dialog.get_by_role("button", name="安装", exact=True).click()
                result = lifecycle.wait_command(
                    client, base, reply.value.json()["command"]
                )
                assert result["result_body"]["installed"]
                panel = lifecycle.expanded_command_panel(page, result["id"])
                expect(panel.get_by_text("WARP 免费版", exact=True)).to_be_visible(
                    timeout=30000
                )
                for size, w, h in (
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                    ("narrow", 320, 780),
                ):
                    page.set_viewport_size({"width": w, "height": h})
                    panel.scroll_into_view_if_needed()
                    lifecycle.ui.check_layout(page)
                    page.screenshot(
                        path=output / f"{mode}-{size}-status.png", animations="disabled"
                    )
        page.get_by_role("button", name="移除 WARP", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_role("button", name="移除", exact=True)).to_be_disabled()
        dialog.get_by_role("button", name="取消", exact=True).click()


def exercise_mode(work, fixture, wheel, xray, client, echo, endpoint, ca, mode, output):
    directory = work / mode
    directory.mkdir()
    name = "warp-" + mode
    created = (
        client.post("/api/v1/servers", json={"name": name}).raise_for_status().json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    ports, user = [runtime.free_port(), runtime.free_port()], str(uuid4())
    config, xray_config = directory / "agent.json", directory / "xray.json"
    runtime.write_private(
        config,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
            "connection_mode": mode,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 2,
            "poll_seconds": 0.2,
        },
    )
    original = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless" + str(index),
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "vless",
                "settings": {"decryption": "none", "clients": [{"id": user}]},
            }
            for index, port in enumerate(ports)
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    runtime.write_private(xray_config, original)
    with provider(directory, xray, echo) as (cloud, api, certificate):
        # Only the fixture endpoint/CA is injected. The installed Agent and Xray code is unchanged.
        prefix = (
            """import ssl as _ssl
import open_node_agent.warp as _warp
_api = _warp.WarpAPI
"""
            f"_warp.WarpAPI = lambda: _api(base_url={api!r}, "
            f"verify=_ssl.create_default_context(cadata={certificate!r}))\n"
        )
        candidate = lifecycle.release_wheel(
            wheel, directory / "fixture-wheel", "0.2.0", main_prefix=prefix
        )
        fixture.cli(
            "install",
            "--wheel",
            candidate,
            "--config",
            config,
            "--xray-config",
            xray_config,
            "--xray",
            xray,
        )
        runtime.poll("non-root Agent ready over " + mode, fixture.ready)
        process_status = Path("/proc") / fixture.properties()["MainPID"] / "status"
        mask = int(
            re.search(r"CapEff:\s+([0-9a-f]+)", process_status.read_text())[1], 16
        )
        assert not mask & (1 << 12), "Agent acquired CAP_NET_ADMIN"
        declined = command(client, base, "/api/child/warp/install", expected="failed")
        assert "terms" in declined["result_error"] and cloud.registrations == 0
        browser_install(client, base, endpoint, ca, name, output, mode)
        assert cloud.registrations == 1
        state_path = fixture.root / "state/warp.json"
        state = json.loads(state_path.read_text())
        assert state_path.stat().st_mode & 0o777 == 0o600
        assert state_path.stat().st_uid != 0
        runtime.poll(
            "WARP heartbeat records current installation",
            lambda: client.get("/api/v1/agents").raise_for_status().json(),
            lambda rows: any(
                row["server_id"] == created["server"]["id"] and row["warp_installed"]
                for row in rows
            ),
        )

        routed = json.loads((fixture.root / "config/xray.json").read_text())
        routed["routing"] = {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["vless" + str(index)],
                    "outboundTag": tag,
                }
                for index, tag in enumerate(("warp-v4", "warp-v6"))
            ]
        }
        command(client, base, "/api/child/xray/config", {"config": routed})
        operation(
            client, base, "services/control", {"service": "xray", "action": "restart"}
        )

        def traffic():
            for index, target in enumerate(("192.0.2.123", "[2001:db8::123]")):
                with runtime.proxy_client(directory, xray, ports[index], user) as socks:
                    runtime.poll(
                        mode + " encrypted WireGuard IPv" + str(4 + index * 2),
                        lambda target=target: forwards(socks, target),
                    )

        traffic()
        denied = operation(
            client, base, "warp/remove", {"confirm": True}, expected="failed"
        )
        assert "references" in denied["result_error"] and cloud.deletions == 0
        traffic()
        operation(client, base, "warp/install")
        assert cloud.registrations == 1
        traffic()
        operation(client, base, "warp/license", {"license": "fixture-optional-plus"})
        assert (
            json.loads(state_path.read_text())["config"]["account_type"] == "unlimited"
        )
        traffic()
        subprocess.run(["systemctl", "restart", fixture.unit], check=True, timeout=15)
        runtime.poll("WARP state survives Agent restart", fixture.ready)
        traffic()
        current = json.loads((fixture.root / "config/xray.json").read_text())
        current.pop("routing")
        command(client, base, "/api/child/xray/config", {"config": current})
        cloud.fail_delete = True
        failed = operation(
            client, base, "warp/remove", {"confirm": True}, expected="failed"
        )
        assert "503" in failed["result_error"] and cloud.token not in str(failed)
        status = operation(client, base, "warp/status")["result_body"]
        assert status["phase"] == "removal_pending" and not status["installed"]
        assert (
            len(
                json.loads((fixture.root / "config/xray.json").read_text())["outbounds"]
            )
            == 1
        )
        with runtime.proxy_client(directory, xray, ports[0], user) as socks:
            runtime.poll(
                "unrelated direct traffic survives pending removal",
                lambda: runtime.forwards(socks, echo),
            )
        cloud.fail_delete = False
        operation(client, base, "warp/remove", {"confirm": True})
        assert not state_path.exists() and cloud.deletions == 1
        assert json.loads((fixture.root / "config/xray.json").read_text()) == original
        rows = client.get(base + "/commands").raise_for_status().json()["commands"]
        results = json.dumps(
            [row for row in rows if row["path"].startswith("/api/child/warp/")]
        )
        logs = (fixture.root / "state/agent.log").read_text() + (
            fixture.root / "state/xray.log"
        ).read_text()
        for secret in (state["private_key"], state["access_token"]):
            assert secret not in results and secret not in logs
        print(
            "PASS "
            + mode
            + " WARP state, consent, reapply, optional upgrade, restart, removal recovery",
            flush=True,
        )


def run(args):
    before = network_state()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")

    def exercise(work, first, wheel, xray, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
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
                        endpoint,
                        ca,
                        mode,
                        args.output,
                    )
                finally:
                    fixture.cleanup()

    service.exercise = exercise
    service.run(args.wheel, args.xray_archive)
    assert network_state() == before, "Host routes or interfaces changed"
    print(
        "PASS host routing/interfaces unchanged; no Cloudflare account created by fixture tests",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
