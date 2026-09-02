from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from open_node.domain.node_topologies import (
    NodeTopologiesResponse,
    NodeTopologyCandidate,
    NodeTopologyCreate,
    NodeTopologyRead,
    NodeTopologyUpdate,
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
                    server_id=UUID(server.id),
                    server_name=server.name,
                    server_kind=server.server_kind,
                    protocol=node.protocol,
                )
            )
        return result

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
    def _validate_components(session, payload) -> tuple[list[ManagedNodeModel], ManagedNodeModel]:
        identifiers = [str(node_id) for stage in payload.stages for node_id in stage.node_ids]
        rows = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(identifiers))
        ).all()
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(identifiers):
            missing = next(identifier for identifier in identifiers if identifier not in by_id)
            raise NodeTopologyConflict(f"Topology node not found: {missing}")
        private_ids = set(
            session.scalars(
                select(PrivateRoutedNodeModel.node_id).where(
                    PrivateRoutedNodeModel.node_id.in_(identifiers)
                )
            ).all()
        )
        ordered = []
        server_ids: set[str] = set()
        for identifier in identifiers:
            node = by_id[identifier]
            if (
                node.node_type != "physical"
                or not node.enabled
                or node.removal_id
                or not node.config
                or node.id in private_ids
            ):
                raise NodeTopologyConflict(
                    f"Topology node is not an active physical node: {node.name}"
                )
            if node.server_id in server_ids:
                raise NodeTopologyConflict("A topology cannot revisit or reuse the same server")
            if session.get(ServerModel, node.server_id) is None:
                raise NodeTopologyConflict(f"Topology node has no server: {node.name}")
            server_ids.add(node.server_id)
            ordered.append(node)
        return ordered, by_id[identifiers[-1]]

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
            _components, exit_node = self._validate_components(session, payload)
            identifier = str(uuid4())
            stages = self._stage_values(payload)
            layout = self._layout_values(payload)
            node = ManagedNodeModel(
                id=identifier,
                name=payload.name,
                server_id=exit_node.server_id,
                protocol=exit_node.protocol,
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
            self.store._node_management().validate_node(session, node)
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
            _components, exit_node = self._validate_components(session, payload)
            stages = self._stage_values(payload)
            layout = self._layout_values(payload)
            node.name = payload.name
            node.enabled = payload.enabled
            node.server_id = exit_node.server_id
            node.protocol = exit_node.protocol
            node.updated_at = now
            self.store._node_management().validate_node(session, node)
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
