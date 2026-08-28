import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.server_management import (
    ServerRemovalPreview,
    ServerRemovalRequest,
    ServerRemovalResponse,
    ServerSettingsResponse,
    ServerSettingsUpdate,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    DuplicateServerNameError,
    InventoryStore,
    ServerNotFoundError,
)
from open_node.services.server_management import ServerManagementConflict

router = APIRouter(prefix="/servers", tags=["servers"])
Store = Annotated[InventoryStore, Depends(get_inventory_store)]


def call(operation, *args):
    try:
        return operation(*args)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ServerManagementConflict, DuplicateServerNameError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{server_id}/settings", response_model=ServerSettingsResponse)
def settings(server_id: UUID, store: Store):
    return call(store._server_management().settings, server_id)


@router.put("/{server_id}/settings", response_model=ServerSettingsResponse)
def update_settings(server_id: UUID, payload: ServerSettingsUpdate, store: Store):
    return call(store._server_management().update, server_id, payload)


@router.get("/{server_id}/removal", response_model=ServerRemovalPreview)
def removal_preview(server_id: UUID, store: Store):
    return call(store._server_management().preview, server_id)


@router.post("/{server_id}/remove", response_model=ServerRemovalResponse)
async def remove_server(
    server_id: UUID,
    payload: ServerRemovalRequest,
    store: Store,
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
):
    result = await asyncio.to_thread(call, store._server_management().remove, server_id, payload)
    await connections.disconnect(server_id)
    return result
