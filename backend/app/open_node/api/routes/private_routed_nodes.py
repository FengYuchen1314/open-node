from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.api.routes.subscriber_auth import Identity
from open_node.api.routes.subscriber_permissions import require_feature
from open_node.domain.private_routed_nodes import (
    PrivateRoutedNodeCreate,
    PrivateRoutedNodeMutationResponse,
    PrivateRoutedNodesResponse,
    PrivateRoutedPolicyRead,
    PrivateRoutedPolicyUpdate,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.change_sets import ChangeSetConflict
from open_node.services.inventory import (
    InventoryStore,
    ProductUserNotFoundError,
    SubscriptionUnavailableError,
)
from open_node.services.private_routed_nodes import (
    PrivateRoutedNodeConflict,
    PrivateRoutedNodeNotFoundError,
)

router = APIRouter(
    route_class=BackupAPIRoute, prefix="/private-routed-nodes", tags=["private routed nodes"]
)
account_router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/account/private-routed-nodes",
    tags=["subscriber private routed nodes"],
    dependencies=[Depends(require_feature("private_routes"))],
)


def _raise_service_error(exc):
    if isinstance(exc, (ProductUserNotFoundError, PrivateRoutedNodeNotFoundError)):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(
        exc,
        (PrivateRoutedNodeConflict, ChangeSetConflict, SubscriptionUnavailableError),
    ):
        raise HTTPException(409, str(exc)) from exc
    raise exc


async def _dispatch(store, connections, result):
    result.commands = [
        await connections.dispatch_command(store, command) for command in result.commands
    ]
    return result


@router.get("", response_model=PrivateRoutedNodesResponse)
def list_private_routed_nodes(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    return store._private_routed_nodes().list()


@router.get("/policy", response_model=PrivateRoutedPolicyRead)
def get_private_routed_policy(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    return store._private_routed_nodes().list().policy


@router.put("/policy", response_model=PrivateRoutedPolicyRead)
def update_private_routed_policy(
    payload: PrivateRoutedPolicyUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    return store._private_routed_nodes().update_policy(payload)


@account_router.get("", response_model=PrivateRoutedNodesResponse)
def list_account_private_routed_nodes(
    identity: Identity,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        return store._private_routed_nodes().list(identity.username)
    except (ProductUserNotFoundError, PrivateRoutedNodeNotFoundError) as exc:
        _raise_service_error(exc)


@account_router.post(
    "",
    response_model=PrivateRoutedNodeMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_account_private_routed_node(
    payload: PrivateRoutedNodeCreate,
    identity: Identity,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
):
    try:
        result = store._private_routed_nodes().create(identity.username, payload)
    except (
        ProductUserNotFoundError,
        PrivateRoutedNodeConflict,
        ChangeSetConflict,
        SubscriptionUnavailableError,
    ) as exc:
        _raise_service_error(exc)
    return await _dispatch(store, connections, result)


@account_router.delete("/{identifier}", response_model=PrivateRoutedNodeMutationResponse)
async def delete_account_private_routed_node(
    identifier: UUID,
    identity: Identity,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
    command_timeout_ms: Annotated[int, Query(ge=1_000, le=300_000)] = 30_000,
):
    try:
        result = store._private_routed_nodes().delete(
            identity.username, identifier, command_timeout_ms
        )
    except (
        ProductUserNotFoundError,
        PrivateRoutedNodeNotFoundError,
        PrivateRoutedNodeConflict,
        ChangeSetConflict,
    ) as exc:
        _raise_service_error(exc)
    return await _dispatch(store, connections, result)
