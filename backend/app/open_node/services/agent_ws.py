from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from open_node.domain.inventory import AgentCapabilities, AgentCommandRead
from open_node.services.inventory import CommandNotFoundError, InventoryStore


@dataclass
class AgentConnection:
    server_id: UUID
    websocket: WebSocket
    capabilities: AgentCapabilities


class AgentConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, AgentConnection] = {}

    def is_connected(self, server_id: UUID) -> bool:
        return server_id in self._connections

    def register(
        self,
        server_id: UUID,
        websocket: WebSocket,
        capabilities: AgentCapabilities,
    ) -> None:
        self._connections[server_id] = AgentConnection(
            server_id=server_id,
            websocket=websocket,
            capabilities=capabilities,
        )

    def unregister(self, server_id: UUID, websocket: WebSocket) -> None:
        current = self._connections.get(server_id)
        if current and current.websocket is websocket:
            self._connections.pop(server_id, None)

    async def dispatch_command(
        self,
        store: InventoryStore,
        command: AgentCommandRead,
    ) -> AgentCommandRead:
        connection = self._connections.get(command.server_id)
        if not connection or not connection.capabilities.rpc:
            return command

        try:
            leased = store.lease_command_for_push(command.id)
        except CommandNotFoundError:
            return command

        try:
            await connection.websocket.send_json(
                {
                    "type": "rpc_call",
                    "payload": {
                        "request_id": leased.request_id,
                        "method": leased.method,
                        "path": leased.path,
                        "query": leased.query,
                        "body": leased.body,
                        "timeout_ms": leased.timeout_ms,
                        "stream": leased.stream,
                    },
                }
            )
        except (RuntimeError, WebSocketDisconnect):
            self.unregister(connection.server_id, connection.websocket)
        return leased
