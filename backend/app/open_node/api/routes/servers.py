import json
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.inventory import (
    AgentBatchApplyOperationRequest,
    AgentCertDeployOperationRequest,
    AgentCommandCreate,
    AgentCommandCreateResponse,
    AgentCommandStreamFramesResponse,
    AgentDomainLatencyProbeRequest,
    AgentInboundsManageOperationRequest,
    AgentLimiterOperationRequest,
    AgentLogFilesDeleteOperationRequest,
    AgentLogsOperationRequest,
    AgentNginxClearStreamPortOperationRequest,
    AgentNginxConfigFileReadOperationRequest,
    AgentNginxConfigFileWriteOperationRequest,
    AgentNginxConfigOperationRequest,
    AgentNginxInstallOperationRequest,
    AgentNginxSetupSSLOperationRequest,
    AgentNginxWebsiteDeleteOperationRequest,
    AgentOutboundsManageOperationRequest,
    AgentProbeMasterURLOperationRequest,
    AgentReturnRouteTestOperationRequest,
    AgentRoutingManageOperationRequest,
    AgentServiceControlOperationRequest,
    AgentSwitchListenPortOperationRequest,
    AgentSwitchXrayModeOperationRequest,
    AgentUpdateMasterURLOperationRequest,
    AgentValidateSiteOperationRequest,
    AgentWarpLicenseOperationRequest,
    AgentXrayConfigFileReadOperationRequest,
    AgentXrayConfigFileWriteOperationRequest,
    AgentXrayConfigOperationRequest,
    AgentXraySystemConfigOperationRequest,
    AgentXrayTakeoverExternalOperationRequest,
    AgentXrayTestConfigOperationRequest,
    ServerCommandsResponse,
    ServerCreate,
    ServerCreateResponse,
    ServerProbeMetadataUpdate,
    ServerRead,
    ServerResponse,
    ServerScanResultResponse,
    ServerTelemetryResponse,
    ServerXrayConfigSnapshotsResponse,
    XrayRuntimeInventoryResponse,
)
from open_node.domain.subscriptions import (
    ManagedNodeResponse,
    XrayRuntimeNodeCreateRequest,
    XrayRuntimeNodeDraftsResponse,
    XrayRuntimeNodeImportRequest,
    XrayRuntimeNodeImportResponse,
    XrayRuntimeNodeReconciliationResponse,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    CommandNotFoundError,
    DuplicateServerNameError,
    InventoryStore,
    ServerNotFoundError,
    XrayConfigSnapshotNotFoundError,
    XrayRuntimeInboundNotFoundError,
    XrayRuntimeNodeDraftUnavailableError,
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


@router.patch("/{server_id}/probe-metadata", response_model=ServerResponse)
def update_server_probe_metadata(
    server_id: UUID,
    payload: ServerProbeMetadataUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ServerResponse:
    try:
        server = store.update_server_probe_metadata(server_id, payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ServerResponse(server=server)


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


@router.get("/{server_id}/scan/latest", response_model=ServerScanResultResponse)
def latest_server_scan(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ServerScanResultResponse:
    try:
        scan = store.latest_scan_result(server_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ServerScanResultResponse(server_id=server_id, scan=scan)


@router.get("/{server_id}/xray/runtime", response_model=XrayRuntimeInventoryResponse)
def xray_runtime_inventory(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> XrayRuntimeInventoryResponse:
    try:
        return store.xray_runtime_inventory(server_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{server_id}/xray/runtime/node-drafts", response_model=XrayRuntimeNodeDraftsResponse)
def list_xray_runtime_node_drafts(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    host: Annotated[str | None, Query(max_length=255)] = None,
) -> XrayRuntimeNodeDraftsResponse:
    try:
        return store.list_xray_runtime_node_drafts(server_id, host=host)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{server_id}/xray/runtime/nodes",
    response_model=ManagedNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_managed_node_from_xray_runtime(
    server_id: UUID,
    payload: XrayRuntimeNodeCreateRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ManagedNodeResponse:
    try:
        node = store.create_managed_node_from_xray_runtime(server_id, payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except XrayRuntimeInboundNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except XrayRuntimeNodeDraftUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ManagedNodeResponse(node=node)


@router.post(
    "/{server_id}/xray/runtime/nodes/import",
    response_model=XrayRuntimeNodeImportResponse,
)
def import_managed_nodes_from_xray_runtime(
    server_id: UUID,
    payload: XrayRuntimeNodeImportRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> XrayRuntimeNodeImportResponse:
    try:
        return store.import_managed_nodes_from_xray_runtime(server_id, payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{server_id}/xray/runtime/nodes/reconciliation",
    response_model=XrayRuntimeNodeReconciliationResponse,
)
def xray_runtime_node_reconciliation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> XrayRuntimeNodeReconciliationResponse:
    try:
        return store.xray_runtime_node_reconciliation(server_id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{server_id}/xray/config-snapshots", response_model=ServerXrayConfigSnapshotsResponse)
def list_xray_config_snapshots(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    limit: Annotated[int, Query(ge=0, le=100)] = 20,
    with_config: bool = False,
) -> ServerXrayConfigSnapshotsResponse:
    try:
        snapshots = store.list_xray_config_snapshots(
            server_id,
            limit=limit,
            include_config=with_config,
        )
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ServerXrayConfigSnapshotsResponse(server_id=server_id, snapshots=snapshots)


@router.post(
    "/{server_id}/xray/config-snapshots/{snapshot_id}/restore",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def restore_xray_config_snapshot(
    server_id: UUID,
    snapshot_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    try:
        snapshot = store.get_xray_config_snapshot(
            server_id,
            snapshot_id,
            include_config=True,
        )
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except XrayConfigSnapshotNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if snapshot.config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="xray config snapshot body is unavailable",
        )

    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/config",
            body={"config": snapshot.config},
            timeout_ms=60_000,
        ),
        store,
        connections,
    )


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
    "/{server_id}/operations/inbounds/list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_inbounds_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/inbounds"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/inbounds/manage",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_inbounds_manage_operation(
    server_id: UUID,
    payload: AgentInboundsManageOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/inbounds",
            body=_compact_body(
                {
                    "action": payload.action,
                    "inbound": payload.inbound,
                    "tag": payload.tag,
                    "client": payload.client,
                    "domains": payload.domains,
                }
            ),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/outbounds/list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_outbounds_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/outbounds"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/outbounds/manage",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_outbounds_manage_operation(
    server_id: UUID,
    payload: AgentOutboundsManageOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/outbounds",
            body=_compact_body(
                {
                    "action": payload.action,
                    "outbound": payload.outbound,
                    "tag": payload.tag,
                    "tags": payload.tags,
                }
            ),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/routing/read",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_routing_read_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/routing"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/routing/manage",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_routing_manage_operation(
    server_id: UUID,
    payload: AgentRoutingManageOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    body = _compact_body(
        {
            "action": payload.action,
            "routing": payload.routing,
            "rule": payload.rule,
            "index": payload.index,
            "marktag": payload.marktag,
            "user_email": payload.user_email,
            "no_restart": payload.no_restart,
        }
    )
    if "observatory" in payload.model_fields_set:
        body["observatory"] = payload.observatory
    if "burst_observatory" in payload.model_fields_set:
        body["burstObservatory"] = payload.burst_observatory
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/routing",
            body=body,
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/batch-apply",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_batch_apply_operation(
    server_id: UUID,
    payload: AgentBatchApplyOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/batch-apply",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/cert/deploy",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_cert_deploy_operation(
    server_id: UUID,
    payload: AgentCertDeployOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/cert/deploy",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/setup-ssl",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_setup_ssl_operation(
    server_id: UUID,
    payload: AgentNginxSetupSSLOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/nginx/setup-ssl",
            body=payload.model_dump(
                mode="json",
                exclude={"command_timeout_ms"},
                exclude_none=True,
            ),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/servers-list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_servers_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/nginx/servers-list"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/websites/list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_websites_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/nginx/websites"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/websites/delete",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_website_delete_operation(
    server_id: UUID,
    payload: AgentNginxWebsiteDeleteOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="DELETE",
            path="/api/child/nginx/websites",
            body={"domain": payload.domain},
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/network/return-route-test",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_return_route_test_operation(
    server_id: UUID,
    payload: AgentReturnRouteTestOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/network/return-route-test",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/validate-site",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_validate_site_operation(
    server_id: UUID,
    payload: AgentValidateSiteOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/validate-site",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
            timeout_ms=payload.command_timeout_ms,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/limiter",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_limiter_operation(
    server_id: UUID,
    payload: AgentLimiterOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/limiter",
            body=payload.model_dump(mode="json", exclude={"command_timeout_ms"}),
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
    "/{server_id}/operations/logs/files/list",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_log_files_list_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(method="GET", path="/api/child/logs/files"),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/logs/files/delete",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_log_files_delete_operation(
    server_id: UUID,
    payload: AgentLogFilesDeleteOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    query = _query_from_params({"all": "1"} if payload.all else {"name": payload.name})
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="DELETE",
            path="/api/child/logs/files",
            query=query,
            timeout_ms=payload.command_timeout_ms,
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
    "/{server_id}/operations/xray/takeover-external",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_takeover_external_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
    payload: AgentXrayTakeoverExternalOperationRequest | None = None,
) -> AgentCommandCreateResponse:
    request = payload or AgentXrayTakeoverExternalOperationRequest()
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/external-xray/takeover",
            timeout_ms=request.command_timeout_ms,
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
    "/{server_id}/operations/xray/install-legacy",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_install_legacy_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/install",
            timeout_ms=300_000,
        ),
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
    "/{server_id}/operations/xray/remove-legacy",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_xray_remove_legacy_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/xray/remove",
            timeout_ms=300_000,
        ),
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
    "/{server_id}/operations/nginx/install-legacy",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_install_legacy_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
    payload: AgentNginxInstallOperationRequest | None = None,
) -> AgentCommandCreateResponse:
    request = payload or AgentNginxInstallOperationRequest()
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/nginx/install",
            body=_compact_body({"domain": request.domain}),
            timeout_ms=request.command_timeout_ms,
        ),
        store,
        connections,
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
    "/{server_id}/operations/nginx/remove-legacy",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_remove_legacy_operation(
    server_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/nginx/remove",
            timeout_ms=300_000,
        ),
        store,
        connections,
    )


@router.post(
    "/{server_id}/operations/nginx/clear-stream-port",
    response_model=AgentCommandCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def queue_nginx_clear_stream_port_operation(
    server_id: UUID,
    payload: AgentNginxClearStreamPortOperationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandCreateResponse:
    return await _queue_server_command(
        server_id,
        AgentCommandCreate(
            method="POST",
            path="/api/child/nginx/clear-stream-port",
            body={"port": payload.port},
            timeout_ms=payload.command_timeout_ms,
        ),
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


def _compact_body(params: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in params.items() if value is not None and value != []}


def _config_to_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return _json_dumps(value)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
