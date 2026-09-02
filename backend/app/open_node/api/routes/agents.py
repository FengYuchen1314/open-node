import re
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import ValidationError

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute, agent_backup_operation
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.inventory import (
    AgentCommandLeaseRequest,
    AgentCommandLeaseResponse,
    AgentCommandResultRequest,
    AgentCommandResultResponse,
    AgentCommandStreamDataRequest,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentRead,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    AgentScanResultReport,
    AgentTelemetryReport,
    AgentTelemetryResponse,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.backup_coordination import BackupCoordinationError
from open_node.services.backup_runtime import run_in_backup_threadpool
from open_node.services.inventory import (
    CommandNotFoundError,
    CommandNotReadyError,
    InvalidAgentTokenError,
    InventoryStore,
    ServerNotFoundError,
)
from open_node.services.secure_channel import AgentSocket, ChannelError

router = APIRouter(route_class=BackupAPIRoute, prefix="/agents", tags=["agents"])


def _public_ipv4(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = ip_address(value.strip())
    except ValueError:
        return None
    return str(address) if address.version == 4 and address.is_global else None


def _request_public_ipv4(client_host: str | None, forwarded_for: str | None) -> str | None:
    """Use the socket peer, or one Caddy-owned forwarding chain, for IPv4 discovery."""
    direct = _public_ipv4(client_host)
    if direct:
        return direct
    try:
        peer = ip_address((client_host or "").strip())
    except ValueError:
        return None
    if not (peer.is_private or peer.is_loopback or peer.is_link_local):
        return None
    for candidate in reversed((forwarded_for or "").split(",")):
        if detected := _public_ipv4(candidate):
            return detected
    return None


@router.get("/identity", dependencies=[Depends(require_administrator)])
def agent_identity(request: Request) -> dict:
    identity = request.app.state.agent_identity
    return (
        identity.public_metadata()
        if identity
        else {
            "enabled": False,
            "protocol": "securechan-v1",
            "public_key": None,
            "fingerprint": None,
            "license_required": False,
        }
    )


@router.get("", response_model=list[AgentRead], dependencies=[Depends(require_administrator)])
def list_agents(store: Annotated[InventoryStore, Depends(get_inventory_store)]) -> list[AgentRead]:
    return store.list_agents()


@router.post(
    "/register",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    payload: AgentRegistrationRequest,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentRegistrationResponse:
    detected = _request_public_ipv4(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
    )
    if detected and not payload.public_ipv4:
        payload = payload.model_copy(update={"public_ipv4": detected})
    try:
        agent, server = await run_in_backup_threadpool(store.register_agent, payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await connections.dispatch_pending_commands(store, server.id)
    return AgentRegistrationResponse(agent=agent, server=server)


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
def record_agent_heartbeat(
    payload: AgentHeartbeatRequest,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentHeartbeatResponse:
    detected = _request_public_ipv4(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
    )
    if detected and not payload.public_ipv4:
        payload = payload.model_copy(update={"public_ipv4": detected})
    try:
        server = store.record_heartbeat(payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AgentHeartbeatResponse(server=server)


@router.post("/traffic", response_model=AgentTelemetryResponse)
@router.post("/telemetry", response_model=AgentTelemetryResponse)
def record_agent_telemetry(
    payload: AgentTelemetryReport,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentTelemetryResponse:
    try:
        server, telemetry = store.record_telemetry(payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AgentTelemetryResponse(server=server, telemetry=telemetry)


@router.post("/commands/lease", response_model=AgentCommandLeaseResponse)
def lease_agent_commands(
    payload: AgentCommandLeaseRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentCommandLeaseResponse:
    try:
        server, commands = store.lease_commands(payload.token, payload.max_commands)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AgentCommandLeaseResponse(server=server, commands=commands)


@router.post("/scan")
def record_agent_scan(
    payload: AgentScanResultReport,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> dict:
    try:
        server, scan = store.record_scan_result(payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"server_id": server.id, "reported_at": scan.reported_at, "license_required": False}


@router.post("/commands/{command_id}/result", response_model=AgentCommandResultResponse)
async def complete_agent_command(
    command_id: UUID,
    payload: AgentCommandResultRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandResultResponse:
    try:
        command = await run_in_backup_threadpool(store.complete_command, command_id, payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except CommandNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CommandNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await connections.dispatch_ready_commands(store)
    return AgentCommandResultResponse(command=command)


@router.post("/commands/by-request/{request_id}/result", response_model=AgentCommandResultResponse)
async def complete_agent_command_by_request(
    request_id: Annotated[str, Path(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")],
    payload: AgentCommandResultRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandResultResponse:
    try:
        command = await run_in_backup_threadpool(
            store.complete_command_by_request_id, request_id, payload
        )
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except CommandNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CommandNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await connections.dispatch_ready_commands(store)
    return AgentCommandResultResponse(command=command)


@router.websocket("/ws")
async def agent_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    store: InventoryStore = websocket.app.state.inventory
    connections: AgentConnectionManager = websocket.app.state.agent_connections
    barrier = getattr(websocket.app.state, "backup_writes", None)
    channel = AgentSocket(websocket)
    server_id: UUID | None = None
    token = ""
    detected_ipv4 = _request_public_ipv4(
        websocket.client.host if websocket.client else None,
        websocket.headers.get("x-forwarded-for"),
    )

    try:
        identity = websocket.app.state.agent_identity
        auth_message = await channel.authenticate_message(
            identity,
            require_encryption=identity is not None and websocket.url.path == "/api/remote/ws",
        )
        auth_payload = _message_payload(auth_message)
        if not isinstance(auth_message, dict) or auth_message.get("type") != "auth":
            await _send_auth_result(channel, False, "first message must be auth")
            await channel.close(code=1008)
            return

        token = str(auth_payload.get("token") or "")
        async with agent_backup_operation(barrier):
            try:
                if auth_payload.get("probe") is True:
                    server = store.authenticate_agent(token)
                    await _send_auth_result(channel, True, "authenticated", server.id)
                    await channel.close(code=1000)
                    return
                agent, server = store.register_agent(
                    _registration_from_ws_payload(
                        auth_payload,
                        legacy_transport=websocket.url.path == "/api/remote/ws",
                        detected_ipv4=detected_ipv4,
                    )
                )
            except (InvalidAgentTokenError, ValidationError) as exc:
                await _send_auth_result(channel, False, str(exc))
                await channel.close(code=1008)
                return

            server_id = server.id
            await _send_auth_result(channel, True, "authenticated", server.id)
            connections.register(server.id, channel, agent.capabilities)
            await connections.dispatch_pending_commands(store, server.id)

        while True:
            message = await channel.receive_json()
            async with agent_backup_operation(barrier):
                store.authenticate_agent(token)
                await _handle_agent_ws_message(
                    channel, store, token, message, detected_ipv4
                )
                if isinstance(message, dict) and message.get("type") == "rpc_reply":
                    await connections.dispatch_ready_commands(store)
                else:
                    await connections.dispatch_pending_commands(store, server.id)
    except WebSocketDisconnect:
        pass
    except BackupCoordinationError:
        try:
            await channel.close(code=1013)
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
    except (ChannelError, TimeoutError, UnicodeError, InvalidAgentTokenError, ServerNotFoundError):
        await channel.close(code=1008)
    finally:
        if server_id:
            connections.unregister(server_id, channel)


def _message_payload(message: object) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    payload = message.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _registration_from_ws_payload(
    payload: dict[str, Any], *, legacy_transport: bool = False,
    detected_ipv4: str | None = None,
) -> AgentRegistrationRequest:
    token = str(payload.get("token") or "")
    agent_version = payload.get("agent_version")
    raw_capabilities = payload.get("capabilities") or {}
    capabilities = (
        dict(raw_capabilities) if isinstance(raw_capabilities, dict) else raw_capabilities
    )
    if (
        isinstance(capabilities, dict)
        and legacy_transport
        and _legacy_agent_settings_supported(agent_version)
    ):
        for capability in (
            "agent_switch_xray_mode",
            "agent_switch_listen_port",
            "agent_probe_master_url",
            "agent_update_master_url",
        ):
            capabilities.setdefault(capability, True)
    return AgentRegistrationRequest(
        token=token,
        hostname=str(payload.get("hostname") or "websocket-agent"),
        agent_version=agent_version,
        connection_mode=payload.get("connection_mode") or "websocket",
        listen_port=(23889 if payload.get("listen_port") is None else payload.get("listen_port")),
        public_ipv4=payload.get("public_ipv4") or detected_ipv4,
        public_ipv6=payload.get("public_ipv6"),
        xray_mode=payload.get("xray_mode") or "external",
        capabilities=capabilities,
        warp_installed=bool(payload.get("warp_installed", False)),
        same_host_as_master=payload.get("same_host_as_master"),
    )


def _legacy_agent_settings_supported(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value.strip())
    return bool(match and tuple(map(int, match.groups())) >= (0, 4, 7))


async def _handle_agent_ws_message(
    websocket: AgentSocket,
    store: InventoryStore,
    token: str,
    message: object,
    detected_ipv4: str | None = None,
) -> None:
    if not isinstance(message, dict):
        await _send_ws_error(websocket, "message must be an object")
        return

    message_type = message.get("type")
    payload = _message_payload(message)

    if message_type == "ping":
        await websocket.send_json({"type": "pong", "payload": {"server_time": _server_time()}})
        return

    if message_type == "heartbeat":
        try:
            heartbeat = AgentHeartbeatRequest(
                token=token,
                warp_installed=payload.get("warp_installed"),
                listen_port=payload.get("listen_port"),
                public_ipv4=payload.get("public_ipv4") or detected_ipv4,
                public_ipv6=payload.get("public_ipv6"),
            )
            store.record_heartbeat(heartbeat)
        except (InvalidAgentTokenError, ValidationError) as exc:
            await _send_ws_error(websocket, str(exc))
            return
        await websocket.send_json(
            {"type": "heartbeat_ack", "payload": {"server_time": _server_time()}}
        )
        return

    if message_type in {"traffic", "telemetry"}:
        try:
            report = AgentTelemetryReport.model_validate({**payload, "token": token})
            server, telemetry = store.record_telemetry(report)
        except (InvalidAgentTokenError, ValidationError) as exc:
            await _send_ws_error(websocket, str(exc))
            return
        await websocket.send_json(
            {
                "type": "telemetry_ack",
                "payload": {
                    "server_id": str(server.id),
                    "telemetry_id": str(telemetry.id),
                    "server_time": _server_time(),
                },
            }
        )
        return

    if message_type == "scan_result":
        try:
            report = AgentScanResultReport.model_validate({**payload, "token": token})
            server, scan = store.record_scan_result(report)
        except (InvalidAgentTokenError, ValidationError) as exc:
            await _send_ws_error(websocket, str(exc))
            return
        await websocket.send_json(
            {
                "type": "scan_result_ack",
                "payload": {
                    "server_id": str(server.id),
                    "reported_at": scan.reported_at.isoformat(),
                    "server_time": _server_time(),
                    "license_required": False,
                },
            }
        )
        return

    if message_type == "rpc_reply":
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            await _send_ws_error(websocket, "request_id required")
            return
        try:
            command = store.complete_command_by_request_id(
                request_id,
                AgentCommandResultRequest(
                    token=token,
                    status=int(payload.get("status") or 500),
                    body=payload.get("body"),
                    error=payload.get("error"),
                ),
            )
        except (CommandNotFoundError, ValidationError, ValueError) as exc:
            await _send_ws_error(websocket, str(exc))
            return
        await websocket.send_json(
            {
                "type": "rpc_reply_ack",
                "payload": {"request_id": command.request_id, "status": command.status},
            }
        )
        return

    if message_type == "rpc_stream_data":
        try:
            store.append_command_stream_frame(
                AgentCommandStreamDataRequest.model_validate({**payload, "token": token})
            )
        except (CommandNotFoundError, InvalidAgentTokenError, ValidationError) as exc:
            await _send_ws_error(websocket, str(exc))
        return

    await _send_ws_error(websocket, f"unsupported message type: {message_type}")


async def _send_auth_result(
    websocket: AgentSocket,
    success: bool,
    message: str,
    server_id: UUID | None = None,
) -> None:
    payload = {
        "success": success,
        "message": message,
        "server_time": _server_time(),
        "license_required": False,
    }
    if server_id:
        payload["server_id"] = str(server_id)
    await websocket.send_json({"type": "auth_result", "payload": payload})


async def _send_ws_error(websocket: AgentSocket, message: str) -> None:
    await websocket.send_json(
        {
            "type": "error",
            "payload": {
                "message": message,
                "server_time": _server_time(),
                "license_required": False,
            },
        }
    )


def _server_time() -> int:
    return int(datetime.now(tz=UTC).timestamp())
