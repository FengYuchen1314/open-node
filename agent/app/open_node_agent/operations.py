import copy
import json
import platform
import time
from time import monotonic
from urllib.parse import parse_qs

import psutil

from open_node_agent import __version__
from open_node_agent.diagnostics import Diagnostics
from open_node_agent.http01 import ENDPOINT as HTTP01_ENDPOINT
from open_node_agent.http01 import HttpChallenges
from open_node_agent.journal import CommandJournal
from open_node_agent.lifecycle import HostLifecycle
from open_node_agent.logs import OwnedLogs
from open_node_agent.nginx import NginxRuntime
from open_node_agent.runtime import RuntimeFailure, XrayRuntime
from open_node_agent.warp import Warp
from open_node_agent.xray_releases import XrayReleases
from open_node_agent.xray_takeover import ENDPOINT as TAKEOVER_ENDPOINT
from open_node_agent.xray_takeover import XrayTakeover


def telemetry() -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    counters = [
        value for name, value in psutil.net_io_counters(pernic=True).items() if name != "lo"
    ]
    return {
        "system": {
            "rx_total": sum(item.bytes_recv for item in counters),
            "tx_total": sum(item.bytes_sent for item in counters),
            "boot_time_unix": int(psutil.boot_time()),
        },
        "sysmetrics": {
            "cpu_pct": psutil.cpu_percent(),
            "loadavg": " ".join(map(str, psutil.getloadavg())),
            "mem_used": memory.used,
            "mem_total": memory.total,
            "disk_used": disk.used,
            "disk_total": disk.total,
            "uptime": max(0, int(time.time() - psutil.boot_time())),
            "cpu_model": platform.processor(),
            "cpu_cores": psutil.cpu_count(logical=False) or 0,
            "cpu_threads": psutil.cpu_count() or 0,
            "os": platform.system(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "has_cpu": True,
            "has_mem": True,
            "has_disk": True,
        },
    }


def find_tag(entries: list[dict], tag: str) -> dict:
    if not tag:
        raise RuntimeFailure("A tag is required")
    matches = [entry for entry in entries if entry.get("tag") == tag]
    if len(matches) != 1:
        raise RuntimeFailure("Tag must identify exactly one entry")
    return matches[0]


def snell_options(settings: dict) -> dict:
    users = settings.get("users") or []
    source = users[0] if users else settings
    version = source.get("version") or 4
    if type(version) is not int or version not in {4, 5, 6}:
        raise RuntimeFailure("Snell user management supports versions 4, 5 and 6")
    if version == 6:
        mode = source.get("v6Mode") or "default"
        if not isinstance(mode, str) or mode not in {"default", "unshaped"}:
            raise RuntimeFailure("Snell user management requires an authenticated v6 mode")
        return {"version": version, "v6Mode": mode}
    mode = source.get("obfsMode") or "none"
    host = source.get("obfsHost") or ""
    if not isinstance(mode, str) or mode not in {"none", "http", "tls"}:
        raise RuntimeFailure("Unsupported Snell obfuscation mode")
    if not isinstance(host, str):
        raise RuntimeFailure("Snell obfuscation host must be text")
    return {
        "version": version,
        "obfsMode": mode,
        "obfsHost": host,
    }


def edit_client(inbound: dict, client: dict, *, remove: bool = False) -> None:
    protocol = str(inbound.get("protocol", "")).lower()
    container = {
        "vless": "clients",
        "vmess": "clients",
        "trojan": "clients",
        "shadowsocks": "clients",
        "hysteria": "clients",
        "anytls": "users",
        "snell": "users",
        "mieru": "users",
    }.get(protocol)
    if container is None:
        raise RuntimeFailure("Client editing for this protocol is not implemented")
    if (
        not isinstance(client, dict)
        or not isinstance(client.get("email"), str)
        or not client["email"]
    ):
        raise RuntimeFailure("Client email is required")
    settings = inbound.get("settings", {})
    if not isinstance(settings, dict):
        raise RuntimeFailure("Inbound settings must be an object")
    clients = settings.get(container, [])
    if not isinstance(clients, list) or any(not isinstance(item, dict) for item in clients):
        raise RuntimeFailure(f"settings.{container} must be an array of user objects")
    if container == "users" and settings.get("clients"):
        raise RuntimeFailure("This protocol uses settings.users, not settings.clients")
    remaining = [item for item in clients if item.get("email") != client["email"]]
    options = snell_options(settings) if protocol == "snell" else None
    if options and any(snell_options({"users": [item]}) != options for item in clients):
        raise RuntimeFailure("Snell users must share the inbound's version and transport options")
    replacement = copy.deepcopy(client)
    if not remove:
        if options:
            replacement = {**options, **replacement}
            candidate_options = snell_options({"users": [replacement]})
            if remaining and candidate_options != options:
                raise RuntimeFailure(
                    "Changing one user's Snell options would change the whole inbound"
                )
            options = candidate_options
        index = next(
            (i for i, item in enumerate(clients) if item.get("email") == client["email"]),
            len(remaining),
        )
        remaining.insert(min(index, len(remaining)), replacement)
    if options:
        # The fork derives transport options from its first user; retain them when empty.
        settings.update(options)
    settings[container] = remaining
    inbound["settings"] = settings


def edit_entries(config: dict, key: str, payload: dict) -> None:
    entries = config.setdefault(key, [])
    action = payload.get("action")
    item_key = "inbound" if key == "inbounds" else "outbound"
    if action == "reorder":
        tags = payload.get("tags", [])
        if len(tags) != len(entries) or len(set(tags)) != len(tags):
            raise RuntimeFailure("Reordering requires every existing tag exactly once")
        config[key] = [find_tag(entries, tag) for tag in tags]
        return
    if action == "add":
        item = payload.get(item_key)
        if not isinstance(item, dict) or not item.get("tag"):
            raise RuntimeFailure("A tagged entry is required")
        if any(entry.get("tag") == item["tag"] for entry in entries):
            raise RuntimeFailure("Tag already exists")
        entries.append(copy.deepcopy(item))
        return
    current = find_tag(entries, payload.get("tag"))
    if action == "remove":
        entries.remove(current)
    elif action in {"replace", "update"}:
        item = payload.get(item_key)
        if not isinstance(item, dict) or item.get("tag") != current.get("tag"):
            raise RuntimeFailure("Replacement must retain its tag")
        entries[entries.index(current)] = copy.deepcopy(item)
    elif action in {"add-client", "remove-client"} and key == "inbounds":
        edit_client(current, payload.get("client"), remove=action == "remove-client")
    elif action == "add-sniffing-exclude" and key == "inbounds":
        domains = payload.get("domains", [])
        if not isinstance(domains, list) or any(not isinstance(item, str) for item in domains):
            raise RuntimeFailure("Domains must be a list of strings")
        excluded = current.setdefault("sniffing", {}).setdefault("domainsExcluded", [])
        current["sniffing"]["domainsExcluded"] = list(dict.fromkeys([*excluded, *domains]))
    else:
        raise RuntimeFailure("Unsupported entry action")


def routing_rule(rules: list[dict], payload: dict) -> dict:
    if payload.get("marktag"):
        matches = [rule for rule in rules if rule.get("marktag") == payload["marktag"]]
    elif payload.get("outbound_tag"):
        matches = [rule for rule in rules if rule.get("outboundTag") == payload["outbound_tag"]]
    else:
        raise RuntimeFailure("A rule marktag or outbound_tag is required")
    if len(matches) != 1:
        raise RuntimeFailure("Routing selector must identify exactly one rule")
    return matches[0]


def edit_routing(config: dict, payload: dict) -> None:
    action = payload.get("action")
    rules = config.setdefault("routing", {}).setdefault("rules", [])
    if action == "set":
        if not isinstance(payload.get("routing"), dict):
            raise RuntimeFailure("Routing must be an object")
        config["routing"] = copy.deepcopy(payload["routing"])
        for source, target in [
            ("observatory", "observatory"),
            ("burst_observatory", "burstObservatory"),
        ]:
            if source in payload:
                config[target] = copy.deepcopy(payload[source])
    elif action == "add_rule":
        rule = payload.get("rule")
        if not isinstance(rule, dict):
            raise RuntimeFailure("Rule must be an object")
        if rule.get("marktag") and any(item.get("marktag") == rule["marktag"] for item in rules):
            raise RuntimeFailure("Rule marktag already exists")
        index = payload.get("index", len(rules))
        if not isinstance(index, int) or not 0 <= index <= len(rules):
            raise RuntimeFailure("Rule insertion index is out of bounds")
        rules.insert(index, copy.deepcopy(rule))
    elif action == "remove_rule":
        if payload.get("marktag"):
            rules.remove(routing_rule(rules, payload))
        else:
            index = payload.get("index")
            if not isinstance(index, int) or not 0 <= index < len(rules):
                raise RuntimeFailure("Rule index is out of bounds")
            rules.pop(index)
    elif action in {"add_user_to_rule", "remove_user_from_rule"}:
        rule = routing_rule(rules, payload)
        email = payload.get("user_email")
        if not isinstance(email, str) or not email:
            raise RuntimeFailure("User email is required")
        users = rule.setdefault("user", [])
        if action == "add_user_to_rule" and email not in users:
            users.append(email)
        elif action == "remove_user_from_rule" and email in users:
            users.remove(email)
            if not users:
                rules.remove(rule)
    else:
        raise RuntimeFailure("Unsupported routing action")


class Operations:
    def __init__(self, runtime: XrayRuntime, journal: CommandJournal):
        self.runtime = runtime
        self.journal = journal
        self.nginx = NginxRuntime(runtime, journal)
        self.releases = XrayReleases(runtime, journal)
        self.lifecycle = HostLifecycle(runtime.config)
        self.diagnostics = Diagnostics(runtime.config)
        self.logs = OwnedLogs(runtime.config)
        self.warp = Warp(runtime)
        self.http01 = HttpChallenges(runtime.config, journal)
        self.takeover = XrayTakeover(runtime, journal)
        self.previous_network: dict | None = None
        self.previous_sample: float | None = None

    async def scan(self) -> dict:
        return {
            **await self.runtime.scan(),
            "nginx": await self.nginx.status(),
            "runtime_release": self.releases.status(),
            "warp": self.warp.snapshot(),
            "http01": self.http01.snapshot(),
        }

    def network_speed(self) -> dict:
        current = telemetry()["system"]
        now = monotonic()
        speeds = {"upload_speed": 0, "download_speed": 0}
        previous = self.previous_network
        if (
            previous is not None
            and self.previous_sample is not None
            and now > self.previous_sample
            and previous["boot_time_unix"] == current["boot_time_unix"]
        ):
            elapsed = now - self.previous_sample
            for field, counter in (("upload_speed", "tx_total"), ("download_speed", "rx_total")):
                speeds[field] = max(0, int((current[counter] - previous[counter]) / elapsed))
        self.previous_network, self.previous_sample = current, now
        return {"success": True, **speeds}

    async def handle(self, command: dict) -> dict:
        method, path = command["method"], command["path"]
        body = command.get("body") or {}
        if not isinstance(body, dict):
            raise RuntimeFailure("Command body must be an object")
        query = parse_qs(command.get("query") or "")
        if path == HTTP01_ENDPOINT:
            if method == "PUT":
                return await self.http01.present(body)
            if method == "DELETE":
                return await self.http01.release(body)
            if method == "GET":
                return self.http01.snapshot()
        if path == "/api/child/domains/latency" and method == "POST":
            return await self.diagnostics.latency(body)
        if path == "/api/child/network/return-route-test" and method == "POST":
            return await self.diagnostics.return_route(body)
        if path == "/api/child/logs" and method == "GET":
            return self.logs.tail(query)
        if path == "/api/child/logs/files":
            if method == "GET":
                return self.logs.list()
            if method == "DELETE":
                return self.logs.delete(query)
        async with self.runtime.lock:
            if path == "/api/child/limiter":
                if method == "GET":
                    return await self.runtime.limiter.status()
                if method == "POST":
                    return await self.runtime.limiter.apply(body)
            if path == TAKEOVER_ENDPOINT and method in {"GET", "POST"}:
                return await self.takeover.handle({"preview": True} if method == "GET" else body)
            if path.startswith("/api/child/warp/"):
                return await self.warp.handle(method, path, body)
            if path == "/api/child/agent/lifecycle" and method == "GET":
                return await self.lifecycle.status()
            if (
                path in {"/api/child/xray/install", "/api/child/xray/install-stream"}
                and method == "POST"
            ):
                return await self.releases.install(body)
            if (
                path in {"/api/child/xray/remove", "/api/child/xray/remove-stream"}
                and method == "POST"
            ):
                return await self.releases.remove()
            if path == "/api/child/xray/rollback" and method == "POST":
                return await self.releases.rollback()
            if path == "/api/child/xray/release" and method == "GET":
                return self.releases.status()
            if path == "/api/child/tunnel/deploy" and method == "POST":
                return await self.nginx.deploy_tunnel(body)
            if path.startswith("/api/child/nginx/") or path in {
                "/api/child/cert/deploy",
                "/api/child/validate-site",
            }:
                return await self.nginx.handle(method, path, body, query)
            if path == "/api/child/xray/config":
                supplied_path = body.get("path") or query.get("path", [None])[0]
                if supplied_path and supplied_path != str(self.runtime.config.xray_config):
                    raise RuntimeFailure("Only the configured Xray file may be accessed")
                if method == "GET":
                    await self.runtime.binding()
                    return {
                        "success": True,
                        "config": json.dumps(self.runtime.read()),
                        "path": str(self.runtime.config.xray_config),
                    }
                if method == "POST":
                    return await self.runtime.write(body.get("config"))
            if path == "/api/child/xray/test-config" and method == "POST":
                ok, output = await self.runtime.validate(body.get("config"))
                return {"ok": ok, "output": output}
            if path == "/api/child/scan" and method == "POST":
                return await self.scan()
            if path == "/api/child/services/status" and method == "GET":
                return {
                    "success": True,
                    "nginx": await self.nginx.status(),
                    "xray": {
                        "running": await self.runtime.running(),
                        "mode": self.runtime.config.runtime_mode,
                        "message": self.runtime.binding_error,
                    },
                }
            if path == "/api/child/services/control" and method == "POST":
                if body.get("service") not in {"xray", "nginx"} or body.get("action") not in {
                    "start",
                    "stop",
                    "restart",
                    "reload",
                }:
                    raise RuntimeFailure(
                        "Only the configured Xray and Nginx services can be controlled"
                    )
                if body["service"] == "nginx":
                    if body["action"] == "start":
                        await self.nginx.start()
                    elif body["action"] == "stop":
                        await self.nginx.stop()
                    elif body["action"] == "reload":
                        await self.nginx.apply({}, activate=True)
                    else:
                        await self.nginx.apply({})
                        await self.nginx.stop()
                        await self.nginx.start()
                    if body["action"] != "reload":
                        self.journal.set_desired_running(body["action"] != "stop", "nginx")
                    return {"success": True, **await self.nginx.status()}
                desired = body["action"] != "stop"
                if desired:
                    await (
                        self.runtime.start()
                        if body["action"] == "start"
                        else self.runtime.restart()
                    )
                else:
                    await self.runtime.stop()
                self.journal.set_desired_running(desired)
                return {"success": True, "running": await self.runtime.running()}
            if path in {"/api/child/inbounds", "/api/child/outbounds", "/api/child/routing"}:
                key = path.rsplit("/", 1)[-1]
                config = self.runtime.read()
                if method == "GET":
                    return {"success": True, key: config.get(key, {} if key == "routing" else [])}
                if method == "POST":
                    if key == "routing":
                        edit_routing(config, body)
                    else:
                        edit_entries(config, key, body)
                    return await self.runtime.write(
                        config, restart=not body.get("no_restart", False)
                    )
            if path == "/api/child/batch-apply" and method == "POST":
                config = self.runtime.read()
                for item in body.get("inbound_clients", []):
                    edit_client(
                        find_tag(config.get("inbounds", []), item.get("tag")), item.get("client")
                    )
                for item in body.get("routing_user_additions", []):
                    edit_routing(config, {**item, "action": "add_user_to_rule"})
                limits = await self.runtime.limiter.provision(body.get("limiter_users", []), config)
                try:
                    result = await self.runtime.write(
                        config, restart=not body.get("no_restart", False)
                    )
                except (OSError, ValueError) as exc:
                    if limits is not None:
                        raise RuntimeFailure(
                            "Limiter policies were persisted but the Xray config update failed; "
                            "inspect the runtime before retrying"
                        ) from exc
                    raise
                if limits is not None:
                    result["limiter"] = {"applied": True, "revision": limits["revision"]}
                elif body.get("limiter_users"):
                    result["limiter"] = {"applied": True, "unlimited": True, "revision": None}
                return result
            if path == "/api/child/system/info" and method == "GET":
                return {
                    "success": True,
                    "agent_version": __version__,
                    "runtime": await self.scan(),
                    **telemetry(),
                }
            if path == "/api/child/system/nics" and method == "GET":
                return {
                    "success": True,
                    "interfaces": [
                        {"name": name, "addresses": [item.address for item in addresses]}
                        for name, addresses in psutil.net_if_addrs().items()
                    ],
                }
            if path == "/api/child/speed" and method == "GET":
                return self.network_speed()
            if path == "/api/child/traffic" and method == "GET":
                return {"success": True, "stats": await self.runtime.stats(), **telemetry()}
        raise NotImplementedError(f"Operation not implemented: {method} {path}")
