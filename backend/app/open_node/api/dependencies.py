from fastapi import Request

from open_node.services.inventory import InventoryStore


def get_inventory_store(request: Request) -> InventoryStore:
    return request.app.state.inventory
