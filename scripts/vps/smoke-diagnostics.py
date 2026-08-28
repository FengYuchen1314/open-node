"""Native diagnostics over verified HTTPS/WSS, non-root systemd, and the real UI."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import socket
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect

SPEC = importlib.util.spec_from_file_location(
    "diagnostic_lifecycle_fixture", Path(__file__).with_name("smoke-agent-lifecycle.py")
)
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)
service, runtime = lifecycle.service, lifecycle.runtime
ROOT = Path(__file__).resolve().parents[2]
NEXTTRACE_SHA256 = "093849f1012b065c29d307b8e47fedec667206829c14e105f83a852f60c628d1"


def operation(client, base, name, payload=None):
    queued = (
        client.post(base + "/operations/" + name, json=payload or {})
        .raise_for_status()
        .json()
    )
    return lifecycle.wait_command(client, base, queued["command"])


def check_layout(page, output):
    try:
        lifecycle.ui.check_layout(page)
        page.wait_for_function("""() => [...document.querySelectorAll('.page-shell button, .page-shell input')]
          .filter(el => el.getClientRects().length && !el.closest('[inert], [aria-hidden="true"]')
            && getComputedStyle(el).visibility !== 'hidden')
          .every(el => {
            const inside = box => box.x >= 0 && box.right <= innerWidth + 1;
            if (inside(el.getBoundingClientRect())) return true;
            for (let parent = el.parentElement; parent; parent = parent.parentElement) {
              if (['auto', 'scroll'].includes(getComputedStyle(parent).overflowX)
                  && parent.scrollWidth > parent.clientWidth + 1
                  && inside(parent.getBoundingClientRect())) return true;
            }
            return false;
          })""")
    except Exception:
        page.screenshot(path=output / "layout-failure.png", full_page=True)
        print(
            page.evaluate("""() => [...document.querySelectorAll('body *')]
          .filter(el => el.getClientRects().length && !el.closest('[inert], [aria-hidden="true"]')
            && el.getBoundingClientRect().right > innerWidth + 1)
          .slice(0, 25).map(el => ({html: el.outerHTML.slice(0, 250),
            width: el.getBoundingClientRect().width, x: el.getBoundingClientRect().x}))"""),
            flush=True,
        )
        raise


def browser_checks(client, endpoint, ca, name, base, target, output, mode):
    with lifecycle.browser_panel(client, endpoint, ca, name) as page:
        for label, width, height in (
            ("desktop", 1440, 1000),
            ("mobile", 390, 844),
            ("narrow", 320, 780),
        ):
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_label("Latency targets", exact=True).fill(target)
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.endswith("/operations/domain-latency")
                )
            ) as reply:
                page.get_by_role(
                    "button", name="Queue latency probe", exact=True
                ).click()
            command = reply.value.json()["command"]
            lifecycle.wait_command(client, base, command)
            panel = page.locator(f'[data-command-id="{command["id"]}"]')
            if (
                panel.get_by_role("button").first.get_attribute("aria-expanded")
                != "true"
            ):
                panel.get_by_role("button").first.click()
            expect(panel.get_by_text("TCP port open", exact=False)).to_be_visible(
                timeout=30000
            )
            check_layout(page, output)
            panel.scroll_into_view_if_needed()
            page.screenshot(
                path=output / f"{mode}-{label}-latency.png", animations="disabled"
            )

            page.get_by_label("Telecom host", exact=True).fill("127.0.0.1")
            page.get_by_label("Telecom port", exact=True).fill(target.rsplit(":", 1)[1])
            with page.expect_response(
                lambda response: (
                    response.request.method == "POST"
                    and response.url.endswith("/operations/network/return-route-test")
                )
            ) as reply:
                page.get_by_role(
                    "button", name="Trace return route", exact=True
                ).click()
            command = reply.value.json()["command"]
            lifecycle.wait_command(client, base, command)
            panel = page.locator(f'[data-command-id="{command["id"]}"]')
            if (
                panel.get_by_role("button").first.get_attribute("aria-expanded")
                != "true"
            ):
                panel.get_by_role("button").first.click()
            expect(panel.get_by_text("Unknown", exact=False)).to_be_visible(
                timeout=30000
            )
            expect(panel.get_by_text("ASN unavailable", exact=False)).to_be_visible()
            check_layout(page, output)
            panel.scroll_into_view_if_needed()
            page.screenshot(
                path=output / f"{mode}-{label}-route.png", animations="disabled"
            )

        page.get_by_label("All files", exact=True).check()
        purge = page.get_by_role("button", name="Purge logs", exact=True)
        expect(purge).to_be_disabled()
        page.get_by_label("Confirm log deletion", exact=True).check()
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/operations/logs/files/delete")
            )
        ) as reply:
            purge.click()
        lifecycle.wait_command(client, base, reply.value.json()["command"])
        expect(purge).to_be_disabled()
        print(
            "PASS " + mode + " responsive browser probes and explicit log deletion",
            flush=True,
        )

        page.goto(endpoint + "/probe")
        server = page.get_by_label("Server", exact=True)
        server.press("Enter")
        page.get_by_role("option", name=re.compile(re.escape(name))).click()
        kind = page.get_by_label("Probe type", exact=True)
        kind.press("Enter")
        page.get_by_role("option", name="Return route", exact=True).click()
        page.get_by_label("Telecom host", exact=True).fill("127.0.0.1")
        page.get_by_label("Telecom port", exact=True).fill(target.rsplit(":", 1)[1])
        with page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith("/api/v1/probe/tasks")
            )
        ) as reply:
            page.get_by_role("button", name="Add task", exact=True).click()
        assert reply.value.ok, reply.value.text()
        task = reply.value.json()["task"]
        assert task["kind"] == "return_route", task
        assert task["server_id"] == base.rsplit("/", 1)[1]
        for label, width, height in (
            ("desktop", 1440, 1000),
            ("mobile", 390, 844),
            ("narrow", 320, 780),
        ):
            page.set_viewport_size({"width": width, "height": height})
            check_layout(page, output)
            kind.scroll_into_view_if_needed()
            page.screenshot(
                path=output / f"{mode}-{label}-scheduled-route.png",
                animations="disabled",
            )
        dispatched = (
            client.post("/api/v1/probe/tasks/dispatch-due").raise_for_status().json()
        )
        command = next(
            item["command"]
            for item in dispatched["dispatched"]
            if item["task"]["id"] == task["id"]
        )
        completed = lifecycle.wait_command(client, base, command)
        assert completed["result_body"]["results"][0]["success"]
        print(
            "PASS scheduled return-route UI creates and dispatches a real probe",
            flush=True,
        )


def exercise_mode(
    work, fixture, wheel, xray, client, echo, mode, endpoint, ca, nexttrace, output
):
    directory = work / ("diagnostic-" + mode)
    directory.mkdir()
    name = "diagnostic-" + mode
    created = (
        client.post("/api/v1/servers", json={"name": name}).raise_for_status().json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    config, xray_config = directory / "agent.json", directory / "xray.json"
    port, user = runtime.free_port(), str(uuid4())
    runtime.write_private(
        config,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
            "connection_mode": mode,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 2,
            "poll_seconds": 0.2,
            "nexttrace_binary": str(nexttrace),
            "nexttrace_geoip": mode == "websocket",
        },
    )
    runtime.write_private(
        xray_config,
        {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "vless",
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "vless",
                    "settings": {
                        "decryption": "none",
                        "clients": [{"id": user, "email": "diagnostic-user"}],
                    },
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        },
    )
    fixture.cli(
        "install",
        "--wheel",
        wheel,
        "--config",
        config,
        "--xray-config",
        xray_config,
        "--xray",
        xray,
        "--network-diagnostics",
    )
    assert fixture.ready()
    account = service.pwd.getpwnam(fixture.user)
    assert account.pw_uid != 0
    assert "CAP_NET_RAW" in fixture.record()["unit_text"]
    assert (fixture.root / "runtime/nexttrace").stat().st_uid == 0
    target = f"localhost:{echo}"
    closed_port = runtime.free_port()
    probed = operation(
        client,
        base,
        "domain-latency",
        {
            "domains": [
                target,
                f"127.0.0.1:{closed_port}",
                "not-resolvable.invalid:443",
            ],
            "allow_icmp": True,
            "timeout_ms": 500,
        },
    )["result_body"]
    assert probed["count"] == 3
    by_target = {row["target"]: row for row in probed["results"]}
    assert by_target[target]["success"] and by_target[target]["method"] == "tcp"
    assert by_target[f"127.0.0.1:{closed_port}"]["method"] == "icmp", probed
    assert not by_target["not-resolvable.invalid:443"]["success"]
    route = operation(
        client,
        base,
        "network/return-route-test",
        {
            "targets": [{"carrier": "telecom", "host": "localhost", "port": echo}],
            "timeout_seconds": 10,
        },
    )["result_body"]["results"][0]
    assert route["success"] and route["reached"], route
    assert route["hops"][0]["ip"] == "127.0.0.1"
    assert route["route_type"] == "Unknown" and route["entry_hop"] is None
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
        listener.bind(("::1", 0))
        listener.listen()
        port6 = listener.getsockname()[1]
        result6 = operation(
            client, base, "domain-latency", {"domains": [f"[::1]:{port6}"]}
        )
        assert result6["result_body"]["results"][0]["success"]
        traced6 = operation(
            client,
            base,
            "network/return-route-test",
            {
                "ip_version": 6,
                "timeout_seconds": 10,
                "targets": [{"carrier": "unicom", "host": "::1", "port": port6}],
            },
        )["result_body"]["results"][0]
        assert traced6["success"] and traced6["reached"], traced6
    print(
        "PASS "
        + mode
        + " non-root TCP, ICMP fallback, DNS failure, IPv4/IPv6 route hops",
        flush=True,
    )
    if mode == "websocket":
        public = operation(
            client,
            base,
            "network/return-route-test",
            {
                "targets": [{"carrier": "mobile", "host": "1.1.1.1", "port": 443}],
                "timeout_seconds": 25,
            },
        )["result_body"]["results"][0]
        assert public["success"] and public["path_asns"], public
        (output / "public-route-evidence.json").write_text(json.dumps(public, indent=2))
        print("PASS public TCP route with real ASN/geolocation evidence", flush=True)

    for name in ("agent", "xray", "nginx"):
        assert operation(client, base, "logs", {"service": name})["result_body"][
            "success"
        ]
    snapshots = {
        name: (fixture.root / "config" / name).read_bytes()
        for name in ("agent.json", "xray.json")
    }
    archive = fixture.root / "state/agent.log.1"
    archive.write_text("rotation fixture\n")
    archive.chmod(0o600)
    os.chown(archive, account.pw_uid, account.pw_gid)
    files = operation(client, base, "logs/files/list")["result_body"]
    assert "commands.sqlite" not in str(files) and created["agent_token"] not in str(
        files
    )
    assert any(row["name"] == "agent.log.1" for row in files["files"])
    result = operation(client, base, "logs/files/delete", {"name": "agent.log.1"})[
        "result_body"
    ]
    assert result["success"] and not archive.exists()
    token = created["agent_token"]
    assert token not in (fixture.root / "state/agent.log").read_text()
    for name, data in snapshots.items():
        assert (fixture.root / "config" / name).read_bytes() == data
    latest = client.get(base + "/telemetry/latest").raise_for_status().json()
    assert latest
    targets = client.get("/api/v1/public/probe-targets", params={"range": "1h"})
    targets.raise_for_status()
    assert target in targets.text, targets.text
    print("PASS diagnostic samples persist in real probe comparisons", flush=True)
    browser_checks(
        client, endpoint, ca, "diagnostic-" + mode, base, target, output, mode
    )
    with runtime.proxy_client(directory, xray, port, user) as socks:
        runtime.poll(
            "real VLESS traffic survives probing and log clearing",
            lambda: runtime.forwards(socks, echo),
        )


def exercise_no_raw(work, wheel, xray, client, endpoint, ca, echo):
    fixture = service.Fixture(work)
    directory = work / "no-raw"
    directory.mkdir()
    created = (
        client.post("/api/v1/servers", json={"name": "diagnostic-no-raw"})
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    config, xray_config = directory / "agent.json", directory / "xray.json"
    runtime.write_private(
        config,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
            "connection_mode": "http",
            "auto_start": False,
            "poll_seconds": 0.2,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 2,
        },
    )
    runtime.write_private(
        xray_config, {"inbounds": [], "outbounds": [{"protocol": "freedom"}]}
    )
    try:
        fixture.cli(
            "install",
            "--wheel",
            wheel,
            "--config",
            config,
            "--xray-config",
            xray_config,
            "--xray",
            xray,
        )
        assert fixture.ready()
        status = Path("/proc") / fixture.properties()["MainPID"] / "status"
        mask = int(re.search(r"CapEff:\s+([0-9a-f]+)", status.read_text())[1], 16)
        assert not mask & (1 << 13)
        tcp = operation(
            client, base, "domain-latency", {"domains": [f"localhost:{echo}"]}
        )
        assert tcp["result_body"]["results"][0]["success"]
        denied = operation(
            client,
            base,
            "domain-latency",
            {
                "domains": [f"127.0.0.1:{runtime.free_port()}"],
                "allow_icmp": True,
            },
        )["result_body"]["results"][0]
        assert not denied["success"] and denied["icmp_error"], denied
        route = operation(
            client,
            base,
            "network/return-route-test",
            {
                "targets": [{"carrier": "telecom", "host": "127.0.0.1"}],
            },
        )["result_body"]["results"][0]
        assert not route["success"] and route["error"]
        print(
            "PASS default non-root policy retains TCP probing and reports ICMP/tool restrictions",
            flush=True,
        )
    finally:
        fixture.cleanup()


def run(args):
    assert hashlib.sha256(args.nexttrace.read_bytes()).hexdigest() == NEXTTRACE_SHA256
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")

    def exercise(work, first, wheel, xray, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            for mode in ("websocket", "http"):
                fixture = first if mode == "websocket" else service.Fixture(work)
                try:
                    exercise_mode(
                        work,
                        fixture,
                        wheel,
                        xray,
                        client,
                        echo,
                        mode,
                        endpoint,
                        ca,
                        args.nexttrace,
                        args.output,
                    )
                finally:
                    fixture.cleanup()
            exercise_no_raw(work, wheel, xray, client, endpoint, ca, echo)

    service.exercise = exercise
    service.run(args.wheel, args.xray_archive)
    print("PASS native diagnostics smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nexttrace", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
