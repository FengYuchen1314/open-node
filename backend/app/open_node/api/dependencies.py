from fastapi import Request

from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import InventoryStore


def get_inventory_store(request: Request) -> InventoryStore:
    return request.app.state.inventory


def get_agent_connection_manager(request: Request) -> AgentConnectionManager:
    return request.app.state.agent_connections
