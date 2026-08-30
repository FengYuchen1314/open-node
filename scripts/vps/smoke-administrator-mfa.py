"""Verify administrator MFA in the production frontend against a disposable VPS database."""

import argparse
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pyotp
from cryptography.fernet import Fernet
from playwright.sync_api import expect, sync_playwright


def wait_http(url, process):
    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError("Disposable control plane exited before readiness")
            try:
                if client.get(url + "/healthz").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.25)
    raise TimeoutError("Disposable control plane did not become ready")


def capture(page, output, name, *, mask=()):
    for width, height, suffix in ((1440, 1000, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        if mask:
            expect(
                page.get_by_role("dialog")
                if name == "administrator-enrollment"
                else page.locator(".auth-card")
            ).to_be_visible()
        assert page.locator(".ant-modal-container").evaluate_all(
            "items => items.every(item => item.scrollWidth <= item.clientWidth + 1)"
        )
        page.screenshot(
            path=str(output / f"{name}-{suffix}.png"),
            full_page=True,
            animations="disabled",
            mask=list(mask),
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


def sign_in(page, password):
    page.get_by_label("Username", exact=True).fill("admin")
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign In", exact=True).click()


def prove(dialog, password, code):
    dialog.get_by_label("Current password", exact=True).fill(password)
    dialog.get_by_label("Authenticator or recovery code", exact=True).fill(code)
    dialog.get_by_role("button", name="Confirm", exact=True).click()


def accept_codes(dialog):
    codes = dialog.locator(".recovery-grid code").all_text_contents()
    assert len(codes) == len(set(codes)) == 10
    dialog.get_by_label("I have stored the recovery codes securely", exact=True).check()
    dialog.get_by_role("button", name="Done", exact=True).click()
    expect(dialog).not_to_be_visible()
    return codes


def exercise(url, password, output, *, reset_password, database):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url + "/access")
            sign_in(page, password)
            expect(
                page.get_by_role("heading", name="Access", exact=True)
            ).to_be_visible()
            panel = page.get_by_role(
                "region", name="Administrator security", exact=True
            )
            panel.get_by_role("button", name="Enable", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("Current password", exact=True).fill(password)
            dialog.get_by_role("button", name="Start enrollment", exact=True).click()
            secret_input = dialog.get_by_label("Authenticator secret", exact=True)
            expect(secret_input).to_have_value(re.compile(r"[A-Z2-7]{32}"))
            secret = secret_input.input_value()
            capture(
                page,
                output,
                "administrator-enrollment",
                mask=(secret_input, dialog.locator("img")),
            )
            dialog.get_by_label("Authenticator code", exact=True).fill(
                pyotp.TOTP(secret).now()
            )
            dialog.get_by_role("button", name="Confirm", exact=True).click()
            expect(dialog.locator(".recovery-grid code")).to_have_count(10)
            codes = accept_codes(dialog)
            panel.get_by_role("button", name="Require 2FA", exact=True).click()
            prove(dialog, password, codes[0])
            expect(dialog).not_to_be_visible()
            expect(
                panel.get_by_role("button", name="Disable", exact=True)
            ).to_be_disabled()
            capture(page, output, "administrator-security-enabled")

            page.get_by_role("button", name="Sign out", exact=True).click()
            sign_in(page, password)
            factor_input = page.get_by_label(
                "Authenticator or recovery code", exact=True
            )
            expect(factor_input).to_be_visible()
            assert (
                context.request.get(url + "/api/v1/auth/session").json()[
                    "authenticated"
                ]
                is False
            )
            assert context.request.get(url + "/api/v1/servers").status == 401
            capture(page, output, "administrator-login-challenge")
            factor_input.fill(codes[1])
            page.get_by_role("button", name="Verify", exact=True).click()
            expect(
                page.get_by_role("heading", name="Access", exact=True)
            ).to_be_visible()

            panel.get_by_role("button", name="New recovery codes", exact=True).click()
            prove(dialog, password, codes[2])
            expect(dialog.locator(".recovery-grid code")).to_have_count(10)
            replacement_codes = accept_codes(dialog)
            panel.get_by_role("button", name="Make optional", exact=True).click()
            prove(dialog, password, replacement_codes[0])
            expect(dialog).not_to_be_visible()
            panel.get_by_role("button", name="Disable", exact=True).click()
            prove(dialog, password, replacement_codes[1])
            expect(dialog).not_to_be_visible()
            expect(
                panel.get_by_role("button", name="Enable", exact=True)
            ).to_be_visible()
            storage = page.evaluate(
                "JSON.stringify({ ...localStorage, ...sessionStorage })"
            )
            for value in [password, secret, *codes, *replacement_codes]:
                assert value not in storage

            page.get_by_role("button", name="Sign out", exact=True).click()
            replacement = reset_password()
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE administrator_security_policy SET require_totp=1 WHERE id=1"
                )
            sign_in(page, replacement)
            mandatory_secret = page.get_by_label("Authenticator secret", exact=True)
            expect(mandatory_secret).to_be_visible()
            secret = mandatory_secret.input_value()
            assert (
                context.request.get(url + "/api/v1/auth/session").json()[
                    "authenticated"
                ]
                is False
            )
            capture(
                page,
                output,
                "administrator-required-enrollment",
                mask=(mandatory_secret, page.locator(".totp-qr")),
            )
            page.get_by_label("Authenticator code", exact=True).fill(
                pyotp.TOTP(secret).now()
            )
            page.get_by_role("button", name="Verify", exact=True).click()
            expect(page.locator(".recovery-grid code")).to_have_count(10)
            expect(
                page.get_by_role("button", name="Continue to Open Node", exact=True)
            ).to_be_disabled()
            page.get_by_label(
                "I have stored the recovery codes securely", exact=True
            ).check()
            page.get_by_role("button", name="Continue to Open Node", exact=True).click()
            expect(
                page.get_by_role("heading", name="Access", exact=True)
            ).to_be_visible()
            assert not errors, errors
        finally:
            context.close()
            browser.close()


def run(output):
    if sys.platform != "linux":
        raise SystemExit("Run this smoke on the isolated Linux VPS, not locally.")
    root = Path(__file__).resolve().parents[2]
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(
            "Choose a new or empty output directory; existing evidence is preserved."
        )
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (root / "frontend/dist/index.html").is_file():
        raise ValueError(
            "Build the production frontend on the VPS before running this smoke"
        )
    with tempfile.TemporaryDirectory(prefix="open-node-admin-mfa-") as temporary:
        work = Path(temporary)
        database = work / "mfa.db"
        password = secrets.token_urlsafe(32)
        environment = {
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("OPEN_NODE_")
            },
            "PYTHONPATH": str(root / "backend/app"),
            "OPEN_NODE_DATABASE_URL": f"sqlite:///{database}",
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_SUBSCRIBER_TOTP_KEY": Fernet.generate_key().decode(),
            "OPEN_NODE_FRONTEND_DIR": str(root / "frontend/dist"),
            "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "certificates"),
        }

        def administrator(action, value):
            subprocess.run(
                [sys.executable, "-m", "open_node.admin", action, "--password-stdin"],
                cwd=work,
                env=environment,
                input=value + "\n",
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )

        def reset_password():
            replacement = secrets.token_urlsafe(32)
            administrator("reset-password", replacement)
            return replacement

        administrator("create", password)
        with socket.socket() as listener, (work / "service.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "open_node.main:app",
                    "--fd",
                    str(listener.fileno()),
                    "--no-access-log",
                ],
                cwd=work,
                env=environment,
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=log,
            )
            try:
                wait_http(url, process)
                exercise(
                    url,
                    password,
                    output,
                    reset_password=reset_password,
                    database=database,
                )
            except Exception:
                log.seek(0)
                print(log.read().replace(password, "[redacted]"), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    print(
        "PASS administrator MFA production-browser flows and isolated cleanup",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
