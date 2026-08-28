"""Verify guarded native resource cleanup and process-kill recovery with real traffic."""

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import sqlite3
from pathlib import Path
from uuid import uuid4

SPEC = importlib.util.spec_from_file_location(
    "cleanup_servers", Path(__file__).with_name("smoke-server-management.py")
)
servers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(servers)
native, runtime, service, lifecycle = (
    servers.native,
    servers.runtime,
    servers.service,
    servers.lifecycle,
)
ENDPOINT = "/api/child/node-cleanup"

INTERRUPTION = """
_cleanup_original_write = XrayRuntime.write
_cleanup_original_restart = XrayRuntime.restart
async def _cleanup_failed_restart(self):
    marker = self.config.state_dir / "cleanup-fail-restart"
    if marker.exists():
        marker.unlink()
        raise RuntimeFailure("Intentional cleanup restart failure")
    await _cleanup_original_restart(self)
async def _cleanup_interrupted_write(self, value, **kwargs):
    try:
        result = await _cleanup_original_write(self, value, **kwargs)
    except BaseException:
        marker = self.config.state_dir / "cleanup-rollback-pause"
        if marker.exists():
            marker.unlink()
            import signal
            os.kill(os.getpid(), signal.SIGSTOP)
        raise
    marker = self.config.state_dir / "cleanup-interrupt-once"
    wanted = marker.read_text() if marker.exists() else None
    if wanted and not any(item.get("tag") == wanted for item in value.get("inbounds", [])):
        marker.unlink()
        import signal
        os.kill(os.getpid(), signal.SIGSTOP)
    return result
XrayRuntime.write = _cleanup_interrupted_write
XrayRuntime.restart = _cleanup_failed_restart
"""


def queue(client, base, body, path=ENDPOINT):
    return (
        client.post(
            base + "/commands",
            json={
                "method": "POST",
                "path": path,
                "body": body,
                "timeout_ms": 10000,
            },
        )
        .raise_for_status()
        .json()["command"]
    )


def command(client, base, body, path=ENDPOINT, expected="succeeded"):
    return lifecycle.wait_command(client, base, queue(client, base, body, path), expected)


