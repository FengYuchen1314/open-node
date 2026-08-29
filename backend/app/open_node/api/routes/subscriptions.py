import base64
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from open_node.api.dependencies import get_agent_connection_manager, get_inventory_store
from open_node.domain.subscription_links import SubscriptionShortCodeUpdate
from open_node.domain.subscriptions import (
    ManagedNodeCreate,
    ManagedNodeResponse,
    ManagedNodesResponse,
    ProductUserActiveUpdate,
    ProductUserCreate,
    ProductUserCredentialsResponse,
    ProductUserResponse,
    ProductUsersResponse,
    ProductUserSubscriptionTokenRead,
    ProductUserSubscriptionTokenResponse,
    ProductUserTrafficResponse,
    SubscriptionAccessResponse,
    SubscriptionCatalogExportResponse,
    SubscriptionCatalogImportRequest,
    SubscriptionCatalogImportResponse,
    SubscriptionClientFormat,
    SubscriptionDueTrafficResetRequest,
    SubscriptionDueTrafficResetResponse,
    SubscriptionFormatPreview,
    SubscriptionIpPolicyRead,
    SubscriptionIpPolicyUpdate,
    SubscriptionPlanAssignRequest,
    SubscriptionPlanAssignResponse,
    SubscriptionPlanCreate,
    SubscriptionPlanResponse,
    SubscriptionPlansResponse,
    SubscriptionQuotaStatusResponse,
    SubscriptionTemplatePresetApplyRequest,
    SubscriptionTemplatePresetsResponse,
)
from open_node.services.agent_ws import AgentConnectionManager
from open_node.services.inventory import (
    DuplicateProductUserError,
    DuplicateSubscriptionPlanNameError,
    InventoryStore,
    ManagedNodeNotFoundError,
    ProductUserConflict,
    ProductUserNotFoundError,
    ServerNotFoundError,
    SubscriptionPlanNotFoundError,
    SubscriptionTemplatePresetNotFoundError,
    SubscriptionTokenNotFoundError,
    SubscriptionTokenRecord,
    SubscriptionUnavailableError,
)
from open_node.services.subscription_access import SubscriptionAccessConflict

router = APIRouter(tags=["subscriptions"])
public_router = APIRouter(tags=["subscriptions"])


def enforce_subscription_ip(store, username, request):
    peer = request.client.host if request.client else "unknown"
    if not store._subscription_ip_policy().allowed(username, peer):
        raise HTTPException(status_code=404, detail="subscription not found")


