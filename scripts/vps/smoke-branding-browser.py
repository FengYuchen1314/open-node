"""Real production branding UI in a new, owned loopback-only network namespace.

The application, authentication, SQLite store and normal lifespan are unchanged.
Explicit response-delivery faults run only after real API responses. No product
clock, timeout, endpoint, authentication dependency or store method is replaced.
This script never builds assets and must receive a separately frozen main/probe
production bundle. It does not contact Telegram, an Agent or any public service.
"""

import argparse
import contextlib
import importlib.util
import json
import os
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "/api/v1/branding"
PRIVATE = "/api/v1/system-settings/branding"
SUBSCRIBER = "branding-browser-user"
LONG_SITE = "星河网络🌏" * 16
LONG_BRAND = "星河节点🚀" * 8
HTML_SITE = '<img src="https://b.invalid/x" onerror="window.__bx=2">'
HTML_BRAND = '<svg onload="window.__bx=1">'
FAULT_MARKER = "branding-fixture-private-error-marker"
HELPER_HASH = "7210494597bc55d0102aa8aa80a170ee3789236b422a66092dc1e620a3f1b09a"
VIEWPORTS = ((1440, 1000, "desktop"), (390, 844, "mobile"), (320, 844, "narrow"))
HELPERS = None


class GateFailure(RuntimeError):
    def __init__(self, code):
        self.code = code if re.fullmatch(r"[a-z0-9_]{1,100}", code) else "invalid_fixture_code"
        super().__init__(self.code)


def require(condition, code):
    if not condition:
        raise GateFailure(code)


def helpers():
    global HELPERS
    if HELPERS is None:
        import hashlib

        path = ROOT / "scripts/vps/smoke-notifications-browser.py"
        require(path.is_file() and not path.is_symlink(), "frozen_helper_missing")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == HELPER_HASH,
                "frozen_helper_changed")
        spec = importlib.util.spec_from_file_location("branding_readonly_helpers", path)
        require(spec is not None and spec.loader is not None, "helper_loader_missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        HELPERS = module
    return HELPERS


def write_json(path, value):
    helpers().write_json(path, value)


def digest(value):
    return helpers().digest(value)


def private_directory(path, *, empty=False):
    return helpers().private_directory(path, empty=empty)


def owner_processes(namespace, owner):
    result = []
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal() or int(path.name) == os.getpid():
            continue
        try:
            if os.readlink(path / "ns/net") != namespace:
                continue
            if ("BRANDING_GATE_OWNER=" + owner).encode() in (
                path / "environ"
            ).read_bytes().split(b"\0"):
                result.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


def cleanup_processes(namespace, owner):
    for signum in (signal.SIGTERM, signal.SIGKILL):
        for identifier in owner_processes(namespace, owner):
            with contextlib.suppress(ProcessLookupError):
                os.kill(identifier, signum)
        deadline = time.monotonic() + 3
        while owner_processes(namespace, owner) and time.monotonic() < deadline:
            time.sleep(0.1)
    require(not owner_processes(namespace, owner), "owned_process_cleanup_failed")


def protected_files():
    names = (
        "backend/app", "frontend/src", "frontend/dist", "frontend/probe-dist",
        "frontend/dist-probe", "Dockerfile", "backend/pyproject.toml",
        "frontend/package-lock.json",
    )
    return {
        str(root): helpers().file_manifest(root, [name for name in names if (root / name).exists()])
        for root in (Path("/opt/open-node"), Path("/opt/open-node/mmwx-parity-candidate"))
    }


def source_files():
    return helpers().file_manifest(ROOT, [
        "backend/app", "backend/tests", "backend/pyproject.toml", "frontend/src",
        "frontend/public-probe", "frontend/package.json", "frontend/package-lock.json",
        "frontend/vite.config.ts", "frontend/vite.probe.config.ts", "Dockerfile",
        "scripts/vps/smoke-branding-browser.py", "scripts/vps/smoke-notifications-browser.py",
    ])


class ApiAudit:
    """Observe actual branding requests without changing receive/send or results."""

    def __init__(self, application, path, owner):
        self.application, self.path, self.owner = application, path, owner
        if path.exists():
            previous = helpers().read_json(path)
            require(previous["owner"] == owner, "audit_owner_changed")
            self.events = previous["events"]
        else:
            self.events = []
        self.persist()

    def persist(self):
        write_json(self.path, {"owner": self.owner, "events": self.events})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in {PUBLIC, PRIVATE}:
            return await self.application(scope, receive, send)
        headers = {key.lower(): value for key, value in scope["headers"]}
        event = {
            "number": len(self.events) + 1, "path": scope["path"], "method": scope["method"],
            "cookie_present": b"cookie" in headers, "authorization_present": b"authorization"
            in headers, "csrf_present": b"x-csrf-token" in headers,
            "referer_present": b"referer" in headers, "status": None, "complete": False,
        }
        self.events.append(event)
        body = bytearray()

        async def observed(message):
            if message["type"] == "http.response.start":
                event["status"] = message["status"]
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))
            await send(message)

        try:
            await self.application(scope, receive, observed)
            event["complete"] = True
            event["response_bytes"] = len(body)
            event["response_sha256"] = digest(bytes(body))
            value = json.loads(body)
            event["response_keys"] = sorted(value) if isinstance(value, dict) else []
            if isinstance(value, dict) and value.get("code") in {
                "branding_invalid_request", "branding_revision_conflict",
                "branding_storage_unavailable",
            }:
                event["code"] = value["code"]
        finally:
            self.persist()


