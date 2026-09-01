"""Real CA account edits, version revocation, crash reconciliation and browser QA."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from acme import messages
from open_node.services.certificate_acme import account_paths, connect
from open_node.services.certificate_vault import CertificateVault
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "http_smoke", Path(__file__).with_name("smoke-certificate-http.py")
)
http_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(http_smoke)
acme, runtime, ui = http_smoke.acme, http_smoke.runtime, http_smoke.ui
api, wait_job = acme.api, acme.wait_job


class ResponseGate:
    """Forward to real Pebble, then optionally lose or hold its actual response."""

    def __init__(self, upstream, cert, key):
        self.upstream, self.cert, self.key = upstream, cert, key
        self.drop_prefix = ""
        self.hold_prefix = ""
        self.entered, self.release = threading.Event(), threading.Event()
        self.events = []

    def start(self, stack):
        owner = self
        trust = ssl.create_default_context(cafile=str(self.cert))

        class Handler(BaseHTTPRequestHandler):
            def forward(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with httpx.Client(verify=trust, timeout=15, trust_env=False) as client:
                    response = client.request(
                        self.command,
                        owner.upstream + self.path,
                        content=body,
                        headers={
                            key: value
                            for key, value in self.headers.items()
                            if key.lower() not in {"connection", "content-length"}
                        },
                    )
                owner.events.append((self.command, self.path, response.status_code))
                if (
                    owner.drop_prefix
                    and self.command == "POST"
                    and self.path.startswith(owner.drop_prefix)
                    and response.status_code == 200
                ):
                    owner.drop_prefix = ""
                    self.close_connection = True
                    return
                if (
                    owner.hold_prefix
                    and self.command == "POST"
                    and self.path.startswith(owner.hold_prefix)
                    and response.status_code == 200
                ):
                    owner.hold_prefix = ""
                    owner.entered.set()
                    assert owner.release.wait(30), "Response gate not released"
                self.send_response(response.status_code)
                for key, value in response.headers.items():
                    if key.lower() not in {
                        "content-length",
                        "transfer-encoding",
                        "connection",
                        "content-encoding",
                    }:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                if self.command != "HEAD":
                    with suppress(BrokenPipeError, ConnectionResetError):
                        self.wfile.write(response.content)

            do_GET = do_HEAD = do_POST = forward

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, self.key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        stack.callback(self.release.set)
        return f"https://127.0.0.1:{server.server_port}/dir"


def screenshot(page, output, name):
    for label, width, height in (
        ("desktop", 1440, 1000),
        ("mobile", 390, 844),
        ("narrow", 320, 780),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        ui.check_layout(page)
        dialog = page.get_by_role("dialog")
        if dialog.is_visible():
            bounds = dialog.locator(".ant-modal-footer").bounding_box()
            assert (
                bounds and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
            )
        page.screenshot(path=str(output / f"{name}-{label}.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})


def run(args):
    args.screenshots.mkdir(parents=True, exist_ok=True)
    for binary, expected in ((args.lego, "4.35.2"), (args.pebble, "2.6.0")):
        result = subprocess.run(
            [str(binary), "--version" if binary == args.lego else "-version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        assert expected in result.stdout
    with (
        tempfile.TemporaryDirectory(prefix="open-node-ca-admin-smoke-") as temporary,
        ExitStack() as stack,
    ):
        work = Path(temporary)
        dns = acme.DNSFixture()
        dns.start(stack)
        cert, key = acme.https_identity(work)
        ca_port, management_port, challenge_port = (
            runtime.free_port(),
            runtime.free_port(),
            runtime.free_port(),
        )
        upstream = f"https://127.0.0.1:{ca_port}"
        eab_kid, eab_hmac = "admin-fixture", secrets.token_urlsafe(32)
        config = work / "pebble.json"
        runtime.write_private(
            config,
            {
                "pebble": {
                    "listenAddress": f"127.0.0.1:{ca_port}",
                    "managementListenAddress": f"127.0.0.1:{management_port}",
                    "certificate": str(cert),
                    "privateKey": str(key),
                    "httpPort": challenge_port,
                    "tlsPort": runtime.free_port(),
                    "certificateValidityPeriod": 3600,
                    "externalAccountBindingRequired": True,
                    "externalAccountMACKeys": {eab_kid: eab_hmac},
                    "retryAfter": {"authz": 1, "order": 1},
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
                    str(config),
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
        ca_client = stack.enter_context(
            httpx.Client(
                verify=ssl.create_default_context(cafile=str(cert)),
                trust_env=False,
                timeout=5,
            )
        )
        runtime.poll(
            "real TLS-verified CA",
            lambda: ca_client.get(upstream + "/dir").status_code == 200,
        )
        gate = ResponseGate(upstream, cert, key)
        directory = gate.start(stack)
        password = secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend/app"),
            "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'backend.db'}",
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
            "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "vault"),
            "OPEN_NODE_CERTIFICATE_LEGO_BINARY": str(args.lego),
            "OPEN_NODE_CERTIFICATE_CA_FILE": str(cert),
            "OPEN_NODE_CERTIFICATE_ACME_DIRECTORIES": json.dumps([directory]),
            "OPEN_NODE_CERTIFICATE_HTTP_ADDRESS": f"127.0.0.1:{challenge_port}",
            "OPEN_NODE_CERTIFICATE_POLL_SECONDS": "1",
            "OPEN_NODE_FRONTEND_DIR": str(ROOT / "frontend/dist"),
        }
        subprocess.run(
            [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
            input=password + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
            env=env,
            cwd=work,
        )
        listener = stack.enter_context(socket.socket())
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"

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
                ],
                env=env,
                pass_fds=(listener.fileno(),),
            )
            return context, context.__enter__()

        backend, backend_process = start_backend()
        stack.callback(lambda: backend.__exit__(None, None, None))
        client = stack.enter_context(
            httpx.Client(base_url=url, timeout=10, trust_env=False)
        )
        runtime.poll(
            "administration backend", lambda: client.get("/healthz").status_code == 200
        )
        login = (
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": password},
                headers={"X-Open-Node-Client": "browser"},
            )
            .raise_for_status()
            .json()
        )
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        child_pid = None
        try:
            profile = api(
                client,
                "certificates",
                "POST",
                {
                    "name": "CA administration",
                    "domains": ["edge.acme.test"],
                    "email": "before@example.com",
                    "challenge_type": "standalone",
                    "accept_terms": True,
                    "directory_url": directory,
                    "auto_renew": False,
                },
            )
            identifier, base = profile["id"], "certificates/" + profile["id"]
            api(client, base + "/issue", "POST", {})
            wait_job(client, identifier, "failed")
            vault = CertificateVault(work / "vault")
            account_file, key_file = account_paths(
                vault, vault.root / identifier, directory, "before@example.com"
            )
            assert key_file.exists() and not account_file.exists()
            original_hash = hashlib.sha256(vault.read(key_file)).hexdigest()

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1000}, locale="zh-CN"
                )
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
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def inspect(name="CA administration"):
                    page.goto(url + "/certificates")
                    expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
                    page.get_by_role("button", name=name, exact=True).click()
                    expect(
                        page.locator('.ant-card-head-title:text-is("版本")')
                    ).to_be_visible()

                def edit(email, *, eab=False, shots=False):
                    page.get_by_role(
                        "button", name="编辑 ACME 账户", exact=True
                    ).click()
                    dialog = page.get_by_role("dialog")
                    dialog.get_by_label("账户邮箱", exact=True).fill(email)
                    if eab:
                        dialog.get_by_role(
                            "combobox", name="外部账户绑定", exact=True
                        ).click()
                        page.locator(".ant-select-dropdown:visible").get_by_text(
                            "替换凭据", exact=True
                        ).click()
                        expect(
                            dialog.get_by_role(
                                "button", name="更新账户", exact=True
                            )
                        ).to_be_disabled()
                        dialog.get_by_label("EAB 密钥 ID", exact=True).fill(eab_kid)
                        dialog.get_by_label("EAB HMAC 密钥", exact=True).fill(eab_hmac)
                        assert (
                            dialog.get_by_label(
                                "EAB HMAC 密钥", exact=True
                            ).get_attribute("type")
                            == "password"
                        )
                    else:
                        expect(
                            dialog.get_by_label("外部账户绑定", exact=True)
                        ).to_be_disabled()
                    if shots:
                        screenshot(
                            page,
                            args.screenshots,
                            "account-eab" if eab else "account-contact",
                        )
                    dialog.get_by_role(
                        "button", name="更新账户", exact=True
                    ).click()
                    expect(dialog).to_be_hidden()

                inspect()
                edit("registered@example.com", eab=True, shots=True)
                wait_job(client, identifier)
                account_file, key_file = account_paths(
                    vault, vault.root / identifier, directory, "registered@example.com"
                )
                assert hashlib.sha256(vault.read(key_file)).hexdigest() == original_hash
                api(client, base + "/issue", "POST", {})
                first = wait_job(client, identifier)
                first_version = first["certificate"]["version_id"]
                first_data = api(client, base + "/material?include_private_key=true")
                registration_uri = first["account"]["uri"]
                account_path = urlsplit(registration_uri).path
                assert first["account"]["state"] == "registered"

                def account_contact():
                    connection = connect(
                        {"directory_url": directory, "ca_file": str(cert)},
                        vault.read(key_file).decode(),
                    )
                    try:
                        registration = connection.query_registration(
                            messages.RegistrationResource(
                                body=messages.Registration(), uri=""
                            )
                        )
                        assert registration.uri == registration_uri
                        return tuple(registration.body.contact)
                    finally:
                        connection.net.session.close()

                inspect()
                edit("changed@example.com", shots=True)
                changed = wait_job(client, identifier)
                assert changed["account"]["email"] == "changed@example.com"
                assert account_contact() == ("mailto:changed@example.com",)
                assert hashlib.sha256(vault.read(key_file)).hexdigest() == original_hash
                assert (
                    json.loads(vault.read(account_file))["email"]
                    == "changed@example.com"
                )
                api(client, base + "/renew", "POST", {"force": True})
                current = wait_job(client, identifier)
                current_data = api(client, base + "/material?include_private_key=true")
                assert current["certificate"]["version_id"] != first_version
                assert current_data["key_pem"] != first_data["key_pem"]
                assert current["account"]["uri"] == registration_uri
                print(
                    "PASS orphaned key preservation, EAB editing, "
                    "real CA contact update and subsequent lego renewal",
                    flush=True,
                )

                def ca_status(data):
                    response = ca_client.get(
                        f"https://127.0.0.1:{management_port}/cert-status-by-serial/"
                        + format(int(data["serial"]), "x")
                    )
                    response.raise_for_status()
                    return response.json()["Status"]

                inspect()
                versions = page.locator(
                    '.ant-card:has(> .ant-card-head .ant-card-head-title:text-is("版本"))'
                )
                version_row = versions.get_by_role("row").filter(
                    has_text=first_data["serial"]
                )
                expect(version_row).to_have_count(1)
                revoke = version_row.get_by_role(
                    "button", name="吊销版本", exact=True
                )
                glyph = revoke.locator("svg")
                expect(glyph).to_be_visible()
                assert glyph.evaluate("""el => Boolean(el.querySelector('path')?.getAttribute('d'))
                    && el.getBBox().width > 0 && el.getBBox().height > 0"""), (
                    "Revocation icon is missing"
                )
                revoke.click()
                dialog = page.get_by_role("dialog")
                expect(
                    dialog.get_by_role("button", name="吊销版本", exact=True)
                ).to_be_disabled()
                dialog.get_by_role(
                    "combobox", name="吊销原因", exact=True
                ).click()
                page.locator(".ant-select-dropdown:visible").get_by_text(
                    "已被替代", exact=True
                ).click()
                screenshot(page, args.screenshots, "revoke-confirm")
                dialog.get_by_label(
                    "我确认吊销此版本", exact=True
                ).check()
                dialog.get_by_role("button", name="吊销版本", exact=True).click()
                expect(dialog).to_be_hidden()
                historical = wait_job(client, identifier)
                assert (
                    historical["certificate"]["version_id"]
                    == current["certificate"]["version_id"]
                )
                assert (
                    ca_status(first_data) == "Revoked"
                    and ca_status(current_data) == "Valid"
                )
                assert (
                    client.post(
                        "/api/v1/" + base + "/versions/" + first_version + "/activate"
                    ).status_code
                    == 409
                )
                print(
                    "PASS exact historical-version revocation verified independently at CA",
                    flush=True,
                )

                inspect()
                gate.drop_prefix = account_path
                edit("reconciled@example.com")
                failed = wait_job(client, identifier, "failed")
                assert failed["account"]["email"] == "changed@example.com"
                assert account_contact() == ("mailto:reconciled@example.com",)
                updates = sum(path == account_path for _, path, _ in gate.events)
                inspect()
                page.get_by_role(
                    "button", name="重试账户更新", exact=True
                ).click()
                reconciled = wait_job(client, identifier)
                assert reconciled["account"]["email"] == "reconciled@example.com"
                assert (
                    sum(path == account_path for _, path, _ in gate.events) == updates
                )
                print(
                    "PASS lost account response reconciles without repeating the CA contact change",
                    flush=True,
                )

                gate.hold_prefix = "/revoke-cert"
                current_id = current["certificate"]["version_id"]
                job = api(
                    client,
                    base + "/versions/" + current_id + "/revoke",
                    "POST",
                    {"confirm": True, "reason": 1},
                )
                assert gate.entered.wait(20)
                assert ca_status(current_data) == "Revoked"
                children = (
                    Path(
                        f"/proc/{backend_process.pid}/task/{backend_process.pid}/children"
                    )
                    .read_text()
                    .split()
                )
                assert len(children) == 1
                child_pid = int(children[0])
                assert (
                    b"open_node.services.certificate_acme"
                    in Path(f"/proc/{child_pid}/cmdline").read_bytes()
                )
                os.kill(backend_process.pid, signal.SIGKILL)
                backend.__exit__(None, None, None)
                backend, backend_process = start_backend()
                runtime.poll(
                    "backend restarts while CA helper retains lock",
                    lambda: client.get("/healthz").status_code == 200,
                )
                time.sleep(1)
                assert api(client, base)["jobs"][0]["status"] == "running"
                revokes = sum(path == "/revoke-cert" for _, path, _ in gate.events)
                gate.release.set()
                recovered = wait_job(client, identifier)
                assert recovered["jobs"][0]["id"] == job["id"]
                assert recovered["certificate"]["status"] == "revoked"
                assert (
                    sum(path == "/revoke-cert" for _, path, _ in gate.events) == revokes
                )
                runtime.poll(
                    "CA helper exits",
                    lambda: (
                        not Path(f"/proc/{child_pid}").exists()
                        or Path(f"/proc/{child_pid}/stat").read_text().split()[2] == "Z"
                    ),
                )
                child_pid = None
                assert not list(vault.root.glob("*/jobs/*/request.json"))
                print(
                    "PASS hard restart, inherited lock and durable receipt "
                    "reconcile confirmed revocation once",
                    flush=True,
                )

                reissue_job = api(client, base + "/renew", "POST", {})
                assert reissue_job["force"]
                fresh = wait_job(client, identifier)
                fresh_data = api(client, base + "/material?include_private_key=true")
                assert fresh_data["key_pem"] != current_data["key_pem"]
                assert ca_status(fresh_data) == "Valid"
                assert not fresh["certificate"]["auto_renew"]
                copy = api(
                    client,
                    "certificates/import",
                    "POST",
                    {
                        "name": "Imported copy",
                        "cert_pem": fresh_data["cert_pem"],
                        "key_pem": fresh_data["key_pem"],
                    },
                )
                copy_base = "certificates/" + copy["id"]
                endpoint = copy_base + "/versions/" + copy["version_id"] + "/revoke"
                assert (
                    client.post(
                        "/api/v1/" + endpoint, json={"confirm": True}
                    ).status_code
                    == 409
                )
                gate.drop_prefix = "/revoke-cert"
                api(
                    client,
                    endpoint,
                    "POST",
                    {"confirm": True, "directory_url": directory},
                )
                unknown = wait_job(client, copy["id"], "failed")
                assert unknown["versions"][0]["revocation"]["status"] == "unknown"
                assert (
                    api(client, base)["certificate"]["status"] == "revocation_unknown"
                )
                assert ca_status(fresh_data) == "Revoked"
                inspect("Imported copy")
                page.get_by_role("button", name="重试吊销", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(
                    dialog.get_by_role("button", name="吊销版本", exact=True)
                ).to_be_disabled()
                dialog.get_by_label(
                    "我确认吊销此版本", exact=True
                ).check()
                dialog.get_by_role("button", name="吊销版本", exact=True).click()
                expect(dialog).to_be_hidden()
                confirmed = wait_job(client, copy["id"])
                assert "already revoked" in confirmed["jobs"][0]["message"]
                assert api(client, base)["certificate"]["status"] == "revoked"
                inspect("Imported copy")
                completed_job = page.locator(
                    '.ant-card:has(> .ant-card-head .ant-card-head-title:text-is("任务"))'
                ).get_by_role("row").filter(
                    has=page.get_by_text("CA 已确认此证书已被吊销。", exact=True)
                )
                expect(completed_job).to_have_count(1)
                expect(
                    completed_job.get_by_text("已成功", exact=True)
                ).to_be_visible()
                expect(
                    completed_job.get_by_text(
                        "操作未完成，请检查当前状态后重试。", exact=True
                    )
                ).to_have_count(0)
                expect(page.get_by_label("自动续签", exact=True)).to_be_disabled()
                expect(
                    page.get_by_role("button", name="吊销版本", exact=True)
                ).to_be_disabled()
                screenshot(page, args.screenshots, "revoked-copy")
                inspect()
                expect(
                    page.get_by_role(
                        "button", name="启用版本", exact=True
                    ).first
                ).to_be_disabled()
                screenshot(page, args.screenshots, "revoked-history")
                assert not errors, errors
                context.close()
                browser.close()
                print(
                    "PASS forced new-key issuance, imported-key revocation, "
                    "unknown-result retry and duplicate protection",
                    flush=True,
                )
            for path in (base, copy_base):
                api(client, path, "DELETE")
            assert (
                client.post(
                    "/api/v1/certificates/import",
                    json={
                        "name": "Reimport",
                        "cert_pem": fresh_data["cert_pem"],
                        "key_pem": fresh_data["key_pem"],
                    },
                ).status_code
                == 409
            )
            assert not list(vault.root.glob("*/jobs/*/request.json"))
            database = (work / "backend.db").read_bytes()
            assert eab_hmac.encode() not in database
            assert b"BEGIN PRIVATE KEY" not in database
            for path in vault.root.rglob("*"):
                assert path.stat().st_mode & 0o777 == (
                    0o700 if path.is_dir() else 0o600
                )
            print(
                "PASS persistent revocation ledger survives profile deletion; "
                "secrets and request files stay private",
                flush=True,
            )
        except BaseException:
            for log in [
                work / "backend.log",
                *work.glob("vault/*/last-job.log"),
                *work.glob("vault/*/jobs/*/last-job.log"),
            ]:
                print(log.name, log.read_text()[-5000:], flush=True)
            raise
        finally:
            gate.release.set()
            if child_pid:
                with suppress(ProcessLookupError):
                    os.killpg(child_pid, signal.SIGKILL)
    print(
        "PASS all certificate administration smoke checks and fixture cleanup",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lego", type=Path, required=True)
    parser.add_argument("--pebble", type=Path, required=True)
    parser.add_argument("--screenshots", type=Path, required=True)
    run(parser.parse_args())