def exercise(work, fixture, args, client, endpoint, ca):
    created = (
        client.post(
            "/api/v1/servers",
            json={
                "name": "cleanup-agent",
                "domain": "127.0.0.1",
            },
        )
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    ports = {name: runtime.free_port() for name in ("users", "other", "crash")}
    credentials = {name: str(uuid4()) for name in ports}
    agent_path, xray_path = work / "agent-input.json", work / "xray-input.json"
    runtime.write_private(
        agent_path,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
            "connection_mode": args.transport,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 1,
        },
    )
    original = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": name,
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [{"id": credentials[name], "email": name}],
                },
            }
            for name, port in ports.items()
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "exit", "protocol": "freedom"},
            {"tag": "chain", "protocol": "freedom", "proxySettings": {"tag": "exit"}},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["users", "other"], "outboundTag": "direct"},
                {"type": "field", "domain": ["full:unused.example"], "outboundTag": "chain"},
            ]
        },
    }
    runtime.write_private(xray_path, original)
    fixture.cli(
        "install",
        "--wheel",
        args.wheel,
        "--config",
        agent_path,
        "--xray-config",
        xray_path,
        "--xray",
        args.xray,
    )
    runtime.poll("installed non-root cleanup Agent", fixture.ready)
    assert fixture.properties()["User"] != "root"
    live_path = fixture.root / "config/xray.json"

    def preview(**targets):
        result = command(client, base, {"action": "preview", **targets})["result_body"][
            "node_cleanup"
        ]
        return {
            "action": "apply",
            **targets,
            "operation_id": str(uuid4()),
            "expected_revision": result["revision"],
            "acknowledge_runtime_restart": True,
        }, result

    def config(name):
        return {
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": "127.0.0.1",
                                "port": ports[name],
                                "users": [{"id": credentials[name], "encryption": "none"}],
                            }
                        ]
                    },
                }
            ]
        }

    with native.echo_server(work) as (echo, _):

        def transfer(name, size=32768):
            with servers.exported_client(work, args.xray, config(name)) as socks:
                with native.connect(socks, echo) as connection:
                    return native.transfer(connection, size)

        def rejected(name):
            try:
                transfer(name)
            except (OSError, AssertionError, TimeoutError):
                return True
            return False

        for name in ports:
            transfer(name)
        stale, _ = preview(inbound_tags=["users"])
        unchanged = json.loads(live_path.read_text())
        added = {"tag": "late-outbound", "protocol": "freedom"}
        native.command(client, base, "outbounds/manage", {"action": "add", "outbound": added})
        command(client, base, stale, expected="failed")
        assert json.loads(live_path.read_text())["inbounds"] == unchanged["inbounds"]
        transfer("users")
        print("PASS stale cleanup leaves real clients active", flush=True)

        native.command(
            client,
            base,
            "limiter",
            {
                "inbound_tag": "users",
                "users": [{"uid": 0, "email": "users", "speed_limit": 10000000}],
            },
        )
        entries = [
            {
                "tag": "users",
                "protocol": "vless",
                "enabled": False,
                "client": {"id": credentials["users"], "email": "users"},
            }
        ]
        digest = hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        command(
            client, base, {"entries": entries, "revision": digest}, "/api/child/subscription-access"
        )
        assert rejected("users")
        transfer("other")
        payload, read = preview(inbound_tags=["users"], outbound_tags=["exit"])
        assert read["impact"]["suspended_tags"] == ["users"]
        assert read["impact"]["outbound_tags"] == ["chain", "exit"]
        assert read["impact"]["removed_limiter_policies"] == 1
        stopped_pid = int(fixture.properties()["MainPID"])
        os.kill(stopped_pid, signal.SIGSTOP)
        try:
            pending = queue(client, base, payload)
            assert not lifecycle.command_row(client, base, pending)["status"] == "succeeded"
            transfer("other")
        finally:
            os.kill(stopped_pid, signal.SIGCONT)
        result = lifecycle.wait_command(client, base, pending)
        assert result["result_body"]["node_cleanup"]["applied"]
        current = json.loads(live_path.read_text())
        assert [item["tag"] for item in current["inbounds"]] == ["other", "crash"]
        assert [item["tag"] for item in current["outbounds"]] == ["direct", "late-outbound"]
        assert current["routing"]["rules"] == [
            {
                "type": "field",
                "inboundTag": ["other"],
                "outboundTag": "direct",
            }
        ]
        command(client, base, payload)
        entries[0]["enabled"] = True
        digest = hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        command(
            client,
            base,
            {"entries": entries, "revision": digest},
            "/api/child/subscription-access",
            "failed",
        )
        assert rejected("users")
        transfer("other")
        print(
            "PASS paused Agent, suspended-template and dependency cleanup, preserved forwarding",
            flush=True,
        )

        crash_wheel = service.variant_wheel(
            args.wheel, work, "cleanup-interruption", runtime_suffix=INTERRUPTION
        )
        fixture.cli("upgrade", "--wheel", crash_wheel)
        runtime.poll("instrumented cleanup Agent ready", fixture.ready)

        def mark(name):
            marker = fixture.root / "state" / name
            marker.write_text("crash")
            owner = marker.parent.stat()
            os.chown(marker, owner.st_uid, owner.st_gid)
            marker.chmod(0o600)
            return marker

        native.command(
            client,
            base,
            "limiter",
            {
                "inbound_tag": "crash",
                "users": [{"uid": 0, "email": "crash", "speed_limit": 62500}],
            },
        )
        rollback, _ = preview(inbound_tags=["crash"])
        mark("cleanup-fail-restart")
        paused = mark("cleanup-rollback-pause")
        rollback_command = queue(client, base, rollback)
        paused_pid = int(fixture.properties()["MainPID"])
        try:
            runtime.poll("failed restart restores the prior runtime", lambda: not paused.exists())
            assert any(
                item["tag"] == "crash" for item in json.loads(live_path.read_text())["inbounds"]
            )
            policies = json.loads((fixture.root / "state/limits/policy.json").read_text())
            policy = next(item for item in policies["inbounds"] if item["inbound_tag"] == "crash")
            assert policy["users"][0]["speed_limit"] == 62500
            elapsed = transfer("crash", 262144)
            assert elapsed >= 3, elapsed
            transfer("other")
        finally:
            os.kill(paused_pid, signal.SIGCONT)
        lifecycle.wait_command(client, base, rollback_command, "failed")
        command(client, base, rollback)
        assert rejected("crash")
        print(
            "PASS failed restart preserves real forwarding and enforced old bandwidth cap",
            flush=True,
        )

        original_crash = next(item for item in original["inbounds"] if item["tag"] == "crash")
        native.command(
            client, base, "inbounds/manage", {"action": "add", "inbound": original_crash}
        )
        command(client, base, rollback)
        transfer("crash")
        marker = mark("cleanup-interrupt-once")
        payload, _ = preview(inbound_tags=["crash"])
        command_id = queue(client, base, payload)

        def interrupted():
            if marker.exists() or any(
                item["tag"] == "crash" for item in json.loads(live_path.read_text())["inbounds"]
            ):
                return False
            with sqlite3.connect(fixture.root / "state/commands.sqlite") as db:
                return db.execute(
                    "SELECT phase FROM node_cleanup_jobs WHERE id=?", (payload["operation_id"],)
                ).fetchone() == ("prepared",)

        runtime.poll("cleanup interrupted after actual runtime update", interrupted)
        os.kill(int(fixture.properties()["MainPID"]), signal.SIGKILL)
        runtime.poll("Agent restarts and recovers cleanup", fixture.ready)
        completed = lifecycle.wait_command(client, base, command_id)
        assert completed["result_body"]["node_cleanup"]["applied"]
        assert rejected("crash")
        transfer("other")
        fixture.cli("upgrade", "--wheel", args.wheel)
        runtime.poll("verified wheel restored", fixture.ready)
        status = command(
            client, base, {"action": "status", "operation_id": payload["operation_id"]}
        )
        assert status["result_body"]["node_cleanup"]["applied"]
        with sqlite3.connect(fixture.root / "state/commands.sqlite") as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM node_cleanup_jobs WHERE phase='prepared'"
                ).fetchone()[0]
                == 0
            )
            assert all(row[0] == "{}" for row in db.execute("SELECT state FROM node_cleanup_jobs"))
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        transfer("other")
        print(
            "PASS process-kill recovery, exact receipt and independently installed final wheel",
            flush=True,
        )


def run(args):
    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, endpoint, ca)

    service.exercise = callback
    service.run(args.wheel, args.xray_archive)
    print("PASS native node cleanup " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument("--transport", choices=["websocket", "http"], default="websocket")
    run(parser.parse_args())
