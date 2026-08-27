from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

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
from open_node.services.inventory import (
    CommandNotFoundError,
    InvalidAgentTokenError,
    InventoryStore,
)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentRead])
def list_agents(store: Annotated[InventoryStore, Depends(get_inventory_store)]) -> list[AgentRead]:
    return store.list_agents()


@router.post(
    "/register",
    response_model=AgentRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_agent(
    payload: AgentRegistrationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentRegistrationResponse:
    try:
        agent, server = await run_in_threadpool(store.register_agent, payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    await connections.dispatch_pending_commands(store, server.id)
    return AgentRegistrationResponse(agent=agent, server=server)


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
def record_agent_heartbeat(
    payload: AgentHeartbeatRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentHeartbeatResponse:
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


@router.post("/commands/{command_id}/result", response_model=AgentCommandResultResponse)
async def complete_agent_command(
    command_id: UUID,
    payload: AgentCommandResultRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentCommandResultResponse:
    try:
        command = await run_in_threadpool(store.complete_command, command_id, payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except CommandNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await connections.dispatch_pending_commands(store, command.server_id)
    return AgentCommandResultResponse(command=command)


@router.websocket("/ws")
async def agent_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    store: InventoryStore = websocket.app.state.inventory
    connections: AgentConnectionManager = websocket.app.state.agent_connections
    server_id: UUID | None = None
    token = ""

    try:
        auth_message = await websocket.receive_json()
        auth_payload = _message_payload(auth_message)
        if not isinstance(auth_message, dict) or auth_message.get("type") != "auth":
            await _send_auth_result(websocket, False, "first message must be auth")
            await websocket.close(code=1008)
            return

        token = str(auth_payload.get("token") or "")
        try:
            if auth_payload.get("probe") is True:
                server = store.authenticate_agent(token)
                await _send_auth_result(websocket, True, "authenticated", server.id)
                await websocket.close(code=1000)
                return
            agent, server = store.register_agent(_registration_from_ws_payload(auth_payload))
        except (InvalidAgentTokenError, ValidationError) as exc:
            await _send_auth_result(websocket, False, str(exc))
            await websocket.close(code=1008)
            return

        server_id = server.id
        await _send_auth_result(websocket, True, "authenticated", server.id)
        connections.register(server.id, websocket, agent.capabilities)
        await connections.dispatch_pending_commands(store, server.id)

        while True:
            message = await websocket.receive_json()
            await _handle_agent_ws_message(websocket, store, token, message)
            await connections.dispatch_pending_commands(store, server.id)
    except WebSocketDisconnect:
        pass
    finally:
        if server_id:
            connections.unregister(server_id, websocket)


def _message_payload(message: object) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {}
    payload = message.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _registration_from_ws_payload(payload: dict[str, Any]) -> AgentRegistrationRequest:
    token = str(payload.get("token") or "")
    return AgentRegistrationRequest(
        token=token,
        hostname=str(payload.get("hostname") or "websocket-agent"),
        agent_version=payload.get("agent_version"),
        connection_mode=payload.get("connection_mode") or "websocket",
        listen_port=payload.get("listen_port") or 23889,
        public_ipv4=payload.get("public_ipv4"),
        public_ipv6=payload.get("public_ipv6"),
        xray_mode=payload.get("xray_mode") or "external",
        capabilities=payload.get("capabilities") or {},
        warp_installed=bool(payload.get("warp_installed", False)),
        same_host_as_master=payload.get("same_host_as_master"),
    )


async def _handle_agent_ws_message(
    websocket: WebSocket,
    store: InventoryStore,
    token: str,
    message: object,
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
                listen_port=payload.get("listen_port"),
                public_ipv4=payload.get("public_ipv4"),
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
    websocket: WebSocket,
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


async def _send_ws_error(websocket: WebSocket, message: str) -> None:
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
