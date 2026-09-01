"""Administrator sharing controls and token-authenticated federation boundary."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import ValidationError

from open_node.api.backup import BackupAPIRoute
from open_node.domain.server_sharing import (
    FederatedServerCreate,
    FederatedServerDelete,
    FederatedServerRead,
    FederatedServerRefresh,
    FederatedServersResponse,
    FederationCommandCreate,
    FederationCommandRead,
    FederationServerInfo,
    ServerShareCreate,
    ServerShareCreated,
    ServerShareRevoke,
    ServerShareRevoked,
    ServerSharesResponse,
    ServerSharingError,
)
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(
    route_class=BackupAPIRoute, prefix="/server-shares", tags=["server sharing"]
)
consumer_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/server-federation", tags=["server federation"]
)
public_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/federation", tags=["federation"]
)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid(_value):
    raise ValueError()


async def _payload(request, model):
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise ServerSharingError(415, "server_share_invalid_request")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 64 * 1024:
            raise ServerSharingError(413, "server_share_invalid_request")
        content.extend(chunk)
    try:
        return model.model_validate(json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid,
        ))
    except (ValidationError, UnicodeError, ValueError, TypeError, RecursionError):
        raise ServerSharingError(422, "server_share_invalid_request") from None


def _token(request: Request, value: str | None):
    peer = request.client.host if request.client else "unknown"
    if not request.app.state.auth.allow_login_attempt(
        "federation:" + peer, max_attempts=240
    ):
        raise ServerSharingError(429, "server_share_busy")
    if value is None:
        raise ServerSharingError(401, "server_share_token_invalid")
    return value.strip()


@router.get("", response_model=ServerSharesResponse)
def shares(request: Request, server_id: Annotated[UUID, Query()]):
    return request.app.state.server_sharing.list_shares(server_id)


@router.post("", response_model=ServerShareCreated, status_code=201)
async def create_share(request: Request):
    return await run_in_backup_threadpool(
        request.app.state.server_sharing.create_share,
        await _payload(request, ServerShareCreate),
    )


@router.post("/{identifier}/revoke", response_model=ServerShareRevoked)
async def revoke_share(identifier: UUID, request: Request):
    commands = await run_in_backup_threadpool(
        request.app.state.server_sharing.revoke,
        identifier, await _payload(request, ServerShareRevoke),
    )
    dispatched = [
        await request.app.state.agent_connections.dispatch_command(
            request.app.state.inventory, command
        ) for command in commands
    ]
    return request.app.state.server_sharing.revoked_response(dispatched)


@consumer_router.get("", response_model=FederatedServersResponse)
def imported_servers(request: Request):
    return request.app.state.server_sharing.list_federated()


@consumer_router.post("", response_model=FederatedServerRead, status_code=201)
async def import_server(request: Request):
    return await run_in_backup_threadpool(
        request.app.state.server_sharing.add_federated,
        await _payload(request, FederatedServerCreate),
    )


@consumer_router.post("/{identifier}/refresh", response_model=FederatedServerRead)
async def refresh_server(identifier: UUID, request: Request):
    payload = await _payload(request, FederatedServerRefresh)
    return await run_in_backup_threadpool(
        request.app.state.server_sharing.refresh_federated,
        identifier, payload.expected_revision,
    )


@consumer_router.post("/{identifier}/manage", response_model=FederationCommandRead)
async def manage_imported(identifier: UUID, request: Request):
    return await run_in_backup_threadpool(
        request.app.state.server_sharing.manage_federated,
        identifier, await _payload(request, FederationCommandCreate),
    )


@consumer_router.get(
    "/{identifier}/commands/{command_id}", response_model=FederationCommandRead
)
async def imported_command(identifier: UUID, command_id: UUID, request: Request):
    return await run_in_backup_threadpool(
        request.app.state.server_sharing.federated_command, identifier, command_id
    )


@consumer_router.post("/{identifier}/delete", status_code=204)
async def delete_imported(identifier: UUID, request: Request):
    payload = await _payload(request, FederatedServerDelete)
    await run_in_backup_threadpool(
        request.app.state.server_sharing.delete_federated,
        identifier, payload.expected_revision,
    )
    return Response(status_code=204)


@public_router.get("/server-info", response_model=FederationServerInfo)
def server_info(
    request: Request,
    share_token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
):
    return request.app.state.server_sharing.server_info(_token(request, share_token))


@public_router.post("/manage", response_model=FederationCommandRead, status_code=201)
async def federation_manage(
    request: Request,
    share_token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
):
    token = _token(request, share_token)
    command = await run_in_backup_threadpool(
        request.app.state.server_sharing.create_shared_command,
        token, await _payload(request, FederationCommandCreate),
    )
    await request.app.state.agent_connections.dispatch_command(
        request.app.state.inventory, command
    )
    return await run_in_backup_threadpool(
        request.app.state.server_sharing.shared_command, token, command.id
    )


@public_router.get("/commands/{command_id}", response_model=FederationCommandRead)
def federation_command(
    command_id: str,
    request: Request,
    share_token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
):
    token = _token(request, share_token)
    try:
        identifier = UUID(command_id)
    except ValueError:
        raise ServerSharingError(422, "server_share_invalid_request") from None
    return request.app.state.server_sharing.shared_command(
        token, identifier
    )
