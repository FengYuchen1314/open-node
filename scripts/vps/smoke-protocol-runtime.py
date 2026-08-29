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
UDP_ECHO_PREFIX = b"open-node-udp-echo:"
XRAY_EGRESS_ADDRESS = "127.0.0.2"
ACCESS_PATH = "/api/child/subscription-access"


@contextmanager
def udp_echo(required_source=None):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(0.2)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    data, address = listener.recvfrom(65535)
                    if required_source and address[0] != required_source:
                        continue
                    listener.sendto(UDP_ECHO_PREFIX + data, address)
                except TimeoutError:
                    pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1]
        finally:
            stop.set()
            thread.join(3)


DNS_NAME = b"\x05mieru\x05smoke\x07invalid\x00"
DNS_ANSWER = socket.inet_aton("192.0.2.53")


def dns_query():
    identifier = uuid4().int & 0xFFFF
    question = DNS_NAME + struct.pack("!HH", 1, 1)
    return struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 0) + question


def dns_reply(query):
    assert len(query) >= 12
    identifier, flags, questions, answers, authority, additional = struct.unpack(
        "!HHHHHH", query[:12]
    )
    assert flags & 0x8000 == 0
    assert (questions, answers, authority, additional) == (1, 0, 0, 0)
    question = DNS_NAME + struct.pack("!HH", 1, 1)
    assert query[12:] == question
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, len(DNS_ANSWER))
    return (
        struct.pack("!HHHHHH", identifier, 0x8180, 1, 1, 0, 0)
        + question
        + answer
        + DNS_ANSWER
    )


def valid_dns_reply(query, reply):
    try:
        query_id = struct.unpack("!H", query[:2])[0]
        identifier, flags, questions, answers, authority, additional = struct.unpack(
            "!HHHHHH", reply[:12]
        )
        return (
            identifier == query_id
            and flags & 0x8000
            and flags & 0xF == 0
            and (questions, answers, authority, additional) == (1, 1, 0, 0)
            and reply.endswith(DNS_ANSWER)
        )
    except (IndexError, struct.error):
        return False


@contextmanager
def udp_dns(required_source=None):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(0.2)
        stop = threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    query, address = listener.recvfrom(65535)
                    if required_source and address[0] != required_source:
                        continue
                    listener.sendto(dns_reply(query), address)
                except (AssertionError, struct.error):
                    pass
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


def read_socks_address(connection, address_type):
    if address_type == 1:
        return socket.inet_ntop(socket.AF_INET, read_exact(connection, 4))
    if address_type == 4:
        return socket.inet_ntop(socket.AF_INET6, read_exact(connection, 16))
    if address_type == 3:
        return read_exact(connection, read_exact(connection, 1)[0]).decode("ascii")
    raise ValueError("Unsupported SOCKS address type")


@contextmanager
def socks_udp_association(socks_port, timeout=3):
    with socket.create_connection(
        ("127.0.0.1", socks_port), timeout=timeout
    ) as control:
        control.sendall(b"\x05\x01\x00")
        assert read_exact(control, 2) == b"\x05\x00"
        control.sendall(b"\x05\x03\x00\x01" + b"\x00" * 6)
        header = read_exact(control, 4)
        assert header[:3] == b"\x05\x00\x00"
        relay_host = read_socks_address(control, header[3])
        relay_port = struct.unpack("!H", read_exact(control, 2))[0]
        if relay_host in {"0.0.0.0", "::"}:
            relay_host = "127.0.0.1"
        family = socket.AF_INET6 if ":" in relay_host else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as datagram:
            datagram.settimeout(timeout)
            yield datagram, (relay_host, relay_port)


def parse_udp_frame(frame):
    if len(frame) < 4 or frame[:3] != b"\x00\x00\x00":
        raise ValueError("Invalid SOCKS UDP header")
    address_type, offset = frame[3], 4
    if address_type == 1:
        end = offset + 4
        host = socket.inet_ntop(socket.AF_INET, frame[offset:end])
    elif address_type == 4:
        end = offset + 16
        host = socket.inet_ntop(socket.AF_INET6, frame[offset:end])
    elif address_type == 3:
        if offset >= len(frame):
            raise ValueError("Missing SOCKS UDP domain length")
        length, offset = frame[offset], offset + 1
        end = offset + length
        host = frame[offset:end].decode("ascii")
    else:
        raise ValueError("Unsupported SOCKS UDP address type")
    if end + 2 > len(frame):
        raise ValueError("Truncated SOCKS UDP frame")
    port = struct.unpack("!H", frame[end : end + 2])[0]
    return host, port, frame[end + 2 :]


