from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from fastapi import WebSocketDisconnect

from open_node.domain.inventory import AgentCapabilities, AgentCommandRead
from open_node.services.backup_runtime import run_in_backup_thread
from open_node.services.inventory import (
    CommandNotFoundError,
    InventoryStore,
    required_command_capabilities,
)


class AgentTransport(Protocol):
    async def send_json(self, data: Any) -> None: ...


@dataclass
class AgentConnection:
    server_id: UUID
    websocket: AgentTransport
    capabilities: AgentCapabilities


class AgentConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[UUID, AgentConnection] = {}

    def is_connected(self, server_id: UUID) -> bool:
        return server_id in self._connections

    def register(
        self,
        server_id: UUID,
        websocket: AgentTransport,
        capabilities: AgentCapabilities,
    ) -> None:
        self._connections[server_id] = AgentConnection(
            server_id=server_id,
            websocket=websocket,
            capabilities=capabilities,
        )

    def unregister(self, server_id: UUID, websocket: AgentTransport) -> None:
        current = self._connections.get(server_id)
        if current and current.websocket is websocket:
            self._connections.pop(server_id, None)

    async def disconnect(self, server_id: UUID) -> None:
        connection = self._connections.pop(server_id, None)
        if connection and (close := getattr(connection.websocket, "close", None)):
            try:
                await close(code=1008)
            except (OSError, RuntimeError, WebSocketDisconnect):
                pass

    async def dispatch_pending_commands(self, store: InventoryStore, server_id: UUID) -> None:
        connection = self._connections.get(server_id)
        if not connection or not connection.capabilities.rpc:
            return
        for command in store.list_dispatchable_commands(server_id):
            if self._connections.get(server_id) is not connection:
                break
            await self.dispatch_command(store, command)

    async def dispatch_ready_commands(self, store: InventoryStore) -> None:
        for server_id in list(self._connections):
            await self.dispatch_pending_commands(store, server_id)

    async def dispatch_command(
        self,
        store: InventoryStore,
        command: AgentCommandRead,
    ) -> AgentCommandRead:
        relay = getattr(store, "federation_relay", None)
        if relay is not None and relay.is_federated(command.server_id):
            return await run_in_backup_thread(relay.dispatch_agent_command, command)

        connection = self._connections.get(command.server_id)
        if not connection or not connection.capabilities.rpc:
            return command
        if command.stream and not connection.capabilities.stream:
            return command
        if any(
            not getattr(connection.capabilities, capability)
            for capability in required_command_capabilities(command.path, command.body)
        ):
            try:
                return store.reconcile_command_capability_loss(command.id)
            except CommandNotFoundError:
                return command

        try:
            leased = store.lease_command_for_push(command.id)
        except CommandNotFoundError:
            return command
        if leased is None:
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
        except (OSError, RuntimeError, WebSocketDisconnect):
            self.unregister(connection.server_id, connection.websocket)
            return store.release_command_lease(leased.id, leased.attempts)
        return leased
