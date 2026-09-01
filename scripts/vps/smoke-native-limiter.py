"""Exercise free native limits through an installed Agent and real proxy clients."""

import argparse
import copy
import importlib.util
import json
import os
import re
import socket
import socketserver
import ssl
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

import yaml
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "limiter_clients", Path(__file__).with_name("smoke-subscription-clients.py")
)
clients = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clients)
protocols, runtime, lifecycle, service = (
    clients.protocols,
    clients.runtime,
    clients.lifecycle,
    clients.service,
)
ROOT = Path(__file__).resolve().parents[2]


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        try:
            while data := self.request.recv(65536):
                self.request.sendall(data)
        except (OSError, ssl.SSLError):
            pass


@contextmanager
def echo_server(work, *, tls=False):
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

        def get_request(self):
            connection, address = super().get_request()
            if tls:
                connection.settimeout(10)
                try:
                    connection = context.wrap_socket(connection, server_side=True)
                except BaseException:
                    connection.close()
                    raise
            return connection, address

    context = None
    ca = None
    if tls:
        cert, key, _ = lifecycle.nginx_fixture.certificate()
        ca, private = work / "echo-cert.pem", work / "echo-key.pem"
        ca.write_text(cert)
        private.write_text(key)
        private.chmod(0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(ca, private)
    with Server(("127.0.0.1", 0), Echo) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], ca
        finally:
            server.shutdown()
            thread.join(5)


def connect(socks, target, ca=None):
    connection = socket.create_connection(("127.0.0.1", socks), timeout=10)
    try:
        connection.sendall(b"\x05\x01\x00")
        assert protocols.read_exact(connection, 2) == b"\x05\x00"
        connection.sendall(
            b"\x05\x01\x00\x01"
            + socket.inet_aton("127.0.0.1")
            + struct.pack("!H", target)
        )
        header = protocols.read_exact(connection, 4)
        assert header[:2] == b"\x05\x00", header
        if header[3] == 1:
            protocols.read_exact(connection, 4)
        elif header[3] == 4:
            protocols.read_exact(connection, 16)
        else:
            protocols.read_exact(connection, protocols.read_exact(connection, 1)[0])
        protocols.read_exact(connection, 2)
        if ca:
            context = ssl.create_default_context(cafile=str(ca))
            connection = context.wrap_socket(connection, server_hostname="localhost")
        return connection
    except BaseException:
        connection.close()
        raise


def transfer(connection, size=32768):
    payload = os.urandom(size)
    start = time.monotonic()
    connection.sendall(payload)
    assert protocols.read_exact(connection, len(payload)) == payload
    return time.monotonic() - start


def udp_transfer(socks, target):
    with socket.create_connection(("127.0.0.1", socks), timeout=10) as control:
        control.sendall(b"\x05\x01\x00")
        assert protocols.read_exact(control, 2) == b"\x05\x00"
        control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
        header = protocols.read_exact(control, 4)
        assert header[:2] == b"\x05\x00"
        if header[3] == 1:
            protocols.read_exact(control, 4)
        elif header[3] == 4:
            protocols.read_exact(control, 16)
        else:
            protocols.read_exact(control, protocols.read_exact(control, 1)[0])
        relay = struct.unpack("!H", protocols.read_exact(control, 2))[0]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
            datagram.settimeout(10)
            start = time.monotonic()
            for _ in range(32):
                payload = os.urandom(1024)
                frame = b"\x00\x00\x00\x01" + socket.inet_aton("127.0.0.1")
                frame += struct.pack("!H", target) + payload
                datagram.sendto(frame, ("127.0.0.1", relay))
                returned, _ = datagram.recvfrom(65535)
                expected = frame[:10] + protocols.UDP_ECHO_PREFIX + payload
                assert returned == expected, "UDP datagram identity changed"
            return time.monotonic() - start


def check_limiter_layout(page, panel):
    """Keep ordinary controls in view and make every scroll-table control reachable."""
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    controls = panel.locator(
        ".ant-input, .ant-input-number, .ant-select, .ant-btn, .ant-radio-group"
    )
    for control in controls.all():
        if not control.is_visible():
            continue
        table = control.locator(
            "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), "
            "' ant-table-content ')][1]"
        )
        left, right = 0, page.viewport_size["width"]
        if table.count():
            table_bounds = table.bounding_box()
            assert table_bounds and table_bounds["x"] >= 0
            left = table_bounds["x"]
            right = left + table_bounds["width"]
            assert right <= page.viewport_size["width"] + 1, table_bounds
            # Ant Table intentionally scrolls horizontally on small screens. Do
            # not skip its controls: each must fit and be reachable by scrolling.
            control.scroll_into_view_if_needed()
        bounds = control.bounding_box()
        assert (
            bounds
            and bounds["x"] >= left - 1
            and bounds["x"] + bounds["width"] <= right + 1
        ), {
            "bounds": bounds,
            "control": control.evaluate("el => el.outerHTML.slice(0, 300)"),
        }
    for table in panel.locator(".ant-table-content").all():
        table.evaluate("el => { el.scrollLeft = 0; }")


