"""Run complete API subscription exports in real pinned clients on the VPS."""

import argparse
import base64
import copy
import importlib.util
import json
import os
import pwd
import re
import secrets
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from open_node.domain.subscriptions import SubscriptionPlanCreate
from open_node.services.template_rendering import (
    DEFAULT_CLASH,
    DEFAULT_SURGE,
    parse_template,
)
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "protocol_smoke", Path(__file__).with_name("smoke-protocol-runtime.py")
)
protocols = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocols)
runtime, lifecycle, service = protocols.runtime, protocols.lifecycle, protocols.service
ROOT = Path(__file__).resolve().parents[2]


def agent_command(client, base, operation):
    queued = (
        client.post(base + "/operations/" + operation)
        .raise_for_status()
        .json()["command"]
    )
    return lifecycle.wait_command(client, base, queued)["result_body"]


def configuration(work):
    config, _original, ca, stats_port = protocols.configuration(work)
    tls = copy.deepcopy(config["inbounds"][0]["streamSettings"])
    for tag, kind, transport in [
        ("vless-tls", "vless", "tcp"),
        ("vless-vision", "vless", "tcp"),
        ("vless-ws", "vless", "ws"),
        ("vless-grpc", "vless", "grpc"),
        ("vless-upgrade", "vless", "httpupgrade"),
        ("vmess", "vmess", "tcp"),
        ("trojan", "trojan", "tcp"),
        ("shadowsocks", "shadowsocks", "tcp"),
        ("shadowsocks-2022", "shadowsocks", "tcp"),
        ("hysteria2", "hysteria", "hysteria"),
    ]:
        user = {"email": "original-" + tag, "level": 0}
        settings = {"clients": [user]}
        if kind in {"vless", "vmess"}:
            user["id"] = str(uuid4())
            if kind == "vless":
                settings["decryption"] = "none"
                if tag == "vless-vision":
                    user["flow"] = "xtls-rprx-vision"
        elif kind == "hysteria":
            user["auth"] = str(uuid4())
            settings["version"] = 2
        else:
            user["password"] = str(uuid4())
            if kind == "shadowsocks":
                settings["network"] = "tcp,udp"
                if tag == "shadowsocks-2022":
                    settings["method"] = "2022-blake3-aes-128-gcm"
                    settings["password"] = base64.b64encode(
                        secrets.token_bytes(16)
                    ).decode()
                    user["password"] = base64.b64encode(
                        secrets.token_bytes(16)
                    ).decode()
                else:
                    user["method"] = "aes-128-gcm"
        inbound = {
            "tag": tag,
            "listen": "127.0.0.1",
            "port": runtime.free_port(),
            "protocol": kind,
            "settings": settings,
        }
        if kind not in {"shadowsocks", "vmess"}:
            inbound["streamSettings"] = copy.deepcopy(tls)
            inbound["streamSettings"]["network"] = transport
            if transport == "ws":
                inbound["streamSettings"]["wsSettings"] = {"path": "/edge"}
            elif transport == "grpc":
                inbound["streamSettings"]["grpcSettings"] = {"serviceName": "edge"}
            elif transport == "httpupgrade":
                inbound["streamSettings"]["httpupgradeSettings"] = {
                    "path": "/edge",
                    "host": "localhost",
                }
            elif kind == "hysteria":
                inbound["streamSettings"]["hysteriaSettings"] = {"version": 2}
                inbound["streamSettings"]["tlsSettings"]["alpn"] = ["h3"]
        config["inbounds"].append(inbound)
    return config, ca, stats_port


