import copy
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qs

import psutil

from open_node_agent import __version__
from open_node_agent.agent_management import AgentManagement
from open_node_agent.diagnostics import Diagnostics
from open_node_agent.host_files import guarded_path, read_private
from open_node_agent.http01 import ENDPOINT as HTTP01_ENDPOINT
from open_node_agent.http01 import HttpChallenges
from open_node_agent.journal import CommandJournal
from open_node_agent.lifecycle import HostLifecycle
from open_node_agent.logs import OwnedLogs
from open_node_agent.nginx import NginxRuntime
from open_node_agent.node_cleanup import ENDPOINT as CLEANUP_ENDPOINT
from open_node_agent.node_cleanup import NodeCleanup
from open_node_agent.runtime import (
    RuntimeFailure,
    XrayRuntime,
    decode_config,
    format_endpoint,
    loopback_endpoint,
    xray_api_binding,
)
from open_node_agent.subscription_access import ENDPOINT as ACCESS_ENDPOINT
from open_node_agent.subscription_access import SubscriptionAccess
from open_node_agent.warp import Warp
from open_node_agent.xray_releases import XrayReleases
from open_node_agent.xray_takeover import ENDPOINT as TAKEOVER_ENDPOINT
from open_node_agent.xray_takeover import XrayTakeover

SYSTEM_CONFIG_KEYS = {
    "log_level",
    "dns",
    "policy",
    "metrics_enabled",
    "metrics_listen",
    "stats_enabled",
    "grpc_enabled",
    "grpc_port",
}
LOG_LEVELS = {"none", "error", "warning", "info", "debug"}
USER_STATS_COUNTERS = ("statsUserUplink", "statsUserDownlink")
SYSTEM_STATS_COUNTERS = (
    "statsInboundUplink",
    "statsInboundDownlink",
    "statsOutboundUplink",
    "statsOutboundDownlink",
)


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
        existing = [entry for entry in entries if entry.get("tag") == item["tag"]]
        if existing and payload.get("allow_existing") is True and existing == [item]:
            return
        if existing:
            raise RuntimeFailure("Tag already exists")
        entries.append(copy.deepcopy(item))
        return
    if action == "remove" and payload.get("ignore_missing") is True:
        matches = [entry for entry in entries if entry.get("tag") == payload.get("tag")]
        if not matches:
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
        existing = [item for item in rules if item.get("marktag") == rule.get("marktag")]
        if existing and payload.get("allow_existing") is True and existing == [rule]:
            return
        if rule.get("marktag") and existing:
            raise RuntimeFailure("Rule marktag already exists")
        index = payload.get("index", len(rules))
        if not isinstance(index, int) or not 0 <= index <= len(rules):
            raise RuntimeFailure("Rule insertion index is out of bounds")
        rules.insert(index, copy.deepcopy(rule))
    elif action == "remove_rule":
        if payload.get("marktag"):
            if payload.get("ignore_missing") is True and not any(
                rule.get("marktag") == payload["marktag"] for rule in rules
            ):
                return
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


def numeric_policy_levels(policy: dict) -> list[dict]:
    levels = policy.get("levels", {})
    if not isinstance(levels, dict):
        raise RuntimeFailure("Xray stats policy levels must be an object")
    numeric = []
    for name, level in levels.items():
        if isinstance(name, str) and name.isascii() and name.isdecimal():
            if not isinstance(level, dict):
                raise RuntimeFailure("Numeric Xray stats policy levels must be objects")
            numeric.append(level)
    return numeric


