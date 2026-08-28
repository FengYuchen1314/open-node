"""Guarded credential revocation, with durable suspension of empty listeners."""

import copy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from open_node_agent.limiter import LimitBinding
from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure

ENDPOINT = "/api/child/subscription-access"
STATE_KEY = "subscription_suspended_inbounds"
SECRET_FIELDS = {
    "vless": ("id",),
    "vmess": ("id",),
    "trojan": ("password",),
    "shadowsocks": ("password",),
    "hysteria": ("auth",),
    "anytls": ("password",),
    "snell": ("psk",),
    "mieru": ("username", "password"),
}


def revision(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class AccessEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tag: str = Field(min_length=1, max_length=255)
    protocol: Literal[
        "vless", "vmess", "trojan", "shadowsocks", "hysteria", "anytls", "snell", "mieru"
    ]
    client: dict
    enabled: bool
    routing_user_additions: list[dict] = Field(default_factory=list, max_length=100)
    limiter: LimitBinding | None = None

    @model_validator(mode="after")
    def identity(self):
        for field in ("email", *SECRET_FIELDS[self.protocol]):
            if not isinstance(self.client.get(field), str) or not self.client[field].strip():
                raise ValueError("Access entries require a complete credential and email")
        if self.limiter and (
            self.limiter.inbound_tag != self.tag or self.limiter.user.email != self.client["email"]
        ):
            raise ValueError("Access limiter does not match the credential")
        for route in self.routing_user_additions:
            if (
                set(route) - {"marktag", "outbound_tag", "user_email"}
                or route.get("user_email") != self.client["email"]
                or not (route.get("marktag") or route.get("outbound_tag"))
                or any(not isinstance(value, str) or not value for value in route.values())
            ):
                raise ValueError("Invalid access routing selector")
        return self


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: list[AccessEntry] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def distinct(self):
        keys = [(item.tag, item.client["email"]) for item in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate access credential")
        return self


def container(protocol):
    return "users" if protocol in {"anytls", "snell", "mieru"} else "clients"


def clients(inbound):
    settings = inbound.get("settings", {})
    values = (
        settings.get(container(inbound["protocol"]), []) if isinstance(settings, dict) else None
    )
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise RuntimeFailure("Access target has an invalid credential list")
    return values


def secret(client, protocol):
    return tuple(client.get(field) for field in SECRET_FIELDS[protocol])


def skeleton(inbound):
    value = copy.deepcopy(inbound)
    if inbound["protocol"] == "snell":
        from open_node_agent.operations import snell_options

        value.setdefault("settings", {}).update(snell_options(value.get("settings", {})))
    value.setdefault("settings", {})[container(inbound["protocol"])] = []
    return value


class SubscriptionAccess:
    def __init__(self, runtime, journal):
        self.runtime = runtime
        self.journal = journal

    def load(self):
        row = self.journal.db.execute(
            "SELECT value FROM settings WHERE key=?", (STATE_KEY,)
        ).fetchone()
        if row is None:
            return {}
        if len(row[0]) > MAX_CONFIG_BYTES * 2:
            raise RuntimeFailure("Suspended inbound journal exceeds its size limit")
        value = json.loads(row[0])
        if not isinstance(value, dict) or any(
            not isinstance(tag, str)
            or not isinstance(record, dict)
            or record.get("phase") not in {"prepared", "suspended"}
            or type(record.get("index")) is not int
            or record["index"] < 0
            or not isinstance(record.get("inbound"), dict)
            or record["inbound"].get("tag") != tag
            or record["inbound"].get("protocol") not in SECRET_FIELDS
            or not isinstance(record.get("config_revision"), str)
            for tag, record in value.items()
        ):
            raise RuntimeFailure("Invalid suspended inbound journal")
        return value

    def save(self, value):
        encoded = json.dumps(value)
        if len(encoded.encode()) > MAX_CONFIG_BYTES * 2:
            raise RuntimeFailure("Suspended inbound journal exceeds its size limit")
        with self.journal.db:
            self.journal.db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)", (STATE_KEY, encoded)
            )

    async def apply(self, body):
        # Imported here because Operations owns the existing protocol and routing editors.
        from open_node_agent.operations import edit_client, edit_routing, routing_rule

        try:
            request = AccessRequest.model_validate(body)
        except ValidationError as exc:
            raise RuntimeFailure("Invalid subscription access payload") from exc
        if revision(body["entries"]) != request.revision:
            raise RuntimeFailure("Access revision does not match the requested entries")
        await self.runtime.binding()
        original = self.runtime.read()
        config = copy.deepcopy(original)
        inbounds = config.setdefault("inbounds", [])
        if not isinstance(inbounds, list) or any(not isinstance(item, dict) for item in inbounds):
            raise RuntimeFailure("Access configuration has invalid inbounds")
        saved = self.load()
        staged = copy.deepcopy(saved)
        targets = {}
        restoring = []
        for entry in request.entries:
            if entry.tag in targets:
                if targets[entry.tag]["protocol"] != entry.protocol:
                    raise RuntimeFailure("Access entries disagree on the inbound protocol")
                continue
            matches = [item for item in inbounds if item.get("tag") == entry.tag]
            if len(matches) > 1:
                raise RuntimeFailure("Access tag must identify exactly one inbound")
            record = saved.get(entry.tag)
            if matches:
                target = matches[0]
                if record and skeleton(target) != record["inbound"]:
                    raise RuntimeFailure("Suspended inbound was independently modified")
            elif record:
                if record["phase"] == "prepared" and record["config_revision"] != revision(
                    original
                ):
                    raise RuntimeFailure("Interrupted listener suspension needs operator review")
                target = copy.deepcopy(record["inbound"])
                restoring.append((record["index"], target))
            else:
                if any(item.enabled and item.tag == entry.tag for item in request.entries):
                    raise RuntimeFailure(
                        "Access target is missing and was not suspended by this Agent"
                    )
                continue
            if target.get("protocol") != entry.protocol:
                raise RuntimeFailure("Access target protocol changed")
            targets[entry.tag] = target

        for index, target in sorted(restoring, key=lambda item: item[0]):
            inbounds.insert(min(index, len(inbounds)), target)

        limits = []
        for entry in request.entries:
            target = targets.get(entry.tag)
            if target is None:
                continue
            values = clients(target)
            matches = [item for item in values if item.get("email") == entry.client["email"]]
            if len(matches) > 1 or (
                matches
                and secret(matches[0], entry.protocol) != secret(entry.client, entry.protocol)
            ):
                raise RuntimeFailure("Access credential identity changed; no changes were applied")
            if entry.enabled:
                if not matches:
                    edit_client(target, entry.client)
                for route in entry.routing_user_additions:
                    rule = routing_rule(config.get("routing", {}).get("rules", []), route)
                    if (
                        route.get("outbound_tag")
                        and rule.get("outboundTag") != route["outbound_tag"]
                    ):
                        raise RuntimeFailure("Managed access routing target changed")
                    edit_routing(config, {**route, "action": "add_user_to_rule"})
                if entry.limiter:
                    limits.append(entry.limiter.model_dump())
            elif matches:
                edit_client(target, entry.client, remove=True)

        for entry in request.entries:
            target = targets.get(entry.tag)
            if (
                target is not None
                and not entry.enabled
                and any(
                    secret(item, entry.protocol) == secret(entry.client, entry.protocol)
                    for item in clients(target)
                )
            ):
                raise RuntimeFailure("Revoked credential is shared by another runtime user")

        suspended = set()
        positions = {item["tag"]: index for index, item in enumerate(inbounds) if "tag" in item}
        for tag, target in targets.items():
            if clients(target):
                staged.pop(tag, None)
            else:
                # Some protocols reject zero users or fall back to a server password.
                # Remove the listener and retain only its empty, privately journaled template.
                staged[tag] = {
                    "inbound": skeleton(target),
                    "index": positions[tag],
                    "phase": "prepared",
                }
                suspended.add(tag)
                inbounds.remove(target)
        for tag in suspended:
            staged[tag]["config_revision"] = revision(config)

        ok, output = await self.runtime.validate(config)
        if not ok:
            raise RuntimeFailure(f"Xray validation failed before access changes: {output}")
        await self.runtime.limiter.provision(limits, config)
        self.save({**saved, **staged})
        # Even an idempotent retry restarts a running process: a previous interrupted
        # write may have persisted credentials without activating that configuration.
        result = await self.runtime.write(config, restart=True, expected=original)
        for tag in suspended:
            staged[tag]["phase"] = "suspended"
        self.save(staged)
        return {
            **result,
            "access": {
                "applied": True,
                "revision": request.revision,
                "enabled": sum(item.enabled for item in request.entries),
                "disabled": sum(not item.enabled for item in request.entries),
            },
        }
