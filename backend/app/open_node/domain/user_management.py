from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import AgentCommandRead
from open_node.domain.subscriptions import (
    ProductUserRead,
    SubscriptionAccessResponse,
    SubscriptionAccessServerRead,
    _strip_required_text,
)
from open_node.domain.user_limits import UserLimitOverrides, UserLimitsRead


class UserUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    remark: str = Field(default="", max_length=1000)
    is_active: bool = Field(strict=True)
    limit_overrides: UserLimitOverrides | None = None
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_runtime_restart: Literal[True]

    @field_validator("display_name")
    @classmethod
    def name(cls, value):
        return _strip_required_text(value, "display name")

    @field_validator("email")
    @classmethod
    def contact(cls, value):
        return _strip_required_text(value, "email") if value and value.strip() else None


class UserRemoval(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_name: str = Field(min_length=1, max_length=80)
    acknowledge_runtime_restart: Literal[True]
    acknowledge_unmanaged_credentials: bool = Field(default=False, strict=True)


class UserManagementRead(BaseModel):
    user: ProductUserRead
    revision: str
    credential_count: int
    blockers: list[str]
    warnings: list[str]
    access: SubscriptionAccessResponse
    limits: UserLimitsRead
    license_required: Literal[False] = False


class UserManagementResult(UserManagementRead):
    commands: list[AgentCommandRead]


class UserRemovalRead(BaseModel):
    id: UUID
    username: str
    status: Literal["pending", "failed", "completed"]
    requested_at: datetime
    completed_at: datetime | None = None
    servers: list[SubscriptionAccessServerRead]
    warnings: list[str]
    commands: list[AgentCommandRead] = Field(default_factory=list)
    license_required: Literal[False] = False
