"""Run browser-level operator workflows against disposable, loopback-only services."""

import argparse
import importlib.util
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
    page.wait_for_function(
        "document.documentElement.scrollWidth <= innerWidth + 1", timeout=5000
    )
    page.wait_for_function(
        """() =>
        [...document.querySelectorAll('form input:not([hidden]), form button')]
        .filter(el => el.checkVisibility({checkVisibilityCSS: true}))
        .every(el => {
            const box = el.getBoundingClientRect();
            return box.x >= 0 && box.right <= innerWidth + 1;
        })
    """,
        timeout=5000,
    )
    for control in page.locator("form input:not([hidden]), form button").all():
        if control.is_visible():
            box = control.bounding_box()
            assert (
                box
                and box["x"] >= 0
                and box["x"] + box["width"] <= page.viewport_size["width"] + 1
            ), {
                "box": box,
                "control": control.evaluate("el => el.outerHTML.slice(0, 300)"),
            }


def certificates_ui(page, url, output):
    spec = importlib.util.spec_from_file_location(
        "certificate_ui_fixture", Path(__file__).with_name("smoke-nginx.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    cert, key, _ = fixture.certificate()
    page.goto(f"{url}/certificates")
    expect(page.get_by_role("heading", name="Certificates", exact=True)).to_be_visible()
    page.get_by_role("tab", name="DNS providers", exact=True).click()
    page.get_by_role("button", name="Add DNS provider", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Provider name", exact=True).fill("Browser DNS")
    secret = secrets.token_urlsafe(24)
    token = dialog.get_by_label("CF_DNS_API_TOKEN", exact=True)
    expect(token).to_have_attribute("type", "password")
    token.fill(secret)
    dialog.get_by_role("button", name="Save provider", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_text("Browser DNS", exact=True)).to_be_visible()
    page.get_by_role("row").filter(has_text="Browser DNS").get_by_role(
        "button", name="Rotate DNS credentials"
    ).click()
    expect(dialog.get_by_label("CF_DNS_API_TOKEN", exact=True)).to_have_value("")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    page.get_by_role("tab", name="Certificates", exact=True).click()
    page.get_by_role("button", name="New certificate", exact=True).click()
    dialog.get_by_label("Certificate name", exact=True).fill("Pending wildcard")
    dialog.get_by_label("DNS names", exact=True).fill("example.com *.example.com")
    dialog.get_by_label("Account email", exact=True).fill("operator@example.com")
    create = dialog.get_by_role("button", name="Create certificate", exact=True)
    expect(create).to_be_disabled()
    dialog.get_by_label("I accept this CA's terms of service", exact=True).check()
    for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        check_layout(page)
        page.screenshot(
            path=output / f"certificate-form-{name}.png",
            full_page=True,
            animations="disabled",
        )
    create.click()
    expect(dialog).not_to_be_visible()
    expect(
        page.get_by_role("button", name="Pending wildcard", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Import PEM", exact=True).click()
    dialog.get_by_label("Certificate name", exact=True).fill("Imported localhost")
    dialog.get_by_label("Certificate PEM", exact=True).fill(cert)
    dialog.get_by_label("Private key PEM", exact=True).fill(key)
    dialog.get_by_role("button", name="Import certificate", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.get_by_role("button", name="Imported localhost", exact=True).click()
    expect(
        page.locator(".ant-card-head-title").filter(has_text="Imported localhost")
    ).to_be_visible()
    expect(page.get_by_label("Auto-renew", exact=True)).to_be_disabled()
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Download certificate", exact=True).click()
    assert Path(downloaded.value.path()).read_text() == cert
    page.get_by_role("button", name="Download private key", exact=True).click()
    private_key_dialog = page.get_by_role(
        "dialog", name="Download the private key?", exact=True
    )
    with page.expect_download() as downloaded:
        private_key_dialog.get_by_role("button", name="Confirm", exact=True).click()
    assert Path(downloaded.value.path()).read_text() == key
    for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
        page.set_viewport_size({"width": width, "height": height})
        check_layout(page)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(
            path=output / f"certificates-{name}.png",
            full_page=True,
            animations="disabled",
        )
    page.get_by_role("button", name="Import PEM", exact=True).click()
    expect(dialog.get_by_label("Private key PEM", exact=True)).to_have_value("")
    expect(dialog.get_by_label("Certificate PEM", exact=True)).to_have_value("")
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    assert secret not in page.content() and key not in page.content()
    storage = page.evaluate("JSON.stringify({ ...localStorage, ...sessionStorage })")
    assert key not in storage and secret not in storage
    page.set_viewport_size({"width": 1440, "height": 900})
    print(
        "PASS certificate forms, terms, secret clearing and explicit PEM downloads on desktop/mobile",
        flush=True,
    )


def probe_administration_ui(page, url, output):
    page.goto(f"{url}/probe")
    save = page.get_by_role("button", name="Save settings", exact=True)
    expect(save).to_be_enabled()
    writes = []

    def record(request):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            writes.append(urlparse(request.url).path)

    page.on("request", record)
    try:
        refresh = page.get_by_label("Refresh seconds", exact=True)
        for invalid in ("-1", "0.4", "", "1e", "61"):
            before = len(writes)
            refresh.fill(invalid)
            refresh.press("Tab")
            refresh.press("Enter")
            save.click()
            expect(
                page.get_by_text(
                    "Refresh seconds must be a whole number from 1 to 60.",
                    exact=True,
                )
            ).to_be_visible()
            assert len(writes) == before, "Invalid refresh draft issued a write"
        refresh.fill("10")
        with page.expect_response(
            lambda response: (
                response.request.method == "PUT"
                and response.url.endswith("/api/v1/public/probe-settings")
            )
        ) as saved:
            save.click()
        assert saved.value.status == 200
        assert saved.value.json()["settings"]["refresh_interval_sec"] == 10

        page.get_by_label("Probe type", exact=True).click()
        page.locator(".ant-select-dropdown:visible").get_by_text(
            "System", exact=True
        ).click()
        interval = page.get_by_label("Interval seconds", exact=True)
        add = page.get_by_role("button", name="Add task", exact=True)
        for invalid in ("-1", "60.5", "", "86401"):
            before = len(writes)
            interval.fill(invalid)
            interval.press("Tab")
            interval.press("Enter")
            add.click()
            expect(
                page.get_by_text(
                    "Interval seconds must be a whole number from 60 to 86400.",
                    exact=True,
                )
            ).to_be_visible()
            assert len(writes) == before, "Invalid task interval issued a write"
        interval.fill("120")
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/api/v1/probe/tasks")
            )
        ) as created:
            add.click()
        assert created.value.status == 201
        task = created.value.json()["task"]
        assert task["kind"] == "system" and task["interval_sec"] == 120
        assert task["command_timeout_ms"] == 30000 and task["domains"] == []
        toggle = page.get_by_role(
            "switch", name=f"Enable probe task {task['id']}", exact=True
        )
        expect(toggle).to_be_checked()
        with page.expect_response(
            lambda response: (
                response.request.method == "PATCH"
                and response.url.endswith(f"/api/v1/probe/tasks/{task['id']}")
            )
        ) as updated:
            toggle.click()
        assert (
            updated.value.status == 200
            and updated.value.json()["task"]["enabled"] is False
        )
        page.reload()
        expect(toggle).not_to_be_checked()
        expect(refresh).to_have_value("10")

        generate = page.get_by_role("button", name="Generate", exact=True)
        expect(generate).to_be_enabled()
        generate.click()
        token_input = page.get_by_label("New token", exact=True)
        expect(token_input).to_be_visible()
        token = token_input.input_value()
        assert len(token) >= 32
        before = len(writes)
        generate.click()
        rotation = page.locator(".ant-popconfirm").filter(
            has=page.get_by_text("Generate a new Worker token?", exact=True)
        )
        expect(rotation).to_be_visible()
        rotation.get_by_role("button", name="Cancel", exact=True).click()
        expect(rotation).not_to_be_visible()
        assert len(writes) == before, "Cancelled token rotation issued a write"
        page.get_by_role("button", name="Clear", exact=True).click()
        revocation = page.locator(".ant-popconfirm").filter(
            has=page.get_by_text("Clear Worker token?", exact=True)
        )
        expect(revocation).to_be_visible()
        revocation.get_by_role("button", name="OK", exact=True).click()
        expect(revocation).not_to_be_visible()
        expect(token_input).not_to_be_visible()
        assert token not in page.content()
        assert token not in page.evaluate(
            "JSON.stringify({ ...localStorage, ...sessionStorage })"
        )
        for width, height, name in (
            (1440, 900, "desktop"),
            (390, 844, "mobile"),
            (320, 740, "narrow"),
        ):
            page.set_viewport_size({"width": width, "height": height})
            check_layout(page)
            for title in ("Scheduled probes", "Probe settings", "Probe nodes"):
                heading = page.get_by_text(title, exact=True)
                expect(heading).to_be_visible()
                assert heading.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (
                    title
                )
            scheduled = page.locator(".ant-card").filter(
                has=page.get_by_text("Scheduled probes", exact=True)
            )
            for control in (
                scheduled.locator(":scope > .ant-card-head").get_by_role("button").all()
            ):
                box = control.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
                    box
                )
            page.screenshot(
                path=output / f"probe-administration-{name}.png",
                full_page=True,
                animations="disabled",
            )
        page.set_viewport_size({"width": 1440, "height": 900})
    finally:
        page.remove_listener("request", record)
    print(
        "PASS production Probe settings, numeric write guards, tasks and Worker token lifecycle",
        flush=True,
    )


def exercise(
    url: str,
    password: str,
    output: Path,
    database_url: str,
    *,
    certificate_spki: str = "",
    agent_identity: dict | None = None,
) -> None:
    from open_node.services.auth import AuthStore, OperatorSession
    from sqlalchemy import update

    with sync_playwright() as playwright:
        args = (
            [f"--ignore-certificate-errors-spki-list={certificate_spki}"]
            if certificate_spki
            else []
        )
        browser = playwright.chromium.launch(args=args)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(urlparse(request.url).path))
        page.goto(f"{url}/config")
        expect(
            page.get_by_role("heading", name="Administrator Sign-In")
        ).to_be_visible()
        assert not any(path.startswith("/api/v1/servers") for path in requests)
        check_layout(page)
        page.screenshot(
            path=output / "login-desktop.png", full_page=True, animations="disabled"
        )
        sign_in(page, "incorrect-password")
        expect(
            page.get_by_text("Invalid username or password", exact=True)
        ).to_be_visible()
        sign_in(page, password)
        expect(page.get_by_role("button", name="Sign out", exact=True)).to_be_visible()
        page.get_by_role("menuitem", name="Overview", exact=True).click()
        page.get_by_label("Name", exact=True).fill("browser-smoke-edge")
        page.get_by_role("button", name="Create server", exact=True).click()
        expect(page.get_by_text("browser-smoke-edge", exact=True).first).to_be_visible()
        page.reload()
        expect(page.get_by_text("browser-smoke-edge", exact=True).first).to_be_visible()
        print(
            "PASS desktop sign-in, authenticated server creation, and reload",
            flush=True,
        )

        page.set_viewport_size({"width": 320, "height": 740})
        title = page.locator(".application-header").get_by_role(
            "heading", name="Open Node", exact=True
        )
        page.wait_for_function(
            """el => {
            const range = document.createRange();
            range.selectNodeContents(el);
            const text = range.getBoundingClientRect();
            const box = el.getBoundingClientRect();
            return text.left >= box.left - 1 && text.right <= box.right + 1;
        }""",
            arg=title.element_handle(),
            timeout=5000,
        )
        check_layout(page)
        page.screenshot(path=output / "overview-narrow.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 900})

        page.goto(f"{url}/config")
        page.get_by_role("tab", name="Files", exact=True).click()
        nginx_form = (
            page.locator(".ant-card")
            .filter(has=page.get_by_text("Nginx file", exact=True))
            .last.locator("form")
        )
        expect(nginx_form.get_by_label("Read path", exact=True)).to_have_value(
            "servers/site.conf"
        )
        expect(nginx_form.get_by_label("Write path", exact=True)).to_have_value(
            "servers/site.conf"
        )
        page.get_by_role("tab", name="Sites", exact=True).click()
        payload = page.get_by_label("Payload", exact=True)
        expect(payload).to_be_visible()
        assert json.loads(payload.input_value()) == {"domain": "example.com"}
        for width, height, name in ((1440, 900, "desktop"), (390, 844, "mobile")):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_function(
                """() => {
                const tab = document.querySelector('[role=tab][aria-selected=true]');
                const track = tab.closest('.ant-tabs-nav-wrap').getBoundingClientRect();
                const box = tab.getBoundingClientRect();
                return box.left >= track.left - 1 && box.right <= track.right + 1;
            }""",
                timeout=5000,
            )
            page.screenshot(
                path=output / f"nginx-sites-{name}.png",
                full_page=True,
                animations="disabled",
            )
            try:
                check_layout(page)
            except Exception:
                print(
                    page.evaluate("""() => [...document.querySelectorAll('body *')]
                    .filter(el => el.getClientRects().length &&
                        el.getBoundingClientRect().right > innerWidth + 1)
                    .slice(0, 20).map(el => ({tag: el.tagName, class: el.className,
                        width: el.getBoundingClientRect().width,
                        right: el.getBoundingClientRect().right}))
                """),
                    file=sys.stderr,
                )
                raise
        page.set_viewport_size({"width": 1440, "height": 900})
        print("PASS owned Nginx paths and SSL form on desktop/mobile", flush=True)

        page.get_by_role("tab", name="Runtime", exact=True).click()
        tunnel = page.locator("form").filter(
            has=page.get_by_role("button", name="Deploy tunnel", exact=True)
        )
        tunnel.get_by_label("Domain", exact=True).fill("localhost")
        expect(tunnel.get_by_label("Static root", exact=True)).to_have_value("")
        tunnel.get_by_role("button", name="Listeners", exact=True).click()
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
            for label in tunnel.locator(
                ".ant-form-item:has(button[role=switch]) .ant-form-item-label label, .ant-checkbox-label"
            ).all():
                text_height = label.evaluate("""el => {
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    return range.getBoundingClientRect().height;
                }""")
                assert text_height <= 28, {
                    "label": label.inner_text(),
                    "height": text_height,
                }
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_function("scrollY === 0")
            page.screenshot(
                path=output / f"tunnel-{name}.png",
                full_page=True,
                animations="disabled",
            )
        with page.expect_request("**/xray/runtime/tunnel-deploy") as sent:
            submit.click()
        body = sent.value.post_data_json
        assert body["site_value"] is None and body["listen_address"] == "0.0.0.0"
        assert body["listen_port"] == 443 and body["nginx_port"] == 8001
        assert body["forward_port"] == 46174 and body["api_port"] == 46736
        assert body["metrics_port"] == 38889
        page.set_viewport_size({"width": 1440, "height": 900})
        print(
            "PASS tunnel defaults, listener validation and request payload on desktop/mobile",
            flush=True,
        )

        certificates_ui(page, url, output)
        probe_administration_ui(page, url, output)

        page.get_by_role("menuitem", name="Access", exact=True).click()
        expect(page.get_by_role("heading", name="Access", exact=True)).to_be_visible()
        if agent_identity:
            expect(
                page.get_by_text(agent_identity["public_key"], exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text(agent_identity["fingerprint"], exact=True)
            ).to_be_visible()
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=url)
            page.get_by_role("button", name="Copy public key", exact=True).click()
            assert (
                page.evaluate("navigator.clipboard.readText()")
                == agent_identity["public_key"]
            )
            print(
                "PASS public Agent identity, fingerprint and clipboard copy", flush=True
            )
        else:
            expect(page.get_by_text("Not configured", exact=True)).to_be_visible()
        check_layout(page)
        page.screenshot(
            path=output / "access-desktop.png", full_page=True, animations="disabled"
        )
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.get_by_role("button", name="Toggle navigation")).to_be_visible()
        check_layout(page)
        page.screenshot(
            path=output / "access-mobile.png", full_page=True, animations="disabled"
        )
        page.get_by_role("button", name="Toggle navigation").click()
        expect(
            page.get_by_role("menuitem", name="Overview", exact=True)
        ).to_be_visible()
        page.get_by_role("menuitem", name="Access", exact=True).click()
        expect(
            page.get_by_role("dialog", name="Open Node", exact=True)
        ).not_to_be_visible()
        page.get_by_role("button", name="Toggle navigation").click()
        page.get_by_role("dialog", name="Open Node", exact=True).get_by_role(
            "button", name="Close", exact=True
        ).click()
        replacement = secrets.token_urlsafe(24)
        page.get_by_label("Current password", exact=True).fill(password)
        page.get_by_label("New password", exact=True).fill(replacement)
        page.get_by_label("Confirm new password", exact=True).fill(replacement)
        page.get_by_role("button", name="Change Password", exact=True).click()
        expect(
            page.get_by_role("heading", name="Administrator Sign-In")
        ).to_be_visible()
        check_layout(page)
        page.screenshot(
            path=output / "login-mobile.png", full_page=True, animations="disabled"
        )
        sign_in(page, password)
        expect(
            page.get_by_text("Invalid username or password", exact=True)
        ).to_be_visible()
        sign_in(page, replacement)
        expect(page.get_by_role("heading", name="Access", exact=True)).to_be_visible()
        page.get_by_role("button", name="Sign out", exact=True).click()
        expect(
            page.get_by_role("heading", name="Administrator Sign-In")
        ).to_be_visible()
        page.reload()
        expect(
            page.get_by_role("heading", name="Administrator Sign-In")
        ).to_be_visible()
        print(
            "PASS mobile navigation, password change, old-password rejection, and sign-out",
            flush=True,
        )

        sign_in(page, replacement)
        expect(page.get_by_role("heading", name="Access", exact=True)).to_be_visible()
        store = AuthStore(database_url)
        with store.session.begin() as db:
            db.execute(update(OperatorSession).values(last_seen_at=0))
        store.engine.dispose()
        page.get_by_role("button", name="Toggle navigation").click()
        page.get_by_role("menuitem", name="Overview", exact=True).click()
        expect(
            page.get_by_role("heading", name="Administrator Sign-In")
        ).to_be_visible()
        assert not errors, errors
        assert page.evaluate("localStorage.length") == 0
        print(
            "PASS expired session returns to sign-in without browser-stored credentials",
            flush=True,
        )
        browser.close()


def run(output: Path) -> None:
    from open_node.services.secure_channel import AgentIdentity

    if sys.platform != "linux":
        raise SystemExit("Run this smoke on the isolated Linux VPS, not locally.")
    root = Path(__file__).resolve().parents[2]
    assets = root / "frontend" / "dist"
    if not (assets / "index.html").is_file():
        raise SystemExit("Build the production frontend bundle before this smoke.")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit(
            "Choose a new or empty output directory; existing evidence is preserved."
        )
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="open-node-ui-") as temporary:
        work = Path(temporary)
        identity = AgentIdentity.create(work / "identity" / "seed")
        password = secrets.token_urlsafe(32)
        database_url = f"sqlite:///{work / 'ui.db'}"
        env = {
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("OPEN_NODE_")
            },
            "PYTHONPATH": str(root / "backend" / "app"),
            "OPEN_NODE_DATABASE_URL": database_url,
            "OPEN_NODE_SESSION_COOKIE_SECURE": "false",
            "OPEN_NODE_AGENT_IDENTITY_FILE": str(work / "identity" / "seed"),
            "OPEN_NODE_CERTIFICATE_STATE_DIR": str(work / "certificates"),
            "OPEN_NODE_FRONTEND_DIR": str(assets),
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
        backend = None
        with socket.socket() as listener, (work / "services.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            backend_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            try:
                backend = subprocess.Popen(
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
                    env=env,
                    pass_fds=(listener.fileno(),),
                    stdout=log,
                    stderr=log,
                )
                url = backend_url
                wait_http(f"{backend_url}/healthz", backend)
                wait_http(url, backend)
                exercise(
                    url,
                    password,
                    output,
                    database_url,
                    agent_identity=identity.public_metadata(),
                )
            except Exception:
                log.seek(0)
                print(log.read().replace(password, "[redacted]"), file=sys.stderr)
                raise
            finally:
                for process in [backend]:
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
