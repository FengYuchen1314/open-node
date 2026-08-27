from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import AgentCommandRead


def _strip_required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is empty")
    if any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{field_name} must not contain control characters")
    return normalized


def _strip_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _strip_required_text(value, field_name)


def _ensure_json_object(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return value


class ProductUserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class ManagedNodeType(StrEnum):
    PHYSICAL = "physical"
    ROUTED = "routed"


class SubscriptionTrafficMode(StrEnum):
    ONEWAY = "oneway"
    TWOWAY = "twoway"


class SubscriptionClientFormat(StrEnum):
    CLASH = "clash"
    SING_BOX = "sing-box"
    URI_LIST = "uri-list"
    BASE64 = "base64"


class ProductUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)
    role: ProductUserRole = ProductUserRole.USER
    is_active: bool = True

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _strip_required_text(value, "username")

    @field_validator("email", "display_name")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "text")


class ProductUserRead(BaseModel):
    username: str
    email: str | None = None
    display_name: str
    role: ProductUserRole
    is_active: bool
    current_plan_id: UUID | None = None
    plan_started_at: datetime | None = None
    plan_expires_at: datetime | None = None
    is_reset: bool = False
    reset_day: int = 0
    created_at: datetime
    updated_at: datetime


class ProductUsersResponse(BaseModel):
    users: list[ProductUserRead]
    license_required: Literal[False] = False


class ProductUserResponse(BaseModel):
    user: ProductUserRead
    license_required: Literal[False] = False


class ProductUserSubscriptionTokenRead(BaseModel):
    username: str
    token: str
    short_code: str
    subscription_url: str
    short_url: str
    created_at: datetime
    updated_at: datetime


class ProductUserSubscriptionTokenResponse(BaseModel):
    subscription: ProductUserSubscriptionTokenRead
    license_required: Literal[False] = False


class ManagedNodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    server_id: UUID
    protocol: str = Field(min_length=1, max_length=40)
    node_type: ManagedNodeType = ManagedNodeType.PHYSICAL
    inbound_tag: str | None = Field(default=None, max_length=255)
    routed_outbound_tag: str | None = Field(default=None, max_length=255)
    routed_rule_marktag: str | None = Field(default=None, max_length=255)
    tag: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=24)
    enabled: bool = True
    client_template: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "protocol")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _strip_required_text(value, "node field")

    @field_validator("inbound_tag", "routed_outbound_tag", "routed_rule_marktag", "tag")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "node field")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = _strip_required_text(raw, "tag")
            if tag not in seen:
                tags.append(tag)
                seen.add(tag)
        return tags

    @field_validator("client_template", "config")
    @classmethod
    def validate_json_objects(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _ensure_json_object(value, "node JSON")


class ManagedNodeRead(ManagedNodeCreate):
    id: UUID
    created_at: datetime
    updated_at: datetime


class ManagedNodesResponse(BaseModel):
    nodes: list[ManagedNodeRead]
    license_required: Literal[False] = False


class ManagedNodeResponse(BaseModel):
    node: ManagedNodeRead
    license_required: Literal[False] = False


class SubscriptionCredentialRead(BaseModel):
    id: UUID
    username: str
    node_id: UUID
    server_id: UUID
    inbound_tag: str | None = None
    protocol: str
    email: str
    credential: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProductUserCredentialsResponse(BaseModel):
    username: str
    credentials: list[SubscriptionCredentialRead]
    license_required: Literal[False] = False


class SubscriptionTrafficEntryRead(BaseModel):
    username: str
    server_id: UUID
    email: str
    upload: int
    download: int
    total: int
    last_reported_at: datetime | None = None
    updated_at: datetime


class ProductUserTrafficResponse(BaseModel):
    username: str
    upload: int
    download: int
    total: int
    entries: list[SubscriptionTrafficEntryRead]
    license_required: Literal[False] = False


class SubscriptionTemplatePresetRead(BaseModel):
    id: str
    name: str
    description: str
    protocol: str
    node_type: ManagedNodeType = ManagedNodeType.PHYSICAL
    inbound_tag: str | None = None
    routed_outbound_tag: str | None = None
    routed_rule_marktag: str | None = None
    tag: str | None = None
    tags: list[str] = Field(default_factory=list)
    client_template: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class SubscriptionTemplatePresetsResponse(BaseModel):
    presets: list[SubscriptionTemplatePresetRead]
    license_required: Literal[False] = False


class SubscriptionTemplatePresetApplyRequest(BaseModel):
    server_id: UUID
    name: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    inbound_tag: str | None = Field(default=None, max_length=255)
    routed_outbound_tag: str | None = Field(default=None, max_length=255)
    routed_rule_marktag: str | None = Field(default=None, max_length=255)
    tag: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=24)
    enabled: bool = True

    @field_validator(
        "name",
        "host",
        "inbound_tag",
        "routed_outbound_tag",
        "routed_rule_marktag",
        "tag",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "preset field")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        tags: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = _strip_required_text(raw, "tag")
            if tag not in seen:
                tags.append(tag)
                seen.add(tag)
        return tags


