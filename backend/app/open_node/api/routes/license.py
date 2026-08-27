from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/license", tags=["license"])


class LicenseStatus(BaseModel):
    edition: Literal["free"] = "free"
    license_required: bool = False
    paid_entitlements_enabled: bool = False
    external_license_server: None = None
    feature_gates: list[str] = Field(default_factory=list)
    message: str = "Open Node does not require activation keys or paid licenses."


@router.get("/status", response_model=LicenseStatus)
def get_license_status() -> LicenseStatus:
    return LicenseStatus()
