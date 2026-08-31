"""Private external-source workflows; no source URL or proxy secret is returned."""

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from open_node.api.backup import BackupAPIRoute
from open_node.domain.external_subscriptions import (
    ExternalConfirmationRead,
    ExternalNodeUpdate,
    ExternalPreviewConfirm,
    ExternalPreviewRead,
    ExternalRevisionRequest,
    ExternalSourceCreate,
    ExternalSourceDelete,
    ExternalSourceDetail,
    ExternalSourceRead,
    ExternalSourcesResponse,
    ExternalSourceUpdate,
)
from open_node.services.backup_runtime import run_in_backup_threadpool

router = APIRouter(
    route_class=BackupAPIRoute, prefix="/external-subscriptions", tags=["external subscriptions"]
)
MAX_REQUEST_BYTES = 65536


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Non-finite JSON number")


async def _payload(request, model):
    # Authentication/CSRF is resolved by the private router before consuming a
    # potentially secret body. Do not let validation errors echo its input.
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise HTTPException(415, "External subscription requests require JSON")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "External subscription request is too large")
        content.extend(chunk)
    try:
        return model.model_validate(
            json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        )
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise HTTPException(422, "Invalid external subscription request") from None


@router.get("", response_model=ExternalSourcesResponse)
def list_sources(request: Request):
    return ExternalSourcesResponse(sources=request.app.state.external_subscriptions.list())


@router.post("", response_model=ExternalSourceRead, status_code=201)
async def create_source(request: Request):
    payload = await _payload(request, ExternalSourceCreate)
    return await run_in_backup_threadpool(request.app.state.external_subscriptions.create, payload)


@router.get("/{source_id}", response_model=ExternalSourceDetail)
def source_detail(source_id: UUID, request: Request):
    return request.app.state.external_subscriptions.detail(source_id)


@router.put("/{source_id}", response_model=ExternalSourceRead)
async def update_source(source_id: UUID, request: Request):
    payload = await _payload(request, ExternalSourceUpdate)
    return await run_in_backup_threadpool(
        request.app.state.external_subscriptions.update, source_id, payload
    )


@router.post("/{source_id}/delete")
async def delete_source(source_id: UUID, request: Request):
    payload = await _payload(request, ExternalSourceDelete)
    await run_in_backup_threadpool(
        request.app.state.external_subscriptions.delete, source_id, payload
    )
    return {"deleted": True, "license_required": False}


@router.put("/{source_id}/nodes/{node_id}", response_model=ExternalSourceDetail)
async def update_node(source_id: UUID, node_id: UUID, request: Request):
    payload = await _payload(request, ExternalNodeUpdate)
    return await run_in_backup_threadpool(
        request.app.state.external_subscriptions.update_node, source_id, node_id, payload
    )


@router.post("/{source_id}/previews", response_model=ExternalPreviewRead)
async def fetch_preview(source_id: UUID, request: Request):
    payload = await _payload(request, ExternalRevisionRequest)
    return await run_in_backup_threadpool(
        request.app.state.external_subscriptions.prepare_preview,
        source_id,
        payload.expected_revision,
    )


@router.get("/{source_id}/previews/{preview_id}", response_model=ExternalPreviewRead)
def preview_detail(source_id: UUID, preview_id: UUID, request: Request):
    return request.app.state.external_subscriptions.preview(source_id, preview_id)


@router.post("/{source_id}/previews/{preview_id}/confirm", response_model=ExternalConfirmationRead)
async def confirm_preview(source_id: UUID, preview_id: UUID, request: Request):
    payload = await _payload(request, ExternalPreviewConfirm)
    return await run_in_backup_threadpool(
        request.app.state.external_subscriptions.confirm, source_id, preview_id, payload
    )


@router.delete("/{source_id}/previews/{preview_id}")
def cancel_preview(source_id: UUID, preview_id: UUID, request: Request):
    request.app.state.external_subscriptions.cancel_preview(source_id, preview_id)
    return {"cancelled": True, "license_required": False}
