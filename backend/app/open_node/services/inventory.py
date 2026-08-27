from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from open_node.domain.inventory import (
    AgentCapabilities,
    AgentCommandCreate,
    AgentCommandRead,
    AgentCommandResultRequest,
    AgentCommandStatus,
    AgentCommandStreamDataRequest,
    AgentCommandStreamFrameRead,
    AgentHeartbeatRequest,
    AgentRead,
    AgentRegistrationRequest,
    AgentTelemetryRead,
    AgentTelemetryReport,
    ConnectionMode,
    ProbeLatencySample,
    ProbeSysMetrics,
    ServerCreate,
    ServerRead,
    ServerRecord,
    ServerStatus,
    SystemTraffic,
    TrafficSource,
    TrafficStatsMode,
    XrayMode,
    XrayStats,
)
from open_node.domain.probe import (
    ProbeBucket,
    ProbeMetricPoint,
    ProbePayload,
    ProbePingSeries,
    ProbeSeriesResponse,
    ProbeServer,
    ProbeSystemSeries,
)
from open_node.domain.subscriptions import (
    ManagedNodeCreate,
    ManagedNodeRead,
    ProductUserCreate,
    ProductUserRead,
    SubscriptionPlanAssignRequest,
    SubscriptionPlanCreate,
    SubscriptionPlanRead,
    SubscriptionProvisionBatch,
)


class InvalidAgentTokenError(ValueError):
    """Raised when an agent presents an unknown bootstrap token."""


class DuplicateServerNameError(ValueError):
    """Raised when a server name would no longer be a stable inventory key."""


class ServerNotFoundError(ValueError):
    """Raised when an inventory lookup targets an unknown server."""


class CommandNotFoundError(ValueError):
    """Raised when an agent command cannot be found for the requesting server."""


class ProbeNotFoundError(ValueError):
    """Raised when a public probe lookup targets data outside the public list."""


class DuplicateProductUserError(ValueError):
    """Raised when a product username is already taken."""


class ProductUserNotFoundError(ValueError):
    """Raised when a product user lookup targets an unknown username."""


class DuplicateSubscriptionPlanNameError(ValueError):
    """Raised when a subscription plan name is already taken."""


class SubscriptionPlanNotFoundError(ValueError):
    """Raised when a subscription plan lookup targets an unknown plan."""


class ManagedNodeNotFoundError(ValueError):
    """Raised when a managed node lookup targets an unknown node."""


_PROBE_SERIES_RANGES = {
    "1h": (12, 300),
    "6h": (36, 600),
    "24h": (48, 1800),
}


class Base(DeclarativeBase):
    pass