def client_config(work, kind, payload, binary, ca):
    directory = work / (kind + "-" + uuid4().hex[:8])
    directory.mkdir()
    config = copy.deepcopy(payload)
    port, control = runtime.free_port(), runtime.free_port()
    secret = secrets.token_urlsafe(20)
    if kind in {"uri-list", "base64"}:
        subscription = directory / "subscription.txt"
        subscription.write_text(payload)
        subscription.chmod(0o600)
        config = {
            "allow-lan": False,
            "mode": "rule",
            "log-level": "info",
            "proxy-providers": {
                "subscription": {"type": "file", "path": str(subscription)}
            },
            "proxy-groups": [
                {"name": "Proxy", "type": "select", "use": ["subscription"]}
            ],
            "rules": ["MATCH,Proxy"],
        }
    if kind in {"clash", "uri-list", "base64"}:
        config["mixed-port"] = port
        config["external-controller"] = f"127.0.0.1:{control}"
        config["secret"] = secret
        args = [str(binary), "-d", str(directory), "-f", str(directory / "config.json")]
        validate = [
            str(binary),
            "-t",
            "-d",
            str(directory),
            "-f",
            str(directory / "config.json"),
        ]
    elif kind == "sing-box":
        config["inbounds"][0]["listen_port"] = port
        config["experimental"] = {
            "clash_api": {
                "external_controller": f"127.0.0.1:{control}",
                "secret": secret,
            }
        }
        args = [str(binary), "run", "-c", str(directory / "config.json")]
        validate = [str(binary), "check", "-c", str(directory / "config.json")]
    else:
        config["inbounds"][0]["port"] = port
        args = [str(binary), "run", "-config", str(directory / "config.json")]
        validate = [
            str(binary),
            "run",
            "-test",
            "-config",
            str(directory / "config.json"),
        ]
    runtime.write_private(directory / "config.json", config)
    env = {**os.environ, "SSL_CERT_FILE": str(ca)}
    checked = subprocess.run(
        validate, capture_output=True, text=True, env=env, check=False, timeout=30
    )
    assert checked.returncode == 0, (kind, checked.stdout, checked.stderr)
    return directory, args, env, port, control, secret


def diagnostic_forwards(socks_port, echo_port, kind, node_index):
    """Keep runtime.forwards' exact probe; log only safe failure metadata."""
    started = time.monotonic()
    result = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--noproxy",
            "",
            "--socks5-hostname",
            f"127.0.0.1:{socks_port}",
            "--max-time",
            "2",
            f"http://127.0.0.1:{echo_port}/fixture",
        ],
        capture_output=True,
        check=False,
        timeout=5,
    )
    forwarded = result.returncode == 0 and result.stdout == runtime.RESPONSE_BODY
    if not forwarded:
        categories = {
            0: "body_mismatch",
            7: "connect_failure",
            18: "partial_transfer",
            22: "http_error",
            28: "timeout",
            35: "tls_handshake_failure",
            52: "empty_reply",
            55: "send_failure",
            56: "receive_failure",
            60: "certificate_verification_failure",
        }
        record = {
            "client_format": kind
            if kind in {"clash", "sing-box", "xray", "uri-list", "base64"}
            else "other",
            "node_index": node_index,
            "category": categories.get(result.returncode, "curl_nonzero"),
            "curl_returncode": result.returncode,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "stdout_bytes": len(result.stdout),
            "expected_bytes": len(runtime.RESPONSE_BODY),
            "stderr_bytes": len(result.stderr),
        }
        print("TCP_PROBE " + json.dumps(record, sort_keys=True), flush=True)
    return forwarded


