"""Verify managed node edits/removal through real Agent, Xray and browser workflows."""

import argparse
import importlib.util
import json
import os
import signal
import sqlite3
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "node_users", Path(__file__).with_name("smoke-user-management.py")
)
users = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(users)
servers, native, runtime, service, lifecycle = (
    users.servers,
    users.native,
    users.runtime,
    users.service,
    users.lifecycle,
)
ROOT = Path(__file__).resolve().parents[2]
ACK = "I accept Xray restarts, disconnected clients and pending remote changes"


def exercise(work, fixture, args, client, backend, endpoint, ca):
    created = (
        client.post(
            "/api/v1/servers",
            json={
                "name": "node-agent",
                "domain": "127.0.0.1",
            },
        )
        .raise_for_status()
        .json()
    )
    server_id = created["server"]["id"]
    base = "/api/v1/servers/" + server_id
    port, other_port, stats = (
        runtime.free_port(),
        runtime.free_port(),
        runtime.free_port(),
    )
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
                    "tag": tag,
                    "listen": "127.0.0.1",
                    "port": listener,
                    "protocol": "vless",
                    "settings": {"decryption": "none", "clients": []},
                }
                for tag, listener in (("users", port), ("other", other_port))
            ],
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "exit", "protocol": "freedom"},
                {
                    "tag": "chain",
                    "protocol": "freedom",
                    "proxySettings": {"tag": "exit"},
                },
            ],
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "marktag": "route-users",
                        "user": ["placeholder"],
                        "outboundTag": "exit",
                    },
                ]
            },
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
    runtime.poll("installed non-root node Agent", fixture.ready)
    assert fixture.properties()["User"] != "root"
    native.command(client, base, "scan")
    nodes = (
        client.post(base + "/xray/runtime/nodes/import", json={"host": "127.0.0.1"})
        .raise_for_status()
        .json()["created_nodes"]
    )
    assert len(nodes) == 2
    parent = next(node for node in nodes if node["inbound_tag"] == "users")
    other = next(node for node in nodes if node["inbound_tag"] == "other")
    fields = ("server_id", "protocol", "inbound_tag", "client_template", "config")
    alias = (
        client.post(
            "/api/v1/nodes",
            json={
                **{key: parent[key] for key in fields},
                "name": "Shared alias",
            },
        )
        .raise_for_status()
        .json()["node"]
    )
    child = (
        client.post(
            "/api/v1/nodes",
            json={
                **{key: parent[key] for key in fields},
                "name": "Routed child",
                "node_type": "routed",
                "parent_id": parent["id"],
                "routed_outbound_tag": "exit",
                "routed_rule_marktag": "route-users",
            },
        )
        .raise_for_status()
        .json()["node"]
    )
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Node plan",
                "traffic_limit_gb": 128,
                "node_ids": [parent["id"], alias["id"], child["id"], other["id"]],
                "node_multipliers": {parent["id"]: 2},
                "node_speed_limits": {child["id"]: 50},
                "node_device_limits": {child["id"]: 5},
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    bob_plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "Other plan",
                "traffic_limit_gb": 128,
                "node_ids": [other["id"]],
            },
        )
        .raise_for_status()
        .json()["plan"]
    )
    for name, selected in (("alice", plan), ("bob", bob_plan)):
        client.post("/api/v1/users", json={"username": name}).raise_for_status()
        client.post(
            f"/api/v1/users/{name}/plan",
            json={
                "plan_id": selected["id"],
                "queue_agent_commands": True,
            },
        ).raise_for_status()
        users.wait_access(client, name)

    def subscription(name):
        return (
            client.post(f"/api/v1/users/{name}/subscription-token")
            .raise_for_status()
            .json()["subscription"]["token"]
        )

    alice_token, bob_token = subscription("alice"), subscription("bob")

    def exported(token, node_id):
        return (
            client.get(f"/api/v1/subscribe/{token}?format=xray&node_id={node_id}")
            .raise_for_status()
            .json()
        )

    original, routed, unrelated = (
        exported(alice_token, parent["id"]),
        exported(alice_token, child["id"]),
        exported(bob_token, other["id"]),
    )
    credentials = (
        client.get("/api/v1/users/alice/credentials")
        .raise_for_status()
        .json()["credentials"]
    )
    shared = [
        row for row in credentials if row["node_id"] in {parent["id"], alias["id"]}
    ]
    assert len(shared) == 2 and shared[0]["credential"] == shared[1]["credential"]
    original_user = client.get("/api/v1/users/alice/settings").json()["user"]

    def detail(identifier):
        return (
            client.get(f"/api/v1/nodes/{identifier}/settings").raise_for_status().json()
        )

    def removal(identifier):
        value = detail(identifier)
        return (
            client.post(
                f"/api/v1/nodes/{identifier}/remove",
                json={
                    "expected_revision": value["revision"],
                    "confirm_name": value["node"]["name"],
                    "acknowledge_runtime_restart": True,
                },
            )
            .raise_for_status()
            .json()
        )

    def wait_removal(identifier):
        return runtime.poll(
            "confirmed managed node removal",
            lambda: (
                client.get("/api/v1/node-removals/" + identifier)
                .raise_for_status()
                .json()
            ),
            ready=lambda value: value["status"] == "completed",
            timeout=120,
        )

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
        transfer(routed)
        transfer(unrelated)
        charged = runtime.poll(
            "charged node traffic",
            lambda: client.get("/api/v1/users/alice/traffic").json()["total"],
            ready=lambda total: total >= 65536,
        )
        alias_job = removal(alias["id"])
        assert alias_job["servers"][0]["retained_inbound_tags"] == ["users"]
        assert alias_job["servers"][0]["inbound_tags"] == []
        wait_removal(alias_job["id"])
        transfer(original)
        transfer(routed)
        print(
            "PASS shared alias removal retains the listener and live physical/routed credentials",
            flush=True,
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
            page.get_by_role(
                "button", name="Edit node " + parent["name"], exact=True
            ).click()
            dialog = page.get_by_role("dialog")

            def capture(label):
                for width, height, suffix in (
                    (1440, 1000, "desktop"),
                    (390, 844, "mobile"),
                    (320, 740, "narrow"),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    page.wait_for_timeout(200)
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
                page.set_viewport_size({"width": 1440, "height": 1000})

            name = dialog.get_by_label("Node name", exact=True)
            expect(name).to_have_value(parent["name"])
            dialog.get_by_label("Node config", exact=True).fill("[]")
            dialog.get_by_label(ACK, exact=True).check()
            dialog.get_by_role("button", name="Save", exact=True).click()
            expect(
                dialog.get_by_text("Node config must be a JSON object", exact=True)
            ).to_be_visible()
            dialog.get_by_label("Node config", exact=True).fill(
                json.dumps(parent["config"], indent=2)
            )
            name.fill("Managed physical node")
            with page.expect_response(
                lambda response: (
                    response.url.endswith(f"/nodes/{parent['id']}/settings")
                    and response.request.method == "PUT"
                )
            ) as saved:
                dialog.get_by_role("button", name="Save", exact=True).click()
            assert saved.value.status == 200
            expect(dialog.get_by_text("Node saved", exact=True)).to_be_visible()
            capture("edited")
            dialog.locator(".ant-modal-footer").get_by_role(
                "button", name="Close", exact=True
            ).click()
            assert (
                client.get("/api/v1/users/alice/settings").json()["user"]
                == original_user
            )
            assert subscription("alice") == alice_token
            transfer(original)
            print(
                "PASS browser node editing, JSON validation and preserved subscriber identity",
                flush=True,
            )

            page.get_by_role(
                "button", name="Remove node Managed physical node", exact=True
            ).click()
            expect(
                dialog.get_by_role("heading", name="Remote resources", exact=True)
            ).to_be_visible()
            expect(
                dialog.get_by_label("Affected nodes").get_by_text(
                    "Routed child", exact=True
                )
            ).to_be_visible()
            capture("impact")
            dialog.get_by_label("Confirm node name", exact=True).fill(
                "Managed physical node"
            )
            dialog.get_by_label(ACK, exact=True).check()
            pid = int(fixture.properties()["MainPID"])
            os.kill(pid, signal.SIGSTOP)
            try:
                with page.expect_response(
                    lambda response: response.url.endswith(
                        f"/nodes/{parent['id']}/remove"
                    )
                ) as started:
                    dialog.get_by_role("button", name="Remove", exact=True).click()
                assert started.value.status == 202
                job = started.value.json()
                assert job["status"] == "pending" and set(job["node_ids"]) == {
                    parent["id"],
                    child["id"],
                }
                expect(
                    dialog.get_by_text("Removal pending Agent confirmation", exact=True)
                ).to_be_visible()
                assert (
                    client.get(
                        f"/api/v1/subscribe/{alice_token}?format=xray&node_id={parent['id']}"
                    ).status_code
                    == 404
                )
                transfer(unrelated)
                transfer(original)
                capture("pending")
                dialog.locator(".ant-modal-footer").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                page.get_by_role(
                    "button",
                    name="Node removal status Managed physical node",
                    exact=True,
                ).click()
                expect(
                    dialog.get_by_text("Removal pending Agent confirmation", exact=True)
                ).to_be_visible()
                old_restore = (
                    client.post(
                        base + "/commands",
                        json={
                            "method": "POST",
                            "path": "/api/child/xray/config",
                            "body": {
                                "config": json.dumps(
                                    {
                                        "inbounds": [
                                            {
                                                "tag": "users",
                                                "protocol": "vless",
                                                "settings": {
                                                    "clients": [shared[0]["credential"]]
                                                },
                                            }
                                        ],
                                        "outbounds": [
                                            {"tag": "direct", "protocol": "freedom"}
                                        ],
                                    }
                                )
                            },
                        },
                    )
                    .raise_for_status()
                    .json()["command"]
                )
                os.kill(pid, signal.SIGKILL)
                runtime.poll(
                    "Agent process restarts",
                    lambda: (
                        fixture.ready() and int(fixture.properties()["MainPID"]) != pid
                    ),
                    timeout=45,
                )
            finally:
                try:
                    os.kill(pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            done = wait_removal(job["id"])
            assert all(step["phase"] == "completed" for step in done["servers"])
            expect(dialog.get_by_text("Node removed", exact=True)).to_be_visible(
                timeout=15000
            )
            expect(
                dialog.get_by_role("button", name="Retry node removal", exact=True)
            ).to_be_disabled()
            capture("completed")
            assert rejected(original) and rejected(routed)
            transfer(unrelated)
            transfer(exported(alice_token, other["id"]))
            assert client.get("/api/v1/users/alice/traffic").json()["total"] >= charged
            assert (
                client.get("/api/v1/users/alice/settings").json()["user"]
                == original_user
            )
            assert subscription("alice") == alice_token
            current_plan = client.get(f"/api/v1/plans/{plan['id']}/settings").json()[
                "plan"
            ]
            assert current_plan["node_ids"] == [other["id"]]
            assert all(
                current_plan[field] == {}
                for field in (
                    "node_multipliers",
                    "node_speed_limits",
                    "node_device_limits",
                )
            )
            assert (
                client.get(f"/api/v1/nodes/{child['id']}/settings").status_code == 404
            )
            commands = client.get(base + "/commands").json()["commands"]
            assert (
                next(value for value in commands if value["id"] == old_restore["id"])[
                    "status"
                ]
                == "skipped"
            )
            live = json.loads((fixture.root / "config/xray.json").read_text())
            assert {entry["tag"] for entry in live["inbounds"]} == {"other"}
            assert {entry["tag"] for entry in live["outbounds"]} == {"direct"}
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
                assert db.execute(
                    "SELECT completed_at FROM managed_node_removals WHERE id=?",
                    (job["id"],),
                ).fetchone()[0]
            assert not errors, errors
            print(
                "PASS offline pending removal, Agent crash recovery, routed closure, dead old clients and preserved traffic/links",
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
    os.environ["OPEN_NODE_SUBSCRIPTION_ACCESS_POLL_SECONDS"] = "1"
    service.run(args.wheel, args.xray_archive)
    print("PASS node management end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
