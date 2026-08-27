from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from open_node import __version__
from open_node.core.config import get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    timestamp: datetime


class AppMeta(BaseModel):
    name: str
    version: str
    api_prefix: str
    license_required: bool
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
def meta() -> AppMeta:
    settings = get_settings()
    return AppMeta(
        name=settings.app_name,
        version=__version__,
        api_prefix=settings.api_prefix,
        license_required=settings.license_required,
        stack={
            "backend": "fastapi",
            "frontend": "vue3-vuetify",
        },
    )
