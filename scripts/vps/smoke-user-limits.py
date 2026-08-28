"""Verify subscriber overrides with real forwarding, native limits and browser edits."""

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import signal
import sqlite3
import subprocess
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "limit_accounts", Path(__file__).with_name("smoke-subscriber-account.py")
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


def exercise(work, fixture, args, client, backend, endpoint, ca):
    plan = accounts.setup(work, fixture, args, client, endpoint, ca)
    node = client.get("/api/v1/nodes").raise_for_status().json()["nodes"][0]
    plan_view = (
        client.get(f"/api/v1/plans/{plan['id']}/settings").raise_for_status().json()
    )
    values = {
        key: value
        for key, value in plan_view["plan"].items()
        if key not in {"id", "created_at", "updated_at", "traffic_limit_bytes"}
    }
    client.put(
        f"/api/v1/plans/{plan['id']}/settings",
        json={
            **values,
            "speed_limit_mbps": 4,
            "device_limit": 3,
            "expected_revision": plan_view["revision"],
            "acknowledge_runtime_restart": True,
        },
    ).raise_for_status()
    for username in ("alice", "bob"):
        accounts.users.wait_access(client, username)
    before = (
        client.get("/api/v1/users/alice/settings").raise_for_status().json()["user"]
    )
    credentials = (
        client.get("/api/v1/users/alice/credentials").raise_for_status().json()
    )
    links = {
        username: client.post(f"/api/v1/users/{username}/subscription-token")
        .raise_for_status()
        .json()["subscription"]
        for username in ("alice", "bob")
    }
    configs = {
        username: client.get(value["subscription_url"], params={"format": "xray"})
        .raise_for_status()
        .json()
        for username, value in links.items()
    }
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

    def api_save(**changes):
        current = client.get("/api/v1/users/alice/settings").raise_for_status().json()
        fields = {
            key: current["user"][key]
            for key in ("display_name", "email", "remark", "is_active")
        }
        overrides = {**current["user"]["limit_overrides"], **changes}
        return (
            client.put(
                "/api/v1/users/alice/settings",
                json={
                    **fields,
                    "limit_overrides": overrides,
                    "expected_revision": current["revision"],
                    "acknowledge_runtime_restart": True,
                },
            )
            .raise_for_status()
            .json()
        )

    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:

        def transfer(username="alice", size=65536):
            with (
                servers.exported_client(work, args.xray, configs[username]) as socks,
                native.connect(socks, echo) as connection,
            ):
                return native.transfer(connection, size)

        def rejected(username="alice"):
            try:
                transfer(username, 128)
            except (OSError, AssertionError, TimeoutError):
                return True
            return False

        assert transfer("bob") < 1
        browser = playwright.chromium.launch()
        admin_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        portal_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        errors, requests = [], []
        try:
            admin_context.add_cookies(
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
            page, portal = admin_context.new_page(), portal_context.new_page()
            for target in (page, portal):
                target.on("pageerror", lambda error: errors.append(str(error)))
                target.on("request", lambda request: requests.append(request.url))
            page.goto(backend + "/subscriptions")
            page.get_by_role("button", name="Edit user alice", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_role("tab", name="Limits", exact=True).click()

            def mode(label, choice, value=None):
                dialog.locator(".v-select").filter(
                    has=page.get_by_label(label + " mode", exact=True)
                ).locator(".v-field").click()
                page.get_by_role("option", name=choice, exact=True).click()
                if value is not None:
                    units = {
                        "Traffic quota": " (GiB)",
                        "Speed limit": " (Mbps)",
                        "Node speed": " (Mbps)",
                    }
                    dialog.get_by_label(label + units.get(label, ""), exact=True).fill(
                        str(value)
                    )

            def save(expected=200):
                dialog.get_by_label(
                    "I accept runtime restarts and pending changes", exact=True
                ).check()
                with page.expect_response(
                    lambda response: (
                        response.url.endswith("/users/alice/settings")
                        and response.request.method == "PUT"
                    )
                ) as response:
                    dialog.get_by_role("button", name="Save", exact=True).click()
                assert response.value.status == expected, response.value.text()
                if expected == 200:
                    expect(dialog.get_by_text("User saved", exact=True)).to_be_visible()
                    accounts.users.wait_access(client, "alice")

            mode("Speed limit", "Custom", "0.5")
            api_save()
            save(409)
            expect(
                dialog.get_by_text(
                    "User or credentials changed; reload before saving", exact=True
                )
            ).to_be_visible()
            dialog.get_by_role("button", name="Reload user details", exact=True).click()
            dialog.get_by_role("tab", name="Limits", exact=True).click()
            mode("Speed limit", "Custom", "0.5")
            mode("Connection limit", "Custom", "1")
            dialog.get_by_label("Speed limit (Mbps)", exact=True).fill("")
            dialog.get_by_label(
                "I accept runtime restarts and pending changes", exact=True
            ).check()
            expect(
                dialog.get_by_role("button", name="Save", exact=True)
            ).to_be_disabled()
            dialog.get_by_label("Speed limit (Mbps)", exact=True).fill("0.5")
            accounts.capture(page, args.output, "limits-edit")
            save()
            limited = transfer()
            assert limited >= 1.5, limited
            group = (
                "account-"
                + hashlib.sha256(
                    json.dumps(
                        ["alice", node["server_id"], node["inbound_tag"]],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
            )
            runtime.poll(
                "previous connection releases admission slot",
                lambda: (
                    native.command(
                        client, "/api/v1/servers/" + node["server_id"], "limiter/status"
                    )["conn_counts"].get(group, 0)
                    == 0
                ),
                timeout=45,
            )
            with (
                servers.exported_client(work, args.xray, configs["alice"]) as socks,
                native.connect(socks, echo) as connection,
            ):
                native.transfer(connection, 128)
                denied = False
                try:
                    with native.connect(socks, echo) as second:
                        native.transfer(second, 128)
                except (OSError, AssertionError):
                    denied = True
                assert denied, "A second connection bypassed the user cap"
                native.transfer(connection, 128)
            assert transfer("bob") < 1
            print(
                "PASS user speed and connection limits with independent Bob access",
                limited,
                flush=True,
            )

            dialog.get_by_label("Node", exact=True).fill(node["name"])
            page.get_by_role("option", name=node["name"], exact=True).click()
            dialog.get_by_role("button", name="Add node override", exact=True).click()
            mode("Node speed", "Unlimited")
            mode("Node connections", "Unlimited")
            save()
            unlimited = transfer()
            assert unlimited < 1 and limited > unlimited * 2, (limited, unlimited)
            with (
                servers.exported_client(work, args.xray, configs["alice"]) as socks,
                native.connect(socks, echo) as first,
                native.connect(socks, echo) as second,
            ):
                native.transfer(first, 128)
                native.transfer(second, 128)
            accounts.capture(page, args.output, "node-overrides")
            accounts.sign_in(portal, backend, password)
            expect(
                portal.get_by_role("heading", name="Alice", exact=True)
            ).to_be_visible()
            expect(
                portal.get_by_role("region", name="Node limits").get_by_text(
                    "Unlimited speed", exact=True
                )
            ).to_be_visible()
            accounts.capture(portal, args.output, "subscriber-limits")
            print(
                "PASS explicit zero node overrides and own subscriber display",
                unlimited,
                flush=True,
            )

            mode("Node speed", "Custom", "0.5")
            save()
            subprocess.run(
                ["systemctl", "restart", fixture.unit], check=True, timeout=30
            )
            runtime.poll(
                "Agent reconnects with persisted limits", fixture.ready, timeout=60
            )
            accounts.users.wait_access(client, "alice")
            assert transfer() >= 1.5
            print("PASS native limits persist across Agent restart", flush=True)
            dialog.get_by_role(
                "button", name="Remove override " + node["name"], exact=True
            ).click()
            mode("Speed limit", "Inherit")
            mode("Connection limit", "Inherit")
            save()
            assert transfer() < 1
            stored = (
                client.get("/api/v1/users/alice/settings").raise_for_status().json()
            )
            assert stored["limits"]["nodes"][0]["speed_limit_mbps"] == 4
            assert stored["user"]["limit_overrides"]["speed_limit_mbps"] is None
            assert not stored["user"]["limit_overrides"]["node_speed_limits"]
            print("PASS clearing overrides restores plan limits", flush=True)

            used = (
                client.get("/api/v1/users/alice/quota")
                .raise_for_status()
                .json()["quota"]["charged_usage_bytes"]
            )
            assert used > 0
            pid = int(fixture.properties()["MainPID"])
            os.kill(pid, signal.SIGSTOP)
            try:
                saved = api_save(traffic_limit_gb=1 / 1024**3)
                assert saved["access"]["servers"][0]["status"] == "pending"
                assert client.get(links["alice"]["subscription_url"]).status_code == 404
                transfer(size=4096)
                portal.get_by_role("button", name="Refresh account", exact=True).click()
                expect(
                    portal.get_by_role("region", name="Current plan").get_by_text(
                        "Quota reached", exact=True
                    )
                ).to_be_visible()
                portal.get_by_role("heading", name="Alice", exact=True).click()
                accounts.capture(portal, args.output, "quota-exhausted")
            finally:
                os.kill(pid, signal.SIGCONT)
            accounts.users.wait_access(client, "alice")
            assert rejected()
            assert transfer("bob") < 1
            saved = api_save(traffic_limit_gb=0)
            accounts.users.wait_access(client, "alice")
            assert client.get(links["alice"]["subscription_url"]).status_code == 200
            assert transfer() < 1
            quota = (
                client.get("/api/v1/users/alice/quota")
                .raise_for_status()
                .json()["quota"]
            )
            assert (
                quota["traffic_limit_bytes"] == 0
                and quota["charged_usage_bytes"] >= used
            )
            print(
                "PASS offline quota withdrawal, reconnection and unlimited restoration without resetting usage",
                flush=True,
            )

            assert (
                client.get("/api/v1/users/alice/credentials").raise_for_status().json()
                == credentials
            )
            assert (
                client.get(
                    links["alice"]["subscription_url"], params={"format": "xray"}
                )
                .raise_for_status()
                .json()
                == configs["alice"]
            )
            after = (
                client.get("/api/v1/users/alice/settings")
                .raise_for_status()
                .json()["user"]
            )
            assert all(
                before[key] == after[key]
                for key in (
                    "current_plan_id",
                    "plan_started_at",
                    "plan_expires_at",
                    "reset_day",
                    "created_at",
                )
            )
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not errors, errors
            assert all(url.startswith((backend, "data:")) for url in requests), (
                "Unexpected external browser request"
            )
        finally:
            admin_context.close()
            portal_context.close()
            browser.close()


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    service.run(args.wheel, args.xray_archive)
    print("PASS user limits end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
