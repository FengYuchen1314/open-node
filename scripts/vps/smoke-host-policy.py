"""Real non-root host-policy changes, old helpers, and crash recovery on the VPS."""

import argparse
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

SPEC = importlib.util.spec_from_file_location(
    "policy_diagnostics", Path(__file__).with_name("smoke-diagnostics.py")
)
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)
lifecycle, service, runtime = (
    diagnostics.lifecycle,
    diagnostics.service,
    diagnostics.runtime,
)


def run_cli(fixture, *args, bootstrap=None, env=None):
    arguments = list(fixture.arguments)
    if bootstrap:
        arguments[1] = str(bootstrap)
    return subprocess.run(
        [*arguments, *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env=env,
    )


def systemctl(*args, check=True):
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=45,
    ).stdout.strip()


def capabilities(fixture, enabled):
    assert fixture.ready()
    pid = fixture.properties()["MainPID"]
    status = dict(
        line.split(":", 1)
        for line in (Path("/proc") / pid / "status").read_text().splitlines()
        if ":" in line
    )
    mask = (1 << 10) | ((1 << 13) if enabled else 0)
    for key in ("CapEff", "CapBnd", "CapAmb"):
        assert int(status[key], 16) == mask, status
    assert status["NoNewPrivs"].strip() == "1"
    assert set(status["Uid"].split()) == {str(fixture.record()["uid"])}
    assert fixture.record()["uid"] != 0


def probe(client, base, echo, enabled, trace=True):
    reachable, closed = f"127.0.0.1:{echo}", f"127.0.0.1:{runtime.free_port()}"
    result = diagnostics.operation(
        client,
        base,
        "domain-latency",
        {
            "domains": [reachable, closed],
            "allow_icmp": True,
            "timeout_ms": 500,
        },
    )["result_body"]["results"]
    by_target = {row["target"]: row for row in result}
    assert (
        by_target[reachable]["success"] and by_target[reachable]["method"] == "tcp"
    ), result
    assert by_target[closed]["success"] is enabled, result
    if enabled:
        assert by_target[closed]["method"] == "icmp", result
    else:
        assert by_target[closed]["icmp_error"], result
    if trace:
        result = diagnostics.operation(
            client,
            base,
            "network/return-route-test",
            {
                "targets": [{"carrier": "telecom", "host": "127.0.0.1", "port": echo}],
                "timeout_seconds": 10,
            },
        )["result_body"]["results"][0]
        assert result["success"] is enabled, result
        if enabled:
            assert result["reached"] and result["hops"][0]["ip"] == "127.0.0.1", result


def injected_command(fixture, directory, action):
    hooks = directory / action
    hooks.mkdir()
    marker = hooks / "injected"
    script = hooks / "systemctl"
    helper = fixture.unit.removesuffix(".service") + "-lifecycle.service"
    script.write_text(f"""#!/usr/bin/python3
import os, signal, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
marker = Path({str(marker)!r})
if not marker.exists():
    if {action!r} == 'crash' and args == ['daemon-reload']:
        subprocess.run(['/usr/bin/systemctl', *args], check=True)
        marker.touch()
        os.kill(os.getppid(), signal.SIGKILL)
        raise SystemExit(0)
    if {action!r} == 'helper-failure' and args[0] == 'start' and {helper!r} in args:
        marker.touch()
        raise SystemExit(1)
os.execv('/usr/bin/systemctl', ['systemctl', *args])
""")
    script.chmod(0o755)
    return {**os.environ, "PATH": str(hooks) + os.pathsep + os.environ["PATH"]}, marker


