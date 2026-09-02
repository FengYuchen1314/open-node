from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.api.routes.subscriber_auth import require_subscriber
from open_node.domain.subscription_templates import (
    TemplateFormat,
    TemplateList,
    TemplatePreview,
    TemplatePreviewRead,
    TemplateRead,
    TemplateRemove,
    TemplateSettings,
    TemplateSettingsUpdate,
    TemplateUpdate,
    TemplateWrite,
)
from open_node.domain.subscriptions import SubscriptionClientFormat
from open_node.services.backup_runtime import protected_sync
from open_node.services.inventory import InventoryStore, SubscriptionUnavailableError
from open_node.services.template_rendering import DEFAULT_CLASH, render

router = APIRouter(
    route_class=BackupAPIRoute, prefix="/subscription-templates", tags=["subscription templates"]
)


@protected_sync
def actor(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith(request.app.state.settings.api_prefix + "/account/"):
        identity = require_subscriber(request)
        request.app.state.subscriber_permissions.require_page(identity.username, "templates")
        return identity.username
    require_administrator(request, response)
    return None


Actor = Annotated[str | None, Depends(actor)]


@router.get("", response_model=TemplateList)
def list_templates(request: Request, username: Actor):
    return request.app.state.inventory.subscription_templates().list(username)


@router.get("/starter")
def starter(username: Actor, format: TemplateFormat = "clash"):
    return {"format": format, "content": DEFAULT_CLASH}


@router.get("/settings", response_model=TemplateSettings)
def settings(
    request: Request, identity: Actor, username: Annotated[str | None, Query(max_length=80)] = None
):
    if identity is not None and username not in (None, identity):
        raise HTTPException(403, "Another subscriber's settings are private")
    return request.app.state.inventory.subscription_templates().get_settings(identity or username)


@router.put("/settings", response_model=TemplateSettings)
def save_settings(
    payload: TemplateSettingsUpdate,
    request: Request,
    identity: Actor,
    username: Annotated[str | None, Query(max_length=80)] = None,
):
    if identity is not None and username not in (None, identity):
        raise HTTPException(403, "Another subscriber's settings are private")
    return request.app.state.inventory.subscription_templates().save_settings(
        payload, identity or username, identity
    )


@router.post("/preview", response_model=TemplatePreviewRead)
def preview(payload: TemplatePreview, request: Request, username: Actor):
    store = request.app.state.inventory
    if username is not None and payload.username not in (None, username):
        raise HTTPException(403, "Another subscriber's credentials are private")
    with store._session() as session:
        if not store.subscription_templates().allowed(session, username):
            raise HTTPException(403, "Template editing is not permitted")
        target = username or payload.username
        warnings, excluded = [], 0
        if target:
            user = store.subscription_templates().user(session, target)
            try:
                plan = store._available_subscription_plan(session, user)
            except SubscriptionUnavailableError as exc:
                raise HTTPException(404, str(exc)) from exc
            proxies, report = store._prepare_subscription_format(
                session, user, plan, SubscriptionClientFormat(payload.format), payload.content
            )
            excluded = sum(not node.available for node in report.nodes)
            warnings.extend(report.warnings)
            warnings.extend(
                node.name + ": " + node.reason for node in report.nodes if not node.available
            )
        else:
            proxies = [
                {
                    "name": "Example node",
                    "type": "vmess",
                    "server": "example.invalid",
                    "port": 443,
                    "uuid": "00000000-0000-4000-8000-000000000001",
                    "tls": True,
                }
            ]
            warnings.append("Preview uses an example node")
        content, notices = render(payload.content, payload.format, proxies)
        return TemplatePreviewRead(
            content=content,
            warnings=[*warnings, *notices],
            included_nodes=len(proxies),
            excluded_nodes=excluded,
        )


@router.post("", response_model=TemplateRead, status_code=201)
def create_template(payload: TemplateWrite, request: Request, username: Actor):
    return request.app.state.inventory.subscription_templates().write(payload, actor=username)


@router.get("/{identifier}", response_model=TemplateRead)
def detail(identifier: UUID, request: Request, username: Actor):
    return request.app.state.inventory.subscription_templates().detail(identifier, username)


@router.get("/{identifier}/file")
def download(identifier: UUID, request: Request, username: Actor):
    row = request.app.state.inventory.subscription_templates().detail(identifier, username)
    return Response(
        row.content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": InventoryStore.subscription_content_disposition(row.name),
        },
    )


@router.put("/{identifier}", response_model=TemplateRead)
def update_template(identifier: UUID, payload: TemplateUpdate, request: Request, username: Actor):
    return request.app.state.inventory.subscription_templates().write(payload, identifier, username)


@router.post("/{identifier}/remove", status_code=204)
def remove_template(identifier: UUID, payload: TemplateRemove, request: Request, username: Actor):
    request.app.state.inventory.subscription_templates().remove(identifier, payload, username)
