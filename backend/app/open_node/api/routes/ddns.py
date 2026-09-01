"""Administrator DDNS configuration and durable manual trigger routes."""

from uuid import UUID

from fastapi import APIRouter, Request

from open_node.api.backup import BackupAPIRoute
from open_node.domain.ddns import DDNSConfig, DDNSServerRead, DDNSSyncRead, DDNSWorkspaceRead
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(route_class=BackupAPIRoute, prefix="/ddns", tags=["ddns"])


@router.get("", response_model=DDNSWorkspaceRead)
def workspace(request: Request):
    return request.app.state.ddns.workspace()


@router.put("/{server_id}", response_model=DDNSServerRead)
async def configure(server_id: UUID, value: DDNSConfig, request: Request):
    return await run_in_backup_threadpool(request.app.state.ddns.configure, server_id, value)


@router.post("/{server_id}/sync", response_model=DDNSSyncRead)
async def synchronize(server_id: UUID, request: Request):
    return await run_in_backup_threadpool(request.app.state.ddns.queue, server_id)
