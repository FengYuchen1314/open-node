"""Persistence and inventory binding for managed shared TCP 443 ingress."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from open_node.domain.shared_ingress import (
    SharedIngressConfiguration,
    SharedIngressState,
)
from open_node.services.inventory import (
    Base,
    InventoryStore,
    ManagedNodeModel,
    ServerModel,
    ServerNotFoundError,
)


class SharedIngressConflict(ValueError):
    """Raised when an optimistic update targets an old declaration."""


class SharedIngressBindingError(ValueError):
    """Raised when a route does not describe a managed node on this server."""


class SharedIngressModel(Base):
    __tablename__ = "shared_ingress_configurations"

    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SharedIngressStore:
    def __init__(self, inventory: InventoryStore) -> None:
        self.inventory = inventory

    def create_schema(self) -> None:
        SharedIngressModel.metadata.create_all(self.inventory._engine)

    @staticmethod
    def _require_server(session: Session, server_id: UUID) -> ServerModel:
        server = session.get(ServerModel, str(server_id))
        if server is None:
            raise ServerNotFoundError(f"server not found: {server_id}")
        return server

    @staticmethod
    def _read(model: SharedIngressModel | None, server_id: UUID) -> SharedIngressState:
        if model is None:
            return SharedIngressState(server_id=server_id)
        configuration = (
            SharedIngressConfiguration.model_validate(model.configuration)
            if model.configuration is not None
            else None
        )
        return SharedIngressState(
            server_id=server_id,
            configuration=configuration,
            revision=model.revision,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _check_revision(
        model: SharedIngressModel | None,
        expected_revision: int | None,
    ) -> None:
        actual = model.revision if model is not None else 0
        if expected_revision is not None and expected_revision != actual:
            raise SharedIngressConflict(
                f"shared ingress revision changed: expected {expected_revision}, current {actual}"
            )

    @staticmethod
    def _validate_node_bindings(
        session: Session,
        server_id: UUID,
        configuration: SharedIngressConfiguration,
    ) -> None:
        for route in configuration.routes:
            node = session.get(ManagedNodeModel, str(route.node_id))
            if node is None or node.removal_id is not None:
                raise SharedIngressBindingError(f"managed node not found: {route.node_id}")
            if node.server_id != str(server_id):
                raise SharedIngressBindingError(
                    f"managed node belongs to a different server: {route.node_id}"
                )
            if not node.enabled:
                raise SharedIngressBindingError(f"managed node is disabled: {route.node_id}")
            if node.node_type != "physical":
                raise SharedIngressBindingError(
                    f"only physical managed nodes may own ingress routes: {route.node_id}"
                )
            if node.protocol_profile != route.profile.value:
                raise SharedIngressBindingError(
                    f"managed node profile does not match route: {route.node_id}"
                )
            if not isinstance(node.config, dict) or node.config.get("port") != 443:
                raise SharedIngressBindingError(
                    f"managed node is not configured for the shared public 443 entry: "
                    f"{route.node_id}"
                )
            node_sni = (node.camouflage_sni or "").lower().rstrip(".")
            if node_sni != route.sni:
                raise SharedIngressBindingError(
                    f"managed node camouflage SNI does not match route: {route.node_id}"
                )
            if route.upstream_address != "127.0.0.1" or route.upstream_port != node.runtime_port:
                raise SharedIngressBindingError(
                    f"managed node runtime endpoint does not match route: {route.node_id}"
                )

    def get(self, server_id: UUID) -> SharedIngressState:
        with self.inventory._session() as session:
            self._require_server(session, server_id)
            return self._read(session.get(SharedIngressModel, str(server_id)), server_id)

    def save(
        self,
        server_id: UUID,
        configuration: SharedIngressConfiguration,
        *,
        expected_revision: int | None = None,
    ) -> SharedIngressState:
        now = datetime.now(tz=UTC)
        with self.inventory._coordinated_session() as session:
            self._require_server(session, server_id)
            current = session.get(SharedIngressModel, str(server_id))
            self._check_revision(current, expected_revision)
            self._validate_node_bindings(session, server_id, configuration)
            if current is None:
                current = SharedIngressModel(
                    server_id=str(server_id),
                    configuration=None,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(current)
            current.configuration = configuration.model_dump(mode="json")
            current.revision += 1
            current.updated_at = now
            session.commit()
            session.refresh(current)
            return self._read(current, server_id)

    def disable(
        self,
        server_id: UUID,
        *,
        expected_revision: int | None = None,
    ) -> SharedIngressState:
        now = datetime.now(tz=UTC)
        with self.inventory._coordinated_session() as session:
            self._require_server(session, server_id)
            current = session.get(SharedIngressModel, str(server_id))
            self._check_revision(current, expected_revision)
            if current is None:
                current = SharedIngressModel(
                    server_id=str(server_id),
                    configuration=None,
                    revision=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(current)
            current.configuration = None
            current.revision += 1
            current.updated_at = now
            session.commit()
            session.refresh(current)
            return self._read(current, server_id)
