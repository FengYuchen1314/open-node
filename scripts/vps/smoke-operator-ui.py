"""Run browser-level operator workflows against disposable, loopback-only services."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import expect, sync_playwright


def wait_http(url: str, process: subprocess.Popen) -> None:
    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError(f"Preview process exited: {process.returncode}")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.25)
    raise TimeoutError(f"Service did not start: {url}")


def sign_in(page, password: str) -> None:
    page.get_by_label("Username", exact=True).fill("admin")
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign In", exact=True).click()


def check_layout(page) -> None:
    page.wait_for_function("document.documentElement.scrollWidth <= innerWidth + 1", timeout=5000)
    page.wait_for_function("""() =>
        [...document.querySelectorAll('form input:not([hidden]), form button')]
        .filter(el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden')
        .every(el => {
            const box = el.getBoundingClientRect();
            return box.x >= 0 && box.right <= innerWidth + 1;
        })
    """, timeout=5000)
    for control in page.locator("form input:not([hidden]), form button").all():
        if control.is_visible():
            box = control.bounding_box()
            assert (
                box and box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"] + 1
            ), {"box": box, "control": control.evaluate("el => el.outerHTML.slice(0, 300)")}


def exercise(url: str, password: str, output: Path, database_url: str) -> None:
    from open_node.services.auth import AuthStore, OperatorSession
    from sqlalchemy import update

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(urlparse(request.url).path))
        page.goto(f"{url}/config")
        expect(page.get_by_role("heading", name="Administrator Sign-In")).to_be_visible()
        assert not any(path.startswith("/api/v1/servers") for path in requests)
        check_layout(page)
        page.screenshot(path=output / "login-desktop.png", full_page=True, animations="disabled")
        sign_in(page, "incorrect-password")
        expect(page.get_by_text("Invalid username or password", exact=True)).to_be_visible()
        sign_in(page, password)
        expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_visible()
        page.get_by_role("link", name="Overview", exact=True).click()
        page.get_by_label("Name", exact=True).fill("browser-smoke-edge")
        page.get_by_role("button", name="Create server", exact=True).click()
        expect(page.get_by_text("browser-smoke-edge", exact=True).first).to_be_visible()
        page.reload()
        expect(page.get_by_text("browser-smoke-edge", exact=True).first).to_be_visible()
        print("PASS desktop sign-in, authenticated server creation, and reload", flush=True)

        page.goto(f"{url}/config")
        page.get_by_role("tab", name="Files", exact=True).click()
        nginx_form = page.locator("form").filter(has=page.get_by_text("Nginx file", exact=True))
        expect(nginx_form.get_by_label("Read path", exact=True)).to_have_value("servers/site.conf")
        expect(nginx_form.get_by_label("Write path", exact=True)).to_have_value("servers/site.conf")
        page.get_by_role("tab", name="Sites", exact=True).click()
        payload = page.get_by_label("Payload", exact=True)
        expect(payload).to_be_visible()
        assert json.loads(payload.input_value()) == {"domain": "example.com"}
        for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_function("""() => {
                const tab = document.querySelector('[role=tab][aria-selected=true]');
                const track = tab.closest('.v-slide-group__container').getBoundingClientRect();
                const box = tab.getBoundingClientRect();
                return box.left >= track.left - 1 && box.right <= track.right + 1;
            }""", timeout=5000)
            page.screenshot(path=output / f"nginx-sites-{name}.png", full_page=True,
                            animations="disabled")
            try:
                check_layout(page)
            except Exception:
                print(page.evaluate("""() => [...document.querySelectorAll('body *')]
                    .filter(el => el.getClientRects().length &&
                        el.getBoundingClientRect().right > innerWidth + 1)
                    .slice(0, 20).map(el => ({tag: el.tagName, class: el.className,
                        width: el.getBoundingClientRect().width,
                        right: el.getBoundingClientRect().right}))
                """), file=sys.stderr)
                raise
        page.set_viewport_size({"width": 1440, "height": 900})
        print("PASS owned Nginx paths and SSL form on desktop/mobile", flush=True)

        page.get_by_role("tab", name="Runtime", exact=True).click()
        tunnel = page.locator("form.runtime-tunnel-deploy-form")
        tunnel.get_by_label("Domain", exact=True).fill("localhost")
        expect(tunnel.get_by_label("Static root", exact=True)).to_have_value("")
        tunnel.locator("summary").click()
        submit = tunnel.get_by_role("button", name="Deploy tunnel", exact=True)
        expect(submit).to_be_enabled()
        public = tunnel.get_by_label("Public port", exact=True)
        public.fill("8001")
        expect(submit).to_be_disabled()
        public.fill("70000")
        expect(submit).to_be_disabled()
        public.fill("443")
        expect(submit).to_be_enabled()
        for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
            page.set_viewport_size({"width": width, "height": height})
            tunnel.scroll_into_view_if_needed()
            check_layout(page)
            for label in tunnel.locator(".runtime-deploy-toggle-row .v-label").all():
                text_height = label.evaluate("""el => {
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    return range.getBoundingClientRect().height;
                }""")
                assert text_height <= 28, {"label": label.inner_text(), "height": text_height}
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_function("scrollY === 0")
            page.screenshot(path=output / f"tunnel-{name}.png", full_page=True, animations="disabled")
        with page.expect_request("**/xray/runtime/tunnel-deploy") as sent:
            submit.click()
        body = sent.value.post_data_json
        assert body["site_value"] is None and body["listen_address"] == "0.0.0.0"
        assert body["listen_port"] == 443 and body["nginx_port"] == 8001
        assert body["forward_port"] == 46174 and body["api_port"] == 46736
        assert body["metrics_port"] == 38889
        page.set_viewport_size({"width": 1440, "height": 900})
        print("PASS tunnel defaults, listener validation and request payload on desktop/mobile", flush=True)

        page.get_by_role("link", name="Access", exact=True).click()
        expect(page.get_by_role("heading", name="Change Password", exact=True)).to_be_visible()
        check_layout(page)
        page.screenshot(path=output / "access-desktop.png", full_page=True, animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("button", name="Toggle navigation")).to_be_visible()
        check_layout(page)
        page.screenshot(path=output / "access-mobile.png", full_page=True, animations="disabled")
        page.get_by_role("button", name="Toggle navigation").click()
        expect(page.get_by_role("link", name="Overview", exact=True)).to_be_visible()
        page.get_by_role("link", name="Access", exact=True).click()
        page.get_by_role("button", name="Close navigation").click()
        replacement = secrets.token_urlsafe(24)
        page.get_by_label("Current password", exact=True).fill(password)
        page.get_by_label("New password", exact=True).fill(replacement)
        page.get_by_label("Confirm new password", exact=True).fill(replacement)
        page.get_by_role("button", name="Change Password", exact=True).click()
        expect(page.get_by_role("heading", name="Administrator Sign-In")).to_be_visible()
        check_layout(page)
        page.screenshot(path=output / "login-mobile.png", full_page=True, animations="disabled")
        sign_in(page, password)
        expect(page.get_by_text("Invalid username or password", exact=True)).to_be_visible()
        sign_in(page, replacement)
        expect(page.get_by_role("heading", name="Change Password", exact=True)).to_be_visible()
        page.get_by_role("button", name="Sign out", exact=True).click()
        expect(page.get_by_role("heading", name="Administrator Sign-In")).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name="Administrator Sign-In")).to_be_visible()
        print(
            "PASS mobile navigation, password change, old-password rejection, and sign-out",
            flush=True,
        )

        sign_in(page, replacement)
        expect(page.get_by_role("heading", name="Change Password", exact=True)).to_be_visible()
        store = AuthStore(database_url)
        with store.session.begin() as db:
            db.execute(update(OperatorSession).values(last_seen_at=0))
        store.engine.dispose()
        page.get_by_role("button", name="Toggle navigation").click()
        page.get_by_role("link", name="Overview", exact=True).click()
        expect(page.get_by_role("heading", name="Administrator Sign-In")).to_be_visible()
        assert not errors, errors
        assert page.evaluate("localStorage.length") == 0
        print(
            "PASS expired session returns to sign-in without browser-stored credentials", flush=True
        )
        browser.close()


def run(output: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-node-ui-") as temporary:
        work = Path(temporary)
        password = secrets.token_urlsafe(32)
        database_url = f"sqlite:///{work / 'ui.db'}"
        env = {
            **os.environ,
            "PYTHONPATH": str(root / "backend" / "app"),
            "OPEN_NODE_DATABASE_URL": database_url,
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
        }
        subprocess.run(
            [sys.executable, "-m", "open_node.admin", "create", "--password-stdin"],
            cwd=work,
            env=env,
            input=password + "\n",
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        backend = frontend = None
        with socket.socket() as listener, (work / "services.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            backend_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with socket.socket() as frontend_socket:
                frontend_socket.bind(("127.0.0.1", 0))
                port = frontend_socket.getsockname()[1]
            try:
                backend = subprocess.Popen(
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
                frontend = subprocess.Popen(
                    [
                        "node",
                        "node_modules/vite/bin/vite.js",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--strictPort",
                    ],
                    cwd=root / "frontend",
                    env={**os.environ, "OPEN_NODE_DEV_API_TARGET": backend_url},
                    stdout=log,
                    stderr=log,
                )
                url = f"http://127.0.0.1:{port}"
                wait_http(f"{backend_url}/healthz", backend)
                wait_http(url, frontend)
                exercise(url, password, output, database_url)
            except Exception:
                log.seek(0)
                print(log.read().replace(password, "[redacted]"), file=sys.stderr)
                raise
            finally:
                for process in [frontend, backend]:
                    if process:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
    print(f"PASS operator UI; screenshots: {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
