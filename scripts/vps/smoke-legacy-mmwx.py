"""Exercise MMWX identity export/import, browser login, and a live legacy link."""

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import bcrypt
import httpx
import pyotp
from cryptography.fernet import Fernet
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts/migrations/export-mmwx-identities.py"
SPEC = importlib.util.spec_from_file_location(
    "legacy_subscriber_smoke", Path(__file__).with_name("smoke-subscriber-account.py")
)
subscriber = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subscriber)
native, runtime, servers = subscriber.native, subscriber.runtime, subscriber.servers


def wait_http(url, process):
    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError(f"preview exited: {process.returncode}")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.25)
    raise TimeoutError(f"service did not start: {url}")


def legacy_database(path, password, totp_secret, recovery_code, token):
    with sqlite3.connect(path) as database:
        database.executescript(
            """
            CREATE TABLE users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                email TEXT,
                nickname TEXT,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                totp_secret TEXT NOT NULL,
                totp_enabled INTEGER NOT NULL,
                recovery_codes TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE user_tokens (
                username TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                user_short_code TEXT NOT NULL,
                custom_user_short_code TEXT NOT NULL
            );
            """
        )
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        recovery_hash = hashlib.sha256(recovery_code.encode()).hexdigest()
        database.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "alice",
                password_hash,
                "legacy@example.com",
                "Legacy Alice",
                "admin",
                1,
                totp_secret,
                1,
                json.dumps([recovery_hash]),
                "2026-01-02T03:04:05Z",
            ),
        )
        database.execute(
            "INSERT INTO user_tokens VALUES (?, ?, ?, ?)",
            ("alice", token, "lga", "legacy_link"),
        )


@contextmanager
def operator_client(url, password):
    with httpx.Client(
        base_url=url,
        headers={"X-Open-Node-Client": "browser"},
        trust_env=False,
        timeout=15,
    ) as client:
        session = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": password}
        )
        session.raise_for_status()
        client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
        yield client


def catalog(client, port):
    server = (
        client.post(
            "/api/v1/servers", json={"name": "legacy-edge", "domain": "127.0.0.1"}
        )
        .raise_for_status()
        .json()["server"]
    )
    node = (
        client.post(
            "/api/v1/nodes",
            json={
                "name": "Legacy VLESS",
                "server_id": server["id"],
                "protocol": "vless",
                "node_type": "physical",
                "inbound_tag": "legacy-vless",
                "tags": ["legacy"],
                "client_template": {"email": "{username}__legacy"},
                "config": {
                    "name": "Legacy VLESS",
                    "type": "vless",
                    "server": "127.0.0.1",
                    "port": port,
                    "network": "tcp",
                    "tls": False,
                },
            },
        )
        .raise_for_status()
        .json()["node"]
    )
    plan = (
        client.post(
            "/api/v1/plans",
            json={"name": "Legacy", "traffic_limit_gb": 1, "node_ids": [node["id"]]},
        )
        .raise_for_status()
        .json()["plan"]
    )
    return plan["id"]


def capture_dialog(page, output):
    dialog = page.get_by_role("dialog")
    for width, height, suffix in (
        (1440, 900, "desktop"),
        (390, 844, "mobile"),
        (320, 740, "narrow"),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(200)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert dialog.evaluate(
            "element => element.scrollWidth <= element.clientWidth + 1"
        )
        page.screenshot(
            path=output / f"legacy-import-{suffix}.png",
            full_page=True,
            animations="disabled",
        )
    page.set_viewport_size({"width": 1440, "height": 900})


def browser_import(url, admin_password, bundle, output, secrets_to_hide):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        try:
            page.goto(url + "/subscriptions")
            page.get_by_label("Username", exact=True).fill("admin")
            page.get_by_label("Password", exact=True).fill(admin_password)
            page.get_by_role("button", name="Sign In", exact=True).click()
            button = page.get_by_role("button", name="MMWX identities", exact=True)
            expect(button).to_be_visible(timeout=15000)
            button.click()
            dialog = page.get_by_role("dialog")
            dialog.locator('input[type="file"]').set_input_files(bundle)
            expect(dialog.get_by_text(bundle.name, exact=True)).to_be_visible()
            dialog.get_by_role("button", name="Preview", exact=True).click()
            expect(dialog.get_by_text("Users 1", exact=True)).to_be_visible()
            expect(dialog.get_by_text("TOTP 1", exact=True)).to_be_visible()
            expect(
                dialog.get_by_text(
                    "alice: source administrator will import as subscriber"
                )
            )
            capture_dialog(page, output)
            dialog.get_by_label("Confirm user count (1)", exact=True).fill("1")
            dialog.get_by_role("button", name="Import", exact=True).click()
            expect(
                dialog.get_by_text("Imported 1 identities", exact=True)
            ).to_be_visible()
            expect(dialog.locator('input[type="file"]')).to_have_value("")
            assert dialog.get_by_text("No file selected", exact=True).is_visible()
            content = page.content()
            storage = page.evaluate(
                "JSON.stringify({ ...localStorage, ...sessionStorage })"
            )
            assert all(
                secret not in content and secret not in storage
                for secret in secrets_to_hide
            )
            dialog.get_by_role("button", name="Close", exact=True).click()
            expect(page.get_by_text("Legacy Alice", exact=True).last).to_be_visible()
            assert not errors, errors
            assert all(request.startswith((url, "data:")) for request in requests)
        finally:
            context.close()
            browser.close()


def browser_subscriber(url, password, totp_secret, output):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(url + "/account")
            page.get_by_label("Username", exact=True).fill("alice")
            page.get_by_label("Password", exact=True).fill(password)
            page.get_by_role("button", name="Sign In", exact=True).click()
            expect(
                page.get_by_role("heading", name="Two-Factor Verification")
            ).to_be_visible()
            page.get_by_label("Authenticator or recovery code", exact=True).fill(
                pyotp.TOTP(totp_secret).now()
            )
            page.get_by_role("button", name="Verify", exact=True).click()
            expect(
                page.get_by_role("heading", name="Legacy", exact=True)
            ).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth + 1"
            )
            page.screenshot(
                path=output / "legacy-account-mobile.png",
                full_page=True,
                animations="disabled",
            )
            assert not errors, errors
        finally:
            context.close()
            browser.close()


