"""Anonymous first-run endpoints protected by a locally issued, one-use credential."""

import json

from fastapi import APIRouter, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from open_node.api.auth import check_request_origin
from open_node.api.backup import BackupAPIRoute
from open_node.domain.initial_setup import (
    InitialSetupError,
    InitialSetupRequest,
    InitialSetupResult,
    InitialSetupStatus,
)
from open_node.services.backup_runtime import run_in_backup_threadpool
from open_node.services.initial_setup import InitialSetupStore

router = APIRouter(route_class=BackupAPIRoute, prefix="/setup", tags=["initial setup"])
MAX_BODY = 16384


def _unique(pairs):
    value = {}
    for key, entry in pairs:
        if key in value:
            raise ValueError()
        value[key] = entry
    return value


def _invalid(_value):
    raise ValueError()


@router.get("", response_model=InitialSetupStatus)
def setup_status(request: Request):
    return InitialSetupStore(request.app.state.auth).status()


@router.post("", response_model=InitialSetupResult, status_code=201)
async def initialize(request: Request):
    check_request_origin(request)
    if request.headers.get("x-open-node-client") != "browser":
        raise InitialSetupError(403, "setup_invalid_request")
    peer = request.client.host if request.client else "unknown"
    try:
        allowed = await run_in_backup_threadpool(
            request.app.state.auth.allow_login_attempt, "initial-setup:" + peer,
        )
    except SQLAlchemyError:
        raise InitialSetupError(503, "setup_unavailable") from None
    if not allowed:
        raise InitialSetupError(429, "setup_rate_limited")
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media != "application/json":
        raise InitialSetupError(415, "setup_invalid_request")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_BODY:
            raise InitialSetupError(413, "setup_invalid_request")
        content.extend(chunk)
    try:
        payload = InitialSetupRequest.model_validate(json.loads(
            content.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid,
        ))
    except (ValueError, ValidationError, TypeError, RecursionError):
        raise InitialSetupError(422, "setup_invalid_request") from None
    await run_in_backup_threadpool(InitialSetupStore(request.app.state.auth).complete, payload)
    return InitialSetupResult()
