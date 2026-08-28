"""Verify original fork configurations, subscriptions and revocation on the VPS."""

import argparse
import copy
import importlib.util
import json
import os
import pwd
import socket
import struct
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml


def module(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(filename)
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


lifecycle = module("protocol_lifecycle", "smoke-agent-lifecycle.py")
service, runtime = lifecycle.service, lifecycle.runtime


@contextmanager
def udp_echo():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(0.2)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    data, address = listener.recvfrom(65535)
                    listener.sendto(data, address)
                except TimeoutError:
                    pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1]
        finally:
            stop.set()
            thread.join(3)


def read_exact(connection, size):
    data = b""
    while len(data) < size:
        block = connection.recv(size - len(data))
        if not block:
            raise ConnectionError("SOCKS control connection closed")
        data += block
    return data


def udp_forwards(socks_port, echo_port):
    try:
        with socket.create_connection(("127.0.0.1", socks_port), timeout=3) as control:
            control.sendall(b"\x05\x01\x00")
            assert read_exact(control, 2) == b"\x05\x00"
            control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
            header = read_exact(control, 4)
            assert header[:2] == b"\x05\x00"
            if header[3] == 1:
                read_exact(control, 4)
            elif header[3] == 4:
                read_exact(control, 16)
            else:
                read_exact(control, read_exact(control, 1)[0])
            relay = struct.unpack("!H", read_exact(control, 2))[0]
            payload = ("protocol-udp-" + uuid4().hex).encode()
            frame = b"\x00\x00\x00\x01" + socket.inet_aton("127.0.0.1")
            frame += struct.pack("!H", echo_port) + payload
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
                datagram.settimeout(3)
                datagram.sendto(frame, ("127.0.0.1", relay))
                returned, _ = datagram.recvfrom(65535)
                return returned == frame
    except (OSError, AssertionError):
        return False


@contextmanager
def proxy_client(work, args, proxy, ca):
    directory = work / ("client-" + uuid4().hex[:8])
    directory.mkdir()
    socks = runtime.free_port()
    config = directory / "client.json"
    if proxy["type"] == "snell" and proxy.get("version") == 6:
        # Mihomo has no Snell v6; consume the same subscribed endpoint and PSK.
        runtime.write_private(
            config,
            {
                "log": {"loglevel": "warning"},
                "inbounds": [
                    {
                        "listen": "127.0.0.1",
                        "port": socks,
                        "protocol": "socks",
                        "settings": {"auth": "noauth", "udp": True},
                    }
                ],
                "outbounds": [
                    {
                        "protocol": "snell",
                        "settings": {
                            "address": proxy["server"],
                            "port": proxy["port"],
                            "psk": proxy["psk"],
                            "version": 6,
                            "v6Mode": proxy["mode"],
                        },
                    }
                ],
            },
        )
        command = [str(args.reference), "run", "-config", str(config)]
    else:
        runtime.write_private(
            config,
            {
                "socks-port": socks,
                "bind-address": "127.0.0.1",
                "allow-lan": False,
                "mode": "rule",
                "log-level": "warning",
                "ipv6": False,
                "proxies": [proxy],
                "rules": ["MATCH," + proxy["name"]],
            },
        )
        command = [str(args.mihomo), "-d", str(directory), "-f", str(config)]
    env = {**os.environ, "SSL_CERT_FILE": str(ca)}
    with runtime.process(directory, "proxy", command, env=env) as child:
        try:
            runtime.poll("protocol client starts", lambda: runtime.port_open(socks))
            assert child.poll() is None
            yield socks
        except BaseException:
            print((directory / "proxy.log").read_text()[-12000:], flush=True)
            raise


def check_traffic(work, args, proxy, ca, tcp_port, udp_port, label, allowed=True):
    with proxy_client(work, args, proxy, ca) as socks:
        if allowed:
            runtime.poll(label + " TCP", lambda: runtime.forwards(socks, tcp_port))
            if proxy.get("udp"):
                runtime.poll(label + " UDP", lambda: udp_forwards(socks, udp_port))
        else:
            assert not runtime.forwards(socks, tcp_port), (
                label + " TCP still authorized"
            )
            if proxy.get("udp"):
                assert not udp_forwards(socks, udp_port), (
                    label + " UDP still authorized"
                )
            print("PASS " + label + " rejects supported traffic", flush=True)


