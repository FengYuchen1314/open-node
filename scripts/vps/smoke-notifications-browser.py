"""Production React notification UI against an owned, loopback-only fault fixture.

This is NOT a Telegram canary. The real application, SQLite store and notification
worker use a test-only transport. One explicitly selected receipt commit is lost
to exercise the unchanged 40-second lease and late-receipt fencing.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

PREFIX = "/api/v1/notifications"
CHAT_A = "-1001234567101"
CHAT_B = "-1001234567102"
CHAT_C = "-1001234567103"
SUBSCRIBER = "notification-browser-user"
EXPIRY_USER = "notification-expiry-user"
PLAN_NAME = "通知验收套餐"
MODES = {"accepted", "unknown", "failed", "lost_receipt"}


class GateFailure(RuntimeError):
    def __init__(self, code):
        if not re.fullmatch(r"[a-z0-9_]{1,100}", code):
            code = "invalid_fixture_error_code"
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise GateFailure(code)


def phase(name):
    require(bool(re.fullmatch(r"[a-z0-9_]+", name)), "invalid_phase")
    print("PASS notification_browser " + name, flush=True)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def write_json(path, value):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as out:
        temporary = Path(out.name)
        try:
            json.dump(value, out, ensure_ascii=True, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def read_json(path):
    require(path.is_file() and not path.is_symlink(), "private_json_missing")
    require(path.stat().st_size <= 262144, "private_json_size")
    return json.loads(path.read_text(encoding="utf-8"))


def private_directory(path, *, empty=False):
    path = path.absolute()
    require(".." not in path.parts and path != Path("/"), "unsafe_directory")
    require(not any(item.is_symlink() for item in (path, *path.parents)), "symlink_directory")
    if path.exists():
        mode = path.stat()
        require(stat.S_ISDIR(mode.st_mode) and mode.st_uid == os.geteuid(), "unowned_directory")
        require(mode.st_mode & 0o077 == 0, "directory_not_private")
        if empty:
            require(not any(path.iterdir()), "evidence_directory_not_empty")
    else:
        path.mkdir(parents=True, mode=0o700)
    return path


def command_json(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=10, check=False)
    require(result.returncode == 0, "namespace_inspection_failed")
    return json.loads(result.stdout)


def assert_loopback_namespace():
    require(sys.platform == "linux", "linux_required")
    own = os.readlink("/proc/self/ns/net")
    require(own != os.readlink("/proc/1/ns/net"), "host_network_namespace_forbidden")
    links = command_json(["ip", "-j", "link", "show"])
    require({row["ifname"] for row in links} == {"lo"}, "non_loopback_interface")
    for family in ("-4", "-6"):
        for route in command_json(["ip", "-j", family, "route", "show", "table", "all"]):
            require(route.get("dev") == "lo", "external_route_forbidden")
            require(not route.get("gateway") and route.get("dst") != "default", "default_route")
    return own


def listeners():
    result = subprocess.run(["ss", "-H", "-lntu"], capture_output=True, text=True, timeout=10)
    require(result.returncode == 0, "listener_inspection_failed")
    return [row for row in result.stdout.splitlines() if row.strip()]


def file_manifest(root, directories):
    result = {}
    for directory in directories:
        path = root / directory
        require(path.exists(), "manifest_input_missing")
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in files:
            if not item.is_file() or "__pycache__" in item.parts or item.suffix == ".pyc":
                continue
            require(not item.is_symlink(), "source_symlink")
            result[str(item.relative_to(root))] = digest(item.read_bytes())
    return result


def production_fingerprint():
    commands = [
        ["git", "-C", "/opt/open-node", "rev-parse", "HEAD"],
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}} {{.Image}} {{.State.Status}} "
            "{{.State.StartedAt}} {{.RestartCount}} {{json .HostConfig.PortBindings}}",
            "open-node-open-node-1",
        ],
        ["systemctl", "is-enabled", "open-node-compose.service"],
        ["systemctl", "is-active", "open-node-compose.service"],
    ]
    values = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
        require(result.returncode == 0, "production_readonly_probe_failed")
        values.append(result.stdout.strip())
    return digest(b"\n".join(values) + b"\n")


def own_processes(namespace, owner):
    result = []
    for item in Path("/proc").iterdir():
        if not item.name.isdecimal() or int(item.name) == os.getpid():
            continue
        try:
            if os.readlink(item / "ns/net") != namespace:
                continue
            environment = (item / "environ").read_bytes().split(b"\x00")
            if ("NOTIFICATION_GATE_OWNER=" + owner).encode() in environment:
                result.append(int(item.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return result


def cleanup_processes(namespace, owner):
    for signum in (signal.SIGTERM, signal.SIGKILL):
        for identifier in own_processes(namespace, owner):
            with contextlib.suppress(ProcessLookupError):
                os.kill(identifier, signum)
        deadline = time.monotonic() + 3
        while own_processes(namespace, owner) and time.monotonic() < deadline:
            time.sleep(0.1)
    require(not own_processes(namespace, owner), "owned_process_cleanup_failed")


class Control:
    def __init__(self, work, process):
        self.work, self.process, self.sequence = work, process, 0

    def status(self):
        return read_json(self.work / "status.json")

    def send(self, operation, **values):
        self.sequence += 1
        write_json(
            self.work / "command.json",
            {
                "sequence": self.sequence,
                "operation": operation,
                **values,
            },
        )
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            require(self.process.poll() is None, "fixture_process_exited")
            status = self.status()
            require(not status.get("error"), "fixture_command_failed")
            if status["sequence"] == self.sequence:
                return status
            time.sleep(0.05)
        raise GateFailure("fixture_command_timeout")


def serve(args):
    """Trusted fixture construction, never reachable through a product endpoint."""
    assert_loopback_namespace()
    work = private_directory(args.output / "fixture")
    credentials = read_json(work / "credentials.json")
    from cryptography.fernet import Fernet
    from open_node.core.config import Settings
    from open_node.domain.subscriber_auth import SubscriberAccountUpdate
    from open_node.services.inventory import (
        ProductUserModel,
        SubscriptionArchivedTrafficModel,
        SubscriptionPlanModel,
    )
    from open_node.services.notification_worker import NotificationWorker
    from open_node.services.telegram_transport import TelegramOutcome
    from pydantic import SecretStr
    from sqlalchemy import update
    from uvicorn import Config, Server

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///" + str(work / "application.db"),
        frontend_dir=args.frontend_dir,
        session_cookie_secure=False,
        trusted_authorities=[],
        subscriber_totp_key=Fernet.generate_key().decode(),
        certificate_state_dir=work / "certificates",
        notifications_state_dir=work / "notifications",
        external_subscriptions_state_dir=work / "external-subscriptions",
    )
    # main's module-level app also uses this same disposable database, never its
    # normal ./data default. All OPEN_NODE_* inherited settings were stripped.
    os.environ.update(
        {
            "OPEN_NODE_DATABASE_URL": settings.database_url,
            "OPEN_NODE_CERTIFICATE_STATE_DIR": str(settings.certificate_state_dir),
            "OPEN_NODE_NOTIFICATIONS_STATE_DIR": str(settings.notifications_state_dir),
            "OPEN_NODE_EXTERNAL_SUBSCRIPTIONS_STATE_DIR": str(
                settings.external_subscriptions_state_dir
            ),
            "OPEN_NODE_TRUSTED_AUTHORITIES": "[]",
        }
    )
    from open_node.main import create_app
    from open_node.services.auth import OperatorSession

    app = create_app(settings)
    app.state.auth.set_administrator("admin", credentials["administrator_password"])
    now = datetime.now(UTC)
    with app.state.inventory._session() as session:
        session.add(
            ProductUserModel(
                username=SUBSCRIBER,
                display_name="通知验收用户",
                role="user",
                is_active=True,
                created_at=now - timedelta(days=10),
                updated_at=now,
            )
        )
        session.commit()
    account = app.state.subscriber_auth.management(SUBSCRIBER)
    app.state.subscriber_auth.set_password(
        SUBSCRIBER,
        SubscriberAccountUpdate(
            expected_revision=account.revision,
            new_password=SecretStr(credentials["subscriber_password"]),
        ),
    )

    class FixtureTransport:
        def __init__(self):
            self.mode, self.calls, self.drop_next = "accepted", [], False

        async def send(self, token, chat_id, body):
            require(token.get_secret_value() in credentials["tokens"], "unexpected_fixture_token")
            require(chat_id in {CHAT_A, CHAT_B, CHAT_C}, "unexpected_fixture_target")
            index = len(self.calls) + 1
            state = self.mode if self.mode in {"unknown", "failed"} else "accepted"
            code = {
                "accepted": "telegram_accepted",
                "unknown": "telegram_response_timeout",
                "failed": "telegram_forbidden",
            }[state]
            self.calls.append(
                {
                    "number": index,
                    "state": state,
                    "code": code,
                    "chat_digest": digest(chat_id),
                    "text_digest": digest(body),
                }
            )
            self.drop_next = self.mode == "lost_receipt"
            return TelegramOutcome(
                state=state, code=code, message_id=1000 + index if state == "accepted" else None
            )

    transport = FixtureTransport()

    class WorkerStore:
        def __init__(self, store):
            self.store, self.deferred, self.claim_checks = store, [], 0

        def __getattr__(self, name):
            return getattr(self.store, name)

        def claim(self, **values):
            claimed = self.store.claim(**values)
            if claimed:
                saved = self.store.delivery(claimed.delivery_id)
                require(saved.delivery.state == "sending", "sending_not_committed")
                require(saved.delivery.last_attempt_id == claimed.attempt_id, "claim_fence_missing")
                self.claim_checks += 1
            return claimed

        def finish(self, claim, outcome, **values):
            if transport.drop_next:
                transport.drop_next = False
                self.deferred.append((claim, outcome))
                raise RuntimeError("Controlled fixture receipt commit failure")
            return self.store.finish(claim, outcome, **values)

    proxy = WorkerStore(app.state.notifications)
    worker = NotificationWorker(proxy, transport)
    app.state.notification_transport = transport
    state = {"sequence": 0, "auto": False, "error": False, "late_receipts": 0, "seeded": False}

    def publish():
        write_json(
            work / "status.json",
            {
                **state,
                "calls": transport.calls,
                "call_count": len(transport.calls),
                "deferred_count": len(proxy.deferred),
                "committed_claim_checks": proxy.claim_checks,
            },
        )

    def seed_expiry():
        require(not state["seeded"], "fixture_already_seeded")
        active = datetime.now(UTC)
        plan_id = str(uuid4())
        with app.state.inventory._session() as session:
            session.add(
                SubscriptionPlanModel(
                    id=plan_id,
                    name=PLAN_NAME,
                    traffic_limit_bytes=1,
                    cycle_days=30,
                    created_at=active - timedelta(days=20),
                    updated_at=active,
                )
            )
            session.flush()
            session.add(
                ProductUserModel(
                    username=EXPIRY_USER,
                    display_name="临期通知验收用户",
                    role="user",
                    is_active=True,
                    current_plan_id=plan_id,
                    plan_started_at=active - timedelta(days=28),
                    plan_expires_at=active + timedelta(days=2),
                    created_at=active - timedelta(days=35),
                    updated_at=active,
                )
            )
            session.flush()
            session.add(
                SubscriptionArchivedTrafficModel(
                    username=EXPIRY_USER,
                    server_id=str(uuid4()),
                    server_name="Fixture archived server",
                    upload=1,
                    download=2,
                    weighted_upload=1,
                    weighted_download=2,
                    updated_at=active,
                )
            )
            session.commit()
        quota = app.state.inventory.subscription_user_quota(EXPIRY_USER)
        require(quota.over_quota and not quota.available, "fixture_not_actually_overquota")
        state["seeded"] = True

    async def operate(command):
        nonlocal worker
        operation = command["operation"]
        if operation == "mode":
            require(command["mode"] in MODES, "invalid_transport_mode")
            transport.mode = command["mode"]
        elif operation == "pause":
            state["auto"] = False
        elif operation == "resume":
            state["auto"] = True
        elif operation == "step":
            await worker.tick()
        elif operation == "restart_worker":
            worker = NotificationWorker(proxy, transport)
        elif operation == "late_receipt":
            require(len(proxy.deferred) == 1, "missing_controlled_late_receipt")
            claim, outcome = proxy.deferred.pop()
            app.state.notifications.finish(claim, outcome)
            state["late_receipts"] += 1
        elif operation == "seed_expiry":
            seed_expiry()
        elif operation == "expire_sessions":
            with app.state.auth.session.begin() as session:
                session.execute(update(OperatorSession).values(expires_at=0))
        else:
            raise GateFailure("unknown_fixture_command")

    async def drive():
        next_tick = time.monotonic()
        while True:
            try:
                path = work / "command.json"
                if path.exists():
                    command = read_json(path)
                    if command["sequence"] > state["sequence"]:
                        await operate(command)
                        state["sequence"] = command["sequence"]
                if state["auto"] and time.monotonic() >= next_tick:
                    await worker.tick()
                    next_tick = time.monotonic() + 1
                publish()
            except asyncio.CancelledError:
                raise
            except Exception:
                state["error"] = True
                publish()
                return
            await asyncio.sleep(0.1)

    @contextlib.asynccontextmanager
    async def fixture_lifespan(_app):
        task = asyncio.create_task(drive())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # The fixture drives real worker ticks. It has no alternate notification API,
    # no product feature flag and no modified clock/lease/transport timeout.
    app.router.lifespan_context = fixture_lifespan
    publish()
    Server(Config(app, fd=args._fd, access_log=False, log_level="warning")).run()


def wait_ready(url, process):
    import httpx

    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(100):
            require(process.poll() is None, "fixture_exited_before_ready")
            try:
                if client.get(url + "/healthz").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise GateFailure("fixture_readiness_timeout")


def capture(page, output, name):
    from playwright.sync_api import expect

    for width, height, label in (
        (1440, 1000, "desktop"),
        (390, 844, "mobile"),
        (320, 844, "narrow"),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(150)
        require(
            page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "page_overflow"
        )
        if page.get_by_role("dialog").count():
            expect(page.get_by_role("dialog")).to_be_visible()
        elif name == "notifications-default":
            page.get_by_role("heading", name="通知设置", exact=True).scroll_into_view_if_needed()
        elif name == "notifications-unknown":
            page.get_by_text(
                "结果未知：原消息可能已被接受，不能当作未发送。", exact=True
            ).scroll_into_view_if_needed()
        else:
            page.get_by_test_id("notification-preview").locator("pre").scroll_into_view_if_needed()
        page.screenshot(
            path=str(output / f"{name}-{label}.png"),
            full_page=False,
            animations="disabled",
            mask=[page.locator('input[type="password"]')],
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


def ui_login(page, url, username, password, *, account=False):
    from playwright.sync_api import expect

    page.goto(url + ("/account" if account else "/notifications"))
    expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
    page.get_by_label("用户名", exact=True).fill(username)
    page.get_by_label("密码", exact=True).fill(password)
    path = "/api/v1/account/login" if account else "/api/v1/auth/login"
    with page.expect_response(lambda item: urlsplit(item.url).path == path) as login_response:
        page.get_by_role("button", name="登录", exact=True).click()
    require(login_response.value.status == 200, "ui_login_rejected")
    require(login_response.value.json()["authenticated"], "ui_login_not_authenticated")
    if not account:
        expect(page.get_by_role("heading", name="通知设置", exact=True)).to_be_visible()


def exercise(url, work, output, control, credentials):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            env={
                **os.environ,
                "XDG_CACHE_HOME": str(private_directory(work / "browser-cache")),
                "XDG_CONFIG_HOME": str(private_directory(work / "browser-config")),
            }
        )
        context = browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page_errors, console_errors, requests = [], [], []
        page.on("pageerror", lambda _error: page_errors.append(True))
        page.on(
            "console",
            lambda message: console_errors.append(True) if message.type == "error" else None,
        )

        def observed(request):
            path = urlsplit(request.url).path
            if path.startswith(PREFIX):
                requests.append({"method": request.method, "path": path})

        page.on("request", observed)

        def response(path, *, method="GET", payload=None, csrf=True, origin=None):
            headers = {"Content-Type": "application/json", "Origin": origin or url}
            if csrf:
                session = context.request.get(url + "/api/v1/auth/session").json()
                headers["X-CSRF-Token"] = session["csrf_token"]
            result = context.request.fetch(
                url + PREFIX + path, method=method, data=payload, headers=headers
            )
            require(result.headers.get("cache-control") == "no-store", "missing_no_store")
            require(
                result.headers.get("referrer-policy") == "no-referrer", "missing_referrer_policy"
            )
            body = result.body()
            require(
                not any(secret.encode() in body for secret in credentials["tokens"]),
                "api_token_echo",
            )
            return result

        def settings():
            result = response("/settings")
            require(result.status == 200, "settings_read_failed")
            return result.json()

        def detail(identifier):
            result = response("/deliveries/" + identifier)
            require(result.status == 200, "detail_read_failed")
            return result.json()

        def save():
            with page.expect_response(
                lambda item: (
                    urlsplit(item.url).path == PREFIX + "/settings" and item.request.method == "PUT"
                )
            ) as pending:
                page.get_by_role("button", name="保存通知配置", exact=True).click()
            require(pending.value.status == 200, "ui_save_failed")
            expect(
                page.get_by_text("通知配置已保存；保存操作不会发送测试消息。", exact=True)
            ).to_be_visible()

        def replace_token(token):
            page.get_by_label("Bot Token 操作", exact=True).click()
            popup = page.locator(".ant-select-dropdown:visible .ant-select-item-option")
            expect(popup.get_by_text("替换 Bot Token", exact=True)).to_be_visible()
            write_json(
                output / "token-select-dom.json",
                {
                    "role_option_count": page.get_by_role("option").count(),
                    "visible_option_count": popup.count(),
                    "exact_replace_text_count": popup.get_by_text(
                        "替换 Bot Token", exact=True
                    ).count(),
                },
            )
            popup.get_by_text("替换 Bot Token", exact=True).click()
            page.get_by_label("新的 Telegram Bot Token", exact=True).fill(token)

        def refresh(identifier=None):
            page.get_by_role("button", name="刷新通知投递记录", exact=True).click()
            if identifier:
                page.get_by_role("button", name="查看通知投递 " + identifier, exact=True).click()

        def test(*, duplicate=False, lose_response=False):
            page.get_by_role("button", name="发送 Telegram 测试", exact=True).click()
            dialog = page.get_by_role("dialog", name="确认发送测试消息", exact=True)
            expect(dialog).to_be_visible()
            submit = dialog.get_by_role("button", name="确认提交通知发送请求", exact=True)
            expect(submit).to_be_disabled()
            dialog.get_by_label("确认 Telegram 接收目标", exact=True).check()
            held = {}

            def swallow(route):
                real = route.fetch()
                held["value"] = real.json()
                held["request_id"] = route.request.post_data_json["request_id"]
                route.fulfill(response=real, body="{")

            if lose_response:
                page.route("**/api/v1/notifications/test", swallow, times=1)
                with page.expect_response(
                    lambda item: urlsplit(item.url).path == PREFIX + "/test"
                ) as pending:
                    submit.click()
                require(pending.value.status == 202, "lost_post_was_not_accepted")
                expect(page.get_by_test_id("notification-pending")).to_contain_text(
                    "尚未确认原请求结果，不能按失败直接再发一条。"
                )
                require(bool(held), "lost_receipt_not_captured")
                return held["value"], held["request_id"]
            with page.expect_response(
                lambda item: urlsplit(item.url).path == PREFIX + "/test"
            ) as pending:
                submit.click(click_count=2 if duplicate else 1)
            require(pending.value.status == 202, "ui_test_not_queued")
            expect(dialog).not_to_be_visible()
            value = pending.value.json()
            return value, value["delivery"]["request_id"]

        def storage_has_no_secrets():
            value = page.evaluate("JSON.stringify({...localStorage,...sessionStorage})")
            protected = [
                *credentials["tokens"],
                credentials["administrator_password"],
                credentials["subscriber_password"],
            ]
            require(not any(secret in value for secret in protected), "browser_storage_secret")

        try:
            ui_login(page, url, "admin", credentials["administrator_password"])
            initial = settings()
            require(
                initial["revision"] == 0 and not initial["enabled"] and not initial["has_token"],
                "default_not_off",
            )
            require(not (work / "notifications").exists(), "read_created_notification_key")
            page.get_by_role("button", name="预览已保存通知配置", exact=True).click()
            expect(page.get_by_test_id("notification-preview")).to_contain_text("以下仅为示例")
            capture(page, output, "notifications-default")
            page.get_by_label("提前提醒天数", exact=True).fill("8")
            save()
            require(control.status()["call_count"] == 0, "offline_action_sent")
            require(not (work / "notifications").exists(), "disabled_save_created_key")
            page.get_by_label("Telegram Chat ID", exact=True).fill(CHAT_A)
            replace_token(credentials["tokens"][0])
            cleared = []

            def observe_save(route):
                expect(page.get_by_label("新的 Telegram Bot Token", exact=True)).to_have_value("")
                cleared.append(True)
                route.continue_()

            page.route("**/api/v1/notifications/settings", observe_save, times=1)
            save()
            require(cleared == [True], "token_not_cleared_at_request_start")
            page.get_by_role("button", name="重新读取已保存通知配置", exact=True).click()
            expect(page.get_by_text("已配置（不回显）", exact=True)).to_be_visible()
            expect(page.get_by_label("新的 Telegram Bot Token", exact=True)).to_have_count(0)
            page.get_by_role("button", name="预览已保存通知配置", exact=True).click()
            expect(page.get_by_test_id("notification-preview")).to_contain_text("提醒已关闭")
            require(control.status()["call_count"] == 0, "save_or_preview_sent")
            storage_has_no_secrets()
            phase("default_off_secret_cleared_and_offline_actions")

            first, _request = test(duplicate=True)
            first_id = first["delivery"]["id"]
            require(first["delivery"]["state"] == "queued", "test_not_durable_before_send")
            require(
                sum(row == {"method": "POST", "path": PREFIX + "/test"} for row in requests) == 1,
                "double_click_enqueued_twice",
            )
            control.send("step")
            require(detail(first_id)["delivery"]["state"] == "accepted", "accepted_not_persisted")
            refresh(first_id)
            expect(page.get_by_test_id("notification-detail")).to_contain_text("不代表收件人已读")
            phase("explicit_confirmation_and_double_click_idempotency")

            lost, lost_request = test(lose_response=True)
            lost_id = lost["delivery"]["id"]
            page.get_by_role("button", name="查询原通知请求", exact=True).click()
            expect(page.get_by_test_id("notification-pending")).to_have_count(0)
            expect(
                page.get_by_text("已找到原请求的投递记录；没有另建请求或重新发送。", exact=True)
            ).to_be_visible()
            require(
                response("/requests/" + lost_request).json()["id"] == lost_id,
                "request_lookup_mismatch",
            )
            page.wait_for_timeout(3300)
            control.send("step")
            posts_before = sum(
                row["method"] == "POST" and row["path"].endswith("/test") for row in requests
            )
            page.wait_for_timeout(5200)
            require(control.status()["call_count"] == 2, "lookup_or_poll_sent")
            require(
                posts_before
                == sum(
                    row["method"] == "POST" and row["path"].endswith("/test") for row in requests
                ),
                "poll_posted",
            )
            phase("lost_post_receipt_get_lookup_and_readonly_polling")

            control.send("mode", mode="lost_receipt")
            uncertain, _request = test()
            uncertain_id = uncertain["delivery"]["id"]
            control.send("step")
            initial_attempt = detail(uncertain_id)["attempts"][0]
            old_attempt_id = initial_attempt["id"]
            require(control.status()["deferred_count"] == 1, "receipt_fault_not_injected")
            require(
                detail(uncertain_id)["delivery"]["state"] == "sending",
                "fault_did_not_preserve_sending",
            )
            deadline = datetime.fromisoformat(initial_attempt["deadline_at"].replace("Z", "+00:00"))
            started = datetime.fromisoformat(initial_attempt["started_at"].replace("Z", "+00:00"))
            require((deadline - started).total_seconds() == 40, "lease_was_modified")
            page.get_by_label("Telegram Chat ID", exact=True).fill(CHAT_B)
            save()
            queued, _request = test()
            queued_id = queued["delivery"]["id"]
            page.get_by_label("Telegram Chat ID", exact=True).fill("")
            page.get_by_label("Bot Token 操作", exact=True).click()
            page.locator(".ant-select-dropdown:visible .ant-select-item-option").get_by_text(
                "清除已保存的 Token", exact=True
            ).click()
            page.get_by_label("确认清除已保存的 Bot Token", exact=True).check()
            save()
            cancelled = detail(queued_id)
            require(
                cancelled["delivery"]["state"] == "cancelled" and cancelled["attempts"] == [],
                "clear_did_not_cancel_unsent_test",
            )
            require(cancelled["delivery"]["chat_id"] == CHAT_B, "clear_erased_target_snapshot")
            require(
                detail(uncertain_id)["attempts"][0]["chat_id"] == CHAT_A,
                "clear_changed_old_attempt",
            )
            page.get_by_label("Telegram Chat ID", exact=True).fill(CHAT_B)
            replace_token(credentials["tokens"][1])
            save()
            require(
                detail(queued_id)["delivery"]["state"] == "cancelled",
                "restore_replayed_explicit_test",
            )
            phase("cleared_configuration_cancels_queue_and_preserves_history")

            subscriber_context = browser.new_context(locale="zh-CN")
            subscriber_page = subscriber_context.new_page()
            subscriber_page.on("pageerror", lambda _error: page_errors.append(True))
            subscriber_page.on(
                "console",
                lambda message: console_errors.append(True) if message.type == "error" else None,
            )
            ui_login(
                subscriber_page, url, SUBSCRIBER, credentials["subscriber_password"], account=True
            )
            profile = subscriber_context.request.get(url + "/api/v1/account/me")
            require(
                profile.status == 200 and profile.json()["username"] == SUBSCRIBER,
                "subscriber_ui_login_failed",
            )
            require(
                subscriber_context.request.get(url + PREFIX + "/settings").status == 401,
                "subscriber_accessed_admin_notifications",
            )
            subscriber_context.close()
            revision = settings()["revision"]
            rejected = response(
                "/test",
                method="POST",
                payload={"expected_revision": revision, "request_id": str(uuid4())},
                csrf=False,
            )
            require(rejected.status == 403, "missing_csrf_accepted")
            rejected = response(
                "/test",
                method="POST",
                payload={"expected_revision": revision, "request_id": str(uuid4())},
                origin="https://wrong-origin.invalid",
            )
            require(rejected.status == 403, "foreign_origin_accepted")
            require(control.status()["call_count"] == 3, "security_checks_sent")
            phase("subscriber_isolation_csrf_and_exact_origin")

            control.send("resume")
            timeout = time.monotonic() + 48
            while detail(uncertain_id)["delivery"]["state"] != "unknown":
                require(time.monotonic() < timeout, "real_lease_recovery_timeout")
                page.wait_for_timeout(250)
            require(datetime.now(UTC) >= deadline, "unknown_before_real_deadline")
            page.wait_for_timeout(1500)
            require(control.status()["call_count"] == 3, "unknown_automatically_replayed")
            control.send("pause")
            refresh(uncertain_id)
            expect(page.get_by_test_id("notification-detail")).to_contain_text(
                "结果未知：原消息可能已被接受，不能当作未发送。"
            )
            capture(page, output, "notifications-unknown")
            phase("real_40_second_lease_unknown_and_no_automatic_replay")

            other = context.new_page()
            other.on("pageerror", lambda _error: page_errors.append(True))
            other.on(
                "console",
                lambda message: console_errors.append(True) if message.type == "error" else None,
            )
            other.goto(url + "/notifications")
            expect(other.get_by_role("heading", name="通知设置", exact=True)).to_be_visible()
            other.get_by_label("提前提醒天数", exact=True).fill("9")
            with other.expect_response(
                lambda item: (
                    urlsplit(item.url).path == PREFIX + "/settings" and item.request.method == "PUT"
                )
            ) as write:
                other.get_by_role("button", name="保存通知配置", exact=True).click()
            require(write.value.status == 200, "second_tab_save_failed")
            other.close()
            page.get_by_role("button", name="人工重试通知 " + uncertain_id, exact=True).click()
            expect(
                page.get_by_text(
                    "已保存通知配置已发生变化，请重新读取配置；旧请求只能先查询对账。", exact=True
                )
            ).to_be_visible()
            page.get_by_role("button", name="重新读取已保存通知配置", exact=True).click()
            expect(page.get_by_label("提前提醒天数", exact=True)).to_have_value("9")
            refresh(uncertain_id)
            page.get_by_role("button", name="人工重试通知 " + uncertain_id, exact=True).click()
            dialog = page.get_by_role("dialog", name="确认人工重试通知", exact=True)
            expect(dialog).to_contain_text(CHAT_B)
            expect(dialog).to_contain_text(CHAT_A)
            submit = dialog.get_by_role("button", name="确认提交通知发送请求", exact=True)
            expect(submit).to_be_disabled()
            dialog.get_by_label("确认 Telegram 接收目标", exact=True).check()
            expect(submit).to_be_disabled()
            capture(page, output, "notifications-retry-confirmation")
            dialog.get_by_label("确认通知可能重复发送", exact=True).check()
            with page.expect_response(
                lambda item: urlsplit(item.url).path.endswith("/retry")
            ) as retry_response:
                submit.click()
            require(retry_response.value.status == 200, "ui_manual_retry_failed")
            retry_receipt = retry_response.value.json()
            require(
                retry_receipt["delivery"]["id"] == uncertain_id
                and retry_receipt["delivery"]["state"] == "queued"
                and len(retry_receipt["attempts"]) == 1
                and retry_receipt["attempts"][0]["id"] == old_attempt_id,
                "retry_receipt_changed_event_or_sent_before_claim",
            )
            control.send("mode", mode="unknown")
            control.send("step")
            newer = detail(uncertain_id)
            new_attempt_id = newer["delivery"]["last_attempt_id"]
            require(
                new_attempt_id != old_attempt_id and newer["delivery"]["state"] == "unknown",
                "manual_retry_attempt_not_fenced",
            )
            require(
                newer["attempts"][0]["chat_id"] == CHAT_A
                and newer["attempts"][1]["chat_id"] == CHAT_B,
                "retry_target_snapshots_wrong",
            )
            rejected = response(
                "/deliveries/" + uncertain_id + "/retry",
                method="POST",
                payload={
                    "expected_revision": settings()["revision"],
                    "request_id": str(uuid4()),
                    "expected_attempt_id": old_attempt_id,
                    "confirm_duplicate_risk": True,
                },
            )
            require(
                rejected.status == 409
                and rejected.json()["code"] == "notification_attempt_conflict",
                "old_attempt_fence_accepted",
            )
            control.send("late_receipt")
            late = detail(uncertain_id)
            require(
                late["delivery"]["last_attempt_id"] == new_attempt_id
                and late["delivery"]["state"] == "unknown",
                "late_receipt_overwrote_new_attempt",
            )
            require(
                late["attempts"][0]["state"] == "accepted"
                and late["attempts"][0]["late_receipt_at"] is not None,
                "late_receipt_history_missing",
            )
            require(
                late["attempts"][1]["state"] == "unknown", "new_history_changed_by_late_receipt"
            )
            refresh(uncertain_id)
            expect(page.get_by_test_id("notification-detail")).to_contain_text("晚到回执")
            phase("fresh_target_config_cas_risk_ack_and_late_receipt_fencing")

            page.get_by_label("Telegram Chat ID", exact=True).fill(CHAT_C)
            save()
            failed, _request = test()
            control.send("mode", mode="failed")
            control.send("step")
            require(
                detail(failed["delivery"]["id"])["delivery"]["state"] == "failed",
                "known_failure_not_displayable",
            )
            control.send("seed_expiry")
            zone = (
                "Asia/Shanghai"
                if datetime.now(ZoneInfo("Asia/Shanghai")).hour >= 9
                else "America/New_York"
            )
            require(datetime.now(ZoneInfo(zone)).hour >= 9, "schedule_fixture_before_local_0900")
            page.get_by_label("通知时区", exact=True).fill(zone)
            page.get_by_role("switch", name="启用套餐临期提醒", exact=True).check()
            save()
            before_schedule = control.status()["call_count"]
            with page.expect_response(
                lambda item: urlsplit(item.url).path == PREFIX + "/preview"
            ) as preview_response:
                page.get_by_role("button", name="预览已保存通知配置", exact=True).click()
            preview = preview_response.value.json()
            require(
                preview["total"] == 1 and preview["candidates"][0]["username"] == EXPIRY_USER,
                "overquota_expiry_not_previewed",
            )
            require(control.status()["call_count"] == before_schedule, "preview_triggered_send")
            expect(page.get_by_test_id("notification-preview")).to_contain_text("不是待发送数量")
            expected_text = digest(preview["sample_message"])
            control.send("mode", mode="accepted")
            control.send("restart_worker")
            control.send("resume")
            timeout = time.monotonic() + 12
            while control.status()["call_count"] == before_schedule:
                require(time.monotonic() < timeout, "scheduler_did_not_send_eligible_expiry")
                page.wait_for_timeout(200)
            latest = control.status()["calls"][-1]
            require(
                latest["text_digest"] == expected_text, "preview_and_scheduler_formatter_differ"
            )
            control.send("restart_worker")
            page.wait_for_timeout(1500)
            require(
                control.status()["call_count"] == before_schedule + 1, "restart_repeated_expiry"
            )
            control.send("pause")
            all_rows = response("/deliveries").json()["deliveries"]
            package = [row for row in all_rows if row["kind"] == "package_expiry"]
            require(
                len(package) == 1 and package[0]["state"] == "accepted",
                "expiry_receipt_not_accepted",
            )
            require(
                {row["state"] for row in all_rows}
                >= {"accepted", "unknown", "failed", "cancelled"},
                "state_coverage_incomplete",
            )
            refresh(package[0]["id"])
            for identifier, label in (
                (first_id, "Telegram 已接受"),
                (uncertain_id, "结果未知"),
                (failed["delivery"]["id"], "失败"),
                (queued_id, "已取消"),
            ):
                row = page.get_by_role("row").filter(
                    has=page.get_by_role("button", name="查看通知投递 " + identifier, exact=True)
                )
                expect(row.get_by_text(label, exact=True)).to_be_visible()
            capture(page, output, "notifications-history-and-preview")
            storage_has_no_secrets()
            phase("actual_overquota_inventory_preview_scheduler_and_restart_dedup")
            require(not page_errors, "browser_page_errors")
            require(not console_errors, "browser_console_errors")
            return {
                "page_errors": len(page_errors),
                "console_errors": len(console_errors),
                "fixture_transport_calls": control.status()["call_count"],
                "real_telegram_canary": False,
                "controlled_receipt_commit_faults": 1,
                "late_receipts": control.status()["late_receipts"],
                "committed_claim_checks": control.status()["committed_claim_checks"],
                "screenshots": 12,
                "all_core_ui_actions_real": True,
            }
        except Exception as error:
            with contextlib.suppress(Exception):
                frames = traceback.extract_tb(error.__traceback__)
                own = [frame for frame in frames if Path(frame.filename) == Path(__file__)]
                write_json(
                    output / "browser-failure.json",
                    {
                        "error_type": type(error).__name__,
                        "source_line": own[-1].lineno if own else None,
                        "page_errors": len(page_errors),
                        "console_errors": len(console_errors),
                        "role_option_count": page.get_by_role("option").count(),
                        "visible_option_count": page.locator(
                            ".ant-select-dropdown:visible .ant-select-item-option"
                        ).count(),
                        "fixture_transport_calls": control.status()["call_count"],
                    },
                )
                page.screenshot(
                    path=str(output / "failure-viewport.png"),
                    full_page=False,
                    animations="disabled",
                    mask=[page.locator('input[type="password"]')],
                )
            raise
        finally:
            context.close()
            browser.close()


def run(args):
    namespace = assert_loopback_namespace()
    require(not listeners(), "namespace_has_existing_listener")
    output = private_directory(args.output, empty=True)
    work = private_directory(output / "fixture")
    private_directory(work / "tmp")
    root = Path(__file__).resolve().parents[2]
    require(root.is_relative_to(Path("/tmp")), "source_not_in_private_tmp_snapshot")
    require(not args.frontend_dir.is_relative_to(Path("/opt")), "production_assets_forbidden")
    require("open-node-zh-release" not in str(args.frontend_dir), "frozen_zh_assets_forbidden")
    require((args.frontend_dir / "index.html").is_file(), "production_frontend_missing")
    source_before = file_manifest(
        root, ["backend/app", "frontend/src", "scripts/vps/smoke-notifications-browser.py"]
    )
    assets_before = file_manifest(args.frontend_dir, [Path(".")])
    production_before = production_fingerprint()
    write_json(output / "source-before.json", source_before)
    write_json(output / "assets-before.json", assets_before)
    owner = secrets.token_hex(16)
    os.environ["NOTIFICATION_GATE_OWNER"] = owner
    os.environ["TMPDIR"] = str(work / "tmp")
    credentials = {
        "administrator_password": secrets.token_urlsafe(32),
        "subscriber_password": secrets.token_urlsafe(32),
        "tokens": ["123456:" + secrets.token_urlsafe(30), "123456:" + secrets.token_urlsafe(30)],
    }
    write_json(work / "credentials.json", credentials)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OPEN_NODE_") and not key.lower().endswith("_proxy")
    }
    environment.update(
        {
            "PYTHONPATH": str(root / "backend/app"),
            "PYTHONPYCACHEPREFIX": str(work / "pycache"),
            "NOTIFICATION_GATE_OWNER": owner,
            "TMPDIR": str(work / "tmp"),
        }
    )
    process, report = None, {}
    with socket.socket() as listener, (work / "service.log").open("w", encoding="utf-8") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = "http://127.0.0.1:" + str(listener.getsockname()[1])
        try:
            process = subprocess.Popen(
                [
                    str(args.python or sys.executable),
                    str(Path(__file__).resolve()),
                    "--frontend-dir",
                    str(args.frontend_dir),
                    "--output",
                    str(output),
                    "--_serve",
                    "--_fd",
                    str(listener.fileno()),
                ],
                cwd=work,
                env=environment,
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            wait_ready(url, process)
            control = Control(work, process)
            report = exercise(url, work, output, control, credentials)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            listener.close()
            cleanup_processes(namespace, owner)
            source_after = file_manifest(
                root, ["backend/app", "frontend/src", "scripts/vps/smoke-notifications-browser.py"]
            )
            assets_after = file_manifest(args.frontend_dir, [Path(".")])
            production_after = production_fingerprint()
            write_json(output / "source-after.json", source_after)
            write_json(output / "assets-after.json", assets_after)
            report.update(
                {
                    "source_unchanged": source_before == source_after,
                    "assets_unchanged": assets_before == assets_after,
                    "production_unchanged": production_before == production_after,
                    "production_fingerprint": production_after,
                    "owned_processes_remaining": len(own_processes(namespace, owner)),
                    "listeners_remaining": len(listeners()),
                    "loopback_only_namespace": namespace != os.readlink("/proc/1/ns/net"),
                }
            )
            write_json(output / "report.json", report)
            require(report["source_unchanged"], "source_changed_during_gate")
            require(report["assets_unchanged"], "assets_changed_during_gate")
            require(report["production_unchanged"], "production_changed_during_gate")
            require(report["listeners_remaining"] == 0, "listener_cleanup_failed")
    screenshots = file_manifest(output, [path.relative_to(output) for path in output.glob("*.png")])
    write_json(output / "screenshots.json", screenshots)
    phase("source_assets_production_unchanged_and_owned_cleanup")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--_netns", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args.frontend_dir, args.output = args.frontend_dir.resolve(), args.output.absolute()
    require(sys.platform == "linux", "run_only_on_isolated_linux_vps")
    if args._serve:
        require(args._fd is not None, "missing_owned_listener")
        serve(args)
    elif args._netns:
        require(
            os.readlink("/proc/self/ns/net") != os.readlink("/proc/1/ns/net"),
            "host_network_namespace_forbidden",
        )
        subprocess.run(
            ["ip", "link", "set", "lo", "up"], check=True, capture_output=True, timeout=10
        )
        run(args)
    else:
        command = [
            "unshare",
            "--net",
            "--",
            sys.executable,
            str(Path(__file__).resolve()),
            *sys.argv[1:],
            "--_netns",
        ]
        result = subprocess.run(command, check=False)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        own = [frame for frame in frames if Path(frame.filename).name == Path(__file__).name]
        print(
            json.dumps(
                {
                    "result": "FAIL",
                    "error_type": type(error).__name__,
                    "code": error.code
                    if isinstance(error, GateFailure)
                    else "browser_or_fixture_failure",
                    "source_line": own[-1].lineno if own else None,
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
