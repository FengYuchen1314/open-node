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

    def initialize(self, xray, config, agent_config):
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
        self.xray_config = self.root / "config/xray.json"
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


def exercise_mode(work, fixture, xray, client, echo_port, mode, endpoint, ca):
    created = client.post("/api/v1/servers", json={"name": "external-systemd-" + mode})
    created.raise_for_status()
    base = "/api/v1/servers/" + created.json()["server"]["id"]
    user_id, new_id = str(uuid4()), str(uuid4())
    port, stats_port = runtime.free_port(), runtime.free_port()
    config = {
        "log": {"loglevel": "warning"},
        "api": {
            "listen": f"127.0.0.1:{stats_port}",
            "services": ["StatsService"],
            "tag": "api",
        },
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}
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
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }
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
            mode + " external user stats reach control plane",
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
