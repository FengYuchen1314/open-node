"""Coordinate catalog removal with credential withdrawal and native resource receipts."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.node_management import (
    NodeManagementRead,
    NodeManagementResult,
    NodeRemovalRead,
)
from open_node.domain.subscriptions import ManagedNodeCreate
from open_node.services.inventory import (
    AgentChangeSetModel,
    AgentChangeSetStepModel,
    ChangeSetServerLockModel,
    CommandModel,
    ManagedNodeConflict,
    ManagedNodeModel,
    ManagedNodeNotFoundError,
    ManagedNodeRemovalModel,
    ProductUserModel,
    ServerModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
)
from open_node.services.node_cleanup import ENDPOINT
from open_node.services.subscription_access import ENDPOINT as ACCESS
from open_node.services.subscription_access import TERMINAL, protocol, revision
from open_node.services.user_limits import prune_node_overrides
from open_node.services.user_management import UserManagement, fingerprint, matches


class NodeRemovalNotFoundError(ValueError):
    pass


class NodeManagement:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def node(session, identifier):
        node = session.get(ManagedNodeModel, str(identifier))
        if node is None:
            raise ManagedNodeNotFoundError(f"Node not found: {identifier}")
        return node

    @staticmethod
    def jobs(session, pending=False):
        query = select(ManagedNodeRemovalModel).order_by(ManagedNodeRemovalModel.requested_at)
        if pending:
            query = query.where(ManagedNodeRemovalModel.completed_at.is_(None))
        return list(session.scalars(query))

    def pending_for_server(self, session, server_id):
        return [
            job
            for job in self.jobs(session, pending=True)
            if any(step["server_id"] == server_id for step in job.servers)
        ]

    def require_editable(self, session, node):
        if node.removal_id or self.pending_for_server(session, node.server_id):
            raise ManagedNodeConflict("A node removal is pending on this server")

    def validate_node(self, session, node):
        with session.no_autoflush:
            self._validate_node(session, node)

    def _validate_node(self, session, node):
        self.require_editable(session, node)
        if session.scalar(
            select(ManagedNodeModel.id).where(
                ManagedNodeModel.server_id == node.server_id,
                ManagedNodeModel.name == node.name,
                ManagedNodeModel.id != node.id,
            )
        ):
            raise ManagedNodeConflict("A node with this name already exists on the server")
        for job in self.jobs(session):
            for step in job.servers:
                if step["server_id"] == node.server_id and (
                    node.inbound_tag
                    and node.inbound_tag in step["inbound_tags"]
                    or node.routed_outbound_tag
                    and node.routed_outbound_tag
                    in (step["outbound_tags"] + (step.get("impact") or {}).get("outbound_tags", []))
                ):
                    raise ManagedNodeConflict("This resource tag was retired; choose a new tag")
        linked = [value for value in (node.parent_id, node.target_node_id) if value]
        if linked and node.node_type != "routed":
            raise ManagedNodeConflict("Only routed nodes can have parent or target nodes")
        parent = session.get(ManagedNodeModel, node.parent_id) if node.parent_id else None
        if parent and (
            parent.server_id != node.server_id
            or not node.inbound_tag
            or parent.inbound_tag != node.inbound_tag
            or protocol(parent.protocol) != protocol(node.protocol)
        ):
            raise ManagedNodeConflict(
                "Parent and child must share the server, inbound and protocol"
            )
        seen, pending = set(), linked
        while pending:
            identifier = pending.pop()
            if identifier == node.id:
                raise ManagedNodeConflict("Node relationships cannot contain a cycle")
            if identifier in seen:
                continue
            seen.add(identifier)
            other = session.get(ManagedNodeModel, identifier)
            if other is None:
                raise ManagedNodeConflict(f"Linked node not found: {identifier}")
            self.require_editable(session, other)
            pending.extend(value for value in (other.parent_id, other.target_node_id) if value)

    @staticmethod
    def credentials(session, identifiers):
        return list(
            session.scalars(
                select(SubscriptionCredentialModel)
                .where(SubscriptionCredentialModel.node_id.in_(identifiers))
                .order_by(SubscriptionCredentialModel.id)
            )
        )

    def closure(self, session, node):
        nodes = list(session.scalars(select(ManagedNodeModel).order_by(ManagedNodeModel.id)))
        selected = {node.id}
        while True:
            before = set(selected)
            removed_inbounds = {
                (item.server_id, item.inbound_tag)
                for item in nodes
                if item.id in selected and item.node_type == "physical" and item.inbound_tag
            }
            remaining_inbounds = {
                (item.server_id, item.inbound_tag)
                for item in nodes
                if item.id not in selected and item.node_type == "physical"
            }
            for item in nodes:
                if (
                    item.parent_id in selected
                    or item.target_node_id in selected
                    or (
                        item.node_type == "routed"
                        and (item.server_id, item.inbound_tag)
                        in removed_inbounds - remaining_inbounds
                    )
                ):
                    selected.add(item.id)
            if selected == before:
                return nodes, [item for item in nodes if item.id in selected]

    def rows(self, session, identifiers):
        return [
            row
            for row in self.store._subscription_access().rows(session)
            if any(set(binding["node_ids"]).intersection(identifiers) for binding in row.bindings)
        ]

    def _view(self, session, node):
        nodes, selected = self.closure(session, node)
        ids = {item.id for item in selected}
        remaining = [item for item in nodes if item.id not in ids]
        credentials = self.credentials(session, ids)
        rows = self.rows(session, ids)
        server_ids = sorted(
            {item.server_id for item in selected} | {row.server_id for row in credentials + rows}
        )
        plans = [
            plan
            for plan in session.scalars(
                select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.id)
            )
            if ids.intersection(plan.node_ids)
        ]
        warnings, blockers, targets = [], [], []
        for item in selected:
            if item.removal_id:
                blockers.append("Node removal is already in progress")
            if not item.inbound_tag:
                warnings.append(
                    f"{item.name}: no managed inbound; external resources need manual cleanup"
                )
            if item.node_type == "routed" and not item.routed_outbound_tag:
                warnings.append(
                    f"{item.name}: no managed outbound; external routing needs manual cleanup"
                )
        for server_id in server_ids:
            server = session.get(ServerModel, server_id)
            if server is None:
                blockers.append("A node or credential refers to a missing server")
                continue
            if self.pending_for_server(session, server_id):
                blockers.append(f"{server.name}: another node removal is pending")
            if session.get(ChangeSetServerLockModel, server_id) or session.scalar(
                select(AgentChangeSetStepModel.id)
                .join(
                    AgentChangeSetModel,
                    AgentChangeSetStepModel.change_set_id == AgentChangeSetModel.id,
                )
                .where(
                    AgentChangeSetStepModel.server_id == server_id,
                    AgentChangeSetModel.status == "needs_review",
                )
                .limit(1)
            ):
                blockers.append(f"{server.name}: resolve the active change set first")
            wanted_in = {
                item.inbound_tag
                for item in selected
                if item.server_id == server_id and item.node_type == "physical" and item.inbound_tag
            }
            wanted_out = {
                item.routed_outbound_tag
                for item in selected
                if item.server_id == server_id and item.routed_outbound_tag
            }
            retained_in = wanted_in.intersection(
                item.inbound_tag for item in remaining if item.server_id == server_id
            )
            retained_out = wanted_out.intersection(
                item.routed_outbound_tag for item in remaining if item.server_id == server_id
            )
            targets.append(
                {
                    "server_id": server_id,
                    "server_name": server.name,
                    "inbound_tags": sorted(wanted_in - retained_in),
                    "outbound_tags": sorted(wanted_out - retained_out),
                    "retained_inbound_tags": sorted(retained_in),
                    "retained_outbound_tags": sorted(retained_out),
                }
            )
        public = self.store._managed_node_read(node)
        users = sorted({row.username for row in credentials + rows})
        stable = self.store._plan_management()._revision_record
        return NodeManagementRead(
            node=public,
            revision=revision(
                {
                    "nodes": [stable(self.store._managed_node_read(item)) for item in nodes],
                    "plans": [stable(self.store._subscription_plan_read(plan)) for plan in plans],
                    "credentials": [
                        (row.id, row.credential, row.email, row.server_id, row.inbound_tag)
                        for row in credentials
                    ],
                    "bindings": [(row.id, row.bindings) for row in rows],
                    "users": [
                        stable(self.store._product_user_read(session.get(ProductUserModel, name)))
                        for name in users
                    ],
                    "targets": targets,
                    "blockers": blockers,
                }
            ),
            nodes=[{"id": item.id, "name": item.name} for item in selected],
            plans=[{"id": plan.id, "name": plan.name} for plan in plans],
            credential_count=len(credentials),
            servers=targets,
            blockers=list(dict.fromkeys(blockers)),
            warnings=list(dict.fromkeys(warnings)),
            access=[
                self.store._subscription_access().read_in_session(session, name) for name in users
            ],
        )

    def read(self, identifier):
        with self.store._session() as session:
            return self._view(session, self.node(session, identifier))

    @staticmethod
    def check_import_update(session, node, entry):
        if session.scalar(
            select(SubscriptionCredentialModel.id)
            .where(SubscriptionCredentialModel.node_id == node.id)
            .limit(1)
        ) and any(
            getattr(node, field) != getattr(entry, field)
            for field in (
                "protocol",
                "node_type",
                "inbound_tag",
                "routed_outbound_tag",
                "routed_rule_marktag",
                "enabled",
                "client_template",
                "config",
            )
        ):
            raise ManagedNodeConflict("Use node settings to edit a node with stored credentials")

    def _track(self, session, identifiers, now):
        warnings = []
        for username in sorted({row.username for row in self.credentials(session, identifiers)}):
            user = session.get(ProductUserModel, username)
            plan = (
                session.get(SubscriptionPlanModel, user.current_plan_id)
                if user.current_plan_id
                else None
            )
            warnings.extend(
                self.store._plan_management()._track_revocations(
                    session, user, plan, now, node_ids=identifiers
                )
            )
        return warnings

    def update(self, identifier, payload):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            node = self.node(session, identifier)
            self.require_editable(session, node)
            before = self._view(session, node)
            if before.revision != payload.expected_revision:
                raise ManagedNodeConflict(
                    "Nodes, plans or credentials changed; reload before saving"
                )
            values = payload.model_dump(
                exclude={"expected_revision", "acknowledge_runtime_restart"}
            )
            validated = ManagedNodeCreate.model_validate(
                {
                    **before.node.model_dump(),
                    **values,
                }
            )
            if not validated.enabled:
                self._track(session, {node.id}, now)
            for field in values:
                value = getattr(validated, field)
                setattr(node, field, str(value) if field.endswith("_id") and value else value)
            node.updated_at = now
            self.validate_node(session, node)
            # Refresh only existing bindings. Public-template edits never enroll preview-only users.
            for credential in self.credentials(session, {node.id}):
                user = session.get(ProductUserModel, credential.username)
                plan = (
                    session.get(SubscriptionPlanModel, user.current_plan_id)
                    if user.current_plan_id
                    else None
                )
                client = self.store._provisioning_client_from_credential(
                    user, plan, node, session.get(ServerModel, node.server_id), credential
                )
                for row in self.rows(session, {node.id}):
                    if row.username != user.username or row.server_id != credential.server_id:
                        continue
                    bindings = deepcopy(row.bindings)
                    for binding in bindings:
                        if node.id not in binding["node_ids"]:
                            continue
                        if binding["client"] != client and set(binding["node_ids"]) - {node.id}:
                            raise ManagedNodeConflict(
                                "Shared credentials require identical client templates"
                            )
                        binding["client"] = client
                    row.bindings = bindings
            commands = self.store._subscription_access().reconcile(session, now)
            session.flush()
            result = NodeManagementResult(
                **self._view(session, node).model_dump(),
                commands=[self.store._command_read(command) for command in commands],
            )
            session.commit()
            return result

    @staticmethod
    def _job(session, identifier):
        job = session.get(ManagedNodeRemovalModel, str(identifier))
        if job is None:
            raise NodeRemovalNotFoundError("Node removal not found")
        return job

    def _job_read(self, job, commands=()):
        return NodeRemovalRead(
            id=job.id,
            node_id=job.node_id,
            name=job.name,
            node_ids=job.node_ids,
            requested_at=job.requested_at,
            completed_at=job.completed_at,
            status="completed"
            if job.completed_at
            else "failed"
            if any(step.get("error") for step in job.servers)
            else "pending",
            servers=job.servers,
            warnings=job.warnings,
            commands=[self.store._command_read(command) for command in commands],
        )

    def read_removal(self, identifier):
        with self.store._session() as session:
            return self._job_read(self._job(session, identifier))

    def remove(self, identifier, payload):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            node = self.node(session, identifier)
            if payload.confirm_name != node.name:
                raise ManagedNodeConflict("Confirm the exact node name")
            if node.removal_id:
                return self._job_read(self._job(session, node.removal_id))
            view = self._view(session, node)
            if view.revision != payload.expected_revision:
                raise ManagedNodeConflict(
                    "Nodes, plans or credentials changed; reload before removing"
                )
            if view.blockers:
                raise ManagedNodeConflict("; ".join(view.blockers))
            if view.warnings and not payload.acknowledge_unmanaged_resources:
                raise ManagedNodeConflict("Confirm responsibility for unmanaged resources")
            ids = {str(item.id) for item in view.nodes}
            warnings = self._track(session, ids, now)
            rows = self.rows(session, ids)
            identities = [
                fingerprint(row.server_id, binding["tag"], binding["client"])
                for row in rows
                for binding in row.bindings
                if set(binding["node_ids"]).intersection(ids) and not set(binding["node_ids"]) - ids
            ]
            identities.extend(
                fingerprint(row.server_id, row.inbound_tag, {**row.credential, "email": row.email})
                for row in self.credentials(session, ids)
                if not row.inbound_tag
            )
            remaining_credentials = list(
                session.scalars(
                    select(SubscriptionCredentialModel).where(
                        SubscriptionCredentialModel.node_id.not_in(ids)
                    )
                )
            )
            identities = [
                item
                for item in identities
                if not any(
                    other.server_id == item["server_id"]
                    and other.email == item["email"]
                    and matches(item, other.credential)
                    for other in remaining_credentials
                )
            ]
            job = ManagedNodeRemovalModel(
                id=str(uuid4()),
                node_id=node.id,
                name=node.name,
                node_ids=sorted(ids),
                requested_at=now,
                fingerprints=identities,
                warnings=list(dict.fromkeys(view.warnings + warnings)),
                servers=[step.model_dump(mode="json") for step in view.servers],
            )
            session.add(job)
            session.flush()
            for selected in session.scalars(
                select(ManagedNodeModel).where(ManagedNodeModel.id.in_(ids))
            ):
                selected.removal_id, selected.enabled, selected.updated_at = job.id, False, now
            for plan in session.scalars(select(SubscriptionPlanModel)):
                if not ids.intersection(plan.node_ids):
                    continue
                plan.node_ids = [value for value in plan.node_ids if value not in ids]
                for field in ("node_multipliers", "node_speed_limits", "node_device_limits"):
                    setattr(
                        plan,
                        field,
                        {
                            key: value
                            for key, value in (getattr(plan, field) or {}).items()
                            if key not in ids
                        },
                    )
                plan.updated_at = now
            for user in session.scalars(select(ProductUserModel)):
                if prune_node_overrides(user, ids):
                    user.updated_at = now
            commands = self.store._subscription_access().reconcile(session, now)
            commands.extend(self.advance(session, now, identifier=job.id))
            result = self._job_read(job, commands)
            session.commit()
            return result

    def retry(self, identifier):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            job = self._job(session, identifier)
            commands = []
            if not job.completed_at:
                access = self.store._subscription_access()
                for username in sorted(
                    {
                        row.username
                        for row in self.rows(session, set(job.node_ids))
                        if row.last_error
                    }
                ):
                    commands.extend(access.reconcile(session, now, username=username, force=True))
                commands.extend(access.reconcile(session, now))
                commands.extend(self.advance(session, now, identifier=job.id, retry=True))
            result = self._job_read(job, commands)
            session.commit()
            return result

    @staticmethod
    def mutates(command):
        if command.path == ENDPOINT:
            return isinstance(command.body, dict) and command.body.get("action") == "apply"
        if command.method not in {"GET", "HEAD", "OPTIONS"}:
            return command.path not in {"/api/child/xray/test-config", "/api/child/scan"}
        return command.path in {
            "/api/child/nginx/install",
            "/api/child/nginx/install-stream",
            "/api/child/nginx/remove",
            "/api/child/nginx/remove-stream",
        }

    def restores(self, command, job):
        if not self.mutates(command):
            return False
        if UserManagement.restores(command, job.fingerprints):
            return True
        if command.path == ACCESS:
            return False
        body = command.body
        if not isinstance(body, dict) or str(body.get("action", "")).startswith(
            ("remove", "delete", "reorder")
        ):
            return False
        tags = {
            tag
            for step in job.servers
            if step["server_id"] == command.server_id
            for tag in step["inbound_tags"]
            + step["outbound_tags"]
            + (step.get("impact") or {}).get("outbound_tags", [])
        }
        pending = [body]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if any(
                    isinstance(value.get(key), str) and value[key] in tags
                    for key in (
                        "tag",
                        "inbound_tag",
                        "outbound_tag",
                        "outboundTag",
                        "dialerProxy",
                    )
                ):
                    return True
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                try:
                    pending.append(json.loads(value))
                except (ValueError, RecursionError):
                    pass
        return False

    def can_lease(self, session, command, now):
        for job in self.jobs(session):
            if any(step.get("command_id") == command.id for step in job.servers):
                return True
            if command.attempts:
                continue
            if self.restores(command, job):
                self.store._subscription_access().skip(
                    session, command, now, "Not sent: node removal retired these resources"
                )
                return False
            if (
                not job.completed_at
                and command.path != ACCESS
                and self.mutates(command)
                and any(
                    step["server_id"] == command.server_id
                    and not (step.get("error") and step["phase"] == "preview")
                    for step in job.servers
                )
            ):
                return False
        return True

    def check_imported_credential(self, session, server_id, entry):
        for job in self.jobs(session):
            if any(
                item["server_id"] == server_id
                and (item["email"] == entry.email or matches(item, entry.credential))
                for item in job.fingerprints
            ):
                raise ManagedNodeConflict(
                    "Retired node credentials or traffic labels cannot be reimported"
                )

    def _ready(self, session, job, server_id, now):
        access = self.store._subscription_access()
        for command in session.scalars(
            select(CommandModel).where(
                CommandModel.server_id == server_id, CommandModel.status.not_in(TERMINAL)
            )
        ).all():
            if command.attempts and self.mutates(command):
                return False
            if not command.attempts and self.restores(command, job):
                access.skip(session, command, now, "Not sent: node removal retired these resources")
        return all(
            row.applied_revision == access.desired(session, row, now)[0]["revision"]
            for row in self.rows(session, set(job.node_ids))
            if row.server_id == server_id
        )

    def _withdrawal_error(self, session, job, server_id, now):
        access = self.store._subscription_access()
        for row in self.rows(session, set(job.node_ids)):
            if (
                row.server_id != server_id
                or row.applied_revision == access.desired(session, row, now)[0]["revision"]
            ):
                continue
            command = session.get(CommandModel, row.command_id) if row.command_id else None
            if command and command.status in {"failed", "skipped"}:
                return row.last_error or "Subscription withdrawal needs attention"
        return None

    def _queue(self, session, step, body, phase, now):
        command = self.store._create_command_model(
            session,
            session.get(ServerModel, step["server_id"]),
            AgentCommandCreate(method="POST", path=ENDPOINT, body=body, timeout_ms=60_000),
            now=now,
        )
        session.flush()
        step.update(command_id=command.id, phase=phase, error=None)
        return command

    def _preview_safe(self, session, job, step, impact):
        if not isinstance(impact, dict) or any(
            not isinstance(impact.get(key), list)
            or any(not isinstance(value, str) for value in impact[key])
            for key in ("inbound_tags", "outbound_tags", "suspended_tags")
        ):
            return "Agent returned an invalid resource impact"
        if (set(impact["inbound_tags"]) | set(impact["suspended_tags"])) - set(
            step["inbound_tags"]
        ):
            return "Cleanup preview includes an unselected inbound"
        for node in session.scalars(
            select(ManagedNodeModel).where(
                ManagedNodeModel.server_id == step["server_id"],
                ManagedNodeModel.id.not_in(job.node_ids),
            )
        ):
            if node.routed_outbound_tag in impact["outbound_tags"]:
                return "An outbound dependency is still used by another managed node"
        if impact.get("default_outbound_changed"):
            return "Cleanup would change the server default outbound; reorder it first"
        return None

    def advance(self, session, now, identifier=None, retry=False):
        commands = []
        for job in self.jobs(session, pending=True):
            if identifier and job.id != str(identifier):
                continue
            steps = deepcopy(job.servers)
            for step in steps:
                if step["phase"] in {"withdrawing", "completed"}:
                    step["error"] = self._withdrawal_error(session, job, step["server_id"], now)
                if step["phase"] == "completed":
                    continue
                previous = (
                    session.get(CommandModel, step["command_id"])
                    if step.get("command_id")
                    else None
                )
                if previous and previous.status not in TERMINAL:
                    commands.append(previous)
                    continue
                if step.get("error") and not retry:
                    continue
                targets = {key: step[key] for key in ("inbound_tags", "outbound_tags")}
                if step.get("error") and retry and step["phase"] == "preview":
                    commands.append(
                        self._queue(session, step, {"action": "preview", **targets}, "preview", now)
                    )
                    continue
                if previous and previous.status != "succeeded":
                    step["error"] = previous.result_error or f"Agent command {previous.status}"
                    if not retry:
                        continue
                    action = "status" if step["phase"] in {"apply", "inspect"} else "preview"
                    body = (
                        {"action": action, "operation_id": step["operation_id"]}
                        if action == "status"
                        else {"action": action, **targets}
                    )
                    commands.append(
                        self._queue(
                            session, step, body, "inspect" if action == "status" else "preview", now
                        )
                    )
                    continue
                if step["phase"] == "withdrawing":
                    if not self._ready(session, job, step["server_id"], now):
                        continue
                    if not step["inbound_tags"] and not step["outbound_tags"]:
                        step["phase"] = "completed"
                    else:
                        commands.append(
                            self._queue(
                                session, step, {"action": "preview", **targets}, "preview", now
                            )
                        )
                elif step["phase"] == "preview":
                    receipt = previous.result_body["node_cleanup"]
                    step["error"] = self._preview_safe(session, job, step, receipt["impact"])
                    step["impact"] = receipt["impact"]
                    if step["error"]:
                        continue
                    if not self._ready(session, job, step["server_id"], now):
                        step.update(phase="withdrawing", command_id=None)
                        continue
                    step["operation_id"] = str(uuid4())
                    step["expected_revision"] = receipt["revision"]
                    step["apply_body"] = {
                        "action": "apply",
                        **targets,
                        "operation_id": step["operation_id"],
                        "expected_revision": step["expected_revision"],
                        "acknowledge_runtime_restart": True,
                    }
                    commands.append(self._queue(session, step, step["apply_body"], "apply", now))
                elif step["phase"] == "apply":
                    if previous.result_body["node_cleanup"]["impact"] != step["impact"]:
                        step["error"] = "Cleanup receipt impact does not match the preview"
                    else:
                        step.update(phase="completed", error=None)
                elif step["phase"] == "inspect":
                    receipt = previous.result_body["node_cleanup"]
                    if receipt.get("exists") is False:
                        step.update(phase="withdrawing", command_id=None, error=None)
                    elif (
                        receipt["revision"] != step["expected_revision"]
                        or receipt["impact"] != step["impact"]
                    ):
                        step["error"] = "Cleanup status does not match the recorded operation"
                    elif receipt["applied"]:
                        step.update(phase="completed", error=None)
                    else:
                        commands.append(
                            self._queue(session, step, step["apply_body"], "apply", now)
                        )
            job.servers = steps
            session.flush()
            if all(step["phase"] == "completed" for step in steps) and all(
                self._ready(session, job, step["server_id"], now) for step in steps
            ):
                self._finish(session, job, now)
        return commands

    def _finish(self, session, job, now):
        ids = set(job.node_ids)
        access = self.store._subscription_access()
        for row in self.rows(session, ids):
            bindings = []
            for binding in deepcopy(row.bindings):
                binding["node_ids"] = [value for value in binding["node_ids"] if value not in ids]
                if binding["node_ids"]:
                    bindings.append(binding)
            if bindings:
                row.bindings = bindings
                row.applied_revision = access.desired(session, row, now)[0]["revision"]
                row.updated_at = now
            else:
                session.delete(row)
        for credential in self.credentials(session, ids):
            session.delete(credential)
        nodes = list(session.scalars(select(ManagedNodeModel).where(ManagedNodeModel.id.in_(ids))))
        for node in nodes:
            node.parent_id = node.target_node_id = None
        session.flush()
        for node in nodes:
            session.delete(node)
        job.completed_at = now
        session.flush()
