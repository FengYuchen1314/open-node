"""Exercise plan aliases, browser downloads and uninterrupted installed Xray access."""

import argparse
import base64
import importlib.util
import json
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from open_node.domain.subscriptions import SubscriptionPlanCreate
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "alias_accounts", Path(__file__).with_name("smoke-subscriber-account.py")
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


def capture_aliases(page, container, output, name):
    for width, height, suffix in (
        (1440, 1000, "desktop"),
        (390, 844, "mobile"),
        (320, 740, "narrow"),
    ):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(200)
        field = container.get_by_label("Subscription name", exact=True)
        field.scroll_into_view_if_needed()
        expect(field).to_be_in_viewport()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert container.locator(".plan-node-aliases").evaluate(
            "el => el.scrollWidth <= el.clientWidth + 1"
        )
        page.mouse.move(0, 0)
        page.screenshot(
            path=str(output / f"{name}-{suffix}.png"), animations="disabled"
        )
    page.set_viewport_size({"width": 1440, "height": 1000})


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
        json={"plan_id": other["id"], "queue_agent_commands": True},
    ).raise_for_status()
    accounts.users.wait_access(client, "bob")
    server_base = "/api/v1/servers/" + node["server_id"]
    plan_base = "/api/v1/plans/" + plan["id"]
    pid = native.command(client, server_base, "limiter/status")["pid"]

    def links(username):
        return (
            client.get("/api/v1/user-subscription-token", params={"username": username})
            .raise_for_status()
            .json()["subscription"]
        )

    original_links = {username: links(username) for username in ("alice", "bob")}

    def exported(username, target="xray"):
        return client.get(
            original_links[username]["subscription_url"], params={"format": target}
        ).raise_for_status()

    original = {username: exported(username).json() for username in original_links}
    original_nodes = client.get("/api/v1/nodes").json()
    credentials = client.get("/api/v1/users/alice/credentials").json()
    password = secrets.token_urlsafe(24)
    login = (
        client.get("/api/v1/subscriber-accounts", params={"username": "alice"})
        .raise_for_status()
        .json()
    )
    client.put(
        "/api/v1/subscriber-accounts",
        params={"username": "alice"},
        json={"expected_revision": login["revision"], "new_password": password},
    ).raise_for_status()

    def edit_api(**changes):
        read = client.get(plan_base + "/settings").raise_for_status().json()
        return (
            client.put(
                plan_base + "/settings",
                json={
                    **{
                        field: read["plan"][field]
                        for field in SubscriptionPlanCreate.model_fields
                    },
                    "expected_revision": read["revision"],
                    "acknowledge_runtime_restart": True,
                    **changes,
                },
            )
            .raise_for_status()
            .json()
        )

    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:

        def forward(config):
            with (
                servers.exported_client(work, args.xray, config) as socks,
                native.connect(socks, echo) as connection,
            ):
                native.transfer(connection, 4096)

        forward(original["alice"])
        browser = playwright.chromium.launch()
        admin = browser.new_context(viewport={"width": 1440, "height": 1000})
        subscriber = browser.new_context(
            viewport={"width": 1440, "height": 1000}, accept_downloads=True
        )
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
            page, portal = admin.new_page(), subscriber.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            portal.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(backend + "/subscriptions")
            page.get_by_role("button", name="Edit plan Community", exact=True).click()
            dialog = page.get_by_role("dialog")
            toggle = dialog.get_by_label("Custom subscription names", exact=True)
            field = dialog.get_by_role(
                "region", name=node["name"], exact=True
            ).get_by_label("Subscription name", exact=True)
            expect(field).to_be_disabled()
            toggle.check()
            field.fill("x" * 129)
            acknowledgment = dialog.get_by_label(
                "I accept the runtime restart and pending changes", exact=True
            )
            acknowledgment.check()
            expect(
                dialog.get_by_role("button", name="Save", exact=True)
            ).to_be_disabled()
            field.fill("Pending name")
            assert (
                edit_api(node_name_overrides={node["id"]: "Concurrent name"})[
                    "commands"
                ]
                == []
            )

            def save(expected=200):
                acknowledgment.check()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith(plan_base + "/settings")
                        and response.request.method == "PUT"
                    )
                ) as response:
                    dialog.get_by_role("button", name="Save", exact=True).click()
                assert response.value.status == expected, response.value.text()
                return response.value.json()

            save(409)
            dialog.get_by_role("button", name="Reload plan details").click()
            expect(field).to_have_value("Concurrent name")
            expect(toggle).not_to_be_checked()
            toggle.check()
            alias = "\u4e1c\u4eac / Community #1"
            field.fill(alias)
            capture_aliases(page, dialog, args.output, "plan-alias-edit")
            assert save()["commands"] == []
            expect(field).to_have_value(alias)
            capture_aliases(page, dialog, args.output, "plan-alias-saved")
            dialog.locator(".plan-actions").get_by_role(
                "button", name="Close", exact=True
            ).click()

            for target in ("clash", "sing-box", "xray", "uri-list", "base64"):
                response = exported("alice", target)
                if target == "clash":
                    content = yaml.safe_load(response.text)
                    assert content["proxies"][0]["name"] == alias
                    assert content["proxy-groups"][0]["proxies"] == [alias]
                elif target in {"sing-box", "xray"}:
                    content = response.json()
                    assert alias in [
                        outbound["tag"] for outbound in content["outbounds"]
                    ]
                    if target == "sing-box":
                        assert content["outbounds"][0]["outbounds"] == [alias]
                else:
                    raw = (
                        base64.b64decode(response.text).decode()
                        if target == "base64"
                        else response.text
                    )
                    assert unquote(urlsplit(raw.strip()).fragment) == alias
            accounts.sign_in(portal, backend, password)
            expect(
                portal.get_by_role("heading", name="Alice", exact=True)
            ).to_be_visible()
            portal.locator(".account-link-controls .v-select .v-field").click()
            portal.get_by_role("option", name="Xray", exact=True).click()
            with portal.expect_download() as download:
                portal.get_by_role(
                    "link", name="Download subscription", exact=True
                ).click()
            downloaded = json.loads(Path(download.value.path()).read_text())
            assert downloaded == exported("alice").json()
            assert downloaded != original["alice"]
            forward(downloaded)
            accounts.capture(portal, args.output, "alias-subscription-download")
            print(
                "PASS five formats, stale edit rejection and downloaded alias config forwarding",
                flush=True,
            )

            page.get_by_role("button", name="Edit plan Community", exact=True).click()
            expect(field).to_have_value(alias)
            toggle.uncheck()
            assert save()["commands"] == []
            assert exported("alice").json() == original["alice"]
            expect(field).to_have_value(alias)
            toggle.check()
            field.fill("")
            assert save()["commands"] == []
            assert (
                client.get(plan_base + "/settings").json()["plan"][
                    "node_name_overrides"
                ]
                == {}
            )
            dialog.locator(".plan-actions").get_by_role(
                "button", name="Close", exact=True
            ).click()

            page.get_by_role("tab", name="Plans", exact=True).click()
            form = page.locator("form").filter(
                has=page.get_by_role("button", name="Create plan", exact=True)
            )
            form.get_by_label("Name", exact=True).fill("Created with alias")
            form.get_by_role("combobox", name="Nodes", exact=True).press("Enter")
            page.get_by_role(
                "option", name=f"{node['name']} ({node['protocol']})", exact=True
            ).click()
            form.get_by_role("combobox", name="Nodes", exact=True).press("Escape")
            form.get_by_label("Custom subscription names", exact=True).check()
            form.get_by_label("Subscription name", exact=True).fill("New plan name")
            capture_aliases(page, form, args.output, "plan-alias-create")
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/api/v1/plans")
                    and response.request.method == "POST"
                )
            ) as created:
                form.get_by_role("button", name="Create plan", exact=True).click()
            assert created.value.status == 201, created.value.text()
            saved = created.value.json()["plan"]
            assert saved["node_name_overrides"] == {node["id"]: "New plan name"}
            assert saved["node_name_override_enabled"] is True
            expect(
                form.get_by_label("Custom subscription names", exact=True)
            ).not_to_be_checked()
            assert client.get("/api/v1/nodes").json() == original_nodes
            assert client.get("/api/v1/users/alice/credentials").json() == credentials
            assert {
                username: links(username) for username in original_links
            } == original_links
            assert exported("bob").json() == original["bob"]
            forward(original["alice"])
            forward(original["bob"])
            assert native.command(client, server_base, "limiter/status")["pid"] == pid
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not errors, errors
            print(
                "PASS create, toggle, clear, plan isolation and unchanged Xray/credentials/links",
                flush=True,
            )
        finally:
            admin.close()
            subscriber.close()
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
    print("PASS plan node aliases end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