def configuration(work):
    cert, key, _ = lifecycle.nginx_fixture.certificate()
    ca = work / "protocol-ca.pem"
    ca.write_text(cert)
    variants = [
        ("anytls", {}),
        ("snell4", {"version": 4}),
        ("snell4-http", {"version": 4, "obfsMode": "http", "obfsHost": "example.org"}),
        ("snell5-tls", {"version": 5, "obfsMode": "tls", "obfsHost": "example.org"}),
        ("snell6", {"version": 6, "v6Mode": "default"}),
        ("snell6-unshaped", {"version": 6, "v6Mode": "unshaped"}),
        ("mieru-tcp", {"transport": "tcp"}),
        ("mieru-udp", {"transport": "udp"}),
    ]
    inbounds, proxies = [], {}
    for tag, options in variants:
        protocol = "snell" if tag.startswith("snell") else tag.split("-")[0]
        secret = str(uuid4())
        user = {"email": "original-" + tag, "level": 0}
        settings = {"users": [user]}
        proxy = {
            "name": tag,
            "type": protocol,
            "server": "127.0.0.1",
            "port": runtime.free_port(),
            "udp": True,
        }
        if protocol == "snell":
            user.update({"psk": secret, **options})
            proxy.update({"psk": secret, "version": options["version"]})
            if options["version"] == 6:
                proxy["mode"] = options["v6Mode"]
            elif "obfsMode" in options:
                proxy["obfs-opts"] = {
                    "mode": options["obfsMode"],
                    "host": options["obfsHost"],
                }
        else:
            user["password"] = secret
            proxy["password"] = secret
            if protocol == "mieru":
                proxy["udp"] = False
                user["username"] = "original"
                settings.update(options)
                proxy.update(
                    {"username": "original", "transport": options["transport"].upper()}
                )
            else:
                proxy["sni"] = "localhost"
        inbound = {
            "tag": tag,
            "protocol": protocol,
            "listen": "127.0.0.1",
            "port": proxy["port"],
            "settings": settings,
        }
        if protocol == "anytls":
            inbound["streamSettings"] = {
                "network": "tcp",
                "security": "tls",
                "tlsSettings": {
                    "serverName": "localhost",
                    "certificates": [
                        {"certificate": cert.splitlines(), "key": key.splitlines()},
                    ],
                },
            }
        inbounds.append(inbound)
        proxies[tag] = proxy
    stats_port = runtime.free_port()
    return (
        {
            "log": {"loglevel": "warning"},
            "api": {
                "tag": "api",
                "listen": f"127.0.0.1:{stats_port}",
                "services": ["StatsService"],
            },
            "stats": {},
            "policy": {
                "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}
            },
            "inbounds": inbounds,
            "outbounds": [
                {"protocol": "freedom", "tag": "direct"},
                {"protocol": "blackhole", "tag": "preserved"},
            ],
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "domain": ["full:blocked.invalid"],
                        "outboundTag": "preserved",
                    }
                ]
            },
        },
        proxies,
        ca,
        stats_port,
    )


