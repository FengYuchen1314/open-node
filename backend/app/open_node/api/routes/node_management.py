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
from open_node.services.inventory import (
    InventoryStore,
    ManagedNodeConflict,
    ManagedNodeNotFoundError,
)
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
    except (ManagedNodeConflict, SubscriptionAccessConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def apply(operation, store, connections, *args):
    result = await run_in_backup_thread(call, operation, *args)
    for command in result.commands:
        await connections.dispatch_command(store, command)
    return result


async def reconcile_managed_runtime(store, connections, server_ids):
    reconciled = []
    for server_id in dict.fromkeys(server_ids):
        payloads = [store.managed_protocol_command(server_id)]
        ingress = store.reconcile_managed_shared_ingress(server_id)
        if ingress is not None:
            payloads.append(ingress)
        commands = store.create_command_sequence(server_id, payloads)
        for command in commands:
            deployed = await connections.dispatch_command(store, command)
            reconciled.append(deployed.model_copy(update={"body": None}))
    return reconciled


@router.get("/nodes/{identifier}/settings", response_model=NodeManagementRead)
@router.get("/nodes/{identifier}/removal", response_model=NodeManagementRead)
def settings(identifier: UUID, store: Store):
    return call(store._node_management().read, identifier)


@router.put("/nodes/{identifier}/settings", response_model=NodeManagementResult)
async def update(identifier: UUID, payload: NodeUpdate, store: Store, connections: Connections):
    result = await apply(store._node_management().update, store, connections, identifier, payload)
    if result.node.protocol_profile is not None:
        result.commands.extend(
            await reconcile_managed_runtime(store, connections, [result.node.server_id])
        )
    return result


@router.post("/nodes/{identifier}/remove", response_model=NodeRemovalRead, status_code=202)
async def remove(identifier: UUID, payload: NodeRemoval, store: Store, connections: Connections):
    result = await apply(store._node_management().remove, store, connections, identifier, payload)
    result.commands.extend(
        await reconcile_managed_runtime(
            store,
            connections,
            [step.server_id for step in result.servers],
        )
    )
    return result


@router.get("/node-removals/{identifier}", response_model=NodeRemovalRead)
def status(identifier: UUID, store: Store):
    return call(store._node_management().read_removal, identifier)


@router.post("/node-removals/{identifier}/retry", response_model=NodeRemovalRead)
async def retry(identifier: UUID, store: Store, connections: Connections):
    result = await apply(store._node_management().retry, store, connections, identifier)
    result.commands.extend(
        await reconcile_managed_runtime(
            store,
            connections,
            [step.server_id for step in result.servers],
        )
    )
    return result
