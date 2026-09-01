"""Administrator sharing controls and token-authenticated federation boundary."""

import asyncio
import base64
import binascii
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse
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
from open_node.services.federation_crypto import (
    FEDERATION_ENCRYPTED_HEADER,
    FEDERATION_KEY_EXCHANGE_HEADER,
    FederationCryptoError,
    derive_federation_session,
    generate_ephemeral,
)
from open_node.services.secure_channel import ChannelError, decode_public_key

router = APIRouter(
    route_class=BackupAPIRoute, prefix="/server-shares", tags=["server sharing"]
)
consumer_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/server-federation", tags=["server federation"]
)
public_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/federation", tags=["federation"]
)
legacy_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/api/federation", tags=["federation compatibility"]
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


async def _bounded_body(request):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 64 * 1024:
            raise ServerSharingError(413, "server_share_invalid_request")
        content.extend(chunk)
    return bytes(content)


def _legacy_command(payload):
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=_invalid,
        )
        if not isinstance(value, dict) or set(value) - {"method", "path", "body"}:
            raise ValueError()
        encoded = value.get("body", "")
        if not isinstance(encoded, str):
            raise ValueError()
        body_bytes = base64.b64decode(encoded, validate=True) if encoded else b""
        if len(body_bytes) > 64 * 1024:
            raise ValueError()
        body = None
        if body_bytes:
            body = json.loads(
                body_bytes.decode("utf-8"),
                object_pairs_hook=_unique,
                parse_constant=_invalid,
            )
        return FederationCommandCreate.model_validate({
            "method": value.get("method", "GET"),
            "path": value.get("path"),
            "body": body,
            "timeout_ms": 30_000,
        })
    except (
        ValidationError,
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        binascii.Error,
    ):
        raise ServerSharingError(422, "server_share_invalid_request") from None


async def _legacy_envelope(request, token):
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    payload = await _bounded_body(request)
    key_exchange = request.headers.get(FEDERATION_KEY_EXCHANGE_HEADER)
    encrypted = request.headers.get(FEDERATION_ENCRYPTED_HEADER) == "1"
    sessions = request.app.state.server_sharing.legacy_sessions
    if key_exchange:
        if media != "application/json" or encrypted:
            raise ServerSharingError(415, "server_share_invalid_request")
        try:
            consumer_public = decode_public_key(key_exchange)
            private, owner_public = generate_ephemeral()
            session = derive_federation_session(
                private,
                owner_public,
                consumer_public,
                token,
                is_initiator=False,
            )
        except (ChannelError, FederationCryptoError):
            raise ServerSharingError(422, "server_share_invalid_request") from None
        sessions.set(token, session)
        return payload, None, {
            FEDERATION_KEY_EXCHANGE_HEADER: base64.b64encode(owner_public).decode("ascii")
        }
    if encrypted:
        if media != "application/octet-stream":
            raise ServerSharingError(415, "server_share_invalid_request")
        session = sessions.get(token)
        if session is None:
            return None, None, JSONResponse(
                status_code=412,
                content={"success": False, "error": "no session, re-negotiate"},
            )
        try:
            return session.decrypt(payload), session, {}
        except ChannelError:
            sessions.delete(token)
            raise ServerSharingError(422, "server_share_invalid_request") from None
    if media != "application/json":
        raise ServerSharingError(415, "server_share_invalid_request")
    return payload, None, {}


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


@legacy_router.get("/server-info", response_model=FederationServerInfo)
def legacy_server_info(
    request: Request,
    share_token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
):
    return request.app.state.server_sharing.server_info(_token(request, share_token))


@legacy_router.post("/manage")
async def legacy_federation_manage(
    request: Request,
    share_token: Annotated[str | None, Header(alias="X-Share-Token")] = None,
):
    token = _token(request, share_token)
    payload, response_session, response_headers = await _legacy_envelope(request, token)
    if isinstance(response_headers, Response):
        return response_headers
    command_payload = _legacy_command(payload)
    command = await run_in_backup_threadpool(
        request.app.state.server_sharing.create_shared_command,
        token,
        command_payload,
    )
    await request.app.state.agent_connections.dispatch_command(
        request.app.state.inventory, command
    )
    deadline = asyncio.get_running_loop().time() + command_payload.timeout_ms / 1000
    while True:
        current = await run_in_backup_threadpool(
            request.app.state.server_sharing.shared_command, token, command.id
        )
        if current.status in {"succeeded", "failed", "skipped"}:
            break
        if asyncio.get_running_loop().time() >= deadline:
            return JSONResponse(
                status_code=504,
                content={"success": False, "error": "federation command timed out"},
                headers=response_headers,
            )
        await asyncio.sleep(0.1)

    if current.failed or (current.result_status is not None and current.result_status >= 400):
        return JSONResponse(
            status_code=(
                current.result_status
                if current.result_status is not None and 400 <= current.result_status <= 599
                else 502
            ),
            content={"success": False, "error": "federation command failed"},
            headers=response_headers,
        )
    result = current.result_body if current.result_body is not None else {"success": True}
    encoded = json.dumps(
        result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if response_session is not None:
        try:
            encrypted = response_session.encrypt(encoded)
        except ChannelError:
            request.app.state.server_sharing.legacy_sessions.delete(token)
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": "federation encryption failed"},
            )
        return Response(
            content=encrypted,
            media_type="application/octet-stream",
            headers={**response_headers, FEDERATION_ENCRYPTED_HEADER: "1"},
        )
    return JSONResponse(content=result, headers=response_headers)
