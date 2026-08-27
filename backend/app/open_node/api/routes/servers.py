from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.inventory import (
    AgentCommandCreate,
    AgentCommandCreateResponse,
    AgentCommandStreamFramesResponse,
    AgentDomainLatencyProbeRequest,
    ServerCommandsResponse,
    ServerCreate,
    ServerCreateResponse,
    ServerRead,
    ServerTelemetryResponse,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    CommandNotFoundError,
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


@router.get("/{server_id}/commands", response_model=ServerCommandsResponse)
def list_server_commands(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ServerCommandsResponse:
    try:
        commands = store.list_commands(server_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ServerCommandsResponse(server_id=server_id, commands=commands)


@router.get(
    "/{server_id}/commands/{command_id}/stream",
    response_model=AgentCommandStreamFramesResponse,
)
def list_server_command_stream_frames(
    server_id: UUID,
    command_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentCommandStreamFramesResponse:
    try:
        frames = store.list_command_stream_frames(server_id, command_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CommandNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AgentCommandStreamFramesResponse(
        server_id=server_id,
        command_id=command_id,
        frames=frames,
    )


@router.post(
    "/{server_id}/commands",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_server_command(
    server_id: UUID,
    payload: AgentCommandCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(server_id, payload, store, connections)


@router.post(
    "/{server_id}/operations/system-info",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_system_info_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/system/info"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/traffic",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_traffic_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/traffic"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/speed",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_speed_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/speed"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/domain-latency",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_domain_latency_operation(
    server_id: UUID,
    payload: AgentDomainLatencyProbeRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/domains/latency",
            body=payload.model_dump(
                mode="json",
                include={"domains", "timeout_ms", "allow_icmp"},
            ),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


async def _queue_server_command(
    server_id: UUID,
    payload: AgentCommandCreate,
    store: InventoryStore,
    connections: AgentConnectionManager,
) -> AgentCommandCreateResponse:
    try:
        command = store.create_command(server_id, payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    command = await connections.dispatch_command(store, command)
    return AgentCommandCreateResponse(command=command)