def check_numeric_drafts(page, panel):
    """Blur/Enter must not silently turn invalid rates into unlimited or old values."""
    mutations = []

    def record(request):
        if request.method == "POST" and request.url.endswith("/operations/limiter"):
            mutations.append(request.url)

    page.on("request", record)
    save = panel.get_by_role("button", name="保存限制", exact=True)
    try:
        for label, raw in (
            ("每用户限速（Mbps）", "-1"),
            ("每用户限速（Mbps）", "1e-999"),
            ("每用户限速（Mbps）", "not-a-number"),
            ("每用户限速（Mbps）", ""),
            ("限速值（Mbps）", "-1"),
            ("限速值（Mbps）", ""),
            ("连接数", "-1"),
            ("连接数", "0.4"),
            ("连接数", "1000001"),
        ):
            control = panel.get_by_label(label, exact=True)
            original = control.input_value()
            control.fill(raw)
            expect(save).to_be_disabled()
            control.press("Enter")
            control.press("Tab")
            expect(save).to_be_disabled()
            assert not mutations, (label, raw, mutations)
            control.fill(original)
            control.press("Tab")
            expect(save).to_be_enabled()
        cap = panel.get_by_label("每用户限速（Mbps）", exact=True)
        original = cap.input_value()
        cap.fill("")
        cap.press_sequentially("-")
        expect(cap).to_have_value("-")
        expect(save).to_be_disabled()
        cap.press_sequentially("1")
        expect(cap).to_have_value("-1")
        expect(save).to_be_disabled()
        cap.fill("")
        cap.press_sequentially("1e")
        expect(cap).to_have_value("1e")
        expect(save).to_be_disabled()
        cap.press_sequentially("2")
        cap.press("Tab")
        expect(cap).to_have_value("1e2")
        expect(cap).to_have_attribute("aria-valuenow", "100")
        expect(save).to_be_enabled()
        cap.fill("0")
        cap.press("Tab")
        expect(cap).to_have_value("0")
        expect(save).to_be_enabled()
        assert not mutations, mutations
        cap.fill(original)
        cap.press("Tab")
    finally:
        page.remove_listener("request", record)
    print(
        "PASS numeric blur/Enter, raw drafts, progressive typing and explicit zero",
        flush=True,
    )


