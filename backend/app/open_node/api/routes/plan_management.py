import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.plan_management import (
    PlanManagementRead,
    PlanManagementResult,
    PlanRemoval,
    PlanUpdate,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    DuplicateSubscriptionPlanNameError,
    InventoryStore,
    ManagedNodeNotFoundError,
    ProductUserNotFoundError,
    SubscriptionPlanNotFoundError,
)
from open_node.services.plan_management import PlanManagementConflict
from open_node.services.subscription_access import SubscriptionAccessConflict

router = APIRouter(tags=["subscriptions"])
Store = Annotated[InventoryStore, Depends(get_inventory_store)]
Connections = Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)]


def call(operation, *args):
    try:
        return operation(*args)
    except (
        ProductUserNotFoundError,
        SubscriptionPlanNotFoundError,
        ManagedNodeNotFoundError,
    ) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        PlanManagementConflict,
        SubscriptionAccessConflict,
        DuplicateSubscriptionPlanNameError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def apply(operation, store, connections, *args):
    result = await asyncio.to_thread(call, operation, *args)
    for command in result.commands:
        await connections.dispatch_command(store, command)
    return result


@router.get("/plans/{identifier}/settings", response_model=PlanManagementRead)
def settings(identifier: UUID, store: Store):
    return call(store._plan_management().read, identifier)


@router.put("/plans/{identifier}/settings", response_model=PlanManagementResult)
async def update(identifier: UUID, payload: PlanUpdate, store: Store, connections: Connections):
    return await apply(store._plan_management().update, store, connections, identifier, payload)


@router.post("/plans/{identifier}/remove", response_model=PlanManagementResult)
async def remove(identifier: UUID, payload: PlanRemoval, store: Store, connections: Connections):
    return await apply(store._plan_management().remove, store, connections, identifier, payload)


@router.get("/user-plan/removal", response_model=PlanManagementRead)
@router.get("/users/{username}/plan/removal", response_model=PlanManagementRead)
def assignment(username: str, store: Store):
    return call(store._plan_management().assignment, username)


@router.post("/user-plan/remove", response_model=PlanManagementResult)
@router.post("/users/{username}/plan/remove", response_model=PlanManagementResult)
async def unassign(username: str, payload: PlanRemoval, store: Store, connections: Connections):
    return await apply(store._plan_management().unassign, store, connections, username, payload)
