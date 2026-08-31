from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_inventory_store
from open_node.api.routes.subscriptions import rendered_subscription_response
from open_node.domain.subscriptions import SubscriptionClientFormat
from open_node.domain.temporary_subscriptions import (
    TemporarySubscriptionCreate,
    TemporarySubscriptionDeleteResponse,
    TemporarySubscriptionRead,
    TemporarySubscriptionsResponse,
)
from open_node.services.inventory import (
    InventoryStore,
    ManagedNodeNotFoundError,
    SubscriptionUnavailableError,
)
from open_node.services.temporary_subscriptions import (
    TemporarySubscriptionConflict,
    TemporarySubscriptionNotFoundError,
)

router = APIRouter(route_class=BackupAPIRoute, tags=["temporary subscriptions"])
public_router = APIRouter(route_class=BackupAPIRoute, tags=["temporary subscriptions"])


@router.get("/temporary-subscriptions", response_model=TemporarySubscriptionsResponse)
def list_temporary_subscriptions(
    request: Request,
    response: Response,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    response.headers["Cache-Control"] = "no-store"
    return TemporarySubscriptionsResponse(
        subscriptions=store._temporary_subscriptions().list(request.url_for)
    )


@router.post(
    "/temporary-subscriptions",
    response_model=TemporarySubscriptionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_temporary_subscription(
    payload: TemporarySubscriptionCreate,
    request: Request,
    response: Response,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return store._temporary_subscriptions().create(payload, request.url_for)
    except ManagedNodeNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except TemporarySubscriptionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete(
    "/temporary-subscriptions/{identifier}",
    response_model=TemporarySubscriptionDeleteResponse,
)
def delete_temporary_subscription(
    identifier: UUID,
    response: Response,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        store._temporary_subscriptions().delete(identifier)
    except TemporarySubscriptionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return TemporarySubscriptionDeleteResponse(id=identifier)


@public_router.get("/t/{code}", name="render_temporary_subscription")
def render_temporary_subscription(
    code: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    client_format: Annotated[
        SubscriptionClientFormat, Query(alias="format")
    ] = SubscriptionClientFormat.CLASH,
    node_id: UUID | None = None,
):
    try:
        rendered = store._temporary_subscriptions().render(code, client_format, node_id)
    except (TemporarySubscriptionNotFoundError, SubscriptionUnavailableError) as exc:
        raise HTTPException(
            404,
            "temporary subscription not found",
            headers={"Cache-Control": "no-store"},
        ) from exc
    return rendered_subscription_response(rendered)