def exercise_export(work, client, token, kind, binary, ca, echo, udp, reports):
    response = client.get(f"/api/v1/subscribe/{token}?format={kind}").raise_for_status()
    payload = (
        yaml.safe_load(response.text)
        if kind == "clash"
        else response.text
        if kind in {"uri-list", "base64"}
        else response.json()
    )
    if kind == "clash":
        mieru = [proxy for proxy in payload["proxies"] if proxy["type"] == "mieru"]
        assert len(mieru) == 2, mieru
        assert {proxy["transport"] for proxy in mieru} == {"TCP", "UDP"}, mieru
        assert all(proxy.get("udp") is True for proxy in mieru), mieru
    nodes = [node for node in reports[kind]["nodes"] if node["available"]]
    assert int(response.headers["x-open-node-included-nodes"]) == len(nodes)
    directory, args, env, port, control, secret = client_config(
        work, kind, payload, binary, ca
    )
    with runtime.process(directory, "client", args, env=env):
        try:
            runtime.poll(
                kind + " full exported config starts", lambda: runtime.port_open(port)
            )
            if kind != "xray":
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{control}",
                    headers={"Authorization": "Bearer " + secret},
                    trust_env=False,
                    timeout=3,
                ) as api:
                    selected_names = (
                        api.get("/proxies/Proxy").raise_for_status().json()["all"]
                    )
                    assert set(selected_names) == {node["name"] for node in nodes}, (
                        kind,
                        selected_names,
                    )
                    for node_index, node in enumerate(nodes):
                        api.put(
                            "/proxies/Proxy", json={"name": node["name"]}
                        ).raise_for_status()
                        runtime.poll(
                            kind + " " + node["name"] + " TCP",
                            lambda node_index=node_index: diagnostic_forwards(
                                port, echo, kind, node_index
                            ),
                        )
                        runtime.poll(
                            kind + " " + node["name"] + " UDP",
                            lambda: protocols.udp_forwards(port, udp),
                        )
            else:
                runtime.poll(
                    "xray unselected full export forwards",
                    lambda: diagnostic_forwards(port, echo, kind, -1),
                )
        except BaseException:
            print((directory / "client.log").read_text()[-18000:], flush=True)
            raise
    if kind == "xray":
        for node_index, node in enumerate(nodes):
            selected = (
                client.get(
                    f"/api/v1/subscribe/{token}?format=xray&node_id={node['node_id']}"
                )
                .raise_for_status()
                .json()
            )
            assert len(selected["outbounds"]) == 1
            directory, args, env, port, _, _ = client_config(
                work, kind, selected, binary, ca
            )
            with runtime.process(directory, "client", args, env=env):
                try:
                    runtime.poll(
                        "xray selected config starts",
                        lambda port=port: runtime.port_open(port),
                    )
                    runtime.poll(
                        "xray " + node["name"] + " TCP",
                        lambda port=port, node_index=node_index: diagnostic_forwards(
                            port, echo, kind, node_index
                        ),
                    )
                    runtime.poll(
                        "xray " + node["name"] + " UDP",
                        lambda port=port: protocols.udp_forwards(port, udp),
                    )
                except BaseException:
                    print((directory / "client.log").read_text()[-12000:], flush=True)
                    raise


def sensitive_fields(page):
    return [
        page.locator('input[type="password"], input[readonly], textarea[readonly]'),
        page.locator(".ant-qrcode"),
    ]


