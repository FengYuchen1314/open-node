"""Verify durable server billing with installed Agent, real Xray traffic and browser UI."""

import argparse
import importlib.util
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "billing_native", Path(__file__).with_name("smoke-native-limiter.py")
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)
runtime, service, lifecycle = native.runtime, native.service, native.lifecycle
ROOT = Path(__file__).resolve().parents[2]


def browser(client, backend, base, output):
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
            panel = page.get_by_role("region", name="Server traffic", exact=True)
            expect(panel.get_by_label("Traffic source", exact=True)).to_be_visible(
                timeout=15000
            )
            panel.get_by_label("Traffic source", exact=True).press("Enter")
            page.get_by_role("option", name="Xray nodes", exact=True).click()
            panel.get_by_label("Counted direction", exact=True).press("Enter")
            page.get_by_role("option", name="Larger direction", exact=True).click()
            panel.get_by_label("Monthly reset day (UTC)", exact=True).press("Enter")
            page.get_by_role("option", name="31", exact=True).click()
            panel.get_by_label("Quota (GiB, 0 = unlimited)", exact=True).fill("-1")
            expect(
                panel.get_by_role("button", name="Save", exact=True)
            ).to_be_disabled()
            panel.get_by_label("Quota (GiB, 0 = unlimited)", exact=True).fill("2")
            with page.expect_response(
                lambda r: (
                    r.url.endswith(base + "/traffic") and r.request.method == "PUT"
                )
            ) as saved:
                panel.get_by_role("button", name="Save", exact=True).click()
            assert saved.value.status == 200
            result = client.get(base + "/traffic").raise_for_status().json()
            assert (
                result["traffic_stats_mode"] == "max"
                and result["traffic_reset_day"] == 31
            )
            assert result["traffic_limit"] == 2 * 1024**3
            assert result["used"] == max(result["upload"], result["download"])
            for width, height, label in [
                (1440, 1000, "desktop"),
                (390, 844, "mobile"),
                (320, 740, "narrow"),
            ]:
                page.set_viewport_size({"width": width, "height": height})
                panel.scroll_into_view_if_needed()
                panel.get_by_role("button", name="Reset cycle", exact=True).click()
                dialog = page.get_by_role("dialog")
                expect(
                    dialog.get_by_text("Reset server traffic?", exact=True)
                ).to_be_visible()
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert client.get(base + "/traffic").json()["used"] > 0
                assert panel.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                for field in panel.locator(".v-field").all():
                    box = field.bounding_box()
                    assert box and box["width"] > 120 and box["x"] >= 0
                    assert box["x"] + box["width"] <= width + 1
                panel.screenshot(path=str(output / (label + "-panel.png")))
                page.screenshot(path=str(output / (label + ".png")))
            panel.get_by_role("button", name="Reset cycle", exact=True).click()
            with page.expect_response(
                lambda r: r.url.endswith(base + "/traffic/reset")
            ) as reset:
                page.get_by_role("dialog").get_by_role(
                    "button", name="Reset", exact=True
                ).click()
            assert reset.value.status == 200 and reset.value.json()["used"] == 0
            expect(panel.get_by_test_id("server-traffic-used")).to_have_text("0 B")
            assert not errors, errors
            print(
                "PASS browser settings, validation, reset/cancel and 1440/390/320 layouts",
                flush=True,
            )
        finally:
            context.close()
            browser.close()


def exercise(work, fixture, args, client, backend, endpoint, ca):
    created = (
        client.post("/api/v1/servers", json={"name": "server-traffic"})
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    port, stats_port = runtime.free_port(), runtime.free_port()
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
            "stats_address": f"127.0.0.1:{stats_port}",
        },
    )
    runtime.write_private(
        xray,
        {
            "log": {"loglevel": "warning"},
            "api": {
                "listen": f"127.0.0.1:{stats_port}",
                "tag": "api",
                "services": ["StatsService"],
            },
            "stats": {},
            "policy": {
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True,
                    "statsOutboundUplink": True,
                    "statsOutboundDownlink": True,
                }
            },
            "inbounds": [
                {
                    "tag": "billing",
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
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
    runtime.poll("installed non-root Agent", fixture.ready)
    assert fixture.properties()["User"] != "root"

    def meter():
        return client.get(base + "/traffic").raise_for_status().json()

    runtime.poll(
        "first Xray sample",
        meter,
        ready=lambda item: item["last_reported_at"] is not None,
    )
    with native.echo_server(work) as (echo, _):

        def send():
            with native.connect(port, echo) as connection:
                native.transfer(connection, 256 * 1024)

        send()
        first = runtime.poll(
            "real proxy bytes", meter, ready=lambda item: item["used"] >= 1024 * 1024
        )
        assert first["used"] == first["upload"] + first["download"]
        config_bytes = (fixture.root / "config/xray.json").read_bytes()
        reset = client.post(base + "/traffic/reset").raise_for_status().json()
        assert reset["used"] == 0
        assert reset["cumulative_upload"] == first["cumulative_upload"]
        assert (fixture.root / "config/xray.json").read_bytes() == config_bytes
        send()
        second = runtime.poll(
            "post-reset proxy bytes",
            meter,
            ready=lambda item: item["used"] >= 1024 * 1024,
        )
        assert second["cumulative_upload"] > first["cumulative_upload"]
        native.command(
            client, base, "services/control", {"service": "xray", "action": "restart"}
        )
        old = second["cumulative_upload"]
        runtime.poll(
            "zero Xray counters after restart",
            lambda: client.get(base + "/telemetry/latest").json(),
            ready=lambda row: (
                row.get("latest", {})
                .get("stats", {})
                .get("inbound", {})
                .get("billing", {})
                .get("uplink")
                == 0
            ),
        )
        send()
        runtime.poll(
            "persistent traffic after Xray restart",
            meter,
            ready=lambda item: item["cumulative_upload"] >= old + 512 * 1024,
        )
        subprocess.run(["systemctl", "restart", fixture.unit], check=True, timeout=30)
        runtime.poll("Agent restarted", fixture.ready)
        assert meter()["cumulative_upload"] >= old + 512 * 1024
        print(
            "PASS real Xray bytes, cycle reset, runtime/Agent restarts and preserved config",
            flush=True,
        )

        now = datetime.now(UTC)
        assert now.hour > 0 or now.minute >= 5, "Run this fixture after 00:05 UTC"
        client.put(
            base + "/traffic",
            json={
                "traffic_source": "xray",
                "traffic_stats_mode": "both",
                "traffic_limit": 1024**3,
                "traffic_reset_day": now.day,
            },
        ).raise_for_status()
        with sqlite3.connect(work / "backend.db") as db:
            db.execute(
                "UPDATE servers SET last_traffic_reset_at=? WHERE id=?",
                ((now - timedelta(days=40)).isoformat(), created["server"]["id"]),
            )
        runtime.poll(
            "automatic monthly server reset",
            meter,
            ready=lambda item: (
                datetime.fromisoformat(item["last_reset_at"]) >= now
                and item["used"] == 0
            ),
        )
        send()
        runtime.poll(
            "billing resumes after monthly reset",
            meter,
            ready=lambda item: item["used"] >= 1024 * 1024,
        )
        print(
            "PASS lifespan worker applies monthly reset and billing resumes", flush=True
        )
        browser(client, backend, base, args.output)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_SERVER_TRAFFIC_POLL_SECONDS"] = "1"
    service.run(args.wheel, args.xray_archive)
    print("PASS server traffic end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
