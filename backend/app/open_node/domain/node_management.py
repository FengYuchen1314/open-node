from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import AgentCommandRead
from open_node.domain.subscriptions import (
    ManagedNodeCreate,
    ManagedNodeRead,
    SubscriptionAccessResponse,
)


class NodeUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=120)
    tag: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=24)
    enabled: bool = Field(strict=True)
    parent_id: UUID | None = None
    target_node_id: UUID | None = None
    client_template: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_runtime_restart: Literal[True]

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return ManagedNodeCreate.validate_required_text(value)

    @field_validator("tag")
    @classmethod
    def valid_tag(cls, value):
        return ManagedNodeCreate.validate_optional_text(value)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value):
        return ManagedNodeCreate.validate_tags(value)

    @field_validator("client_template", "config")
    @classmethod
    def valid_json(cls, value):
        return ManagedNodeCreate.validate_json_objects(value)


class NodeRemoval(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_name: str
    acknowledge_runtime_restart: Literal[True]
    acknowledge_unmanaged_resources: bool = Field(default=False, strict=True)


class NodeReference(BaseModel):
    id: UUID
    name: str


class NodeRemovalServer(BaseModel):
    server_id: UUID
    server_name: str
    inbound_tags: list[str]
    outbound_tags: list[str]
    retained_inbound_tags: list[str] = Field(default_factory=list)
    retained_outbound_tags: list[str] = Field(default_factory=list)
    phase: Literal["withdrawing", "preview", "apply", "inspect", "completed"] = "withdrawing"
    command_id: UUID | None = None
    error: str | None = None
    impact: dict[str, Any] | None = None


class NodeManagementRead(BaseModel):
    node: ManagedNodeRead
    revision: str
    nodes: list[NodeReference]
    plans: list[NodeReference]
    credential_count: int
    servers: list[NodeRemovalServer]
    blockers: list[str]
    warnings: list[str]
    access: list[SubscriptionAccessResponse]
    license_required: Literal[False] = False


class NodeManagementResult(NodeManagementRead):
    commands: list[AgentCommandRead]


class NodeRemovalRead(BaseModel):
    id: UUID
    node_id: UUID
    name: str
    node_ids: list[UUID]
    status: Literal["pending", "failed", "completed"]
    requested_at: datetime
    completed_at: datetime | None = None
    servers: list[NodeRemovalServer]
    warnings: list[str]
    commands: list[AgentCommandRead] = Field(default_factory=list)
    license_required: Literal[False] = False
