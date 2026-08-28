"""Revision-guarded resource removal with recoverable runtime and journal updates."""

import copy
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from open_node_agent.runtime import MAX_CONFIG_BYTES, RuntimeFailure, atomic_write
from open_node_agent.subscription_access import STATE_KEY, revision

ENDPOINT = "/api/child/node-cleanup"


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["preview", "apply", "status"]
    inbound_tags: list[str] = Field(default_factory=list, max_length=1000)
    outbound_tags: list[str] = Field(default_factory=list, max_length=1000)
    operation_id: str | None = Field(default=None, max_length=36)
    expected_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    acknowledge_runtime_restart: bool = False

    @model_validator(mode="after")
    def validate_request(self):
        for tags in (self.inbound_tags, self.outbound_tags):
            if len(tags) != len(set(tags)) or any(
                not tag.strip() or len(tag) > 255 or any(ord(char) < 32 for char in tag)
                for tag in tags
            ):
                raise ValueError("Cleanup tags must be distinct nonempty labels")
        if self.operation_id and str(UUID(self.operation_id)) != self.operation_id:
            raise ValueError("Cleanup identity must be a canonical UUID")
        if self.action == "status":
            if not self.operation_id or self.inbound_tags or self.outbound_tags:
                raise ValueError("Status requires only an operation identity")
        elif not self.inbound_tags and not self.outbound_tags:
            raise ValueError("Select at least one cleanup target")
        if self.action == "apply" and not (
            self.operation_id and self.expected_revision and self.acknowledge_runtime_restart
        ):
            raise ValueError("Cleanup requires its identity, revision and restart acknowledgment")
        return self


def tagged(config, key):
    entries = config.get(key, [])
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise RuntimeFailure("Cleanup requires structured runtime resources")
    tags = [item.get("tag") for item in entries if item.get("tag") is not None]
    if any(not isinstance(tag, str) for tag in tags) or len(tags) != len(set(tags)):
        raise RuntimeFailure("Duplicate or invalid runtime tags require review")
    return entries


def proxy_targets(outbound):
    proxy, stream = outbound.get("proxySettings", {}), outbound.get("streamSettings", {})
    if not isinstance(proxy, dict) or not isinstance(stream, dict):
        raise RuntimeFailure("Invalid outbound proxy references require review")
    options = stream.get("sockopt", {})
    if not isinstance(options, dict):
        raise RuntimeFailure("Invalid outbound socket options require review")
    targets = [proxy.get("tag"), options.get("dialerProxy")]
    if any(value is not None and not isinstance(value, str) for value in targets):
        raise RuntimeFailure("Invalid outbound proxy tags require review")
    return set(targets) - {None, ""}


def candidate(config, suspended, limits, request):
    value, saved, policies = copy.deepcopy((config, suspended, limits))
    inbounds, outbounds = tagged(value, "inbounds"), tagged(value, "outbounds")
    inbound_tags, outbound_tags = set(request.inbound_tags), set(request.outbound_tags)
    if value.get("api", {}).get("tag") in inbound_tags:
        raise RuntimeFailure("The runtime API listener cannot be removed as a product node")
    # Chained outbounds cannot survive removal of their dialer; preserve unrelated chains.
    while True:
        if any(not item.get("tag") and proxy_targets(item) & outbound_tags for item in outbounds):
            raise RuntimeFailure("Name dependent outbounds before removing their dialer")
        dependent = {
            item["tag"]
            for item in outbounds
            if item.get("tag") and proxy_targets(item) & outbound_tags
        }
        if dependent <= outbound_tags:
            break
        outbound_tags.update(dependent)
    routing = value.get("routing", {})
    if not isinstance(routing, dict) or not isinstance(routing.get("rules", []), list):
        raise RuntimeFailure("Invalid routing configuration requires review")
    for balancer in routing.get("balancers", []):
        if balancer.get("fallbackTag") in outbound_tags or any(
            tag.startswith(prefix)
            for tag in outbound_tags
            for prefix in balancer.get("selector", [])
        ):
            raise RuntimeFailure("Update balancer references before removing their outbounds")
    kept_rules, removed_rules, changed_rules = [], 0, 0
    for rule in routing.get("rules", []):
        if not isinstance(rule, dict):
            raise RuntimeFailure("Invalid routing rule requires review")
        if rule.get("outboundTag") in outbound_tags:
            removed_rules += 1
            continue
        refs = rule.get("inboundTag", [])
        if not isinstance(refs, list) or any(not isinstance(tag, str) for tag in refs):
            raise RuntimeFailure("Invalid inbound routing references require review")
        if inbound_tags.intersection(refs):
            kept = [tag for tag in refs if tag not in inbound_tags]
            if not kept:
                removed_rules += 1
                continue
            rule["inboundTag"] = kept
            changed_rules += 1
        kept_rules.append(rule)
    if "rules" in routing:
        routing["rules"] = kept_rules
    selected_inbounds = [item for item in inbounds if item.get("tag") in inbound_tags]
    selected_outbounds = [item for item in outbounds if item.get("tag") in outbound_tags]
    value["inbounds"] = [item for item in inbounds if item.get("tag") not in inbound_tags]
    value["outbounds"] = [item for item in outbounds if item.get("tag") not in outbound_tags]
    if outbounds and not value["outbounds"]:
        raise RuntimeFailure("At least one outbound must remain after node cleanup")
    saved = {tag: record for tag, record in saved.items() if tag not in inbound_tags}
    policies["inbounds"] = [
        item for item in policies["inbounds"] if item["inbound_tag"] not in inbound_tags
    ]
    impact = {
        "inbound_tags": sorted(item["tag"] for item in selected_inbounds),
        "outbound_tags": sorted(item["tag"] for item in selected_outbounds),
        "suspended_tags": sorted(set(suspended).intersection(inbound_tags)),
        "removed_rules": removed_rules,
        "changed_rules": changed_rules,
        "removed_limiter_policies": len(limits["inbounds"]) - len(policies["inbounds"]),
        "default_outbound_changed": bool(outbounds and outbounds[0] in selected_outbounds),
    }
    return {"config": value, "suspended": saved, "limits": policies}, impact


