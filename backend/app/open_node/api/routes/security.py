"""Administrator security settings, event history and IP-ban routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from open_node.api.auth import require_administrator
from open_node.api.backup import BackupAPIRoute
from open_node.domain.security import (
    SecurityBanCreate,
    SecurityBanRead,
    SecurityBansRead,
    SecurityEventKind,
    SecurityEventsRead,
    SecuritySettingsRead,
    SecuritySettingsUpdate,
)
from open_node.services.auth import SessionIdentity

router = APIRouter(route_class=BackupAPIRoute, prefix="/security", tags=["security"])


@router.get("/events", response_model=SecurityEventsRead)
def events(
    request: Request,
    kind: SecurityEventKind | None = None,
    ip: str | None = Query(default=None, min_length=2, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1_000_000),
):
    return request.app.state.security.events(kind=kind, ip=ip, limit=limit, offset=offset)


@router.get("/bans", response_model=SecurityBansRead)
def bans(request: Request):
    return request.app.state.security.bans()


@router.post("/bans", response_model=SecurityBanRead, status_code=201)
def create_ban(
    payload: SecurityBanCreate,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    return request.app.state.security.ban(
        payload.ip, permanent=payload.permanent, actor=identity.username,
    )


@router.delete("/bans/{ip}", status_code=204)
def remove_ban(
    ip: str,
    request: Request,
    identity: Annotated[SessionIdentity, Depends(require_administrator)],
):
    request.app.state.security.unban(ip, actor=identity.username)


@router.get("/settings", response_model=SecuritySettingsRead)
def settings(request: Request):
    return request.app.state.security.settings()


@router.put("/settings", response_model=SecuritySettingsRead)
def update_settings(payload: SecuritySettingsUpdate, request: Request):
    return request.app.state.security.update_settings(payload)


def subscription_request_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def guard_public_subscription(request: Request) -> str:
    ip = subscription_request_ip(request)
    if request.app.state.security.is_blocked(ip):
        from fastapi import HTTPException

        raise HTTPException(404, "subscription not found")
    return ip


def failed_public_subscription(request: Request, ip: str, path: str) -> None:
    request.app.state.security.record_probe_failure(ip, path)


def successful_public_subscription(request: Request, ip: str) -> None:
    request.app.state.security.record_probe_success(ip)