def udp_exchange(datagram, relay, target_port, payload):
    assert not (
        relay[0] in {"127.0.0.1", "::1", "::ffff:127.0.0.1"} and relay[1] == target_port
    ), "SOCKS UDP relay collides with the target responder"
    frame = b"\x00\x00\x00\x01" + socket.inet_aton("127.0.0.1")
    frame += struct.pack("!H", target_port) + payload
    datagram.sendto(frame, relay)
    returned, _ = datagram.recvfrom(65535)
    host, port, body = parse_udp_frame(returned)
    assert host in {"127.0.0.1", "::ffff:127.0.0.1"}
    assert port == target_port
    return body


def udp_forwards(socks_port, echo_port):
    try:
        with socks_udp_association(socks_port) as (datagram, relay):
            payload = ("protocol-udp-" + uuid4().hex).encode()
            return (
                udp_exchange(datagram, relay, echo_port, payload)
                == UDP_ECHO_PREFIX + payload
            )
    except (ConnectionError, OSError, AssertionError, UnicodeError, ValueError):
        return False


def mieru_udp_forwards(socks_port, targets):
    echo, large_echo, dns = targets
    try:
        with socks_udp_association(socks_port) as (datagram, relay):
            first = ("mieru-udp-first-" + uuid4().hex).encode()
            assert udp_exchange(datagram, relay, echo, first) == UDP_ECHO_PREFIX + first
            query = dns_query()
            reply = udp_exchange(datagram, relay, dns, query)
            assert valid_dns_reply(query, reply)
            large = os.urandom(4096)
            assert (
                udp_exchange(datagram, relay, large_echo, large)
                == UDP_ECHO_PREFIX + large
            )
            final = ("mieru-udp-final-" + uuid4().hex).encode()
            assert udp_exchange(datagram, relay, echo, final) == UDP_ECHO_PREFIX + final
            return True
    except (ConnectionError, OSError, AssertionError, UnicodeError, ValueError):
        return False


@contextmanager
def proxy_client(work, args, proxy, ca, native=False):
    directory = work / ("client-" + uuid4().hex[:8])
    directory.mkdir()
    socks = runtime.free_port()
    config = directory / "client.json"
    if native or (proxy["type"] == "snell" and proxy.get("version") == 6):
        # Native fork clients provide a fail-closed revocation oracle. Authorized
        # interoperability remains covered with Mihomo below.
        outbound = copy.deepcopy(proxy.get("xray-outbound")) or {
            "protocol": "snell",
            "settings": {
                "address": proxy["server"],
                "port": proxy["port"],
                "version": 6,
                "v6Mode": proxy["mode"],
            },
        }
        if proxy["type"] == "snell":
            outbound["settings"]["psk"] = proxy["psk"]
        elif proxy["type"] == "anytls":
            outbound["settings"]["password"] = proxy["password"]
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
                "outbounds": [outbound],
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