def exercise_mode(
    work,
    fixture,
    wheel,
    xray,
    client,
    echo,
    mode,
    endpoint,
    ca,
    assets,
    release_base,
    release_ca,
    args,
):
    directory = work / ("policy-" + mode)
    directory.mkdir()
    created = (
        client.post("/api/v1/servers", json={"name": "policy-" + mode})
        .raise_for_status()
        .json()
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
                        "clients": [{"id": user, "email": "policy-user"}],
                    },
                }
            ],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        },
    )
    installed = run_cli(
        fixture,
        "install",
        "--wheel",
        wheel,
        "--config",
        config,
        "--xray-config",
        xray_config,
        "--xray",
        xray,
        bootstrap=args.previous_bootstrap,
    )
    assert installed.returncode == 0, installed.stderr
    result = run_cli(
        fixture,
        "enable-remote",
        "--release-base-url",
        release_base,
        "--release-ca",
        release_ca,
        bootstrap=args.previous_bootstrap,
    )
    assert result.returncode == 0, result.stderr
    initial = fixture.record()
    initial_config = (fixture.root / "config/agent.json").read_bytes()
    initial_xray = (fixture.root / "config/xray.json").read_bytes()
    helpers = list(initial["lifecycle"]["units"])
    states = {
        unit: systemctl("is-enabled", unit, check=False)
        for unit in [fixture.unit, *helpers]
    }

    def preserved():
        assert (fixture.root / "config/xray.json").read_bytes() == initial_xray
        saved = json.loads((fixture.root / "config/agent.json").read_bytes())
        for key, value in json.loads(initial_config).items():
            if key not in {"nexttrace_binary", "nexttrace_geoip"}:
                assert saved[key] == value, key
        assert fixture.record()["lifecycle"] == initial["lifecycle"]
        for name, digest in initial["lifecycle"]["files"].items():
            assert (
                hashlib.sha256(
                    (fixture.root / "lifecycle" / name).read_bytes()
                ).hexdigest()
                == digest
            )
        assert {
            unit: systemctl("is-enabled", unit, check=False) for unit in states
        } == states
        assert not fixture.record().get("pending")
        assert not fixture.record().get("policy_restore")
        assert not list(fixture.root.glob("policy-*"))
        with runtime.proxy_client(directory, xray, port, user) as socks:
            runtime.poll(
                "VLESS survives host policy update",
                lambda: runtime.forwards(socks, echo),
            )

    capabilities(fixture, False)
    probe(client, base, echo, False)
    enabled = fixture.cli(
        "policy",
        "--network-diagnostics",
        "on",
        "--nexttrace",
        args.nexttrace,
        "--nexttrace-sha256",
        diagnostics.NEXTTRACE_SHA256,
        "--nexttrace-geoip",
        "off",
    )
    assert created["agent_token"] not in enabled.stdout + enabled.stderr
    capabilities(fixture, True)
    probe(client, base, echo, True)
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as listener:
        listener.bind(("::1", 0))
        listener.listen()
        route = diagnostics.operation(
            client,
            base,
            "network/return-route-test",
            {
                "ip_version": 6,
                "timeout_seconds": 10,
                "targets": [
                    {
                        "carrier": "unicom",
                        "host": "::1",
                        "port": listener.getsockname()[1],
                    }
                ],
            },
        )["result_body"]["results"][0]
        assert route["success"] and route["reached"], route
    preserved()
    pid = fixture.properties()["MainPID"]
    fixture.cli("policy", "--network-diagnostics", "on")
    assert fixture.properties()["MainPID"] == pid
    fixture.cli("policy", "--network-diagnostics", "off")
    capabilities(fixture, False)
    probe(client, base, echo, False)
    assert (fixture.root / "runtime/nexttrace").exists()
    preserved()
    print(
        "PASS "
        + mode
        + " old installation/helper, real non-root ICMP/IPv4/IPv6, disable and idempotency",
        flush=True,
    )

    before = (fixture.root / "config/agent.json").read_bytes()
    binary = (fixture.root / "runtime/nexttrace").read_bytes()
    bad_tool = directory / "invalid-nexttrace"
    bad_tool.write_bytes(b"not an executable\n")
    failed = fixture.cli(
        "policy",
        "--network-diagnostics",
        "on",
        "--nexttrace",
        bad_tool,
        "--nexttrace-sha256",
        hashlib.sha256(bad_tool.read_bytes()).hexdigest(),
        check=False,
    )
    assert failed.returncode and "previous host policy restored" in failed.stderr, (
        failed
    )
    assert (fixture.root / "config/agent.json").read_bytes() == before
    assert (fixture.root / "runtime/nexttrace").read_bytes() == binary
    capabilities(fixture, False)
    preserved()

    env, marker = injected_command(fixture, directory, "crash")
    result = run_cli(fixture, "policy", "--network-diagnostics", "on", env=env)
    assert result.returncode < 0 and marker.exists(), result
    pending = fixture.record()
    assert pending["schema"] == 2 and pending["pending"]["kind"] == "policy"
    legacy = run_cli(fixture, "recover", bootstrap=args.previous_bootstrap)
    assert legacy.returncode and "identity does not match" in legacy.stderr, legacy
    assert fixture.record() == pending
    assert fixture.properties()["ActiveState"] == "inactive"
    fixture.cli("recover")
    capabilities(fixture, False)
    preserved()

    env, marker = injected_command(fixture, directory, "helper-failure")
    result = run_cli(fixture, "policy", "--network-diagnostics", "on", env=env)
    assert (
        result.returncode and marker.exists() and "Policy committed" in result.stderr
    ), result
    assert fixture.record()["schema"] == 1 and fixture.record()["policy_restore"]
    assert fixture.record()["network_diagnostics"]
    pid = fixture.properties()["MainPID"]
    fixture.cli("recover")
    assert fixture.properties()["MainPID"] == pid
    capabilities(fixture, True)
    preserved()
    print(
        "PASS "
        + mode
        + " invalid executable rollback, SIGKILL recovery, old bootstrap refusal, helper restart retry",
        flush=True,
    )

    systemctl("disable", fixture.unit)
    systemctl("stop", fixture.unit, *helpers)
    fixture.cli("policy", "--network-diagnostics", "off")
    assert fixture.properties()["ActiveState"] == "inactive"
    assert systemctl("is-enabled", fixture.unit, check=False) == "disabled"
    assert all(
        systemctl("is-active", unit, check=False) == "inactive" for unit in helpers
    )
    systemctl("enable", "--now", fixture.unit)
    systemctl("start", *helpers)
    runtime.poll("manually restarted Agent ready", fixture.ready)
    preserved()

    if mode == "http":
        prefix = """import sys
from pathlib import Path
if '--version' not in sys.argv and '--check' not in sys.argv:
    values = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if int(values['CapEff'], 16) & (1 << 13):
        raise SystemExit('intentional startup failure under new host policy')
"""
        startup = lifecycle.release_wheel(
            wheel, directory / "startup-fault", "0.2.8", main_prefix=prefix
        )
        fixture.cli("upgrade", "--wheel", startup)
        selected = fixture.record()["current"]
        failed = fixture.cli("policy", "--network-diagnostics", "on", check=False)
        assert failed.returncode and "previous host policy restored" in failed.stderr, (
            failed
        )
        assert fixture.record()["current"] == selected
        capabilities(fixture, False)
        fixture.cli("upgrade", "--wheel", wheel)
        preserved()
        print(
            "PASS real systemd startup failure rolls back host permissions and restores readiness",
            flush=True,
        )

    version = "0.2.9"
    package = lifecycle.release_wheel(wheel, directory / "remote-upgrade", version)
    assets[f"/releases/download/agent-v{version}/{package.name}"] = package.read_bytes()
    result = lifecycle.wait_command(
        client,
        base,
        lifecycle.queue(
            client,
            base,
            "upgrade",
            {
                "version": version,
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
            },
        ),
    )
    assert result["result_body"]["current"]["version"] == version
    capabilities(fixture, False)
    preserved()
    print(
        "PASS "
        + mode
        + " stopped/boot-disabled preferences and real remote upgrade through unchanged old helper",
        flush=True,
    )

    diagnostics.operation(
        client, base, "services/control", {"service": "xray", "action": "stop"}
    )
    assert not runtime.port_open(port)
    fixture.cli("policy", "--network-diagnostics", "on")
    stopped = diagnostics.operation(client, base, "scan")["result_body"]
    assert not stopped["xray_running"] and not runtime.port_open(port)
    fixture.cli("policy", "--network-diagnostics", "off")
    assert not runtime.port_open(port)
    diagnostics.operation(
        client, base, "services/control", {"service": "xray", "action": "start"}
    )
    preserved()
    print(
        "PASS "
        + mode
        + " persistent stopped Xray intent across both policy transitions",
        flush=True,
    )


def run(args):
    assert (
        hashlib.sha256(args.nexttrace.read_bytes()).hexdigest()
        == diagnostics.NEXTTRACE_SHA256
    )
    assert "def policy(self" not in args.previous_bootstrap.read_text()

    def exercise(work, first, wheel, xray, client, backend, echo):
        with (
            lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _),
            lifecycle.release_server(work) as (assets, release_base, release_ca),
        ):
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
                        assets,
                        release_base,
                        release_ca,
                        args,
                    )
                finally:
                    if (fixture.root / "installation.json").exists():
                        fixture.cli("recover", check=False)
                    fixture.cleanup()

    service.exercise = exercise
    service.run(args.wheel, args.xray_archive)
    print("PASS real host policy smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nexttrace", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--previous-bootstrap", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    run(parser.parse_args())
