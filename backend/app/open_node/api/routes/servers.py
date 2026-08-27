from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.dependencies import get_inventory_store
from open_node.domain.inventory import (
    ServerCreate,
    ServerCreateResponse,
    ServerRead,
    ServerTelemetryResponse,
)
from open_node.services.inventory import (
    DuplicateServerNameError,
    InventoryStore,
    ServerNotFoundError,
)

router = APIRouter(prefix="/servers", tags=["servers"])


@router.get("", response_model=list[ServerRead])
def list_servers(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> list[ServerRead]:
    return store.list_servers()


@router.post("", response_model=ServerCreateResponse, status_code=status.HTTP_201_CREATED)
def create_server(
    payload: ServerCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ServerCreateResponse:
    try:
        server = store.create_server(payload)
    except DuplicateServerNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    public_server = store.public_server(server)
    return ServerCreateResponse(server=public_server, agent_token=server.agent_token)


@router.get("/{server_id}/telemetry/latest", response_model=ServerTelemetryResponse)
def latest_server_telemetry(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ServerTelemetryResponse:
    try:
        latest = store.latest_telemetry(server_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ServerTelemetryResponse(server_id=server_id, latest=latest)
