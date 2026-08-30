"""Verify a non-root Agent controlling a separately owned Xray systemd service."""

import argparse
import importlib.util
import json
import os
import pwd
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


lifecycle = module("external_lifecycle", "smoke-agent-lifecycle.py")
service = lifecycle.service
runtime = service.runtime


def run_command(*args, check=True):
    result = subprocess.run(
        list(map(str, args)),
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": ""},
    )
    if check and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


class Fixture:
    def __init__(self, python):
        self.suffix = uuid4().hex[:8]
        self.root = Path("/opt") / ("open-node-external-smoke-" + self.suffix)
        self.user = "open-node-ext-" + self.suffix
        self.xray_unit = "open-node-external-xray-" + self.suffix + ".service"
        self.agent_unit = "open-node-external-agent-" + self.suffix + ".service"
        self.other_unit = "open-node-external-other-" + self.suffix + ".service"
        self.python = python
        self.units = []
        self.user_created = False
        self.root_created = False
        self.granted = False

    def unit(self, name, content):
        path = Path("/etc/systemd/system") / name
        with path.open("x") as output:
            output.write(content)
        path.chmod(0o644)
        self.units.append(path)
        return path

    def private(self, path, value):
        runtime.write_private(path, value)
        os.chown(path, self.uid, self.gid)

    def initialize(self, xray, config, agent_config, *, config_name="xray.json"):
        capabilities = (
            "AmbientCapabilities=CAP_NET_BIND_SERVICE\n"
            "CapabilityBoundingSet=CAP_NET_BIND_SERVICE\n"
            if agent_config["connection_mode"] == "http"
            else ""
        )
        self.root.mkdir(mode=0o755)
        self.root_created = True
        run_command(
            "useradd",
            "--system",
            "--user-group",
            "--home-dir",
            self.root / "state",
            "--shell",
            "/usr/sbin/nologin",
            self.user,
        )
        self.user_created = True
        user = pwd.getpwnam(self.user)
        self.uid, self.gid = user.pw_uid, user.pw_gid
        for name in ("config", "state"):
            path = self.root / name
            path.mkdir(mode=0o700)
            os.chown(path, self.uid, self.gid)
        self.binary = self.root / "xray"
        shutil.copyfile(xray, self.binary)
        self.binary.chmod(0o755)
        self.xray_config = self.root / "config" / config_name
        self.private(self.xray_config, config)
        self.private(self.root / "config/other.json", config)
        self.agent_config = self.root / "config/agent.json"
        ca = self.root / "config/ca.pem"
        shutil.copyfile(agent_config.pop("ca_file"), ca)
        ca.chmod(0o644)
        os.chown(ca, self.uid, self.gid)
        self.private(
            self.agent_config,
            {
                **agent_config,
                "runtime_mode": "systemd",
                "xray_service": self.xray_unit,
                "xray_binary": str(self.binary),
                "xray_config": str(self.xray_config),
                "state_dir": str(self.root / "state"),
                "ca_file": str(ca),
            },
        )
        self.xray_text = (
            "[Unit]\nDescription=Disposable external Xray\nStartLimitIntervalSec=0\n"
            "[Service]\nType=exec\n"
            f"User={self.user}\nGroup={self.user}\n"
            f"WorkingDirectory={self.root / 'config'}\n"
            f"ExecStart={self.binary} run -config {self.xray_config}\n"
            'Environment="XRAY_LOCATION_ASSET=/opt/fixture assets"\n'
            "NoNewPrivileges=true\nRestart=no\n" + capabilities
        )
        self.xray_path = self.unit(self.xray_unit, self.xray_text)
        self.unit(
            self.other_unit,
            f"[Service]\nType=exec\nUser={self.user}\nExecStart=/usr/bin/sleep 3600\n",
        )
        self.unit(
            self.agent_unit,
            "[Service]\nType=exec\n"
            f"User={self.user}\nGroup={self.user}\n"
            f"ExecStart={self.python} -m open_node_agent --config {self.agent_config}\n"
            "NoNewPrivileges=true\nKillMode=control-group\nTimeoutStopSec=10\n"
            + capabilities,
        )
        run_command("systemctl", "daemon-reload")

    def access(self, action, check=True, **overrides):
        args = [
            self.python,
            "-m",
            "open_node_agent.systemd_access",
            action,
            "--user",
            self.user,
            "--service",
            overrides.get("service", self.xray_unit),
        ]
        if action == "grant":
            args.extend(
                [
                    "--xray-binary",
                    overrides.get("binary", self.binary),
                    "--xray-config",
                    self.xray_config,
                ]
            )
            if overrides.get("allow_takeover"):
                args.append("--allow-takeover")
        result = run_command(*args, check=check)
        if action == "grant" and result.returncode == 0:
            self.granted = True
            self.rule = Path(json.loads(result.stdout)["rule"])
        if action == "revoke" and result.returncode == 0:
            self.granted = False
        return result

    def as_user(self, *args):
        return run_command("runuser", "-u", self.user, "--", *args, check=False)

    def pid(self, unit=None):
        return int(
            run_command(
                "systemctl",
                "show",
                unit or self.xray_unit,
                "--property=MainPID",
                "--value",
            ).stdout.strip()
        )

    def health(self):
        try:
            return json.loads((self.root / "state/health.json").read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def ready(self, *, bound=True):
        health = self.health()
        return (
            health.get("pid") == self.pid(self.agent_unit)
            and health.get("connected")
            and health.get("runtime_ready") is bound
            and time.time() - health.get("observed_at", 0) < 5
        )

    def cleanup(self):
        if self.granted:
            self.access("revoke")
        for path in reversed(self.units):
            run_command("systemctl", "stop", path.name, check=False)
            path.unlink()
        if self.units:
            run_command("systemctl", "daemon-reload")
            for path in self.units:
                run_command("systemctl", "reset-failed", path.name, check=False)
        if self.user_created:
            run_command("userdel", self.user)
        if self.root_created:
            assert self.root.parent == Path("/opt")
            assert self.root.name == "open-node-external-smoke-" + self.suffix
            shutil.rmtree(self.root)


def queue_operation(client, base, operation, body=None, *, expected="succeeded"):
    request = {} if body is None else {"json": body}
    command = (
        client.post(base + "/operations/" + operation, **request)
        .raise_for_status()
        .json()["command"]
    )
    return lifecycle.wait_command(client, base, command, expected=expected)


def exercise_mode(work, fixture, xray, client, echo_port, mode, endpoint, ca):
    created = client.post("/api/v1/servers", json={"name": "external-systemd-" + mode})
    created.raise_for_status()
    base = "/api/v1/servers/" + created.json()["server"]["id"]
    user_id, new_id = str(uuid4()), str(uuid4())
    port, stats_port = runtime.free_port(), runtime.free_port()
    api_mode = "direct" if mode == "websocket" else "routed"
    api = {
        "listen": f"127.0.0.1:{stats_port}",
        "services": ["StatsService"],
        "tag": "api",
    }
    api_inbounds = []
    routing = None
    if api_mode == "routed":
        api.pop("listen")
        api_inbounds = [
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": stats_port,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            }
        ]
        routing = {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["api"],
                    "outboundTag": "api",
                }
            ],
        }
    config = {
        "log": {"access": "none", "loglevel": "warning"},
        "dns": {
            "hosts": {"preserved.invalid": "127.0.0.1"},
            "servers": ["localhost"],
        },
        "api": api,
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "connIdle": 300,
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "inbounds": [
            {
                "tag": "vless",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [{"id": user_id, "email": "external", "level": 0}],
                },
            },
            *api_inbounds,
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    if routing is not None:
        config["routing"] = routing
    fixture.initialize(
        xray,
        config,
        {
            "master_url": endpoint,
            "token": created.json()["agent_token"],
            "ca_file": ca,
            "connection_mode": mode,
            "auto_start": True,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
        },
    )

    def queue(path, body=None, method="POST", expected="succeeded"):
        result = (
            client.post(
                base + "/commands",
                json={
                    "method": method,
                    "path": "/api/child/" + path,
                    "body": body,
                    "timeout_ms": 30000,
                },
            )
            .raise_for_status()
            .json()["command"]
        )
        return lifecycle.wait_command(client, base, result, expected=expected)

    run_command("systemctl", "start", fixture.xray_unit)
    runtime.poll(mode + " independently started Xray", lambda: runtime.port_open(port))
    pid = fixture.pid()
    denied = fixture.as_user(
        "systemctl", "--no-ask-password", "stop", fixture.xray_unit
    )
    assert denied.returncode and fixture.pid() == pid
    assert fixture.access("grant", binary="/usr/bin/true", check=False).returncode != 0
    fixture.xray_path.chmod(0o664)
    try:
        assert fixture.access("grant", check=False).returncode != 0
    finally:
        fixture.xray_path.chmod(0o644)
    alias = fixture.xray_path.with_name(
        "open-node-external-alias-" + fixture.suffix + ".service"
    )
    alias.symlink_to(fixture.xray_path.name)
    fixture.units.append(alias)
    run_command("systemctl", "daemon-reload")
    assert fixture.access("grant", service=alias.name, check=False).returncode != 0
    fixture.access("grant")
    before = fixture.rule.read_bytes()
    fixture.access("grant")
    assert (
        fixture.rule.read_bytes() == before
        and fixture.rule.stat().st_mode & 0o777 == 0o644
    )
    fixture.rule.write_bytes(before + b"// host edit\n")
    assert fixture.access("revoke", check=False).returncode != 0
    assert fixture.rule.read_bytes() == before + b"// host edit\n"
    fixture.rule.write_bytes(before)
    run_command("systemctl", "start", fixture.agent_unit)
    runtime.poll(
        mode + " non-root Agent connects with verified external binding", fixture.ready
    )
    status = queue("services/status", method="GET")["result_body"]["xray"]
    assert (
        status["running"] and status["mode"] == "systemd" and status["message"] is None
    )
    assert fixture.pid() == pid

    # The grant cannot manage another service or mutate the manager itself.
    assert fixture.as_user(
        "systemctl", "--no-ask-password", "start", fixture.other_unit
    ).returncode
    assert fixture.pid(fixture.other_unit) == 0
    assert fixture.as_user("systemctl", "--no-ask-password", "daemon-reload").returncode
    assert fixture.as_user(
        "systemctl", "--no-ask-password", "enable", fixture.other_unit
    ).returncode
    print(
        f"PASS {mode} scoped polkit denies unrelated units and manager changes",
        flush=True,
    )

    with runtime.proxy_client(work, xray, port, user_id) as socks:
        runtime.poll(
            mode + " external service forwards real VLESS",
            lambda: runtime.forwards(socks, echo_port),
        )
        runtime.poll(
            mode + " " + api_mode + " API user stats reach control plane",
            lambda: client.get(base + "/telemetry/latest").json().get("latest"),
            lambda row: (
                row
                and (row.get("stats") or {})
                .get("user", {})
                .get("external", {})
                .get("downlink", 0)
                >= len(runtime.RESPONSE_BODY)
            ),
        )
        system_read = queue_operation(
            client, base, "xray/system-config/read"
        )["result_body"]
        description = system_read["config"]
        assert description["api_mode"] == api_mode
        assert description["grpc_enabled"] is True
        assert description["grpc_port"] == stats_port
        assert description["stats_enabled"] is True
        assert description["fixed_stats_address"] is None
        assert description["writable"] is True
        assert description["read_only_reason"] is None
        assert description["log_level"] == "warning"
        assert description["dns"] == config["dns"]
        assert description["policy"] == config["policy"]

        system_payload = {
            key: description[key]
            for key in (
                "log_level",
                "dns",
                "policy",
                "metrics_enabled",
                "metrics_listen",
                "stats_enabled",
                "grpc_enabled",
                "grpc_port",
            )
        }
        system_payload["log_level"] = "info"
        system_payload["dns"] = json.loads(json.dumps(description["dns"]))
        system_payload["dns"]["queryStrategy"] = "UseIPv4"
        expected_policy = json.loads(json.dumps(description["policy"]))
        expected_policy["levels"]["0"]["connIdle"] = 301
        system_payload["policy"] = expected_policy
        system_payload["expected_sha256"] = system_read["sha256"]
        before_system_pid = fixture.pid()
        system_written = queue_operation(
            client, base, "xray/system-config/write", system_payload
        )["result_body"]
        assert fixture.pid() != before_system_pid
        saved = json.loads(fixture.xray_config.read_text())
        assert saved["log"] == {"access": "none", "loglevel": "info"}
        assert saved["dns"]["hosts"] == config["dns"]["hosts"]
        assert saved["dns"]["servers"] == config["dns"]["servers"]
        assert saved["dns"]["queryStrategy"] == "UseIPv4"
        assert saved["policy"] == expected_policy
        assert saved["policy"]["levels"]["0"]["statsUserUplink"] is True
        assert saved["policy"]["system"]["statsInboundDownlink"] is True
        assert saved["api"] == config["api"]
        assert saved["inbounds"] == config["inbounds"]
        assert saved["outbounds"] == config["outbounds"]
        if api_mode == "routed":
            assert saved["routing"] == config["routing"]
        assert system_written["config"]["log_level"] == "info"
        assert system_written["config"]["api_mode"] == api_mode
        runtime.poll(
            mode + " system-config write restarts Xray and preserves forwarding",
            lambda: runtime.forwards(socks, echo_port),
        )

        files = queue_operation(
            client, base, "xray/config-files/list"
        )["result_body"]["files"]["main"]
        assert len(files) == 1
        assert files[0]["name"] == fixture.xray_config.name
        assert files[0]["active"] is True
        assert files[0]["writable"] is True
        assert files[0]["read_only_reason"] is None
        file_read = queue_operation(
            client,
            base,
            "xray/config-files/read",
            {"file": fixture.xray_config.name},
        )["result_body"]
        assert file_read["sha256"] == files[0]["sha256"]
        assert json.loads(file_read["content"]) == saved
        assert file_read["active"] is True
        assert file_read["writable"] is True

        stale_bytes = fixture.xray_config.read_bytes() + b"\n"
        fixture.xray_config.write_bytes(stale_bytes)
        stale = queue_operation(
            client,
            base,
            "xray/config-files/write",
            {
                "file": fixture.xray_config.name,
                "content": json.loads(file_read["content"]),
                "expected_sha256": file_read["sha256"],
            },
            expected="failed",
        )
        assert "changed since it was read" in stale["result_error"]
        assert fixture.xray_config.read_bytes() == stale_bytes

        fresh_file = queue_operation(
            client,
            base,
            "xray/config-files/read",
            {"file": fixture.xray_config.name},
        )["result_body"]
        assert fresh_file["sha256"] != file_read["sha256"]
        replacement = json.loads(fresh_file["content"])
        replacement["outbounds"][0]["settings"] = {"domainStrategy": "UseIP"}
        telemetry_before_write = (
            client.get(base + "/telemetry/latest")
            .raise_for_status()
            .json()["latest"]["id"]
        )
        before_file_pid = fixture.pid()
        file_written = queue_operation(
            client,
            base,
            "xray/config-files/write",
            {
                "file": fixture.xray_config.name,
                "content": replacement,
                "expected_sha256": fresh_file["sha256"],
            },
        )["result_body"]
        assert fixture.pid() != before_file_pid
        assert file_written["active"] is True and file_written["writable"] is True
        assert json.loads(fixture.xray_config.read_text()) == replacement
        runtime.poll(
            mode + " config-file write restarts Xray and preserves forwarding",
            lambda: runtime.forwards(socks, echo_port),
        )
        runtime.poll(
            mode + " " + api_mode + " stats survive workspace restarts",
            lambda: client.get(base + "/telemetry/latest").json().get("latest"),
            lambda row: (
                row
                and row["id"] != telemetry_before_write
                and (row.get("stats") or {})
                .get("user", {})
                .get("external", {})
                .get("downlink", 0)
                >= len(runtime.RESPONSE_BODY)
            ),
        )
        print(
            f"PASS {mode} {api_mode} system config, config files and live restart",
            flush=True,
        )

        added = (
            client.post(
                base + "/operations/batch-apply",
                json={
                    "inbound_clients": [
                        {
                            "tag": "vless",
                            "client": {
                                "id": new_id,
                                "email": "new-external",
                                "level": 0,
                            },
                        }
                    ]
                },
            )
            .raise_for_status()
            .json()["command"]
        )
        lifecycle.wait_command(client, base, added)
        with runtime.proxy_client(work, xray, port, new_id) as new_socks:
            runtime.poll(
                mode + " provisioned user forwards through external Xray",
                lambda: runtime.forwards(new_socks, echo_port),
            )
        healthy = fixture.xray_config.read_bytes()
        invalid = json.loads(healthy)
        invalid["inbounds"][0]["protocol"] = "invalid-protocol"
        queue("xray/config", {"config": invalid}, expected="failed")
        assert fixture.xray_config.read_bytes() == healthy
        queue(
            "inbounds",
            {
                "action": "add",
                "inbound": {
                    "tag": "occupied",
                    "listen": "127.0.0.1",
                    "port": echo_port,
                    "protocol": "socks",
                    "settings": {"auth": "noauth"},
                },
            },
            expected="failed",
        )
        assert fixture.xray_config.read_bytes() == healthy
        runtime.poll(
            mode + " failed restart restores configuration and live traffic",
            lambda: runtime.forwards(socks, echo_port),
        )

        before_pid = fixture.pid()
        run_command("systemctl", "restart", fixture.agent_unit)
        runtime.poll(mode + " Agent restart reconnects", fixture.ready)
        assert fixture.pid() == before_pid
        runtime.poll(
            mode + " Agent restart leaves external process untouched",
            lambda: runtime.forwards(socks, echo_port),
        )

        fixture.xray_path.write_text(
            fixture.xray_text.replace(
                str(fixture.xray_config), str(fixture.root / "config/other.json")
            )
        )
        run_command("systemctl", "daemon-reload")
        runtime.poll(
            mode + " changed unit reports unavailable while Agent remains connected",
            lambda: fixture.ready(bound=False),
        )
        scan = queue("scan")["result_body"]
        assert not scan["xray_running"] and scan["message"] and not scan["inbounds"]
        queue(
            "services/control", {"service": "xray", "action": "stop"}, expected="failed"
        )
        queue("xray/config", {"config": config}, expected="failed")
        assert (
            fixture.pid() == before_pid and fixture.xray_config.read_bytes() == healthy
        )
        runtime.poll(
            mode + " mismatched service is not stopped or rewritten",
            lambda: runtime.forwards(socks, echo_port),
        )
        fixture.xray_path.write_text(fixture.xray_text)
        run_command("systemctl", "daemon-reload")
        runtime.poll(
            mode + " restored unit binding recovers without Agent restart",
            fixture.ready,
        )

        queue("services/control", {"service": "xray", "action": "stop"})
        assert fixture.pid() == 0 and not runtime.port_open(port)
        run_command("systemctl", "restart", fixture.agent_unit)
        runtime.poll(mode + " stopped intent survives Agent restart", fixture.ready)
        time.sleep(6)
        assert not runtime.port_open(port)
        queue("services/control", {"service": "xray", "action": "start"})
        runtime.poll(
            mode + " explicit service start restores forwarding",
            lambda: runtime.forwards(socks, echo_port),
        )
        queue("xray/remove", expected="failed")
        assert fixture.binary.exists() and fixture.xray_path.exists()
        before_pid = fixture.pid()
        run_command("systemctl", "stop", fixture.agent_unit)
        assert fixture.pid() == before_pid
        fixture.access("revoke")
        runtime.poll(
            mode + " revoked account cannot control the service",
            lambda: (
                fixture.as_user(
                    "systemctl", "--no-ask-password", "stop", fixture.xray_unit
                ).returncode
                != 0
            ),
        )
        assert fixture.pid() == before_pid
        runtime.poll(
            mode + " Agent removal and grant revocation preserve host-owned Xray",
            lambda: runtime.forwards(socks, echo_port),
        )
    print(
        f"PASS {mode} external systemd ownership, controls, recovery and real traffic",
        flush=True,
    )


def exercise_jsonc_read_only(
    work, fixture, xray, client, echo_port, mode, endpoint, ca
):
    created = (
        client.post(
            "/api/v1/servers", json={"name": "external-jsonc-" + mode}
        )
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    user_id, port = str(uuid4()), runtime.free_port()
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "vless",
                "settings": {
                    "decryption": "none",
                    "clients": [
                        {"id": user_id, "email": "external-jsonc", "level": 0}
                    ],
                },
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
    fixture.initialize(
        xray,
        config,
        {
            "master_url": endpoint,
            "token": created["agent_token"],
            "ca_file": ca,
            "connection_mode": mode,
            "auto_start": True,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
        },
        config_name="xray.jsonc",
    )
    fixture.xray_config.write_text(
        "// comments must survive a read-only workspace\n"
        + fixture.xray_config.read_text()
        + "\n"
    )
    original = fixture.xray_config.read_bytes()
    run_command("systemctl", "start", fixture.xray_unit)
    runtime.poll(mode + " JSONC Xray starts", lambda: runtime.port_open(port))
    fixture.access("grant")
    run_command("systemctl", "start", fixture.agent_unit)
    runtime.poll(mode + " JSONC Agent connects", fixture.ready)

    with runtime.proxy_client(work, xray, port, user_id) as socks:
        runtime.poll(
            mode + " JSONC primary forwards real VLESS",
            lambda: runtime.forwards(socks, echo_port),
        )
        files = queue_operation(
            client, base, "xray/config-files/list"
        )["result_body"]["files"]["main"]
        assert len(files) == 1 and files[0]["name"] == "xray.jsonc"
        assert files[0]["active"] is True
        assert files[0]["writable"] is False
        assert "JSONC" in files[0]["read_only_reason"]
        read = queue_operation(
            client,
            base,
            "xray/config-files/read",
            {"file": "xray.jsonc"},
        )["result_body"]
        assert read["content"].startswith("// comments must survive")
        assert read["writable"] is False
        assert "JSONC" in read["read_only_reason"]
        before_pid = fixture.pid()
        rejected = queue_operation(
            client,
            base,
            "xray/config-files/write",
            {
                "file": "xray.jsonc",
                "content": config,
                "expected_sha256": read["sha256"],
            },
            expected="failed",
        )
        assert "read-only" in rejected["result_error"]
        assert fixture.xray_config.read_bytes() == original
        assert fixture.pid() == before_pid
        runtime.poll(
            mode + " rejected JSONC write leaves forwarding untouched",
            lambda: runtime.forwards(socks, echo_port),
        )

    run_command("systemctl", "stop", fixture.agent_unit)
    fixture.access("revoke")
    print(f"PASS {mode} JSONC primary is visible and read-only", flush=True)


def run(args):
    def exercise(work, unused, wheel, xray, client, backend, echo_port):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            for mode in ("websocket", "http"):
                fixture = Fixture(args.agent_python)
                try:
                    exercise_mode(
                        work, fixture, xray, client, echo_port, mode, endpoint, ca
                    )
                except BaseException:
                    for unit in fixture.units:
                        print(
                            run_command(
                                "journalctl",
                                "-u",
                                unit.name,
                                "-n",
                                "50",
                                "--no-pager",
                                check=False,
                            ).stdout,
                            flush=True,
                        )
                    print("HEALTH", fixture.health(), flush=True)
                    raise
                finally:
                    fixture.cleanup()
                jsonc_fixture = Fixture(args.agent_python)
                try:
                    exercise_jsonc_read_only(
                        work,
                        jsonc_fixture,
                        xray,
                        client,
                        echo_port,
                        mode,
                        endpoint,
                        ca,
                    )
                except BaseException:
                    for unit in jsonc_fixture.units:
                        print(
                            run_command(
                                "journalctl",
                                "-u",
                                unit.name,
                                "-n",
                                "50",
                                "--no-pager",
                                check=False,
                            ).stdout,
                            flush=True,
                        )
                    print("HEALTH", jsonc_fixture.health(), flush=True)
                    raise
                finally:
                    jsonc_fixture.cleanup()

    service.exercise = exercise
    service.run(args.wheel, args.xray_archive)
    print("PASS independent external Xray systemd smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    run(parser.parse_args())
