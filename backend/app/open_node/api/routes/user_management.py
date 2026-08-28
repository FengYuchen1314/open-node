import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.user_management import (
    UserManagementRead,
    UserManagementResult,
    UserRemoval,
    UserRemovalRead,
    UserUpdate,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    InventoryStore,
    ManagedNodeConflict,
    ManagedNodeNotFoundError,
    ProductUserConflict,
    ProductUserNotFoundError,
)
from open_node.services.subscription_access import SubscriptionAccessConflict
from open_node.services.user_management import UserRemovalNotFoundError

router = APIRouter(tags=["subscriptions"])
Store = Annotated[InventoryStore, Depends(get_inventory_store)]
Connections = Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)]


def call(operation, *args):
    try:
        return operation(*args)
    except (ProductUserNotFoundError, UserRemovalNotFoundError, ManagedNodeNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProductUserConflict, SubscriptionAccessConflict, ManagedNodeConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def apply(operation, store, connections, *args):
    result = await asyncio.to_thread(call, operation, *args)
    for command in result.commands:
        await connections.dispatch_command(store, command)
    return result


@router.get("/user-settings", response_model=UserManagementRead)
@router.get("/users/{username}/settings", response_model=UserManagementRead)
@router.get("/users/{username}/removal", response_model=UserManagementRead)
def settings(username: str, store: Store):
    return call(store._user_management().read, username)


@router.put("/user-settings", response_model=UserManagementResult)
@router.put("/users/{username}/settings", response_model=UserManagementResult)
async def update(username: str, payload: UserUpdate, store: Store, connections: Connections):
    return await apply(store._user_management().update, store, connections, username, payload)


@router.post("/user-remove", response_model=UserRemovalRead, status_code=202)
@router.post("/users/{username}/remove", response_model=UserRemovalRead, status_code=202)
async def remove(username: str, payload: UserRemoval, store: Store, connections: Connections):
    return await apply(store._user_management().remove, store, connections, username, payload)


@router.get("/user-removals/{identifier}", response_model=UserRemovalRead)
def removal_status(identifier: UUID, store: Store):
    return call(store._user_management().read_removal, identifier)


@router.post("/user-removals/{identifier}/retry", response_model=UserRemovalRead)
async def retry(identifier: UUID, store: Store, connections: Connections):
    return await apply(store._user_management().retry, store, connections, identifier)