class SubscriptionCatalogUserEntry(BaseModel):
    username: str
    email: str | None = None
    display_name: str | None = None
    role: ProductUserRole = ProductUserRole.USER
    is_active: bool = True
    current_plan_name: str | None = None
    plan_started_at: datetime | None = None
    plan_expires_at: datetime | None = None
    is_reset: bool = False
    reset_day: int = Field(default=0, ge=0, le=31)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _strip_required_text(value, "username")


class SubscriptionCatalogNodeEntry(BaseModel):
    name: str
    server_name: str
    protocol: str
    node_type: ManagedNodeType = ManagedNodeType.PHYSICAL
    inbound_tag: str | None = None
    routed_outbound_tag: str | None = None
    routed_rule_marktag: str | None = None
    tag: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    client_template: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "server_name", "protocol")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _strip_required_text(value, "catalog node field")


class SubscriptionCatalogPlanEntry(BaseModel):
    name: str
    description: str = ""
    traffic_limit_gb: float = Field(gt=0)
    cycle_days: int = Field(default=30, gt=0)
    is_reset: bool = False
    reset_day: int = Field(default=0, ge=0, le=31)
    node_names: list[str] = Field(default_factory=list)
    node_multipliers: dict[str, float] = Field(default_factory=dict)
    node_speed_limits: dict[str, float] = Field(default_factory=dict)
    node_device_limits: dict[str, int] = Field(default_factory=dict)
    speed_limit_mbps: float = Field(default=0, ge=0)
    device_limit: int = Field(default=0, ge=0)
    traffic_mode: SubscriptionTrafficMode = SubscriptionTrafficMode.ONEWAY

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _strip_required_text(value, "plan name")


class SubscriptionCatalogCredentialEntry(BaseModel):
    username: str
    node_name: str
    server_name: str
    inbound_tag: str | None = None
    protocol: str
    email: str
    credential: dict[str, Any]


class SubscriptionCatalogBundle(BaseModel):
    version: int = 1
    exported_at: datetime | None = None
    users: list[SubscriptionCatalogUserEntry] = Field(default_factory=list)
    nodes: list[SubscriptionCatalogNodeEntry] = Field(default_factory=list)
    plans: list[SubscriptionCatalogPlanEntry] = Field(default_factory=list)
    credentials: list[SubscriptionCatalogCredentialEntry] = Field(default_factory=list)


class SubscriptionCatalogExportResponse(BaseModel):
    catalog: SubscriptionCatalogBundle
    license_required: Literal[False] = False


class SubscriptionCatalogImportRequest(BaseModel):
    catalog: SubscriptionCatalogBundle
    server_map: dict[str, UUID] = Field(default_factory=dict)
    import_credentials: bool = False


class SubscriptionCatalogImportSummary(BaseModel):
    created_users: int = 0
    updated_users: int = 0
    created_nodes: int = 0
    updated_nodes: int = 0
    created_plans: int = 0
    updated_plans: int = 0
    imported_credentials: int = 0
    warnings: list[str] = Field(default_factory=list)


class SubscriptionCatalogImportResponse(BaseModel):
    summary: SubscriptionCatalogImportSummary
    license_required: Literal[False] = False


class SubscriptionPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    traffic_limit_gb: float = Field(gt=0)
    cycle_days: int = Field(default=30, gt=0)
    is_reset: bool = False
    reset_day: int = Field(default=0, ge=0, le=31)
    node_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    node_multipliers: dict[UUID, float] = Field(default_factory=dict)
    node_speed_limits: dict[UUID, float] = Field(default_factory=dict)
    node_device_limits: dict[UUID, int] = Field(default_factory=dict)
    speed_limit_mbps: float = Field(default=0, ge=0)
    device_limit: int = Field(default=0, ge=0)
    traffic_mode: SubscriptionTrafficMode = SubscriptionTrafficMode.ONEWAY

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _strip_required_text(value, "name")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return value.strip()


class SubscriptionPlanRead(SubscriptionPlanCreate):
    id: UUID
    traffic_limit_bytes: int
    created_at: datetime
    updated_at: datetime


class SubscriptionPlansResponse(BaseModel):
    plans: list[SubscriptionPlanRead]
    license_required: Literal[False] = False


class SubscriptionPlanResponse(BaseModel):
    plan: SubscriptionPlanRead
    license_required: Literal[False] = False


class SubscriptionPlanAssignRequest(BaseModel):
    plan_id: UUID
    start_date: date | None = None
    expire_date: date | None = None
    is_reset: bool | None = None
    reset_day: int | None = Field(default=None, ge=1, le=31)
    queue_agent_commands: bool = False
    no_restart: bool = True
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)


class SubscriptionProvisionBatch(BaseModel):
    server_id: UUID
    server_name: str
    body: dict[str, Any]


class SubscriptionPlanAssignResponse(BaseModel):
    user: ProductUserRead
    plan: SubscriptionPlanRead
    provisioning_batches: list[SubscriptionProvisionBatch]
    commands: list[AgentCommandRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False
