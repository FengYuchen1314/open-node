from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from open_node import __version__
from open_node.api.backup import BackupAPIRoute
from open_node.core.config import get_settings

router = APIRouter(route_class=BackupAPIRoute, tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    timestamp: datetime


class AppMeta(BaseModel):
    name: str
    version: str
    api_prefix: str
    license_required: bool
    short_links_enabled: bool
    stack: dict[str, str]


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        timestamp=datetime.now(tz=UTC),
    )


@router.get("/meta", response_model=AppMeta)
def meta(request: Request) -> AppMeta:
    settings = request.app.state.settings
    return AppMeta(
        name=settings.app_name,
        version=__version__,
        api_prefix=settings.api_prefix,
        license_required=settings.license_required,
        short_links_enabled=settings.short_links_enabled,
        stack={
            "backend": "fastapi",
            "frontend": "react-antd",
        },
    )
