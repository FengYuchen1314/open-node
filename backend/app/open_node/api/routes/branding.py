"""Public site text projection and administrator-only, versioned settings."""

import json

from fastapi import APIRouter, Request
from pydantic import ValidationError

from open_node.api.backup import BackupAPIRoute
from open_node.domain.branding import (
    BrandingError,
    BrandingPublicRead,
    BrandingSettingsRead,
    BrandingSettingsUpdate,
)
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(
    route_class=BackupAPIRoute, prefix="/system-settings/branding", tags=["system-settings"]
)
public_router = APIRouter(route_class=BackupAPIRoute, prefix="/branding", tags=["branding"])
MAX_REQUEST_BYTES = 4096


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Non-finite JSON number")


async def _payload(request):
    # The private router checks the session/Origin/CSRF before this reads a body.
    # Even unexpected secret fields must not appear in validation responses.
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise BrandingError(415, "branding_invalid_request")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_REQUEST_BYTES:
            raise BrandingError(413, "branding_invalid_request")
        content.extend(chunk)
    try:
        return BrandingSettingsUpdate.model_validate(
            json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise BrandingError(422, "branding_invalid_request") from None


@public_router.get("", response_model=BrandingPublicRead)
def public_branding(request: Request):
    return request.app.state.branding.get_public()


@router.get("", response_model=BrandingSettingsRead)
def settings(request: Request):
    return request.app.state.branding.get_settings()


@router.put("", response_model=BrandingSettingsRead)
async def update_settings(request: Request):
    payload = await _payload(request)
    return await run_in_backup_threadpool(request.app.state.branding.update_settings, payload)