@router.get("/user-subscription-ip-policy", response_model=SubscriptionIpPolicyRead)
@router.get(
    "/users/{username}/subscription-ip-policy", response_model=SubscriptionIpPolicyRead
)
def get_subscription_ip_policy(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        return store._subscription_ip_policy().read(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/user-subscription-ip-policy", response_model=SubscriptionIpPolicyRead)
@router.put(
    "/users/{username}/subscription-ip-policy", response_model=SubscriptionIpPolicyRead
)
def update_subscription_ip_policy(
    username: str,
    payload: SubscriptionIpPolicyUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        return store._subscription_ip_policy().update(username, payload)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/user-access", response_model=SubscriptionAccessResponse)
@router.get("/users/{username}/access", response_model=SubscriptionAccessResponse)
def subscription_access(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        return store._subscription_access().read(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/user-access/sync", response_model=SubscriptionAccessResponse)
@router.post("/users/{username}/access/sync", response_model=SubscriptionAccessResponse)
async def sync_subscription_access(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
):
    try:
        commands = store._subscription_access().run_once(username=username, force=True)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for command in commands:
        await connections.dispatch_command(store, command)
    return store._subscription_access().read(username)


@router.patch("/user-active", response_model=ProductUserResponse)
@router.patch("/users/{username}/active", response_model=ProductUserResponse)
async def update_product_user_active(
    username: str,
    payload: ProductUserActiveUpdate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
):
    try:
        user, commands = store._subscription_access().set_active(username, payload.is_active)
    except (ProductUserConflict, SubscriptionAccessConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    for command in commands:
        await connections.dispatch_command(store, command)
    return ProductUserResponse(user=user)


@router.get("/users", response_model=ProductUsersResponse)
def list_product_users(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUsersResponse:
    return ProductUsersResponse(users=store.list_product_users())


@router.post("/users", response_model=ProductUserResponse, status_code=status.HTTP_201_CREATED)
def create_product_user(
    payload: ProductUserCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserResponse:
    try:
        user = store.create_product_user(payload)
    except DuplicateProductUserError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ProductUserResponse(user=user)


@router.get("/user-subscription-token", response_model=ProductUserSubscriptionTokenResponse)
@router.get(
    "/users/{username}/subscription-token",
    response_model=ProductUserSubscriptionTokenResponse,
)
def get_subscription_token(
    username: str,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserSubscriptionTokenResponse:
    try:
        token = store.get_or_create_subscription_token(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProductUserConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _subscription_token_response(request, token)


@router.post(
    "/user-subscription-token", response_model=ProductUserSubscriptionTokenResponse, status_code=201
)
@router.post(
    "/users/{username}/subscription-token",
    response_model=ProductUserSubscriptionTokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_subscription_token(
    username: str,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserSubscriptionTokenResponse:
    try:
        token = store.get_or_create_subscription_token(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProductUserConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _subscription_token_response(request, token)


@router.post("/user-subscription-token/reset", response_model=ProductUserSubscriptionTokenResponse)
@router.post(
    "/users/{username}/subscription-token/reset",
    response_model=ProductUserSubscriptionTokenResponse,
)
def reset_subscription_token(
    username: str,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserSubscriptionTokenResponse:
    try:
        token = store.reset_subscription_token(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProductUserConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _subscription_token_response(request, token)


@router.put("/user-subscription-short-code", response_model=ProductUserSubscriptionTokenResponse)
@router.put(
    "/users/{username}/subscription-short-code", response_model=ProductUserSubscriptionTokenResponse
)
def update_subscription_short_code(
    username: str,
    payload: SubscriptionShortCodeUpdate,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
):
    try:
        token = store.set_subscription_short_code(username, payload)
    except ProductUserNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProductUserConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _subscription_token_response(request, token)


@router.get("/user-credentials", response_model=ProductUserCredentialsResponse)
@router.get(
    "/users/{username}/credentials",
    response_model=ProductUserCredentialsResponse,
)
def list_subscription_credentials(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserCredentialsResponse:
    try:
        credentials = store.list_subscription_credentials(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProductUserCredentialsResponse(username=username, credentials=credentials)


@router.get("/user-traffic", response_model=ProductUserTrafficResponse)
@router.get(
    "/users/{username}/traffic",
    response_model=ProductUserTrafficResponse,
)
def get_subscription_traffic(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProductUserTrafficResponse:
    try:
        return store.subscription_user_traffic(username)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/user-quota", response_model=SubscriptionQuotaStatusResponse)
@router.get(
    "/users/{username}/quota",
    response_model=SubscriptionQuotaStatusResponse,
)
def get_subscription_quota(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    now: Annotated[datetime | None, Query()] = None,
) -> SubscriptionQuotaStatusResponse:
    try:
        quota = store.subscription_user_quota(username, now=now)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SubscriptionQuotaStatusResponse(quota=quota)


@router.post("/user-traffic/reset", response_model=SubscriptionQuotaStatusResponse)
@router.post(
    "/users/{username}/traffic/reset",
    response_model=SubscriptionQuotaStatusResponse,
)
def reset_subscription_traffic(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    now: Annotated[datetime | None, Query()] = None,
) -> SubscriptionQuotaStatusResponse:
    try:
        quota = store.reset_subscription_traffic(username, now=now)
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SubscriptionQuotaStatusResponse(quota=quota)


@router.post("/traffic/reset-due", response_model=SubscriptionDueTrafficResetResponse)
def reset_due_subscription_traffic(
    payload: SubscriptionDueTrafficResetRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionDueTrafficResetResponse:
    return store.reset_due_subscription_traffic(payload)


@router.get("/node-presets", response_model=SubscriptionTemplatePresetsResponse)
def list_subscription_node_presets(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionTemplatePresetsResponse:
    return SubscriptionTemplatePresetsResponse(presets=store.list_subscription_template_presets())


@router.post(
    "/node-presets/{preset_id}/nodes",
    response_model=ManagedNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_node_from_preset(
    preset_id: str,
    payload: SubscriptionTemplatePresetApplyRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ManagedNodeResponse:
    try:
        node = store.create_managed_node_from_preset(preset_id, payload)
    except SubscriptionTemplatePresetNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ManagedNodeResponse(node=node)


@router.get("/catalog/export", response_model=SubscriptionCatalogExportResponse)
def export_subscription_catalog(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    include_credentials: bool = False,
) -> SubscriptionCatalogExportResponse:
    try:
        return SubscriptionCatalogExportResponse(
            catalog=store.export_subscription_catalog(include_credentials=include_credentials)
        )
    except ProductUserConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/catalog/import", response_model=SubscriptionCatalogImportResponse)
def import_subscription_catalog(
    payload: SubscriptionCatalogImportRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionCatalogImportResponse:
    try:
        return store.import_subscription_catalog(payload)
    except ProductUserConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/nodes", response_model=ManagedNodesResponse)
def list_managed_nodes(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ManagedNodesResponse:
    return ManagedNodesResponse(nodes=store.list_managed_nodes())


@router.post("/nodes", response_model=ManagedNodeResponse, status_code=status.HTTP_201_CREATED)
def create_managed_node(
    payload: ManagedNodeCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ManagedNodeResponse:
    try:
        node = store.create_managed_node(payload)
    except ServerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ManagedNodeResponse(node=node)


@router.get("/plans", response_model=SubscriptionPlansResponse)
def list_subscription_plans(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionPlansResponse:
    return SubscriptionPlansResponse(plans=store.list_subscription_plans())


@router.post("/plans", response_model=SubscriptionPlanResponse, status_code=status.HTTP_201_CREATED)
def create_subscription_plan(
    payload: SubscriptionPlanCreate,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> SubscriptionPlanResponse:
    try:
        plan = store.create_subscription_plan(payload)
    except DuplicateSubscriptionPlanNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ManagedNodeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SubscriptionPlanResponse(plan=plan)


@router.get("/user-subscription-preview", response_model=SubscriptionFormatPreview)
@router.get("/users/{username}/subscription-preview", response_model=SubscriptionFormatPreview)
def preview_subscription_format(
    username: str,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    client_format: Annotated[
        SubscriptionClientFormat, Query(alias="format")
    ] = SubscriptionClientFormat.CLASH,
) -> SubscriptionFormatPreview:
    try:
        return store.subscription_format_preview(username, client_format)
    except (ProductUserNotFoundError, SubscriptionUnavailableError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@public_router.get("/subscribe/{subscription_key}", name="render_user_subscription")
def render_user_subscription(
    subscription_key: str,
    request: Request,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    client_format: Annotated[
        SubscriptionClientFormat,
        Query(alias="format"),
    ] = SubscriptionClientFormat.CLASH,
    node_id: UUID | None = None,
) -> Response:
    try:
        rendered = store.render_subscription(subscription_key, client_format, node_id)
    except SubscriptionTokenNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SubscriptionUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    enforce_subscription_ip(store, rendered.username, request)
    return rendered_subscription_response(rendered)


def rendered_subscription_response(rendered) -> Response:
    headers = {
        "Cache-Control": "no-store",
        "X-Open-Node-Included-Nodes": str(rendered.included_nodes),
        "X-Open-Node-Excluded-Nodes": str(rendered.excluded_nodes),
        "Content-Disposition": InventoryStore.subscription_content_disposition(rendered.filename),
        "profile-title": "base64:"
        + base64.b64encode(rendered.plan_name.encode("utf-8")).decode("ascii"),
    }
    if rendered.subscription_userinfo:
        headers["subscription-userinfo"] = rendered.subscription_userinfo
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers=headers,
    )


@router.post("/user-plan", response_model=SubscriptionPlanAssignResponse)
@router.post("/users/{username}/plan", response_model=SubscriptionPlanAssignResponse)
async def assign_subscription_plan(
    username: str,
    payload: SubscriptionPlanAssignRequest,
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    connections: Annotated[AgentConnectionManager, Depends(get_agent_connection_manager)],
) -> SubscriptionPlanAssignResponse:
    queued = []
    try:
        user, plan, batches, warnings = store.assign_subscription_plan(
            username, payload, queued_commands=queued
        )
    except ProductUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SubscriptionPlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ProductUserConflict, SubscriptionAccessConflict) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    commands = []
    for command in queued:
        commands.append(await connections.dispatch_command(store, command))

    return SubscriptionPlanAssignResponse(
        user=user,
        plan=plan,
        provisioning_batches=batches,
        commands=commands,
        warnings=warnings,
    )


def _subscription_token_response(
    request: Request,
    token: SubscriptionTokenRecord,
) -> ProductUserSubscriptionTokenResponse:
    subscription_url = str(
        request.url_for("render_user_subscription", subscription_key=token.token)
    )
    short_url = str(request.url_for("render_user_subscription", subscription_key=token.short_code))
    return ProductUserSubscriptionTokenResponse(
        subscription=ProductUserSubscriptionTokenRead(
            username=token.username,
            token=token.token,
            short_code=token.short_code,
            generated_short_code=token.generated_short_code,
            custom_short_code=token.custom_short_code,
            revision=token.revision,
            subscription_url=subscription_url,
            short_url=short_url,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
    )
