"""Verify package automatic rules with real installed Xray and browser workflows."""

import argparse
import importlib.util
import json
import os
import sqlite3
import time
from pathlib import Path

from open_node.domain.subscriptions import SubscriptionPlanCreate
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "speed_accounts", Path(__file__).with_name("smoke-subscriber-account.py")
)
accounts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(accounts)
native, runtime, service, lifecycle, servers = (
    accounts.native,
    accounts.runtime,
    accounts.service,
    accounts.lifecycle,
    accounts.servers,
)
ROOT = Path(__file__).resolve().parents[2]
SUSTAINED = {
    "type": "sustained",
    "threshold_mbps": 0.01,
    "sustained_seconds": 1,
    "window_seconds": 300,
    "burst_count": 3,
    "limit_mbps": 0.5,
    "limit_duration": 15,
}
BURST = SUSTAINED | {
    "type": "burst",
    "window_seconds": 20,
    "burst_count": 2,
    "limit_duration": 8,
}


def capture(page, container, output, name):
    editor = container.get_by_role("region", name="Automatic limits", exact=True)
    for width, height, suffix in (
        (1440, 1000, "desktop"),
        (390, 844, "mobile"),
        (320, 740, "narrow"),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(200)
        editor.get_by_label(
            "Trigger Mbps", exact=True
        ).first.scroll_into_view_if_needed()
        expect(
            editor.get_by_label("Trigger Mbps", exact=True).first
        ).to_be_in_viewport()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert editor.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        assert editor.evaluate("""el => [...el.querySelectorAll('.v-input,.v-btn')].every(item => {
            const r = item.getBoundingClientRect();
            return !item.getClientRects().length || (r.left >= 0 && r.right <= innerWidth + 1);
        })""")
        page.mouse.move(0, 0)
        page.screenshot(
            path=str(output / f"{name}-{suffix}.png"), animations="disabled"
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


def configure(row, rule):
    row.get_by_role(
        "button", name="Burst" if rule["type"] == "burst" else "Sustained", exact=True
    ).click()
    fields = {
        "Trigger Mbps": "threshold_mbps",
        "Hold seconds": "sustained_seconds",
        "Cap Mbps": "limit_mbps",
        "Duration seconds": "limit_duration",
    }
    if rule["type"] == "burst":
        fields |= {"Window seconds": "window_seconds", "Bursts": "burst_count"}
    for label, key in fields.items():
        field = row.get_by_label(label, exact=True)
        field.fill("")
        field.press_sequentially(str(rule[key]), delay=20)
        expect(field).to_have_value(str(rule[key]))
        expect(field).to_be_focused()


def exercise(work, fixture, args, client, backend, endpoint, ca):
    plan = accounts.setup(work, fixture, args, client, endpoint, ca)
    node = client.get("/api/v1/nodes").raise_for_status().json()["nodes"][0]
    other = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Other plan",
                "traffic_limit_gb": 1,
                "node_ids": [node["id"]],
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    client.post(
        "/api/v1/users/bob/plan",
        json={
            "plan_id": other["id"],
            "queue_agent_commands": True,
        },
    ).raise_for_status()
    accounts.users.wait_access(client, "bob")
    server_base = "/api/v1/servers/" + node["server_id"]
    plan_base = "/api/v1/plans/" + plan["id"]

    def links(username):
        return (
            client.get("/api/v1/user-subscription-token", params={"username": username})
            .raise_for_status()
            .json()["subscription"]
        )

    original_links = {name: links(name) for name in ("alice", "bob")}

    def exported(username):
        return (
            client.get(
                original_links[username]["subscription_url"], params={"format": "xray"}
            )
            .raise_for_status()
            .json()
        )

    original = {name: exported(name) for name in original_links}
    credentials = {
        name: client.get(f"/api/v1/users/{name}/credentials").json()
        for name in original_links
    }
    emails = {
        name: value["credentials"][0]["email"] for name, value in credentials.items()
    }

    def status():
        return native.command(client, server_base, "limiter/status")

    def policy(state):
        return next(
            item for item in state["inbounds"] if item["inbound_tag"] == "subscribers"
        )

    def assert_rules(rules):
        state = status()
        users = {item["email"]: item for item in policy(state)["users"]}
        assert users[emails["alice"]].get("auto_speed_rules", []) == rules, users
        assert not users.get(emails["bob"], {}).get("auto_speed_rules"), users
        return state

    def edit_api(rules):
        read = client.get(plan_base + "/settings").raise_for_status().json()
        client.put(
            plan_base + "/settings",
            json={
                **{
                    field: read["plan"][field]
                    for field in SubscriptionPlanCreate.model_fields
                },
                "expected_revision": read["revision"],
                "acknowledge_runtime_restart": True,
                "auto_speed_rules": rules,
            },
        ).raise_for_status()
        accounts.users.wait_access(client, "alice")
        return assert_rules(rules)

    measurements = {}
    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        admin = browser.new_context(viewport={"width": 1440, "height": 1000})
        errors = []
        try:
            admin.add_cookies(
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
            page = admin.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(backend + "/subscriptions")
            page.get_by_role("button", name="Edit plan Community", exact=True).click()
            dialog = page.get_by_role("dialog")
            editor = dialog.get_by_role("region", name="Automatic limits", exact=True)
            editor.get_by_role("button", name="Add automatic rule", exact=True).click()
            first = editor.get_by_role("region", name="Automatic rule 1", exact=True)
            first.get_by_label("Hold seconds", exact=True).fill("0")
            acknowledgment = dialog.get_by_label(
                "I accept the runtime restart and pending changes", exact=True
            )
            acknowledgment.check()
            expect(
                dialog.get_by_role("button", name="Save", exact=True)
            ).to_be_disabled()
            configure(first, SUSTAINED)
            editor.get_by_role("button", name="Add automatic rule", exact=True).click()
            second = editor.get_by_role("region", name="Automatic rule 2", exact=True)
            configure(second, BURST)
            second.get_by_role("button", name="Move rule 2 up", exact=True).click()
            expect(first.get_by_label("Window seconds", exact=True)).to_have_value("20")
            first.get_by_role("button", name="Move rule 1 down", exact=True).click()
            expect(first.get_by_label("Duration seconds", exact=True)).to_have_value(
                "15"
            )
            capture(page, dialog, args.output, "plan-rules-edit")
            second.get_by_role(
                "button", name="Remove automatic rule 2", exact=True
            ).click()

            def save():
                acknowledgment.check()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith(plan_base + "/settings")
                        and response.request.method == "PUT"
                    )
                ) as response:
                    dialog.get_by_role("button", name="Save", exact=True).click()
                assert response.value.status == 200, response.value.text()
                accounts.users.wait_access(client, "alice")
                return response.value.json()["plan"]

            assert save()["auto_speed_rules"] == [SUSTAINED]
            before = assert_rules([SUSTAINED])
            dialog.locator(".plan-actions").get_by_role(
                "button", name="Close", exact=True
            ).click()

            # Save the inbound editor without losing the package rules it does not edit.
            limits = admin.new_page()
            limits.on("pageerror", lambda error: errors.append(str(error)))
            limits.goto(backend + "/config")
            limits.get_by_role("tab", name="Limits", exact=True).click()
            panel = limits.locator(".limiter-panel")
            expect(panel.get_by_label("Inbound", exact=True)).to_be_visible(
                timeout=20000
            )
            panel.get_by_label("Inbound", exact=True).press("Enter")
            limits.get_by_role("option", name="subscribers", exact=True).click()
            panel.get_by_role("button", name="Save limits", exact=True).click()
            expect(panel.get_by_text("Limits applied.", exact=True)).to_be_visible(
                timeout=20000
            )
            after = assert_rules([SUSTAINED])
            assert after["pid"] == before["pid"]
            limits.close()
            print(
                "PASS ordered editor, validation, sequential typing and native UI preservation",
                flush=True,
            )

            def stream(connection, seconds=3):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    native.transfer(connection, 16384)
                    time.sleep(0.02)

            def automatic_for(state, username):
                return next(
                    (
                        item
                        for item in state["automatic_limits"].values()
                        if item["email"] == emails[username]
                    ),
                    None,
                )

            with (
                servers.exported_client(
                    work, args.xray, original["alice"]
                ) as alice_socks,
                servers.exported_client(work, args.xray, original["bob"]) as bob_socks,
            ):
                with (
                    native.connect(alice_socks, echo) as alice,
                    native.connect(bob_socks, echo) as bob,
                ):
                    stream(alice)
                    stream(bob)
                    active = status()
                    assert automatic_for(active, "alice") and not automatic_for(
                        active, "bob"
                    ), active
                    measurements["sustained_alice"] = native.transfer(alice, 65536)
                    measurements["unlimited_bob"] = native.transfer(bob, 65536)
                    assert measurements["sustained_alice"] >= 1.5, measurements
                    assert measurements["unlimited_bob"] < 1, measurements
                    native.command(
                        client,
                        server_base,
                        "limiter",
                        {**policy(active), "expected_revision": active["revision"]},
                    )
                    unchanged = status()
                    assert (
                        automatic_for(unchanged, "alice")["until"]
                        == automatic_for(active, "alice")["until"]
                    )
                    runtime.poll(
                        "sustained rule expires",
                        lambda: not status()["automatic_limits"],
                        timeout=25,
                    )
                    measurements["expired_alice"] = native.transfer(alice, 65536)
                    assert measurements["expired_alice"] < 1, measurements
                print(
                    "PASS sustained real cap, independent package, hot preservation and expiry",
                    flush=True,
                )

                edit_api([BURST])
                with native.connect(alice_socks, echo) as alice:
                    for _ in range(2):
                        stream(alice)
                        time.sleep(2)
                    active = status()
                    assert automatic_for(active, "alice"), active
                    measurements["burst_alice"] = native.transfer(alice, 65536)
                    assert measurements["burst_alice"] >= 1.5, measurements
                    runtime.poll(
                        "burst rule expires",
                        lambda: not status()["automatic_limits"],
                        timeout=15,
                    )
                before = assert_rules([BURST])
                native.command(
                    client,
                    server_base,
                    "services/control",
                    {"service": "xray", "action": "restart"},
                )
                after = assert_rules([BURST])
                assert (
                    before["revision"] == after["revision"]
                    and before["pid"] != after["pid"]
                )
                print(
                    "PASS real burst activation, expiry and durable restart policy",
                    flush=True,
                )

                page.reload()
                page.get_by_role(
                    "button", name="Edit plan Community", exact=True
                ).click()
                expect(first.get_by_label("Window seconds", exact=True)).to_have_value(
                    "20"
                )
                capture(page, dialog, args.output, "plan-rules-saved")
                first.get_by_role(
                    "button", name="Remove automatic rule 1", exact=True
                ).click()
                assert save()["auto_speed_rules"] == []
                assert_rules([])
                dialog.locator(".plan-actions").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                with native.connect(alice_socks, echo) as alice:
                    for _ in range(2):
                        stream(alice)
                        time.sleep(2)
                    assert not status()["automatic_limits"]
                    assert native.transfer(alice, 65536) < 1

            page.get_by_role("tab", name="Plans", exact=True).click()
            form = page.locator("form").filter(
                has=page.get_by_role("button", name="Create plan", exact=True)
            )
            form.get_by_label("Name", exact=True).fill("Created with rules")
            form.get_by_role("combobox", name="Nodes", exact=True).press("Enter")
            page.get_by_role(
                "option", name=f"{node['name']} ({node['protocol']})", exact=True
            ).click()
            form.get_by_role("combobox", name="Nodes", exact=True).press("Escape")
            form.get_by_role("button", name="Add automatic rule", exact=True).click()
            configure(
                form.get_by_role("region", name="Automatic rule 1", exact=True), BURST
            )
            capture(page, form, args.output, "plan-rules-create")
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/api/v1/plans")
                    and response.request.method == "POST"
                )
            ) as created:
                form.get_by_role("button", name="Create plan", exact=True).click()
            assert created.value.status == 201, created.value.text()
            assert created.value.json()["plan"]["auto_speed_rules"] == [BURST]
            expect(form.get_by_text("No automatic rules", exact=True)).to_be_visible()
            assert {name: links(name) for name in original_links} == original_links
            assert {name: exported(name) for name in original_links} == original
            assert {
                name: client.get(f"/api/v1/users/{name}/credentials").json()
                for name in original_links
            } == credentials
            with sqlite3.connect(work / "backend.db") as db:
                assert not db.execute("PRAGMA foreign_key_check").fetchall()
            assert not errors, errors
            (args.output / "rates.json").write_text(json.dumps(measurements, indent=2))
            print(
                "PASS create, clear, unchanged credentials/exports/tokens and clean browser",
                flush=True,
            )
        finally:
            admin.close()
            browser.close()


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_SUBSCRIBER_TOTP_KEY"] = (
        accounts.Fernet.generate_key().decode()
    )

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    service.run(args.wheel, args.xray_archive)
    print("PASS plan speed rules end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
