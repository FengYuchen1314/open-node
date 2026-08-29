from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator

from open_node.domain.inventory import AgentCommandRead
from open_node.domain.subscriptions import (
    ProductUserRead,
    SubscriptionPlanRead,
    _strip_optional_text,
    _strip_required_text,
)


class RegistrationInvitationStatus(StrEnum):
    ACTIVE = "active"
    USED = "used"
    REVOKED = "revoked"
    EXPIRED = "expired"


class RegistrationInvitationCreate(BaseModel):
    plan_id: UUID
    expires_minutes: int = Field(default=1440, ge=5, le=10080)


class RegistrationInvitationRead(BaseModel):
    id: UUID
    token_hint: str
    plan_id: UUID
    plan_name: str
    status: RegistrationInvitationStatus
    used_by: str | None = None
    expires_at: datetime
    used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class RegistrationInvitationsResponse(BaseModel):
    invitations: list[RegistrationInvitationRead]
    license_required: Literal[False] = False


class RegistrationInvitationCreateResponse(BaseModel):
    invitation: RegistrationInvitationRead
    registration_url: str
    license_required: Literal[False] = False


class RegistrationClaim(BaseModel):
    token: SecretStr = Field(min_length=32, max_length=256)
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=12, max_length=1024)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return _strip_required_text(value, "username")

    @field_validator("email", "display_name")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "profile field")


class RegistrationClaimResponse(BaseModel):
    user: ProductUserRead
    plan: SubscriptionPlanRead
    commands: list[AgentCommandRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False
