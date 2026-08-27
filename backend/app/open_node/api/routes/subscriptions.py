from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.subscriptions import (
    ManagedNodeCreate,
    ManagedNodeResponse,
    ManagedNodesResponse,
    ProductUserCreate,
    ProductUserResponse,
    ProductUsersResponse,
    SubscriptionPlanAssignRequest,
    SubscriptionPlanAssignResponse,
    SubscriptionPlanCreate,
    SubscriptionPlanResponse,
    SubscriptionPlansResponse,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    DuplicateProductUserError,
    DuplicateSubscriptionPlanNameError,
    InventoryStore,
    ManagedNodeNotFoundError,
    ProductUserNotFoundError,
    ServerNotFoundError,
    SubscriptionPlanNotFoundError,
)

router = APIRouter(tags=["subscriptions"])


@router.get("/users", response_model=ProductUsersResponse)
def list_product_users(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUsersResponse:
    return ProductUsersResponse(users=store.list_product_users())


@router.post("/users", response_model=ProductUserResponse, status_code=status.HTTP_201_CREATED)
def create_product_user(
    payload: ProductUserCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserResponse:
    try:
        user = store.create_product_user(payload)
    except DuplicateProductUserError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ProductUserResponse(user=user)


@router.get("/nodes", response_model=ManagedNodesResponse)
def list_managed_nodes(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ManagedNodesResponse:
    return ManagedNodesResponse(nodes=store.list_managed_nodes())


@router.post("/nodes", response_model=ManagedNodeResponse, status_code=status.HTTP_201_CREATED)
def create_managed_node(
    payload: ManagedNodeCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ManagedNodeResponse:
    try:
        node = store.create_managed_node(payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ManagedNodeResponse(node=node)


@router.get("/plans", response_model=SubscriptionPlansResponse)
def list_subscription_plans(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionPlansResponse:
    return SubscriptionPlansResponse(plans=store.list_subscription_plans())


@router.post("/plans", response_model=SubscriptionPlanResponse, status_code=status.HTTP_201_CREATED)
def create_subscription_plan(
    payload: SubscriptionPlanCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionPlanResponse:
    try:
        plan = store.create_subscription_plan(payload)
    except DuplicateSubscriptionPlanNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ManagedNodeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SubscriptionPlanResponse(plan=plan)


@router.post("/users/{username}/plan", response_model=SubscriptionPlanAssignResponse)
async def assign_subscription_plan(
    username: str,
    payload: SubscriptionPlanAssignRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> SubscriptionPlanAssignResponse:
    try:
        user, plan, batches, warnings = store.assign_subscription_plan(username, payload)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SubscriptionPlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    commands = []
    if payload.queue_agent_commands:
        for batch in batches:
            command = store.create_command(
                batch.server_id,
                AgentCommandCreate(
                    method="POST",
                    path="/api/child/batch-apply",
                    body=batch.body,
                    timeout_ms=payload.command_timeout_ms,
                ),
            )
            commands.append(await connections.dispatch_command(store, command))

    return SubscriptionPlanAssignResponse(
        user=user,
        plan=plan,
        provisioning_batches=batches,
        commands=commands,
        warnings=warnings,
    )
