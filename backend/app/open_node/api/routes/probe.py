from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.probe import (
    ProbeAccessTokenCreateResponse,
    ProbeSettingsResponse,
    ProbeTaskCreate,
    ProbeTaskDispatchItem,
    ProbeTaskDispatchResponse,
    ProbeTaskListResponse,
    ProbeTaskResponse,
    ProbeTaskUpdate,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.backup_runtime import run_in_backup_threadpool
from open_node.services.inventory import (
    InventoryStore,
    ProbeTaskNotFoundError,
    ServerNotFoundError,
)

router = APIRouter(route_class=BackupAPIRoute, prefix="/probe", tags=["probe"])


@router.post("/access-token", response_model=ProbeAccessTokenCreateResponse)
def create_probe_access_token(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeAccessTokenCreateResponse:
    return store.create_probe_access_token()


@router.delete("/access-token", response_model=ProbeSettingsResponse)
def clear_probe_access_token(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeSettingsResponse:
    return store.clear_probe_access_token()


@router.get("/tasks", response_model=ProbeTaskListResponse)
def list_probe_tasks(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeTaskListResponse:
    return ProbeTaskListResponse(tasks=store.list_probe_tasks())


@router.post(
    "/tasks",
    response_model=ProbeTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_probe_task(
    payload: ProbeTaskCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeTaskResponse:
    try:
        return ProbeTaskResponse(task=store.create_probe_task(payload))
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/tasks/{task_id}", response_model=ProbeTaskResponse)
def update_probe_task(
    task_id: UUID,
    payload: ProbeTaskUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeTaskResponse:
    try:
        return ProbeTaskResponse(task=store.update_probe_task(task_id, payload))
    except ProbeTaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc


@router.post("/tasks/dispatch-due", response_model=ProbeTaskDispatchResponse)
async def dispatch_due_probe_tasks(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ProbeTaskDispatchResponse:
    checked_at, queued = await run_in_backup_threadpool(store.dispatch_due_probe_tasks, limit)
    dispatched = []
    for task, command in queued:
        dispatched_command = await connections.dispatch_command(store, command)
        dispatched.append(ProbeTaskDispatchItem(task=task, command=dispatched_command))
    return ProbeTaskDispatchResponse(checked_at=checked_at, dispatched=dispatched)
