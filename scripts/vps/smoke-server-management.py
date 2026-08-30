"""Verify server profile edits/removal with installed Agent, VLESS and browser UI."""

import argparse
import importlib.util
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "management_native", Path(__file__).with_name("smoke-native-limiter.py")
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)
runtime, service, lifecycle = native.runtime, native.service, native.lifecycle
ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("name", "ip_address", "ip_address_v6", "domain", "domain_v6", "ipv6_enabled")


@contextmanager
def exported_client(work, xray, exported):
    port = runtime.free_port()
    name = "subscription-client-" + uuid4().hex[:8]
    path = work / (name + ".json")
    runtime.write_private(
        path,
        {
            **exported,
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "socks",
                    "settings": {"auth": "noauth"},
                }
            ],
        },
    )
    with runtime.process(work, name, [str(xray), "run", "-config", str(path)]):
        runtime.poll("exported client starts", lambda: runtime.port_open(port))
        yield port


def browser(client, backend, base, output, quota, before_remove):
    with sync_playwright() as playwright:
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
            page.goto(backend + "/")
            page.get_by_role("button", name="Edit management", exact=True).click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_label("Server name", exact=True)).to_have_value(
                "management"
            )
            dialog.get_by_label("Domain", exact=True).fill("https://localhost")
            with page.expect_response(
                lambda r: (
                    r.url.endswith(base + "/settings") and r.request.method == "PUT"
                )
            ) as invalid:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert invalid.value.status == 422
            expect(dialog.get_by_role("alert")).to_contain_text("hostname")

            # A second administrator changes the same profile while the dialog is open.
            current = client.get(base + "/settings").raise_for_status().json()
            client.put(
                base + "/settings",
                json={
                    **{key: current["server"][key] for key in FIELDS},
                    "expected_revision": current["revision"],
                    "name": "management-concurrent",
                },
            ).raise_for_status()
            dialog.get_by_label("Domain", exact=True).fill("localhost")
            with page.expect_response(
                lambda r: (
                    r.url.endswith(base + "/settings") and r.request.method == "PUT"
                )
            ) as stale:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert stale.value.status == 409
            expect(dialog.get_by_role("alert")).to_contain_text("refresh")
            dialog.get_by_role("button", name="Reload server details").click()
            expect(dialog.get_by_label("Server name", exact=True)).to_have_value(
                "management-concurrent"
            )
            dialog.get_by_label("Server name", exact=True).fill("management-edited")
            dialog.get_by_label("Domain", exact=True).fill("localhost")

            def capture(label, width, height):
                page.set_viewport_size({"width": width, "height": height})
                expect(dialog).to_be_visible()
                page.wait_for_timeout(150)
                container = dialog.locator(".ant-modal-container")
                expect(container).to_have_count(1)
                box = container.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, (
                    f"{label}: modal must fit viewport width {width}: {box}"
                )
                assert box["y"] >= 0 and box["y"] + box["height"] <= height + 1, (
                    f"{label}: modal must fit viewport height {height}: {box}"
                )
                content = dialog.locator(".ant-modal-body")
                expect(content).to_have_count(1)
                assert content.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                buttons = dialog.locator(".ant-modal-footer button")
                expect(buttons).to_have_count(2)
                for button in buttons.all():
                    bounds = button.bounding_box()
                    assert (
                        bounds
                        and bounds["x"] >= 0
                        and bounds["x"] + bounds["width"] <= width + 1
                    )
                page.screenshot(path=str(output / (label + ".png")))

            sizes = [
                (1440, 1000, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]
            for width, height, label in sizes:
                capture("edit-" + label, width, height)
            with page.expect_response(
                lambda r: (
                    r.url.endswith(base + "/settings") and r.request.method == "PUT"
                )
            ) as saved:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert saved.value.status == 200
            expect(dialog).not_to_be_visible()
            before_remove()

            page.get_by_role(
                "button", name="Remove management-edited", exact=True
            ).click()
            dialog = page.get_by_role("dialog")
            expect(
                dialog.get_by_label("Confirm server name", exact=True)
            ).to_be_visible()
            expect(
                dialog.get_by_role("button", name="Remove", exact=True)
            ).to_be_disabled()
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            assert client.get(base + "/settings").status_code == 200
            page.get_by_role(
                "button", name="Remove management-edited", exact=True
            ).click()
            expect(
                dialog.get_by_label("Confirm server name", exact=True)
            ).to_be_visible()
            dialog.get_by_label("Confirm server name", exact=True).fill("wrong")
            dialog.get_by_label(
                "I accept that remote services may keep running"
            ).check()
            expect(
                dialog.get_by_role("button", name="Remove", exact=True)
            ).to_be_disabled()
            dialog.get_by_label("Confirm server name", exact=True).fill(
                "management-edited"
            )
            for width, height, label in sizes:
                capture("remove-" + label, width, height)
            used = quota()
            retained_usage = (
                client.get("/api/v1/users/subscriber/traffic").raise_for_status().json()
            )
            assert retained_usage["charged_usage_bytes"] == used
            # This fixture uses a twoway plan with the default node multiplier.
            # Raw traffic and the doubled billed traffic are separate API fields.
            assert retained_usage["total"] * 2 == used
            with page.expect_response(
                lambda r: r.url.endswith(base + "/remove")
            ) as removed:
                dialog.get_by_role("button", name="Remove", exact=True).click()
            assert removed.value.status == 200
            expect(dialog).not_to_be_visible()
            expect(
                page.get_by_role("button", name="Edit management-edited", exact=True)
            ).to_have_count(0)
            assert quota() == used
            assert not errors, errors
            print(
                "PASS browser invalid/stale edits, selective sync, cancel, explicit removal and 1440/390/320 layouts",
                flush=True,
            )
            return retained_usage
        finally:
            context.close()
            browser.close()


def exercise(work, fixture, args, client, backend, endpoint, ca):
    created = (
        client.post(
            "/api/v1/servers",
            json={
                "name": "management",
                "domain": "127.0.0.1",
            },
        )
        .raise_for_status()
        .json()
    )
    server_id = created["server"]["id"]
    base = "/api/v1/servers/" + server_id
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
                    "tag": "managed",
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
    runtime.poll("installed Agent connected", fixture.ready)
    assert fixture.properties()["User"] != "root"
    native.command(client, base, "scan")
    nodes = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"]
    )
    assert len(nodes) == 1
    inherited = nodes[0]
    custom = (
        client.post(
            "/api/v1/nodes",
            json={
                "name": "custom-address",
                "server_id": server_id,
                "protocol": "vless",
                "inbound_tag": "custom",
                "config": {
                    "type": "vless",
                    "server": "custom.example",
                    "port": 443,
                    "uuid": "{{client_uuid}}",
                },
            },
        )
        .raise_for_status()
        .json()["node"]
    )
    other = (
        client.post("/api/v1/servers", json={"name": "unrelated"})
        .raise_for_status()
        .json()["server"]
    )
    client.post("/api/v1/users", json={"username": "subscriber"}).raise_for_status()
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Management plan",
                "node_ids": [inherited["id"]],
                "traffic_limit_gb": 1,
                "traffic_mode": "twoway",
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    assigned = (
        client.post(
            "/api/v1/users/subscriber/plan",
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
    credentials = (
        client.get("/api/v1/users/subscriber/credentials")
        .raise_for_status()
        .json()["credentials"]
    )
    token = (
        client.post("/api/v1/users/subscriber/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    path = "/api/v1/subscribe/" + token + "?format=xray"

    def quota():
        return (
            client.get("/api/v1/users/subscriber/quota")
            .raise_for_status()
            .json()["quota"]["charged_usage_bytes"]
        )

    with native.echo_server(work) as (echo, _):
        exported = client.get(path).raise_for_status().json()
        with (
            exported_client(work, args.xray, exported) as socks,
            native.connect(socks, echo) as connection,
        ):
            native.transfer(connection, 256 * 1024)
        runtime.poll(
            "actual subscriber usage", quota, ready=lambda value: value >= 512 * 1024
        )
        saved_config = (fixture.root / "config/xray.json").read_bytes()

        change = (
            client.post(
                "/api/v1/change-sets",
                json={
                    "name": "Retained change",
                    "steps": [
                        {
                            "server_id": server_id,
                            "forward": {
                                "method": "GET",
                                "path": "/api/child/system/info",
                            },
                        }
                    ],
                },
            )
            .raise_for_status()
            .json()["change_set"]
        )
        preview = client.get(base + "/removal").raise_for_status().json()
        assert preview["blockers"]
        denied = client.post(
            base + "/remove",
            json={
                "expected_revision": preview["revision"],
                "confirm_name": "management",
                "acknowledge_remote_runtime": True,
            },
        )
        assert denied.status_code == 409
        client.post(
            "/api/v1/change-sets/" + change["id"] + "/rollback", json={}
        ).raise_for_status()

        def after_edit():
            updated = {
                row["id"]: row for row in client.get("/api/v1/nodes").json()["nodes"]
            }
            assert updated[inherited["id"]]["config"]["server"] == "localhost"
            assert updated[custom["id"]]["config"]["server"] == "custom.example"
            assert (
                client.get("/api/v1/users/subscriber/credentials").json()["credentials"]
                == credentials
            )
            assert (fixture.root / "config/xray.json").read_bytes() == saved_config
            current = client.get(path).raise_for_status().json()
            proxy = next(
                row for row in current["outbounds"] if row["protocol"] == "vless"
            )
            assert proxy["settings"]["vnext"][0]["address"] == "localhost"
            before = quota()
            with (
                exported_client(work, args.xray, current) as socks,
                native.connect(socks, echo) as connection,
            ):
                native.transfer(connection, 128 * 1024)
            runtime.poll(
                "edited subscription traffic",
                quota,
                ready=lambda value: value >= before + 256 * 1024,
            )
            assert fixture.ready()

        before_removal = browser(client, backend, base, args.output, quota, after_edit)
        health = fixture.root / "state/health.json"
        removed_at = time.time()
        runtime.poll(
            "installed Agent loses authorization",
            lambda: json.loads(health.read_text()),
            ready=lambda row: not row["connected"] and row["observed_at"] >= removed_at,
            timeout=35,
        )
        denied = client.post(
            "/api/v1/agents/heartbeat", json={"token": created["agent_token"]}
        )
        assert denied.status_code == 401
        assert client.get(base + "/settings").status_code == 404
        assert (fixture.root / "config/xray.json").read_bytes() == saved_config
        retained = quota()
        # Removing records does not revoke the credential in the remote Xray process.
        with (
            exported_client(work, args.xray, exported) as socks,
            native.connect(socks, echo) as connection,
        ):
            native.transfer(connection, 32768)
        assert quota() == retained
        assert client.get("/api/v1/servers").json() == [other]
        assert client.get("/api/v1/plans").json()["plans"][0]["node_ids"] == []
        unavailable = client.get(path)
        assert unavailable.status_code == 404, unavailable.text
        assert "no compatible nodes" in unavailable.json()["detail"]
        assert (
            client.post("/api/v1/users/subscriber/subscription-token").json()[
                "subscription"
            ]["token"]
            == token
        )
        archive = client.get("/api/v1/change-sets/" + change["id"]).json()["change_set"]
        assert archive["steps"][0]["archived"]
        assert archive["steps"][0]["server_name"] == "management-edited"
        usage = client.get("/api/v1/users/subscriber/traffic").raise_for_status().json()
        traffic_fields = (
            "upload", "download", "total", "weighted_upload", "weighted_download",
            "charged_usage_bytes",
        )
        for field in traffic_fields:
            assert usage[field] == before_removal[field], f"Archived {field} changed"
        assert usage["charged_usage_bytes"] == retained
        assert usage["total"] > 0 and usage["total"] * 2 == retained
        assert len(usage["entries"]) == 1
        archived_usage = usage["entries"][0]
        assert archived_usage["archived"]
        assert archived_usage["server_id"] == server_id
        assert archived_usage["server_name"] == "management-edited"
        for field in traffic_fields:
            assert archived_usage[field] == usage[field], f"Archived entry {field} differs"
        with sqlite3.connect(work / "backend.db") as db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        fixture.cli("uninstall", "--purge")
        assert not fixture.root.exists()
        assert not runtime.port_open(port)
        print(
            "PASS actual VLESS provisioning/edited export, durable quota, immutable history, Agent rejection and explicit remote cleanup",
            flush=True,
        )


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    service.run(args.wheel, args.xray_archive)
    print("PASS server management end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
