from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from open_node.domain.changes import (
    AgentChangeSetCreate,
    AgentChangeSetStatus,
    AgentChangeSetStepCreate,
    AgentRoutedOutboundChangeSetCreate,
)
from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.private_routed_nodes import (
    PrivateRoutedCandidateRead,
    PrivateRoutedNodeAction,
    PrivateRoutedNodeCreate,
    PrivateRoutedNodeMutationResponse,
    PrivateRoutedNodeRead,
    PrivateRoutedNodesResponse,
    PrivateRoutedNodeStatus,
    PrivateRoutedPolicyRead,
    PrivateRoutedPolicyUpdate,
)
from open_node.services import subscription_clients
from open_node.services.inventory import (
    AgentChangeSetModel,
    ManagedNodeModel,
    PrivateRoutedActionModel,
    PrivateRoutedNodeModel,
    PrivateRoutedPolicyModel,
    ProductUserConflict,
    ProductUserModel,
    ProductUserNotFoundError,
    ServerModel,
    SubscriptionAccessModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
)
from open_node.services.subscription_access import protocol


class PrivateRoutedNodeConflict(ValueError):
    pass


class PrivateRoutedNodeNotFoundError(ValueError):
    pass


class PrivateRoutedNodes:
    POLICY_ID = "default"
    IN_PROGRESS = {"dispatched", "rollback_queued"}
    FAILED = {"failed", "rollback_failed", "rollback_incomplete", "needs_review", "accepted"}

    def __init__(self, store):
        self.store = store

    def _policy(self, session, now=None):
        policy = session.get(PrivateRoutedPolicyModel, self.POLICY_ID)
        if policy is None:
            policy = PrivateRoutedPolicyModel(
                id=self.POLICY_ID,
                enabled=False,
                max_nodes=2,
                daily_limit=5,
                updated_at=now or datetime.now(UTC),
            )
            session.add(policy)
            session.flush()
        return policy

    @staticmethod
    def _policy_read(policy):
        return PrivateRoutedPolicyRead(
            enabled=policy.enabled,
            max_nodes=policy.max_nodes,
            daily_limit=policy.daily_limit,
            updated_at=policy.updated_at,
        )

    @staticmethod
    def _day_start(now):
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _actions_today(self, session, username, now):
        return int(
            session.scalar(
                select(func.count(PrivateRoutedActionModel.id)).where(
                    PrivateRoutedActionModel.username == username,
                    PrivateRoutedActionModel.created_at >= self._day_start(now),
                )
            )
            or 0
        )

    def _check_daily_limit(self, session, username, policy, now):
        if self._actions_today(session, username, now) >= policy.daily_limit:
            raise PrivateRoutedNodeConflict("Daily private route action limit reached")

    @staticmethod
    def _record_action(session, username, node_id, action, now):
        session.add(
            PrivateRoutedActionModel(
                id=str(uuid4()),
                username=username,
                node_id=node_id,
                action=action,
                created_at=now,
            )
        )

    def _read(self, session, lifecycle):
        node = session.get(ManagedNodeModel, lifecycle.node_id)
        if node is None or node.parent_id is None or node.target_node_id is None:
            raise PrivateRoutedNodeNotFoundError("Private routed node is incomplete")
        parent = session.get(ManagedNodeModel, node.parent_id)
        target = session.get(ManagedNodeModel, node.target_node_id)
        return PrivateRoutedNodeRead(
            id=UUID(node.id),
            username=lifecycle.username,
            name=node.name,
            status=lifecycle.status,
            action=lifecycle.action,
            server_id=UUID(node.server_id),
            protocol=node.protocol,
            parent_id=UUID(node.parent_id),
            parent_name=parent.name if parent else "Removed parent",
            target_node_id=UUID(node.target_node_id),
            target_name=target.name if target else "Removed target",
            change_set_id=UUID(lifecycle.change_set_id) if lifecycle.change_set_id else None,
            last_error=lifecycle.last_error,
            created_at=lifecycle.created_at,
            updated_at=lifecycle.updated_at,
        )

    @staticmethod
    def _plan(session, user):
        return (
            session.get(SubscriptionPlanModel, user.current_plan_id)
            if user.current_plan_id
            else None
        )

    def _candidates(self, session, user):
        plan = self._plan(session, user)
        if plan is None:
            return []
        private_ids = set(session.scalars(select(PrivateRoutedNodeModel.node_id)).all())
        nodes = session.scalars(
            select(ManagedNodeModel)
            .where(ManagedNodeModel.id.in_(plan.node_ids))
            .order_by(ManagedNodeModel.name, ManagedNodeModel.id)
        ).all()
        result = []
        for node in nodes:
            if node.id in private_ids or not node.enabled or node.removal_id:
                continue
            physical = node.node_type == "physical"
            result.append(
                PrivateRoutedCandidateRead(
                    id=UUID(node.id),
                    name=node.name,
                    server_id=UUID(node.server_id),
                    protocol=node.protocol,
                    can_parent=physical and bool(node.inbound_tag and node.config),
                    can_target=physical and bool(node.config),
                )
            )
        return result

    def _response(self, session, username=None):
        now = datetime.now(UTC)
        policy = self._policy(session, now)
        query = select(PrivateRoutedNodeModel).order_by(
            PrivateRoutedNodeModel.created_at.desc(), PrivateRoutedNodeModel.node_id
        )
        user = None
        if username is not None:
            user = session.get(ProductUserModel, username)
            if user is None:
                raise ProductUserNotFoundError(f"user not found: {username}")
            query = query.where(PrivateRoutedNodeModel.username == username)
        rows = list(session.scalars(query))
        return PrivateRoutedNodesResponse(
            policy=self._policy_read(policy),
            nodes=[self._read(session, row) for row in rows],
            candidates=self._candidates(session, user) if user else [],
            used_nodes=len(rows),
            actions_today=self._actions_today(session, username, now) if username else 0,
        )

    def list(self, username=None):
        with self.store._coordinated_session() as session:
            result = self._response(session, username)
            session.commit()
            return result

    def update_policy(self, payload: PrivateRoutedPolicyUpdate):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            policy = self._policy(session, now)
            policy.enabled = payload.enabled
            policy.max_nodes = payload.max_nodes
            policy.daily_limit = payload.daily_limit
            policy.updated_at = now
            session.commit()
            return self._policy_read(policy)

    @staticmethod
    def _eligible_node(session, plan, identifier, *, parent):
        node = session.get(ManagedNodeModel, str(identifier))
        private = session.get(PrivateRoutedNodeModel, str(identifier))
        if (
            node is None
            or node.id not in plan.node_ids
            or private is not None
            or node.node_type != "physical"
            or not node.enabled
            or node.removal_id
            or not node.config
        ):
            raise PrivateRoutedNodeConflict("Selected node is not available for private routing")
        if parent and not node.inbound_tag:
            raise PrivateRoutedNodeConflict("Parent node requires an authenticated inbound tag")
        return node

    def _target_outbound(self, session, user, plan, target, outbound_tag):
        proxies, warnings = self.store._subscription_proxy_configs(
            session, user, plan, {target.id}
        )
        if len(proxies) != 1 or warnings:
            detail = warnings[0] if warnings else "Target node has no usable Xray outbound"
            raise PrivateRoutedNodeConflict(detail)
        try:
            outbound = subscription_clients.xray_outbound(proxies[0][1])
        except (KeyError, TypeError, ValueError) as exc:
            raise PrivateRoutedNodeConflict(
                "Target node is not compatible with Xray routing"
            ) from exc
        outbound["tag"] = outbound_tag
        return outbound

    def create(self, username, payload: PrivateRoutedNodeCreate):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            user = session.get(ProductUserModel, username)
            if user is None:
                raise ProductUserNotFoundError(f"user not found: {username}")
            policy = self._policy(session, now)
            if not policy.enabled:
                raise PrivateRoutedNodeConflict("Private routed nodes are disabled")
            self._check_daily_limit(session, username, policy, now)
            rows = list(
                session.scalars(
                    select(PrivateRoutedNodeModel).where(
                        PrivateRoutedNodeModel.username == username
                    )
                )
            )
            if len(rows) >= policy.max_nodes:
                raise PrivateRoutedNodeConflict("Private routed node limit reached")
            if any(
                session.get(ManagedNodeModel, row.node_id).name.lower() == payload.label.lower()
                for row in rows
                if session.get(ManagedNodeModel, row.node_id)
            ):
                raise PrivateRoutedNodeConflict("Private route label is already in use")
            plan = self.store._available_subscription_plan(session, user)
            parent = self._eligible_node(session, plan, payload.parent_id, parent=True)
            target = self._eligible_node(session, plan, payload.target_node_id, parent=False)
            if parent.id == target.id:
                raise PrivateRoutedNodeConflict("Parent and target nodes must be different")

            node_id = str(uuid4())
            compact = node_id.replace("-", "")[:16]
            owner_ref = sha256(username.encode()).hexdigest()[:10]
            outbound_tag = f"private:{owner_ref}:{compact}"
            marktag = outbound_tag
            outbound = self._target_outbound(session, user, plan, target, outbound_tag)
            private_config = deepcopy(parent.config or {})
            private_config["name"] = payload.label
            node = ManagedNodeModel(
                id=node_id,
                name=payload.label,
                server_id=parent.server_id,
                protocol=parent.protocol,
                node_type="routed",
                parent_id=parent.id,
                target_node_id=target.id,
                inbound_tag=parent.inbound_tag,
                routed_outbound_tag=outbound_tag,
                routed_rule_marktag=marktag,
                tag=payload.label,
                tags=["private"],
                enabled=False,
                client_template=deepcopy(parent.client_template or {}),
                config=private_config,
                created_at=now,
                updated_at=now,
            )
            session.add(node)
            session.flush()
            server = session.get(ServerModel, parent.server_id)
            credential = self.store._get_or_create_subscription_credential(
                session, user, node, server
            )
            client = self.store._provisioning_client_from_credential(
                user, plan, node, server, credential
            )
            change_payload = self.store.build_routed_outbound_change_set(
                AgentRoutedOutboundChangeSetCreate(
                    server_id=UUID(parent.server_id),
                    inbound_tag=parent.inbound_tag,
                    inbound_protocol=parent.protocol,
                    label=payload.label,
                    outbound=outbound,
                    parent_ref=parent.id.replace("-", "")[:16],
                    admin_username=username,
                    admin_email=credential.email,
                    outbound_tag=outbound_tag,
                    marktag=marktag,
                    node_name=payload.label,
                    client=client,
                    command_timeout_ms=payload.command_timeout_ms,
                    rollback_on_failure=True,
                )
            )
            change = self.store._create_change_set_model(session, change_payload, now)
            lifecycle = PrivateRoutedNodeModel(
                node_id=node.id,
                username=username,
                status=PrivateRoutedNodeStatus.PROVISIONING.value,
                action=PrivateRoutedNodeAction.CREATE.value,
                change_set_id=change.id,
                outbound=outbound,
                client=client,
                created_at=now,
                updated_at=now,
            )
            session.add(lifecycle)
            self._record_action(session, username, node.id, "create", now)
            commands = self.store._change_sets().dispatch_model(session, change)
            session.commit()
            return PrivateRoutedNodeMutationResponse(
                node=self._read(session, lifecycle),
                commands=[self.store._command_read(command) for command in commands],
            )

    def _lifecycle(self, session, username, identifier):
        row = session.get(PrivateRoutedNodeModel, str(identifier))
        if row is None or row.username != username:
            raise PrivateRoutedNodeNotFoundError("Private routed node not found")
        return row

    def delete(self, username, identifier: UUID, command_timeout_ms=30_000):
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            lifecycle = self._lifecycle(session, username, identifier)
            policy = self._policy(session, now)
            self._check_daily_limit(session, username, policy, now)
            if lifecycle.status in {
                PrivateRoutedNodeStatus.PROVISIONING.value,
                PrivateRoutedNodeStatus.REMOVING.value,
            }:
                raise PrivateRoutedNodeConflict("Private route operation is still in progress")
            node = session.get(ManagedNodeModel, lifecycle.node_id)
            credential = session.scalar(
                select(SubscriptionCredentialModel).where(
                    SubscriptionCredentialModel.username == username,
                    SubscriptionCredentialModel.node_id == lifecycle.node_id,
                )
            )
            if node is None or credential is None:
                raise PrivateRoutedNodeConflict("Private route runtime identity is incomplete")
            previous = (
                session.get(AgentChangeSetModel, lifecycle.change_set_id)
                if lifecycle.change_set_id
                else None
            )
            if lifecycle.status == "failed" and previous and previous.status in {
                AgentChangeSetStatus.ROLLED_BACK.value,
                AgentChangeSetStatus.CANCELLED.value,
            }:
                deleted_id = UUID(node.id)
                self._record_action(session, username, node.id, "delete", now)
                session.delete(lifecycle)
                session.flush()
                session.delete(node)
                session.commit()
                return PrivateRoutedNodeMutationResponse(deleted_id=deleted_id)

            client = deepcopy(lifecycle.client or credential.credential or {})
            client["email"] = credential.email
            rule = {
                "type": "field",
                "marktag": node.routed_rule_marktag,
                "user": [credential.email],
                "inboundTag": [node.inbound_tag],
                "outboundTag": node.routed_outbound_tag,
            }
            steps = [
                AgentChangeSetStepCreate(
                    server_id=UUID(node.server_id),
                    label=f"Remove private client {credential.email}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/inbounds",
                        body={
                            "action": "remove-client",
                            "tag": node.inbound_tag,
                            "client": {"email": credential.email},
                        },
                        timeout_ms=command_timeout_ms,
                    ),
                    rollback=AgentCommandCreate(
                        method="POST",
                        path="/api/child/inbounds",
                        body={"action": "add-client", "tag": node.inbound_tag, "client": client},
                        timeout_ms=command_timeout_ms,
                    ),
                ),
                AgentChangeSetStepCreate(
                    server_id=UUID(node.server_id),
                    label=f"Remove private rule {node.routed_rule_marktag}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/routing",
                        body={
                            "action": "remove_rule",
                            "marktag": node.routed_rule_marktag,
                            "ignore_missing": True,
                        },
                        timeout_ms=command_timeout_ms,
                    ),
                    rollback=AgentCommandCreate(
                        method="POST",
                        path="/api/child/routing",
                        body={"action": "add_rule", "rule": rule, "allow_existing": True},
                        timeout_ms=command_timeout_ms,
                    ),
                ),
                AgentChangeSetStepCreate(
                    server_id=UUID(node.server_id),
                    label=f"Remove private outbound {node.routed_outbound_tag}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/outbounds",
                        body={
                            "action": "remove",
                            "tag": node.routed_outbound_tag,
                            "ignore_missing": True,
                        },
                        timeout_ms=command_timeout_ms,
                    ),
                    rollback=AgentCommandCreate(
                        method="POST",
                        path="/api/child/outbounds",
                        body={
                            "action": "add",
                            "outbound": lifecycle.outbound,
                            "allow_existing": True,
                        },
                        timeout_ms=command_timeout_ms,
                    ),
                ),
            ]
            change = self.store._create_change_set_model(
                session,
                AgentChangeSetCreate(
                    name=f"Delete private routed node {node.name}",
                    description=(
                        f"Remove subscriber-owned route {node.id} and its runtime identity."
                    ),
                    rollback_on_failure=True,
                    steps=steps,
                ),
                now,
            )
            node.enabled = False
            node.updated_at = now
            lifecycle.status = PrivateRoutedNodeStatus.REMOVING.value
            lifecycle.action = PrivateRoutedNodeAction.DELETE.value
            lifecycle.change_set_id = change.id
            lifecycle.last_error = None
            lifecycle.updated_at = now
            self._record_action(session, username, node.id, "delete", now)
            commands = self.store._change_sets().dispatch_model(session, change)
            session.commit()
            return PrivateRoutedNodeMutationResponse(
                node=self._read(session, lifecycle),
                commands=[self.store._command_read(command) for command in commands],
            )

    def _track_binding(self, session, lifecycle, node, now):
        credential = session.scalar(
            select(SubscriptionCredentialModel).where(
                SubscriptionCredentialModel.username == lifecycle.username,
                SubscriptionCredentialModel.node_id == node.id,
            )
        )
        if credential is None:
            lifecycle.status = "failed"
            lifecycle.last_error = "Private route credential is missing"
            node.enabled = False
            return
        change = session.get(AgentChangeSetModel, lifecycle.change_set_id)
        first = self.store._change_set_steps(session, change.id)[0]
        body = first.forward_body if isinstance(first.forward_body, dict) else {}
        client = deepcopy(body.get("client") or credential.credential or {})
        client["email"] = credential.email
        row = session.scalar(
            select(SubscriptionAccessModel).where(
                SubscriptionAccessModel.username == lifecycle.username,
                SubscriptionAccessModel.server_id == node.server_id,
            )
        )
        if row is None:
            row = SubscriptionAccessModel(
                id=str(uuid4()),
                username=lifecycle.username,
                server_id=node.server_id,
                bindings=[],
                updated_at=now,
            )
            session.add(row)
        key = (node.inbound_tag, credential.email)
        bindings = {
            (item["tag"], item["client"]["email"]): deepcopy(item) for item in row.bindings
        }
        bindings[key] = {
            "tag": node.inbound_tag,
            "protocol": protocol(node.protocol),
            "client": client,
            "node_ids": [node.id],
        }
        row.bindings = [bindings[item] for item in sorted(bindings)]
        row.applied_revision = None
        row.retry_at = None
        row.updated_at = now
        session.flush()
        self.store._subscription_access().reconcile(
            session, now, username=lifecycle.username
        )

    def _remove_binding(self, session, lifecycle, node, now):
        row = session.scalar(
            select(SubscriptionAccessModel).where(
                SubscriptionAccessModel.username == lifecycle.username,
                SubscriptionAccessModel.server_id == node.server_id,
            )
        )
        if row is not None:
            row.bindings = [
                item for item in row.bindings if node.id not in item.get("node_ids", [])
            ]
            row.applied_revision = None
            row.retry_at = None
            row.updated_at = now
            session.flush()
            self.store._subscription_access().reconcile(
                session, now, username=lifecycle.username
            )
        session.execute(
            delete(SubscriptionCredentialModel).where(
                SubscriptionCredentialModel.username == lifecycle.username,
                SubscriptionCredentialModel.node_id == node.id,
            )
        )

    @staticmethod
    def _cleanup_steps(node, credential, outbound, client, command_timeout_ms):
        client = deepcopy(client or credential.credential or {})
        client["email"] = credential.email
        rule = {
            "type": "field",
            "marktag": node.routed_rule_marktag,
            "user": [credential.email],
            "inboundTag": [node.inbound_tag],
            "outboundTag": node.routed_outbound_tag,
        }
        target = UUID(node.server_id)
        return [
            AgentChangeSetStepCreate(
                server_id=target,
                label=f"Remove private client {credential.email}",
                forward=AgentCommandCreate(
                    method="POST",
                    path="/api/child/inbounds",
                    body={
                        "action": "remove-client",
                        "tag": node.inbound_tag,
                        "client": {"email": credential.email},
                    },
                    timeout_ms=command_timeout_ms,
                ),
                rollback=AgentCommandCreate(
                    method="POST",
                    path="/api/child/inbounds",
                    body={"action": "add-client", "tag": node.inbound_tag, "client": client},
                    timeout_ms=command_timeout_ms,
                ),
            ),
            AgentChangeSetStepCreate(
                server_id=target,
                label=f"Remove private rule {node.routed_rule_marktag}",
                forward=AgentCommandCreate(
                    method="POST",
                    path="/api/child/routing",
                    body={
                        "action": "remove_rule",
                        "marktag": node.routed_rule_marktag,
                        "ignore_missing": True,
                    },
                    timeout_ms=command_timeout_ms,
                ),
                rollback=AgentCommandCreate(
                    method="POST",
                    path="/api/child/routing",
                    body={"action": "add_rule", "rule": rule, "allow_existing": True},
                    timeout_ms=command_timeout_ms,
                ),
            ),
            AgentChangeSetStepCreate(
                server_id=target,
                label=f"Remove private outbound {node.routed_outbound_tag}",
                forward=AgentCommandCreate(
                    method="POST",
                    path="/api/child/outbounds",
                    body={
                        "action": "remove",
                        "tag": node.routed_outbound_tag,
                        "ignore_missing": True,
                    },
                    timeout_ms=command_timeout_ms,
                ),
                rollback=AgentCommandCreate(
                    method="POST",
                    path="/api/child/outbounds",
                    body={"action": "add", "outbound": outbound, "allow_existing": True},
                    timeout_ms=command_timeout_ms,
                ),
            ),
        ]

    def prepare_user_removal(self, session, username, now, command_timeout_ms=30_000):
        rows = list(
            session.scalars(
                select(PrivateRoutedNodeModel).where(
                    PrivateRoutedNodeModel.username == username
                )
            )
        )
        if not rows:
            return []
        if any(row.status in {"provisioning", "removing"} for row in rows):
            raise ProductUserConflict(
                "Wait for private route operations before removing this user"
            )
        steps = []
        nodes = []
        for row in rows:
            node = session.get(ManagedNodeModel, row.node_id)
            credential = session.scalar(
                select(SubscriptionCredentialModel).where(
                    SubscriptionCredentialModel.username == username,
                    SubscriptionCredentialModel.node_id == row.node_id,
                )
            )
            if node is None or credential is None:
                raise ProductUserConflict(
                    "Private route runtime identity is incomplete"
                )
            steps.extend(
                self._cleanup_steps(
                    node,
                    credential,
                    row.outbound,
                    row.client,
                    command_timeout_ms,
                )
            )
            nodes.append((row, node))
        change = self.store._create_change_set_model(
            session,
            AgentChangeSetCreate(
                name=f"Remove private routes for {username}",
                description="Remove subscriber-owned routes before deleting the account.",
                rollback_on_failure=True,
                steps=steps,
            ),
            now,
        )
        for row, node in nodes:
            row.status = PrivateRoutedNodeStatus.REMOVING.value
            row.action = PrivateRoutedNodeAction.DELETE.value
            row.change_set_id = change.id
            row.last_error = None
            row.updated_at = now
            node.enabled = False
            node.updated_at = now
        return self.store._change_sets().dispatch_model(session, change)

    def after_change_set(self, session, change, now):
        lifecycles = list(session.scalars(
            select(PrivateRoutedNodeModel).where(
                PrivateRoutedNodeModel.change_set_id == change.id
            )
        ))
        for lifecycle in lifecycles:
            self._after_change_set_row(session, change, lifecycle, now)

    def _after_change_set_row(self, session, change, lifecycle, now):
        node = session.get(ManagedNodeModel, lifecycle.node_id)
        if node is None:
            return
        if change.status in self.IN_PROGRESS:
            return
        if lifecycle.action == PrivateRoutedNodeAction.CREATE.value:
            if change.status == AgentChangeSetStatus.SUCCEEDED.value:
                lifecycle.status = PrivateRoutedNodeStatus.ACTIVE.value
                lifecycle.last_error = None
                lifecycle.updated_at = now
                node.enabled = True
                node.updated_at = now
                self._track_binding(session, lifecycle, node, now)
            elif change.status in self.FAILED or change.status in {
                AgentChangeSetStatus.ROLLED_BACK.value,
                AgentChangeSetStatus.CANCELLED.value,
            }:
                lifecycle.status = PrivateRoutedNodeStatus.FAILED.value
                lifecycle.last_error = change.rollback_reason or "Private route creation failed"
                lifecycle.updated_at = now
                node.enabled = False
                node.updated_at = now
        elif lifecycle.action == PrivateRoutedNodeAction.DELETE.value:
            if change.status == AgentChangeSetStatus.SUCCEEDED.value:
                self._remove_binding(session, lifecycle, node, now)
                session.delete(lifecycle)
                session.flush()
                session.delete(node)
                session.flush()
            elif change.status == AgentChangeSetStatus.ROLLED_BACK.value:
                lifecycle.status = PrivateRoutedNodeStatus.ACTIVE.value
                lifecycle.last_error = None
                lifecycle.updated_at = now
                node.enabled = True
                node.updated_at = now
            elif change.status in self.FAILED:
                lifecycle.status = PrivateRoutedNodeStatus.FAILED.value
                lifecycle.last_error = change.rollback_reason or "Private route deletion failed"
                lifecycle.updated_at = now
                node.enabled = False
                node.updated_at = now
