import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.api.routes.subscriber_auth import Identity, require_subscriber
from open_node.domain.renewals import (
    AccountRenewalsResponse,
    RenewalCreate,
    RenewalDecision,
    RenewalDecisionResponse,
    RenewalError,
    RenewalRead,
    RenewalsResponse,
    RenewalStatus,
)
from open_node.services.auth import SessionIdentity
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(route_class=BackupAPIRoute, prefix="/renewals", tags=["renewals"])
account_router = APIRouter(
    route_class=BackupAPIRoute, prefix="/account/renewals", tags=["subscriber renewals"],
    dependencies=[Depends(require_subscriber)],
)


async def _payload(request, model):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise RenewalError("renewal_invalid_request", 415)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 8192:
            raise RenewalError("renewal_invalid_request", 413)
        content.extend(chunk)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("Invalid number")

    try:
        return model.model_validate(json.loads(
            content.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid
        ))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise RenewalError("renewal_invalid_request", 422) from None


def _limit(request, key, maximum):
    if not request.app.state.auth.allow_login_attempt("renewal:" + key, max_attempts=maximum):
        raise RenewalError("renewal_rate_limited", 429)


@account_router.get("", response_model=AccountRenewalsResponse)
def account_requests(
    request: Request, identity: Identity,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
):
    return request.app.state.renewals.account(identity.username, limit=limit, offset=offset)


@account_router.post("", response_model=RenewalRead, status_code=201)
async def submit(request: Request, identity: Identity):
    _limit(request, "user:" + identity.username, 10)
    payload = await _payload(request, RenewalCreate)
    return await run_in_backup_threadpool(
        request.app.state.renewals.submit, identity.username, payload
    )


@account_router.get("/{identifier}", response_model=RenewalRead)
def account_request(identifier: UUID, request: Request, identity: Identity):
    return request.app.state.renewals.get(identifier, username=identity.username)


@account_router.post("/{identifier}/cancel", response_model=RenewalRead)
async def cancel(identifier: UUID, request: Request, identity: Identity):
    _limit(request, "user:" + identity.username, 10)
    return await run_in_backup_threadpool(
        request.app.state.renewals.cancel, identifier, identity.username
    )


@router.get("", response_model=RenewalsResponse)
def list_requests(
    request: Request,
    status: RenewalStatus | None = None,
    username: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
):
    return request.app.state.renewals.list(
        username=username, status=status, limit=limit, offset=offset
    )


@router.get("/{identifier}", response_model=RenewalRead)
def get_request(identifier: UUID, request: Request):
    return request.app.state.renewals.get(identifier)


@router.post("/{identifier}/review", response_model=RenewalDecisionResponse)
async def review(
    identifier: UUID, request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    _limit(request, "administrator:" + identity.username, 20)
    payload = await _payload(request, RenewalDecision)
    result = await run_in_backup_threadpool(
        request.app.state.renewals.review, identifier, payload, identity.username
    )
    commands = []
    for command in result.commands:
        commands.append(await request.app.state.agent_connections.dispatch_command(
            request.app.state.inventory, command
        ))
    return result.model_copy(update={"commands": commands})