def check_traffic(
    work,
    args,
    proxy,
    ca,
    tcp_port,
    udp_port,
    label,
    allowed=True,
    udp_allowed=None,
    udp_targets=None,
):
    with proxy_client(
        work,
        args,
        proxy,
        ca,
        native=not allowed and proxy.get("type") != "mieru",
    ) as socks:
        forwards_udp = None
        udp_checked_early = False
        if proxy.get("udp"):
            udp_allowed = allowed if udp_allowed is None else udp_allowed

            def forwards_udp():
                if proxy.get("type") == "mieru" and udp_targets:
                    return mieru_udp_forwards(socks, udp_targets)
                return udp_forwards(socks, udp_port)

            def rejects_udp():
                # A symmetric echo can make a locally reflected SOCKS request
                # look like a response. The responder transforms each nonce,
                # and three fresh associations must all fail closed.
                return all(not forwards_udp() for _ in range(3))

            # Mieru needs an explicit UDP-only revocation proof. Keep the
            # established TCP-then-UDP ordering for other fork protocols,
            # whose clients may tear down cached UDP state after TCP failure.
            if not udp_allowed and not allowed and proxy.get("type") == "mieru":
                assert rejects_udp(), label + " UDP still authorized"
                udp_checked_early = True
                print(
                    "PASS " + label + " rejects UDP-only target traffic",
                    flush=True,
                )
        if allowed:
            runtime.poll(label + " TCP", lambda: runtime.forwards(socks, tcp_port))
        else:
            assert not runtime.forwards(socks, tcp_port), (
                label + " TCP still authorized"
            )
        if forwards_udp:
            if udp_allowed:
                runtime.poll(label + " UDP", forwards_udp)
            elif allowed:
                assert rejects_udp(), label + " UDP unexpectedly forwarded"
                print("PASS " + label + " rejects UDP target traffic", flush=True)
            elif not udp_checked_early:
                assert rejects_udp(), label + " UDP still authorized"
        if not allowed:
            print("PASS " + label + " rejects supported traffic", flush=True)


