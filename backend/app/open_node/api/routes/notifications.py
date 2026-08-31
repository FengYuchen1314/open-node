"""Administrator-only notification settings, offline previews and durable requests."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from open_node.domain.notifications import (
    NotificationDeliveriesResponse,
    NotificationDeliveryDetail,
    NotificationDeliveryRead,
    NotificationError,
    NotificationPreviewRead,
    NotificationRetryRequest,
    NotificationRevisionRequest,
    NotificationSettingsRead,
    NotificationSettingsUpdate,
    NotificationTestRequest,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])
MAX_REQUEST_BYTES = 8192


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
    # Authentication, exact-Origin and CSRF run before reading any secret body.
    # Pydantic validation details must never echo the bot token, including errors
    # in adjacent fields or a duplicate-key/oversized JSON document.
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise NotificationError(415, "notification_invalid_request")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_REQUEST_BYTES:
            raise NotificationError(413, "notification_invalid_request")
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
        raise NotificationError(422, "notification_invalid_request") from None


@router.get("/settings", response_model=NotificationSettingsRead)
def settings(request: Request):
    return request.app.state.notifications.get_settings()


@router.put("/settings", response_model=NotificationSettingsRead)
async def update_settings(request: Request):
    payload = await _payload(request, NotificationSettingsUpdate)
    return await run_in_threadpool(request.app.state.notifications.update_settings, payload)


@router.post("/preview", response_model=NotificationPreviewRead)
async def preview(request: Request):
    payload = await _payload(request, NotificationRevisionRequest)
    return await run_in_threadpool(
        request.app.state.notifications.preview, payload.expected_revision
    )


@router.post("/test", response_model=NotificationDeliveryDetail, status_code=202)
async def enqueue_test(request: Request):
    payload = await _payload(request, NotificationTestRequest)
    return await run_in_threadpool(request.app.state.notifications.enqueue_test, payload)


@router.get("/deliveries", response_model=NotificationDeliveriesResponse)
def deliveries(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return request.app.state.notifications.list_deliveries(limit=limit)


@router.get("/deliveries/{delivery_id}", response_model=NotificationDeliveryDetail)
def delivery(delivery_id: UUID, request: Request):
    return request.app.state.notifications.delivery(delivery_id)


@router.get("/requests/{request_id}", response_model=NotificationDeliveryRead)
def request_delivery(request_id: UUID, request: Request):
    # A missing row is not proof that an in-flight POST will never commit.
    return request.app.state.notifications.request_delivery(request_id)


@router.post("/deliveries/{delivery_id}/retry", response_model=NotificationDeliveryDetail)
async def retry(delivery_id: UUID, request: Request):
    payload = await _payload(request, NotificationRetryRequest)
    return await run_in_threadpool(request.app.state.notifications.retry, delivery_id, payload)
