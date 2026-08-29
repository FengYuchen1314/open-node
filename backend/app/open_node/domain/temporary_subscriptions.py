from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class TemporarySubscriptionCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    label: str = Field(default="Temporary subscription", min_length=1, max_length=120)
    node_ids: list[UUID] = Field(min_length=1, max_length=10000)
    max_access: int = Field(default=1, ge=1, le=100)
    expires_in_seconds: int = Field(default=300, ge=60, le=3600)

    @model_validator(mode="after")
    def clean(self):
        self.username = self.username.strip()
        self.label = self.label.strip()
        if not self.username:
            raise ValueError("username is required")
        if not self.label:
            raise ValueError("temporary subscription label is required")
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("temporary subscription nodes must be distinct")
        return self


class TemporarySubscriptionRead(BaseModel):
    id: UUID
    username: str
    label: str
    node_ids: list[UUID]
    max_access: int
    access_count: int
    expires_at: datetime
    status: Literal["active", "expired", "exhausted"]
    subscription_url: str
    created_at: datetime
    updated_at: datetime


class TemporarySubscriptionsResponse(BaseModel):
    subscriptions: list[TemporarySubscriptionRead]
    license_required: Literal[False] = False


class TemporarySubscriptionDeleteResponse(BaseModel):
    id: UUID
    deleted: Literal[True] = True
    license_required: Literal[False] = False