def complete_stats_policy_state(config: dict) -> bool | None:
    if "policy" not in config:
        return None
    policy = config["policy"]
    if not isinstance(policy, dict):
        raise RuntimeFailure("Xray stats policy must be an object")
    system = policy.get("system", {})
    if not isinstance(system, dict):
        raise RuntimeFailure("Xray stats system policy must be an object")
    groups = [
        (system, SYSTEM_STATS_COUNTERS),
        *((level, USER_STATS_COUNTERS) for level in numeric_policy_levels(policy)),
    ]
    tracked: list[bool | None] = []
    for container, counters in groups:
        present = [counter in container for counter in counters]
        if not any(present):
            tracked.append(None)
            continue
        values = [container[counter] for counter in counters if counter in container]
        if not all(present) or any(type(value) is not bool for value in values):
            raise RuntimeFailure(
                "Partial Xray stats policy cannot be edited by the system-config form"
            )
        if len(set(values)) != 1:
            raise RuntimeFailure(
                "Mixed Xray stats policy cannot be edited by the system-config form"
            )
        tracked.append(values[0])
    explicit = [value for value in tracked if value is not None]
    if explicit and (len(explicit) != len(tracked) or len(set(explicit)) != 1):
        raise RuntimeFailure(
            "Partial Xray stats policy cannot be edited by the system-config form"
        )
    return explicit[0] if explicit else None


def require_editable_system_config(config: dict) -> dict:
    if "log" in config:
        log = config["log"]
        if not isinstance(log, dict):
            raise RuntimeFailure("Xray log configuration must be an object")
        log_level = log.get("loglevel", "warning")
        if not isinstance(log_level, str) or log_level not in LOG_LEVELS:
            raise RuntimeFailure("Existing Xray loglevel is not supported by this form")

    if "dns" in config and not isinstance(config["dns"], dict):
        raise RuntimeFailure("Xray DNS configuration must be an object")

    if "metrics" in config:
        metrics = config["metrics"]
        if not isinstance(metrics, dict) or "listen" not in metrics:
            raise RuntimeFailure(
                "Routed or tag-only metrics cannot be edited by the system-config form"
            )
        if loopback_endpoint(metrics["listen"]) is None:
            raise RuntimeFailure("Existing metrics must use a literal loopback listener")

    if "stats" in config and not isinstance(config["stats"], dict):
        raise RuntimeFailure("Xray stats must be an object")
    complete_stats_policy_state(config)
    return xray_api_binding(config)


def fixed_stats_address_error(api_binding: dict, stats_address: str | None) -> str | None:
    if stats_address is None:
        return None
    required = loopback_endpoint(stats_address)
    if required is None:
        return "Configured stats_address must be a literal loopback IP and port"
    if api_binding["mode"] not in {"direct", "routed"}:
        return "Xray API endpoint is missing and does not match the configured stats_address"
    actual = (api_binding["host"], api_binding["port"])
    if actual != required:
        return (
            f"Xray API endpoint {format_endpoint(*actual)} does not match the configured "
            f"stats_address {format_endpoint(*required)}"
        )
    return None


def xray_system_config(config: dict, *, stats_address: str | None = None) -> dict:
    log = config.get("log")
    dns = config.get("dns")
    policy = config.get("policy")
    metrics = config.get("metrics")
    metrics_listen = metrics.get("listen") if isinstance(metrics, dict) else None
    reason = None
    try:
        api_binding = require_editable_system_config(config)
    except RuntimeFailure as exc:
        reason = str(exc)
        try:
            api_binding = xray_api_binding(config)
        except RuntimeFailure:
            api_binding = {
                "mode": "unsupported",
                "host": "127.0.0.1",
                "port": 46_736,
                "inbound_index": None,
            }
    if reason is None:
        reason = fixed_stats_address_error(api_binding, stats_address)
    try:
        policy_state = complete_stats_policy_state(config)
    except RuntimeFailure:
        policy_state = None
    api = config.get("api")
    return {
        "log_level": (
            log.get("loglevel", "warning")
            if isinstance(log, dict)
            and isinstance(log.get("loglevel", "warning"), str)
            and log.get("loglevel", "warning") in LOG_LEVELS
            else "warning"
        ),
        "dns": copy.deepcopy(dns) if isinstance(dns, dict) else {},
        "policy": copy.deepcopy(policy) if isinstance(policy, dict) else {},
        "metrics_enabled": isinstance(metrics, dict),
        "metrics_listen": metrics_listen
        if isinstance(metrics_listen, str)
        else "127.0.0.1:11111",
        "stats_enabled": isinstance(config.get("stats"), dict) and policy_state is True,
        "grpc_enabled": isinstance(api, dict),
        "grpc_port": api_binding["port"],
        "api_mode": api_binding["mode"],
        "grpc_disable_supported": (
            reason is None
            and stats_address is None
            and api_binding["mode"] in {"absent", "direct"}
        ),
        "grpc_port_writable": (
            reason is None
            and stats_address is None
            and api_binding["mode"] in {"absent", "direct", "routed"}
        ),
        "fixed_stats_address": stats_address,
        "writable": reason is None,
        "read_only_reason": reason,
    }


