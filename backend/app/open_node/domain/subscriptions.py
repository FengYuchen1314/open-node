from datetime import date, datetime
from enum import StrEnum
from math import isfinite
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
    XRAY = "xray"
    URI_LIST = "uri-list"
    BASE64 = "base64"


class SubscriptionFormatNode(BaseModel):
    node_id: UUID
    name: str
    protocol: str
    available: bool
    reason: str | None = None


class SubscriptionFormatPreview(BaseModel):
    username: str
    client_format: SubscriptionClientFormat
    nodes: list[SubscriptionFormatNode]
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


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
    last_traffic_reset_at: datetime | None = None
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


class XrayRuntimeNodeDraft(BaseModel):
    source_index: int = Field(ge=0)
    source_tag: str | None = None
    source_display_name: str
    draft: ManagedNodeCreate
    create_available: bool = True
    existing_node_id: UUID | None = None
    warnings: list[str] = Field(default_factory=list)


class XrayRuntimeNodeDraftsResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    drafts: list[XrayRuntimeNodeDraft] = Field(default_factory=list)
    license_required: Literal[False] = False


class XrayRuntimeNodeCreateRequest(BaseModel):
    source_index: int | None = Field(default=None, ge=0)
    inbound_tag: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=120)
    host: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = Field(default=None, max_length=24)
    enabled: bool = True

    @field_validator("inbound_tag", "display_name", "name", "host")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "runtime node field")

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


