from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from open_node.api.dependencies import get_inventory_store
from open_node.api.routes.subscriptions import rendered_subscription_response
from open_node.domain.subscription_profiles import (
    SubscriptionProfileRead,
    SubscriptionProfilesResponse,
    SubscriptionProfileUpdate,
)
from open_node.domain.subscriptions import SubscriptionClientFormat
from open_node.services.inventory import (
    InventoryStore,
    SubscriptionTokenNotFoundError,
    SubscriptionUnavailableError,
)
from open_node.services.subscription_profiles import (
    SubscriptionProfileConflict,
    SubscriptionProfileNotFoundError,
)

router = APIRouter(tags=["subscription profiles"])
legacy_router = APIRouter(tags=["MMWX compatibility"])


@router.get("/subscription-profiles", response_model=SubscriptionProfilesResponse)
def list_subscription_profiles(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    return SubscriptionProfilesResponse(profiles=store._subscription_profiles().list())


@router.put("/subscription-profiles/{identifier}", response_model=SubscriptionProfileRead)
def update_subscription_profile(
    identifier: UUID,
    payload: SubscriptionProfileUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        return store._subscription_profiles().update(identifier, payload)
    except SubscriptionProfileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SubscriptionProfileConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@legacy_router.get("/x/{code}", name="legacy_mmwx_subscription")
def render_legacy_mmwx_subscription(
    code: str,
    request: Request,
    client_format: Annotated[
        SubscriptionClientFormat, Query(alias="format")
    ] = SubscriptionClientFormat.CLASH,
    t: str | None = None,
    node_id: UUID | None = None,
):
    profiles = request.app.state.inventory._subscription_profiles()
    selected_format = profiles.legacy_format(t, client_format)
    try:
        rendered = profiles.resolve(code, selected_format, node_id)
    except (SubscriptionTokenNotFoundError, SubscriptionUnavailableError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return rendered_subscription_response(rendered)
