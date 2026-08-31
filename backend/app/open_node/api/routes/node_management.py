from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.node_management import (
    NodeManagementRead,
    NodeManagementResult,
    NodeRemoval,
    NodeRemovalRead,
    NodeUpdate,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.backup_runtime import run_in_backup_thread
from open_node.services.inventory import InventoryStore, ManagedNodeNotFoundError
from open_node.services.node_management import NodeRemovalNotFoundError
from open_node.services.subscription_access import SubscriptionAccessConflict

router = APIRouter(route_class=BackupAPIRoute, tags=["subscriptions"])
Store = Annotated[InventoryStore, Depends(get_inventory_store)]
Connections = Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)]


def call(operation, *args):
    try:
        return operation(*args)
    except (ManagedNodeNotFoundError, NodeRemovalNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SubscriptionAccessConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def apply(operation, store, connections, *args):
    result = await run_in_backup_thread(call, operation, *args)
    for command in result.commands:
        await connections.dispatch_command(store, command)
    return result


@router.get("/nodes/{identifier}/settings", response_model=NodeManagementRead)
@router.get("/nodes/{identifier}/removal", response_model=NodeManagementRead)
def settings(identifier: UUID, store: Store):
    return call(store._node_management().read, identifier)


@router.put("/nodes/{identifier}/settings", response_model=NodeManagementResult)
async def update(identifier: UUID, payload: NodeUpdate, store: Store, connections: Connections):
    return await apply(store._node_management().update, store, connections, identifier, payload)


@router.post("/nodes/{identifier}/remove", response_model=NodeRemovalRead, status_code=202)
async def remove(identifier: UUID, payload: NodeRemoval, store: Store, connections: Connections):
    return await apply(store._node_management().remove, store, connections, identifier, payload)


@router.get("/node-removals/{identifier}", response_model=NodeRemovalRead)
def status(identifier: UUID, store: Store):
    return call(store._node_management().read_removal, identifier)


@router.post("/node-removals/{identifier}/retry", response_model=NodeRemovalRead)
async def retry(identifier: UUID, store: Store, connections: Connections):
    return await apply(store._node_management().retry, store, connections, identifier)
