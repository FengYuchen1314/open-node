from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
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


class InvalidAgentTokenError(ValueError):
    """Raised when an agent presents an unknown bootstrap token."""


class DuplicateServerNameError(ValueError):
    """Raised when a server name would no longer be a stable inventory key."""


class ServerNotFoundError(ValueError):
    """Raised when an inventory lookup targets an unknown server."""


class CommandNotFoundError(ValueError):
    """Raised when an agent command cannot be found for the requesting server."""


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
            session.commit()
            session.refresh(command)
            return self._command_read(command)

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


def create_inventory_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = {}
    if url.drivername.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args, future=True)