def serve(args):
    helpers().assert_loopback_namespace()
    work = private_directory(args.output / "fixture")
    owner = os.environ.get("BRANDING_GATE_OWNER", "")
    require(re.fullmatch(r"[0-9a-f]{32}", owner), "server_owner_missing")
    bootstrap = json.loads(sys.stdin.buffer.readline(32769))
    require(bootstrap["owner"] == owner, "server_owner_mismatch")
    database = work / "application.db"
    require(not database.is_symlink(), "database_symlink")
    first_start = not database.exists()
    require(not first_start or args._serve == "main", "probe_cannot_initialize_database")
    frontend = args.frontend_dir if args._serve == "main" else args.probe_frontend_dir
    with socket.fromfd(args._fd, socket.AF_INET, socket.SOCK_STREAM) as inherited:
        address, port = inherited.getsockname()
        require(address == "127.0.0.1" and 1024 <= port <= 65535, "listener_not_loopback")
    from open_node.core.config import Settings
    from open_node.domain.subscriber_auth import SubscriberAccountUpdate
    from open_node.services.inventory import ProductUserModel
    from pydantic import SecretStr
    from uvicorn import Config, Server

    settings = Settings(
        _env_file=None, database_url="sqlite:///" + str(database), frontend_dir=frontend,
        session_cookie_secure=False, subscriber_totp_key=SecretStr(bootstrap["totp_key"]),
        certificate_state_dir=work / ("certificates-" + args._serve),
        notifications_state_dir=work / "notifications",
        external_subscriptions_state_dir=work / "external-subscriptions",
    )
    # main's module-level app is also confined to this same owned database.
    os.environ.update({
        "OPEN_NODE_DATABASE_URL": settings.database_url,
        "OPEN_NODE_CERTIFICATE_STATE_DIR": str(settings.certificate_state_dir),
        "OPEN_NODE_NOTIFICATIONS_STATE_DIR": str(settings.notifications_state_dir),
        "OPEN_NODE_EXTERNAL_SUBSCRIPTIONS_STATE_DIR": str(
            settings.external_subscriptions_state_dir
        ),
    })
    from open_node.main import create_app

    app = create_app(settings)
    if first_start:
        app.state.auth.set_administrator("admin", bootstrap["administrator_password"])
        now = datetime.now(UTC)
        with app.state.inventory._session() as session:
            session.add(ProductUserModel(
                username=SUBSCRIBER, display_name="站点文字验收用户", role="user",
                is_active=True, created_at=now, updated_at=now,
            ))
            session.commit()
        account = app.state.subscriber_auth.management(SUBSCRIBER)
        app.state.subscriber_auth.set_password(SUBSCRIBER, SubscriberAccountUpdate(
            expected_revision=account.revision,
            new_password=SecretStr(bootstrap["subscriber_password"]),
        ))
    # Preserve the application's normal lifespan and all real routes/stores.
    audit = ApiAudit(app, work / (args._serve + "-api-audit.json"), owner)
    Server(Config(audit, fd=args._fd, access_log=False, log_level="info")).run()


class Runtime:
    def __init__(self, args, output, work, owner, namespace, report):
        self.args, self.output, self.work = args, output, work
        self.owner, self.namespace, self.report = owner, namespace, report
        self.services = {}
        from cryptography.fernet import Fernet

        self.credentials = {
            "owner": owner, "administrator_password": secrets.token_urlsafe(32),
            "subscriber_password": secrets.token_urlsafe(32),
            "totp_key": Fernet.generate_key().decode(),
        }
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("OPEN_NODE_") and not key.lower().endswith("_proxy")
        }
        self.environment.update({
            "PYTHONPATH": str(ROOT / "backend/app"), "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(work / "pycache"), "BRANDING_GATE_OWNER": owner,
            "TMPDIR": str(work / "tmp"),
        })

    def start(self, role):
        if role not in self.services:
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.services[role] = {
                "listener": listener, "url": "http://127.0.0.1:"
                + str(listener.getsockname()[1]), "process": None, "starts": 0,
            }
        row = self.services[role]
        require(row["process"] is None or row["process"].poll() is not None,
                "service_already_running")
        log_path = self.work / (role + ".log")
        row["log_offset"] = log_path.stat().st_size if log_path.exists() else 0
        row["started_at"] = datetime.now(UTC).isoformat()
        with log_path.open("ab") as log:
            process = subprocess.Popen([
                str(self.args.python or sys.executable), str(Path(__file__).resolve()),
                "--frontend-dir", str(self.args.frontend_dir), "--probe-frontend-dir",
                str(self.args.probe_frontend_dir), "--output", str(self.output),
                "--revision", self.args.revision, "--_serve", role, "--_fd",
                str(row["listener"].fileno()),
            ], cwd=self.work, env=self.environment, stdin=subprocess.PIPE,
                stdout=log, stderr=log, pass_fds=(row["listener"].fileno(),),
                start_new_session=True)
            row["process"], row["starts"] = process, row["starts"] + 1
            process.stdin.write(json.dumps(self.credentials).encode() + b"\n")
            process.stdin.close()
        helpers().wait_ready(row["url"], process)
        return row["url"]

    def stop(self, role):
        row = self.services[role]
        process = row["process"]
        if process is None:
            return
        require(process.poll() is None, "service_exited_before_stop")
        require(process.pid in owner_processes(self.namespace, self.owner), "service_owner_lost")
        started = time.monotonic()
        process.terminate()
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise GateFailure("service_graceful_stop_timeout") from None
        elapsed = time.monotonic() - started
        log = (self.work / (role + ".log")).read_bytes()[row["log_offset"]:]
        started_pids = re.findall(rb"(?m)^INFO:\s+Started server process \[(\d+)\]\r?$", log)
        finished_pids = re.findall(rb"(?m)^INFO:\s+Finished server process \[(\d+)\]\r?$", log)
        completed = (
            len(started_pids) == 1 and finished_pids == started_pids
            and log.count(b"Application shutdown complete.") == 1
            and b"Application shutdown failed" not in log
            and b"timeout graceful shutdown exceeded" not in log
        )
        self.report.setdefault("shutdowns", []).append({
            "role": role, "generation": row["starts"], "pid": process.pid,
            "started_at": row["started_at"], "finished_at": datetime.now(UTC).isoformat(),
            "exit_code": code, "elapsed_seconds": round(elapsed, 6), "grace_seconds": 30,
            "requested_signal": "SIGTERM", "current_generation_completed": completed,
            "current_generation_log_sha256": digest(log),
        })
        require(code in (0, -signal.SIGTERM) and elapsed < 30 and completed,
                "service_did_not_stop_cleanly")
        row["process"] = None

    def close(self):
        errors = []
        for role in reversed(list(self.services)):
            try:
                if self.services[role]["process"] is not None:
                    self.stop(role)
            except Exception:
                errors.append("owned_service_cleanup_failed")
            finally:
                self.services[role]["listener"].close()
        cleanup_processes(self.namespace, self.owner)
        require(not errors, "owned_service_cleanup_failed")

    def corrupt_branding(self):
        path = self.work / "application.db"
        require(path.is_file() and not path.is_symlink(), "owned_database_missing")
        with sqlite3.connect(path, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision,site_title,brand_title FROM site_branding_settings WHERE id=1"
            ).fetchone()
            require(row is not None, "branding_row_missing")
            result = connection.execute(
                "UPDATE site_branding_settings SET site_title=? WHERE id=1 AND revision=?",
                ("\n" + FAULT_MARKER, row[0]),
            )
            require(result.rowcount == 1, "controlled_corruption_conflict")
        return row

    def restore_branding(self, saved):
        with sqlite3.connect(self.work / "application.db", timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "UPDATE site_branding_settings SET site_title=? "
                "WHERE id=1 AND revision=? AND site_title=? AND brand_title=?",
                (saved[1], saved[0], "\n" + FAULT_MARKER, saved[2]),
            )
            require(result.rowcount == 1, "controlled_corruption_restore_conflict")