def check_udp_only(work, args, proxy, ca, targets, label):
    with proxy_client(work, args, proxy, ca) as socks:
        runtime.poll(
            label + " UDP-only echo/DNS/multi-target",
            lambda: mieru_udp_forwards(socks, targets),
        )


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
                {
                    "protocol": "freedom",
                    "tag": "direct",
                    "sendThrough": XRAY_EGRESS_ADDRESS,
                },
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
    work, fixture, args, wheel, stock, client, endpoint, ca, echo, udp_targets, mode
):
    directory = work / mode
    directory.mkdir()
    udp = udp_targets[0]
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
                mode + " unpatched reference " + tag,
                udp_allowed=proxy["type"] != "mieru",
                udp_targets=udp_targets,
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

    def active_runtime():
        try:
            children = subprocess.check_output(
                ["pgrep", "-P", fixture.properties()["MainPID"]], text=True
            ).split()
        except subprocess.CalledProcessError:
            return set()
        runtimes = set()
        for pid in children:
            try:
                command = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0")
            except FileNotFoundError:
                continue
            if command[1:4] == [b"run", b"-config", os.fsencode(saved)]:
                runtimes.add(pid)
        return runtimes

    installed_runtime = active_runtime()
    assert len(installed_runtime) == 1
    for tag in ("mieru-tcp", "mieru-udp"):
        check_traffic(
            directory,
            args,
            proxies[tag],
            protocol_ca,
            echo,
            udp,
            mode + " patched original " + tag,
            udp_targets=udp_targets,
        )
    assert active_runtime() == installed_runtime, "Mieru traffic replaced the runtime"

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
        if node["protocol"] == "mieru":
            assert node["config"]["udp"] is True, node
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

    def wait_access(command, enabled):
        assert command["server_id"] == created["server"]["id"], command
        assert command["method"] == "POST", command
        assert command["path"] == ACCESS_PATH, command
        completed = lifecycle.wait_command(client, base, command)
        confirmation = completed["result_body"]["access"]
        assert confirmation["revision"] == command["body"]["revision"], completed
        assert completed["result_body"].get("restart_required") is not True, completed
        return runtime.poll(
            mode + " subscription access " + ("enabled" if enabled else "disabled"),
            lambda: (
                client.get(f"/api/v1/users/{username}/access").raise_for_status().json()
            ),
            lambda state: (
                len(state["servers"]) == 1
                and state["servers"][0]["server_id"] == created["server"]["id"]
                and state["servers"][0]["command_id"] == command["id"]
                and state["servers"][0]["status"] == "applied"
                and all(
                    entry["enabled"] is enabled
                    for entry in state["servers"][0]["entries"]
                )
            ),
        )

    def access_command(command_id):
        matches = [
            row
            for row in client.get(base + "/commands")
            .raise_for_status()
            .json()["commands"]
            if row["id"] == command_id
        ]
        assert len(matches) == 1, command_id
        return matches[0]

    def sync_access(enabled):
        state = (
            client.post(f"/api/v1/users/{username}/access/sync")
            .raise_for_status()
            .json()
        )
        assert len(state["servers"]) == 1, state
        command_id = state["servers"][0]["command_id"]
        assert command_id, state
        return wait_access(access_command(command_id), enabled)

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
        access_commands = [
            command
            for command in assigned["commands"]
            if command["server_id"] == created["server"]["id"]
            and command["path"] == ACCESS_PATH
        ]
        assert len(access_commands) == 1, assigned["commands"]
        wait_access(access_commands[0], True)
        return {
            item["tag"]: item["client"]
            for item in assigned["provisioning_batches"][0]["body"]["inbound_clients"]
        }

    users = assign()
    refreshed_scan = queue("scan")
    assert refreshed_scan["result_body"]["xray_running"] is True, refreshed_scan
    assert refreshed_scan["result_body"]["xray_capabilities"] == {
        "mieru_udp_target": 1
    }, refreshed_scan
    latest_scan = client.get(base + "/scan/latest").raise_for_status().json()["scan"]
    assert latest_scan["xray_running"] is True, latest_scan
    assert latest_scan["xray_capabilities"] == {"mieru_udp_target": 1}, latest_scan
    assigned_runtime = active_runtime()
    assert len(assigned_runtime) == 1
    token = (
        client.post(f"/api/v1/users/{username}/subscription-token")
        .raise_for_status()
        .json()["subscription"]["token"]
    )
    subscription = yaml.safe_load(
        client.get(f"/api/v1/subscribe/{token}").raise_for_status().text
    )
    subscribed = {by_name[proxy["name"]]: proxy for proxy in subscription["proxies"]}
    assert set(subscribed) == set(proxies) - {"snell6", "snell6-unshaped"}
    for node in nodes:
        tag = node["inbound_tag"]
        if node["protocol"] == "mieru":
            continue
        exported = (
            client.get(f"/api/v1/subscribe/{token}?format=xray&node_id={node['id']}")
            .raise_for_status()
            .json()
        )
        outbound = exported["outbounds"][0]
        if tag in subscribed:
            subscribed[tag]["xray-outbound"] = outbound
            proxies[tag]["xray-outbound"] = copy.deepcopy(outbound)
            continue
        settings = outbound["settings"]
        subscribed[tag] = {
            "type": "snell",
            "version": 6,
            "mode": settings["v6Mode"],
            "name": outbound["tag"],
            "server": settings["address"],
            "port": settings["port"],
            "psk": settings["psk"],
            "udp": True,
            "xray-outbound": outbound,
        }
    assert set(subscribed) == set(proxies)
    for tag in ("mieru-tcp", "mieru-udp"):
        assert subscribed[tag].get("udp") is True, subscribed[tag]

    def user_stats(email):
        latest = (
            client.get(base + "/telemetry/latest")
            .raise_for_status()
            .json()
            .get("latest")
        )
        counters = (((latest or {}).get("stats") or {}).get("user") or {}).get(
            email
        ) or {}
        return counters.get("uplink", 0), counters.get("downlink", 0)

    for tag, proxy in subscribed.items():
        if proxy["type"] == "mieru":
            email = users[tag]["email"]
            before_udp = user_stats(email)
            check_udp_only(
                directory,
                args,
                proxy,
                protocol_ca,
                udp_targets,
                mode + " subscription " + tag,
            )
            runtime.poll(
                mode + " UDP-only user statistics " + tag,
                lambda email=email: user_stats(email),
                lambda counters, before=before_udp: (
                    counters[0] > before[0] and counters[1] > before[1]
                ),
            )
            print(
                "PASS " + mode + " UDP-only attributed statistics " + tag,
                flush=True,
            )
        check_traffic(
            directory,
            args,
            proxy,
            protocol_ca,
            echo,
            udp,
            mode + " subscription " + tag,
            udp_targets=udp_targets,
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

    assert active_runtime() == assigned_runtime, (
        "subscription traffic replaced the runtime"
    )

    before = saved.read_bytes()
    invalid = copy.deepcopy(original)
    invalid["inbounds"][0]["settings"]["users"][0]["password"] = ""
    queue("xray/config", {"config": invalid}, expected="failed")
    assert saved.read_bytes() == before
    # A credential-affecting mutation is conservatively reconciled even when
    # the Agent rejects it before writing. Finish that expected reconciliation
    # before taking the exact-PID baseline for the failed runtime migration.
    sync_access(True)
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
        assert int(fixture.properties()["MainPID"]) == agent_pid
        # Capability probes and candidate validation are also direct Agent
        # children. active_runtime only returns the long-lived Xray runner.
        return active_runtime()

    def configured_users(tag):
        inbounds = json.loads(saved.read_text())["inbounds"]
        matches = [inbound for inbound in inbounds if inbound.get("tag") == tag]
        assert len(matches) == 1
        return matches[0]["settings"]["users"]

    def port_owners(network, port):
        result = subprocess.run(
            ["fuser", "-n", network, str(port)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode in {0, 1}, result
        return set(result.stdout.split())

    def listener(tag):
        return (
            "udp"
            if subscribed[tag].get("transport", "TCP").upper() == "UDP"
            else "tcp",
            subscribed[tag]["port"],
        )

    def assert_runtime_restarted(previous, listeners, *, listening=True):
        current = runtime_children()
        assert len(current) == 1
        assert current.isdisjoint(previous)
        assert all(not (Path("/proc") / pid).exists() for pid in previous)
        expected = current if listening else set()
        for network, port in listeners:
            runtime.poll(
                mode
                + f" {network.upper()} port {port} "
                + ("owned by restarted runtime" if listening else "suspended"),
                lambda network=network, port=port: port_owners(network, port),
                lambda owners: owners == expected,
            )
        return current

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
    sync_access(True)

    for tag in ("anytls", "snell6", "mieru-tcp", "mieru-udp"):
        field = "psk" if tag.startswith("snell") else "password"
        original_client = next(
            user for user in configured_users(tag) if user["email"] == "original-" + tag
        )
        rotated = {**original_client, field: str(uuid4())}
        queue("inbounds", {"action": "add-client", "tag": tag, "client": rotated})
        check_traffic(
            directory,
            args,
            proxies[tag],
            protocol_ca,
            echo,
            udp,
            mode + " rotated old " + tag,
            allowed=False,
            udp_targets=udp_targets,
        )
        check_traffic(
            directory,
            args,
            {**proxies[tag], field: rotated[field]},
            protocol_ca,
            echo,
            udp,
            mode + " rotated new " + tag,
            udp_targets=udp_targets,
        )
        queue(
            "inbounds",
            {"action": "add-client", "tag": tag, "client": original_client},
        )

    for tag in proxies:
        queue(
            "inbounds",
            {
                "action": "remove-client",
                "tag": tag,
                "client": {"email": "original-" + tag},
            },
        )
        assert {user["email"] for user in configured_users(tag)} == {
            users[tag]["email"]
        }
        check_traffic(
            directory,
            args,
            proxies[tag],
            protocol_ca,
            echo,
            udp,
            mode + " removed original " + tag,
            allowed=False,
            udp_targets=udp_targets,
        )
        check_traffic(
            directory,
            args,
            subscribed[tag],
            protocol_ca,
            echo,
            udp,
            mode + " remaining user " + tag,
            udp_targets=udp_targets,
        )
        before_empty = runtime_children()
        assert len(before_empty) == 1
        queue(
            "inbounds",
            {
                "action": "remove-client",
                "tag": tag,
                "client": {"email": users[tag]["email"]},
            },
        )
        assert configured_users(tag) == []
        assert_runtime_restarted(before_empty, [listener(tag)])
        check_traffic(
            directory,
            args,
            subscribed[tag],
            protocol_ca,
            echo,
            udp,
            mode + " direct zero-user " + tag,
            allowed=False,
            udp_targets=udp_targets,
        )
        if tag in {"snell4", "mieru-tcp"}:
            unpatched = subprocess.run(
                [str(args.reference), "run", "-test", "-config", str(saved)],
                capture_output=True,
                check=False,
                timeout=15,
            )
            assert unpatched.returncode != 0
            assert b"no users configured" in unpatched.stdout + unpatched.stderr
        before_restore = runtime_children()
        queue(
            "inbounds",
            {"action": "add-client", "tag": tag, "client": users[tag]},
        )
        assert {user["email"] for user in configured_users(tag)} == {
            users[tag]["email"]
        }
        assert_runtime_restarted(before_restore, [listener(tag)])

    before_revocation = runtime_children()
    assert len(before_revocation) == 1
    deactivated = (
        client.patch(f"/api/v1/users/{username}/active", json={"is_active": False})
        .raise_for_status()
        .json()["user"]
    )
    assert deactivated["is_active"] is False, deactivated
    access_state = (
        client.get(f"/api/v1/users/{username}/access").raise_for_status().json()
    )
    assert len(access_state["servers"]) == 1, access_state
    wait_access(access_command(access_state["servers"][0]["command_id"]), False)
    listeners = [listener(tag) for tag in subscribed]
    assert_runtime_restarted(before_revocation, listeners, listening=False)
    suspended_config = json.loads(saved.read_text())
    assert suspended_config["inbounds"] == []
    for tag, proxy in subscribed.items():
        check_traffic(
            directory,
            args,
            proxy,
            protocol_ca,
            echo,
            udp,
            mode + " last user revoked " + tag,
            allowed=False,
            udp_targets=udp_targets,
        )
        assert fixture.ready()
    empty = json.loads(saved.read_text())
    # Managed access durably suspends an empty listener instead of leaving an
    # unauthenticated protocol surface in the active Xray configuration.
    assert empty["inbounds"] == []
    for key in ("routing", "outbounds", "policy", "api", "stats"):
        assert empty[key] == original[key]
    subprocess.run(["systemctl", "restart", fixture.unit], check=True, timeout=20)
    runtime.poll(mode + " empty inbounds survive service restart", fixture.ready)
    agent_pid = int(fixture.properties()["MainPID"])
    assert json.loads(saved.read_text()) == empty
    for network, port in listeners:
        assert port_owners(network, port) == set()
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
            udp_targets=udp_targets,
        )
    before_reactivation = runtime_children()
    assert len(before_reactivation) == 1
    reactivated = (
        client.patch(f"/api/v1/users/{username}/active", json={"is_active": True})
        .raise_for_status()
        .json()["user"]
    )
    assert reactivated["is_active"] is True, reactivated
    access_state = (
        client.get(f"/api/v1/users/{username}/access").raise_for_status().json()
    )
    assert len(access_state["servers"]) == 1, access_state
    wait_access(access_command(access_state["servers"][0]["command_id"]), True)
    assert_runtime_restarted(before_reactivation, listeners)
    for tag, proxy in subscribed.items():
        assert {user["email"] for user in configured_users(tag)} == {
            users[tag]["email"]
        }
        check_traffic(
            directory,
            args,
            proxy,
            protocol_ca,
            echo,
            udp,
            mode + " reactivated " + tag,
            udp_targets=udp_targets,
        )
    assert xray_config.read_bytes() == original_bytes
    print(
        "PASS "
        + mode
        + " Mieru TCP/UDP underlays, UDP echo/DNS/multi-target, stats, rotation and revocation",
        flush=True,
    )
    print(
        "PASS "
        + mode
        + " original config, import, actual subscriptions, stats, rotation, "
        "zero-user revocation and reactivation",
        flush=True,
    )


def run(args):
    def exercise(work, unused, wheel, stock, client, backend, echo):
        with (
            lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _),
            udp_echo(XRAY_EGRESS_ADDRESS) as udp,
            udp_echo(XRAY_EGRESS_ADDRESS) as large_udp,
            udp_dns(XRAY_EGRESS_ADDRESS) as dns,
        ):
            udp_targets = (udp, large_udp, dns)
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
                        udp_targets,
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
    # The smoke explicitly drives every access reconciliation. Keep the
    # periodic worker from interleaving a legitimate restart with exact-PID
    # assertions for deliberately rejected mutations.
    poll_key = "OPEN_NODE_SUBSCRIPTION_ACCESS_POLL_SECONDS"
    previous_poll = os.environ.get(poll_key)
    os.environ[poll_key] = "300"
    try:
        service.run(args.wheel, args.xray_archive)
    finally:
        if previous_poll is None:
            os.environ.pop(poll_key, None)
        else:
            os.environ[poll_key] = previous_poll
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
