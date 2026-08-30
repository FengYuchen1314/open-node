"""Exercise user profile changes and durable removal with real Agent/Xray traffic."""

import argparse
import importlib.util
import os
import signal
import sqlite3
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "user_servers", Path(__file__).with_name("smoke-server-management.py")
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


def wait_access(client, username):
    return runtime.poll(
        "confirmed user access " + username,
        lambda: (
            client.get(f"/api/v1/users/{username}/access").raise_for_status().json()
        ),
        ready=lambda value: (
            bool(value["servers"])
            and all(row["status"] == "applied" for row in value["servers"])
        ),
        timeout=60,
    )


def exercise(work, fixture, args, client, backend, endpoint, ca):
    created = (
        client.post(
            "/api/v1/servers", json={"name": "user-agent", "domain": "127.0.0.1"}
        )
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    port, stats = runtime.free_port(), runtime.free_port()
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
                    "tag": "users",
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
    runtime.poll("installed non-root user Agent", fixture.ready)
    assert fixture.properties()["User"] != "root"
    native.command(client, base, "scan")
    nodes = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"]
    )
    assert len(nodes) == 1
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Shared plan",
                "traffic_limit_gb": 128,
                "cycle_days": 30,
                "node_ids": [nodes[0]["id"]],
            },
        )
        .raise_for_status()
        .json()["plan"]
    )

    def assign(username):
        client.post(
            f"/api/v1/users/{username}/plan",
            json={
                "plan_id": plan["id"],
                "queue_agent_commands": True,
            },
        ).raise_for_status()
        wait_access(client, username)

    for username in ("alice", "bob"):
        client.post(
            "/api/v1/users",
            json={"username": username, "display_name": username.title()},
        ).raise_for_status()
        assign(username)
    client.post(
        "/api/v1/users", json={"username": "catalog-admin", "role": "admin"}
    ).raise_for_status()

    def subscription(username):
        return (
            client.post(f"/api/v1/users/{username}/subscription-token")
            .raise_for_status()
            .json()["subscription"]["token"]
        )

    def exported(token):
        return (
            client.get("/api/v1/subscribe/" + token + "?format=xray")
            .raise_for_status()
            .json()
        )

    def credentials():
        return (
            client.get("/api/v1/users/alice/credentials")
            .raise_for_status()
            .json()["credentials"]
        )

    old_token, bob_token = subscription("alice"), subscription("bob")
    original, bob_config = exported(old_token), exported(bob_token)
    before_credentials = credentials()
    before_user = client.get("/api/v1/users/alice/settings").json()["user"]
    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:

        def transfer(config, size=4096):
            with (
                servers.exported_client(work, args.xray, config) as socks,
                native.connect(socks, echo) as connection,
            ):
                return native.transfer(connection, size)

        def rejected(config):
            try:
                transfer(config)
            except (OSError, AssertionError, TimeoutError):
                return True
            return False

        transfer(original, 32768)
        runtime.poll(
            "charged real user traffic",
            lambda: client.get("/api/v1/users/alice/traffic").json()["total"] >= 65536,
        )
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
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
            expect(
                page.get_by_role("button", name="Remove user catalog-admin", exact=True)
            ).to_be_disabled()
            page.get_by_role("button", name="Edit user alice", exact=True).click()
            dialog = page.get_by_role("dialog")
            name = dialog.get_by_label("Display name", exact=True)
            expect(name).to_have_value("Alice")
            acknowledgment = dialog.get_by_label(
                "I accept runtime restarts and pending changes", exact=True
            )
            name.fill("")
            acknowledgment.check()
            expect(
                dialog.get_by_role("button", name="Save", exact=True)
            ).to_be_disabled()
            name.fill("Alice")
            read = client.get("/api/v1/users/alice/settings").json()
            client.put(
                "/api/v1/users/alice/settings",
                json={
                    **{
                        key: read["user"][key]
                        for key in ("display_name", "email", "remark", "is_active")
                    },
                    "remark": "Concurrent note",
                    "expected_revision": read["revision"],
                    "acknowledge_runtime_restart": True,
                },
            ).raise_for_status()
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/users/alice/settings")
                    and response.request.method == "PUT"
                )
            ) as stale:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert stale.value.status == 409
            dialog.get_by_role("button", name="Reload user details", exact=True).click()
            expect(dialog.get_by_label("Remark", exact=True)).to_have_value(
                "Concurrent note"
            )
            name.fill("Alice - regional operations")
            dialog.get_by_label("Email", exact=True).fill("alice@example.test")
            dialog.get_by_label("Remark", exact=True).fill(
                "Shared plan subscriber\nProfile update keeps runtime identity."
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
                    response.url.endswith("/users/alice/settings")
                    and response.request.method == "PUT"
                )
            ) as saved:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert saved.value.status == 200, saved.value.text()
            expect(dialog.get_by_text("User saved", exact=True)).to_be_visible()
            assert (
                credentials() == before_credentials
                and subscription("alice") == old_token
            )
            updated = client.get("/api/v1/users/alice/settings").json()["user"]
            for key in (
                "username",
                "role",
                "plan_started_at",
                "plan_expires_at",
                "current_plan_id",
            ):
                assert updated[key] == before_user[key]
            assert exported(old_token) == original
            transfer(original)
            print(
                "PASS browser profile revision guards and unchanged real runtime identity",
                flush=True,
            )

            dialog.get_by_label("Active", exact=True).uncheck()
            acknowledgment.check()
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/users/alice/settings")
                    and response.request.method == "PUT"
                )
            ) as disabled:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert disabled.value.status == 200, disabled.value.text()
            wait_access(client, "alice")
            runtime.poll(
                "disabled user subscription unavailable",
                lambda: client.get("/api/v1/subscribe/" + old_token).status_code == 404,
            )
            assert rejected(original)
            transfer(bob_config)
            capture("disabled")
            dialog.get_by_label("Active", exact=True).check()
            acknowledgment.check()
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/users/alice/settings")
                    and response.request.method == "PUT"
                )
            ) as enabled:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert enabled.value.status == 200, enabled.value.text()
            wait_access(client, "alice")
            assert (
                credentials() == before_credentials and exported(old_token) == original
            )
            transfer(original)
            dialog.locator(".ant-modal-footer").get_by_role(
                "button", name="Close", exact=True
            ).click()
            print(
                "PASS actual disable/reactivation and unaffected shared-plan subscriber",
                flush=True,
            )

            page.get_by_role("button", name="Remove user alice", exact=True).click()
            expect(dialog.get_by_label("Confirm username", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            transfer(original)
            page.get_by_role("button", name="Remove user alice", exact=True).click()
            dialog.get_by_label("Confirm username", exact=True).fill("wrong")
            acknowledgment.check()
            expect(
                dialog.get_by_role("button", name="Remove", exact=True)
            ).to_be_disabled()
            dialog.get_by_label("Confirm username", exact=True).fill("alice")
            capture("confirm-removal")
            pid = int(fixture.properties()["MainPID"])
            os.kill(pid, signal.SIGSTOP)
            try:
                with page.expect_response(
                    lambda response: response.url.endswith("/users/alice/remove")
                ) as removed:
                    dialog.get_by_role("button", name="Remove", exact=True).click()
                assert removed.value.status == 202, removed.value.text()
                removal = removed.value.json()
                assert removal["status"] == "pending"
                assert client.get("/api/v1/subscribe/" + old_token).status_code == 404
                assert (
                    client.post("/api/v1/users", json={"username": "alice"}).status_code
                    == 409
                )
                transfer(original)
                capture("pending-removal")
                dialog.locator(".ant-modal-footer").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                page.get_by_role(
                    "button", name="View removal for alice", exact=True
                ).click()
                expect(
                    dialog.get_by_text("Removal pending Agent confirmation", exact=True)
                ).to_be_visible()
            finally:
                os.kill(pid, signal.SIGCONT)
            runtime.poll(
                "completed durable user removal",
                lambda: (
                    client.get("/api/v1/user-removals/" + removal["id"])
                    .raise_for_status()
                    .json()
                ),
                ready=lambda value: value["status"] == "completed",
                timeout=90,
            )
            expect(dialog.get_by_text("User removed", exact=True)).to_be_visible(
                timeout=15000
            )
            expect(
                dialog.get_by_role("button", name="Reload user details", exact=True)
            ).to_be_disabled()
            expect(
                dialog.get_by_role(
                    "button", name="Retry user synchronization", exact=True
                )
            ).to_be_disabled()
            capture("removed")
            assert rejected(original)
            transfer(bob_config)
            assert client.get("/api/v1/users/alice/settings").status_code == 404
            assert len(client.get("/api/v1/plans").json()["plans"]) == 1
            assert len(client.get("/api/v1/nodes").json()["nodes"]) == 1
            dialog.locator(".ant-modal-footer").get_by_role(
                "button", name="Close", exact=True
            ).click()
            expect(
                page.get_by_role("button", name="View removal for alice", exact=True)
            ).to_have_count(0)
            print(
                "PASS unavailable-Agent removal, visible pending status, confirmed traffic withdrawal and unrelated data",
                flush=True,
            )

            client.post(
                "/api/v1/users", json={"username": "alice", "display_name": "New Alice"}
            ).raise_for_status()
            assign("alice")
            fresh_credentials = credentials()
            assert fresh_credentials[0]["email"] != before_credentials[0]["email"]
            assert (
                fresh_credentials[0]["credential"]
                != before_credentials[0]["credential"]
            )
            assert client.get("/api/v1/users/alice/traffic").json()["total"] == 0
            new_token = subscription("alice")
            assert new_token != old_token
            transfer(exported(new_token))
            assert (
                rejected(original)
                and client.get("/api/v1/subscribe/" + old_token).status_code == 404
            )
            page.reload()
            expect(
                page.get_by_role("button", name="Edit user alice", exact=True)
            ).to_be_visible()
            for width, height, suffix in [
                (1440, 1000, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(350)
                page.get_by_role(
                    "button", name="Edit user alice", exact=True
                ).scroll_into_view_if_needed()
                cards = page.locator(".ant-card-small")
                assert cards.count() > 0
                assert cards.evaluate_all(
                    "items => items.every(item => item.scrollWidth <= item.clientWidth + 1 && item.firstElementChild.scrollWidth <= item.firstElementChild.clientWidth + 1)"
                )
                page.screenshot(path=str(args.output / f"catalog-{suffix}.png"))
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not errors, errors
            print(
                "PASS same-name recreation with fresh credentials, isolated traffic and permanently dead old subscription",
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
    service.run(args.wheel, args.xray_archive)
    print("PASS user management end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
