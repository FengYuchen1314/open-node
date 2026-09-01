"""Official-compatible administrator node speed-test routes."""

from uuid import UUID

from fastapi import APIRouter, Query, Request, WebSocket

from open_node.api.backup import BackupAPIRoute
from open_node.domain.speedtests import (
    MihomoStatusRead,
    SpeedTesterCreate,
    SpeedTesterMutation,
    SpeedTesterSecret,
    SpeedTestersRead,
    SpeedTestResultsRead,
    SpeedTestRunAccepted,
    SpeedTestRunRequest,
)
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(route_class=BackupAPIRoute, prefix="/speedtest", tags=["speedtest"])


@router.post("/run", response_model=SpeedTestRunAccepted)
async def run_speedtest(payload: SpeedTestRunRequest, request: Request):
    return await request.app.state.speedtests.queue(payload)


@router.get("/results", response_model=SpeedTestResultsRead)
def results(
    request: Request,
    node_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    latest: bool = False,
):
    return SpeedTestResultsRead(results=request.app.state.speedtest_store.results(
        node_id=str(node_id) if node_id else None, limit=limit, latest=latest,
    ))


@router.get("/mihomo-status", response_model=MihomoStatusRead)
def mihomo_status(request: Request):
    return request.app.state.mihomo_speedtest.status()


@router.get("/testers", response_model=SpeedTestersRead)
def testers(request: Request):
    return request.app.state.speedtest_store.list_testers(
        request.app.state.speedtester_connections.online_ids()
    )


@router.post("/testers/create", response_model=SpeedTesterSecret)
async def create_tester(payload: SpeedTesterCreate, request: Request):
    identity = request.state.administrator
    return await run_in_backup_threadpool(
        request.app.state.speedtest_store.create_tester,
        payload.name,
        identity.username,
        request.app.state.speedtester_connections.online_ids(),
    )


@router.post("/testers/rotate-token", response_model=SpeedTesterSecret)
async def rotate_tester(payload: SpeedTesterMutation, request: Request):
    identifier = str(payload.id)
    secret = await run_in_backup_threadpool(
        request.app.state.speedtest_store.rotate,
        identifier,
        request.app.state.speedtester_connections.online_ids(),
    )
    await request.app.state.speedtester_connections.disconnect(identifier)
    return secret


@router.post("/testers/revoke", status_code=204)
async def revoke_tester(payload: SpeedTesterMutation, request: Request):
    identifier = str(payload.id)
    await run_in_backup_threadpool(request.app.state.speedtest_store.revoke, identifier)
    await request.app.state.speedtester_connections.disconnect(identifier)


async def speedtester_websocket(websocket: WebSocket) -> None:
    await websocket.app.state.speedtester_connections.serve(websocket)