class ServerModel(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    agent_token: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address_v6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_v6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_mode: Mapped[str] = mapped_column(String(24))
    listen_port: Mapped[int] = mapped_column(Integer)
    pull_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pull_address_v6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pull_port: Mapped[int] = mapped_column(Integer)
    ipv6_enabled: Mapped[bool] = mapped_column(Boolean)
    traffic_limit: Mapped[int] = mapped_column(Integer)
    traffic_stats_mode: Mapped[str] = mapped_column(String(24))
    traffic_source: Mapped[str] = mapped_column(String(24))
    xray_mode: Mapped[str] = mapped_column(String(24))
    current_upload_speed: Mapped[int] = mapped_column(Integer, default=0)
    current_download_speed: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    hostname: Mapped[str] = mapped_column(String(255))
    agent_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    connection_mode: Mapped[str] = mapped_column(String(24))
    listen_port: Mapped[int] = mapped_column(Integer)
    public_ipv4: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_ipv6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    xray_mode: Mapped[str] = mapped_column(String(24))
    capability_rpc: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_return_route_test: Mapped[bool] = mapped_column(Boolean, default=False)
    warp_installed: Mapped[bool] = mapped_column(Boolean, default=False)
    same_host_as_master: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelemetrySnapshotModel(Base):
    __tablename__ = "telemetry_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    online_users: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    user_speeds: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    conn_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    system_rx_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    system_tx_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    system_boot_time_unix: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sysmetrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latency: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class CommandModel(Base):
    __tablename__ = "agent_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    method: Mapped[str] = mapped_column(String(12))
    path: Mapped[str] = mapped_column(String(255))
    query: Mapped[str] = mapped_column(String(2048), default="")
    body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer)
    stream: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    result_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CommandStreamFrameModel(Base):
    __tablename__ = "agent_command_stream_frames"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    command_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_commands.id", ondelete="CASCADE"),
        index=True,
    )
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    data: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProductUserModel(Base):
    __tablename__ = "product_users"

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(24))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    current_plan_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("subscription_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_day: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ManagedNodeModel(Base):
    __tablename__ = "managed_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    protocol: Mapped[str] = mapped_column(String(40))
    node_type: Mapped[str] = mapped_column(String(24), default="physical", index=True)
    inbound_tag: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    routed_outbound_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routed_rule_marktag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    client_template: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionPlanModel(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    cycle_days: Mapped[int] = mapped_column(Integer)
    is_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_day: Mapped[int] = mapped_column(Integer, default=0)
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    node_multipliers: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    node_speed_limits: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    node_device_limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    speed_limit_mbps: Mapped[float] = mapped_column(Float, default=0)
    device_limit: Mapped[int] = mapped_column(Integer, default=0)
    traffic_mode: Mapped[str] = mapped_column(String(24), default="oneway")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InventoryStore:
    def __init__(self, database_url: str) -> None:
        self._engine = create_inventory_engine(database_url)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self._engine)

    def list_servers(self) -> list[ServerRead]:
        with self._session() as session:
            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            return [self._public_server(server) for server in servers]

    def create_server(self, payload: ServerCreate) -> ServerRecord:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            existing = session.scalar(select(ServerModel).where(ServerModel.name == payload.name))
            if existing:
                raise DuplicateServerNameError(f"server name already exists: {payload.name}")

            server = ServerModel(
                id=str(uuid4()),
                name=payload.name,
                status=ServerStatus.PENDING.value,
                ip_address=payload.ip_address,
                ip_address_v6=payload.ip_address_v6,
                domain=payload.domain,
                domain_v6=payload.domain_v6,
                connection_mode=payload.connection_mode.value,
                listen_port=payload.listen_port,
                pull_address=payload.pull_address,
                pull_address_v6=payload.pull_address_v6,
                pull_port=payload.pull_port,
                ipv6_enabled=payload.ipv6_enabled,
                traffic_limit=payload.traffic_limit,
                traffic_stats_mode=payload.traffic_stats_mode.value,
                traffic_source=payload.traffic_source.value,
                xray_mode=payload.xray_mode.value,
                current_upload_speed=0,
                current_download_speed=0,
                created_at=now,
                updated_at=now,
                agent_token=token_urlsafe(32),
            )
            session.add(server)
            session.commit()
            session.refresh(server)
            return self._server_record(server)

    def public_server(self, server: ServerRecord) -> ServerRead:
        return ServerRead(**server.model_dump(exclude={"agent_token"}))

    def list_agents(self) -> list[AgentRead]:
        with self._session() as session:
            agents = session.scalars(select(AgentModel).order_by(AgentModel.registered_at)).all()
            return [self._agent_read(agent) for agent in agents]

    def register_agent(self, payload: AgentRegistrationRequest) -> tuple[AgentRead, ServerRead]:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.ip_address = payload.public_ipv4 or server.ip_address
            server.ip_address_v6 = payload.public_ipv6 or server.ip_address_v6
            server.connection_mode = payload.connection_mode.value
            server.listen_port = payload.listen_port
            server.xray_mode = payload.xray_mode.value
            server.updated_at = now

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if not agent:
                agent = AgentModel(
                    id=str(uuid4()),
                    server_id=server.id,
                    registered_at=now,
                    last_seen_at=now,
                    hostname=payload.hostname,
                    connection_mode=payload.connection_mode.value,
                    listen_port=payload.listen_port,
                    xray_mode=payload.xray_mode.value,
                )
                session.add(agent)

            agent.hostname = payload.hostname
            agent.agent_version = payload.agent_version
            agent.connection_mode = payload.connection_mode.value
            agent.listen_port = payload.listen_port
            agent.public_ipv4 = payload.public_ipv4
            agent.public_ipv6 = payload.public_ipv6
            agent.xray_mode = payload.xray_mode.value
            agent.capability_rpc = payload.capabilities.rpc
            agent.capability_stream = payload.capabilities.stream
            agent.capability_return_route_test = payload.capabilities.return_route_test
            agent.warp_installed = payload.warp_installed
            agent.same_host_as_master = payload.same_host_as_master
            agent.last_seen_at = now

            session.commit()
            session.refresh(agent)
            session.refresh(server)
            return self._agent_read(agent), self._public_server(server)

    def record_heartbeat(self, payload: AgentHeartbeatRequest) -> ServerRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.current_upload_speed = payload.upload_speed
            server.current_download_speed = payload.download_speed
            server.listen_port = payload.listen_port or server.listen_port
            server.ip_address = payload.public_ipv4 or server.ip_address
            server.ip_address_v6 = payload.public_ipv6 or server.ip_address_v6
            server.updated_at = now

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now
                agent.listen_port = server.listen_port
                agent.public_ipv4 = payload.public_ipv4 or agent.public_ipv4
                agent.public_ipv6 = payload.public_ipv6 or agent.public_ipv6

            session.commit()
            session.refresh(server)
            return self._public_server(server)

    def record_telemetry(
        self,
        payload: AgentTelemetryReport,
    ) -> tuple[ServerRead, AgentTelemetryRead]:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            reported_at = self._aware_datetime(payload.reported_at or now)
            previous = self._latest_telemetry_model(session, server.id)

            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now
            self._update_server_speed_from_system_traffic(server, previous, payload)

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now

            telemetry = TelemetrySnapshotModel(
                id=str(uuid4()),
                server_id=server.id,
                reported_at=reported_at,
                received_at=now,
                stats=payload.stats.model_dump(mode="json") if payload.stats else None,
                online_users=payload.online_users,
                user_speeds=payload.user_speeds,
                conn_counts=payload.conn_counts,
                system_rx_total=payload.system.rx_total if payload.system else None,
                system_tx_total=payload.system.tx_total if payload.system else None,
                system_boot_time_unix=payload.system.boot_time_unix if payload.system else None,
                sysmetrics=payload.sysmetrics.model_dump(mode="json")
                if payload.sysmetrics
                else None,
                latency=[sample.model_dump(mode="json") for sample in payload.latency],
            )
            session.add(telemetry)
            session.commit()
            session.refresh(server)
            session.refresh(telemetry)
            return self._public_server(server), self._telemetry_read(telemetry)

    def latest_telemetry(self, server_id: UUID) -> AgentTelemetryRead | None:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            telemetry = self._latest_telemetry_model(session, str(server_id))
            return self._telemetry_read(telemetry) if telemetry else None

    def public_probe_payload(self) -> ProbePayload:
        with self._session() as session:
            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            probe_servers = []
            for server in servers:
                latest = self._latest_telemetry_model(session, server.id)
                ping = self._probe_ping_series(session, server.id, bucket_count=12, bucket_sec=300)
                probe_servers.append(self._probe_server(server, latest, ping))
            return ProbePayload(servers=probe_servers)

    def public_probe_series(
        self,
        server_index: int,
        metric: str,
        range_name: str,
        target: str,
        all_targets: bool,
    ) -> ProbeSeriesResponse:
        if server_index < 0:
            raise ProbeNotFoundError("probe server not found")
        buckets, bucket_sec = _PROBE_SERIES_RANGES.get(range_name, _PROBE_SERIES_RANGES["1h"])

        with self._session() as session:
            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            if server_index >= len(servers):
                raise ProbeNotFoundError("probe server not found")
            server = servers[server_index]
            generated_at = int(datetime.now(tz=UTC).timestamp())

            if metric == "system":
                return ProbeSeriesResponse(
                    success=True,
                    series=self._probe_system_series(session, server.id, buckets, bucket_sec),
                    bucket_sec=bucket_sec,
                    generated_at=generated_at,
                )
            if metric != "ping":
                raise ProbeNotFoundError("probe metric not found")

            series_by_key = self._probe_ping_series(session, server.id, buckets, bucket_sec)
            if target and target not in {"__avg__", "__all__"}:
                series = series_by_key.get(target)
                if not series:
                    raise ProbeNotFoundError("probe target not found")
            else:
                series = self._average_probe_ping_series(series_by_key.values(), buckets)

            response = ProbeSeriesResponse(
                success=True,
                series=series,
                bucket_sec=bucket_sec,
                generated_at=generated_at,
            )
            if all_targets:
                response.all_series = sorted(
                    series_by_key.values(),
                    key=lambda item: (item.label, item.key or ""),
                )
            return response

    def list_product_users(self) -> list[ProductUserRead]:
        with self._session() as session:
            users = session.scalars(
                select(ProductUserModel).order_by(ProductUserModel.created_at)
            ).all()
            return [self._product_user_read(user) for user in users]

    def create_product_user(self, payload: ProductUserCreate) -> ProductUserRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            if session.get(ProductUserModel, payload.username):
                raise DuplicateProductUserError(f"username already exists: {payload.username}")
            user = ProductUserModel(
                username=payload.username,
                email=payload.email,
                display_name=payload.display_name or payload.username,
                role=payload.role.value,
                is_active=payload.is_active,
                current_plan_id=None,
                plan_started_at=None,
                plan_expires_at=None,
                is_reset=False,
                reset_day=0,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return self._product_user_read(user)

    def list_managed_nodes(self) -> list[ManagedNodeRead]:
        with self._session() as session:
            nodes = session.scalars(
                select(ManagedNodeModel).order_by(ManagedNodeModel.created_at)
            ).all()
            return [self._managed_node_read(node) for node in nodes]

    def create_managed_node(self, payload: ManagedNodeCreate) -> ManagedNodeRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            server = session.get(ServerModel, str(payload.server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {payload.server_id}")
            node = ManagedNodeModel(
                id=str(uuid4()),
                name=payload.name,
                server_id=server.id,
                protocol=payload.protocol.lower(),
                node_type=payload.node_type.value,
                inbound_tag=payload.inbound_tag,
                routed_outbound_tag=payload.routed_outbound_tag,
                routed_rule_marktag=payload.routed_rule_marktag,
                tag=payload.tag,
                tags=payload.tags,
                enabled=payload.enabled,
                client_template=payload.client_template,
                config=payload.config,
                created_at=now,
                updated_at=now,
            )
            session.add(node)
            session.commit()
            session.refresh(node)
            return self._managed_node_read(node)

    def list_subscription_plans(self) -> list[SubscriptionPlanRead]:
        with self._session() as session:
            plans = session.scalars(
                select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.created_at)
            ).all()
            return [self._subscription_plan_read(plan) for plan in plans]

    def create_subscription_plan(self, payload: SubscriptionPlanCreate) -> SubscriptionPlanRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            existing = session.scalar(
                select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == payload.name)
            )
            if existing:
                raise DuplicateSubscriptionPlanNameError(
                    f"subscription plan name already exists: {payload.name}"
                )
            self._ensure_managed_nodes_exist(session, payload.node_ids)
            plan = SubscriptionPlanModel(
                id=str(uuid4()),
                name=payload.name,
                description=payload.description,
                traffic_limit_bytes=int(payload.traffic_limit_gb * 1024 * 1024 * 1024),
                cycle_days=payload.cycle_days,
                is_reset=payload.is_reset,
                reset_day=payload.reset_day,
                node_ids=[str(node_id) for node_id in payload.node_ids],
                node_multipliers=self._uuid_keyed_float_map(payload.node_multipliers),
                node_speed_limits=self._uuid_keyed_float_map(payload.node_speed_limits),
                node_device_limits=self._uuid_keyed_int_map(payload.node_device_limits),
                speed_limit_mbps=payload.speed_limit_mbps,
                device_limit=payload.device_limit,
                traffic_mode=payload.traffic_mode.value,
                created_at=now,
                updated_at=now,
            )
            session.add(plan)
            session.commit()
            session.refresh(plan)
            return self._subscription_plan_read(plan)

    def assign_subscription_plan(
        self,
        username: str,
        payload: SubscriptionPlanAssignRequest,
    ) -> tuple[ProductUserRead, SubscriptionPlanRead, list[SubscriptionProvisionBatch], list[str]]:
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        now = datetime.now(tz=UTC)
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            plan = session.get(SubscriptionPlanModel, str(payload.plan_id))
            if not plan:
                raise SubscriptionPlanNotFoundError(
                    f"subscription plan not found: {payload.plan_id}"
                )

            started_at = self._date_to_utc_start(payload.start_date) if payload.start_date else now
            expires_at = (
                self._date_to_utc_start(payload.expire_date)
                if payload.expire_date
                else started_at + timedelta(days=plan.cycle_days)
            )
            is_reset = payload.is_reset if payload.is_reset is not None else plan.is_reset
            reset_day = payload.reset_day if payload.reset_day is not None else plan.reset_day
            if is_reset and reset_day == 0:
                reset_day = min(now.day, 28)

            user.current_plan_id = plan.id
            user.plan_started_at = started_at
            user.plan_expires_at = expires_at
            user.is_reset = is_reset
            user.reset_day = reset_day
            user.updated_at = now

            batches, warnings = self._subscription_provision_batches(
                session,
                user,
                plan,
                no_restart=payload.no_restart,
            )
            session.commit()
            session.refresh(user)
            session.refresh(plan)
            return (
                self._product_user_read(user),
                self._subscription_plan_read(plan),
                batches,
                warnings,
            )

    def create_command(self, server_id: UUID, payload: AgentCommandCreate) -> AgentCommandRead:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")

            now = datetime.now(tz=UTC)
            command = CommandModel(
                id=str(uuid4()),
                server_id=server.id,
                request_id=f"{server.id}-{uuid4().hex}",
                method=payload.method,
                path=payload.path,
                query=payload.query,
                body=payload.body,
                timeout_ms=payload.timeout_ms,
                stream=payload.stream,
                status=AgentCommandStatus.PENDING.value,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            session.add(command)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def list_commands(self, server_id: UUID) -> list[AgentCommandRead]:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            commands = session.scalars(
                select(CommandModel)
                .where(CommandModel.server_id == str(server_id))
                .order_by(CommandModel.created_at.desc())
            ).all()
            return [self._command_read(command) for command in commands]

    def list_command_stream_frames(
        self,
        server_id: UUID,
        command_id: UUID,
    ) -> list[AgentCommandStreamFrameRead]:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")

            command = session.get(CommandModel, str(command_id))
            if not command or command.server_id != server.id:
                raise CommandNotFoundError(f"command not found: {command_id}")

            frames = session.scalars(
                select(CommandStreamFrameModel)
                .where(CommandStreamFrameModel.command_id == command.id)
                .order_by(CommandStreamFrameModel.sequence)
            ).all()
            return [self._stream_frame_read(frame, command) for frame in frames]

    def lease_commands(
        self,
        token: str,
        max_commands: int,
    ) -> tuple[ServerRead, list[AgentCommandRead]]:
        with self._session() as session:
            server = self._server_by_token(session, token)
            now = datetime.now(tz=UTC)
            candidates = session.scalars(
                select(CommandModel)
                .where(
                    CommandModel.server_id == server.id,
                    CommandModel.status.in_(
                        [AgentCommandStatus.PENDING.value, AgentCommandStatus.LEASED.value]
                    ),
                )
                .order_by(CommandModel.created_at)
            ).all()

            leased: list[CommandModel] = []
            for command in candidates:
                if len(leased) >= max_commands:
                    break
                if command.status == AgentCommandStatus.LEASED.value and not self._lease_expired(
                    command,
                    now,
                ):
                    continue
                command.status = AgentCommandStatus.LEASED.value
                command.attempts += 1
                command.leased_at = now
                command.updated_at = now
                leased.append(command)

            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now
            session.commit()
            for command in leased:
                session.refresh(command)
            session.refresh(server)
            return self._public_server(server), [self._command_read(command) for command in leased]

    def complete_command(
        self,
        command_id: UUID,
        payload: AgentCommandResultRequest,
    ) -> AgentCommandRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            command = session.get(CommandModel, str(command_id))
            if not command or command.server_id != server.id:
                raise CommandNotFoundError(f"command not found: {command_id}")

            self._apply_command_result(server, command, payload)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def complete_command_by_request_id(
        self,
        request_id: str,
        payload: AgentCommandResultRequest,
    ) -> AgentCommandRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            command = session.scalar(
                select(CommandModel).where(
                    CommandModel.request_id == request_id,
                    CommandModel.server_id == server.id,
                )
            )
            if not command:
                raise CommandNotFoundError(f"command not found: {request_id}")

            self._apply_command_result(server, command, payload)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def append_command_stream_frame(
        self,
        payload: AgentCommandStreamDataRequest,
    ) -> AgentCommandStreamFrameRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            command = session.scalar(
                select(CommandModel).where(
                    CommandModel.request_id == payload.request_id,
                    CommandModel.server_id == server.id,
                )
            )
            completed_statuses = {
                AgentCommandStatus.SUCCEEDED.value,
                AgentCommandStatus.FAILED.value,
            }
            if not command or not command.stream or command.status in completed_statuses:
                raise CommandNotFoundError(f"stream command not found: {payload.request_id}")

            last_sequence = (
                session.scalar(
                    select(CommandStreamFrameModel.sequence)
                    .where(CommandStreamFrameModel.command_id == command.id)
                    .order_by(CommandStreamFrameModel.sequence.desc())
                    .limit(1)
                )
                or 0
            )
            now = datetime.now(tz=UTC)
            frame = CommandStreamFrameModel(
                id=str(uuid4()),
                command_id=command.id,
                server_id=server.id,
                sequence=last_sequence + 1,
                data=payload.data,
                received_at=now,
            )
            session.add(frame)
            command.updated_at = now
            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now
            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now
            session.commit()
            session.refresh(frame)
            session.refresh(command)
            return self._stream_frame_read(frame, command)

    def lease_command_for_push(self, command_id: UUID) -> AgentCommandRead:
        with self._session() as session:
            command = session.get(CommandModel, str(command_id))
            if not command:
                raise CommandNotFoundError(f"command not found: {command_id}")
            if command.status in {
                AgentCommandStatus.SUCCEEDED.value,
                AgentCommandStatus.FAILED.value,
            }:
                return self._command_read(command)

            now = datetime.now(tz=UTC)
            command.status = AgentCommandStatus.LEASED.value
            command.attempts += 1
            command.leased_at = now
            command.updated_at = now
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def _probe_server(
        self,
        server: ServerModel,
        latest: TelemetrySnapshotModel | None,
        ping_by_key: dict[str, ProbePingSeries],
    ) -> ProbeServer:
        probe = ProbeServer(
            name=server.name,
            online=server.status == ServerStatus.CONNECTED.value,
            upload_speed=server.current_upload_speed,
            download_speed=server.current_download_speed,
            traffic_limit=server.traffic_limit,
        )

        if latest:
            if latest.system_tx_total is not None or latest.system_rx_total is not None:
                probe.cumulative_up = latest.system_tx_total or 0
                probe.cumulative_down = latest.system_rx_total or 0

            if latest.stats:
                up, down = self._traffic_totals_from_stats(latest.stats)
                if up or down:
                    probe.traffic_used_up = up
                    probe.traffic_used_down = down
                    probe.traffic_used_total = up + down
                    probe.traffic_used = up + down

            if latest.sysmetrics:
                metrics = ProbeSysMetrics.model_validate(latest.sysmetrics)
                probe.uptime = metrics.uptime or None
                probe.cpu_model = metrics.cpu_model or None
                probe.cpu_cores = metrics.cpu_cores or None
                probe.cpu_threads = metrics.cpu_threads or None
                probe.os = metrics.os or None
                probe.kernel = metrics.kernel or None
                probe.arch = metrics.arch or None
                if metrics.has_cpu:
                    probe.cpu_pct = metrics.cpu_pct
                    probe.loadavg = metrics.loadavg
                if metrics.has_mem:
                    probe.mem_used = metrics.mem_used
                    probe.mem_total = metrics.mem_total
                if metrics.has_disk:
                    probe.disk_used = metrics.disk_used
                    probe.disk_total = metrics.disk_total

        if ping_by_key:
            probe.ping = sorted(ping_by_key.values(), key=lambda item: (item.label, item.key or ""))
        return probe

    def _probe_ping_series(
        self,
        session: Session,
        server_id: str,
        bucket_count: int,
        bucket_sec: int,
    ) -> dict[str, ProbePingSeries]:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        last_bucket = now_ts - now_ts % bucket_sec
        first_bucket = last_bucket - (bucket_count - 1) * bucket_sec
        since = datetime.fromtimestamp(first_bucket, tz=UTC)
        snapshots = session.scalars(
            select(TelemetrySnapshotModel)
            .where(
                TelemetrySnapshotModel.server_id == server_id,
                TelemetrySnapshotModel.reported_at >= since,
            )
            .order_by(TelemetrySnapshotModel.reported_at)
        ).all()

        series: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            for raw_sample in snapshot.latency or []:
                key = str(raw_sample.get("key") or "").strip()
                if not key:
                    continue
                timestamp = self._probe_sample_timestamp(raw_sample, snapshot)
                bucket_start = timestamp - timestamp % bucket_sec
                bucket_index = int((bucket_start - first_bucket) / bucket_sec)
                if bucket_index < 0 or bucket_index >= bucket_count:
                    continue

                state = series.setdefault(
                    key,
                    {
                        "current_at": -1,
                        "current_ms": -1,
                        "success": 0,
                        "fail": 0,
                        "buckets": [
                            {"sum": 0, "success": 0, "fail": 0} for _ in range(bucket_count)
                        ],
                    },
                )
                bucket = state["buckets"][bucket_index]
                success = bool(raw_sample.get("success"))
                if success:
                    latency_ms = int(raw_sample.get("latency_ms") or 0)
                    bucket["sum"] += latency_ms
                    bucket["success"] += 1
                    state["success"] += 1
                    if timestamp >= state["current_at"]:
                        state["current_at"] = timestamp
                        state["current_ms"] = latency_ms
                else:
                    bucket["fail"] += 1
                    state["fail"] += 1
                    if timestamp >= state["current_at"]:
                        state["current_at"] = timestamp
                        state["current_ms"] = -1

        return {
            key: self._probe_ping_series_from_state(key, state)
            for key, state in series.items()
        }

    @staticmethod
    def _probe_ping_series_from_state(key: str, state: dict[str, Any]) -> ProbePingSeries:
        buckets = []
        for bucket in state["buckets"]:
            total = bucket["success"] + bucket["fail"]
            if total == 0:
                buckets.append(ProbeBucket(ms=-1, loss=-1))
                continue
            ms = int(bucket["sum"] / bucket["success"]) if bucket["success"] else -1
            buckets.append(ProbeBucket(ms=ms, loss=bucket["fail"] * 100 / total))

        total = state["success"] + state["fail"]
        loss_pct = state["fail"] * 100 / total if total else 0
        return ProbePingSeries(
            key=key,
            label=key,
            current_ms=state["current_ms"],
            loss_pct=loss_pct,
            buckets=buckets,
        )

    @staticmethod
    def _average_probe_ping_series(
        source_series: Iterable[ProbePingSeries],
        bucket_count: int,
    ) -> ProbePingSeries:
        series = list(source_series)
        if not series:
            return ProbePingSeries(
                key="__avg__",
                label="Average",
                current_ms=-1,
                loss_pct=0,
                buckets=[ProbeBucket(ms=-1, loss=-1) for _ in range(bucket_count)],
            )

        current_values = [item.current_ms for item in series if item.current_ms >= 0]
        current_ms = int(sum(current_values) / len(current_values)) if current_values else -1
        loss_pct = sum(item.loss_pct for item in series) / len(series)
        buckets = []
        for index in range(bucket_count):
            ms_values = [
                item.buckets[index].ms
                for item in series
                if index < len(item.buckets) and item.buckets[index].ms >= 0
            ]
            loss_values = [
                item.buckets[index].loss
                for item in series
                if index < len(item.buckets) and item.buckets[index].loss >= 0
            ]
            ms = int(sum(ms_values) / len(ms_values)) if ms_values else -1
            loss = sum(loss_values) / len(loss_values) if loss_values else -1
            buckets.append(ProbeBucket(ms=ms, loss=loss))

        return ProbePingSeries(
            key="__avg__",
            label="Average",
            current_ms=current_ms,
            loss_pct=loss_pct,
            buckets=buckets,
        )

    def _probe_system_series(
        self,
        session: Session,
        server_id: str,
        bucket_count: int,
        bucket_sec: int,
    ) -> ProbeSystemSeries:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        last_bucket = now_ts - now_ts % bucket_sec
        first_bucket = last_bucket - (bucket_count - 1) * bucket_sec
        since = datetime.fromtimestamp(first_bucket, tz=UTC)
        snapshots = session.scalars(
            select(TelemetrySnapshotModel)
            .where(
                TelemetrySnapshotModel.server_id == server_id,
                TelemetrySnapshotModel.reported_at >= since,
            )
            .order_by(TelemetrySnapshotModel.reported_at)
        ).all()

        buckets: dict[int, dict[str, Any]] = {}
        previous: TelemetrySnapshotModel | None = None
        for snapshot in snapshots:
            timestamp = int(self._aware_datetime(snapshot.reported_at).timestamp())
            bucket_start = timestamp - timestamp % bucket_sec
            bucket_index = int((bucket_start - first_bucket) / bucket_sec)
            if bucket_index < 0 or bucket_index >= bucket_count:
                continue

            bucket = buckets.setdefault(
                bucket_index,
                {
                    "t": bucket_start,
                    "cpu_sum": 0.0,
                    "cpu_count": 0,
                    "mem_sum": 0,
                    "mem_count": 0,
                    "mem_total": 0,
                    "up_sum": 0,
                    "down_sum": 0,
                    "net_count": 0,
                    "cumulative_up": 0,
                    "cumulative_down": 0,
                },
            )
            if snapshot.sysmetrics:
                metrics = ProbeSysMetrics.model_validate(snapshot.sysmetrics)
                if metrics.has_cpu:
                    bucket["cpu_sum"] += metrics.cpu_pct
                    bucket["cpu_count"] += 1
                if metrics.has_mem:
                    bucket["mem_sum"] += metrics.mem_used
                    bucket["mem_count"] += 1
                    bucket["mem_total"] = metrics.mem_total

            if snapshot.system_tx_total is not None or snapshot.system_rx_total is not None:
                bucket["cumulative_up"] = snapshot.system_tx_total or 0
                bucket["cumulative_down"] = snapshot.system_rx_total or 0
                speed = self._system_speed_between(previous, snapshot)
                if speed:
                    upload_speed, download_speed = speed
                    bucket["up_sum"] += upload_speed
                    bucket["down_sum"] += download_speed
                    bucket["net_count"] += 1
            previous = snapshot

        output = ProbeSystemSeries()
        for index in range(bucket_count):
            bucket = buckets.get(index)
            if not bucket:
                continue
            timestamp = bucket["t"]
            if bucket["cpu_count"]:
                output.cpu_pct.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["cpu_sum"] / bucket["cpu_count"])
                )
            if bucket["mem_count"]:
                output.mem_used.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["mem_sum"] / bucket["mem_count"])
                )
                output.mem_total.append(ProbeMetricPoint(t=timestamp, value=bucket["mem_total"]))
            if bucket["net_count"]:
                output.upload_speed.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["up_sum"] / bucket["net_count"])
                )
                output.download_speed.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["down_sum"] / bucket["net_count"])
                )
            if bucket["cumulative_up"] or bucket["cumulative_down"]:
                output.cumulative_up.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["cumulative_up"])
                )
                output.cumulative_down.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["cumulative_down"])
                )
        return output

    @staticmethod
    def _system_speed_between(
        previous: TelemetrySnapshotModel | None,
        current: TelemetrySnapshotModel,
    ) -> tuple[int, int] | None:
        if not previous:
            return None
        if (
            previous.system_rx_total is None
            or previous.system_tx_total is None
            or current.system_rx_total is None
            or current.system_tx_total is None
            or previous.system_boot_time_unix != current.system_boot_time_unix
            or current.system_rx_total < previous.system_rx_total
            or current.system_tx_total < previous.system_tx_total
        ):
            return None

        previous_at = InventoryStore._aware_datetime(previous.reported_at)
        current_at = InventoryStore._aware_datetime(current.reported_at)
        elapsed = (current_at - previous_at).total_seconds()
        if elapsed <= 0:
            return None
        upload_speed = int((current.system_tx_total - previous.system_tx_total) / elapsed)
        download_speed = int((current.system_rx_total - previous.system_rx_total) / elapsed)
        return upload_speed, download_speed

    @staticmethod
    def _probe_sample_timestamp(
        sample: dict[str, Any],
        snapshot: TelemetrySnapshotModel,
    ) -> int:
        raw_at = sample.get("at")
        if isinstance(raw_at, (int, float)) and raw_at > 0:
            return int(raw_at)
        return int(InventoryStore._aware_datetime(snapshot.reported_at).timestamp())

    @staticmethod
    def _traffic_totals_from_stats(stats: dict[str, Any]) -> tuple[int, int]:
        source = stats.get("inbound") or stats.get("user") or {}
        if not isinstance(source, dict):
            return 0, 0
        uplink = 0
        downlink = 0
        for item in source.values():
            if not isinstance(item, dict):
                continue
            uplink += int(item.get("uplink") or 0)
            downlink += int(item.get("downlink") or 0)
        return uplink, downlink

    @staticmethod
    def _product_user_read(user: ProductUserModel) -> ProductUserRead:
        return ProductUserRead(
            username=user.username,
            email=user.email,
            display_name=user.display_name or user.username,
            role=user.role,
            is_active=user.is_active,
            current_plan_id=UUID(user.current_plan_id) if user.current_plan_id else None,
            plan_started_at=user.plan_started_at,
            plan_expires_at=user.plan_expires_at,
            is_reset=user.is_reset,
            reset_day=user.reset_day,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    @staticmethod
    def _managed_node_read(node: ManagedNodeModel) -> ManagedNodeRead:
        return ManagedNodeRead(
            id=UUID(node.id),
            name=node.name,
            server_id=UUID(node.server_id),
            protocol=node.protocol,
            node_type=node.node_type,
            inbound_tag=node.inbound_tag,
            routed_outbound_tag=node.routed_outbound_tag,
            routed_rule_marktag=node.routed_rule_marktag,
            tag=node.tag,
            tags=node.tags or [],
            enabled=node.enabled,
            client_template=node.client_template or {},
            config=node.config or {},
            created_at=node.created_at,
            updated_at=node.updated_at,
        )

    @staticmethod
    def _subscription_plan_read(plan: SubscriptionPlanModel) -> SubscriptionPlanRead:
        return SubscriptionPlanRead(
            id=UUID(plan.id),
            name=plan.name,
            description=plan.description,
            traffic_limit_gb=plan.traffic_limit_bytes / (1024 * 1024 * 1024),
            traffic_limit_bytes=plan.traffic_limit_bytes,
            cycle_days=plan.cycle_days,
            is_reset=plan.is_reset,
            reset_day=plan.reset_day,
            node_ids=[UUID(node_id) for node_id in (plan.node_ids or [])],
            node_multipliers={
                UUID(node_id): multiplier
                for node_id, multiplier in (plan.node_multipliers or {}).items()
            },
            node_speed_limits={
                UUID(node_id): limit for node_id, limit in (plan.node_speed_limits or {}).items()
            },
            node_device_limits={
                UUID(node_id): limit for node_id, limit in (plan.node_device_limits or {}).items()
            },
            speed_limit_mbps=plan.speed_limit_mbps,
            device_limit=plan.device_limit,
            traffic_mode=plan.traffic_mode,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def _uuid_keyed_float_map(values: dict[UUID, float]) -> dict[str, float]:
        return {str(key): value for key, value in values.items()}

    @staticmethod
    def _uuid_keyed_int_map(values: dict[UUID, int]) -> dict[str, int]:
        return {str(key): value for key, value in values.items()}

    @staticmethod
    def _date_to_utc_start(value: date) -> datetime:
        return datetime(value.year, value.month, value.day, tzinfo=UTC)

    @staticmethod
    def _ensure_managed_nodes_exist(session: Session, node_ids: list[UUID]) -> None:
        for node_id in node_ids:
            if not session.get(ManagedNodeModel, str(node_id)):
                raise ManagedNodeNotFoundError(f"managed node not found: {node_id}")

    def _subscription_provision_batches(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        no_restart: bool,
    ) -> tuple[list[SubscriptionProvisionBatch], list[str]]:
        warnings: list[str] = []
        if not plan.node_ids:
            return [], warnings

        nodes = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(plan.node_ids))
        ).all()
        nodes_by_id = {node.id: node for node in nodes}
        batches: dict[str, dict[str, Any]] = {}
        server_names: dict[str, str] = {}
        seen_inbound: set[tuple[str, str, str]] = set()
        seen_route: set[tuple[str, str, str, str]] = set()

        for node_id in plan.node_ids:
            node = nodes_by_id.get(node_id)
            if not node:
                warnings.append(f"node {node_id} no longer exists")
                continue
            if not node.enabled:
                continue
            server = session.get(ServerModel, node.server_id)
            if not server:
                warnings.append(f"node {node.name} points to a missing server")
                continue

            body = batches.setdefault(
                server.id,
                {"inbound_clients": [], "routing_user_additions": [], "no_restart": no_restart},
            )
            server_names[server.id] = server.name
            email = self._default_client_email(user, node)

            if node.inbound_tag:
                if node.client_template:
                    context = self._template_context(user, plan, node, server, email)
                    rendered = self._render_template(node.client_template, context)
                    if isinstance(rendered, dict) and rendered:
                        client = dict(rendered)
                        client_email = str(client.get("email") or email)
                        client["email"] = client_email
                        inbound_key = (server.id, node.inbound_tag, client_email)
                        if inbound_key not in seen_inbound:
                            body["inbound_clients"].append(
                                {"tag": node.inbound_tag, "client": client}
                            )
                            seen_inbound.add(inbound_key)
                            email = client_email
                    else:
                        warnings.append(f"node {node.name} client_template is not usable")
                else:
                    warnings.append(f"node {node.name} has no client_template")

            if node.routed_rule_marktag or node.routed_outbound_tag:
                route_key = (
                    server.id,
                    node.routed_rule_marktag or "",
                    node.routed_outbound_tag or "",
                    email,
                )
                if route_key not in seen_route:
                    item = {"user_email": email}
                    if node.routed_rule_marktag:
                        item["marktag"] = node.routed_rule_marktag
                    if node.routed_outbound_tag:
                        item["outbound_tag"] = node.routed_outbound_tag
                    body["routing_user_additions"].append(item)
                    seen_route.add(route_key)

        result = []
        for server_id, body in batches.items():
            if not body["inbound_clients"] and not body["routing_user_additions"]:
                continue
            result.append(
                SubscriptionProvisionBatch(
                    server_id=UUID(server_id),
                    server_name=server_names[server_id],
                    body=body,
                )
            )
        return result, warnings

    @staticmethod
    def _default_client_email(user: ProductUserModel, node: ManagedNodeModel) -> str:
        suffix = "".join(
            char if char.isalnum() or char in {"-", "_", "."} else "_"
            for char in node.name.strip()
        ).strip("_")
        return f"{user.username}__{suffix or node.protocol}"

    @staticmethod
    def _template_context(
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        node: ManagedNodeModel,
        server: ServerModel,
        email: str,
    ) -> dict[str, str]:
        return {
            "username": user.username,
            "user_email": user.email or user.username,
            "display_name": user.display_name or user.username,
            "client_email": email,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "node_id": node.id,
            "node_name": node.name,
            "protocol": node.protocol,
            "server_id": server.id,
            "server_name": server.name,
        }

    @classmethod
    def _render_template(cls, value: Any, context: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {key: cls._render_template(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._render_template(item, context) for item in value]
        if isinstance(value, str):
            rendered = value
            for key, replacement in context.items():
                rendered = rendered.replace("{" + key + "}", replacement)
            return rendered
        return value

    def _session(self) -> Session:
        return self._session_factory()

    @staticmethod
    def _server_record(server: ServerModel) -> ServerRecord:
        return ServerRecord(
            id=UUID(server.id),
            name=server.name,
            status=ServerStatus(server.status),
            ip_address=server.ip_address,
            ip_address_v6=server.ip_address_v6,
            domain=server.domain,
            domain_v6=server.domain_v6,
            connection_mode=ConnectionMode(server.connection_mode),
            listen_port=server.listen_port,
            pull_address=server.pull_address,
            pull_address_v6=server.pull_address_v6,
            pull_port=server.pull_port,
            ipv6_enabled=server.ipv6_enabled,
            traffic_limit=server.traffic_limit,
            traffic_stats_mode=TrafficStatsMode(server.traffic_stats_mode),
            traffic_source=TrafficSource(server.traffic_source),
            xray_mode=XrayMode(server.xray_mode),
            current_upload_speed=server.current_upload_speed,
            current_download_speed=server.current_download_speed,
            last_heartbeat=server.last_heartbeat,
            created_at=server.created_at,
            updated_at=server.updated_at,
            agent_token=server.agent_token,
        )

    @staticmethod
    def _public_server(server: ServerModel) -> ServerRead:
        payload = InventoryStore._server_record(server).model_dump(exclude={"agent_token"})
        return ServerRead(**payload)

    @staticmethod
    def _agent_read(agent: AgentModel) -> AgentRead:
        return AgentRead(
            id=UUID(agent.id),
            server_id=UUID(agent.server_id),
            hostname=agent.hostname,
            agent_version=agent.agent_version,
            connection_mode=ConnectionMode(agent.connection_mode),
            listen_port=agent.listen_port,
            public_ipv4=agent.public_ipv4,
            public_ipv6=agent.public_ipv6,
            xray_mode=XrayMode(agent.xray_mode),
            capabilities=AgentCapabilities(
                rpc=agent.capability_rpc,
                stream=agent.capability_stream,
                return_route_test=agent.capability_return_route_test,
            ),
            warp_installed=agent.warp_installed,
            same_host_as_master=agent.same_host_as_master,
            registered_at=agent.registered_at,
            last_seen_at=agent.last_seen_at,
        )

    @staticmethod
    def _telemetry_read(snapshot: TelemetrySnapshotModel) -> AgentTelemetryRead:
        system = None
        if (
            snapshot.system_rx_total is not None
            and snapshot.system_tx_total is not None
            and snapshot.system_boot_time_unix is not None
        ):
            system = SystemTraffic(
                rx_total=snapshot.system_rx_total,
                tx_total=snapshot.system_tx_total,
                boot_time_unix=snapshot.system_boot_time_unix,
            )

        return AgentTelemetryRead(
            id=UUID(snapshot.id),
            server_id=UUID(snapshot.server_id),
            reported_at=snapshot.reported_at,
            received_at=snapshot.received_at,
            stats=XrayStats.model_validate(snapshot.stats) if snapshot.stats else None,
            online_users=snapshot.online_users or {},
            user_speeds=snapshot.user_speeds or {},
            conn_counts=snapshot.conn_counts or {},
            system=system,
            sysmetrics=ProbeSysMetrics.model_validate(snapshot.sysmetrics)
            if snapshot.sysmetrics
            else None,
            latency=[
                ProbeLatencySample.model_validate(sample) for sample in (snapshot.latency or [])
            ],
        )

    @staticmethod
    def _command_read(command: CommandModel) -> AgentCommandRead:
        return AgentCommandRead(
            id=UUID(command.id),
            server_id=UUID(command.server_id),
            request_id=command.request_id,
            method=command.method,
            path=command.path,
            query=command.query,
            body=command.body,
            timeout_ms=command.timeout_ms,
            stream=command.stream,
            status=AgentCommandStatus(command.status),
            attempts=command.attempts,
            result_status=command.result_status,
            result_body=command.result_body,
            result_error=command.result_error,
            created_at=command.created_at,
            leased_at=command.leased_at,
            completed_at=command.completed_at,
            updated_at=command.updated_at,
        )

    @staticmethod
    def _stream_frame_read(
        frame: CommandStreamFrameModel,
        command: CommandModel,
    ) -> AgentCommandStreamFrameRead:
        return AgentCommandStreamFrameRead(
            id=UUID(frame.id),
            command_id=UUID(frame.command_id),
            server_id=UUID(frame.server_id),
            request_id=command.request_id,
            sequence=frame.sequence,
            data=frame.data,
            received_at=frame.received_at,
        )

    @staticmethod
    def _server_by_token(session: Session, token: str) -> ServerModel:
        server = session.scalar(select(ServerModel).where(ServerModel.agent_token == token))
        if server:
            return server
        raise InvalidAgentTokenError("invalid agent token")

    @staticmethod
    def _latest_telemetry_model(
        session: Session,
        server_id: str,
    ) -> TelemetrySnapshotModel | None:
        return session.scalar(
            select(TelemetrySnapshotModel)
            .where(TelemetrySnapshotModel.server_id == server_id)
            .order_by(
                TelemetrySnapshotModel.reported_at.desc(),
                TelemetrySnapshotModel.received_at.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _update_server_speed_from_system_traffic(
        server: ServerModel,
        previous: TelemetrySnapshotModel | None,
        payload: AgentTelemetryReport,
    ) -> None:
        if not payload.system or not previous:
            return
        if (
            previous.system_rx_total is None
            or previous.system_tx_total is None
            or previous.system_boot_time_unix != payload.system.boot_time_unix
        ):
            return
        if (
            payload.system.rx_total < previous.system_rx_total
            or payload.system.tx_total < previous.system_tx_total
        ):
            return

        current_at = InventoryStore._aware_datetime(payload.reported_at or datetime.now(tz=UTC))
        previous_at = InventoryStore._aware_datetime(previous.reported_at)
        elapsed = (current_at - previous_at).total_seconds()
        if elapsed <= 0:
            return
        server.current_download_speed = int(
            (payload.system.rx_total - previous.system_rx_total) / elapsed
        )
        server.current_upload_speed = int(
            (payload.system.tx_total - previous.system_tx_total) / elapsed
        )

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _lease_expired(command: CommandModel, now: datetime) -> bool:
        if command.leased_at is None:
            return True
        leased_at = InventoryStore._aware_datetime(command.leased_at)
        elapsed_ms = (now - leased_at).total_seconds() * 1000
        return elapsed_ms >= command.timeout_ms

    @staticmethod
    def _apply_command_result(
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
    ) -> None:
        now = datetime.now(tz=UTC)
        command.status = (
            AgentCommandStatus.FAILED.value
            if payload.error or payload.status >= 400
            else AgentCommandStatus.SUCCEEDED.value
        )
        command.result_status = payload.status
        command.result_body = payload.body
        command.result_error = payload.error
        command.completed_at = now
        command.updated_at = now
        server.last_heartbeat = now
        server.updated_at = now


def create_inventory_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = {}
    if url.drivername.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args, future=True)