def browser_workflow(client, base, backend, output):
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = None
        try:
            context.add_cookies(
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
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(backend + "/config")
            expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
            page.get_by_role("tab", name="限制", exact=True).click()
            panel = page.get_by_role("tabpanel", name="限制", exact=True)
            expect(panel.get_by_label("入站", exact=True)).to_be_visible()
            panel.get_by_role(
                "combobox", name="入站", exact=True
            ).click()
            popup = page.locator(".ant-select-dropdown:visible")
            popup.locator(".ant-select-item-option").get_by_text(
                "vless-vision", exact=True
            ).click()
            check_numeric_drafts(page, panel)
            panel.get_by_label("限速值（Mbps）", exact=True).fill("0.0000001")
            expect(
                panel.get_by_role("button", name="保存限制", exact=True)
            ).to_be_disabled()
            panel.get_by_label("限速值（Mbps）", exact=True).fill("0.75")
            panel.get_by_role("button", name="添加自动限速规则", exact=True).click()
            panel.get_by_label("规则 1 类型", exact=True).get_by_text(
                "突发限速", exact=True
            ).click()
            expect(panel.get_by_role("radio", name="突发限速", exact=True)).to_be_checked()
            for width, height, label in [
                (1440, 900, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]:
                page.set_viewport_size({"width": width, "height": height})
                panel.scroll_into_view_if_needed()
                check_limiter_layout(page, panel)
                panel.scroll_into_view_if_needed()
                page.screenshot(path=output / ("limiter-" + label + ".png"))
            page.set_viewport_size({"width": 1440, "height": 900})
            panel.get_by_role("button", name="保存限制", exact=True).click()
            expect(panel.get_by_text("限制已应用。", exact=True)).to_be_visible(
                timeout=20000
            )
            state = command(client, base, "limiter/status")
            policy = next(
                item
                for item in state["inbounds"]
                if item["inbound_tag"] == "vless-vision"
            )
            assert (
                policy["users"][0]["speed_limit"] == 93750
                and policy["auto_speed_rules"][0]["type"] == "burst"
            )
            command(
                client,
                base,
                "limiter",
                {
                    **policy,
                    "node_limit": 125000,
                    "expected_revision": state["revision"],
                },
            )
            panel.get_by_role("row").filter(
                has=page.get_by_label("用户标识（email）", exact=True)
            ).get_by_label("限速值（Mbps）", exact=True).fill("1")
            panel.get_by_role("button", name="保存限制", exact=True).click()
            expect(
                panel.get_by_text(
                    "限速设置版本已变化，请刷新后应用。", exact=False
                )
            ).to_be_visible(timeout=20000)
            panel.get_by_role("button", name="刷新限制", exact=True).click()
            expect(panel.get_by_label("每用户限速（Mbps）", exact=True)).to_have_value(
                "1", timeout=20000
            )
            panel.get_by_role("button", name="移除限制", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_text("移除限制？", exact=True)).to_be_visible()
            dialog.get_by_role("button", name=re.compile(r"^取\s*消$")).click()
            assert any(
                item["inbound_tag"] == "vless-vision"
                for item in command(client, base, "limiter/status")["inbounds"]
            )
            panel.get_by_role("button", name="移除限制", exact=True).click()
            dialog.get_by_role("button", name="移除", exact=True).click()
            expect(panel.get_by_text("限制已移除。", exact=True)).to_be_visible(
                timeout=20000
            )
            assert not any(
                item["inbound_tag"] == "vless-vision"
                for item in command(client, base, "limiter/status")["inbounds"]
            )
            assert not errors, errors
            print(
                "PASS real desktop/mobile/narrow limit apply, stale revision and confirmed removal",
                flush=True,
            )
        except BaseException:
            if page is not None:
                with suppress(Exception):
                    page.screenshot(path=output / "failure.png", full_page=True)
                    (output / "failure-layout.json").write_text(
                        json.dumps(
                            page.evaluate(
                                "() => ({width: innerWidth,\n"
                                "                                documentWidth: "
                                "document.documentElement.scrollWidth,\n"
                                "                                controls: "
                                "[...document.querySelectorAll("
                                "'.ant-input,.ant-input-number,.ant-select,"
                                ".ant-btn,.ant-radio-group')]\n"
                                "                                    "
                                ".filter(el => el.checkVisibility({checkVisibilityCSS: true}))\n"
                                "                                    "
                                ".map(el => ({label: el.getAttribute('aria-label'),\n"
                                "                                        className: el.className,\n"
                                "                                        "
                                "bounds: el.getBoundingClientRect().toJSON()}))})"
                            ),
                            indent=2,
                        )
                    )
            raise
        finally:
            context.close()
            browser.close()


@contextmanager
def proxy(work, args, node, clash, xray, ca):
    value = next(
        (item for item in clash["proxies"] if item["name"] == node["name"]), None
    )
    if value:
        with protocols.proxy_client(work, args, value, ca) as port:
            yield port
        return
    outbound = next(item for item in xray["outbounds"] if item["tag"] == node["name"])
    directory = work / ("limiter-client-" + uuid4().hex[:8])
    directory.mkdir()
    port = runtime.free_port()
    config = directory / "config.json"
    runtime.write_private(
        config,
        {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                }
            ],
            "outbounds": [outbound],
        },
    )
    with runtime.process(
        directory,
        "proxy",
        [str(args.xray), "run", "-config", str(config)],
        env={**os.environ, "SSL_CERT_FILE": str(ca)},
    ):
        runtime.poll("native exported client starts", lambda: runtime.port_open(port))
        yield port


def command(client, base, operation, body=None, expected="succeeded"):
    queued = (
        client.post(base + "/operations/" + operation, json=body)
        .raise_for_status()
        .json()["command"]
    )
    return lifecycle.wait_command(client, base, queued, expected)["result_body"]