class NodeCleanup:
    def __init__(self, runtime, journal, access):
        self.runtime, self.journal, self.access = runtime, journal, access
        self.journal.db.executescript("""
            CREATE TABLE IF NOT EXISTS node_cleanup_jobs (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                phase TEXT NOT NULL CHECK (phase IN ('prepared', 'completed')),
                state TEXT NOT NULL, result TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_prepared_node_cleanup
                ON node_cleanup_jobs(phase) WHERE phase = 'prepared';
        """)

    def current(self):
        return {
            "config": self.runtime.read(),
            "suspended": self.access.load(),
            "limits": self.runtime.limiter.document(),
        }

    def pending(self):
        return self.journal.db.execute(
            "SELECT id, state, result FROM node_cleanup_jobs WHERE phase='prepared'"
        ).fetchone()

    def status(self, identifier):
        row = self.journal.db.execute(
            "SELECT phase, result FROM node_cleanup_jobs WHERE id=?", (identifier,)
        ).fetchone()
        if not row:
            raise RuntimeFailure("Node cleanup operation not found")
        return {
            "success": True,
            "node_cleanup": {**json.loads(row[1]), "applied": row[0] == "completed"},
        }

    async def recover(self):
        pending = self.pending()
        if not pending:
            return
        identifier, raw, _ = pending
        if len(raw.encode()) > MAX_CONFIG_BYTES * 8:
            raise RuntimeFailure("Node cleanup recovery record exceeds its size limit")
        state = json.loads(raw)
        await self.runtime.binding()
        current = self.current()
        original, desired = state["original"], state["desired"]
        if any(current[key] not in (original[key], desired[key]) for key in current):
            raise RuntimeFailure(
                "Interrupted node cleanup conflicts with host edits; review is required"
            )
        if current["config"] != desired["config"] and current["limits"] != original["limits"]:
            atomic_write(self.runtime.limiter.path, json.dumps(original["limits"]).encode())
        await self.runtime.write(desired["config"], restart=True, expected=current["config"])
        if self.runtime.read() != desired["config"]:
            raise RuntimeFailure("Host configuration changed during cleanup; review is required")
        limits = self.runtime.limiter.document()
        if limits not in (original["limits"], desired["limits"]):
            raise RuntimeFailure("Host limiter state changed during cleanup; review is required")
        if limits != desired["limits"]:
            # Keep old caps available if the first runtime restart rolls back. Only
            # retire those caps after the selected listeners are actually gone.
            atomic_write(self.runtime.limiter.path, json.dumps(desired["limits"]).encode())
            if await self.runtime.running():
                await self.runtime.write(
                    desired["config"], restart=True, expected=desired["config"]
                )
        if (
            self.runtime.read() != desired["config"]
            or self.runtime.limiter.document() != desired["limits"]
            or self.access.load() != current["suspended"]
        ):
            raise RuntimeFailure("Host state changed during node cleanup; review is required")
        with self.journal.db:
            self.journal.db.execute(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                (STATE_KEY, json.dumps(desired["suspended"])),
            )
            self.journal.db.execute(
                "UPDATE node_cleanup_jobs SET phase='completed', state='{}' WHERE id=?",
                (identifier,),
            )

    async def handle(self, body):
        try:
            request = CleanupRequest.model_validate(body)
        except ValidationError as exc:
            raise RuntimeFailure("Invalid node cleanup request") from exc
        if request.action == "status":
            return self.status(request.operation_id)
        fingerprint = revision(request.model_dump())
        if request.action == "apply":
            previous = self.journal.db.execute(
                "SELECT fingerprint, phase FROM node_cleanup_jobs WHERE id=?",
                (request.operation_id,),
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise RuntimeFailure("Node cleanup identity was reused with different content")
                if previous[1] == "prepared":
                    await self.recover()
                return self.status(request.operation_id)
        if self.pending():
            raise RuntimeFailure(
                "An earlier node cleanup must finish before another preview or operation"
            )
        await self.runtime.binding()
        original = self.current()
        desired, impact = candidate(
            original["config"], original["suspended"], original["limits"], request
        )
        expected = revision(
            {
                "state": original,
                "inbound_tags": sorted(request.inbound_tags),
                "outbound_tags": sorted(request.outbound_tags),
            }
        )
        result = {"revision": expected, "impact": impact, "applied": False}
        if request.action == "preview":
            return {"success": True, "node_cleanup": result}
        if expected != request.expected_revision:
            raise RuntimeFailure("Runtime resources changed; preview node cleanup again")
        valid, _ = await self.runtime.validate(desired["config"])
        if not valid:
            raise RuntimeFailure("Xray rejected the node cleanup configuration")
        if self.current() != original:
            raise RuntimeFailure("Host state changed during cleanup validation; preview again")
        result["operation_id"] = request.operation_id
        state = json.dumps({"original": original, "desired": desired})
        if len(state.encode()) > MAX_CONFIG_BYTES * 8:
            raise RuntimeFailure("Node cleanup recovery record exceeds its size limit")
        with self.journal.db:
            self.journal.db.execute(
                "INSERT INTO node_cleanup_jobs VALUES (?, ?, 'prepared', ?, ?)",
                (request.operation_id, fingerprint, state, json.dumps(result)),
            )
        await self.recover()
        return self.status(request.operation_id)