def apply_xray_system_config(
    config: dict, payload: dict, *, stats_address: str | None = None
) -> dict:
    if set(payload) != SYSTEM_CONFIG_KEYS:
        raise RuntimeFailure("Xray system config requires the complete supported field set")
    for key in ("metrics_enabled", "stats_enabled", "grpc_enabled"):
        if type(payload[key]) is not bool:
            raise RuntimeFailure(f"{key} must be a boolean")
    if not isinstance(payload["log_level"], str) or payload["log_level"] not in LOG_LEVELS:
        raise RuntimeFailure("log_level must be none, error, warning, info or debug")
    if not isinstance(payload["dns"], dict):
        raise RuntimeFailure("dns must be a JSON object")
    if not isinstance(payload["policy"], dict):
        raise RuntimeFailure("policy must be a JSON object")
    submitted_policy = copy.deepcopy(payload["policy"])
    submitted_levels = submitted_policy.get("levels", {})
    if not isinstance(submitted_levels, dict):
        raise RuntimeFailure("Xray stats policy levels must be an object")
    numeric_levels = numeric_policy_levels(submitted_policy)
    submitted_system = submitted_policy.get("system", {})
    if not isinstance(submitted_system, dict):
        raise RuntimeFailure("Xray stats system policy must be an object")
    port = payload["grpc_port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeFailure("grpc_port must be between 1 and 65535")
    api_binding = require_editable_system_config(config)
    if reason := fixed_stats_address_error(api_binding, stats_address):
        raise RuntimeFailure(reason)
    current_stats_enabled = (
        isinstance(config.get("stats"), dict)
        and complete_stats_policy_state(config) is True
    )
    current_grpc_enabled = isinstance(config.get("api"), dict)
    stats_changed = payload["stats_enabled"] != current_stats_enabled
    grpc_changed = payload["grpc_enabled"] != current_grpc_enabled
    grpc_enabled_now = payload["grpc_enabled"] and grpc_changed

    candidate = copy.deepcopy(config)
    log = candidate.get("log")
    if not isinstance(log, dict):
        log = {}
    log["loglevel"] = payload["log_level"]
    candidate["log"] = log
    candidate["dns"] = copy.deepcopy(payload["dns"])

    if stats_changed:
        if not numeric_levels:
            level = {}
            submitted_levels["0"] = level
            numeric_levels = [level]
        for level in numeric_levels:
            level.update(
                statsUserUplink=payload["stats_enabled"],
                statsUserDownlink=payload["stats_enabled"],
            )
        submitted_system.update(
            statsInboundUplink=payload["stats_enabled"],
            statsInboundDownlink=payload["stats_enabled"],
            statsOutboundUplink=payload["stats_enabled"],
            statsOutboundDownlink=payload["stats_enabled"],
        )
        submitted_policy.update(levels=submitted_levels, system=submitted_system)
    if "policy" in config or submitted_policy:
        candidate["policy"] = submitted_policy

    if payload["metrics_enabled"]:
        endpoint = loopback_endpoint(payload["metrics_listen"])
        if endpoint is None:
            raise RuntimeFailure("Metrics must listen on a literal loopback IP and port")
        metrics = candidate.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        metrics["listen"] = format_endpoint(*endpoint)
        candidate["metrics"] = metrics
    else:
        candidate.pop("metrics", None)

    if stats_changed:
        if payload["stats_enabled"]:
            stats = candidate.get("stats")
            candidate["stats"] = stats if isinstance(stats, dict) else {}
        else:
            candidate.pop("stats", None)

    api = candidate.get("api")
    if not isinstance(api, dict):
        api = {}
    services = list(api.get("services", []))
    if stats_changed or grpc_enabled_now:
        if payload["stats_enabled"] and payload["grpc_enabled"]:
            if "StatsService" not in services:
                services.append("StatsService")
        elif stats_changed:
            services = [item for item in services if item != "StatsService"]
    if api_binding["mode"] == "routed" and not payload["grpc_enabled"]:
        raise RuntimeFailure(
            "Traditional routed Xray API cannot be disabled by the system-config form"
        )
    if not payload["grpc_enabled"]:
        candidate.pop("api", None)
    elif grpc_enabled_now:
        api.setdefault("tag", "api")
        api["services"] = services
        candidate["api"] = api
        api["listen"] = format_endpoint(api_binding["host"], port)
    else:
        if stats_changed:
            api["services"] = services
        if api_binding["mode"] == "routed" and port != api_binding["port"]:
            candidate["inbounds"][api_binding["inbound_index"]]["port"] = port
        elif api_binding["mode"] == "direct" and port != api_binding["port"]:
            api["listen"] = format_endpoint(api_binding["host"], port)
    if stats_address is not None:
        required_endpoint = loopback_endpoint(stats_address)
        candidate_binding = xray_api_binding(candidate)
        candidate_endpoint = (
            (candidate_binding["host"], candidate_binding["port"])
            if candidate_binding["mode"] in {"direct", "routed"}
            else None
        )
        if required_endpoint is None or candidate_endpoint != required_endpoint:
            raise RuntimeFailure(
                "Xray API listener must match the operator's stats_address"
            )
    return candidate


def direct_filename(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise RuntimeFailure("Xray config file must be one direct filename")
    return value


def xray_primary_path(primary: Path, name: object | None = None) -> Path:
    direct_filename(primary.name)
    target = guarded_path(primary.parent, primary)
    if not target.exists():
        raise RuntimeFailure("Xray primary config file not found")
    if name is not None:
        requested = guarded_path(primary.parent, direct_filename(name))
        if requested != target:
            raise RuntimeFailure("Only the configured primary Xray file may be accessed")
    return target


def read_xray_primary(runtime: XrayRuntime, target: Path) -> bytes:
    return runtime.systemd.read_private(target) if runtime.systemd else read_private(target)


def decode_xray_primary(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeFailure("Xray primary config must use valid UTF-8") from exc


def xray_file_write_metadata(target: Path) -> dict:
    if target.suffix.lower() == ".jsonc":
        return {
            "writable": False,
            "read_only_reason": (
                "JSONC primary configs are read-only; consolidate to plain JSON first"
            ),
        }
    return {"writable": True, "read_only_reason": None}


def require_sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeFailure("A lowercase expected_sha256 from a successful read is required")
    return value


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
        self.subscription_access = SubscriptionAccess(runtime, journal)
        self.node_cleanup = NodeCleanup(runtime, journal, self.subscription_access)
        self.agent_management = AgentManagement(runtime.config)
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
        if path == "/api/child/agent/probe-master-url" and method == "POST":
            return await self.agent_management.probe_master_url(body)
        if path == "/api/child/agent/update-master-url" and method == "POST":
            return self.agent_management.update_master_url(body)
        if path == "/api/child/agent/switch-xray-mode" and method == "POST":
            raise RuntimeFailure(
                "Open Node runtime mode is selected during host deployment; remote switching "
                "is not supported"
            )
        if path == "/api/child/agent/switch-listen-port" and method == "POST":
            raise RuntimeFailure(
                "Open Node Agent uses outbound control connections and has no inbound listen port"
            )
        async with self.runtime.lock:
            if path == CLEANUP_ENDPOINT and method == "POST":
                return await self.node_cleanup.handle(body)
            if method != "GET" or path in {
                "/api/child/nginx/install",
                "/api/child/nginx/install-stream",
                "/api/child/nginx/remove",
                "/api/child/nginx/remove-stream",
            }:
                await self.node_cleanup.recover()
            if path == ACCESS_ENDPOINT and method == "POST":
                return await self.subscription_access.apply(body)
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
            if path == "/api/child/xray/system-config":
                await self.runtime.binding()
                raw = self.runtime.read_raw()
                config = decode_config(raw.decode())
                current_sha256 = hashlib.sha256(raw).hexdigest()
                if method == "GET":
                    return {
                        "success": True,
                        "config": xray_system_config(
                            config, stats_address=self.runtime.config.stats_address
                        ),
                        "sha256": current_sha256,
                    }
                if method == "POST":
                    if set(body) != SYSTEM_CONFIG_KEYS | {"expected_sha256"}:
                        raise RuntimeFailure(
                            "Xray system config requires a prior-read expected_sha256"
                        )
                    expected_sha256 = require_sha256(body["expected_sha256"])
                    if expected_sha256 != current_sha256:
                        raise RuntimeFailure("Xray configuration changed since it was read")
                    candidate = apply_xray_system_config(
                        config,
                        {key: body[key] for key in SYSTEM_CONFIG_KEYS},
                        stats_address=self.runtime.config.stats_address,
                    )
                    result = await self.runtime.write(
                        candidate,
                        restart=True,
                        expected=config,
                        expected_sha256=expected_sha256,
                    )
                    written = self.runtime.read_raw()
                    return {
                        **result,
                        "config": xray_system_config(
                            decode_config(written.decode()),
                            stats_address=self.runtime.config.stats_address,
                        ),
                        "sha256": hashlib.sha256(written).hexdigest(),
                    }
            if path == "/api/child/xray/config-files":
                await self.runtime.binding()
                primary = xray_primary_path(self.runtime.config.xray_config)
                if method == "GET" and query.get("file"):
                    if len(query["file"]) != 1:
                        raise RuntimeFailure("Specify exactly one Xray config filename")
                    target = xray_primary_path(primary, query["file"][0])
                    content = read_xray_primary(self.runtime, target)
                    return {
                        "success": True,
                        "path": str(target),
                        "content": decode_xray_primary(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "active": True,
                        **xray_file_write_metadata(target),
                    }
                if method == "GET":
                    content = read_xray_primary(self.runtime, primary)
                    info = primary.stat()
                    return {
                        "success": True,
                        "files": {
                            "main": [
                                {
                                    "name": primary.name,
                                    "path": str(primary),
                                    "size": len(content),
                                    "sha256": hashlib.sha256(content).hexdigest(),
                                    "mod_time": datetime.fromtimestamp(
                                        info.st_mtime, UTC
                                    ).isoformat(),
                                    "active": True,
                                    **xray_file_write_metadata(primary),
                                }
                            ]
                        },
                    }
                if method == "POST":
                    target = xray_primary_path(primary, direct_filename(body.get("file")))
                    if target.suffix.lower() == ".jsonc":
                        raise RuntimeFailure(
                            "JSONC primary configs are read-only; consolidate to plain JSON first"
                        )
                    expected_sha256 = require_sha256(body.get("expected_sha256"))
                    current = read_xray_primary(self.runtime, target)
                    if hashlib.sha256(current).hexdigest() != expected_sha256:
                        raise RuntimeFailure("Xray configuration changed since it was read")
                    expected = decode_config(decode_xray_primary(current))
                    result = await self.runtime.write(
                        body.get("content"),
                        restart=True,
                        expected=expected,
                        expected_sha256=expected_sha256,
                    )
                    written = read_xray_primary(self.runtime, target)
                    return {
                        **result,
                        "path": str(target),
                        "sha256": hashlib.sha256(written).hexdigest(),
                        "active": True,
                        **xray_file_write_metadata(target),
                    }
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