class BrowserGate:
    def __init__(self, runtime, url, probe_url, report):
        self.runtime, self.url, self.probe_url, self.report = runtime, url, probe_url, report
        self.output = runtime.output
        self.current = "initialization"
        self.requests, self.responses, self.console, self.page_errors = [], [], [], []
        self.external, self.dialogs, self.contexts, self.screenshots = [], [], [], []
        self.page_number = 0
        self.browser, self.anonymous = None, None

    def persist(self):
        write_json(self.output / "report.json", self.report)

    def phase(self, name, evidence):
        self.report["phases"].append({"name": name, "status": "passed", "evidence": evidence})
        self.persist()
        print("PASS branding_browser " + name, flush=True)

    def origin_allowed(self, value):
        parsed = urlsplit(value)
        if parsed.scheme in {"data", "blob"}:
            return True
        return parsed.scheme in {"http", "ws"} and parsed.netloc in {
            urlsplit(self.url).netloc, urlsplit(self.probe_url).netloc,
        }

    def context(self):
        context = self.browser.new_context(
            locale="zh-CN", viewport={"width": 1440, "height": 1000}, service_workers="block",
        )
        context.set_default_timeout(20000)
        context.add_init_script("window.__bx = 0;")

        def guard(route):
            if self.origin_allowed(route.request.url):
                route.continue_()
            else:
                self.external.append({"scenario": self.current, "scheme":
                                      urlsplit(route.request.url).scheme})
                route.abort("blockedbyclient")

        context.route("**/*", guard)
        self.contexts.append(context)
        return context

    def page(self, context):
        self.page_number += 1
        number = self.page_number
        page = context.new_page()

        def observed(request):
            path = urlsplit(request.url).path
            self.requests.append({
                "page": number, "scenario": self.current, "path": path,
                "method": request.method,
            })

        def responded(response):
            self.responses.append({
                "page": number, "scenario": self.current, "path": urlsplit(response.url).path,
                "status": response.status, "method": response.request.method,
            })

        def console(message):
            if message.type != "error":
                return
            value = message.text
            for key in ("administrator_password", "subscriber_password", "totp_key"):
                value = value.replace(self.runtime.credentials[key], "<redacted-fixture-secret>")
            self.console.append({
                "page": number, "scenario": self.current,
                "path": urlsplit(message.location.get("url", "")).path,
                "text": value, "text_sha256": digest(message.text),
            })

        def dialog(value):
            self.dialogs.append({"page": number, "scenario": self.current, "type": value.type})
            value.dismiss()

        def websocket(value):
            if not self.origin_allowed(value.url):
                self.external.append({"scenario": self.current, "scheme": "websocket"})

        page.on("request", observed)
        page.on("response", responded)
        page.on("console", console)
        page.on("dialog", dialog)
        page.on("websocket", websocket)
        page.on("pageerror", lambda error: self.page_errors.append({
            "page": number, "scenario": self.current, "message_sha256": digest(str(error)),
        }))
        return page

    def api(self, context, path=PRIVATE, *, method="GET", payload=None, csrf=True, origin=None):
        headers = {"Origin": origin or self.url, "Content-Type": "application/json"}
        if csrf and method not in {"GET", "HEAD", "OPTIONS"}:
            session = context.request.get(self.url + "/api/v1/auth/session").json()
            if session.get("csrf_token"):
                headers["X-CSRF-Token"] = session["csrf_token"]
        response = context.request.fetch(
            self.url + path, method=method, data=payload, headers=headers, max_redirects=0,
        )
        require(response.headers.get("cache-control") == "no-store", "api_missing_no_store")
        require(response.headers.get("referrer-policy") == "no-referrer",
                "api_missing_no_referrer")
        require(FAULT_MARKER.encode() not in response.body(), "api_reflected_invalid_input")
        return response

    def settings(self, context):
        response = self.api(context)
        require(response.status == 200, "administrator_read_failed")
        value = response.json()
        require(set(value) == {"site_title", "brand_title", "revision", "license_required"}
                and value["license_required"] is False, "administrator_fields_wrong")
        return value

    def public(self):
        response = self.anonymous.get(self.url + PUBLIC, max_redirects=0)
        require(response.status == 200, "anonymous_read_failed")
        require(response.headers.get("cache-control") == "no-store"
                and response.headers.get("referrer-policy") == "no-referrer",
                "anonymous_response_headers_wrong")
        value = response.json()
        require(set(value) == {"site_title", "brand_title", "license_required"}
                and value["license_required"] is False, "anonymous_fields_wrong")
        return value

    @staticmethod
    def fill(page, site, brand):
        page.get_by_label("浏览器标题", exact=True).fill(site)
        page.get_by_label("页面品牌文字", exact=True).fill(brand)

    def writes(self):
        return sum(row["path"] == PRIVATE and row["method"] == "PUT" for row in self.requests)

    def reads(self):
        return sum(row["path"] == PRIVATE and row["method"] == "GET" for row in self.requests)

    def saved(self, page, context, site, brand, *, double=False):
        from playwright.sync_api import expect

        before = self.settings(context)
        count = self.writes()
        self.fill(page, site, brand)
        with page.expect_response(lambda item: urlsplit(item.url).path == PRIVATE
                                  and item.request.method == "PUT") as pending:
            page.get_by_role("button", name="保存站点文字", exact=True).click(
                click_count=2 if double else 1
            )
        require(pending.value.status == 200, "ui_save_rejected")
        expect(page.get_by_text("站点文字已保存。", exact=True)).to_be_visible()
        value = self.settings(context)
        require(value["revision"] == before["revision"] + 1 and value["site_title"] == site
                and value["brand_title"] == brand, "save_not_atomically_persisted")
        require(self.writes() == count + 1, "save_replayed")
        return value

    @staticmethod
    def branding(page, site, brand, title, *, logged_in=True):
        from playwright.sync_api import expect

        expect(page).to_have_title(title + " - " + site)
        if logged_in:
            header = page.locator(".application-header .branding-header-text")
            expect(header).to_have_text(brand)
            expect(header).to_have_attribute("title", brand)
        else:
            expect(page.get_by_role("heading", name=brand, exact=True)).to_be_visible()
        require(page.evaluate("window.__bx === 0"), "branding_executed_script")
        require(page.locator("svg[onload],img[onerror],iframe").count() == 0,
                "branding_created_executable_dom")
        require(FAULT_MARKER not in page.locator("body").inner_text(), "unsafe_response_displayed")

    def screenshot(self, page, name, brand, *, login=False, probe=False):
        from playwright.sync_api import expect

        for width, height, label in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(150)
            require(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),
                    "page_horizontal_overflow")
            controls = ["刷新探针状态"] if probe else ["登录" if login else "退出登录"]
            for control in controls:
                button = page.get_by_role("button", name=control, exact=True)
                expect(button).to_be_visible()
                require(button.evaluate("""element => {
                  const box = element.getBoundingClientRect();
                  const hit = document.elementFromPoint(box.x + box.width/2, box.y + box.height/2);
                  return box.width > 0 && box.height > 0 && box.x >= 0
                    && box.right <= innerWidth + 1 && box.y >= 0 && box.bottom <= innerHeight
                    && !!hit && (hit === element || element.contains(hit));
                }"""), "navigation_or_login_obstructed")
            menu = page.get_by_role("button", name="切换导航菜单", exact=True)
            if not login and not probe and width < 1024 and menu.count():
                menu.click()
                dialog = page.get_by_role("dialog")
                expect(dialog.get_by_title(brand, exact=True)).to_be_visible()
                expect(dialog.get_by_role("menuitem", name="系统设置", exact=True)).to_be_visible()
                page.keyboard.press("Escape")
                expect(dialog).not_to_be_visible()
            name_with_size = f"{name}-{label}.png"
            page.screenshot(path=str(self.output / name_with_size), full_page=False,
                            animations="disabled", mask=[page.locator('input[type="password"]')])
            self.screenshots.append({"file": name_with_size, "width": width, "height": height,
                                     "sha256": digest((self.output / name_with_size).read_bytes())})
        page.set_viewport_size({"width": 1440, "height": 1000})

    def login(self, page, password, *, account=False):
        from playwright.sync_api import expect

        page.get_by_label("用户名", exact=True).fill(SUBSCRIBER if account else "admin")
        page.get_by_label("密码", exact=True).fill(password)
        path = "/api/v1/account/login" if account else "/api/v1/auth/login"
        with page.expect_response(lambda item: urlsplit(item.url).path == path) as pending:
            page.get_by_role("button", name="登录", exact=True).click()
        require(pending.value.status == 200 and pending.value.json()["authenticated"] is True,
                "real_ui_login_failed")
        expect(page.get_by_role("button", name="退出登录", exact=True)).to_be_visible()

    @staticmethod
    def settle(page):
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => "
                      "requestAnimationFrame(resolve)))")

    def assertions(self):
        expected = []
        unexpected = []
        for event in self.console:
            match = re.fullmatch(
                r"Failed to load resource: the server responded with a status of (\d+) \([^\n]*\)",
                event["text"],
            )
            code = int(match[1]) if match else None
            allowed = (event["scenario"], event["path"], code) in {
                ("cas_conflict", PRIVATE, 409), ("public_storage_failure", PUBLIC, 503),
            }
            witnessed = any(row["page"] == event["page"] and row["scenario"] == event["scenario"]
                            and row["path"] == event["path"] and row["status"] == code
                            for row in self.responses)
            (expected if allowed and witnessed else unexpected).append(event)
        self.report.update({
            "expected_network_console_diagnostics": expected,
            "expected_network_console_count": len(expected),
            "unexpected_console_errors": unexpected, "unexpected_console_count": len(unexpected),
            "page_errors": self.page_errors, "pageerror_count": len(self.page_errors),
            "external_requests": self.external, "external_request_count": len(self.external),
            "dialogs": self.dialogs, "screenshots": self.screenshots,
            "browser_requests": self.requests, "browser_responses": self.responses,
        })
        self.persist()
        require(not unexpected and not self.page_errors, "unexpected_browser_error")
        require(not self.external and not self.dialogs, "branding_injection_or_external_request")
        for context in self.contexts:
            for page in context.pages:
                if not page.is_closed() and page.url.startswith((self.url, self.probe_url)):
                    stored = page.evaluate("JSON.stringify({local:{...localStorage},"
                                           "session:{...sessionStorage}})")
                    require(not any(value in stored for value in (
                        LONG_SITE, LONG_BRAND, HTML_SITE, HTML_BRAND,
                        self.runtime.credentials["administrator_password"],
                        self.runtime.credentials["subscriber_password"],
                    )), "branding_or_secrets_in_browser_storage")

    def exercise(self):
        from playwright.sync_api import expect, sync_playwright

        with sync_playwright() as playwright:
            self.browser = playwright.chromium.launch(env={
                **self.runtime.environment,
                "XDG_CACHE_HOME": str(private_directory(self.runtime.work / "browser-cache")),
                "XDG_CONFIG_HOME": str(private_directory(self.runtime.work / "browser-config")),
            })
            self.anonymous = playwright.request.new_context()
            try:
                self.scenarios(expect)
                self.assertions()
            finally:
                try:
                    self.assertions()
                finally:
                    for context in reversed(self.contexts):
                        with contextlib.suppress(Exception):
                            context.close()
                    self.anonymous.dispose()
                    self.browser.close()
                self.assertions()

    def scenarios(self, expect):
        self.current = "anonymous_and_login"
        public = self.public()
        require(public == {"site_title": "Open Node", "brand_title": "Open Node",
                           "license_required": False}, "default_branding_wrong")
        probe_before = self.anonymous.get(self.url + "/api/v1/public/probe-settings").json()
        health_before = self.anonymous.get(self.url + "/healthz").json()
        require(set(health_before) == {"status", "service", "timestamp"}
                and health_before["status"] == "ok" and health_before["service"] == "Open Node",
                "health_identity_initially_invalid")
        meta_response = self.anonymous.get(self.url + "/api/v1/meta")
        require(meta_response.status == 200, "meta_initial_read_failed")
        meta_before = meta_response.json()
        require(set(meta_before) == {
            "name", "version", "api_prefix", "license_required", "short_links_enabled", "stack",
        } and meta_before["name"] == "Open Node" and meta_before["license_required"] is False,
                "meta_identity_initially_invalid")
        context = self.context()
        page = self.page(context)
        with page.expect_response(lambda item: urlsplit(item.url).path == PUBLIC) as initial:
            page.goto(self.url + "/system-settings")
        require(initial.value.status == 200 and initial.value.json() == public,
                "initial_public_read_not_completed")
        self.settle(page)
        self.branding(page, "Open Node", "Open Node", "管理员登录", logged_in=False)
        self.login(page, self.runtime.credentials["administrator_password"])
        expect(page.get_by_role("heading", name="系统设置", exact=True)).to_be_visible()
        expect(page.get_by_label("浏览器标题", exact=True)).to_have_value("Open Node")
        expect(page.get_by_text(
            "这两项文字会公开显示在登录页和其他页面，请勿填写密码、Token 或其他秘密。", exact=True,
        )).to_be_visible()
        require(self.settings(context)["revision"] == 0, "default_revision_wrong")
        self.phase("anonymous_projection_and_real_administrator_login", {"public_fields":
                   sorted(public), "license_required": False, "default_revision": 0})

        self.current = "administrator_permissions"
        outsider = self.context()
        for method in ("GET", "PUT"):
            result = self.api(outsider, method=method, payload={"secret": FAULT_MARKER})
            require(result.status == 401, "anonymous_private_api_allowed")
        for csrf, origin in ((False, None), (True, "https://attacker.invalid")):
            result = self.api(context, method="PUT", payload={"secret": FAULT_MARKER},
                              csrf=csrf, origin=origin)
            require(result.status == 403, "csrf_or_origin_not_enforced")
        require(self.settings(context)["revision"] == 0, "forbidden_write_changed_revision")
        self.phase("administrator_csrf_origin_and_anonymous_permissions", {
            "anonymous_statuses": [401, 401], "csrf_origin_statuses": [403, 403],
            "revision_unchanged": True,
        })

        self.current = "cas_conflict"
        other = {
            "expected_revision": 0, "site_title": "并发保存站点🌏", "brand_title": "另一会话保存",
        }
        real = self.api(context, method="PUT", payload=other)
        require(real.status == 200 and real.json()["revision"] == 1, "concurrent_write_failed")
        before_writes, before_reads = self.writes(), self.reads()
        self.fill(page, "过期表单不得覆盖", "过期草稿")
        with page.expect_response(lambda item: urlsplit(item.url).path == PRIVATE
                                  and item.request.method == "PUT") as conflict:
            page.get_by_role("button", name="保存站点文字", exact=True).click()
        require(conflict.value.status == 409, "cas_did_not_conflict")
        expect(page.get_by_role("alert").filter(has_text="没有自动重新提交")).to_contain_text(
            "已重新读取当前配置，请核对"
        )
        expect(page.get_by_label("页面品牌文字", exact=True)).to_have_value(other["brand_title"])
        require(self.writes() == before_writes + 1 and self.reads() == before_reads + 1,
                "cas_write_replayed_or_not_reconciled")
        require(self.settings(context) == real.json(), "cas_overwrote_newer_settings")
        self.phase("cas_conflict_readonly_reconciliation", {"status": 409, "ui_puts": 1,
                   "reconciliation_gets": 1, "revision": 1, "automatic_puts": 0})

        self.current = "unicode_and_default_draft"
        saved = self.saved(page, context, LONG_SITE, LONG_BRAND, double=True)
        self.branding(page, LONG_SITE, LONG_BRAND, "系统设置")
        self.screenshot(page, "settings-long", LONG_BRAND)
        count = self.writes()
        page.get_by_role("button", name="恢复默认草稿", exact=True).click()
        expect(page.get_by_label("浏览器标题", exact=True)).to_have_value("Open Node")
        expect(page.get_by_label("页面品牌文字", exact=True)).to_have_value("Open Node")
        self.branding(page, LONG_SITE, LONG_BRAND, "系统设置")
        require(self.writes() == count and self.settings(context) == saved,
                "restore_defaults_saved_implicitly")
        page.get_by_role("button", name="重新读取站点文字", exact=True).click()
        expect(page.get_by_label("浏览器标题", exact=True)).to_have_value(LONG_SITE)
        self.phase("unicode_limits_double_click_and_default_draft", {
            "site_codepoints": len(LONG_SITE), "brand_codepoints": len(LONG_BRAND),
            "double_click_puts": 1, "default_draft_puts": 0, "saved_revision": saved["revision"],
        })

        self.current = "lost_save_receipt"
        held = {}

        def lose_receipt(route):
            require(route.request.method == "PUT", "receipt_fault_wrong_method")
            real_response = route.fetch(max_redirects=0, max_retries=0)
            require(real_response.status == 200, "receipt_fault_before_successful_commit")
            held["saved"] = real_response.json()
            route.fulfill(response=real_response, body="{")

        before_writes, before_reads = self.writes(), self.reads()
        page.route("**" + PRIVATE, lose_receipt, times=1)
        self.fill(page, "回执丢失但已保存🚀", "保存结果只读对账")
        page.get_by_role("button", name="保存站点文字", exact=True).click()
        expect(page.get_by_role("alert").filter(has_text="未收到有效的保存回执")).to_contain_text(
            "已重新读取当前配置，请核对；没有自动重新提交。"
        )
        expect(page.get_by_text("站点文字已保存。", exact=True)).to_have_count(0)
        require(self.writes() == before_writes + 1 and self.reads() == before_reads + 1,
                "uncertain_receipt_write_replayed")
        require(held.get("saved") == self.settings(context), "uncertain_receipt_not_reconciled")
        self.phase("committed_save_lost_receipt_readonly_reconciliation", {
            "real_api_committed": True,
            "fault": "replace_response_body_after_real_200_with_invalid_json",
            "ui_puts": 1, "reconciliation_gets": 1, "automatic_puts": 0,
            "saved_revision": held["saved"]["revision"], "false_success_notice": False,
        })

        self.current = "late_public_response"
        held = {}

        def hold_public(route):
            real_response = self.anonymous.get(self.url + PUBLIC, max_redirects=0)
            require(real_response.status == 200, "late_public_read_failed")
            held.update(route=route, request=route.request, response=real_response,
                        started=time.monotonic())

        page.route("**" + PUBLIC, hold_public, times=1)
        page.goto(self.url + "/system-settings")
        expect(page.get_by_label("页面品牌文字", exact=True)).to_have_value("保存结果只读对账")
        newer = self.saved(page, context, "新保存优先于旧公开读取🌏", "公开旧响应不可覆盖")
        require(held and time.monotonic() - held["started"] < 15, "late_public_request_expired")
        with page.expect_response(lambda item: item.request == held["request"]) as released, (
            page.expect_request_finished(lambda item: item == held["request"])
        ) as completed:
            held["route"].fulfill(response=held["response"])
        require(released.value.status == 200 and completed.value.method == "GET",
                "late_public_response_not_completed")
        self.settle(page)
        self.branding(page, newer["site_title"], newer["brand_title"], "系统设置")
        require(self.settings(context) == newer, "old_public_response_changed_settings")
        self.phase("late_public_response_cannot_override_new_save", {
            "real_old_response_released": True, "new_revision": newer["revision"],
            "product_timeout_unchanged_seconds": 15,
        })

        self.current = "late_administrator_response"
        held = {}

        def hold_administrator(route):
            require(route.request.method == "GET", "late_admin_fault_wrong_method")
            real_response = route.fetch(max_redirects=0, max_retries=0)
            require(real_response.status == 200, "late_admin_read_failed")
            held.update(route=route, request=route.request, response=real_response,
                        started=time.monotonic())

        page.route("**" + PRIVATE, hold_administrator, times=1)
        page.get_by_role("button", name="重新读取站点文字", exact=True).click()
        page.get_by_role("menuitem", name="概览", exact=True).click()
        expect(page.get_by_role("heading", name=newer["brand_title"] + " 控制台",
                                exact=True)).to_be_visible()
        page.get_by_role("menuitem", name="系统设置", exact=True).click()
        expect(page.get_by_label("页面品牌文字", exact=True)).to_have_value(newer["brand_title"])
        saved = self.saved(page, context, LONG_SITE, LONG_BRAND)
        require(held and time.monotonic() - held["started"] < 15, "late_admin_request_expired")
        with page.expect_response(lambda item: item.request == held["request"]) as released, (
            page.expect_request_finished(lambda item: item == held["request"])
        ) as completed:
            held["route"].fulfill(response=held["response"])
        require(released.value.status == 200 and completed.value.method == "GET",
                "late_administrator_response_not_completed")
        self.settle(page)
        self.branding(page, LONG_SITE, LONG_BRAND, "系统设置")
        expect(page.get_by_label("页面品牌文字", exact=True)).to_have_value(LONG_BRAND)
        require(self.settings(context) == saved, "old_admin_response_changed_settings")
        self.phase("unmounted_administrator_read_cannot_override_new_save", {
            "real_old_response_released": True, "new_revision": saved["revision"],
            "product_timeout_unchanged_seconds": 15,
        })

        self.current = "long_branding_surfaces"
        page.get_by_role("menuitem", name="概览", exact=True).click()
        expect(page.get_by_role("heading", name=LONG_BRAND + " 控制台", exact=True)).to_be_visible()
        self.branding(page, LONG_SITE, LONG_BRAND, "概览")
        self.screenshot(page, "dashboard-long", LONG_BRAND)
        sign_in = self.page(self.context())
        sign_in.goto(self.url + "/")
        self.branding(sign_in, LONG_SITE, LONG_BRAND, "管理员登录", logged_in=False)
        self.screenshot(sign_in, "administrator-signin-long", LONG_BRAND, login=True)
        subscriber_context = self.context()
        subscriber_page = self.page(subscriber_context)
        subscriber_page.goto(self.url + "/account")
        self.branding(subscriber_page, LONG_SITE, LONG_BRAND, "用户中心", logged_in=False)
        self.screenshot(subscriber_page, "subscriber-signin-long", LONG_BRAND, login=True)
        self.login(subscriber_page, self.runtime.credentials["subscriber_password"], account=True)
        self.branding(subscriber_page, LONG_SITE, LONG_BRAND, "用户中心")
        self.screenshot(subscriber_page, "subscriber-account-long", LONG_BRAND)
        for method in ("GET", "PUT"):
            result = self.api(subscriber_context, method=method, payload={"secret": FAULT_MARKER})
            require(result.status == 401, "subscriber_private_api_allowed")
        require(self.settings(context) == saved, "subscriber_changed_branding")
        self.phase("long_admin_login_dashboard_user_login_and_account", {
            "real_subscriber_login": True, "subscriber_admin_statuses": [401, 401],
            "viewports": [width for width, _height, _label in VIEWPORTS],
        })

        self.current = "restart_preserves_sessions"
        administrator = context.request.get(self.url + "/api/v1/auth/session").json()
        subscriber = subscriber_context.request.get(self.url + "/api/v1/account/session").json()
        administrator_cookies = context.cookies()
        subscriber_cookies = subscriber_context.cookies()
        self.runtime.stop("main")
        require(self.runtime.start("main") == self.url, "restart_origin_changed")
        for browser_context, endpoint, original in (
            (context, "/api/v1/auth/session", administrator),
            (subscriber_context, "/api/v1/account/session", subscriber),
        ):
            current = browser_context.request.get(self.url + endpoint).json()
            require(current["authenticated"] is True and current["username"] == original["username"]
                    and current["csrf_token"] == original["csrf_token"], "restart_lost_session")
        require(context.cookies() == administrator_cookies
                and subscriber_context.cookies() == subscriber_cookies, "restart_rotated_cookies")
        require(self.settings(context) == saved, "restart_lost_branding")
        page.reload()
        self.branding(page, LONG_SITE, LONG_BRAND, "概览")
        subscriber_page.reload()
        self.branding(subscriber_page, LONG_SITE, LONG_BRAND, "用户中心")
        self.phase("real_process_restart_preserves_original_sessions_and_settings", {
            "same_origin": True, "administrator_session": True, "subscriber_session": True,
            "original_csrf": True, "cookies_rotated": False, "revision": saved["revision"],
            "administrator_reseeded": False,
        })

        self.current = "public_storage_failure"
        corrupted = self.runtime.corrupt_branding()
        try:
            fallback = self.page(self.context())
            with fallback.expect_response(lambda item: urlsplit(item.url).path == PUBLIC) as failed:
                fallback.goto(self.url + "/")
            require(failed.value.status == 503
                    and failed.value.json()["code"] == "branding_storage_unavailable",
                    "real_storage_fault_not_observed")
            self.branding(fallback, "Open Node", "Open Node", "管理员登录", logged_in=False)
            self.screenshot(fallback, "public-failure-default-login", "Open Node", login=True)
            self.login(fallback, self.runtime.credentials["administrator_password"])
            self.branding(fallback, "Open Node", "Open Node", "概览")
        finally:
            self.runtime.restore_branding(corrupted)
        require(self.settings(context) == saved, "storage_fault_restore_changed_revision")
        self.phase("public_storage_failure_defaults_do_not_block_real_login", {
            "actual_api_status": 503, "actual_code": "branding_storage_unavailable",
            "fault": "invalid_value_in_owned_sqlite_row_restored_with_revision_guard",
            "default_site_title": "Open Node", "login_succeeded": True,
        })

        self.current = "invalid_public_response"
        invalid_page = self.page(self.context())

        def invalid_public(route):
            real_response = self.anonymous.get(self.url + PUBLIC, max_redirects=0)
            require(real_response.status == 200, "invalid_body_real_read_failed")
            body = {**real_response.json(), "revision": 999, "detail": FAULT_MARKER}
            route.fulfill(response=real_response, json=body)

        invalid_page.route("**" + PUBLIC, invalid_public, times=1)
        with invalid_page.expect_response(
            lambda item: urlsplit(item.url).path == PUBLIC
        ) as invalid_response:
            invalid_page.goto(self.url + "/")
        require(invalid_response.value.status == 200
                and invalid_response.value.json().get("detail") == FAULT_MARKER,
                "invalid_public_response_not_delivered")
        self.settle(invalid_page)
        self.branding(invalid_page, "Open Node", "Open Node", "管理员登录", logged_in=False)
        self.login(invalid_page, self.runtime.credentials["administrator_password"])
        self.branding(invalid_page, "Open Node", "Open Node", "概览")
        self.phase("invalid_public_response_defaults_do_not_block_real_login", {
            "fault": "extra_keys_after_actual_public_200_response", "raw_detail_displayed": False,
            "login_succeeded": True, "core_api_replaced": False,
        })

        self.current = "html_like_plain_text"
        page.goto(self.url + "/system-settings")
        expect(page.get_by_label("浏览器标题", exact=True)).to_have_value(LONG_SITE)
        saved = self.saved(page, context, HTML_SITE, HTML_BRAND)
        self.branding(page, HTML_SITE, HTML_BRAND, "系统设置")
        self.screenshot(page, "settings-html-literal", HTML_BRAND)
        html_login = self.page(self.context())
        html_login.goto(self.url + "/")
        self.branding(html_login, HTML_SITE, HTML_BRAND, "管理员登录", logged_in=False)
        self.screenshot(html_login, "administrator-signin-html-literal", HTML_BRAND, login=True)
        subscriber_page.reload()
        self.branding(subscriber_page, HTML_SITE, HTML_BRAND, "用户中心")
        require(self.public() == {"site_title": HTML_SITE, "brand_title": HTML_BRAND,
                                 "license_required": False}, "public_plain_text_changed")
        self.phase("html_like_names_are_text_not_dom_or_urls", {
            "site_title": HTML_SITE, "brand_title": HTML_BRAND, "revision": saved["revision"],
            "script_executed": False, "html_elements_injected": 0,
        })

        self.current = "independent_public_probe"
        probe_context = self.context()
        probe_page = self.page(probe_context)
        count = len(self.requests)
        probe_page.goto(self.probe_url + "/")
        expect(probe_page.get_by_role("heading", name=probe_before["settings"]["title"],
                                     exact=True)).to_be_visible()
        expect(probe_page).to_have_title("Open Node 公共探针")
        expect(probe_page.get_by_text("实时连接已建立", exact=True)).to_be_visible()
        self.screenshot(probe_page, "probe-original-title", "", probe=True)
        probe_requests = self.requests[count:]
        require(not any(row["path"] in {PUBLIC, PRIVATE} for row in probe_requests),
                "public_probe_requested_branding")
        require(self.anonymous.get(self.probe_url + "/api/v1/public/probe-settings").json()
                == probe_before, "branding_changed_probe_settings")
        health_after = self.anonymous.get(self.url + "/healthz").json()
        require(set(health_after) == set(health_before)
                and health_after["status"] == health_before["status"]
                and health_after["service"] == health_before["service"],
                "branding_changed_service_identity")
        before_time = datetime.fromisoformat(health_before["timestamp"].replace("Z", "+00:00"))
        after_time = datetime.fromisoformat(health_after["timestamp"].replace("Z", "+00:00"))
        require(before_time.tzinfo is not None and after_time.tzinfo is not None
                and before_time.utcoffset().total_seconds() == 0
                and after_time.utcoffset().total_seconds() == 0
                and after_time >= before_time, "health_timestamp_invalid_or_regressed")
        meta_response = self.anonymous.get(self.url + "/api/v1/meta")
        require(meta_response.status == 200 and meta_response.json() == meta_before,
                "branding_changed_application_metadata")
        self.phase("probe_only_bundle_keeps_own_title_and_never_reads_branding", {
            "probe_title": probe_before["settings"]["title"],
            "document_title": "Open Node 公共探针",
            "branding_requests": 0, "real_probe_websocket": True, "settings_unchanged": True,
            "service_identity_unchanged": True, "complete_metadata_unchanged": True,
            "health_before": health_before, "health_after": health_after,
        })

        audit = helpers().read_json(self.runtime.work / "main-api-audit.json")
        public_requests = [row for row in audit["events"] if row["path"] == PUBLIC]
        require(public_requests, "actual_public_requests_not_observed")
        for row in public_requests:
            require(not row["cookie_present"] and not row["authorization_present"]
                    and not row["csrf_present"] and not row["referer_present"],
                    "public_branding_inherited_credentials")
            if row["status"] == 200:
                require(row["response_keys"] == ["brand_title", "license_required", "site_title"],
                        "actual_public_api_leaked_private_fields")
        probe_audit = helpers().read_json(self.runtime.work / "probe-api-audit.json")
        require(not probe_audit["events"], "probe_server_received_branding_request")
        self.phase("actual_wire_public_headers_and_projection", {
            "public_request_count": len(public_requests), "cookie_authorization_csrf_referer": 0,
            "only_public_fields": ["brand_title", "license_required", "site_title"],
            "probe_branding_requests": 0,
        })


