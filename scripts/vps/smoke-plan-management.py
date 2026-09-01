"""Exercise plan edits, unassignment and deletion against installed Agent/Xray."""

import argparse
import importlib.util
import os
import signal
import sqlite3
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "plan_servers", Path(__file__).with_name("smoke-server-management.py")
)
servers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(servers)
native, runtime, service, lifecycle = (
    servers.native,
    servers.runtime,
    servers.service,
    servers.lifecycle,
)
ROOT = Path(__file__).resolve().parents[2]
FIELDS = (
    "name",
    "description",
    "traffic_limit_gb",
    "cycle_days",
    "is_reset",
    "reset_day",
    "node_ids",
    "node_multipliers",
    "node_speed_limits",
    "node_device_limits",
    "speed_limit_mbps",
    "device_limit",
    "traffic_mode",
)


def wait_access(client, username):
    return runtime.poll(
        "confirmed access " + username,
        lambda: (
            client.get(f"/api/v1/users/{username}/access").raise_for_status().json()
        ),
        ready=lambda state: (
            bool(state["servers"])
            and all(row["status"] == "applied" for row in state["servers"])
        ),
        timeout=60,
    )


def exercise(work, fixture, args, client, backend, endpoint, ca):
    created = (
        client.post(
            "/api/v1/servers", json={"name": "plan-agent", "domain": "127.0.0.1"}
        )
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    ports = [runtime.free_port(), runtime.free_port()]
    stats = runtime.free_port()
    agent, xray = work / "agent-input.json", work / "xray-input.json"
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
                    "tag": "node-" + str(index),
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "vless",
                    "settings": {"decryption": "none", "clients": []},
                }
                for index, port in enumerate(ports)
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
    runtime.poll("installed non-root Agent", fixture.ready)
    assert fixture.properties()["User"] != "root"
    native.command(client, base, "scan")
    nodes = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"]
    )
    nodes.sort(key=lambda node: node["inbound_tag"])
    assert len(nodes) == 2
    for name in ("alice", "bob", "carol"):
        client.post("/api/v1/users", json={"username": name}).raise_for_status()
    plans = []
    for name in ("Working plan", "Unrelated plan"):
        plans.append(
            client.post(
                "/api/v1/plans",
                json={
                    "name": name,
                    "traffic_limit_gb": 128,
                    "traffic_mode": "twoway",
                    "cycle_days": 30,
                    "is_reset": True,
                    "reset_day": 1,
                    "node_ids": [nodes[0]["id"]],
                },
            )
            .raise_for_status()
            .json()["plan"]
        )
    plan_base = "/api/v1/plans/" + plans[0]["id"]
    tokens = {}

    def assign(username, plan):
        response = (
            client.post(
                f"/api/v1/users/{username}/plan",
                json={
                    "plan_id": plan["id"],
                    "queue_agent_commands": True,
                },
            )
            .raise_for_status()
            .json()
        )
        for command in response["commands"]:
            lifecycle.wait_command(client, base, command)
        wait_access(client, username)

    for username in ("alice", "bob", "carol"):
        assign(username, plans[1 if username == "bob" else 0])
        tokens[username] = client.post(
            f"/api/v1/users/{username}/subscription-token"
        ).json()["subscription"]["token"]

    def exported(username):
        return (
            client.get("/api/v1/subscribe/" + tokens[username] + "?format=xray")
            .raise_for_status()
            .json()
        )

    def credentials(username):
        return (
            client.get(f"/api/v1/users/{username}/credentials")
            .raise_for_status()
            .json()["credentials"]
        )

    def traffic(username):
        return (
            client.get(f"/api/v1/users/{username}/traffic")
            .raise_for_status()
            .json()["total"]
        )

    def wait_idle():
        runtime.poll(
            "closed plan connections release their slots",
            lambda: (
                not any(
                    native.command(client, base, "limiter/status")[
                        "conn_counts"
                    ].values()
                )
            ),
            timeout=30,
        )

    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:

        def transfer(config, size=32768):
            with (
                servers.exported_client(work, args.xray, config) as socks,
                native.connect(socks, echo) as connection,
            ):
                return native.transfer(connection, size)

        def rejected(config):
            try:
                transfer(config, 1024)
            except (OSError, AssertionError, TimeoutError):
                return True
            return False

        old_alice, bob_config = exported("alice"), exported("bob")
        transfer(old_alice)
        runtime.poll("real user traffic", lambda: traffic("alice") >= 65536)
        before_users = client.get("/api/v1/users").json()["users"]
        old_credentials = credentials("alice")

        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
        try:
            context.add_cookies(
                [
                    {
                        "name": item.name,
                        "value": item.value,
                        "url": backend,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for item in client.cookies.jar
                ]
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(backend + "/subscriptions")
            expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
            page.get_by_role(
                "button", name="编辑套餐 Working plan", exact=True
            ).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_label("套餐名称", exact=True)).to_have_value(
                "Working plan"
            )
            acknowledgment = dialog.get_by_label(
                "我接受运行时重启及变更待确认的影响", exact=True
            )
            quota = dialog.get_by_label("流量配额（GiB）", exact=True)
            quota.fill("")
            quota.press_sequentially("-1")
            quota.press("Enter")
            expect(quota).to_have_value("-1")
            acknowledgment.check()
            expect(quota).to_have_value("-1")
            with page.expect_response(
                lambda response: (
                    response.url.endswith(plan_base + "/settings")
                    and response.request.method == "PUT"
                )
            ) as invalid:
                dialog.get_by_role("button", name="保存", exact=True).click()
            assert invalid.value.status == 422
            dialog.get_by_label("流量配额（GiB）", exact=True).fill("256")
            read = client.get(plan_base + "/settings").raise_for_status().json()
            client.put(
                plan_base + "/settings",
                json={
                    **{field: read["plan"][field] for field in FIELDS},
                    "description": "Concurrent edit",
                    "expected_revision": read["revision"],
                    "acknowledge_runtime_restart": True,
                },
            ).raise_for_status()
            with page.expect_response(
                lambda response: (
                    response.url.endswith(plan_base + "/settings")
                    and response.request.method == "PUT"
                )
            ) as stale:
                dialog.get_by_role("button", name="保存", exact=True).click()
            assert stale.value.status == 409
            dialog.get_by_role("button", name="重新加载套餐详情").click()
            expect(dialog.get_by_label("说明", exact=True)).to_have_value(
                "Concurrent edit"
            )
            dialog.get_by_label("套餐名称", exact=True).fill("Updated plan")
            dialog.get_by_label("流量配额（GiB）", exact=True).fill("256")
            dialog.get_by_label("新分配的有效期（天）", exact=True).fill("7")
            dialog.get_by_label("默认速度（Mbps）", exact=True).fill("2")
            dialog.get_by_label("默认连接数", exact=True).fill("3")
            dialog.get_by_role(
                "combobox", name="新分配的重置日（UTC）", exact=True
            ).click()
            dropdown = page.locator(".ant-select-dropdown:visible")
            dropdown.locator(".ant-select-dropdown-list-holder").evaluate(
                "element => { element.scrollTop = element.scrollHeight; }"
            )
            dropdown.locator(".ant-select-item-option").get_by_text(
                "25", exact=True
            ).click()
            selector = dialog.get_by_role("combobox", name="套餐节点", exact=True)
            selector.click()
            for node in nodes:
                page.locator(
                    ".ant-select-dropdown:visible .ant-select-item-option"
                ).get_by_text(node["name"], exact=True).click()
            selector.press("Escape")
            override = dialog.get_by_label(nodes[1]["name"], exact=True)
            expect(override).to_be_visible()
            override.get_by_label(nodes[1]["name"] + "：速度", exact=True).fill("0.5")
            override.get_by_label(nodes[1]["name"] + "：连接数", exact=True).fill(
                "1"
            )
            override.get_by_label(nodes[1]["name"] + "：计费倍率", exact=True).fill(
                "2"
            )

            def capture(label):
                for width, height, suffix in [
                    (1440, 1000, "desktop"),
                    (390, 844, "mobile"),
                    (320, 740, "narrow"),
                ]:
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(150)
                    box = dialog.bounding_box()
                    assert (
                        box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1
                    )
                    assert box["y"] >= 0 and box["y"] + box["height"] <= height + 1
                    content = dialog.locator(".ant-modal-body")
                    expect(content).to_have_count(1)
                    assert content.evaluate(
                        "el => el.scrollWidth <= el.clientWidth + 1"
                    ), (
                        suffix,
                        content.evaluate(
                            "el => ({scroll: el.scrollWidth, client: el.clientWidth})"
                        ),
                    )
                    content.evaluate("el => { el.scrollTop = 0; }")
                    page.screenshot(path=str(args.output / f"{label}-{suffix}.png"))
                    content.evaluate("el => { el.scrollTop = el.scrollHeight; }")
                    page.screenshot(
                        path=str(args.output / f"{label}-{suffix}-bottom.png")
                    )

            capture("edit")
            acknowledgment.check()
            with page.expect_response(
                lambda response: (
                    response.url.endswith(plan_base + "/settings")
                    and response.request.method == "PUT"
                )
            ) as saved:
                dialog.get_by_role("button", name="保存", exact=True).click()
            assert saved.value.status == 200, saved.value.text()
            for username in ("alice", "carol"):
                wait_access(client, username)
            expect(
                dialog.get_by_role("region", name="套餐部署状态").get_by_text(
                    "已应用", exact=True
                )
            ).to_have_count(2, timeout=15000)
            capture("saved")
            dialog.locator(".ant-modal-footer").get_by_role(
                "button", name="关 闭", exact=True
            ).click()
            for width, height, suffix in [
                (1440, 1000, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]:
                page.set_viewport_size({"width": width, "height": height})
                catalog = page.locator(".ant-card-small")
                assert catalog.count() > 0, "No catalog entries found"
                catalog.first.scroll_into_view_if_needed()
                page.wait_for_timeout(150)
                assert catalog.evaluate_all(
                    """items => items.every(item =>
                        item.scrollWidth <= item.clientWidth + 1 &&
                        item.firstElementChild.scrollWidth <=
                            item.firstElementChild.clientWidth + 1
                    )"""
                )
                page.screenshot(path=str(args.output / f"catalog-{suffix}.png"))
            assert client.get("/api/v1/users").json()["users"] == before_users
            updated = client.get(plan_base + "/settings").json()["plan"]
            assert updated["node_speed_limits"] == {nodes[1]["id"]: 0.5}
            assert updated["node_device_limits"] == {nodes[1]["id"]: 1}
            assert updated["node_multipliers"] == {nodes[1]["id"]: 2}
            assert old_credentials[0] in credentials("alice")
            assert rejected(old_alice)
            current_alice, carol_config = exported("alice"), exported("carol")
            elapsed = transfer(current_alice)
            assert 0.65 <= elapsed <= 8, elapsed
            wait_idle()
            with (
                servers.exported_client(work, args.xray, current_alice) as socks,
                native.connect(socks, echo) as first,
            ):
                native.transfer(first, 1024)
                denied = False
                try:
                    with native.connect(socks, echo) as second:
                        native.transfer(second, 1024)
                except (OSError, AssertionError, TimeoutError):
                    denied = True
                assert denied, "Plan connection override was not enforced"
                transfer(bob_config, 1024)
            wait_idle()
            print(
                "PASS browser guarded edits, membership provisioning, actual rate/connection "
                "overrides and unaffected subscriber",
                flush=True,
            )

            page.get_by_role(
                "button", name="取消 alice 的套餐分配", exact=True
            ).click()
            expect(dialog.get_by_label("确认用户名", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="取 消", exact=True).click()
            transfer(current_alice, 1024)
            page.get_by_role(
                "button", name="取消 alice 的套餐分配", exact=True
            ).click()
            dialog.get_by_label("确认用户名", exact=True).fill("alice")
            acknowledgment.check()
            before_traffic = traffic("alice")
            before_credentials = credentials("alice")
            with page.expect_response(
                lambda response: response.url.endswith("/users/alice/plan/remove")
            ) as unassigned:
                dialog.get_by_role("button", name="取消分配", exact=True).click()
            assert unassigned.value.status == 200, unassigned.value.text()
            wait_access(client, "alice")
            capture("unassigned")
            dialog.locator(".ant-modal-footer").get_by_role(
                "button", name="关 闭", exact=True
            ).click()
            assert rejected(current_alice)
            transfer(carol_config, 1024)
            transfer(bob_config, 1024)
            assert (
                credentials("alice") == before_credentials
                and traffic("alice") >= before_traffic
            )
            assert client.get("/api/v1/subscribe/" + tokens["alice"]).status_code == 404
            assign("alice", updated)
            assert credentials("alice") == before_credentials
            assert exported("alice") == current_alice
            transfer(current_alice, 1024)
            wait_idle()
            print(
                "PASS explicit unassign/cancel, actual revocation, "
                "preserved credentials/usage and reactivation",
                flush=True,
            )

            page.reload()
            page.get_by_role(
                "button", name="移除套餐 Updated plan", exact=True
            ).click()
            expect(dialog.get_by_label("确认套餐名称", exact=True)).to_be_visible()
            dialog.get_by_label("确认套餐名称", exact=True).fill("wrong")
            acknowledgment.check()
            expect(
                dialog.get_by_role("button", name="移除", exact=True)
            ).to_be_disabled()
            dialog.get_by_label("确认套餐名称", exact=True).fill("Updated plan")
            pid = int(fixture.properties()["MainPID"])
            os.kill(pid, signal.SIGSTOP)
            try:
                with page.expect_response(
                    lambda response: response.url.endswith(plan_base + "/remove")
                ) as removed:
                    dialog.get_by_role("button", name="移除", exact=True).click()
                assert removed.value.status == 200, removed.value.text()
                pending = client.get("/api/v1/users/alice/access").json()
                assert pending["servers"][0]["status"] == "pending"
                assert (
                    client.get("/api/v1/subscribe/" + tokens["alice"]).status_code
                    == 404
                )
                transfer(current_alice, 1024)
                capture("pending-removal")
            finally:
                os.kill(pid, signal.SIGCONT)
            for username in ("alice", "carol"):
                wait_access(client, username)
            expect(
                dialog.get_by_role("region", name="套餐部署状态").get_by_text(
                    "已应用", exact=True
                )
            ).to_have_count(2, timeout=15000)
            assert rejected(current_alice) and rejected(carol_config)
            transfer(bob_config, 1024)
            assert client.get("/api/v1/plans").json()["plans"] == [plans[1]]
            assert credentials("alice") == before_credentials
            assert (
                client.post("/api/v1/users/alice/subscription-token").json()[
                    "subscription"
                ]["token"]
                == tokens["alice"]
            )
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not errors, errors
            print(
                "PASS unavailable-Agent removal, visible pending state, later confirmed "
                "revocation and unrelated plan preservation",
                flush=True,
            )
        finally:
            context.close()
            browser.close()


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_TRUSTED_AUTHORITIES"] = "[]"
    service.run(args.wheel, args.xray_archive)
    print("PASS plan management end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
