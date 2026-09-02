"""Administrator API for the declarative managed TCP 443 ingress."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.inventory import AgentCommandCreate
from open_node.domain.shared_ingress import (
    SharedIngressApplyRequest,
    SharedIngressDeleteRequest,
    SharedIngressMutationResponse,
    SharedIngressState,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    AgentCapabilityUnavailableError,
    InventoryStore,
    ServerNotFoundError,
)
from open_node.services.shared_ingress import (
    SharedIngressBindingError,
    SharedIngressConflict,
    SharedIngressStore,
)

router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/servers/{server_id}/shared-ingress",
    tags=["shared-ingress"],
)


def _service(inventory: InventoryStore) -> SharedIngressStore:
    return SharedIngressStore(inventory)


def _raise_store_error(exc: ValueError) -> None:
    if isinstance(exc, ServerNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, SharedIngressConflict):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _queue(
    server_id: UUID,
    payload: AgentCommandCreate,
    inventory: InventoryStore,
    connections: AgentConnectionManager,
):
    try:
        command = inventory.create_command(server_id, payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AgentCapabilityUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await connections.dispatch_command(inventory, command)


@router.get("", response_model=SharedIngressState)
def get_shared_ingress(
    server_id: UUID,
    inventory: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SharedIngressState:
    try:
        return _service(inventory).get(server_id)
    except ServerNotFoundError as exc:
        _raise_store_error(exc)


@router.put("", response_model=SharedIngressMutationResponse)
async def apply_shared_ingress(
    server_id: UUID,
    payload: SharedIngressApplyRequest,
    inventory: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> SharedIngressMutationResponse:
    try:
        state = _service(inventory).save(
            server_id,
            payload.configuration,
            expected_revision=payload.expected_revision,
        )
    except (ServerNotFoundError, SharedIngressConflict, SharedIngressBindingError) as exc:
        _raise_store_error(exc)
    command = await _queue(
        server_id,
        AgentCommandCreate(
            method="PUT",
            path="/api/child/nginx/shared-ingress",
            body={
                "revision": state.revision,
                "configuration": payload.configuration.model_dump(mode="json"),
            },
            timeout_ms=payload.command_timeout_ms,
        ),
        inventory,
        connections,
    )
    return SharedIngressMutationResponse(state=state, command=command)


@router.delete("", response_model=SharedIngressMutationResponse)
async def delete_shared_ingress(
    server_id: UUID,
    payload: SharedIngressDeleteRequest,
    inventory: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> SharedIngressMutationResponse:
    try:
        state = _service(inventory).disable(
            server_id,
            expected_revision=payload.expected_revision,
        )
    except (ServerNotFoundError, SharedIngressConflict) as exc:
        _raise_store_error(exc)
    command = await _queue(
        server_id,
        AgentCommandCreate(
            method="DELETE",
            path="/api/child/nginx/shared-ingress",
            body={"revision": state.revision},
            timeout_ms=payload.command_timeout_ms,
        ),
        inventory,
        connections,
    )
    return SharedIngressMutationResponse(state=state, command=command)
