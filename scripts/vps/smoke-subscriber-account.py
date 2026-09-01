"""Exercise subscriber self-service with a real installed Agent and exported Xray client."""

import argparse
import importlib.util
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

import pyotp
from cryptography.fernet import Fernet
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "subscriber_users", Path(__file__).with_name("smoke-user-management.py")
)
users = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(users)
native, runtime, service, lifecycle, servers = (
    users.native,
    users.runtime,
    users.service,
    users.lifecycle,
    users.servers,
)
ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "/api/v1/account"


def capture(page, output, name):
    page.mouse.move(0, 0)
    page.evaluate("document.activeElement?.blur()")
    page.wait_for_function("""() => [...document.querySelectorAll('.ant-tooltip')]
        .every(element => element.getClientRects().length === 0
            || getComputedStyle(element).visibility === 'hidden')""")
    for width, height, suffix in (
        (1440, 1000, "desktop"),
        (390, 844, "mobile"),
        (320, 740, "narrow"),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        if not page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"):
            print(
                "Overflow",
                name,
                width,
                page.evaluate("""() => [...document.querySelectorAll('*')]
                .map(e => ({tag:e.tagName, cls:e.className,
                    width:e.getBoundingClientRect().width, right:e.getBoundingClientRect().right}))
                .filter(e => e.right > innerWidth + 1).slice(0, 30)"""),
                flush=True,
            )
            raise AssertionError("Horizontal page overflow")
        surfaces = page.locator(
            ".application-content:visible, .account-content:visible, "
            ".application-header:visible, .ant-modal-body:visible"
        )
        assert surfaces.count() > 0, "No responsive account or dialog surface found"
        assert surfaces.evaluate_all(
            "items => items.every(item => item.scrollWidth <= item.clientWidth + 1)"
        )
        page.screenshot(
            path=str(output / f"{name}-{suffix}.png"),
            full_page=True,
            animations="disabled",
            mask=[
                page.locator("#account-subscription-url"),
                page.locator("#subscriber-setup-key"),
                page.locator(".totp-qr"),
                page.locator(".recovery-grid"),
                page.get_by_label("临时订阅链接", exact=True),
                page.get_by_label("订阅短链接", exact=True),
                page.get_by_label("自定义短码", exact=True),
                page.get_by_role("dialog", name="订阅短码", exact=True).locator(
                    ".ant-descriptions-item-content"
                ),
            ],
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


def sign_in(page, backend, password):
    page.goto(backend + "/account")
    expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
    page.get_by_label("用户名", exact=True).fill("alice")
    page.get_by_label("密码", exact=True).fill(password)
    page.get_by_role("button", name="登录", exact=True).click()


def expect_current_plan(page):
    expect(
        page.get_by_role("region", name="当前套餐", exact=True).get_by_text(
            "Community", exact=True
        )
    ).to_be_visible()


def setup(work, fixture, args, client, endpoint, ca):
    created = (
        client.post(
            "/api/v1/servers", json={"name": "subscriber-agent", "domain": "127.0.0.1"}
        )
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    stats, port = runtime.free_port(), runtime.free_port()
    agent, xray = work / "subscriber-agent.json", work / "subscriber-xray.json"
    runtime.write_private(
        agent,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
            "connection_mode": args.transport,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 1,
            "stats_address": f"127.0.0.1:{stats}",
        },
    )
    runtime.write_private(
        xray,
        {
            "log": {"loglevel": "warning"},
            "api": {
                "listen": f"127.0.0.1:{stats}",
                "tag": "api",
                "services": ["StatsService"],
            },
            "stats": {},
            "policy": {
                "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}
            },
            "inbounds": [
                {
                    "tag": "subscribers",
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "vless",
                    "settings": {"decryption": "none", "clients": []},
                }
            ],
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        },
    )
    fixture.cli(
        "install",
        "--wheel",
        args.wheel,
        "--config",
        agent,
        "--xray-config",
        xray,
        "--xray",
        args.xray,
    )
    runtime.poll("installed subscriber Agent", fixture.ready)
    assert fixture.properties()["User"] != "root"
    native.command(client, base, "scan")
    node = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"][0]
    )
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Community",
                "traffic_limit_gb": 1,
                "cycle_days": 30,
                "node_ids": [node["id"]],
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    for username in ("alice", "bob"):
        client.post(
            "/api/v1/users",
            json={"username": username, "display_name": username.title()},
        ).raise_for_status()
        client.post(
            f"/api/v1/users/{username}/plan",
            json={"plan_id": plan["id"], "queue_agent_commands": True},
        ).raise_for_status()
        users.wait_access(client, username)
    return plan


def exercise(work, fixture, args, operator, backend, endpoint, ca):
    setup(work, fixture, args, operator, endpoint, ca)
    password, replacement = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    before = (
        operator.get("/api/v1/users/alice/settings").raise_for_status().json()["user"]
    )
    bob_token = (
        operator.post("/api/v1/users/bob/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    bob_config = (
        operator.get("/api/v1/subscribe/" + bob_token, params={"format": "xray"})
        .raise_for_status()
        .json()
    )
    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        administrator = browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="zh-CN"
        )
        primary = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
            locale="zh-CN",
        )
        secondary = browser.new_context(
            viewport={"width": 390, "height": 844}, locale="zh-CN"
        )
        errors, requests = [], []

        def transfer(config, size=4096):
            with (
                servers.exported_client(work, args.xray, config) as socks,
                native.connect(socks, echo) as connection,
            ):
                return native.transfer(connection, size)

        try:
            administrator.add_cookies(
                [
                    {
                        "name": item.name,
                        "value": item.value,
                        "url": backend,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for item in operator.cookies.jar
                ]
            )
            admin = administrator.new_page()
            admin.on("pageerror", lambda error: errors.append(str(error)))
            admin.goto(backend + "/subscriptions")
            admin.get_by_role(
                "button", name="alice 的登录设置", exact=True
            ).click()
            dialog = admin.get_by_role("dialog")
            dialog.get_by_label("新登录密码", exact=True).fill(password)
            dialog.get_by_label("确认登录密码", exact=True).fill(password)
            dialog.get_by_label("撤销该用户的所有已有会话", exact=True).check()
            capture(admin, args.output, "admin-login")
            dialog.get_by_role("button", name="保存密码", exact=True).click()
            expect(
                dialog.get_by_text(
                    "登录密码已保存，已有会话已全部撤销。"
                )
            ).to_be_visible()
            dialog.locator(".ant-modal-footer").get_by_role(
                "button", name="关 闭", exact=True
            ).click()

            page = primary.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            sign_in(page, backend, password + "wrong")
            expect(page.get_by_text("登录凭据错误。", exact=True)).to_be_visible()
            page.get_by_label("密码", exact=True).fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            expect_current_plan(page)
            assert primary.request.get(backend + "/api/v1/servers").status == 401
            assert (
                primary.request.get(backend + "/api/v1/auth/session").json()[
                    "authenticated"
                ]
                is False
            )
            page.get_by_role("combobox", name="客户端格式", exact=True).click()
            page.locator(".ant-select-dropdown:visible").get_by_text(
                "Xray", exact=True
            ).click()
            original_url = page.get_by_label(
                "订阅地址", exact=True
            ).input_value()
            with page.expect_download() as downloaded:
                page.get_by_label("下载订阅", exact=True).click()
            original = json.loads(Path(downloaded.value.path()).read_text())
            transfer(original, 32768)
            runtime.poll(
                "subscriber charged live traffic",
                lambda: (
                    primary.request.get(backend + ACCOUNT + "/me").json()["quota"][
                        "charged_usage_bytes"
                    ]
                    >= 32768
                ),
            )
            page.get_by_role("button", name="刷新账户", exact=True).click()
            expect_current_plan(page)
            page.get_by_role(
                "button", name="复制订阅链接", exact=True
            ).click()
            assert page.evaluate("navigator.clipboard.readText()") == original_url
            capture(page, args.output, "subscription")
            print(
                "PASS subscriber-only API, live Xray download and responsive usage",
                flush=True,
            )

            other = secondary.new_page()
            sign_in(other, backend, password)
            expect_current_plan(other)
            page.get_by_role("tab", name="安全设置", exact=True).click()
            page.get_by_role("button", name="启用", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("当前密码", exact=True).fill(password)
            dialog.get_by_role("button", name="继续", exact=True).click()
            key = dialog.get_by_label("设置密钥", exact=True)
            expect(key).to_be_visible()
            secret = key.input_value()
            image = dialog.get_by_alt_text("验证器绑定二维码")
            expect(image).to_be_visible()
            source = image.get_attribute("src")
            assert source.startswith("data:image/png;base64,")
            pixels = image.evaluate("""image => {
                const canvas = document.createElement('canvas');
                canvas.width = canvas.height = 240;
                const context = canvas.getContext('2d');
                context.drawImage(image, 0, 0);
                const data = context.getImageData(0, 0, 240, 240).data;
                let dark = 0, light = 0;
                for (let i = 0; i < data.length; i += 4) {
                    if (data[i] === 0) ++dark;
                    if (data[i] === 255) ++light;
                }
                return { dark, light };
            }""")
            assert pixels["dark"] > 1000 and pixels["light"] > 1000
            capture(page, args.output, "totp-enrollment")
            used_code = pyotp.TOTP(secret).now()
            dialog.get_by_label("验证器验证码", exact=True).fill(used_code)
            dialog.get_by_role("button", name="验证并启用", exact=True).click()
            expect(dialog.get_by_text("恢复码", exact=True)).to_be_visible()
            with page.expect_download() as codes_download:
                dialog.get_by_role(
                    "button", name="下载恢复码", exact=True
                ).click()
            codes = Path(codes_download.value.path()).read_text().splitlines()
            assert len(codes) == len(set(codes)) == 10
            with sqlite3.connect(work / "backend.db") as db:
                encrypted, hashes = db.execute(
                    "SELECT totp_secret,recovery_hashes FROM subscriber_accounts "
                    "WHERE username='alice'"
                ).fetchone()
                assert secret not in encrypted and all(
                    code not in hashes for code in codes
                )
            dialog.get_by_label(
                "我已妥善保存恢复码", exact=True
            ).check()
            dialog.get_by_role("button", name="完成", exact=True).click()
            other.reload()
            expect(
                other.get_by_role("heading", name="用户登录", exact=True)
            ).to_be_visible()
            sign_in(other, backend, password)
            expect(
                other.get_by_role("heading", name="双重验证", exact=True)
            ).to_be_visible()
            assert secondary.request.get(backend + ACCOUNT + "/me").status == 401
            other.get_by_label("验证器验证码或恢复码", exact=True).fill(
                used_code
            )
            other.get_by_role("button", name="验证", exact=True).click()
            expect(other.get_by_text("登录凭据错误。", exact=True)).to_be_visible()
            other.get_by_label("验证器验证码或恢复码", exact=True).fill(
                pyotp.TOTP(secret).at(time.time() + 30)
            )
            other.get_by_role("button", name="验证", exact=True).click()
            expect_current_plan(other)
            page.get_by_role(
                "button", name="刷新安全设置", exact=True
            ).click()
            expect(
                page.get_by_role("button", name="撤销其他会话", exact=True)
            ).to_be_enabled()
            capture(page, args.output, "sessions")
            page.get_by_role("button", name="撤销其他会话", exact=True).click()
            other.reload()
            expect(
                other.get_by_role("heading", name="用户登录", exact=True)
            ).to_be_visible()
            print(
                "PASS private QR/TOTP, replay rejection and session revocation",
                flush=True,
            )

            sign_in(other, backend, password)
            other.get_by_label("验证器验证码或恢复码", exact=True).fill(
                codes[0]
            )
            other.get_by_role("button", name="验证", exact=True).click()
            expect_current_plan(other)
            assert (
                secondary.request.get(backend + ACCOUNT + "/security").json()[
                    "recovery_codes_remaining"
                ]
                == 9
            )
            page.get_by_role("button", name="修改密码", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("当前密码", exact=True).fill(password)
            dialog.get_by_label("新密码", exact=True).fill(replacement)
            dialog.get_by_label("确认密码", exact=True).fill(replacement)
            dialog.get_by_label("验证器验证码或恢复码", exact=True).fill(
                codes[1]
            )
            dialog.get_by_role("button", name="确认", exact=True).click()
            expect(
                page.get_by_role("heading", name="用户登录", exact=True)
            ).to_be_visible()
            other.reload()
            expect(
                other.get_by_role("heading", name="用户登录", exact=True)
            ).to_be_visible()
            sign_in(page, backend, password)
            expect(page.get_by_text("登录凭据错误。", exact=True)).to_be_visible()
            page.get_by_label("密码", exact=True).fill(replacement)
            page.get_by_role("button", name="登录", exact=True).click()
            page.get_by_label("验证器验证码或恢复码", exact=True).fill(
                codes[0]
            )
            page.get_by_role("button", name="验证", exact=True).click()
            expect(page.get_by_text("登录凭据错误。", exact=True)).to_be_visible()
            page.get_by_label("验证器验证码或恢复码", exact=True).fill(
                codes[2]
            )
            page.get_by_role("button", name="验证", exact=True).click()
            expect_current_plan(page)
            page.get_by_role("tab", name="安全设置", exact=True).click()
            page.get_by_role("button", name="重置链接", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("当前密码", exact=True).fill(replacement)
            dialog.get_by_label("验证器验证码或恢复码", exact=True).fill(
                codes[3]
            )
            dialog.get_by_role("button", name="确认", exact=True).click()
            expect(
                page.get_by_text("订阅链接已重置", exact=True)
            ).to_be_visible()
            assert primary.request.get(original_url).status == 404
            transfer(original)
            print(
                "PASS recovery codes, password/link rotation and retained runtime identity",
                flush=True,
            )

            admin.get_by_role(
                "button", name="alice 的登录设置", exact=True
            ).click()
            dialog = admin.get_by_role("dialog")
            dialog.get_by_label("新登录密码", exact=True).fill(password)
            dialog.get_by_label("确认登录密码", exact=True).fill(password)
            dialog.get_by_label(
                "重置双因素认证及恢复码", exact=True
            ).check()
            dialog.get_by_label("撤销该用户的所有已有会话", exact=True).check()
            dialog.get_by_role("button", name="保存密码", exact=True).click()
            expect(
                dialog.get_by_text(
                    "登录密码已保存，已有会话已全部撤销。"
                )
            ).to_be_visible()
            page.reload()
            expect(
                page.get_by_role("heading", name="用户登录", exact=True)
            ).to_be_visible()
            sign_in(page, backend, password)
            expect_current_plan(page)
            old_cookie = next(
                cookie
                for cookie in primary.cookies()
                if cookie["name"] == "open_node_subscriber"
            )
            operator.patch(
                "/api/v1/users/alice/active", json={"is_active": False}
            ).raise_for_status()
            users.wait_access(operator, "alice")
            page.get_by_role("button", name="刷新账户", exact=True).click()
            expect(
                page.get_by_role("heading", name="用户登录", exact=True)
            ).to_be_visible()
            transfer(bob_config)
            operator.patch(
                "/api/v1/users/alice/active", json={"is_active": True}
            ).raise_for_status()
            users.wait_access(operator, "alice")
            secondary.add_cookies([old_cookie])
            assert secondary.request.get(backend + ACCOUNT + "/me").status == 401
            transfer(original)
            after = (
                operator.get("/api/v1/users/alice/settings")
                .raise_for_status()
                .json()["user"]
            )
            assert all(
                before[field] == after[field]
                for field in (
                    "current_plan_id",
                    "plan_started_at",
                    "plan_expires_at",
                    "created_at",
                )
            )
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not errors, errors
            assert all(url.startswith((backend + "/", "data:")) for url in requests), (
                "Unexpected external browser request"
            )
            print(
                "PASS administrator MFA recovery, account disable/re-enable and isolation",
                flush=True,
            )
        finally:
            administrator.close()
            primary.close()
            secondary.close()
            browser.close()


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_TRUSTED_AUTHORITIES"] = "[]"
    os.environ["OPEN_NODE_SUBSCRIBER_TOTP_KEY"] = Fernet.generate_key().decode()

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    service.run(args.wheel, args.xray_archive)
    print("PASS subscriber account end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
