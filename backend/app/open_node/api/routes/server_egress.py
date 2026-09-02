from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.server_egress import (
    ServerEgressApplyRequest,
    ServerEgressApplyResponse,
    ServerEgressCatalogRead,
    ServerEgressPreviewRead,
    ServerEgressPreviewRequest,
    ServerEgressRemovePreviewRequest,
    ServerEgressRemoveRequest,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.change_sets import ChangeSetConflict
from open_node.services.inventory import InventoryStore
from open_node.services.server_egress import (
    ServerEgress,
    ServerEgressConflict,
    ServerEgressNotFoundError,
)

router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/servers/{server_id}/egress",
    tags=["server egress"],
)
Store = Annotated[InventoryStore, Depends(get_inventory_store)]
Connections = Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)]


def _service(store: InventoryStore) -> ServerEgress:
    return ServerEgress(store)


def _error(exc):
    if isinstance(exc, ServerEgressNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (ServerEgressConflict, ChangeSetConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("", response_model=ServerEgressCatalogRead)
def catalog(server_id: UUID, store: Store):
    try:
        return _service(store).catalog(server_id)
    except (ServerEgressNotFoundError, ServerEgressConflict) as exc:
        _error(exc)


@router.post("/preview", response_model=ServerEgressPreviewRead)
def preview(server_id: UUID, payload: ServerEgressPreviewRequest, store: Store):
    try:
        return _service(store).preview(server_id, payload)
    except (ServerEgressNotFoundError, ServerEgressConflict) as exc:
        _error(exc)


@router.post("/apply", response_model=ServerEgressApplyResponse)
async def apply(
    server_id: UUID,
    payload: ServerEgressApplyRequest,
    store: Store,
    connections: Connections,
):
    try:
        result, commands = _service(store).apply(server_id, payload)
    except (ServerEgressNotFoundError, ServerEgressConflict, ChangeSetConflict) as exc:
        _error(exc)
    dispatched = [await connections.dispatch_command(store, command) for command in commands]
    result.command_ids = [command.id for command in dispatched]
    return result


@router.post("/remove/preview", response_model=ServerEgressPreviewRead)
def preview_remove(
    server_id: UUID,
    payload: ServerEgressRemovePreviewRequest,
    store: Store,
):
    try:
        return _service(store).preview_remove(server_id, payload)
    except (ServerEgressNotFoundError, ServerEgressConflict) as exc:
        _error(exc)


@router.post("/remove", response_model=ServerEgressApplyResponse)
async def remove(
    server_id: UUID,
    payload: ServerEgressRemoveRequest,
    store: Store,
    connections: Connections,
):
    try:
        result, commands = _service(store).remove(server_id, payload)
    except (ServerEgressNotFoundError, ServerEgressConflict, ChangeSetConflict) as exc:
        _error(exc)
    dispatched = [await connections.dispatch_command(store, command) for command in commands]
    result.command_ids = [command.id for command in dispatched]
    return result