def exercise_mode(
    work, fixture, args, wheel, stock, client, endpoint, ca, echo, udp, mode
):
    directory = work / mode
    directory.mkdir()
    original, proxies, protocol_ca, stats_port = configuration(directory)
    xray_config = directory / "original.json"
    runtime.write_private(xray_config, original)
    original_bytes = xray_config.read_bytes()
    with runtime.process(
        directory,
        "original-core",
        [
            str(args.reference),
            "run",
            "-config",
            str(xray_config),
        ],
    ):
        for tag, proxy in proxies.items():
            check_traffic(
                directory,
                args,
                proxy,
                protocol_ca,
                echo,
                udp,
                mode + " original " + tag,
            )

    created = (
        client.post("/api/v1/servers", json={"name": "protocol-" + mode})
        .raise_for_status()
        .json()
    )
    base = "/api/v1/servers/" + created["server"]["id"]
    config = directory / "agent.json"
    runtime.write_private(
        config,
        {
            "master_url": endpoint,
            "ca_file": str(ca),
            "token": created["agent_token"],
            "connection_mode": mode,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
            "stats_address": f"127.0.0.1:{stats_port}",
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
        args.xray,
    )
    assert fixture.ready()
    assert pwd.getpwnam(fixture.user).pw_uid != 0
    saved = fixture.root / "config/xray.json"
    assert json.loads(saved.read_text()) == original

    def queue(path, body=None, expected="succeeded", method="POST"):
        command = (
            client.post(
                base + "/commands",
                json={
                    "method": method,
                    "path": "/api/child/" + path,
                    "body": body,
                    "timeout_ms": 75000,
                },
            )
            .raise_for_status()
            .json()["command"]
        )
        return lifecycle.wait_command(client, base, command, expected)

    queue("scan")
    imported = (
        client.post(
            base + "/xray/runtime/nodes/import",
            json={
                "host": "127.0.0.1",
            },
        )
        .raise_for_status()
        .json()
    )
    assert imported["created_count"] == len(proxies), imported
    nodes = imported["created_nodes"]
    by_name = {node["name"]: node["inbound_tag"] for node in nodes}
    for node in nodes:
        assert node["config"]["port"] == proxies[node["inbound_tag"]]["port"]
        assert "psk" not in node["client_template"]
        assert "password" not in node["client_template"]
    username = "protocol-" + mode
    client.post("/api/v1/users", json={"username": username}).raise_for_status()
    plan = (
        client.post(
            "/api/v1/plans",
            json={
                "name": "protocol-plan-" + mode,
                "traffic_limit_gb": 64,
                "node_ids": [node["id"] for node in nodes],
            },
        )
        .raise_for_status()
        .json()["plan"]
    )

    def assign():
        assigned = (
            client.post(
                f"/api/v1/users/{username}/plan",
                json={
                    "plan_id": plan["id"],
                    "queue_agent_commands": True,
                    "no_restart": False,
                    "command_timeout_ms": 30000,
                },
            )
            .raise_for_status()
            .json()
        )
        for command in assigned["commands"]:
            lifecycle.wait_command(client, base, command)
        return {
            item["tag"]: item["client"]
            for item in assigned["provisioning_batches"][0]["body"]["inbound_clients"]
        }

    users = assign()
    token = (
        client.post(f"/api/v1/users/{username}/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    subscription = yaml.safe_load(
        client.get(f"/api/v1/subscribe/{token}").raise_for_status().text
    )
    subscribed = {by_name[proxy["name"]]: proxy for proxy in subscription["proxies"]}
    assert set(subscribed) == set(proxies)
    for tag, proxy in subscribed.items():
        check_traffic(
            directory,
            args,
            proxy,
            protocol_ca,
            echo,
            udp,
            mode + " subscription " + tag,
        )
        runtime.poll(
            mode + " user statistics " + tag,
            lambda: client.get(base + "/telemetry/latest").json().get("latest"),
            lambda row, tag=tag: (
                row
                and (row.get("stats") or {})
                .get("user", {})
                .get(users[tag]["email"], {})
                .get("downlink", 0)
                > 0
            ),
        )

    before = saved.read_bytes()
    invalid = copy.deepcopy(original)
    invalid["inbounds"][0]["settings"]["users"][0]["password"] = ""
    queue("xray/config", {"config": invalid}, expected="failed")
    assert saved.read_bytes() == before
    # Stock Xray cannot parse fork protocols; migration must not overwrite this file.
    validated = subprocess.run(
        [str(stock), "run", "-test", "-config", str(saved)],
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert validated.returncode != 0
    assert saved.read_bytes() == before
    agent_pid = int(fixture.properties()["MainPID"])

    def runtime_children():
        return set(
            subprocess.check_output(["pgrep", "-P", str(agent_pid)], text=True).split()
        )

    children = runtime_children()
    assert len(children) == 1
    rejected = queue(
        "xray/install", {"version": "v26.3.27", "start": True}, expected="failed"
    )
    assert "rejected the existing configuration" in rejected["result_error"]
    assert saved.read_bytes() == before
    assert runtime_children() == children
    print(
        "PASS " + mode + " stock Xray switch rejected without stopping the fork",
        flush=True,
    )

    for tag in ("anytls", "snell6", "mieru-tcp"):
        field = "psk" if tag.startswith("snell") else "password"
        rotated = {**users[tag], field: str(uuid4())}
        queue("inbounds", {"action": "add-client", "tag": tag, "client": rotated})
        check_traffic(
            directory,
            args,
            subscribed[tag],
            protocol_ca,
            echo,
            udp,
            mode + " rotated old " + tag,
            allowed=False,
        )
        check_traffic(
            directory,
            args,
            {**subscribed[tag], field: rotated[field]},
            protocol_ca,
            echo,
            udp,
            mode + " rotated new " + tag,
        )
        queue("inbounds", {"action": "add-client", "tag": tag, "client": users[tag]})

    for tag in proxies:
        queue(
            "inbounds",
            {
                "action": "remove-client",
                "tag": tag,
                "client": {"email": "original-" + tag},
            },
        )
        check_traffic(
            directory,
            args,
            proxies[tag],
            protocol_ca,
            echo,
            udp,
            mode + " removed original " + tag,
            allowed=False,
        )
        check_traffic(
            directory,
            args,
            subscribed[tag],
            protocol_ca,
            echo,
            udp,
            mode + " remaining user " + tag,
        )
        queue(
            "inbounds",
            {
                "action": "remove-client",
                "tag": tag,
                "client": {"email": users[tag]["email"]},
            },
        )
        check_traffic(
            directory,
            args,
            subscribed[tag],
            protocol_ca,
            echo,
            udp,
            mode + " last user revoked " + tag,
            allowed=False,
        )
        assert fixture.ready()
    empty = json.loads(saved.read_text())
    assert all(inbound["settings"]["users"] == [] for inbound in empty["inbounds"])
    unpatched = subprocess.run(
        [str(args.reference), "run", "-test", "-config", str(saved)],
        capture_output=True,
        check=False,
        timeout=15,
    )
    assert unpatched.returncode != 0
    assert b"no users configured" in unpatched.stdout + unpatched.stderr
    for key in ("routing", "outbounds", "policy", "api", "stats"):
        assert empty[key] == original[key]
    for previous, current in zip(original["inbounds"], empty["inbounds"], strict=True):
        assert {k: v for k, v in previous.items() if k != "settings"} == {
            k: v for k, v in current.items() if k != "settings"
        }
    subprocess.run(["systemctl", "restart", fixture.unit], check=True, timeout=20)
    runtime.poll(mode + " empty inbounds survive service restart", fixture.ready)
    assert json.loads(saved.read_text()) == empty
    for tag, proxy in subscribed.items():
        check_traffic(
            directory,
            args,
            proxy,
            protocol_ca,
            echo,
            udp,
            mode + " restart retains revocation " + tag,
            allowed=False,
        )
    assert assign() == users
    for tag, proxy in subscribed.items():
        check_traffic(
            directory, args, proxy, protocol_ca, echo, udp, mode + " reactivated " + tag
        )
    assert xray_config.read_bytes() == original_bytes
    print(
        "PASS "
        + mode
        + " original config, import, actual subscriptions, stats, rotation, zero-user revocation and reactivation",
        flush=True,
    )


def run(args):
    def exercise(work, unused, wheel, stock, client, backend, echo):
        with (
            lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _),
            udp_echo() as udp,
        ):
            for mode in args.modes:
                fixture = service.Fixture(work)
                try:
                    exercise_mode(
                        work,
                        fixture,
                        args,
                        wheel,
                        stock,
                        client,
                        endpoint,
                        ca,
                        echo,
                        udp,
                        mode,
                    )
                except BaseException:
                    print(
                        subprocess.run(
                            [
                                "journalctl",
                                "-u",
                                fixture.unit,
                                "-n",
                                "50",
                                "--no-pager",
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=10,
                        ).stdout,
                        flush=True,
                    )
                    for log in (work / mode).glob("*.log"):
                        print(log.name, log.read_text()[-10000:], flush=True)
                    raise
                finally:
                    fixture.cleanup()

    service.exercise = exercise
    service.run(args.wheel, args.xray_archive)
    print("PASS fork protocol migration smoke", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "reference", "mihomo", "wheel", "nginx"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("websocket", "http"),
        default=["websocket", "http"],
    )
    run(parser.parse_args())
