from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.api.redaction import public_change_set, public_command_read
from open_node.domain.changes import (
    AgentChangeSetAcceptRequest,
    AgentChangeSetCreate,
    AgentChangeSetResponse,
    AgentChangeSetRollbackRequest,
    AgentChangeSetsResponse,
    AgentRoutedOutboundChangeSetCreate,
)
from open_node.domain.inventory import AgentCommandRead
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.change_sets import ChangeSetConflict
from open_node.services.inventory import (
    ChangeSetNotFoundError,
    InventoryStore,
    ServerNotFoundError,
)

router = APIRouter(route_class=BackupAPIRoute, prefix="/change-sets", tags=["change-sets"])


@router.get("", response_model=AgentChangeSetsResponse)
def list_change_sets(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentChangeSetsResponse:
    return AgentChangeSetsResponse(
        change_sets=[public_change_set(change) for change in store.list_change_sets()]
    )


@router.post(
    "",
    response_model=AgentChangeSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_change_set(
    payload: AgentChangeSetCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentChangeSetResponse:
    _reject_internal_only_steps(payload)
    try:
        change_set = store.create_change_set(payload)
        commands: list[AgentCommandRead] = []
        if payload.dispatch:
            change_set, commands = store.dispatch_change_set(change_set.id)
            commands = await _dispatch_commands(commands, store, connections)
            change_set = store.get_change_set(change_set.id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChangeSetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentChangeSetResponse(
        change_set=public_change_set(change_set),
        commands=[public_command_read(command) for command in commands],
    )


@router.post(
    "/routed-outbound",
    response_model=AgentChangeSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_routed_outbound_change_set(
    payload: AgentRoutedOutboundChangeSetCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentChangeSetResponse:
    try:
        change_set_payload = store.build_routed_outbound_change_set(payload)
        change_set = store.create_change_set(change_set_payload)
        commands: list[AgentCommandRead] = []
        if payload.dispatch:
            change_set, commands = store.dispatch_change_set(change_set.id)
            commands = await _dispatch_commands(commands, store, connections)
            change_set = store.get_change_set(change_set.id)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409
            if isinstance(exc, ChangeSetConflict)
            else status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return AgentChangeSetResponse(
        change_set=public_change_set(change_set),
        commands=[public_command_read(command) for command in commands],
    )


@router.get("/{change_set_id}", response_model=AgentChangeSetResponse)
def get_change_set(
    change_set_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> AgentChangeSetResponse:
    try:
        change_set = store.get_change_set(change_set_id)
    except ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AgentChangeSetResponse(change_set=public_change_set(change_set))


def _reject_internal_only_steps(payload: AgentChangeSetCreate) -> None:
    details = {
        "/api/child/egress/apply": (
            "Managed egress apply is only available through the previewed "
            "server egress workflow"
        ),
        "/api/child/node-cleanup": (
            "Node cleanup is only available through the dedicated node management workflow"
        ),
        "/api/child/subscription-access": (
            "Subscription access is only available through the dedicated user and plan workflows"
        ),
        "/api/child/outbound-tls-pin/probe": (
            "TLS certificate probing is only available through the validated outbound workflow"
        ),
    }
    for step in payload.steps:
        for command in (step.forward, step.rollback):
            if command is not None and (detail := details.get(command.path)):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=detail,
                )


@router.post("/{change_set_id}/dispatch", response_model=AgentChangeSetResponse)
async def dispatch_change_set(
    change_set_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentChangeSetResponse:
    try:
        change_set, commands = store.dispatch_change_set(change_set_id)
        commands = await _dispatch_commands(commands, store, connections)
        change_set = store.get_change_set(change_set_id)
    except ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChangeSetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentChangeSetResponse(
        change_set=public_change_set(change_set),
        commands=[public_command_read(command) for command in commands],
    )


@router.post("/{change_set_id}/rollback", response_model=AgentChangeSetResponse)
async def rollback_change_set(
    change_set_id: UUID,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
    payload: AgentChangeSetRollbackRequest | None = None,
) -> AgentChangeSetResponse:
    try:
        change_set, commands, warnings = store.rollback_change_set(
            change_set_id,
            payload or AgentChangeSetRollbackRequest(),
        )
        commands = await _dispatch_commands(commands, store, connections)
        change_set = store.get_change_set(change_set_id)
    except ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChangeSetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentChangeSetResponse(
        change_set=public_change_set(change_set),
        commands=[public_command_read(command) for command in commands],
        warnings=warnings,
    )


@router.post("/{change_set_id}/accept", response_model=AgentChangeSetResponse)
async def accept_change_set(
    change_set_id: UUID,
    payload: AgentChangeSetAcceptRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> AgentChangeSetResponse:
    try:
        change_set = store.accept_change_set(change_set_id, payload)
    except ChangeSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChangeSetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await connections.dispatch_ready_commands(store)
    return AgentChangeSetResponse(change_set=public_change_set(change_set))


async def _dispatch_commands(
    commands: list[AgentCommandRead],
    store: InventoryStore,
    connections: AgentConnectionManager,
) -> list[AgentCommandRead]:
    dispatched: list[AgentCommandRead] = []
    for command in commands:
        dispatched.append(await connections.dispatch_command(store, command))
    await connections.dispatch_ready_commands(store)
    return dispatched