def run(args):
    namespace = helpers().assert_loopback_namespace()
    require(not helpers().listeners(), "namespace_has_existing_listener")
    require(ROOT.is_relative_to(Path("/tmp")) or ROOT.is_relative_to(Path("/root")),
            "source_not_private_snapshot")
    require(not any(value in str(ROOT) for value in (
        "open-node-zh-release", "open-node-notifications-integration",
        "open-node-notifications-commit", "open-node-notifications-docker",
    )), "frozen_notification_or_zh_snapshot_forbidden")
    require(args.frontend_dir == ROOT / "frontend/dist"
            and args.probe_frontend_dir == ROOT / "frontend/dist-probe", "assets_not_same_source")
    require((args.frontend_dir / "index.html").is_file()
            and (args.probe_frontend_dir / "index.html").is_file(), "frozen_bundles_missing")
    require(not args.output.is_relative_to(ROOT), "evidence_inside_source")
    require(len(args.output.parts) >= 4 and (
        args.output.is_relative_to(Path("/tmp")) or args.output.is_relative_to(Path("/root"))
    ), "evidence_not_private_task_directory")
    require(not any(value in str(args.output) for value in (
        "open-node-zh-release", "open-node-notifications-integration",
        "open-node-notifications-commit", "open-node-notifications-docker",
    )), "evidence_inside_previous_frozen_gate")
    output = private_directory(args.output, empty=True)
    work = private_directory(output / "fixture")
    private_directory(work / "tmp")
    owner = secrets.token_hex(16)
    os.environ["BRANDING_GATE_OWNER"] = owner
    os.environ["TMPDIR"] = str(work / "tmp")
    before = {
        "source": source_files(), "main_assets": helpers().file_manifest(args.frontend_dir, ["."]),
        "probe_assets": helpers().file_manifest(args.probe_frontend_dir, ["."]),
        "production": helpers().production_fingerprint(), "protected_files": protected_files(),
        "resolv_conf": digest(Path("/etc/resolv.conf").read_bytes()),
    }
    write_json(output / "before.json", before)
    report = {
        "status": "running", "revision": args.revision, "owner": owner, "phases": [],
        "fixture_sha256": digest(Path(__file__).read_bytes()), "helper_sha256": HELPER_HASH,
        "namespace": namespace, "host_namespace": os.readlink("/proc/1/ns/net"),
        "network": "fresh lo-only namespace; random inherited loopback listener per bundle",
        "product_timeout_modified": False, "core_api_or_auth_or_store_replaced": False,
        "loopback_fixture_cookie_secure": False, "production_security_settings_modified": False,
        "source": str(ROOT), "main_asset_count": len(before["main_assets"]),
        "probe_asset_count": len(before["probe_assets"]),
    }
    write_json(output / "report.json", report)
    runtime = Runtime(args, output, work, owner, namespace, report)
    failed = None
    try:
        url = runtime.start("main")
        probe_url = runtime.start("probe")
        BrowserGate(runtime, url, probe_url, report).exercise()
    except Exception as error:
        failed = error.code if isinstance(error, GateFailure) else "browser_or_fixture_failure"
        frames = traceback.extract_tb(error.__traceback__)
        own = [frame for frame in frames
               if Path(frame.filename).resolve() == Path(__file__).resolve()]
        summary = str(error).splitlines()[0] if str(error) else type(error).__name__
        for key in ("administrator_password", "subscriber_password", "totp_key"):
            summary = summary.replace(runtime.credentials[key], "<redacted-fixture-secret>")
        summary = re.sub(r"https?://\S+", "<fixture-url>", summary)
        summary = re.sub(r"[A-Za-z0-9_-]{24,}", "<opaque-value>", summary)
        report["failure"] = {"code": failed, "type": type(error).__name__,
                             "source_line": own[-1].lineno if own else None,
                             "safe_first_line": summary[:500],
                             "message_sha256": digest(str(error))}
    finally:
        try:
            runtime.close()
        except Exception:
            failed = failed or "owned_cleanup_failed"
            report["cleanup_error"] = "owned_cleanup_failed"
        after = {
            "source": source_files(),
            "main_assets": helpers().file_manifest(args.frontend_dir, ["."]),
            "probe_assets": helpers().file_manifest(args.probe_frontend_dir, ["."]),
            "production": helpers().production_fingerprint(), "protected_files": protected_files(),
            "resolv_conf": digest(Path("/etc/resolv.conf").read_bytes()),
        }
        write_json(output / "after.json", after)
        report["unchanged"] = {name: before[name] == after[name] for name in before}
        report["owned_processes_remaining"] = len(owner_processes(namespace, owner))
        report["listeners_remaining"] = len(helpers().listeners())
        leaks = []
        for path in work.glob("*.log"):
            raw = path.read_bytes()
            if any(runtime.credentials[key].encode() in raw for key in (
                "administrator_password", "subscriber_password", "totp_key",
            )):
                leaks.append(path.name)
        report["secret_log_leaks"] = leaks
        complete = (
            failed is None and all(report["unchanged"].values())
            and report["owned_processes_remaining"] == report["listeners_remaining"] == 0
            and not leaks and report.get("unexpected_console_count") == 0
            and report.get("pageerror_count") == 0 and report.get("external_request_count") == 0
        )
        report["status"] = "passed" if complete else "failed"
        report["failure_code"] = failed
        write_json(output / "report.json", report)
    require(complete, "branding_browser_gate_failed")
    print(json.dumps({"status": "passed", "report": str(output / "report.json"),
                      "screenshots": len(report["screenshots"])}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--probe-frontend-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--_netns", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_serve", choices=("main", "probe"), help=argparse.SUPPRESS)
    parser.add_argument("--_fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    require(sys.platform == "linux", "only_run_on_private_linux_vps")
    require(re.fullmatch(r"(?:[0-9a-f]{40}|working-tree[-:][A-Za-z0-9._-]{1,100})", args.revision),
            "explicit_revision_required")
    args.frontend_dir = args.frontend_dir.resolve()
    args.probe_frontend_dir = args.probe_frontend_dir.resolve()
    args.output = args.output.absolute()
    if args._serve:
        require(args._fd is not None, "missing_owned_listener")
        serve(args)
    elif args._netns:
        # Inspect real netlink interfaces/routes BEFORE changing even lo. sysfs
        # is shared with the host and is not a namespace membership proof.
        helpers().assert_loopback_namespace()
        subprocess.run(["ip", "link", "set", "lo", "up"], check=True, capture_output=True,
                       timeout=10)
        helpers().assert_loopback_namespace()
        run(args)
    else:
        result = subprocess.run([
            "unshare", "--net", "--", sys.executable, str(Path(__file__).resolve()),
            *sys.argv[1:], "--_netns",
        ], check=False)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "code": error.code if isinstance(error, GateFailure)
                          else "browser_or_fixture_failure"}), flush=True)
        raise SystemExit(1) from None
