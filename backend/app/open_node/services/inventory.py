from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from open_node.domain.inventory import (
    AgentCapabilities,
    AgentHeartbeatRequest,
    AgentRead,
    AgentRegistrationRequest,
    ConnectionMode,
    ServerCreate,
    ServerRead,
    ServerRecord,
    ServerStatus,
    TrafficSource,
    TrafficStatsMode,
    XrayMode,
)


class InvalidAgentTokenError(ValueError):
    """Raised when an agent presents an unknown bootstrap token."""


class DuplicateServerNameError(ValueError):
    """Raised when a server name would no longer be a stable inventory key."""


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
    def _server_by_token(session: Session, token: str) -> ServerModel:
        server = session.scalar(select(ServerModel).where(ServerModel.agent_token == token))
        if server:
            return server
        raise InvalidAgentTokenError("invalid agent token")


def create_inventory_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = {}
    if url.drivername.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args, future=True)
