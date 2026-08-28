"""Real HTTP-01 issuance, recovery, renewal, UI and Agent TLS deployment on the VPS."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pwd
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

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "acme_smoke", Path(__file__).with_name("smoke-certificates.py")
)
acme = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acme)
runtime, service, api, wait_job = acme.runtime, acme.service, acme.api, acme.wait_job
SPEC = importlib.util.spec_from_file_location(
    "operator_ui", Path(__file__).with_name("smoke-operator-ui.py")
)
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class ValidationGate:
    """An observable fault-injection hop; successful responses come from real Nginx."""

    def __init__(self, nginx_port):
        self.nginx_port = nginx_port
        self.reject = False
        self.hold = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.successes = []

    def start(self, stack):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if owner.hold:
                    owner.entered.set()
                    owner.release.wait(60)
                if owner.reject:
                    status, body = 404, b"fixture rejection"
                else:
                    with httpx.Client(trust_env=False, timeout=10) as client:
                        response = client.get(
                            f"http://127.0.0.1:{owner.nginx_port}" + self.path,
                            headers={"Host": self.headers["Host"]},
                        )
                        status, body = response.status_code, response.content
                    if status == 200:
                        owner.successes.append((self.path, body))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        stack.callback(self.release.set)
        return server.server_port


def browser_profiles(client, url, output):
    profiles = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        try:
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
            page.goto(url + "/certificates")
            expect(
                page.get_by_role("button", name="New certificate", exact=True)
            ).to_be_enabled()
            for mode in ("standalone", "webroot"):
                page.get_by_role("button", name="New certificate", exact=True).click()
                dialog = page.get_by_role("dialog")
                dialog.get_by_label("Certificate name", exact=True).fill("HTTP " + mode)
                dialog.get_by_label("DNS names", exact=True).fill("*.acme.test")
                dialog.get_by_label("Account email", exact=True).fill(
                    "operator@example.com"
                )
                dialog.get_by_label("Validation method", exact=True).press("Enter")
                page.get_by_role(
                    "option", name="HTTP-01 / " + mode.title(), exact=True
                ).click()
                expect(dialog.get_by_label("DNS provider", exact=True)).to_have_count(0)
                expect(
                    dialog.get_by_text("Wildcard names require DNS-01", exact=True)
                ).to_be_visible()
                submit = dialog.get_by_role(
                    "button", name="Create certificate", exact=True
                )
                expect(submit).to_be_disabled()
                dialog.get_by_label("DNS names", exact=True).fill(
                    f"edge.acme.test {mode}.acme.test"
                )
                if mode == "webroot":
                    expect(dialog.get_by_label("Webroot", exact=True)).to_be_visible()
                    expect(dialog.get_by_text("site", exact=True)).to_be_visible()
                expect(submit).to_be_disabled()
                dialog.get_by_label(
                    "I accept this CA's terms of service", exact=True
                ).check()
                dialog.get_by_label("Auto-renew", exact=True).uncheck()
                expect(submit).to_be_enabled()
                for label, width, height in (
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                    ("narrow", 320, 780),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    ui.check_layout(page)
                    expect(submit).to_be_in_viewport(ratio=1)
                    page.screenshot(
                        path=output / f"{mode}-{label}-form.png", animations="disabled"
                    )
                dialog.locator("summary").click()
                ui.check_layout(page)
                expect(submit).to_be_in_viewport(ratio=1)
                page.screenshot(
                    path=output / f"{mode}-narrow-eab.png", animations="disabled"
                )
                dialog.locator("summary").click()
                with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and response.url.endswith("/certificates")
                    )
                ) as created:
                    submit.click()
                profiles[mode] = created.value.json()
                expect(dialog).not_to_be_visible()
                row = page.locator(".certificate-row").filter(has_text="HTTP " + mode)
                with page.expect_response(
                    lambda response: response.url.endswith("/issue")
                ):
                    row.get_by_role(
                        "button", name="Issue certificate", exact=True
                    ).click()
                wait_job(client, profiles[mode]["id"])
                row.get_by_role("button", name="HTTP " + mode, exact=True).click()
                expect(page.get_by_label("Auto-renew", exact=True)).to_be_enabled()
                expect(
                    page.get_by_role("button", name="Renew now", exact=True)
                ).to_be_enabled()
                for label, width, height in (
                    ("desktop", 1440, 1000),
                    ("mobile", 390, 844),
                    ("narrow", 320, 780),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    ui.check_layout(page)
                    page.screenshot(
                        path=output / f"{mode}-{label}-issued.png",
                        animations="disabled",
                    )
                page.get_by_role(
                    "button", name="Close certificate details", exact=True
                ).click()
            assert not errors, errors
        except BaseException:
            page.screenshot(path=output / "failure.png", animations="disabled")
            print(
                page.evaluate("""() => [...document.querySelectorAll('form input, form button')]
                .map(el => ({id: el.id, tag: el.tagName, box: el.getBoundingClientRect().toJSON()}))
                .filter(el => el.box.x < 0 || el.box.right > innerWidth + 1)"""),
                file=sys.stderr,
            )
            raise
        finally:
            context.close()
            browser.close()
    print(
        "PASS both HTTP modes created and issued in the browser without DNS providers",
        flush=True,
    )
    return profiles


def run(args):
    for binary, expected in ((args.lego, "4.35.2"), (args.pebble, "2.6.0")):
        result = subprocess.run(
            [str(binary), "--version" if binary == args.lego else "-version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert expected in result.stdout
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-node-http01-smoke-") as temporary:
        root = Path(temporary)
        root.chmod(0o711)
        work, site, nginx_dir = root / "private", root / "site", root / "nginx"
        work.mkdir(mode=0o700)
        site.mkdir(mode=0o755)
        (site / "index.html").write_text("existing website")
        nginx_dir.mkdir(mode=0o750)
        user = pwd.getpwnam("www-data")
        os.chown(nginx_dir, user.pw_uid, user.pw_gid)
        nginx_port, standalone_port = runtime.free_port(), runtime.free_port()
        config = nginx_dir / "nginx.conf"
        config.write_text(f"""
