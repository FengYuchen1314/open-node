from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.dependencies import get_inventory_store
from open_node.domain.inventory import (
    AgentCommandLeaseRequest,
    AgentCommandLeaseResponse,
    AgentCommandResultRequest,
    AgentCommandResultResponse,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentRead,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    AgentTelemetryReport,
    AgentTelemetryResponse,
)
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
def register_agent(
    payload: AgentRegistrationRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentRegistrationResponse:
    try:
        agent, server = store.register_agent(payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
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
def complete_agent_command(
    command_id: UUID,
    payload: AgentCommandResultRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentCommandResultResponse:
    try:
        command = store.complete_command(command_id, payload)
    except InvalidAgentTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except CommandNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AgentCommandResultResponse(command=command)
