"""Real remote HTTP-01 over HTTPS/WSS, non-root Agents, Pebble and browser workflows."""

import argparse
import importlib.util
import json
import os
import secrets
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "http_smoke",
    Path(__file__).with_name("smoke-certificate-http.py"),
)
http = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(http)
acme, runtime, service, api, wait_job = (
    http.acme,
    http.runtime,
    http.service,
    http.api,
    http.wait_job,
)


class Gate:
    def __init__(self, stack):
        self.target = None
        self.reject = False
        self.hold = False
        self.after = False
        self.entered, self.release = threading.Event(), threading.Event()
        self.successes = []
        gate = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if gate.hold and not gate.after:
                    gate.entered.set()
                    gate.release.wait(90)
                try:
                    with httpx.Client(trust_env=False, timeout=5) as client:
                        response = client.get(
                            f"http://127.0.0.1:{gate.target}" + self.path,
                            headers={"Host": self.headers["Host"]},
                        )
                        status, data = response.status_code, response.content
                except httpx.TransportError:
                    status, data = 502, b"node offline"
                if gate.reject:
                    status, data = 404, b"fixture rejection"
                if status == 200:
                    gate.successes.append((self.path, data))
                if gate.hold and gate.after:
                    gate.entered.set()
                    gate.release.wait(90)
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        stack.callback(self.server.server_close)
        stack.callback(self.server.shutdown)
        stack.callback(self.release.set)

    def pause(self, after=False):
        self.entered.clear()
        self.release.clear()
        self.after, self.hold = after, True

    def resume(self):
        self.hold = False
        self.release.set()


def clean(client, identifier):
    return runtime.poll(
        "node confirms challenge cleanup",
        lambda: api(client, "certificates/" + identifier),
        lambda value: not any(job["cleanup_pending"] for job in value["jobs"]),
        timeout=50,
    )


def command(client, server, name, body=None):
    queued = api(client, f"servers/{server}/operations/{name}", "POST", body)["command"]
    result = runtime.poll(
        name,
        lambda: next(
            item
            for item in api(client, f"servers/{server}/commands")["commands"]
            if item["id"] == queued["id"]
        ),
        lambda item: item["status"] in {"succeeded", "failed"},
        timeout=45,
    )
    assert result["status"] == "succeeded", result
    return result["result_body"]


