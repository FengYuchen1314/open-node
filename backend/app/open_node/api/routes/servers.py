from typing import Annotated

from fastapi import APIRouter, Depends, status

from open_node.api.dependencies import get_inventory_store
from open_node.domain.inventory import ServerCreate, ServerCreateResponse, ServerRead
from open_node.services.inventory import InventoryStore

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
    server = store.create_server(payload)
    public_server = store._public_server(server)
    return ServerCreateResponse(server=public_server, agent_token=server.agent_token)
