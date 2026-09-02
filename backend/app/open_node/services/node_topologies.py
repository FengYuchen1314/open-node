from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from open_node.domain.node_topologies import (
    NodeTopologiesResponse,
    NodeTopologyCandidate,
    NodeTopologyCreate,
    NodeTopologyRead,
    NodeTopologyUpdate,
)
from open_node.services.external_subscriptions import (
    ExternalNodeModel,
    ExternalSourceModel,
    ExternalSubscriptionError,
)
from open_node.services.inventory import (
    ManagedNodeModel,
    NodeTopologyModel,
    PrivateRoutedNodeModel,
    ProductUserModel,
    ServerModel,
    SubscriptionAccessModel,
    SubscriptionCredentialModel,
    SubscriptionPlanModel,
    SubscriptionProfileModel,
    SubscriptionTrafficLedgerModel,
    TemporarySubscriptionModel,
)


class NodeTopologyNotFoundError(ValueError):
    pass


class NodeTopologyConflict(ValueError):
    pass


@dataclass(frozen=True)
class _TopologyComponent:
    id: str
    name: str
    protocol: str
    kind: str
    managed: ManagedNodeModel | None = None
    external: ExternalNodeModel | None = None
    source: ExternalSourceModel | None = None
    proxy: dict[str, Any] | None = None


