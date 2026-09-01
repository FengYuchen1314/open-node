from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SubscriptionProfileRead(BaseModel):
    id: UUID
    owner_username: str
    assigned_usernames: list[str] = Field(default_factory=list)
    revision: str
    name: str
    description: str = ""
    node_ids: list[UUID] = Field(default_factory=list)
    clash_template_id: UUID | None = None
    surge_template_id: UUID | None = None
    custom_rules_enabled: bool = False
    selected_custom_rule_ids: list[UUID] = Field(default_factory=list)
    proxy_providers_enabled: bool = False
    selected_proxy_provider_ids: list[UUID] = Field(default_factory=list)
    override_scripts_enabled: bool = False
    selected_override_script_ids: list[UUID] = Field(default_factory=list)
    enabled: bool
    sort_order: int = 0
    source_type: str = "managed"
    source_filename: str = ""
    source_template_filename: str = ""
    legacy_source_id: int | None = None
    legacy_file_short_code: str | None = None
    legacy_custom_short_code: str | None = None
    migration_warnings: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SubscriptionProfilesResponse(BaseModel):
    profiles: list[SubscriptionProfileRead]
    license_required: Literal[False] = False


class SubscriptionProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    node_ids: list[UUID] = Field(default_factory=list, max_length=10000)
    clash_template_id: UUID | None = None
    surge_template_id: UUID | None = None
    custom_rules_enabled: bool = False
    selected_custom_rule_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    proxy_providers_enabled: bool = False
    selected_proxy_provider_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    override_scripts_enabled: bool = False
    selected_override_script_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    assigned_usernames: list[str] = Field(default_factory=list, max_length=10000)
    enabled: bool
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class SubscriberSubscriptionProfileRead(BaseModel):
    id: UUID
    name: str
    description: str = ""
    subscription_url: str
    short_code: str
    enabled: bool
    expires_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class SubscriberSubscriptionProfilesResponse(BaseModel):
    profiles: list[SubscriberSubscriptionProfileRead]
    license_required: Literal[False] = False
