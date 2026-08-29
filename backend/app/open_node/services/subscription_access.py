"""Persist subscription access intent and reconcile it through the Agent queue."""

import asyncio
import copy
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.subscriptions import SubscriptionDueTrafficResetRequest
from open_node.services.inventory import (
    AgentModel,
    CommandModel,
    ManagedNodeModel,
    ProductUserModel,
    ProductUserNotFoundError,
    ServerModel,
    SubscriptionAccessModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
)
from open_node.services.user_limits import effective_limits

ENDPOINT = "/api/child/subscription-access"
TERMINAL = {"succeeded", "failed", "skipped"}
log = logging.getLogger(__name__)


class SubscriptionAccessConflict(ValueError):
    pass


def revision(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def protocol(value):
    return {"ss": "shadowsocks", "hy2": "hysteria", "hysteria2": "hysteria"}.get(value, value)


def command_clients(command):
    if command.path != "/api/child/batch-apply" or not isinstance(command.body, dict):
        return []
    entries = command.body.get("inbound_clients", [])
    return (
        [
            item
            for item in entries
            if isinstance(item, dict)
            and isinstance(item.get("tag"), str)
            and isinstance(item.get("client"), dict)
            and isinstance(item["client"].get("email"), str)
        ]
        if isinstance(entries, list)
        else []
    )


def access_entries(command):
    body = command.body
    if not isinstance(body, dict) or not isinstance(body.get("entries"), list):
        return None
    entries = body["entries"]
    if not entries or any(
        not isinstance(item, dict) or type(item.get("enabled")) is not bool for item in entries
    ):
        return None
    return entries if body.get("revision") == revision(entries) else None


class SubscriptionAccessCoordinator:
    def __init__(self, store):
        self.store = store

    def adopt_command(self, session, command, now):
        for item in command_clients(command):
            matches = session.scalars(
                select(SubscriptionCredentialModel).where(
                    SubscriptionCredentialModel.server_id == command.server_id,
                    SubscriptionCredentialModel.inbound_tag == item.get("tag"),
                    SubscriptionCredentialModel.email == item["client"].get("email"),
                )
            ).all()
            for credential in matches:
                if any(
                    item["client"].get(key) != value for key, value in credential.credential.items()
                ):
                    continue
                row = session.scalar(
                    select(SubscriptionAccessModel).where(
                        SubscriptionAccessModel.username == credential.username,
                        SubscriptionAccessModel.server_id == command.server_id,
                    )
                )
                if row is None:
                    row = SubscriptionAccessModel(
                        id=str(uuid4()),
                        username=credential.username,
                        server_id=command.server_id,
                        bindings=[],
                        updated_at=now,
                    )
                    session.add(row)
                bindings = copy.deepcopy(row.bindings)
                existing = next(
                    (
                        value
                        for value in bindings
                        if value["tag"] == item["tag"]
                        and value["client"]["email"] == credential.email
                    ),
                    None,
                )
                if existing:
                    if (
                        credential.node_id not in existing["node_ids"]
                        and existing["client"] == item["client"]
                    ):
                        existing["node_ids"].append(credential.node_id)
                else:
                    bindings.append(
                        {
                            "tag": item["tag"],
                            "protocol": protocol(credential.protocol),
                            "client": copy.deepcopy(item["client"]),
                            "node_ids": [credential.node_id],
                        }
                    )
                row.bindings = sorted(
                    bindings, key=lambda value: (value["tag"], value["client"]["email"])
                )
                if command.status not in TERMINAL:
                    row.applied_revision = None
                session.flush()

    def backfill(self):
        with self.store._coordinated_session() as session:
            for command in session.scalars(
                select(CommandModel)
                .where(
                    CommandModel.path == "/api/child/batch-apply",
                    CommandModel.method == "POST",
                )
                .order_by(CommandModel.created_at)
            ):
                self.adopt_command(session, command, datetime.now(UTC))
            session.commit()

    @staticmethod
    def rows(session, username=None):
        query = select(SubscriptionAccessModel).order_by(
            SubscriptionAccessModel.username, SubscriptionAccessModel.server_id
        )
        if username is not None:
            query = query.where(SubscriptionAccessModel.username == username)
        return list(session.scalars(query))

    def authorize(self, session, user, plan, batches, now):
        by_server = {str(batch.server_id): batch.body for batch in batches}
        additions = {}
        for node_id in self.store._effective_subscription_node_ids(session, user, plan):
            node = session.get(ManagedNodeModel, node_id)
            if not node or not node.enabled or node.removal_id:
                continue
            if not node.inbound_tag:
                raise SubscriptionAccessConflict(
                    "Managed access requires an authenticated inbound tag"
                )
            server = session.get(ServerModel, node.server_id)
            if server is None:
                continue
            credential = self.store._get_or_create_subscription_credential(
                session, user, node, server
            )
            client = self.store._provisioning_client_from_credential(
                user, plan, node, server, credential
            )
            body = by_server.get(server.id, {})
            if not any(
                item["tag"] == node.inbound_tag and item["client"] == client
                for item in body.get("inbound_clients", [])
            ):
                raise SubscriptionAccessConflict(
                    "Provisioning batch does not match the managed credential"
                )
            key = (node.inbound_tag, client["email"])
            entry = additions.setdefault(server.id, {}).setdefault(
                key,
                {
                    "tag": node.inbound_tag,
                    "protocol": protocol(node.protocol),
                    "client": copy.deepcopy(client),
                    "node_ids": [],
                },
            )
            if entry["client"] != client or entry["protocol"] != protocol(node.protocol):
                raise SubscriptionAccessConflict("Managed credentials sharing an email disagree")
            entry["node_ids"].append(node_id)

        for server_id, entries in additions.items():
            row = session.scalar(
                select(SubscriptionAccessModel).where(
                    SubscriptionAccessModel.username == user.username,
                    SubscriptionAccessModel.server_id == server_id,
                )
            )
            if row is None:
                row = SubscriptionAccessModel(
                    id=str(uuid4()),
                    username=user.username,
                    server_id=server_id,
                    bindings=[],
                    updated_at=now,
                )
                session.add(row)
            merged = {
                (item["tag"], item["client"]["email"]): copy.deepcopy(item) for item in row.bindings
            }
            for key, entry in entries.items():
                previous = merged.get(key)
                if previous:
                    if (
                        previous["client"] != entry["client"]
                        or previous["protocol"] != entry["protocol"]
                    ):
                        raise SubscriptionAccessConflict(
                            "A tracked credential changed; review its runtime identity first"
                        )
                    entry["node_ids"] = sorted(set(previous["node_ids"] + entry["node_ids"]))
                merged[key] = entry
            row.bindings = [merged[key] for key in sorted(merged)]
            row.retry_at = None
            row.updated_at = now
        session.flush()

    def desired(self, session, row, now):
        user = session.get(ProductUserModel, row.username)
        plan = (
            session.get(SubscriptionPlanModel, user.current_plan_id)
            if user.current_plan_id
            else None
        )
        quota = self.store._subscription_quota_status(session, user, plan, now)
        reason = (
            "removing"
            if user.removal_id
            else "disabled"
            if not user.is_active
            else "no_plan"
            if not plan
            else "expired"
            if quota.expired
            else "quota_exceeded"
            if quota.over_quota
            else "available"
        )
        agent = session.scalar(select(AgentModel).where(AgentModel.server_id == row.server_id))
        native = bool(agent and agent.capability_native_limiter)
        entries, reasons = [], []
        for binding in row.bindings:
            nodes = []
            if quota.available:
                for node_id in binding["node_ids"]:
                    node = session.get(ManagedNodeModel, node_id)
                    if (
                        node
                        and node.enabled
                        and not node.removal_id
                        and self.store._subscription_node_allowed(
                            session, user, plan, node_id
                        )
                        and node.server_id == row.server_id
                        and node.inbound_tag == binding["tag"]
                        and protocol(node.protocol) == binding["protocol"]
                    ):
                        nodes.append(node)
            enabled = bool(nodes)
            entry = {key: copy.deepcopy(binding[key]) for key in ("tag", "protocol", "client")}
            entry.update(enabled=enabled, routing_user_additions=[], limiter=None)
            reasons.append(reason if reason != "available" or enabled else "node_not_in_plan")
            if enabled:
                limits = [effective_limits(user, plan, node) for node in nodes]
                speed = min(
                    (
                        int(limit.speed_limit_mbps * 125000)
                        for limit in limits
                        if limit.speed_limit_mbps
                    ),
                    default=0,
                )
                devices = min(
                    (limit.device_limit for limit in limits if limit.device_limit),
                    default=0,
                )
                if speed or devices or native or plan.auto_speed_rules:
                    group = json.dumps(
                        [row.username, row.server_id, binding["tag"]], separators=(",", ":")
                    ).encode()
                    entry["limiter"] = {
                        "inbound_tag": binding["tag"],
                        "user": {
                            "uid": 0,
                            "email": binding["client"]["email"],
                            "speed_limit": speed,
                            "device_limit": devices,
                            "conn_group": "account-" + hashlib.sha256(group).hexdigest(),
                        },
                    }
                    if plan.auto_speed_rules:
                        entry["limiter"]["user"]["auto_speed_rules"] = copy.deepcopy(
                            plan.auto_speed_rules
                        )
                for node in nodes:
                    route = {"user_email": binding["client"]["email"]}
                    if node.routed_rule_marktag:
                        route["marktag"] = node.routed_rule_marktag
                    if node.routed_outbound_tag:
                        route["outbound_tag"] = node.routed_outbound_tag
                    if len(route) > 1 and route not in entry["routing_user_additions"]:
                        entry["routing_user_additions"].append(route)
            entries.append(entry)
        return {"revision": revision(entries), "entries": entries}, reasons

    def skip(self, session, command, now, error):
        command.status, command.result_error = "skipped", error
        command.completed_at = command.updated_at = now
        session.flush()
        self.store._advance_command_dependents(session, command, now)
        self.store._change_sets().advance_after_result(session, command, now)

    def reconcile(self, session, now, *, username=None, force=False, timeout_ms=60_000):
        commands = []
        for row in self.rows(session, username):
            body, _ = self.desired(session, row, now)
            legacy_in_flight = any(
                command_clients(command)
                for command in session.scalars(
                    select(CommandModel).where(
                        CommandModel.server_id == row.server_id,
                        CommandModel.path == "/api/child/batch-apply",
                        CommandModel.attempts > 0,
                        CommandModel.status.not_in(TERMINAL),
                    )
                )
            )
            if legacy_in_flight:
                continue
            previous = session.get(CommandModel, row.command_id) if row.command_id else None
            if previous and previous.status not in TERMINAL:
                if previous.attempts or previous.body == body:
                    commands.append(previous)
                    continue
                self.skip(session, previous, now, "Superseded by current subscription access state")
            if row.applied_revision == body["revision"] and not force:
                continue
            if (
                row.retry_at
                and now < self.store._aware_datetime(row.retry_at)
                and not force
                and previous
                and previous.body == body
            ):
                continue
            command = self.store._create_command_model(
                session,
                session.get(ServerModel, row.server_id),
                AgentCommandCreate(method="POST", path=ENDPOINT, body=body, timeout_ms=timeout_ms),
                now=now,
            )
            session.flush()
            row.command_id, row.updated_at = command.id, now
            if force:
                row.applied_revision = None
            row.last_error, row.retry_at = None, None
            commands.append(command)
        return commands

    def can_lease(self, session, command, now):
        if not self.store._node_management().can_lease(session, command, now):
            return False
        if not command.attempts and self.store._user_management().retired_restore(session, command):
            self.skip(session, command, now, "Not sent: user removal retired these credentials")
            self.after_result(session, command, now)
            return False
        legacy = command_clients(command)
        if legacy and not command.attempts:
            self.adopt_command(session, command, now)
            disabled = set()
            desired = {}
            for row in session.scalars(
                select(SubscriptionAccessModel).where(
                    SubscriptionAccessModel.server_id == command.server_id
                )
            ):
                body, _ = self.desired(session, row, now)
                desired.update(
                    {(entry["tag"], entry["client"]["email"]): entry for entry in body["entries"]}
                )
                disabled.update(
                    (entry["tag"], entry["client"]["email"])
                    for entry in body["entries"]
                    if not entry["enabled"]
                )
            if any((item.get("tag"), item["client"].get("email")) in disabled for item in legacy):
                self.skip(
                    session,
                    command,
                    now,
                    "Not sent: legacy batch would restore unavailable subscription access",
                )
                return False
            requested = {
                (item.get("inbound_tag"), item.get("user", {}).get("email")): item.get("user", {})
                for item in command.body.get("limiter_users", [])
                if isinstance(item, dict) and isinstance(item.get("user"), dict)
            }
            for item in legacy:
                key = (item["tag"], item["client"]["email"])
                entry = desired.get(key)
                if entry is None:
                    continue
                expected = (entry.get("limiter") or {}).get("user", {})
                actual = requested.get(key, {})
                if (
                    any(
                        actual.get(field, 0) != expected.get(field, 0)
                        for field in ("speed_limit", "device_limit")
                    )
                    or (actual.get("auto_speed_rules", []) != expected.get("auto_speed_rules", []))
                    or (
                        expected.get("device_limit")
                        and actual.get("conn_group") != expected.get("conn_group")
                    )
                ):
                    self.skip(
                        session,
                        command,
                        now,
                        "Not sent: legacy batch has outdated subscriber limits",
                    )
                    return False
        if command.path != ENDPOINT:
            return True
        agent = session.scalar(select(AgentModel).where(AgentModel.server_id == command.server_id))
        error = None
        entries = access_entries(command)
        if entries is None:
            error = "Not sent: invalid subscription access payload"
        elif not agent or not agent.capability_subscription_access:
            error = "Not sent: upgrade the Agent for managed subscription access"
        elif any(entry.get("limiter") for entry in entries) and not agent.capability_native_limiter:
            error = "Not sent: managed plan limits require native limiter support"
        elif (
            any(
                (entry.get("limiter") or {}).get("user", {}).get("auto_speed_rules")
                for entry in entries
            )
            and not agent.capability_user_auto_speed_rules
        ):
            error = "Not sent: upgrade the Agent for per-user automatic speed rules"
        row = session.scalar(
            select(SubscriptionAccessModel).where(SubscriptionAccessModel.command_id == command.id)
        )
        if row and not command.attempts and self.desired(session, row, now)[0] != command.body:
            error = "Not sent: subscription access changed before dispatch"
        if error:
            if not command.attempts:
                self.skip(session, command, now, error)
                self.after_result(session, command, now)
            return False
        return True

    @staticmethod
    def confirmation_error(command, body):
        if command.path != ENDPOINT:
            return None
        if not command.attempts:
            return "Unleased subscription access commands cannot be confirmed"
        confirmation = body.get("access")
        entries = access_entries(command)
        if entries is None:
            return "Invalid subscription access command cannot be confirmed"
        if (
            not isinstance(confirmation, dict)
            or confirmation.get("applied") is not True
            or confirmation.get("revision") != command.body.get("revision")
            or type(confirmation.get("enabled")) is not int
            or confirmation["enabled"] != sum(item["enabled"] for item in entries)
            or type(confirmation.get("disabled")) is not int
            or confirmation["disabled"] != sum(not item["enabled"] for item in entries)
            or body.get("restart_required") is True
        ):
            return "Agent did not confirm the requested subscription access state"
        return None

    def after_result(self, session, command, now):
        if command.attempts:
            self.adopt_command(session, command, now)
        if command.path == ENDPOINT:
            row = session.scalar(
                select(SubscriptionAccessModel).where(
                    SubscriptionAccessModel.command_id == command.id
                )
            )
            if row:
                row.applied_revision = (
                    command.body["revision"] if command.status == "succeeded" else None
                )
                row.last_error = command.result_error
                row.retry_at = (
                    None if command.status == "succeeded" else now + timedelta(seconds=60)
                )
                row.updated_at = now
        elif command.attempts and self.store._should_refresh_xray_snapshot_after(
            command.method, command.path, command.body
        ):
            for row in session.scalars(
                select(SubscriptionAccessModel).where(
                    SubscriptionAccessModel.server_id == command.server_id
                )
            ):
                if self.affects_credentials(row, command):
                    row.applied_revision, row.retry_at = None, None

    @staticmethod
    def affects_credentials(row, command):
        keys = {(item["tag"], item["client"]["email"]) for item in row.bindings}
        if command.path == "/api/child/node-cleanup":
            body = command.body if isinstance(command.body, dict) else {}
            return body.get("action") == "apply" and (
                bool(body.get("outbound_tags"))
                or any(tag in body.get("inbound_tags", []) for tag, _ in keys)
            )
        if command.path == "/api/child/batch-apply":
            return any(
                (item["tag"], item["client"]["email"]) in keys for item in command_clients(command)
            )
        if command.path in {"/api/child/routing", "/api/child/outbounds"}:
            return False
        if command.path == "/api/child/inbounds" and isinstance(command.body, dict):
            body = command.body
            if body.get("action") in {"add-client", "remove-client"}:
                client = body.get("client")
                return (
                    isinstance(client, dict)
                    and isinstance(client.get("email"), str)
                    and isinstance(body.get("tag"), str)
                    and (body["tag"], client["email"]) in keys
                )
            if body.get("action") in {"reorder", "add-sniffing-exclude"}:
                return False
            inbound = body.get("inbound")
            tag = body.get("tag") or (inbound.get("tag") if isinstance(inbound, dict) else None)
            return any(key[0] == tag for key in keys)
        return True

    def run_once(self, *, username=None, force=False, now=None):
        active_now = now or datetime.now(UTC)
        with self.store._coordinated_session() as session:
            if username is not None and session.get(ProductUserModel, username) is None:
                raise ProductUserNotFoundError(f"user not found: {username}")
            commands = self.reconcile(session, active_now, username=username, force=force)
            if username is None:
                self.store._user_management().finalize_ready(session, active_now)
                commands.extend(self.store._node_management().advance(session, active_now))
            session.commit()
            return [self.store._command_read(command) for command in commands]

    def set_active(self, username, active):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            user = session.get(ProductUserModel, username)
            if user is None:
                raise ProductUserNotFoundError(f"user not found: {username}")
            self.store._user_management().require_editable(user)
            self.store._user_management().check_active(user, active)
            if not active:
                view = self.store._user_management()._view(session, user)
                if view.blockers:
                    from open_node.services.inventory import ProductUserConflict

                    raise ProductUserConflict("; ".join(view.blockers))
                plan = (
                    session.get(SubscriptionPlanModel, user.current_plan_id)
                    if user.current_plan_id
                    else None
                )
                self.store._plan_management()._track_revocations(session, user, plan, now)
            user.is_active, user.updated_at = active, now
            if not active:
                self.store._user_management().revoke_login(session, username)
            commands = self.reconcile(session, now, username=username)
            session.commit()
            return self.store._product_user_read(user), [
                self.store._command_read(command) for command in commands
            ]

    def read(self, username):
        with self.store._session() as session:
            return self.read_in_session(session, username)

    def read_in_session(self, session, username):
        if session.get(ProductUserModel, username) is None:
            raise ProductUserNotFoundError(f"user not found: {username}")
        states = []
        for row in self.rows(session, username):
            body, reasons = self.desired(session, row, datetime.now(UTC))
            command = session.get(CommandModel, row.command_id) if row.command_id else None
            state = (
                "applied"
                if body["revision"] == row.applied_revision
                else "failed"
                if command and command.status in {"failed", "skipped"}
                else "pending"
            )
            states.append(
                {
                    "server_id": row.server_id,
                    "server_name": session.get(ServerModel, row.server_id).name,
                    "status": state,
                    "command_id": row.command_id,
                    "error": row.last_error,
                    "updated_at": row.updated_at,
                    "entries": [
                        {
                            "inbound_tag": item["tag"],
                            "email": item["client"]["email"],
                            "enabled": item["enabled"],
                            "reason": reason,
                        }
                        for item, reason in zip(body["entries"], reasons, strict=True)
                    ],
                }
            )
        return {
            "username": username,
            "managed": bool(states),
            "servers": states,
            "license_required": False,
        }


class SubscriptionAccessWorker:
    def __init__(self, store, connections, interval=10):
        self.store, self.connections, self.interval = store, connections, interval

    async def tick(self):
        await asyncio.to_thread(
            self.store.reset_due_subscription_traffic, SubscriptionDueTrafficResetRequest()
        )
        commands = await asyncio.to_thread(self.store._subscription_access().run_once)
        for command in commands:
            await self.connections.dispatch_command(self.store, command)

    async def run(self):
        backfilled = False
        while True:
            try:
                if not backfilled:
                    await asyncio.to_thread(self.store._subscription_access().backfill)
                    backfilled = True
                await self.tick()
            except Exception:
                log.exception("Subscription access reconciliation failed")
            await asyncio.sleep(self.interval)
