"""Guarded credential revocation, with durable suspension of empty listeners."""

import asyncio
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
    "socks": ("username", "password"),
    "socks5": ("username", "password"),
}


def assert_protected_xray_records_preserved(before, after):
    # Imported lazily because operations owns the shared protected-record
    # contract and imports SubscriptionAccess while constructing the dispatcher.
    from open_node_agent.operations import assert_managed_egress_preserved

    assert_managed_egress_preserved(before, after)


def client_value(client, field):
    aliases = {"username": "user", "password": "pass"}
    return client.get(field) or client.get(aliases.get(field, ""))


def client_name(client, protocol=None):
    if protocol in {"mieru", "socks", "socks5"}:
        return client.get("username") or client.get("user") or client.get("email")
    return client.get("email") or client_value(client, "username")


def revision(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class AccessEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tag: str = Field(min_length=1, max_length=255)
    protocol: Literal[
        "vless", "vmess", "trojan", "shadowsocks", "hysteria", "anytls", "snell", "mieru",
        "socks", "socks5"
    ]
    client: dict
    enabled: bool
    routing_user_additions: list[dict] = Field(default_factory=list, max_length=100)
    limiter: LimitBinding | None = None

    @model_validator(mode="after")
    def identity(self):
        identity = client_name(self.client, self.protocol)
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("Access entries require a credential identity")
        for field in SECRET_FIELDS[self.protocol]:
            value = (
                identity
                if field == "username" and self.protocol in {"mieru", "socks", "socks5"}
                else client_value(self.client, field)
            )
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Access entries require a complete credential and email")
        if self.limiter and (
            self.limiter.inbound_tag != self.tag
            or self.limiter.user.email != self.client.get("email")
        ):
            raise ValueError("Access limiter does not match the credential")
        for route in self.routing_user_additions:
            if (
                set(route) - {"marktag", "outbound_tag", "user_email"}
                or route.get("user_email") != self.client.get("email")
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
        keys = [(item.tag, client_name(item.client, item.protocol)) for item in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate access credential")
        return self


def container(protocol):
    if protocol in {"socks", "socks5"}:
        return "accounts"
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
    return tuple(client_value(client, field) for field in SECRET_FIELDS[protocol])


def skeleton(inbound):
    value = copy.deepcopy(inbound)
    if inbound["protocol"] == "snell":
        from open_node_agent.operations import snell_options

        value.setdefault("settings", {}).update(snell_options(value.get("settings", {})))
    value.setdefault("settings", {})[container(inbound["protocol"])] = []
    return value


class SubscriptionAccess:
    def __init__(self, runtime, journal, managed_protocols=None):
        self.runtime = runtime
        self.journal = journal
        self.managed_protocols = managed_protocols

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

    def suspended_inbounds(self):
        return [copy.deepcopy(record["inbound"]) for record in self.load().values()]

    async def _prepare_xray(self, entries):
        # Imported here because Operations owns the existing protocol and routing editors.
        from open_node_agent.operations import edit_client, edit_routing, routing_rule
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
        for entry in entries:
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
                if any(item.enabled and item.tag == entry.tag for item in entries):
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
        for entry in entries:
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

        for entry in entries:
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

        assert_protected_xray_records_preserved(original, config)
        ok, output = await self.runtime.validate(config)
        if not ok:
            raise RuntimeFailure(f"Xray validation failed before access changes: {output}")
        return {
            "original": original,
            "config": config,
            "saved": saved,
            "staged": staged,
            "suspended": suspended,
            "limits": limits,
        }

    async def _commit_xray(self, plan):
        config = plan["config"]
        staged = plan["staged"]
        suspended = plan["suspended"]
        saved = plan["saved"]
        assert_protected_xray_records_preserved(plan["original"], config)
        await self.runtime.limiter.provision(plan["limits"], config)
        self.save({**saved, **staged})
        try:
            # Even an idempotent retry restarts a running process: a previous interrupted
            # write may have persisted credentials without activating that configuration.
            result = await self.runtime.write(
                config, restart=True, expected=plan["original"]
            )
            assert_protected_xray_records_preserved(
                plan["original"], self.runtime.read()
            )
        except BaseException:
            self.save(saved)
            raise
        for tag in suspended:
            staged[tag]["phase"] = "suspended"
        try:
            self.save(staged)
        except BaseException:
            try:
                assert_protected_xray_records_preserved(config, plan["original"])
                await asyncio.shield(
                    self.runtime.write(
                        plan["original"], restart=True, expected=config
                    )
                )
                assert_protected_xray_records_preserved(
                    config, self.runtime.read()
                )
                self.save(saved)
            except Exception as rollback_error:
                raise RuntimeFailure(
                    "Xray access journal failed after apply and runtime rollback needs review"
                ) from rollback_error
            raise
        return result

    @staticmethod
    def _access_result(request, result=None):
        return {
            **(result or {"success": True, "restart_required": False}),
            "access": {
                "applied": True,
                "revision": request.revision,
                "enabled": sum(item.enabled for item in request.entries),
                "disabled": sum(not item.enabled for item in request.entries),
            },
        }

    async def apply(self, body):
        try:
            request = AccessRequest.model_validate(body)
        except ValidationError as exc:
            raise RuntimeFailure("Invalid subscription access payload") from exc
        if revision(body["entries"]) != request.revision:
            raise RuntimeFailure("Access revision does not match the requested entries")

        if self.managed_protocols:
            self.managed_protocols.assert_xray_compatible(self.runtime.read())
        managed_tags = self.managed_protocols.tags() if self.managed_protocols else set()
        managed_entries = [entry for entry in request.entries if entry.tag in managed_tags]
        xray_entries = [entry for entry in request.entries if entry.tag not in managed_tags]

        managed_before = None
        managed_after = None
        if managed_entries:
            managed_before = self.managed_protocols.load()
            if managed_before is None:
                raise RuntimeFailure("Managed protocol state disappeared during access update")
            managed_after = self.managed_protocols.access_candidate(managed_entries)
            if managed_after is None:
                raise RuntimeFailure("Managed access target is missing")
            await self.managed_protocols.validate_request(managed_after)

        xray_plan = await self._prepare_xray(xray_entries) if xray_entries else None

        # Both complete candidates have now passed their native runtime validators.
        # Commit Mihomo first, so any Xray/limiter failure can restore Mihomo from
        # its compare-and-swap snapshot without weakening the existing Xray path.
        if managed_after is not None:
            await self.managed_protocols.commit_request(
                managed_after, expected=managed_before
            )
        try:
            xray_result = await self._commit_xray(xray_plan) if xray_plan else None
        except BaseException:
            if managed_after is not None:
                try:
                    await self.managed_protocols.rollback_request(
                        managed_before, expected=managed_after
                    )
                except Exception as rollback_error:
                    raise RuntimeFailure(
                        "Xray access apply failed and Mihomo rollback requires operator review"
                    ) from rollback_error
            raise
        return self._access_result(request, xray_result)
