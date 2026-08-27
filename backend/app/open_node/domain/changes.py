from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import (
    AgentCommandCreate,
    AgentCommandRead,
    _ensure_json_serializable_config,
    _strip_optional_text,
    _strip_required_text,
)


class AgentChangeSetStatus(StrEnum):
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    ROLLBACK_QUEUED = "rollback_queued"


class AgentChangeSetStepCreate(BaseModel):
    server_id: UUID
    label: str = Field(default="", max_length=160)
    forward: AgentCommandCreate
    rollback: AgentCommandCreate | None = None

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return value.strip()


class AgentChangeSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    rollback_on_failure: bool = True
    dispatch: bool = False
    steps: list[AgentChangeSetStepCreate] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name is empty")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class AgentRoutedOutboundChangeSetCreate(BaseModel):
    server_id: UUID
    inbound_tag: str = Field(min_length=1, max_length=255)
    inbound_protocol: str = Field(default="vless", min_length=1, max_length=40)
    label: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    outbound: dict[str, Any]
    parent_ref: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    admin_username: str = Field(default="admin", min_length=1, max_length=120)
    admin_email: str | None = Field(default=None, max_length=255)
    outbound_tag: str | None = Field(default=None, max_length=255)
    marktag: str | None = Field(default=None, max_length=255)
    node_name: str | None = Field(default=None, max_length=160)
    client: dict[str, Any] | None = None
    sniffing_exclude_domains: list[str] = Field(default_factory=list, max_length=50)
    add_reality_sniffing_excludes: bool = True
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    rollback_on_failure: bool = True
    dispatch: bool = False

    @field_validator("inbound_tag", "inbound_protocol", "label", "admin_username", mode="before")
    @classmethod
    def normalize_required_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "parent_ref",
        "admin_email",
        "outbound_tag",
        "marktag",
        "node_name",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("inbound_tag", "inbound_protocol", "label", "admin_username")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _strip_required_text(value, "routed outbound field")

    @field_validator("parent_ref", "admin_email", "outbound_tag", "marktag", "node_name")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "routed outbound field")

    @field_validator("outbound", "client")
    @classmethod
    def validate_json_payload(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _ensure_json_serializable_config(value, "routed outbound payload")

    @field_validator("sniffing_exclude_domains")
    @classmethod
    def normalize_sniffing_exclude_domains(cls, value: list[str]) -> list[str]:
        domains: list[str] = []
        seen: set[str] = set()
        for raw in value:
            domain = _strip_required_text(raw, "sniffing domain").lower()
            if domain in seen:
                continue
            seen.add(domain)
            domains.append(domain)
        return domains


class AgentChangeSetRollbackRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return value.strip()


class AgentChangeSetStepRead(BaseModel):
    id: UUID
    change_set_id: UUID
    sequence: int
    server_id: UUID
    label: str
    forward: AgentCommandCreate
    rollback: AgentCommandCreate | None = None
    forward_command: AgentCommandRead | None = None
    rollback_command: AgentCommandRead | None = None
    created_at: datetime
    updated_at: datetime


class AgentChangeSetRead(BaseModel):
    id: UUID
    name: str
    description: str
    status: AgentChangeSetStatus
    rollback_on_failure: bool
    rollback_reason: str = ""
    steps: list[AgentChangeSetStepRead]
    created_at: datetime
    updated_at: datetime


class AgentChangeSetsResponse(BaseModel):
    change_sets: list[AgentChangeSetRead]
    license_required: Literal[False] = False


class AgentChangeSetResponse(BaseModel):
    change_set: AgentChangeSetRead
    commands: list[AgentCommandRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False