class NodeTopologies:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _fingerprint(name: str, enabled: bool, stages, layout) -> str:
        canonical = json.dumps(
            {
                "name": name,
                "enabled": enabled,
                "stages": stages,
                "layout": layout,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _stage_values(payload) -> list[dict]:
        return [
            {
                "node_ids": [str(node_id) for node_id in stage.node_ids],
                "load_balance_strategy": stage.load_balance_strategy,
            }
            for stage in payload.stages
        ]

    @staticmethod
    def _layout_values(payload) -> dict[str, dict[str, float]]:
        return {node_id: point.model_dump() for node_id, point in sorted(payload.layout.items())}

    @staticmethod
    def _topology(session, identifier) -> tuple[NodeTopologyModel, ManagedNodeModel]:
        topology = session.get(NodeTopologyModel, str(identifier))
        node = session.get(ManagedNodeModel, str(identifier))
        if topology is None or node is None or node.node_type != "orchestrated":
            raise NodeTopologyNotFoundError(f"Node topology not found: {identifier}")
        return topology, node

    @staticmethod
    def _read(topology: NodeTopologyModel, node: ManagedNodeModel) -> NodeTopologyRead:
        return NodeTopologyRead(
            id=UUID(topology.id),
            name=node.name,
            enabled=node.enabled,
            stages=deepcopy(topology.stages or []),
            layout=deepcopy(topology.layout or {}),
            revision=topology.revision,
            created_at=topology.created_at,
            updated_at=topology.updated_at,
        )

    @staticmethod
    def _candidates(session) -> list[NodeTopologyCandidate]:
        private_ids = set(session.scalars(select(PrivateRoutedNodeModel.node_id)).all())
        nodes = session.scalars(
            select(ManagedNodeModel)
            .where(
                ManagedNodeModel.node_type == "physical",
                ManagedNodeModel.enabled.is_(True),
                ManagedNodeModel.removal_id.is_(None),
            )
            .order_by(ManagedNodeModel.name, ManagedNodeModel.id)
        ).all()
        result = []
        for node in nodes:
            if node.id in private_ids or not node.config:
                continue
            server = session.get(ServerModel, node.server_id)
            if server is None:
                continue
            result.append(
                NodeTopologyCandidate(
                    id=UUID(node.id),
                    name=node.name,
                    kind="managed",
                    server_id=UUID(server.id),
                    server_name=server.name,
                    server_kind=server.server_kind,
                    protocol=node.protocol,
                )
            )
        managed_ids = set(session.scalars(select(ManagedNodeModel.id)).all())
        sources = {
            source.id: source
            for source in session.scalars(
                select(ExternalSourceModel).order_by(
                    ExternalSourceModel.owner_username,
                    ExternalSourceModel.created_at,
                    ExternalSourceModel.id,
                )
            )
        }
        external_nodes = session.scalars(
            select(ExternalNodeModel).order_by(
                ExternalNodeModel.created_at, ExternalNodeModel.id
            )
        ).all()
        for external in external_nodes:
            source = sources.get(external.source_id)
            if source is None or external.id in managed_ids:
                continue
            owner = session.get(ProductUserModel, source.owner_username)
            reason = (
                "External source is disabled"
                if not source.enabled
                else "Node is disabled"
                if not external.enabled
                else "Node is missing from the latest confirmed refresh"
                if not external.present
                else external.reason
                or ("Node configuration is unavailable" if external.secret is None else None)
            )
            if owner is None or owner.removal_id or reason is not None:
                continue
            result.append(
                NodeTopologyCandidate(
                    id=UUID(external.id),
                    name=external.display_name or external.upstream_name,
                    kind="external",
                    protocol=external.protocol,
                    source_id=UUID(source.id),
                    source_name=source.name,
                    owner_username=source.owner_username,
                )
            )
        return sorted(
            result,
            key=lambda candidate: (candidate.name, candidate.kind, str(candidate.id)),
        )

    def list(self) -> NodeTopologiesResponse:
        with self.store._session() as session:
            rows = session.scalars(
                select(NodeTopologyModel).order_by(
                    NodeTopologyModel.created_at, NodeTopologyModel.id
                )
            ).all()
            topologies = []
            for topology in rows:
                node = session.get(ManagedNodeModel, topology.id)
                if node is None:
                    raise NodeTopologyConflict(f"Node topology {topology.id} has no virtual node")
                topologies.append(self._read(topology, node))
            return NodeTopologiesResponse(
                topologies=topologies,
                candidates=self._candidates(session),
            )

    @staticmethod
    def _endpoint_key(proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        if not isinstance(server, str) or not server.strip():
            return None
        return server.strip().rstrip(".").casefold()

    def _validate_components(
        self, session, payload
    ) -> tuple[list[_TopologyComponent], ManagedNodeModel | None, str, str | None]:
        identifiers = [str(node_id) for stage in payload.stages for node_id in stage.node_ids]
        managed_rows = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(identifiers))
        ).all()
        external_rows = session.scalars(
            select(ExternalNodeModel).where(ExternalNodeModel.id.in_(identifiers))
        ).all()
        managed_by_id = {row.id: row for row in managed_rows}
        external_by_id = {row.id: row for row in external_rows}
        private_ids = set(
            session.scalars(
                select(PrivateRoutedNodeModel.node_id).where(
                    PrivateRoutedNodeModel.node_id.in_(identifiers)
                )
            ).all()
        )
        ordered: list[_TopologyComponent] = []
        managed_components: list[ManagedNodeModel] = []
        server_ids: set[str] = set()
        endpoint_keys: set[str] = set()
        owners: set[str] = set()
        external_service = self.store.external_subscriptions()
        for identifier in identifiers:
            managed = managed_by_id.get(identifier)
            external = external_by_id.get(identifier)
            if managed is not None and external is not None:
                raise NodeTopologyConflict(
                    f"Topology node identity is ambiguous: {identifier}"
                )
            if managed is not None:
                if (
                    managed.node_type != "physical"
                    or not managed.server_id
                    or not managed.enabled
                    or managed.removal_id
                    or not managed.config
                    or managed.id in private_ids
                ):
                    raise NodeTopologyConflict(
                        f"Topology node is not an active physical node: {managed.name}"
                    )
                if managed.server_id in server_ids:
                    raise NodeTopologyConflict(
                        "A topology cannot revisit or reuse the same server"
                    )
                server = session.get(ServerModel, managed.server_id)
                if server is None:
                    raise NodeTopologyConflict(f"Topology node has no server: {managed.name}")
                endpoint_key = self._endpoint_key(managed.config)
                if endpoint_key is not None and "{" in endpoint_key:
                    endpoint_key = self.store._server_subscription_host(server).casefold()
                if endpoint_key is not None and endpoint_key in endpoint_keys:
                    raise NodeTopologyConflict(
                        "A topology cannot revisit or reuse the same server"
                    )
                server_ids.add(managed.server_id)
                if endpoint_key is not None:
                    endpoint_keys.add(endpoint_key)
                managed_components.append(managed)
                ordered.append(
                    _TopologyComponent(
                        id=managed.id,
                        name=managed.name,
                        protocol=managed.protocol,
                        kind="managed",
                        managed=managed,
                    )
                )
                continue
            if external is None:
                raise NodeTopologyConflict(f"Topology node not found: {identifier}")
            source = session.get(ExternalSourceModel, external.source_id)
            owner = session.get(ProductUserModel, source.owner_username) if source else None
            if (
                source is None
                or owner is None
                or owner.removal_id
                or not source.enabled
                or not external.enabled
                or not external.present
                or external.reason
                or external.secret is None
            ):
                raise NodeTopologyConflict("Topology external node is unavailable")
            try:
                proxy = external_service.topology_proxy(
                    session,
                    external.id,
                    source.id,
                    source.owner_username,
                )
            except ExternalSubscriptionError:
                raise NodeTopologyConflict(
                    "Topology external node configuration is unavailable"
                ) from None
            endpoint_key = self._endpoint_key(proxy)
            if endpoint_key is None:
                raise NodeTopologyConflict(
                    "Topology external node configuration is unavailable"
                )
            if endpoint_key in endpoint_keys:
                raise NodeTopologyConflict("A topology cannot revisit or reuse the same server")
            endpoint_keys.add(endpoint_key)
            owners.add(source.owner_username)
            ordered.append(
                _TopologyComponent(
                    id=external.id,
                    name=external.display_name or external.upstream_name,
                    protocol=external.protocol,
                    kind="external",
                    external=external,
                    source=source,
                    proxy=proxy,
                )
            )
        if len(owners) > 1:
            raise NodeTopologyConflict(
                "Topology external nodes must belong to one subscriber"
            )
        return (
            ordered,
            managed_components[-1] if managed_components else None,
            ordered[-1].protocol,
            next(iter(owners), None),
        )

    @staticmethod
    def _assert_assignment_owner(
        session, identifier: str, owner_username: str | None
    ) -> None:
        if owner_username is None:
            return
        plan_ids = {
            plan.id
            for plan in session.scalars(select(SubscriptionPlanModel)).all()
            if identifier in (plan.node_ids or [])
        }
        if not plan_ids:
            return
        conflicting = session.scalar(
            select(ProductUserModel.username).where(
                ProductUserModel.current_plan_id.in_(plan_ids),
                ProductUserModel.username != owner_username,
            )
        )
        if conflicting:
            raise NodeTopologyConflict(
                "Topology external nodes belong to another assigned subscriber"
            )

    @staticmethod
    def _assert_unique_name(session, name: str, identifier: str | None = None) -> None:
        duplicate = session.scalar(
            select(ManagedNodeModel.id).where(
                ManagedNodeModel.node_type == "orchestrated",
                ManagedNodeModel.name == name,
                ManagedNodeModel.id != (identifier or ""),
            )
        )
        if duplicate:
            raise NodeTopologyConflict("A node topology with this name already exists")

    def create(self, payload: NodeTopologyCreate) -> NodeTopologyRead:
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            self._assert_unique_name(session, payload.name)
            _components, anchor_node, exit_protocol, _owner = self._validate_components(
                session, payload
            )
            identifier = str(uuid4())
            stages = self._stage_values(payload)
            layout = self._layout_values(payload)
            node = ManagedNodeModel(
                id=identifier,
                name=payload.name,
                server_id=anchor_node.server_id if anchor_node else None,
                protocol=exit_protocol,
                protocol_profile=None,
                node_type="orchestrated",
                inbound_tag=None,
                routed_outbound_tag=None,
                routed_rule_marktag=None,
                tag="orchestrated",
                tags=["orchestrated"],
                enabled=payload.enabled,
                client_template={},
                config={},
                created_at=now,
                updated_at=now,
            )
            topology = NodeTopologyModel(
                id=identifier,
                stages=stages,
                layout=layout,
                revision=self._fingerprint(payload.name, payload.enabled, stages, layout),
                created_at=now,
                updated_at=now,
            )
            session.add_all([node, topology])
            session.commit()
            return self._read(topology, node)

    def update(self, identifier: UUID, payload: NodeTopologyUpdate) -> NodeTopologyRead:
        now = datetime.now(UTC)
        with self.store._coordinated_session() as session:
            topology, node = self._topology(session, identifier)
            if topology.revision != payload.expected_revision:
                raise NodeTopologyConflict("Node topology changed; reload before saving")
            self._assert_unique_name(session, payload.name, node.id)
            _components, anchor_node, exit_protocol, owner = self._validate_components(
                session, payload
            )
            self._assert_assignment_owner(session, node.id, owner)
            stages = self._stage_values(payload)
            layout = self._layout_values(payload)
            node.name = payload.name
            node.enabled = payload.enabled
            node.server_id = anchor_node.server_id if anchor_node else None
            node.protocol = exit_protocol
            node.updated_at = now
            topology.stages = stages
            topology.layout = layout
            topology.revision = self._fingerprint(payload.name, payload.enabled, stages, layout)
            topology.updated_at = now
            session.commit()
            return self._read(topology, node)

    def delete(self, identifier: UUID, expected_revision: str, confirm_name: str) -> UUID:
        with self.store._coordinated_session() as session:
            topology, node = self._topology(session, identifier)
            if topology.revision != expected_revision:
                raise NodeTopologyConflict("Node topology changed; reload before deleting")
            if confirm_name != node.name:
                raise NodeTopologyConflict("Topology name confirmation does not match")
            assigned = next(
                (
                    plan.id
                    for plan in session.scalars(select(SubscriptionPlanModel)).all()
                    if node.id
                    in (
                        set(plan.node_ids or [])
                        | set((plan.node_multipliers or {}).keys())
                        | set((plan.node_name_overrides or {}).keys())
                        | set((plan.node_speed_limits or {}).keys())
                        | set((plan.node_device_limits or {}).keys())
                    )
                ),
                None,
            )
            if assigned:
                raise NodeTopologyConflict(
                    "Remove this topology from subscription plans before deleting it"
                )
            profile = next(
                (
                    row.id
                    for row in session.scalars(select(SubscriptionProfileModel)).all()
                    if node.id in (row.node_ids or [])
                ),
                None,
            )
            if profile:
                raise NodeTopologyConflict(
                    "Remove this topology from subscription profiles before deleting it"
                )
            temporary = next(
                (
                    row.id
                    for row in session.scalars(select(TemporarySubscriptionModel)).all()
                    if node.id in (row.node_ids or [])
                ),
                None,
            )
            if temporary:
                raise NodeTopologyConflict(
                    "Remove this topology from temporary subscriptions before deleting it"
                )
            linked_node = session.scalar(
                select(ManagedNodeModel.id).where(
                    (ManagedNodeModel.parent_id == node.id)
                    | (ManagedNodeModel.target_node_id == node.id)
                )
            )
            if linked_node:
                raise NodeTopologyConflict(
                    "Remove managed-node links to this topology before deleting it"
                )
            user_override = next(
                (
                    user.username
                    for user in session.scalars(select(ProductUserModel)).all()
                    if node.id in (user.node_speed_limit_overrides or {})
                    or node.id in (user.node_device_limit_overrides or {})
                ),
                None,
            )
            if user_override:
                raise NodeTopologyConflict(
                    "Remove user limit overrides for this topology before deleting it"
                )
            credential = session.scalar(
                select(SubscriptionCredentialModel.id).where(
                    SubscriptionCredentialModel.node_id == node.id
                )
            )
            if credential:
                raise NodeTopologyConflict(
                    "A topology with stored subscription credentials cannot be deleted"
                )
            access = next(
                (
                    row.id
                    for row in session.scalars(select(SubscriptionAccessModel)).all()
                    if any(
                        node.id in (binding.get("node_ids") or [])
                        for binding in (row.bindings or [])
                        if isinstance(binding, dict)
                    )
                ),
                None,
            )
            if access:
                raise NodeTopologyConflict(
                    "Reconcile subscription access before deleting this topology"
                )
            attributed = session.scalar(
                select(SubscriptionTrafficLedgerModel.id).where(
                    SubscriptionTrafficLedgerModel.attributed_node_id == node.id
                )
            )
            if attributed:
                raise NodeTopologyConflict("A topology with attributed traffic cannot be deleted")
            session.delete(topology)
            session.delete(node)
            session.commit()
            return identifier
