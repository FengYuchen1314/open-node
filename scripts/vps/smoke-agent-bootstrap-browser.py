#!/usr/bin/env python3
"""Exercise the production Agent bootstrap UI against a disposable VPS control plane.

This is a browser/API gate, not a substitute for smoke-agent-bootstrap.py's real
systemd installation. Its final Agent registration is an explicit API fixture.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

CANONICAL_URL = "https://bootstrap.example/panel"


def redacted(value, private):
    for secret in sorted(set(private), key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def stop(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


@contextlib.contextmanager
def backend(repository, frontend, configured, private, output):
    with tempfile.TemporaryDirectory(prefix="open-node-bootstrap-browser-") as directory:
        work = Path(directory)
        password = secrets.token_urlsafe(32)
        private.append(password)
        environment = {
            **{key: value for key, value in os.environ.items() if not key.startswith("OPEN_NODE_")},
            "PYTHONPATH": str(repository / "backend/app"),
            "OPEN_NODE_DATABASE_URL": f"sqlite:///{work / 'browser.db'}",
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_FRONTEND_DIR": str(frontend),
            "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "certificates"),
            "OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL": CANONICAL_URL if configured else "",
        }
        subprocess.run(
            [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
            input=password + "\n", text=True, capture_output=True, check=True,
            cwd=work, env=environment, timeout=30,
        )
        with socket.socket() as listener, (work / "service.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "open_node.main:app", "--fd",
                 str(listener.fileno()), "--no-access-log"],
                cwd=work, env=environment, pass_fds=(listener.fileno(),),
                stdout=log, stderr=log, start_new_session=True,
            )
            try:
                with httpx.Client(trust_env=False, timeout=2) as client:
                    for _ in range(120):
                        if process.poll() is not None:
                            raise RuntimeError("Disposable control plane exited before readiness")
                        try:
                            if client.get(endpoint + "/healthz").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise TimeoutError("Disposable control plane did not become ready")
                yield endpoint, password
            finally:
                stop(process)
                log.seek(0)
                (output / ("configured.log" if configured else "disabled.log")).write_text(
                    redacted(log.read(), private), encoding="utf-8"
                )


def capture(page, dialog, output, name, focus=None):
    for width, height, suffix in ((1440, 1000, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(200)
        if focus is not None:
            focus.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert dialog.locator(".v-card").evaluate_all(
            "items => items.every(item => item.scrollWidth <= item.clientWidth + 1)"
        )
        page.screenshot(
            path=str(output / f"{name}-{suffix}.png"), animations="disabled",
            mask=[page.locator(".token-code"), dialog.locator(".install-command")],
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


def command_ticket(command, private):
    words = shlex.split(command)
    ticket = words[words.index("--ticket") + 1]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket), "Command ticket is not canonical"
    private.extend((ticket, command))
    return ticket


def exercise(endpoint, password, output, configured, private, report):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        errors, requests, bootstrap_responses = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request))
        page.on("response", lambda response: bootstrap_responses.append(response)
                if "/bootstrap" in response.url else None)
        try:
            page.goto(endpoint + "/")
            page.get_by_label("Username", exact=True).fill("admin")
            page.get_by_label("Password", exact=True).fill(password)
            page.get_by_role("button", name="Sign In", exact=True).click()
            expect(page.get_by_role("heading", name="Open Node control plane")).to_be_visible()
            name = "Bootstrap browser configured" if configured else "Bootstrap browser disabled"
            page.locator(".server-form").first.get_by_label("Name", exact=True).fill(name)
            with page.expect_response(lambda response: response.url.endswith("/api/v1/servers")
                                      and response.request.method == "POST") as created:
                page.get_by_role("button", name="Create server", exact=True).click()
            assert created.value.status == 201
            server = created.value.json()
            token = server["agent_token"]
            private.append(token)
            server_id = server["server"]["id"]
            path = f"/api/v1/servers/{server_id}/bootstrap"
            page.locator(".token-alert").get_by_role(
                "button", name="Install Agent", exact=True
            ).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_test_id("bootstrap-status")).to_contain_text(
                "No installation ticket"
            )
            if not configured:
                expect(dialog).to_contain_text("OPEN_NODE_AGENT_BOOTSTRAP_PUBLIC_URL")
                expect(dialog.get_by_test_id("bootstrap-issue")).to_have_count(0)
                capture(page, dialog, output, "bootstrap-disabled")
                assert not any(request.method == "POST" and request.url.endswith(path)
                               for request in requests)
                report["disabled_without_canonical_https"] = True
                return

            expect(dialog.get_by_test_id("bootstrap-issue")).to_be_disabled()
            capture(page, dialog, output, "bootstrap-ready")
            assert not any(request.method == "POST" and request.url.endswith(path)
                           for request in requests)
            # Exercise issuance and copying with real mobile pointer/scroll behavior,
            # not only a resized screenshot of the desktop interaction.
            page.set_viewport_size({"width": 390, "height": 844})
            # Vuetify's display layer overlays its small combobox input.
            # Click the visible select field, as a pointer user would.
            dialog.locator(".v-select").click()
            page.get_by_role("option", name="HTTP polling", exact=True).click()

            def issue_command():
                dialog.get_by_label(
                    "I will use a new Debian 12 amd64 host for this server only.", exact=True
                ).check()
                with page.expect_response(lambda response: response.url.endswith(path)
                                          and response.request.method == "POST") as issued:
                    dialog.get_by_test_id("bootstrap-issue").click()
                response = issued.value
                assert response.status == 201
                payload = response.json()
                command = payload["command"]
                ticket = command_ticket(command, private)
                assert token not in command and CANONICAL_URL in command
                field = dialog.get_by_label("Root shell installation command", exact=True)
                expect(field).to_have_value(re.compile(r".*--ticket.*", re.DOTALL))
                assert field.input_value() == command
                return payload, ticket

            first, first_ticket = issue_command()
            assert first["issued"]["transport"] == "http"
            with httpx.Client(base_url=endpoint, trust_env=False, timeout=10) as anonymous:
                script = anonymous.get("/api/v1/agents/bootstrap/installer.py")
                assert script.status_code == 200
                assert hashlib.sha256(script.content).hexdigest() in first["command"]
                dialog.get_by_role("button", name="Copy command", exact=True).click()
                expect(dialog.get_by_role("status")).to_contain_text("Copied.")
                assert page.evaluate("navigator.clipboard.readText()") == first["command"]
                capture(page, dialog, output, "bootstrap-command",
                        dialog.get_by_label("Root shell installation command", exact=True))
                dialog.get_by_role("button", name="Close", exact=True).click()
                expect(dialog).not_to_be_visible()
                assert first_ticket not in page.content()
                storage = page.evaluate("JSON.stringify({ ...localStorage, ...sessionStorage })")
                assert not any(value in storage for value in private)

                page.get_by_role("button", name=f"Install Agent on {name}", exact=True).click()
                expect(dialog.get_by_test_id("bootstrap-status")).to_contain_text("Ticket ready")
                expect(dialog.get_by_label("Root shell installation command")).to_have_count(0)
                _, second_ticket = issue_command()
                replay = anonymous.post("/api/v1/agents/bootstrap/redeem", json={
                    "ticket": first_ticket, "claim_nonce": secrets.token_urlsafe(32),
                })
                assert replay.status_code == 401
                with page.expect_response(lambda response: response.url.endswith(path)
                                          and response.request.method == "DELETE") as revoked:
                    dialog.get_by_role("button", name="Revoke installation ticket").click()
                assert revoked.value.status == 200
                expect(dialog.get_by_test_id("bootstrap-status")).to_contain_text("Ticket revoked")
                expect(dialog.get_by_label("Root shell installation command")).to_have_count(0)
                assert anonymous.post("/api/v1/agents/bootstrap/redeem", json={
                    "ticket": second_ticket, "claim_nonce": secrets.token_urlsafe(32),
                }).status_code == 401
                _, third_ticket = issue_command()
                nonce = secrets.token_urlsafe(32)
                private.append(nonce)
                claimed = anonymous.post("/api/v1/agents/bootstrap/redeem", json={
                    "ticket": third_ticket, "claim_nonce": nonce,
                })
                assert claimed.status_code == 200
                assert claimed.headers["cache-control"] == "no-store"
                assert claimed.json()["configuration"]["agent_token"] == token
                assert anonymous.post("/api/v1/agents/bootstrap/redeem", json={
                    "ticket": third_ticket, "claim_nonce": secrets.token_urlsafe(32),
                }).status_code == 401
                expect(dialog.get_by_test_id("bootstrap-status")).to_contain_text(
                    "Ticket claimed", timeout=12000
                )
                expect(dialog.get_by_test_id("bootstrap-status")).to_contain_text(
                    "Agent not yet registered"
                )
                expect(dialog.get_by_test_id("bootstrap-issue")).to_have_count(0)
                expect(dialog.get_by_label("Root shell installation command")).to_have_count(0)
                capture(page, dialog, output, "bootstrap-claimed")
                # This exercises the status display only; it is not a real installed Agent.
                registered = anonymous.post("/api/v1/agents/register", json={
                    "token": token, "hostname": "bootstrap-browser-fixture",
                    "agent_version": "open-node/0.3.0a0",
                })
                assert registered.status_code == 201
                expect(dialog.get_by_test_id("bootstrap-status")).to_contain_text(
                    "Agent registered", timeout=12000
                )
                expect(dialog).to_contain_text(
                    "Registration alone is not proof of a healthy installation"
                )
                capture(page, dialog, output, "bootstrap-registered")
                assert anonymous.post("/api/v1/agents/bootstrap/redeem", json={
                    "ticket": third_ticket, "claim_nonce": nonce,
                }).status_code == 401

                # Heartbeat can precede a formal Agent registration. Such a host
                # must not be offered another installation ticket.
                dialog.get_by_role("button", name="Close", exact=True).click()
                heartbeat_name = "Existing heartbeat host"
                page.locator(".server-form").first.get_by_label("Name", exact=True).fill(
                    heartbeat_name
                )
                with page.expect_response(lambda response: response.url.endswith("/api/v1/servers")
                                          and response.request.method == "POST") as created:
                    page.get_by_role("button", name="Create server", exact=True).click()
                assert created.value.status == 201
                heartbeat_server = created.value.json()
                private.append(heartbeat_server["agent_token"])
                heartbeat = anonymous.post("/api/v1/agents/heartbeat", json={
                    "token": heartbeat_server["agent_token"],
                })
                assert heartbeat.status_code == 200
                page.locator(".token-alert").get_by_role(
                    "button", name="Install Agent", exact=True
                ).click()
                expect(dialog).to_contain_text("This server has already reported a heartbeat")
                expect(dialog.get_by_test_id("bootstrap-issue")).to_have_count(0)
                capture(page, dialog, output, "bootstrap-existing-heartbeat")

            for response in bootstrap_responses:
                assert response.all_headers().get("cache-control") == "no-store"
                assert response.all_headers().get("referrer-policy") == "no-referrer"
                assert token not in response.text()
            for request in requests:
                assert not any(value in request.url for value in private), "Private URL value"
                if "/bootstrap" in request.url:
                    assert token not in (request.post_data or "")
                    assert token not in json.dumps(request.all_headers())
            storage = page.evaluate("JSON.stringify({ ...localStorage, ...sessionStorage })")
            assert not any(value in storage for value in private)
            assert not errors, "Browser JavaScript errors"
            report.update({
                "ui_server_creation": True, "explicit_issue_only": True,
                "copy_close_and_reopen": True, "reissue_invalidates_previous": True,
                "revocation": True, "claim_not_installation": True,
                "registered_status_uses_synthetic_api_fixture": True,
                "installer_checksum_bound": True, "no_secret_in_urls_or_storage": True,
                "mobile_issue_and_copy": True, "existing_heartbeat_refuses_issue": True,
                "private_cache_headers": True, "browser_errors": [],
            })
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run browser gates on the isolated Linux VPS only")
    repository = args.repository.resolve(strict=True)
    frontend = (args.frontend_dir or repository / "frontend/dist").resolve(strict=True)
    if not (frontend / "index.html").is_file():
        parser.error("Build the production frontend before this gate")
    output = args.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    private = []
    report = {"status": "running", "repository": str(repository), "frontend": str(frontend),
              "real_systemd_installation": "separate smoke-agent-bootstrap.py gate"}
    try:
        for configured in (False, True):
            with backend(repository, frontend, configured, private, output) as (endpoint, password):
                exercise(endpoint, password, output, configured, private, report)
        report["status"] = "passed"
        print("PASS Agent bootstrap production-browser workflow, privacy and desktop/mobile layout")
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = redacted(f"{type(error).__name__}: {error}", private)
        print(report["error"], file=sys.stderr)
        return 1
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
