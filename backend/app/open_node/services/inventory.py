from datetime import UTC, datetime
from secrets import token_urlsafe
from threading import RLock
from uuid import UUID, uuid4

from open_node.domain.inventory import (
    AgentHeartbeatRequest,
    AgentRead,
    AgentRegistrationRequest,
    ServerCreate,
    ServerRead,
    ServerRecord,
    ServerStatus,
)


class InvalidAgentTokenError(ValueError):
    """Raised when an agent presents an unknown bootstrap token."""


class InventoryStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._servers: dict[UUID, ServerRecord] = {}
        self._agents: dict[UUID, AgentRead] = {}
        self._agent_id_by_server: dict[UUID, UUID] = {}

    def list_servers(self) -> list[ServerRead]:
        with self._lock:
            return [self._public_server(server) for server in self._servers.values()]

    def create_server(self, payload: ServerCreate) -> ServerRecord:
        now = datetime.now(tz=UTC)
        record = ServerRecord(
            id=uuid4(),
            name=payload.name,
            status=ServerStatus.PENDING,
            ip_address=payload.ip_address,
            ip_address_v6=payload.ip_address_v6,
            domain=payload.domain,
            domain_v6=payload.domain_v6,
            connection_mode=payload.connection_mode,
            listen_port=payload.listen_port,
            pull_address=payload.pull_address,
            pull_address_v6=payload.pull_address_v6,
            pull_port=payload.pull_port,
            ipv6_enabled=payload.ipv6_enabled,
            traffic_limit=payload.traffic_limit,
            traffic_stats_mode=payload.traffic_stats_mode,
            traffic_source=payload.traffic_source,
            xray_mode=payload.xray_mode,
            created_at=now,
            updated_at=now,
            agent_token=token_urlsafe(32),
        )
        with self._lock:
            self._servers[record.id] = record
        return record

    def list_agents(self) -> list[AgentRead]:
        with self._lock:
            return list(self._agents.values())

    def register_agent(self, payload: AgentRegistrationRequest) -> tuple[AgentRead, ServerRead]:
        with self._lock:
            server = self._server_by_token(payload.token)
            now = datetime.now(tz=UTC)
            updated_server = server.model_copy(
                update={
                    "status": ServerStatus.CONNECTED,
                    "last_heartbeat": now,
                    "ip_address": payload.public_ipv4 or server.ip_address,
                    "ip_address_v6": payload.public_ipv6 or server.ip_address_v6,
                    "connection_mode": payload.connection_mode,
                    "listen_port": payload.listen_port,
                    "xray_mode": payload.xray_mode,
                    "updated_at": now,
                }
            )
            self._servers[server.id] = updated_server

            agent_id = self._agent_id_by_server.get(server.id, uuid4())
            existing = self._agents.get(agent_id)
            registered_at = existing.registered_at if existing else now
            agent = AgentRead(
                id=agent_id,
                server_id=server.id,
                hostname=payload.hostname,
                agent_version=payload.agent_version,
                connection_mode=payload.connection_mode,
                listen_port=payload.listen_port,
                public_ipv4=payload.public_ipv4,
                public_ipv6=payload.public_ipv6,
                xray_mode=payload.xray_mode,
                capabilities=payload.capabilities,
                warp_installed=payload.warp_installed,
                same_host_as_master=payload.same_host_as_master,
                registered_at=registered_at,
                last_seen_at=now,
            )
            self._agents[agent.id] = agent
            self._agent_id_by_server[server.id] = agent.id
            return agent, self._public_server(updated_server)

    def record_heartbeat(self, payload: AgentHeartbeatRequest) -> ServerRead:
        with self._lock:
            server = self._server_by_token(payload.token)
            now = datetime.now(tz=UTC)
            updated_server = server.model_copy(
                update={
                    "status": ServerStatus.CONNECTED,
                    "last_heartbeat": now,
                    "current_upload_speed": payload.upload_speed,
                    "current_download_speed": payload.download_speed,
                    "listen_port": payload.listen_port or server.listen_port,
                    "ip_address": payload.public_ipv4 or server.ip_address,
                    "ip_address_v6": payload.public_ipv6 or server.ip_address_v6,
                    "updated_at": now,
                }
            )
            self._servers[server.id] = updated_server

            agent_id = self._agent_id_by_server.get(server.id)
            if agent_id and agent_id in self._agents:
                self._agents[agent_id] = self._agents[agent_id].model_copy(
                    update={"last_seen_at": now, "listen_port": updated_server.listen_port}
                )
            return self._public_server(updated_server)

    def _server_by_token(self, token: str) -> ServerRecord:
        for server in self._servers.values():
            if server.agent_token == token:
                return server
        raise InvalidAgentTokenError("invalid agent token")

    @staticmethod
    def _public_server(server: ServerRecord) -> ServerRead:
        return ServerRead(**server.model_dump(exclude={"agent_token"}))
