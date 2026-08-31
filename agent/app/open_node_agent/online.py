"""Bounded sampling of the official Xray online statistics API (no resets)."""

import asyncio
import json
from ipaddress import ip_address

MAX_USERS = 256
MAX_IPS_PER_USER = 64
MAX_IPS = 4096
COLLECTION_TIMEOUT = 8
QUERY_TIMEOUT = 2
CONCURRENCY = 4


class UnsupportedOnlineAPI(ValueError):
    pass


def normalize_ip(value: str) -> str:
    # Xray's Address.String() encloses IPv6 literals in brackets.
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if "%" in value:
        raise ValueError("Scoped addresses are not online IPs")
    address = ip_address(value)
    return str(getattr(address, "ipv4_mapped", None) or address)


def policy_coverage(config: dict) -> str:
    if not isinstance(config.get("stats"), dict):
        return "not_configured"
    levels = config.get("policy", {}).get("levels", {})
    enabled = {
        name for name, value in levels.items()
        if isinstance(value, dict) and value.get("statsUserOnline") is True
    }
    if not enabled:
        return "not_configured"
    # Check actual configured users, including non-zero levels. An email shared
    # across inbounds is deliberately one Xray user identity, not two devices.
    missing = False
    for inbound in config.get("inbounds", []):
        settings = inbound.get("settings", {})
        for key in ("clients", "accounts", "users"):
            for user in settings.get(key, []):
                if isinstance(user, dict) and user.get("email"):
                    missing |= str(user.get("level", 0)) not in enabled
    return "limited" if missing else "ready"


async def collect_online(runtime, runner) -> dict:
    result = {
        "online_users": {},
        "online_collection": {
            "status": "error",
            "source": "xray_stats_api",
            "interval_seconds": runtime.config.telemetry_seconds,
        },
    }

    def finish(status, users=None):
        result["online_collection"]["status"] = status
        result["online_users"] = users or {}
        return result

    async def query(endpoint, command, *arguments, missing_ok=False):
        code, output = await runner(
            str(runtime.binary), "api", command, "--server=" + endpoint,
            "--timeout=2", *arguments, timeout=QUERY_TIMEOUT + 0.5,
        )
        if code:
            lowered = output.lower()
            if any(word in lowered for word in (
                "unimplemented", "not implemented", "unknown command", "unknown subcommand",
            )):
                raise UnsupportedOnlineAPI()
            # A connection can close between enumeration and this IP lookup.
            if missing_ok and "code = notfound" in lowered:
                return {"ips": {}}
            raise ValueError("Online query failed")
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError("Invalid online response")
        return data

    try:
        async with asyncio.timeout(COLLECTION_TIMEOUT):
            endpoint = runtime.stats_endpoint()
            if not endpoint:
                return finish("not_configured")
            host, port = endpoint.rsplit(":", 1)
            if not ip_address(host.strip("[]")).is_loopback or not 1 <= int(port) <= 65535:
                return finish("not_configured")
            coverage = policy_coverage(runtime.read())
            if coverage == "not_configured":
                return finish(coverage)
            if not await runtime.running():
                return finish("stopped")
            response = await query(endpoint, "statsgetallonlineusers")
            names = response.get("users", [])
            if not isinstance(names, list):
                raise ValueError("Invalid online users")
            emails = set()
            for name in names:
                if not isinstance(name, str) or not name.startswith("user>>>"):
                    raise ValueError("Invalid online identity")
                if not name.endswith(">>>online"):
                    raise ValueError("Invalid online identity")
                email = name[7:-9]
                if not email or len(email) > 255 or ">>>" in email:
                    raise ValueError("Invalid online identity")
                if any(ord(char) < 32 or ord(char) == 127 for char in email):
                    raise ValueError("Invalid online identity")
                emails.add(email)
            limited = coverage == "limited" or len(emails) > MAX_USERS
            semaphore = asyncio.Semaphore(CONCURRENCY)

            async def ips_for(email):
                async with semaphore:
                    data = await query(
                        endpoint, "statsonlineiplist", "-email=" + email, missing_ok=True,
                    )
                    if data.get("name", "user>>>" + email + ">>>online") != (
                        "user>>>" + email + ">>>online"
                    ):
                        raise ValueError("Online identity changed")
                    ips = data.get("ips", {})
                    if not isinstance(ips, dict):
                        raise ValueError("Invalid online IP list")
                    normalized = sorted({normalize_ip(value) for value in ips})
                    return email, normalized[:MAX_IPS_PER_USER], len(normalized) > MAX_IPS_PER_USER

            # TaskGroup cancels and awaits every child on errors/timeouts, so no
            # subprocess survives a failed sample or overlaps the next report.
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(ips_for(email)) for email in sorted(emails)[:MAX_USERS]]
            users = {}
            remaining = MAX_IPS
            for task in tasks:
                email, ips, truncated = task.result()
                limited |= truncated or len(ips) > remaining
                if ips and remaining:
                    users[email] = ips[:remaining]
                    remaining -= len(users[email])
            return finish("limited" if limited else "ready", users)
    except UnsupportedOnlineAPI:
        return finish("unsupported")
    except ExceptionGroup as exc:
        unsupported, _ = exc.split(UnsupportedOnlineAPI)
        return finish("unsupported" if unsupported else "error")
    except (ValueError, OSError, TimeoutError, TypeError, AttributeError):
        # Do not put CLI output, user identities or IPs into logs/errors.
        return finish("error")
