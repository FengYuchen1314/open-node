from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.dependencies import get_inventory_store
from open_node.domain.inventory import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentRead,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
)
from open_node.services.inventory import InvalidAgentTokenError, InventoryStore

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
