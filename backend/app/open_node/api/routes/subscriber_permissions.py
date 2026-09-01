"""Administrator policy and subscriber-owned permission snapshot."""

import json

from fastapi import APIRouter, Request
from pydantic import ValidationError

from open_node.api.backup import BackupAPIRoute
from open_node.api.routes.subscriber_auth import Identity
from open_node.domain.subscriber_permissions import (
    SubscriberFeature,
    SubscriberPermissionsAccount,
    SubscriberPermissionsError,
    SubscriberPermissionsSettings,
    SubscriberPermissionsUpdate,
)
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/subscriber-permissions",
    tags=["subscriber permissions"],
)
account_router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/account/permissions",
    tags=["subscriber permissions"],
)


def require_feature(page: SubscriberFeature):
    def dependency(request: Request):
        from open_node.api.routes.subscriber_auth import require_subscriber

        identity = require_subscriber(request)
        request.app.state.subscriber_permissions.require_page(identity.username, page)
        return identity

    return dependency


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _invalid(_value):
    raise ValueError()


async def _payload(request: Request):
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise SubscriberPermissionsError(415, "subscriber_permissions_invalid_request")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 8192:
            raise SubscriberPermissionsError(413, "subscriber_permissions_invalid_request")
        content.extend(chunk)
    try:
        return SubscriberPermissionsUpdate.model_validate(json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid,
        ))
    except (ValidationError, UnicodeError, ValueError, TypeError, RecursionError):
        raise SubscriberPermissionsError(
            422, "subscriber_permissions_invalid_request"
        ) from None


@router.get("", response_model=SubscriberPermissionsSettings)
def settings(request: Request):
    return request.app.state.subscriber_permissions.settings()


@router.put("", response_model=SubscriberPermissionsSettings)
async def update(request: Request):
    payload = await _payload(request)
    return await run_in_backup_threadpool(
        request.app.state.subscriber_permissions.update, payload
    )


@account_router.get("", response_model=SubscriberPermissionsAccount)
def account(request: Request, identity: Identity):
    return request.app.state.subscriber_permissions.account(identity.username)
