import asyncio
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from open_node.api.dependencies import get_inventory_store
from open_node.domain.probe import (
    ProbePayload,
    ProbeSeriesResponse,
    ProbeSettingsResponse,
    ProbeSettingsUpdate,
)
from open_node.services.inventory import InventoryStore, ProbeNotFoundError
from open_node.services.probe_stream import PublicProbeStreamManager

router = APIRouter(prefix="/public", tags=["public"])


@router.get(
    "/probe-servers",
    response_model=ProbePayload,
    response_model_exclude_none=True,
)
def public_probe_servers(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbePayload:
    return store.public_probe_payload()


@router.get(
    "/probe-settings",
    response_model=ProbeSettingsResponse,
    response_model_exclude_none=True,
)
def get_public_probe_settings(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeSettingsResponse:
    return ProbeSettingsResponse(settings=store.probe_settings())


@router.put(
    "/probe-settings",
    response_model=ProbeSettingsResponse,
    response_model_exclude_none=True,
)
def update_public_probe_settings(
    payload: ProbeSettingsUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbeSettingsResponse:
    return store.update_probe_settings(payload)


@router.get(
    "/probe-series",
    response_model=ProbeSeriesResponse,
    response_model_exclude_none=True,
)
def public_probe_series(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    server: Annotated[int, Query(ge=0)],
    metric: str = "ping",
    range_name: Annotated[str, Query(alias="range")] = "1h",
    target: str = "__avg__",
    all_targets: Annotated[bool, Query(alias="all")] = False,
) -> ProbeSeriesResponse | JSONResponse:
    try:
        return store.public_probe_series(
            server_index=server,
            metric=metric,
            range_name=range_name,
            target=target,
            all_targets=all_targets,
        )
    except ProbeNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"success": False, "license_required": False},
        )


@router.websocket("/probe-ws")
async def public_probe_websocket(websocket: WebSocket) -> None:
    streams: PublicProbeStreamManager = websocket.app.state.public_probe_streams
    client_ip = _probe_client_ip(websocket)

    if not streams.try_connect(client_ip):
        await websocket.accept()
        await websocket.send_json(
            {
                "success": False,
                "error": "too many public probe connections",
                "license_required": False,
            }
        )
        await websocket.close(code=1013)
        return

    await websocket.accept()
    store: InventoryStore = websocket.app.state.inventory
    reader = asyncio.create_task(_discard_probe_messages(websocket))
    writer = asyncio.create_task(_send_probe_snapshots(websocket, store, streams))

    try:
        done, pending = await asyncio.wait(
            {reader, writer},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                task.result()
    finally:
        for task in {reader, writer}:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        streams.disconnect(client_ip)
        with suppress(RuntimeError):
            await websocket.close()


async def _send_probe_snapshots(
    websocket: WebSocket,
    store: InventoryStore,
    streams: PublicProbeStreamManager,
) -> None:
    while True:
        payload = await run_in_threadpool(store.public_probe_payload)
        await websocket.send_json(payload.model_dump(mode="json", exclude_none=True))
        interval_sec = max(1, min(payload.refresh_interval_sec, 60))
        await asyncio.sleep(interval_sec or streams.broadcast_interval_sec)


async def _discard_probe_messages(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


def _probe_client_ip(websocket: WebSocket) -> str:
    if websocket.client:
        return websocket.client.host
    return "unknown"