daemon off;
pid {nginx_dir}/nginx.pid;
error_log stderr;
events {{ worker_connections 64; }}
http {{
    access_log off;
    client_body_temp_path {nginx_dir}/client;
    proxy_temp_path {nginx_dir}/proxy;
    fastcgi_temp_path {nginx_dir}/fastcgi;
    uwsgi_temp_path {nginx_dir}/uwsgi;
    scgi_temp_path {nginx_dir}/scgi;
    server {{
        listen 127.0.0.1:{nginx_port};
        root {site};
        location /.well-known/acme-challenge/ {{
            try_files $uri @standalone;
        }}
        location @standalone {{
            proxy_set_header Host $host;
            proxy_pass http://127.0.0.1:{standalone_port};
        }}
    }}
}}
""")
        with ExitStack() as stack:
            stack.enter_context(
                runtime.process(
                    nginx_dir,
                    "nginx",
                    [str(args.nginx), "-p", str(nginx_dir), "-c", str(config)],
                    user=user.pw_uid,
                    group=user.pw_gid,
                    extra_groups=[],
                )
            )
            runtime.poll(
                "independent non-root Nginx", lambda: runtime.port_open(nginx_port)
            )
            gate = ValidationGate(nginx_port)
            challenge_port = gate.start(stack)
            dns = acme.DNSFixture()
            dns.start(stack)
            cert_path, key_path = acme.https_identity(work)
            ca_port, management_port = runtime.free_port(), runtime.free_port()
            directory = f"https://127.0.0.1:{ca_port}/dir"
            ca_config = work / "pebble.json"
            runtime.write_private(
                ca_config,
                {
                    "pebble": {
                        "listenAddress": f"127.0.0.1:{ca_port}",
                        "managementListenAddress": f"127.0.0.1:{management_port}",
                        "certificate": str(cert_path),
                        "privateKey": str(key_path),
                        "httpPort": challenge_port,
                        "tlsPort": runtime.free_port(),
                        "certificateValidityPeriod": 240,
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
                        str(ca_config),
                        "-dnsserver",
                        dns.address + ":53",
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
                    verify=ssl.create_default_context(cafile=str(cert_path)),
                    trust_env=False,
                    timeout=5,
                )
            )
            runtime.poll(
                "TLS-verified Pebble CA",
                lambda: ca_client.get(directory).status_code == 200,
            )
            trust = ca_client.get(f"https://127.0.0.1:{management_port}/roots/0").text
            password = secrets.token_urlsafe(32)
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "backend/app"),
                "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'backend.db'}",
                "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
                "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "vault"),
                "OPEN_NODE_CERTIFICATE_LEGO_BINARY": str(args.lego),
                "OPEN_NODE_CERTIFICATE_CA_FILE": str(cert_path),
                "OPEN_NODE_CERTIFICATE_ACME_DIRECTORIES": json.dumps([directory]),
                "OPEN_NODE_CERTIFICATE_HTTP_ADDRESS": f"127.0.0.1:{standalone_port}",
                "OPEN_NODE_CERTIFICATE_WEBROOTS": json.dumps({"site": str(site)}),
                "OPEN_NODE_CERTIFICATE_POLL_SECONDS": "1",
                "OPEN_NODE_FRONTEND_DIR": str(ROOT / "frontend/dist"),
            }
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
                input=password + "\n",
                text=True,
                check=True,
                capture_output=True,
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
                "HTTP certificate backend",
                lambda: client.get("/healthz").status_code == 200,
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
            lego_pid = None
            try:
                profiles = browser_profiles(client, url, args.screenshots)
                challenges = site / ".well-known/acme-challenge"
                assert not list(challenges.iterdir())
                assert (site / "index.html").read_text() == "existing website"
                assert len(gate.successes) >= 4
                assert not runtime.port_open(standalone_port)
                for mode, profile in profiles.items():
                    base = "certificates/" + profile["id"]
                    original = api(client, base)["certificate"]["version_id"]
                    api(client, base + "/renew", "POST", {})
                    wait_job(client, profile["id"], "skipped")
                    gate.reject = True
                    api(client, base + "/renew", "POST", {"force": True})
                    failed = wait_job(client, profile["id"], "failed")
                    assert failed["certificate"]["version_id"] == original
                    assert not list(challenges.iterdir()) and not runtime.port_open(
                        standalone_port
                    )
                    gate.reject = False
                    api(client, base + "/renew", "POST", {"force": True})
                    renewed = wait_job(client, profile["id"])
                    assert renewed["certificate"]["version_id"] != original
                    assert not list(challenges.iterdir())
                    print(
                        "PASS "
                        + mode
                        + " skip, failed validation preservation, forced renewal and cleanup",
                        flush=True,
                    )

                profile = profiles["webroot"]
                base = "certificates/" + profile["id"]
                previous = api(client, base)["certificate"]["version_id"]
                gate.hold = True
                api(client, base + "/renew", "POST", {"force": True})
                assert gate.entered.wait(30), "CA did not request the webroot token"
                assert list(challenges.iterdir())
                children = (
                    Path(
                        f"/proc/{backend_process.pid}/task/{backend_process.pid}/children"
                    )
                    .read_text()
                    .split()
                )
                assert len(children) == 1, children
                lego_pid = int(children[0])
                assert (
                    str(args.lego).encode()
                    in Path(f"/proc/{lego_pid}/cmdline").read_bytes()
                )
                os.kill(backend_process.pid, signal.SIGKILL)
                backend.__exit__(None, None, None)
                backend, backend_process = start_backend()
                runtime.poll(
                    "backend restart while lego survives",
                    lambda: client.get("/healthz").status_code == 200,
                )
                time.sleep(2)
                assert api(client, base)["jobs"][0]["status"] == "running"
                assert list(challenges.iterdir())
                os.killpg(lego_pid, signal.SIGKILL)
                lego_pid = None
                interrupted = wait_job(client, profile["id"], "interrupted")
                assert interrupted["certificate"]["version_id"] == previous
                assert not list(challenges.iterdir())
                gate.hold = False
                gate.release.set()
                print(
                    "PASS inherited worker lock and hard-crash cleanup preserve the active certificate",
                    flush=True,
                )
                for mode, profile in profiles.items():
                    base = "certificates/" + profile["id"]
                    api(client, base + "/renew", "POST", {"force": True})
                    renewed = wait_job(client, profile["id"])
                    profile["before_auto"] = renewed["certificate"]["version_id"]
                    api(
                        client,
                        base,
                        "PATCH",
                        {"name": profile["name"], "auto_renew": True},
                    )
                for mode, profile in profiles.items():
                    base = "certificates/" + profile["id"]
                    current = runtime.poll(
                        mode + " short-lived automatic HTTP renewal",
                        lambda base=base: api(client, base),
                        lambda row, version=profile["before_auto"]: (
                            row["certificate"]["version_id"] != version
                            and not row["certificate"]["active_job_id"]
                        ),
                        timeout=160,
                    )
                    assert (
                        current["jobs"][0]["status"] == "succeeded"
                        and not current["jobs"][0]["force"]
                    )
                    api(
                        client,
                        base,
                        "PATCH",
                        {"name": profile["name"], "auto_renew": False},
                    )
                xray = runtime.download_xray(work, args.xray_archive)
                for mode, transport in (
                    ("standalone", "websocket"),
                    ("webroot", "http"),
                ):
                    profile = profiles[mode]
                    api(
                        client,
                        "certificates/" + profile["id"] + "/renew",
                        "POST",
                        {"force": True},
                    )
                    current = wait_job(client, profile["id"])
                    fixture = service.Fixture(work)
                    try:
                        acme.deploy_to_agent(
                            work,
                            fixture,
                            args,
                            xray,
                            client,
                            url,
                            profile["id"],
                            current["versions"][1],
                            current,
                            trust,
                            transport,
                        )
                    finally:
                        fixture.cleanup()
                    print(
                        "PASS "
                        + mode
                        + " certificate deployed to live Agent TLS over "
                        + transport,
                        flush=True,
                    )
                assert not list(challenges.iterdir())
                for path in (work / "vault").rglob("*"):
                    assert not path.stat().st_mode & 0o077, path
                assert (site / "index.html").read_text() == "existing website"
            except BaseException:
                if lego_pid is not None:
                    with suppress(OSError):
                        command = Path(f"/proc/{lego_pid}/cmdline").read_bytes()
                        if str(args.lego).encode() in command:
                            os.killpg(lego_pid, signal.SIGKILL)
                gate.hold = False
                gate.release.set()
                for path in [
                    work / "backend.log",
                    work / "pebble.log",
                    nginx_dir / "nginx.log",
                    *(work / "vault").rglob("last-job.log"),
                ]:
                    if path.is_file():
                        print(
                            path.read_text()[-5000:].replace(password, "[redacted]"),
                            file=sys.stderr,
                        )
                raise
    print("PASS real HTTP-01 lifecycle, browser and Agent deployment smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "lego",
        "pebble",
        "wheel",
        "nginx",
        "nginx-stream-module",
        "screenshots",
    ):
        parser.add_argument(
            "--" + name, type=lambda value: Path(value).resolve(), required=True
        )
    parser.add_argument("--xray-archive", type=lambda value: Path(value).resolve())
    run(parser.parse_args())