def browser_workflow(client, url, output, username, node_id):
    output.mkdir(parents=True, exist_ok=True)
    alternate = username + "-other"
    client.post("/api/v1/users", json={"username": alternate}).raise_for_status()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="zh-CN"
        )
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
            page.goto(url + "/subscriptions")
            expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
            page.get_by_role("tab", name="分配", exact=True).click()
            user = page.get_by_role("combobox", name="订阅用户", exact=True)
            user.click()
            page.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text(username, exact=True).click()
            page.get_by_role("button", name="获取链接", exact=True).click()
            format_control = page.get_by_role(
                "combobox", name="客户端格式", exact=True
            )
            for width, height, label in [
                (1440, 900, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]:
                page.set_viewport_size({"width": width, "height": height})
                format_control.scroll_into_view_if_needed()
                format_control.click()
                page.locator(
                    ".ant-select-dropdown:visible .ant-select-item-option"
                ).get_by_text("sing-box JSON", exact=True).click()
                expect(
                    page.locator(".ant-alert").filter(has_text="snell6").first
                ).to_be_visible()
                format_control.click()
                page.locator(
                    ".ant-select-dropdown:visible .ant-select-item-option"
                ).get_by_text("Xray JSON", exact=True).click()
                select = page.get_by_role("combobox", name="Xray 节点", exact=True)
                expect(select).to_be_enabled()
                select.click()
                page.locator(
                    ".ant-select-dropdown:visible .ant-select-item-option"
                ).get_by_text("subscription-clients snell6", exact=True).click()
                expect(page.get_by_label("对应格式链接", exact=True)).to_have_value(
                    re.compile(".*node_id=" + node_id)
                )
                lifecycle.ui.check_layout(page)
                labels = page.get_by_test_id("subscriptions-view").locator(
                    ".ant-btn > span:not([role=img]):visible, "
                    ".ant-select-selection-item:visible"
                )
                assert labels.count() > 0
                assert labels.evaluate_all(
                    "items => items.every("
                    "el => el.scrollWidth <= el.clientWidth + 1 "
                    "&& el.scrollHeight <= el.clientHeight + 1)"
                ), "Control labels are clipped"
                select.scroll_into_view_if_needed()
                page.screenshot(
                    path=output / ("subscription-xray-" + label + ".png"),
                    full_page=False,
                    mask=sensitive_fields(page),
                )
            pending = []

            def hold_preview(route):
                pending.append((route, route.fetch()))

            page.route("**/subscription-preview?format=sing-box", hold_preview)
            format_control.click()
            page.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text("sing-box JSON", exact=True).click()
            expect(
                page.get_by_role("combobox", name="Xray 节点", exact=True)
            ).to_have_count(0)
            format_control.click()
            page.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text("Xray JSON", exact=True).click()
            expect(
                page.get_by_role("combobox", name="Xray 节点", exact=True)
            ).to_be_enabled()
            assert len(pending) == 1
            route, response = pending.pop()
            route.fulfill(response=response)
            page.unroute("**/subscription-preview?format=sing-box", hold_preview)
            expect(page.get_by_label("对应格式链接", exact=True)).to_have_value(
                re.compile(".*format=xray.*")
            )
            pending = []

            def hold_token(route):
                pending.append((route, route.fetch()))

            page.route("**/users/" + username + "/subscription-token", hold_token)
            page.get_by_role("button", name="获取链接", exact=True).click()
            user.click()
            page.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text(alternate, exact=True).click()
            expect(page.get_by_label("订阅链接", exact=True)).to_have_count(0)
            assert len(pending) == 1
            route, response = pending.pop()
            route.fulfill(response=response)
            page.unroute("**/users/" + username + "/subscription-token", hold_token)
            expect(page.get_by_role("button", name="获取链接", exact=True)).to_be_enabled()
            expect(page.get_by_label("订阅链接", exact=True)).to_have_count(0)
            assert not errors, errors
            print(
                "PASS desktop/mobile/narrow compatibility report and selected Xray URL",
                flush=True,
            )
        except BaseException:
            print(
                page.evaluate(
                    """() => ({
                        width: innerWidth,
                        documentWidth: document.documentElement.scrollWidth,
                        overflow: [...document.querySelectorAll('main *')]
                            .filter(el => el.checkVisibility({checkVisibilityCSS: true})
                                && el.getBoundingClientRect().right > innerWidth + 1)
                            .slice(0, 20)
                            .map(el => ({
                                tag: el.tagName,
                                class: el.className,
                                right: el.getBoundingClientRect().right,
                                width: el.getBoundingClientRect().width,
                            })),
                    })"""
                ),
                flush=True,
            )
            page.screenshot(
                path=output / "subscription-failure.png",
                full_page=True,
                mask=sensitive_fields(page),
            )
            raise
        finally:
            context.close()
            browser.close()


def capture_templates(page, output, name):
    for width, height, suffix in (
        (1440, 1000, "desktop"),
        (390, 844, "mobile"),
        (320, 740, "narrow"),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(250)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        for label in ("模板默认设置", "订阅模板库", "模板编辑器"):
            surface = page.get_by_label(label, exact=True)
            expect(surface).to_have_count(1)
            assert surface.evaluate(
                "element => element.scrollWidth <= element.clientWidth + 1"
            )
        page.screenshot(
            path=output / f"templates-{name}-{suffix}.png",
            full_page=True,
            animations="disabled",
            mask=sensitive_fields(page),
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


def template_workflow(
    work, args, client, backend, base, ca, echo, udp, username, plan, token, reports
):
    templates = "/api/v1/subscription-templates"
    before_credentials = client.get(f"/api/v1/users/{username}/credentials").json()
    before_token = client.get(
        "/api/v1/user-subscription-token", params={"username": username}
    ).json()
    pid = agent_command(client, base, "limiter/status")["pid"]
    clash_source = DEFAULT_CLASH.replace(
        "rules:\n  - MATCH,Proxy\n",
        "proxy-groups:\n"
        "  - {name: Proxy, type: select, proxies: [__PROXY_NODES__]}\n"
        "  - {name: Backup, type: select, proxies: [DIRECT]}\n"
        "rules:\n  - MATCH,Proxy\n"
        "x-open-node-smoke: clash-custom\n",
    ).replace(
        "proxy-groups:\n  - name: Proxy\n    type: select\n    proxies: [__PROXY_NODES__]\n",
        "",
    )
    surge_source = DEFAULT_SURGE.replace(
        "loglevel = notify", "loglevel = warning\nx-open-node-smoke = surge-custom"
    )

    def create(name, format, content, owner=None, public=False):
        return (
            client.post(
                templates,
                json={
                    "name": name,
                    "format": format,
                    "content": content,
                    "owner_username": owner,
                    "is_public": public,
                },
            )
            .raise_for_status()
            .json()
        )

    clash = create("vps-custom.yaml", "clash", clash_source, public=True)
    surge = create("vps-custom.conf", "surge", surge_source, public=True)
    stale = dict(clash)
    updated = (
        client.put(
            templates + "/" + clash["id"],
            json={
                **{
                    field: clash[field]
                    for field in (
                        "name",
                        "format",
                        "content",
                        "owner_username",
                        "is_public",
                    )
                },
                "content": clash["content"] + "# revision update\n",
                "expected_revision": clash["revision"],
            },
        )
        .raise_for_status()
        .json()
    )
    assert (
        client.put(
            templates + "/" + clash["id"],
            json={
                **{
                    field: stale[field]
                    for field in (
                        "name",
                        "format",
                        "content",
                        "owner_username",
                        "is_public",
                    )
                },
                "expected_revision": stale["revision"],
            },
        ).status_code
        == 409
    )
    clash = updated

    read = client.get(f"/api/v1/plans/{plan['id']}/settings").raise_for_status().json()
    saved = (
        client.put(
            f"/api/v1/plans/{plan['id']}/settings",
            json={
                **{
                    field: read["plan"][field]
                    for field in SubscriptionPlanCreate.model_fields
                },
                "clash_template_id": clash["id"],
                "surge_template_id": surge["id"],
                "expected_revision": read["revision"],
                "acknowledge_runtime_restart": True,
            },
        )
        .raise_for_status()
        .json()
    )
    assert saved["commands"] == []

    clash_response = client.get(
        f"/api/v1/subscribe/{token}?format=clash"
    ).raise_for_status()
    clash_config = yaml.safe_load(clash_response.text)
    assert clash_config["x-open-node-smoke"] == "clash-custom"
    assert [group["name"] for group in clash_config["proxy-groups"]] == [
        "Proxy",
        "Backup",
    ]
    exercise_export(work, client, token, "clash", args.mihomo, ca, echo, udp, reports)

    surge_response = client.get(
        f"/api/v1/subscribe/{token}?format=surge"
    ).raise_for_status()
    parsed = parse_template(surge_response.text, "surge")
    assert "x-open-node-smoke = surge-custom" in surge_response.text
    assert len(
        [
            line
            for section, line in parsed["chunks"]
            if section == "proxy"
            and "=" in line
            and not line.lstrip().startswith(("#", ";", "//"))
        ]
    ) == len([node for node in reports["surge"]["nodes"] if node["available"]])

    password = secrets.token_urlsafe(24)
    account = (
        client.get("/api/v1/subscriber-accounts", params={"username": username})
        .raise_for_status()
        .json()
    )
    client.put(
        "/api/v1/subscriber-accounts",
        params={"username": username},
        json={"expected_revision": account["revision"], "new_password": password},
    ).raise_for_status()

    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        admin_context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="zh-CN"
        )
        account_context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, locale="zh-CN"
        )
        errors = []
        try:
            admin_context.add_cookies(
                [
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "url": backend,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for cookie in client.cookies.jar
                ]
            )
            admin = admin_context.new_page()
            portal = account_context.new_page()
            admin.on("pageerror", lambda error: errors.append(str(error)))
            portal.on("pageerror", lambda error: errors.append(str(error)))
            admin.goto(backend + "/templates")
            expect(admin.locator("html")).to_have_attribute("lang", "zh-CN")
            expect(
                admin.get_by_role("heading", name="订阅模板", exact=True)
            ).to_be_visible()
            admin.get_by_text("vps-custom.yaml", exact=True).click()
            expect(admin.get_by_label("模板源码", exact=True)).to_have_value(
                re.compile("x-open-node-smoke")
            )
            preview_user = admin.get_by_role(
                "combobox", name="预览用户", exact=True
            )
            preview_user.click()
            admin.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text(re.compile(re.escape(username))).first.click()
            admin.get_by_role("button", name="预览", exact=True).click()
            expect(admin.get_by_text(re.compile(r"已包含 \d+ 个节点"))).to_be_visible()
            capture_templates(admin, args.output, "admin")

            subscriber_select = admin.get_by_role(
                "combobox", name="用户", exact=True
            )
            subscriber_select.click()
            admin.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text(re.compile(re.escape(username))).first.click()
            permission = admin.get_by_label("允许个人模板", exact=True)
            permission.check()
            with admin.expect_response(
                lambda response: (
                    response.url.endswith(
                        "/subscription-templates/settings?username=" + username
                    )
                    and response.request.method == "PUT"
                )
            ) as permission_saved:
                admin.get_by_role("button", name="保存默认设置", exact=True).click()
            assert permission_saved.value.status == 200, permission_saved.value.text()
            assert permission_saved.value.json()["enabled"] is True

            portal.goto(backend + "/account")
            expect(portal.locator("html")).to_have_attribute("lang", "zh-CN")
            portal.get_by_label("用户名", exact=True).fill(username)
            portal.get_by_label("密码", exact=True).fill(password)
            portal.get_by_role("button", name="登录", exact=True).click()
            portal.get_by_role("tab", name="模板", exact=True).click()
            expect(portal.get_by_text("允许编辑", exact=True)).to_be_visible()
            portal.get_by_text("vps-custom.conf", exact=True).click()
            expect(portal.get_by_label("模板源码", exact=True)).to_have_value(
                re.compile("surge-custom")
            )
            capture_templates(portal, args.output, "account")
            assert not errors, errors
        finally:
            admin_context.close()
            account_context.close()
            browser.close()

    assert (
        client.get(f"/api/v1/users/{username}/credentials").json() == before_credentials
    )
    assert (
        client.get(
            "/api/v1/user-subscription-token", params={"username": username}
        ).json()
        == before_token
    )
    assert agent_command(client, base, "limiter/status")["pid"] == pid
    print(
        "PASS custom Clash/Surge templates, real Mihomo forwarding and both template UIs",
        flush=True,
    )


def exercise(work, fixture, args, client, backend, endpoint, control_ca, echo, udp):
    config, ca, stats_port = configuration(work)
    created = (
        client.post("/api/v1/servers", json={"name": "subscription-clients"})
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    xray_config, agent_config = work / "xray-input.json", work / "agent-input.json"
    runtime.write_private(xray_config, config)
    checked = subprocess.run(
        [str(args.xray), "run", "-test", "-config", str(xray_config)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert checked.returncode == 0, (checked.stdout, checked.stderr)
    runtime.write_private(
        agent_config,
        {
            "master_url": endpoint,
            "ca_file": str(control_ca),
            "token": created["agent_token"],
            "connection_mode": "websocket",
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "stats_address": f"127.0.0.1:{stats_port}",
        },
    )
    fixture.cli(
        "install",
        "--wheel",
        args.wheel,
        "--config",
        agent_config,
        "--xray-config",
        xray_config,
        "--xray",
        args.xray,
    )
    command = (
        client.post(base + "/operations/scan").raise_for_status().json()["command"]
    )
    lifecycle.wait_command(client, base, command)
    imported = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()
    )
    nodes = imported["created_nodes"]
    assert len(nodes) == len(config["inbounds"]), imported
    server_key = next(
        inbound["settings"]["password"]
        for inbound in config["inbounds"]
        if inbound["tag"] == "shadowsocks-2022"
    )
    assert server_key not in str(imported)
    username = "subscription-reader"
    client.post("/api/v1/users", json={"username": username}).raise_for_status()
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "All client protocols",
                "traffic_limit_gb": 64,
                "node_ids": [node["id"] for node in nodes],
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    assigned = (
        client.post(
            f"/api/v1/users/{username}/plan",
            json={
                "plan_id": plan["id"],
                "queue_agent_commands": True,
                "no_restart": False,
            },
        )
        .raise_for_status()
        .json()
    )
    for command in assigned["commands"]:
        lifecycle.wait_command(client, base, command)
    token = (
        client.post(f"/api/v1/users/{username}/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    reports = {
        kind: client.get(f"/api/v1/users/{username}/subscription-preview?format={kind}")
        .raise_for_status()
        .json()
        for kind in ("clash", "surge", "sing-box", "xray", "uri-list", "base64")
    }
    assert server_key not in str(reports)
    excluded = {
        "clash": {"snell6", "snell6-unshaped"},
        "sing-box": {
            "snell4",
            "snell4-http",
            "snell5-tls",
            "snell6",
            "snell6-unshaped",
            "mieru-tcp",
            "mieru-udp",
        },
        "xray": {"mieru-tcp", "mieru-udp"},
        "surge": {
            "snell5-tls",
            "mieru-tcp",
            "mieru-udp",
            "vless-tls",
            "vless-vision",
            "vless-ws",
            "vless-grpc",
            "vless-upgrade",
        },
        "uri-list": {
            "anytls",
            "snell4",
            "snell4-http",
            "snell5-tls",
            "snell6",
            "snell6-unshaped",
            "mieru-tcp",
            "mieru-udp",
            "vless-upgrade",
        },
    }
    excluded["base64"] = excluded["uri-list"]
    for kind, report in reports.items():
        expected = {
            node["id"] for node in nodes if node["inbound_tag"] not in excluded[kind]
        }
        actual = {node["node_id"] for node in report["nodes"] if node["available"]}
        assert expected == actual, (kind, report)
    surge = client.get(f"/api/v1/subscribe/{token}?format=surge").raise_for_status()
    assert surge.headers["content-type"].startswith("text/plain")
    assert ".conf" in surge.headers["content-disposition"]
    parsed_surge = parse_template(surge.text, "surge")
    surge_names = {
        line.partition("=")[0].strip()
        for section, line in parsed_surge["chunks"]
        if section == "proxy"
        and "=" in line
        and not line.lstrip().startswith(("#", ";", "//"))
    }
    assert surge_names == {
        node["name"] for node in reports["surge"]["nodes"] if node["available"]
    }
    if not args.templates_only:
        for kind, binary in (
            ("clash", args.mihomo),
            ("sing-box", args.sing_box),
            ("xray", args.xray),
            ("uri-list", args.mihomo),
            ("base64", args.mihomo),
        ):
            exercise_export(work, client, token, kind, binary, ca, echo, udp, reports)
        snell6 = next(node for node in nodes if node["inbound_tag"] == "snell6")
        browser_workflow(client, backend, args.output, username, snell6["id"])
    agent_command(client, base, "scan")
    template_workflow(
        work, args, client, backend, base, ca, echo, udp, username, plan, token, reports
    )


def run(args):
    original_fixture = service.Fixture
    owned_fixtures = []

    class TrackedFixture(original_fixture):
        def __init__(self, work):
            super().__init__(work)
            owned_fixtures.append(self)

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with (
            lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _),
            protocols.udp_echo() as udp,
        ):
            try:
                exercise(work, fixture, args, client, backend, endpoint, ca, echo, udp)
            except BaseException:
                print(
                    subprocess.run(
                        ["journalctl", "-u", fixture.unit, "-n", "80", "--no-pager"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    ).stdout,
                    flush=True,
                )
                raise

    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_TRUSTED_AUTHORITIES"] = "[]"
    service.exercise = callback
    service.Fixture = TrackedFixture
    try:
        service.run(args.wheel, args.xray_archive)
    finally:
        service.Fixture = original_fixture
        for fixture in owned_fixtures:
            assert not fixture.root.exists(), "Owned Agent installation survived cleanup"
            assert not Path("/etc/systemd/system", fixture.unit).exists(), (
                "Owned Agent unit survived cleanup"
            )
            try:
                pwd.getpwnam(fixture.user)
            except KeyError:
                pass
            else:
                raise AssertionError("Owned Agent account survived cleanup")
    print("PASS complete subscriptions in real Mihomo, sing-box and Xray", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "mihomo", "sing-box", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--templates-only", action="store_true")
    run(parser.parse_args())