def exercise(work, fixture, args, client, backend, endpoint, control_ca, _http_echo):
    config, ca, stats_port = clients.configuration(work)
    created = (
        client.post("/api/v1/servers", json={"name": "native-limits"})
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    xray_config, agent_config = work / "xray-input.json", work / "agent-input.json"
    runtime.write_private(xray_config, config)
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
    runtime.poll("installed non-root Agent connected", fixture.ready)
    assert fixture.properties()["User"] != "root"
    command(client, base, "scan")
    nodes = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"]
    )
    assert len(nodes) == 18
    client.post("/api/v1/users", json={"username": "limited"}).raise_for_status()
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Native limits",
                "traffic_limit_gb": 100,
                "node_ids": [node["id"] for node in nodes],
                "speed_limit_mbps": 0.5,
                "device_limit": 2,
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    assigned = (
        client.post(
            "/api/v1/users/limited/plan",
            json={
                "plan_id": plan["id"],
                "queue_agent_commands": True,
                "no_restart": False,
            },
        )
        .raise_for_status()
        .json()
    )
    for queued in assigned["commands"]:
        lifecycle.wait_command(client, base, queued)
    state = command(client, base, "limiter/status")
    assert state["available"] and len(state["inbounds"]) == 18, state
    pid = state["pid"]
    assert (fixture.root / "state/limits/policy.json").stat().st_mode & 0o777 == 0o600
    token = (
        client.post("/api/v1/users/limited/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    clash = yaml.safe_load(
        client.get(f"/api/v1/subscribe/{token}?format=clash").raise_for_status().text
    )
    mieru = [proxy for proxy in clash["proxies"] if proxy["type"] == "mieru"]
    assert len(mieru) == 2 and all(proxy.get("udp") is True for proxy in mieru), mieru
    assert {proxy["transport"] for proxy in mieru} == {"TCP", "UDP"}, mieru
    xray = (
        client.get(f"/api/v1/subscribe/{token}?format=xray").raise_for_status().json()
    )
    measurements = {}
    udp_variants = 0
    with (
        echo_server(work) as (echo, _),
        echo_server(work, tls=True) as (tls_echo, tls_ca),
        protocols.udp_echo() as udp,
    ):
        for node in nodes:
            with proxy(work, args, node, clash, xray, ca) as socks:
                with connect(socks, echo) as connection:
                    elapsed = transfer(connection)
                    assert 0.65 <= elapsed <= 8, (node["inbound_tag"], elapsed)
                    measurements[node["inbound_tag"]] = round(elapsed, 3)
                    print(
                        "PASS real combined-direction rate",
                        node["inbound_tag"],
                        elapsed,
                        flush=True,
                    )
                elapsed = udp_transfer(socks, udp)
                assert 0.65 <= elapsed <= 8, (node["inbound_tag"], "UDP", elapsed)
                measurements[node["inbound_tag"] + " UDP"] = round(elapsed, 3)
                udp_variants += 1
                print(
                    "PASS real limited UDP",
                    node["inbound_tag"],
                    elapsed,
                    flush=True,
                )
        assert udp_variants == 18, udp_variants
        print("PASS 18 protocol variants enforce real UDP target limits", flush=True)
        node = next(node for node in nodes if node["inbound_tag"] == "vless-vision")
        policy = copy.deepcopy(
            next(
                item
                for item in state["inbounds"]
                if item["inbound_tag"] == node["inbound_tag"]
            )
        )
        user = policy["users"][0]
        group = user["conn_group"]

        def apply():
            return command(
                client,
                base,
                "limiter",
                {
                    **policy,
                    "expected_revision": command(client, base, "limiter/status")[
                        "revision"
                    ],
                },
            )

        with proxy(work, args, node, clash, xray, ca) as socks:
            with connect(socks, tls_echo, tls_ca) as connection:
                elapsed = transfer(connection, 65536)
                assert elapsed >= 1.5, elapsed
                print("PASS Vision real TLS bulk remains limited", elapsed, flush=True)
                user["speed_limit"] = 0
                apply()
                assert transfer(connection, 65536) < 1, "hot unlimited failed"
                user["speed_limit"] = 62500
                apply()
                assert transfer(connection, 65536) >= 1.5, (
                    "hot cap bypassed existing Vision flow"
                )
                user["device_limit"] = 1
                original = next(
                    item for item in config["inbounds"] if item["tag"] == "vless-vision"
                )["settings"]["clients"][0]
                alias = {
                    "uid": 0,
                    "email": original["email"],
                    "speed_limit": 0,
                    "device_limit": 1,
                    "conn_group": group,
                }
                policy["users"].append(alias)
                apply()
                denied = False
                try:
                    with connect(socks, echo) as extra:
                        transfer(extra, 128)
                except (OSError, ConnectionError):
                    denied = True
                assert denied, "concurrent connection limit was ignored"
                alias_proxy = copy.deepcopy(
                    next(
                        item
                        for item in clash["proxies"]
                        if item["name"] == node["name"]
                    )
                )
                alias_proxy["uuid"] = original["id"]
                with protocols.proxy_client(work, args, alias_proxy, ca) as alias_socks:
                    denied = False
                    try:
                        with connect(alias_socks, echo) as extra:
                            transfer(extra, 128)
                    except (OSError, ConnectionError):
                        denied = True
                    assert denied, "credential alias bypassed shared admission quota"
                policy["users"].remove(alias)
                apply()
                assert command(client, base, "limiter/status")["pid"] == pid
            runtime.poll(
                "closed connection releases quota",
                lambda: (
                    command(client, base, "limiter/status")["conn_counts"].get(group, 0)
                    == 0
                ),
            )
            user["device_limit"] = 2
            apply()
            with (
                connect(socks, echo) as first,
                connect(socks, echo) as second,
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                start = time.monotonic()
                futures = [
                    executor.submit(transfer, connection)
                    for connection in [first, second]
                ]
                for future in futures:
                    future.result(timeout=10)
                assert time.monotonic() - start >= 1.5, (
                    "parallel connections received separate buckets"
                )
            print(
                "PASS live limits, shared rate, admission and release without restart",
                flush=True,
            )
            runtime.poll(
                "parallel flows release both slots",
                lambda: (
                    command(client, base, "limiter/status")["conn_counts"].get(group, 0)
                    == 0
                ),
            )
            user["speed_limit"] = 0
            policy["auto_speed_rules"] = [
                {
                    "type": "sustained",
                    "threshold_mbps": 0.01,
                    "sustained_seconds": 1,
                    "window_seconds": 0,
                    "burst_count": 0,
                    "limit_mbps": 0.5,
                    "limit_duration": 8,
                }
            ]
            apply()
            with connect(socks, echo) as connection:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    transfer(connection, 16384)
                    time.sleep(0.02)
                automatic = command(client, base, "limiter/status")["automatic_limits"]
                assert any(
                    item["email"] == user["email"] for item in automatic.values()
                ), automatic
                runtime.poll(
                    "sustained rule expires",
                    lambda: (
                        not command(client, base, "limiter/status")["automatic_limits"]
                    ),
                    timeout=15,
                )
                assert transfer(connection, 65536) < 1
            policy["auto_speed_rules"] = []
            runtime.poll(
                "automatic test connection releases its slot",
                lambda: (
                    command(client, base, "limiter/status")["conn_counts"].get(group, 0)
                    == 0
                ),
            )
            policy["auto_speed_rules"] = [
                {
                    "type": "burst",
                    "threshold_mbps": 0.01,
                    "sustained_seconds": 1,
                    "window_seconds": 20,
                    "burst_count": 2,
                    "limit_mbps": 0.5,
                    "limit_duration": 8,
                }
            ]
            apply()
            with connect(socks, echo) as connection:
                for _ in range(2):
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        transfer(connection, 16384)
                        time.sleep(0.02)
                    time.sleep(2)
                automatic = command(client, base, "limiter/status")["automatic_limits"]
                assert any(
                    item["email"] == user["email"] for item in automatic.values()
                ), automatic
                assert transfer(connection, 65536) >= 1.5
                runtime.poll(
                    "burst rule expires",
                    lambda: (
                        not command(client, base, "limiter/status")["automatic_limits"]
                    ),
                    timeout=15,
                )
            print("PASS real burst-rule activation and expiry", flush=True)
            policy["auto_speed_rules"] = []
            user["speed_limit"] = 62500
            apply()
        before = command(client, base, "limiter/status")
        command(
            client, base, "services/control", {"service": "xray", "action": "restart"}
        )
        after = command(client, base, "limiter/status")
        assert before["revision"] == after["revision"] and before["pid"] != after["pid"]
        with (
            proxy(work, args, node, clash, xray, ca) as socks,
            connect(socks, echo) as connection,
        ):
            assert transfer(connection) >= 0.65
        print(
            "PASS automatic activation, expiry and durable restart enforcement",
            flush=True,
        )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "rates.json").write_text(json.dumps(measurements, indent=2))
    browser_workflow(client, base, backend, args.output)


def run(args):
    def callback(work, fixture, wheel, stock, client, backend, echo):
        print(f"FIXTURE {fixture.root} {fixture.unit} {fixture.user}", flush=True)
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca, echo)

    service.exercise = callback
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_TRUSTED_AUTHORITIES"] = "[]"
    service.run(args.wheel, args.xray_archive)
    print("PASS native limiter end-to-end smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "mihomo", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    run(parser.parse_args())