def recovery_login(url, password, recovery_code):
    with httpx.Client(base_url=url, trust_env=False, timeout=15) as client:
        headers = {"X-Open-Node-Client": "browser"}
        challenge = (
            client.post(
                "/api/v1/account/login",
                json={"username": "alice", "password": password},
                headers=headers,
            )
            .raise_for_status()
            .json()["challenge"]
        )
        verified = (
            client.post(
                "/api/v1/account/login/verify",
                json={"challenge": challenge, "code": recovery_code},
                headers=headers,
            )
            .raise_for_status()
            .json()
        )
        assert verified["authenticated"]


def live_link(work, xray, port, server_client, exported):
    config = work / "legacy-server.json"
    runtime.write_private(
        config,
        {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "legacy-vless",
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "vless",
                    "settings": {"decryption": "none", "clients": [server_client]},
                }
            ],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        },
    )
    with (
        runtime.process(
            work, "legacy-server", [str(xray), "run", "-config", str(config)]
        ),
        native.echo_server(work) as (echo, _),
    ):
        runtime.poll("legacy VLESS server starts", lambda: runtime.port_open(port))
        with (
            servers.exported_client(work, xray, exported) as socks,
            native.connect(socks, echo) as connection,
        ):
            native.transfer(connection, 8192)


def run(args):
    args.xray = args.xray.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-node-legacy-") as temporary:
        work = Path(temporary)
        source, target = work / "mmwx.db", work / "open-node.db"
        bundle = work / "identities.json"
        password, admin_password = secrets.token_urlsafe(24), secrets.token_urlsafe(28)
        recovery_code, token = (
            secrets.token_hex(4),
            "legacy-" + secrets.token_urlsafe(28),
        )
        totp_secret = pyotp.random_base32()
        legacy_database(source, password, totp_secret, recovery_code, token)
        subprocess.run(
            [sys.executable, str(EXPORTER), str(source), str(bundle)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert bundle.stat().st_mode & 0o777 == 0o600

        env = {
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("OPEN_NODE_")
            },
            "PYTHONPATH": str(ROOT / "backend/app"),
            "OPEN_NODE_DATABASE_URL": f"sqlite:///{target}",
            "OPEN_NODE_FRONTEND_DIR": str(ROOT / "frontend/dist"),
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_SUBSCRIBER_TOTP_KEY": Fernet.generate_key().decode(),
        }
        subprocess.run(
            [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
            cwd=work,
            env=env,
            input=admin_password + "\n",
            text=True,
            capture_output=True,
            check=True,
        )
        process = None
        with socket.socket() as listener, (work / "backend.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "open_node.main:app",
                        "--fd",
                        str(listener.fileno()),
                    ],
                    cwd=work,
                    env=env,
                    pass_fds=(listener.fileno(),),
                    stdout=log,
                    stderr=log,
                )
                wait_http(url + "/healthz", process)
                with operator_client(url, admin_password) as operator:
                    port = runtime.free_port()
                    plan_id = catalog(operator, port)
                    browser_import(
                        url,
                        admin_password,
                        bundle,
                        args.output,
                        [password, totp_secret, recovery_code, token],
                    )
                    assigned = (
                        operator.post(
                            "/api/v1/users/alice/plan", json={"plan_id": plan_id}
                        )
                        .raise_for_status()
                        .json()
                    )
                    server_client = assigned["provisioning_batches"][0]["body"][
                        "inbound_clients"
                    ][0]["client"]
                    public = (
                        httpx.get(
                            url + "/api/v1/subscribe/" + token,
                            params={"format": "xray"},
                            trust_env=False,
                        )
                        .raise_for_status()
                        .json()
                    )
                    for key in ("lga", "legacy_link"):
                        response = httpx.get(
                            url + "/api/v1/subscribe/" + key,
                            params={"format": "xray"},
                            trust_env=False,
                        )
                        response.raise_for_status()
                        assert response.json() == public
                    live_link(work, args.xray, port, server_client, public)
                    browser_subscriber(url, password, totp_secret, args.output)
                    recovery_login(url, password, recovery_code)
                with sqlite3.connect(target) as database:
                    password_hash, recovery = database.execute(
                        "SELECT password_hash, recovery_hashes FROM subscriber_accounts "
                        "WHERE username='alice'"
                    ).fetchone()
                    assert password_hash.startswith("$argon2id$")
                    assert json.loads(recovery) == []
                    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
                print(
                    "PASS MMWX export, browser preview/import, bcrypt upgrade, TOTP, recovery and live legacy links",
                    flush=True,
                )
            except Exception:
                log.seek(0)
                hidden = log.read()
                for secret in (
                    password,
                    admin_password,
                    recovery_code,
                    totp_secret,
                    token,
                ):
                    hidden = hidden.replace(secret, "[redacted]")
                print(hidden, file=sys.stderr)
                raise
            finally:
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xray", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
