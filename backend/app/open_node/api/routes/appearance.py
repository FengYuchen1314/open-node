"""Versioned public appearance, admin-only uploads and isolated image responses."""

import json
import re

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from open_node.api.backup import BackupAPIRoute
from open_node.domain.appearance import (
    ASSET_LIMITS,
    MAX_REVISION,
    AppearanceError,
    AppearancePublic,
    AppearanceSettings,
    AppearanceUpdate,
)
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(route_class=BackupAPIRoute, prefix="/system-settings/appearance",
                   tags=["system-settings"])
public_router = APIRouter(route_class=BackupAPIRoute, prefix="/appearance", tags=["appearance"])


async def body(request, limit):
    result = bytearray()
    async for chunk in request.stream():
        if len(result) + len(chunk) > limit:
            raise AppearanceError(413, "appearance_invalid_request")
        result.extend(chunk)
    return bytes(result)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def invalid(_value):
    raise ValueError()


@public_router.get("", response_model=AppearancePublic)
def public_settings(request: Request):
    return request.app.state.appearance.get_public()


@public_router.get("/assets/{slot}/{digest}")
def public_image(request: Request, slot: str, digest: str):
    content, media = request.app.state.appearance.image(slot, digest)
    return Response(content, media_type=media, headers={
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store", "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    })


@router.get("", response_model=AppearanceSettings)
def settings(request: Request):
    return request.app.state.appearance.get_settings()


@router.put("", response_model=AppearanceSettings)
async def update_settings(request: Request):
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise AppearanceError(415, "appearance_invalid_request")
    raw = await body(request, 16384)
    try:
        payload = AppearanceUpdate.model_validate(json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid,
        ))
    except (ValueError, TypeError, ValidationError, RecursionError):
        raise AppearanceError(422, "appearance_invalid_request") from None
    return await run_in_backup_threadpool(request.app.state.appearance.update, payload)


@router.post("/{slot}", response_model=AppearanceSettings)
async def upload(request: Request, slot: str):
    if slot not in ASSET_LIMITS:
        raise AppearanceError(422, "appearance_invalid_request")
    revision = request.headers.get("x-appearance-revision", "")
    if not re.fullmatch(r"0|[1-9][0-9]{0,15}", revision) or int(revision) > MAX_REVISION:
        raise AppearanceError(422, "appearance_invalid_request")
    content = await body(request, ASSET_LIMITS[slot])
    return await run_in_backup_threadpool(
        request.app.state.appearance.upload, slot, int(revision), content,
    )
