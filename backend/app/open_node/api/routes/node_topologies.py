from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_inventory_store
from open_node.domain.node_topologies import (
    NodeTopologiesResponse,
    NodeTopologyCreate,
    NodeTopologyDelete,
    NodeTopologyResponse,
    NodeTopologyUpdate,
)
from open_node.services.inventory import InventoryStore
from open_node.services.node_topologies import (
    NodeTopologyConflict,
    NodeTopologyNotFoundError,
)

router = APIRouter(route_class=BackupAPIRoute, tags=["node topologies"])
Store = Annotated[InventoryStore, Depends(get_inventory_store)]


def call(operation, *args):
    try:
        return operation(*args)
    except NodeTopologyNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NodeTopologyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/node-topologies", response_model=NodeTopologiesResponse)
def list_node_topologies(store: Store):
    return call(store._node_topologies().list)


@router.post(
    "/node-topologies",
    response_model=NodeTopologyResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_node_topology(payload: NodeTopologyCreate, store: Store):
    return NodeTopologyResponse(topology=call(store._node_topologies().create, payload))


@router.put("/node-topologies/{identifier}", response_model=NodeTopologyResponse)
def update_node_topology(identifier: UUID, payload: NodeTopologyUpdate, store: Store):
    return NodeTopologyResponse(
        topology=call(store._node_topologies().update, identifier, payload)
    )


@router.delete("/node-topologies/{identifier}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node_topology(identifier: UUID, payload: NodeTopologyDelete, store: Store):
    call(
        store._node_topologies().delete,
        identifier,
        payload.expected_revision,
        payload.confirm_name,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