class XrayRuntimeNodeImportRequest(BaseModel):
    source_indexes: list[int] | None = Field(default=None, max_length=1000)
    host: str | None = Field(default=None, max_length=255)
    extra_tags: list[str] = Field(default_factory=list, max_length=24)
    enabled: bool = True

    @field_validator("host")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "runtime node field")

    @field_validator("source_indexes")
    @classmethod
    def validate_source_indexes(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        indexes: list[int] = []
        seen: set[int] = set()
        for index in value:
            if index < 0:
                raise ValueError("runtime node source index must be non-negative")
            if index not in seen:
                indexes.append(index)
                seen.add(index)
        return indexes

    @field_validator("extra_tags")
    @classmethod
    def validate_extra_tags(cls, value: list[str]) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = _strip_required_text(raw, "tag")
            if tag not in seen:
                tags.append(tag)
                seen.add(tag)
        return tags


class XrayRuntimeNodeImportSkipped(BaseModel):
    source_index: int = Field(ge=0)
    source_tag: str | None = None
    source_display_name: str
    warnings: list[str] = Field(default_factory=list)


class XrayRuntimeNodeImportResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    created_nodes: list[ManagedNodeRead] = Field(default_factory=list)
    existing_nodes: list[ManagedNodeRead] = Field(default_factory=list)
    skipped: list[XrayRuntimeNodeImportSkipped] = Field(default_factory=list)
    created_count: int = 0
    existing_count: int = 0
    skipped_count: int = 0
    license_required: Literal[False] = False


class XrayRuntimeNodeReconciliationDrift(BaseModel):
    field: str
    runtime_value: str | int | bool | list[str] | None = None
    managed_value: str | int | bool | list[str] | None = None


class XrayRuntimeNodeReconciliationRuntimeEntry(BaseModel):
    source_index: int = Field(ge=0)
    source_tag: str | None = None
    source_display_name: str
    protocol: str
    port: int | None = None
    status: Literal["managed", "unmanaged", "unavailable"]
    managed_node_id: UUID | None = None
    managed_node_name: str | None = None
    warnings: list[str] = Field(default_factory=list)


class XrayRuntimeNodeReconciliationManagedEntry(BaseModel):
    node_id: UUID
    node_name: str
    protocol: str
    node_type: ManagedNodeType
    inbound_tag: str | None = None
    enabled: bool
    status: Literal["in_sync", "stale", "missing_runtime", "catalog_only"]
    runtime_source_index: int | None = Field(default=None, ge=0)
    runtime_display_name: str | None = None
    drifts: list[XrayRuntimeNodeReconciliationDrift] = Field(default_factory=list)


class XrayRuntimeNodeReconciliationResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    runtime_count: int = 0
    managed_node_count: int = 0
    managed_runtime_count: int = 0
    unmanaged_runtime_count: int = 0
    unavailable_runtime_count: int = 0
    in_sync_count: int = 0
    stale_count: int = 0
    missing_runtime_count: int = 0
    catalog_only_count: int = 0
    runtime_entries: list[XrayRuntimeNodeReconciliationRuntimeEntry] = Field(default_factory=list)
    managed_entries: list[XrayRuntimeNodeReconciliationManagedEntry] = Field(default_factory=list)
    license_required: Literal[False] = False


class XrayRuntimeNodeSyncRequest(BaseModel):
    source_index: int | None = Field(default=None, ge=0)


class XrayRuntimeNodeSyncResponse(BaseModel):
    server_id: UUID
    node: ManagedNodeRead
    source_index: int = Field(ge=0)
    source_tag: str | None = None
    source_display_name: str
    updated_fields: list[str] = Field(default_factory=list)
    drifts_before: list[XrayRuntimeNodeReconciliationDrift] = Field(default_factory=list)
    drifts_after: list[XrayRuntimeNodeReconciliationDrift] = Field(default_factory=list)
    license_required: Literal[False] = False


class XrayRuntimeCredentialReconciliationEntry(BaseModel):
    node_id: UUID
    node_name: str
    protocol: str
    inbound_tag: str | None = None
    enabled: bool
    runtime_source_index: int | None = Field(default=None, ge=0)
    runtime_display_name: str | None = None
    expected_emails: list[str] = Field(default_factory=list)
    runtime_emails: list[str] = Field(default_factory=list)
    missing_runtime_emails: list[str] = Field(default_factory=list)
    extra_runtime_emails: list[str] = Field(default_factory=list)
    status: Literal[
        "in_sync",
        "missing_runtime",
        "missing_runtime_clients",
        "extra_runtime_clients",
        "drift",
    ]


class XrayRuntimeCredentialReconciliationResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    node_count: int = 0
    expected_credential_count: int = 0
    matched_runtime_client_count: int = 0
    in_sync_count: int = 0
    missing_runtime_count: int = 0
    out_of_sync_count: int = 0
    missing_runtime_client_count: int = 0
    extra_runtime_client_count: int = 0
    entries: list[XrayRuntimeCredentialReconciliationEntry] = Field(default_factory=list)
    license_required: Literal[False] = False


class XrayRuntimeCredentialRepairRequest(BaseModel):
    node_ids: list[UUID] | None = Field(default=None, max_length=100)
    queue_agent_commands: bool = False
    queue_scan_after_apply: bool = False
    no_restart: bool = True
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("node_ids")
    @classmethod
    def validate_node_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is None:
            return None
        node_ids: list[UUID] = []
        seen: set[UUID] = set()
        for node_id in value:
            if node_id in seen:
                continue
            seen.add(node_id)
            node_ids.append(node_id)
        return node_ids


class XrayRuntimeCredentialRepairEntry(BaseModel):
    node_id: UUID
    node_name: str
    protocol: str
    inbound_tag: str
    runtime_source_index: int = Field(ge=0)
    runtime_display_name: str
    emails: list[str] = Field(default_factory=list)


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


class SubscriptionQuotaStatusRead(BaseModel):
    username: str
    is_active: bool
    has_plan: bool
    available: bool
    expired: bool
    over_quota: bool
    reset_enabled: bool
    reset_due: bool
    upload: int
    download: int
    charged_usage_bytes: int
    traffic_limit_bytes: int
    remaining_bytes: int
    percent_used: float
    reset_day: int = 0
    plan_id: UUID | None = None
    plan_name: str | None = None
    traffic_mode: SubscriptionTrafficMode | None = None
    plan_started_at: datetime | None = None
    plan_expires_at: datetime | None = None
    reset_due_at: datetime | None = None
    next_reset_at: datetime | None = None
    last_traffic_reset_at: datetime | None = None


class SubscriptionQuotaStatusResponse(BaseModel):
    quota: SubscriptionQuotaStatusRead
    license_required: Literal[False] = False


class SubscriptionDueTrafficResetRequest(BaseModel):
    now: datetime | None = None
    dry_run: bool = False


class SubscriptionDueTrafficResetSummary(BaseModel):
    checked_users: int = 0
    reset_users: int = 0
    skipped_users: int = 0
    usernames: list[str] = Field(default_factory=list)
    dry_run: bool = False
    warnings: list[str] = Field(default_factory=list)


class SubscriptionDueTrafficResetResponse(BaseModel):
    summary: SubscriptionDueTrafficResetSummary
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
    last_traffic_reset_at: datetime | None = None

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
    speed_limit_mbps: float = Field(default=0, ge=0, le=(1 << 50) / 125000, allow_inf_nan=False)
    device_limit: int = Field(default=0, ge=0, le=1_000_000)
    traffic_mode: SubscriptionTrafficMode = SubscriptionTrafficMode.ONEWAY

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _strip_required_text(value, "plan name")

    @field_validator("node_speed_limits")
    @classmethod
    def valid_speed_map(cls, value):
        if any(
            not isfinite(rate) or rate < 0 or 0 < rate * 125000 < 1 or rate * 125000 > 1 << 50
            for rate in value.values()
        ):
            raise ValueError("Node speed limits must be finite nonnegative rates")
        return value

    @field_validator("speed_limit_mbps")
    @classmethod
    def valid_speed(cls, value):
        if 0 < value * 125000 < 1:
            raise ValueError("A positive speed limit must be at least one byte per second")
        return value

    @field_validator("node_device_limits")
    @classmethod
    def valid_connection_map(cls, value):
        if any(not 0 <= limit <= 1_000_000 for limit in value.values()):
            raise ValueError("Node connection limits must be between 0 and 1000000")
        return value


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
    speed_limit_mbps: float = Field(default=0, ge=0, le=(1 << 50) / 125000, allow_inf_nan=False)
    device_limit: int = Field(default=0, ge=0, le=1_000_000)
    traffic_mode: SubscriptionTrafficMode = SubscriptionTrafficMode.ONEWAY

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _strip_required_text(value, "name")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("node_speed_limits")
    @classmethod
    def valid_speed_map(cls, value):
        return SubscriptionCatalogPlanEntry.valid_speed_map(value)

    @field_validator("speed_limit_mbps")
    @classmethod
    def valid_speed(cls, value):
        return SubscriptionCatalogPlanEntry.valid_speed(value)

    @field_validator("node_device_limits")
    @classmethod
    def valid_connection_map(cls, value):
        return SubscriptionCatalogPlanEntry.valid_connection_map(value)


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


class XrayRuntimeCredentialRepairResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    entries: list[XrayRuntimeCredentialRepairEntry] = Field(default_factory=list)
    provisioning_batches: list[SubscriptionProvisionBatch] = Field(default_factory=list)
    commands: list[AgentCommandRead] = Field(default_factory=list)
    scan_command: AgentCommandRead | None = None
    planned_client_count: int = 0
    batch_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


class XrayRuntimeCredentialCleanupRequest(BaseModel):
    node_ids: list[UUID] | None = Field(default=None, max_length=100)
    queue_agent_commands: bool = False
    queue_scan_after_apply: bool = False
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("node_ids")
    @classmethod
    def validate_node_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        return XrayRuntimeCredentialRepairRequest.validate_node_ids(value)


class XrayRuntimeCredentialCleanupEntry(BaseModel):
    node_id: UUID
    node_name: str
    protocol: str
    inbound_tag: str
    runtime_source_index: int = Field(ge=0)
    runtime_display_name: str
    emails: list[str] = Field(default_factory=list)


class XrayRuntimeCredentialCleanupCommand(BaseModel):
    node_id: UUID
    node_name: str
    body: dict[str, Any]


class XrayRuntimeCredentialCleanupResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    entries: list[XrayRuntimeCredentialCleanupEntry] = Field(default_factory=list)
    command_previews: list[XrayRuntimeCredentialCleanupCommand] = Field(default_factory=list)
    commands: list[AgentCommandRead] = Field(default_factory=list)
    scan_command: AgentCommandRead | None = None
    planned_client_count: int = 0
    command_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


class SubscriptionPlanAssignResponse(BaseModel):
    user: ProductUserRead
    plan: SubscriptionPlanRead
    provisioning_batches: list[SubscriptionProvisionBatch]
    commands: list[AgentCommandRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False