def browser_profile(client, url, mode, node, eab, output):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
        )
        context.add_cookies(
            [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "url": url,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
                for cookie in client.cookies.jar
            ]
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url + "/certificates")
            expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
            page.get_by_role("button", name="新建证书", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("证书名称", exact=True).fill(node["name"] + " " + mode)
            dialog.get_by_label("DNS 域名", exact=True).fill("*.acme.test")
            dialog.get_by_label("账户邮箱", exact=True).fill("operator@example.com")
            dialog.get_by_role(
                "combobox", name="验证方式", exact=True
            ).click()
            page.locator(".ant-select-dropdown:visible .ant-select-item-option").get_by_text(
                {"standalone": "HTTP-01 / 独立服务", "webroot": "HTTP-01 / 网站根目录"}[mode],
                exact=True,
            ).click()
            dialog.get_by_role(
                "combobox", name="验证主机", exact=True
            ).click()
            page.locator(".ant-select-dropdown:visible .ant-select-item-option").get_by_text(
                node["name"], exact=True
            ).click()
            submit = dialog.get_by_role("button", name="创建证书", exact=True)
            expect(submit).to_be_disabled()
            expect(dialog.get_by_text("通配符域名需要使用 DNS-01", exact=True)).to_be_visible()
            dialog.get_by_label("DNS 域名", exact=True).fill("edge.acme.test")
            dialog.get_by_label("自动续签", exact=True).uncheck()
            dialog.get_by_role("button", name="外部账户绑定", exact=True).click()
            dialog.get_by_label("EAB 密钥 ID", exact=True).fill(eab[0])
            dialog.get_by_label("EAB HMAC 密钥", exact=True).fill(eab[1])
            dialog.get_by_role("button", name="外部账户绑定", exact=True).click()
            expect(submit).to_be_disabled()
            dialog.get_by_label("我接受此 CA 的服务条款", exact=True).check()
            expect(submit).to_be_enabled()
            for label, width, height in (
                ("desktop", 1440, 1000),
                ("mobile", 390, 844),
                ("narrow", 320, 780),
            ):
                page.set_viewport_size({"width": width, "height": height})
                http.ui.check_layout(page)
                expect(submit).to_be_in_viewport(ratio=1)
                page.screenshot(path=output / f"{node['name']}-{mode}-{label}.png")
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST" and response.url.endswith("/certificates")
                ),
            ) as response:
                submit.click()
            assert response.value.status == 201, response.value.text()
            profile = response.value.json()
            assert profile["challenge_type"] == mode
            assert profile["validation_server_id"] == node["id"]
            expect(dialog).not_to_be_visible()
            row = page.get_by_role("row").filter(
                has=page.get_by_role("button", name=profile["name"], exact=True)
            )
            expect(row).to_have_count(1)
            row.get_by_role("button", name="签发证书", exact=True).click()
            detail = wait_job(client, profile["id"])
            row.get_by_role("button", name=profile["name"], exact=True).click()
            expect(page.get_by_role("button", name="立即续签", exact=True)).to_be_enabled()
            for label, width, height in (
                ("desktop", 1440, 1000),
                ("mobile", 390, 844),
                ("narrow", 320, 780),
            ):
                page.set_viewport_size({"width": width, "height": height})
                http.ui.check_layout(page)
                page.screenshot(path=output / f"{node['name']}-{mode}-{label}-issued.png")
            assert not errors, errors
            return detail
        except BaseException:
            page.screenshot(path=output / "failure.png", full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def run(args):
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-node-remote-http-smoke-") as directory:
        work = Path(directory)
        with ExitStack() as stack:
            gate = Gate(stack)
            dns = acme.DNSFixture()
            dns.start(stack)
            cert, key = acme.https_identity(work)
            ca_port, management_port = runtime.free_port(), runtime.free_port()
            ca_url = f"https://127.0.0.1:{ca_port}/dir"
            eab = ("open-node-remote", secrets.token_urlsafe(32))
            ca_config = work / "pebble.json"
            runtime.write_private(
                ca_config,
                {
                    "pebble": {
                        "listenAddress": f"127.0.0.1:{ca_port}",
                        "managementListenAddress": f"127.0.0.1:{management_port}",
                        "certificate": str(cert),
                        "privateKey": str(key),
                        "httpPort": gate.server.server_port,
                        "tlsPort": runtime.free_port(),
                        "certificateValidityPeriod": 240,
                        "retryAfter": {"authz": 1, "order": 1},
                        "externalAccountBindingRequired": True,
                        "externalAccountMACKeys": {eab[0]: eab[1]},
                    }
                },
            )
            stack.enter_context(
                runtime.process(
                    work,
                    "pebble",
                    [
                        str(args.pebble),
                        "-config",
                        str(ca_config),
                        "-dnsserver",
                        "127.0.0.1:53",
                    ],
                    env={
                        "PATH": os.defpath,
                        "PEBBLE_VA_NOSLEEP": "1",
                        "PEBBLE_WFE_NONCEREJECT": "0",
                        "PEBBLE_AUTHZREUSE": "0",
                    },
                )
            )
            trust_context = ssl.create_default_context(cafile=str(cert))
            ca_client = stack.enter_context(httpx.Client(verify=trust_context, trust_env=False))
            runtime.poll(
                "TLS-verified isolated CA", lambda: ca_client.get(ca_url).status_code == 200
            )
            trust = ca_client.get(f"https://127.0.0.1:{management_port}/roots/0").text
            password = secrets.token_urlsafe(32)
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "backend/app"),
                "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'db.sqlite'}",
                "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
                "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "vault"),
                "OPEN_NODE_CERTIFICATE_CA_FILE": str(cert),
                "OPEN_NODE_CERTIFICATE_ACME_DIRECTORIES": json.dumps([ca_url]),
                "OPEN_NODE_CERTIFICATE_POLL_SECONDS": "1",
                "OPEN_NODE_FRONTEND_DIR": str(ROOT / "frontend/dist"),
            }
            for name in (
                "OPEN_NODE_CERTIFICATE_LEGO_BINARY",
                "OPEN_NODE_CERTIFICATE_HTTP_ADDRESS",
                "OPEN_NODE_CERTIFICATE_WEBROOTS",
            ):
                env.pop(name, None)
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                input=password + "\n",
                text=True,
                check=True,
                capture_output=True,
                env=env,
                cwd=work,
                timeout=30,
            )
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"https://127.0.0.1:{listener.getsockname()[1]}"

            def start_backend():
                context = runtime.process(
                    work,
                    "backend",
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "open_node.main:app",
                        "--fd",
                        str(listener.fileno()),
                        "--ssl-certfile",
                        str(cert),
                        "--ssl-keyfile",
                        str(key),
                    ],
                    env=env,
                    pass_fds=(listener.fileno(),),
                )
                return context, context.__enter__()

            backend, process = start_backend()
            stack.callback(lambda: backend.__exit__(None, None, None))
            client = stack.enter_context(
                httpx.Client(
                    base_url=url,
                    verify=trust_context,
                    trust_env=False,
                    timeout=10,
                )
            )
            runtime.poll("HTTPS control plane", lambda: client.get("/healthz").status_code == 200)
            login = (
                client.post(
                    "/api/v1/auth/login",
                    json={
                        "username": "admin",
                        "password": password,
                    },
                    headers={"X-Open-Node-Client": "browser"},
                )
                .raise_for_status()
                .json()
            )
            client.headers["X-CSRF-Token"] = login["csrf_token"]
            xray = runtime.download_xray(work, args.xray_archive)
            child_pid = None
            try:
                for transport in ("http", "websocket"):
                    fixture = service.Fixture(work)
                    stack.callback(fixture.cleanup)
                    created = api(client, "servers", "POST", {"name": "remote-" + transport})
                    node = created["server"]
                    direct, web_port = runtime.free_port(), runtime.free_port()
                    http_port, tls_port = runtime.free_port(), runtime.free_port()
                    source, xray_config = (
                        work / f"{transport}.json",
                        work / f"{transport}-xray.json",
                    )
                    runtime.write_private(
                        source,
                        {
                            "master_url": url,
                            "ca_file": str(cert),
                            "token": created["agent_token"],
                            "connection_mode": transport,
                            "heartbeat_seconds": 1,
                            "telemetry_seconds": 1,
                            "poll_seconds": 0.2,
                            "certificate_http_address": f"127.0.0.1:{direct}",
                            "certificate_webroots": ["site"],
                            "nginx_binary": str(args.nginx),
                            "nginx_modules": [str(args.nginx_stream_module)],
                            "nginx_http_port": http_port,
                            "nginx_https_port": tls_port,
                            "nginx_listen_address": "127.0.0.1",
                        },
                    )
                    runtime.write_private(
                        xray_config,
                        {
                            "inbounds": [],
                            "outbounds": [{"protocol": "freedom"}],
                        },
                    )
                    fixture.cli(
                        "install",
                        "--wheel",
                        args.wheel,
                        "--config",
                        source,
                        "--xray-config",
                        xray_config,
                        "--xray",
                        xray,
                    )
                    assert fixture.properties()["User"] != "root"
                    runtime.poll("non-root " + transport + " Agent", fixture.ready)
                    command(client, node["id"], "nginx/install", {"domain": "edge.acme.test"})
                    site = fixture.root / "state/nginx/html/site"
                    command(
                        client,
                        node["id"],
                        "nginx/config-files/write",
                        {
                            "path": "servers/http01.conf",
                            "content": f"server {{ listen 127.0.0.1:{web_port}; "
                            "server_name edge.acme.test; "
                            f"location /.well-known/acme-challenge/ {{ root {site}; }} }}",
                        },
                    )
                    command(
                        client,
                        node["id"],
                        "services/control",
                        {
                            "service": "nginx",
                            "action": "restart",
                        },
                    )
                    runtime.poll(
                        "managed webroot listener is active",
                        lambda web_port=web_port: runtime.port_open(web_port),
                    )
                    runtime.poll(
                        "node HTTP-01 capability arrives",
                        lambda: api(
                            client,
                            "certificates/capabilities",
                        )["validation_nodes"],
                        lambda nodes, node=node: any(item["id"] == node["id"] for item in nodes),
                    )
                    for mode in ("standalone", "webroot"):
                        gate.target = direct if mode == "standalone" else web_port
                        detail = browser_profile(client, url, mode, node, eab, args.screenshots)
                        identifier = detail["certificate"]["id"]
                        base = "certificates/" + identifier
                        clean(client, identifier)
                        original = detail["certificate"]["version_id"]
                        api(client, base + "/renew", "POST", {})
                        wait_job(client, identifier, "skipped")
                        gate.reject = True
                        api(client, base + "/renew", "POST", {"force": True})
                        failed = wait_job(client, identifier, "failed")
                        assert failed["certificate"]["version_id"] == original
                        gate.reject = False
                        clean(client, identifier)
                        api(client, base + "/renew", "POST", {"force": True})
                        renewed = wait_job(client, identifier)
                        assert renewed["certificate"]["version_id"] != original
                        clean(client, identifier)
                        if mode == "standalone":
                            assert not runtime.port_open(direct)
                        else:
                            assert not list((site / ".well-known/acme-challenge").iterdir())
                        print(
                            "PASS "
                            + transport
                            + " "
                            + mode
                            + " issuance, renewal and failure retention",
                            flush=True,
                        )

                    # The response has been read from Nginx; disconnect the node
                    # before the CA receives it, leaving cleanup genuinely pending.
                    gate.pause(after=True)
                    captured_before = len(gate.successes)
                    api(client, base + "/renew", "POST", {"force": True})
                    assert gate.entered.wait(30)
                    runtime.poll(
                        "all CA perspectives read the real node response",
                        lambda captured_before=captured_before: (
                            len(gate.successes) >= captured_before + 3
                        ),
                    )
                    subprocess.run(["systemctl", "stop", fixture.unit], check=True, timeout=30)
                    gate.resume()
                    disconnected = wait_job(client, identifier)
                    assert disconnected["jobs"][0]["cleanup_pending"]
                    assert client.delete("/api/v1/" + base).status_code == 409
                    subprocess.run(["systemctl", "start", fixture.unit], check=True, timeout=30)
                    runtime.poll("node reconnects for deferred cleanup", fixture.ready)
                    clean(client, identifier)
                    assert not list((site / ".well-known/acme-challenge").iterdir())

                    if transport == "http":
                        gate.pause()
                        job = api(client, base + "/renew", "POST", {"force": True})
                        assert gate.entered.wait(30)
                        order_file = work / "vault" / identifier / "jobs" / job["id"] / "order.json"
                        order = order_file.read_bytes()
                        children = (
                            Path(f"/proc/{process.pid}/task/{process.pid}/children")
                            .read_text()
                            .split()
                        )
                        assert len(children) == 1, children
                        child_pid = int(children[0])
                        assert (
                            b"certificate_remote_acme"
                            in Path(f"/proc/{child_pid}/cmdline").read_bytes()
                        )
                        os.kill(process.pid, signal.SIGKILL)
                        backend.__exit__(None, None, None)
                        backend, process = start_backend()
                        runtime.poll(
                            "controller restarts while ACME child survives",
                            lambda: client.get("/healthz").status_code == 200,
                        )
                        time.sleep(2)
                        assert api(client, base)["jobs"][0]["status"] == "running"
                        os.killpg(child_pid, signal.SIGKILL)
                        child_pid = None

                        def fresh_lease(job=job):
                            with sqlite3.connect(work / "db.sqlite") as db:
                                return (
                                    db.execute(
                                        "SELECT COUNT(*) FROM certificate_http_leases "
                                        "WHERE job_id=? AND cleanup_requested=0",
                                        (job["id"],),
                                    ).fetchone()[0]
                                    == 1
                                    and db.execute(
                                        "SELECT COUNT(*) FROM certificate_http_leases "
                                        "WHERE job_id=?",
                                        (job["id"],),
                                    ).fetchone()[0]
                                    == 2
                                )

                        runtime.poll(
                            "same order receives a fresh lease after crash", fresh_lease, timeout=45
                        )
                        gate.resume()
                        recovered = wait_job(client, identifier)
                        assert recovered["jobs"][0]["id"] == job["id"]
                        assert order_file.read_bytes() == order
                        clean(client, identifier)
                        print(
                            "PASS inherited lock, durable order and fresh-lease crash recovery",
                            flush=True,
                        )

                    current = api(client, base)
                    target = api(
                        client,
                        base + "/targets",
                        "POST",
                        {
                            "server_id": node["id"],
                            "domain": "edge.acme.test",
                            "cert_name": "edge.acme.test",
                            "reload": "nginx",
                            "auto_deploy": True,
                        },
                    )
                    runtime.poll(
                        "signed certificate deploys to its validation node",
                        lambda base=base: api(
                            client,
                            base,
                        )["targets"],
                        lambda targets, target=target: any(
                            item["id"] == target["id"] and item["status"] == "succeeded"
                            for item in targets
                        ),
                    )
                    command(client, node["id"], "nginx/setup-ssl", {"domain": "edge.acme.test"})
                    serial = current["versions"][0]["details"]["serial"]
                    runtime.poll(
                        "trusted TLS serves the issued certificate",
                        lambda tls_port=tls_port: acme.tls_read(tls_port, trust),
                        lambda value, serial=serial: value == serial,
                    )
                    if transport == "websocket":
                        account_dir = work / "vault" / identifier / "accounts"
                        account_file = next(account_dir.rglob("account.json"))
                        key_file = next(account_dir.rglob("*.key"))
                        previous_account, previous_key = (
                            json.loads(account_file.read_text()),
                            key_file.read_bytes(),
                        )
                        api(
                            client,
                            base + "/account",
                            "POST",
                            {
                                "email": "updated@example.com",
                                "eab_action": "keep",
                            },
                        )
                        account_detail = wait_job(client, identifier)
                        assert account_detail["account"]["email"] == "updated@example.com"
                        assert (
                            json.loads(account_file.read_text())["registration"]["uri"]
                            == previous_account["registration"]["uri"]
                        )
                        assert key_file.read_bytes() == previous_key
                        before_version = account_detail["certificate"]["version_id"]
                        api(
                            client,
                            base,
                            "PATCH",
                            {
                                "name": account_detail["certificate"]["name"],
                                "auto_renew": True,
                            },
                        )
                        renewed = runtime.poll(
                            "elapsed-time remote automatic renewal",
                            lambda base=base: api(
                                client,
                                base,
                            ),
                            lambda value, before_version=before_version: (
                                not value["certificate"]["active_job_id"]
                                and value["certificate"]["version_id"] != before_version
                            ),
                            timeout=160,
                        )
                        clean(client, identifier)
                        api(
                            client,
                            base,
                            "PATCH",
                            {
                                "name": renewed["certificate"]["name"],
                                "auto_renew": False,
                            },
                        )
                        new_serial = renewed["versions"][0]["details"]["serial"]
                        runtime.poll(
                            "automatic deployment updates live TLS after remote renewal",
                            lambda tls_port=tls_port: acme.tls_read(tls_port, trust),
                            lambda value, new_serial=new_serial: value == new_serial,
                            timeout=45,
                        )
                    api(client, base + "/targets/" + target["id"], "DELETE")
                    # Only public challenge responses reached the node before
                    # the separately requested TLS deployment.
                    for path in (fixture.root / "state").rglob("*"):
                        assert not (
                            path.is_file()
                            and path.name in {"account.json", "certificate.key", "order.json"}
                        )
                    assert (
                        fixture.root / "state/nginx/html/index.html"
                    ).read_text() == "Open Node\n"
                assert len(gate.successes) >= 12
                for path in (work / "vault").rglob("*"):
                    assert not path.stat().st_mode & 0o077, path
                print(
                    "PASS remote HTTP-01, EAB, HTTPS/WSS, cleanup recovery and responsive UI",
                    flush=True,
                )
            except BaseException:
                gate.resume()
                if child_pid:
                    with suppress(ProcessLookupError):
                        os.killpg(child_pid, signal.SIGKILL)
                for path in [
                    work / "backend.log",
                    work / "pebble.log",
                    *(work / "vault").rglob("last-job.log"),
                ]:
                    if path.is_file():
                        print(
                            path.read_text()[-6000:]
                            .replace(password, "[redacted]")
                            .replace(eab[1], "[redacted]"),
                            file=sys.stderr,
                        )
                raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pebble", "wheel", "nginx", "nginx-stream-module", "screenshots"):
        parser.add_argument("--" + name, required=True, type=lambda value: Path(value).resolve())
    parser.add_argument("--xray-archive", type=lambda value: Path(value).resolve())
    run(parser.parse_args())
