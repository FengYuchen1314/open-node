"""Administrator-only application update handoff endpoints."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.domain.application_updates import (
    ApplicationUpdateAccepted,
    ApplicationUpdateApply,
    ApplicationUpdateError,
    ApplicationUpdateState,
)
from open_node.services.auth import SessionIdentity

router = APIRouter(
    route_class=BackupAPIRoute,
    prefix="/application-update",
    tags=["application update"],
)


async def _apply_payload(request: Request) -> ApplicationUpdateApply:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApplicationUpdateError("application_update_invalid_request", 415)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 4096:
            raise ApplicationUpdateError("application_update_invalid_request", 413)
        content.extend(chunk)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    try:
        return ApplicationUpdateApply.model_validate(json.loads(
            content.decode("utf-8"), object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        ))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise ApplicationUpdateError("application_update_invalid_request", 422) from None


def _rate_limit(request: Request, identity: SessionIdentity) -> None:
    if not request.app.state.auth.allow_login_attempt(
        "application-update:administrator:" + identity.username, max_attempts=12
    ):
        raise ApplicationUpdateError("application_update_rate_limited", 429)


@router.get("", response_model=ApplicationUpdateState)
def status(request: Request):
    return request.app.state.application_updates.status()


@router.post("/check", response_model=ApplicationUpdateAccepted, status_code=202)
def check(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    _rate_limit(request, identity)
    return request.app.state.application_updates.check()


@router.post("/apply", response_model=ApplicationUpdateAccepted, status_code=202)
async def apply(
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    _rate_limit(request, identity)
    payload = await _apply_payload(request)
    return request.app.state.application_updates.apply(payload.target_revision)
