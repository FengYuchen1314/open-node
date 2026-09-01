"""Administrator announcement instances and subscriber active projection."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.api.routes.subscriber_auth import Identity, require_subscriber
from open_node.domain.announcements import (
    AnnouncementCreate,
    AnnouncementDeleteResponse,
    AnnouncementError,
    AnnouncementRead,
    AnnouncementsResponse,
)
from open_node.services.auth import SessionIdentity
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(route_class=BackupAPIRoute, prefix="/announcements", tags=["announcements"])
account_router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/account/announcements",
    tags=["subscriber announcements"],
    dependencies=[Depends(require_subscriber)],
)


async def _payload(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/json"
    ):
        raise AnnouncementError("announcement_invalid_request", 415)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 16_384:
            raise AnnouncementError("announcement_invalid_request", 413)
        content.extend(chunk)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate field")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError("Invalid number")

    try:
        return AnnouncementCreate.model_validate(
            json.loads(
                content.decode("utf-8"),
                object_pairs_hook=unique,
                parse_constant=invalid_constant,
            )
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise AnnouncementError("announcement_invalid_request", 422) from None


@router.get("", response_model=AnnouncementsResponse)
def list_active(request: Request):
    return request.app.state.announcements.active()


@router.post("", response_model=AnnouncementRead, status_code=201)
async def publish(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    if not request.app.state.auth.allow_login_attempt(
        "announcement:administrator:" + identity.username, max_attempts=30
    ):
        raise AnnouncementError("announcement_rate_limited", 429)
    payload = await _payload(request)
    return await run_in_backup_threadpool(request.app.state.announcements.create, payload)


@router.delete("/{identifier}", response_model=AnnouncementDeleteResponse)
async def remove(
    identifier: UUID,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    if not request.app.state.auth.allow_login_attempt(
        "announcement:administrator:" + identity.username, max_attempts=30
    ):
        raise AnnouncementError("announcement_rate_limited", 429)
    await run_in_backup_threadpool(request.app.state.announcements.delete, identifier)
    return AnnouncementDeleteResponse(id=identifier)


@account_router.get("", response_model=AnnouncementsResponse)
def account_active(request: Request, identity: Identity):
    return request.app.state.announcements.active(username=identity.username)
