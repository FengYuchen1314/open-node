import json
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.inventory import (
    AgentCommandCreate,
    AgentCommandCreateResponse,
    AgentCommandStreamFramesResponse,
    AgentDomainLatencyProbeRequest,
    AgentLogsOperationRequest,
    AgentNginxConfigFileReadOperationRequest,
    AgentNginxConfigFileWriteOperationRequest,
    AgentNginxConfigOperationRequest,
    AgentNginxInstallOperationRequest,
    AgentProbeMasterURLOperationRequest,
    AgentServiceControlOperationRequest,
    AgentSwitchListenPortOperationRequest,
    AgentSwitchXrayModeOperationRequest,
    AgentUpdateMasterURLOperationRequest,
    AgentWarpLicenseOperationRequest,
    AgentXrayConfigFileReadOperationRequest,
    AgentXrayConfigFileWriteOperationRequest,
    AgentXrayConfigOperationRequest,
    AgentXraySystemConfigOperationRequest,
    AgentXrayTestConfigOperationRequest,
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


@router.post(
    "/{server_id}/operations/services/status",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_services_status_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/services/status"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/services/control",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_service_control_operation(
    server_id: UUID,
    payload: AgentServiceControlOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/services/control",
            body=payload.model_dump(mode="json"),
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/system/nics",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_system_nics_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/system/nics"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/logs",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_logs_operation(
    server_id: UUID,
    payload: AgentLogsOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="GET",
            path="/api/child/logs",
            query=_query_from_params(
                {
                    "service": payload.service.value,
                    "lines": str(payload.lines),
                }
            ),
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/scan",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_scan_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="POST", path="/api/child/scan"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/test-config",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_test_config_operation(
    server_id: UUID,
    payload: AgentXrayTestConfigOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    config = payload.config
    if not isinstance(config, str):
        config = _json_dumps(config)
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/test-config",
            body={"config": config},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/config/read",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_config_read_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/xray/config"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/config/write",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_config_write_operation(
    server_id: UUID,
    payload: AgentXrayConfigOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    body: dict[str, object] = {"config": _config_to_text(payload.config)}
    if payload.path:
        body["path"] = payload.path
    if payload.force:
        body["force"] = payload.force
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/config",
            body=body,
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/system-config/read",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_system_config_read_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/xray/system-config"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/system-config/write",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_system_config_write_operation(
    server_id: UUID,
    payload: AgentXraySystemConfigOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/system-config",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/config-files/list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_config_files_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/xray/config-files"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/config-files/read",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_config_file_read_operation(
    server_id: UUID,
    payload: AgentXrayConfigFileReadOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="GET",
            path="/api/child/xray/config-files",
            query=_query_from_params({"file": payload.file}),
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/config-files/write",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_config_file_write_operation(
    server_id: UUID,
    payload: AgentXrayConfigFileWriteOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/config-files",
            body={"file": payload.file, "content": _config_to_text(payload.content)},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/install",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_install_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_maintenance_command(
        server_id,
        "/api/child/xray/install-stream",
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/xray/remove",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_remove_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_maintenance_command(
        server_id,
        "/api/child/xray/remove-stream",
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/install",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_install_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
    payload: AgentNginxInstallOperationRequest | None = None,
) -> AgentCommandCreateResponse:
    request = payload or AgentNginxInstallOperationRequest()
    return await _queue_maintenance_command(
        server_id,
        "/api/child/nginx/install-stream",
        store,
        connections,
        query=_query_from_params({"domain": request.domain}),
        timeout_ms=request.command_timeout_ms,
    )


@router.post(
    "/{server_id}/operations/nginx/remove",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_remove_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_maintenance_command(
        server_id,
        "/api/child/nginx/remove-stream",
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/config/read",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_config_read_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/nginx/config"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/config/write",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_config_write_operation(
    server_id: UUID,
    payload: AgentNginxConfigOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    body: dict[str, object] = {"config": payload.config}
    if payload.path:
        body["path"] = payload.path
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/nginx/config",
            body=body,
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/config-files/list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_config_files_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/nginx/config-files"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/config-files/read",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_config_file_read_operation(
    server_id: UUID,
    payload: AgentNginxConfigFileReadOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="GET",
            path="/api/child/nginx/config-files",
            query=_query_from_params({"file": payload.file}),
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/config-files/write",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_config_file_write_operation(
    server_id: UUID,
    payload: AgentNginxConfigFileWriteOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/nginx/config-files",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/agent/upgrade",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_agent_upgrade_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_maintenance_command(
        server_id,
        "/api/child/agent/upgrade-stream",
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/agent/uninstall",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_agent_uninstall_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_maintenance_command(
        server_id,
        "/api/child/agent/uninstall-stream",
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/warp/install",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_warp_install_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="POST", path="/api/child/warp/install", timeout_ms=60_000),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/warp/status",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_warp_status_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/warp/status"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/warp/license",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_warp_license_operation(
    server_id: UUID,
    payload: AgentWarpLicenseOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/warp/license",
            body={"license": payload.license},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/warp/remove",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_warp_remove_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="POST", path="/api/child/warp/remove", timeout_ms=60_000),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/agent/switch-xray-mode",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_agent_switch_xray_mode_operation(
    server_id: UUID,
    payload: AgentSwitchXrayModeOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/agent/switch-xray-mode",
            body={"xray_mode": payload.xray_mode.value},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/agent/switch-listen-port",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_agent_switch_listen_port_operation(
    server_id: UUID,
    payload: AgentSwitchListenPortOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/agent/switch-listen-port",
            body={"listen_port": payload.listen_port},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/agent/probe-master-url",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_agent_probe_master_url_operation(
    server_id: UUID,
    payload: AgentProbeMasterURLOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/agent/probe-master-url",
            body={"master_url": payload.master_url},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/agent/update-master-url",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_agent_update_master_url_operation(
    server_id: UUID,
    payload: AgentUpdateMasterURLOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/agent/update-master-url",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
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


async def _queue_maintenance_command(
    server_id: UUID,
    path: str,
    store: InventoryStore,
    connections: AgentConnectionManager,
    query: str = "",
    timeout_ms: int = 300_000,
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path=path,
            query=query,
            timeout_ms=timeout_ms,
            stream=True,
        ),
        store,
        connections,
    )


def _query_from_params(params: dict[str, str | None]) -> str:
    return urlencode({key: value for key, value in params.items() if value})


def _config_to_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return _json_dumps(value)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
