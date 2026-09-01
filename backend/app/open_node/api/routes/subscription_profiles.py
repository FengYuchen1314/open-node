from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from open_node.api.backup import BackupAPIRoute
from open_node.api.dependencies import get_inventory_store
from open_node.api.routes.security import (
    failed_public_subscription,
    guard_public_subscription,
    successful_public_subscription,
)
from open_node.api.routes.subscriptions import (
    enforce_subscription_ip,
    rendered_subscription_response,
)
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
from open_node.services.subscription_clients import select_client_format
from open_node.services.subscription_customizations import SubscriptionCustomizationError
from open_node.services.subscription_profiles import (
    SubscriptionProfileConflict,
    SubscriptionProfileNotFoundError,
)

router = APIRouter(route_class=BackupAPIRoute, tags=["subscription profiles"])
legacy_router = APIRouter(route_class=BackupAPIRoute, tags=["MMWX compatibility"])
public_router = APIRouter(route_class=BackupAPIRoute, tags=["subscription profiles"])


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
        SubscriptionClientFormat | Literal["auto"] | None, Query(alias="format")
    ] = None,
    t: str | None = None,
    node_id: UUID | None = None,
):
    peer = guard_public_subscription(request)
    if not request.app.state.settings.short_links_enabled:
        raise HTTPException(404, "Subscription not found")
    profiles = request.app.state.inventory._subscription_profiles()
    selected_format = select_client_format(client_format, request.headers.get("user-agent", ""))
    selected_format = profiles.legacy_format(t, selected_format)
    try:
        rendered = profiles.resolve(
            code,
            selected_format,
            node_id,
            public_base_url=(
                str(request.base_url).rstrip("/") + request.app.state.settings.api_prefix
            ),
        )
    except SubscriptionTokenNotFoundError as exc:
        failed_public_subscription(request, peer, "/x/{code}")
        raise HTTPException(404, "subscription not found") from exc
    except SubscriptionUnavailableError as exc:
        successful_public_subscription(request, peer)
        raise HTTPException(404, str(exc)) from exc
    successful_public_subscription(request, peer)
    enforce_subscription_ip(request.app.state.inventory, rendered.username, request)
    return rendered_subscription_response(rendered)


@public_router.get(
    "/proxy-provider/{code}/{provider_id}",
    name="render_subscription_proxy_provider",
)
def render_proxy_provider(code: str, provider_id: str, request: Request):
    peer = guard_public_subscription(request)
    try:
        username, name, content, count = (
            request.app.state.inventory._subscription_profiles().provider(code, provider_id)
        )
    except SubscriptionTokenNotFoundError as exc:
        failed_public_subscription(request, peer, "/proxy-provider/{code}/{provider}")
        raise HTTPException(404, "proxy provider unavailable") from exc
    except SubscriptionUnavailableError as exc:
        successful_public_subscription(request, peer)
        raise HTTPException(404, "proxy provider unavailable") from exc
    except SubscriptionCustomizationError as exc:
        successful_public_subscription(request, peer)
        raise HTTPException(404, "proxy provider unavailable") from exc
    successful_public_subscription(request, peer)
    enforce_subscription_ip(request.app.state.inventory, username, request)
    return Response(
        content,
        media_type="text/yaml; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Open-Node-Included-Nodes": str(count),
            "Content-Disposition": InventoryStore.subscription_content_disposition(
                f"{name}.yaml"
            ),
        },
    )
