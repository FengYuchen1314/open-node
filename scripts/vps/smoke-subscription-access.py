"""Prove automatic subscription revocation and recovery with real proxy traffic."""

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "access_native", Path(__file__).with_name("smoke-native-limiter.py")
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)
runtime, service, lifecycle = native.runtime, native.service, native.lifecycle
ROOT = Path(__file__).resolve().parents[2]


def access(client, *, enabled, timeout=45):
    def read():
        response = (
            client.get("/api/v1/users/access-user/access").raise_for_status().json()
        )
        return response["servers"]

    return runtime.poll(
        "access confirmed on every node",
        read,
        ready=lambda rows: (
            rows
            and all(
                row["status"] == "applied"
                and all(entry["enabled"] is enabled for entry in row["entries"])
                for row in rows
            )
        ),
        timeout=timeout,
    )


def forwards(socks, target):
    try:
        with native.connect(socks, target) as connection:
            connection.settimeout(2)
            native.transfer(connection, 1024)
        return True
    except (OSError, AssertionError, EOFError):
        return False


def browser(client, backend, output):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
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
            page.get_by_role("combobox", name="Subscription user", exact=True).click()
            page.locator(
                ".ant-select-dropdown:visible .ant-select-item-option"
            ).get_by_text("access-user", exact=True).click()
            panel = page.get_by_label("Node access", exact=True)
            expect(panel.get_by_text("applied", exact=True)).to_be_visible(
                timeout=15000
            )
            for width, height, label in [
                (1440, 900, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]:
                page.set_viewport_size({"width": width, "height": height})
                panel.scroll_into_view_if_needed()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth + 1"
                )
                controls = panel.locator("button:visible")
                assert controls.count() > 0
                assert controls.evaluate_all(
                    "items => items.every(x => x.getBoundingClientRect().right <= innerWidth + 1)"
                )
                page.screenshot(path=str(output / (label + ".png")))
            page.set_viewport_size({"width": 1440, "height": 900})
            panel.get_by_label("Account enabled", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_text("Disable account?", exact=True)).to_be_visible()
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            expect(panel.get_by_label("Account enabled", exact=True)).to_be_checked()
            panel.get_by_label("Account enabled", exact=True).click()
            dialog.get_by_role("button", name="Disable", exact=True).click()
            access(client, enabled=False)
            expect(
                panel.get_by_text("Account disabled", exact=True).first
            ).to_be_visible(timeout=15000)
            panel.get_by_label("Account enabled", exact=True).click()
            access(client, enabled=True)
            expect(panel.get_by_text("Enabled", exact=True).first).to_be_visible(
                timeout=15000
            )
            panel.get_by_role(
                "button", name="Reconcile node access", exact=True
            ).click()
            expect(panel.get_by_text("applied", exact=True)).to_be_visible(
                timeout=15000
            )
            assert not errors, errors
            print(
                "PASS desktop/mobile/narrow access controls, cancellation and confirmed state",
                flush=True,
            )
        finally:
            context.close()
            browser.close()


def exercise(work, fixture, args, client, backend, endpoint, control_ca):
    config, ca, stats_port = native.clients.configuration(work)
    created = (
        client.post("/api/v1/servers", json={"name": "subscription-access"})
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
            "connection_mode": args.transport,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 1,
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
    runtime.poll("non-root Agent ready", fixture.ready)
    assert fixture.properties()["User"] != "root"
    native.command(client, base, "scan")
    nodes = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"]
    )
    assert len(nodes) == 18
    client.post("/api/v1/users", json={"username": "access-user"}).raise_for_status()
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Access",
                "traffic_limit_gb": 100,
                "node_ids": [node["id"] for node in nodes],
                "speed_limit_mbps": 4,
                "device_limit": 4,
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    assigned = (
        client.post(
            "/api/v1/users/access-user/plan",
            json={
                "plan_id": plan["id"],
                "queue_agent_commands": True,
            },
        )
        .raise_for_status()
        .json()
    )
    for command in assigned["commands"]:
        lifecycle.wait_command(client, base, command)
    access(client, enabled=True)
    token = (
        client.post("/api/v1/users/access-user/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    clash = yaml.safe_load(
        client.get(f"/api/v1/subscribe/{token}?format=clash").raise_for_status().text
    )
    xray = (
        client.get(f"/api/v1/subscribe/{token}?format=xray").raise_for_status().json()
    )
    credentials = {
        item["tag"]: item["client"]
        for item in assigned["provisioning_batches"][0]["body"]["inbound_clients"]
    }

    with native.echo_server(work) as (echo, _):
        for node in nodes:
            with native.proxy(work, args, node, clash, xray, ca) as socks:
                assert forwards(socks, echo), node["inbound_tag"]
        print(
            "PASS actual provisioned credentials on all 18 protocol variants",
            flush=True,
        )
        node = next(item for item in nodes if item["inbound_tag"] == "vless-vision")
        with (
            native.proxy(work, args, node, clash, xray, ca) as socks,
            native.connect(socks, echo) as existing,
        ):
            native.transfer(existing, 4096)
            client.patch(
                "/api/v1/users/access-user/active", json={"is_active": False}
            ).raise_for_status()
            access(client, enabled=False)
            existing.settimeout(2)
            try:
                native.transfer(existing, 1024)
            except (OSError, AssertionError, EOFError):
                pass
            else:
                raise AssertionError(
                    "Existing authenticated stream survived revocation"
                )
        for node in nodes:
            with native.proxy(work, args, node, clash, xray, ca) as socks:
                assert not forwards(socks, echo), node["inbound_tag"]
        assert all(
            len(item["settings"].get("clients", item["settings"].get("users", []))) == 1
            for item in json.loads((fixture.root / "config/xray.json").read_text())[
                "inbounds"
            ]
        )
        print(
            "PASS old credentials rejected, existing stream closed and original users preserved",
            flush=True,
        )
        client.patch(
            "/api/v1/users/access-user/active", json={"is_active": True}
        ).raise_for_status()
        access(client, enabled=True)

        # Remove only the fixture's original users, leaving the managed user as the last one.
        candidate = json.loads((fixture.root / "config/xray.json").read_text())
        for inbound in candidate["inbounds"]:
            key = (
                "users"
                if inbound["protocol"] in {"snell", "mieru", "anytls"}
                else "clients"
            )
            inbound["settings"][key] = [credentials[inbound["tag"]]]
        native.command(client, base, "xray/config/write", {"config": candidate})
        access(client, enabled=True)
        with sqlite3.connect(work / "backend.db") as db:
            db.execute(
                "UPDATE product_users SET plan_expires_at=? WHERE username=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "access-user"),
            )
        access(client, enabled=False)
        assert (
            json.loads((fixture.root / "config/xray.json").read_text())["inbounds"]
            == []
        )
        assert client.get(f"/api/v1/subscribe/{token}").status_code == 404
        subprocess.run(["systemctl", "restart", fixture.unit], check=True, timeout=30)
        runtime.poll("Agent restarted with suspended listeners", fixture.ready)
        client.post(
            "/api/v1/users/access-user/plan", json={"plan_id": plan["id"]}
        ).raise_for_status()
        access(client, enabled=True)
        for node in nodes:
            with native.proxy(work, args, node, clash, xray, ca) as socks:
                assert forwards(socks, echo), node["inbound_tag"]
        print(
            "PASS automatic expiry, empty listeners, restart and original-credential renewal",
            flush=True,
        )

        quota_plan = (
            client.post(
                "/api/v1/plans",
                json={
                    "name": "Quota",
                    "traffic_limit_gb": 0.002,
                    "node_ids": [item["id"] for item in nodes],
                    "traffic_mode": "twoway",
                    "is_reset": True,
                    "reset_day": datetime.now(UTC).day,
                },
            )
            .raise_for_status()
            .json()["plan"]
        )
        client.post(
            "/api/v1/users/access-user/traffic/reset", json={}
        ).raise_for_status()
        client.post(
            "/api/v1/users/access-user/plan", json={"plan_id": quota_plan["id"]}
        ).raise_for_status()
        access(client, enabled=True)
        with native.proxy(work, args, nodes[0], clash, xray, ca) as socks:
            with native.connect(socks, echo) as connection:
                native.transfer(connection, 2 * 1024 * 1024)
            access(client, enabled=False)
            assert not forwards(socks, echo)
            quota = (
                client.get("/api/v1/users/access-user/quota")
                .raise_for_status()
                .json()["quota"]
            )
            assert quota["over_quota"]
            with sqlite3.connect(work / "backend.db") as db:
                earlier = (datetime.now(UTC) - timedelta(days=40)).isoformat()
                db.execute(
                    "UPDATE product_users SET plan_started_at=?,last_traffic_reset_at=? "
                    "WHERE username=?",
                    (earlier, earlier, "access-user"),
                )
            access(client, enabled=True)
            assert forwards(socks, echo)
        print(
            "PASS actual traffic quota revocation and automatic monthly reset recovery",
            flush=True,
        )
        client.post(
            "/api/v1/users/access-user/plan", json={"plan_id": plan["id"]}
        ).raise_for_status()
        access(client, enabled=True)
        browser(client, backend, args.output)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    args.reference = args.xray

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_SUBSCRIPTION_ACCESS_POLL_SECONDS"] = "1"
    service.run(args.wheel, args.xray_archive)
    print("PASS automatic subscription access end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "mihomo", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
