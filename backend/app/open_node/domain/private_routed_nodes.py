from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import AgentCommandRead, _strip_required_text


class PrivateRoutedNodeStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    REMOVING = "removing"
    FAILED = "failed"


class PrivateRoutedNodeAction(StrEnum):
    CREATE = "create"
    DELETE = "delete"


class PrivateRoutedPolicyRead(BaseModel):
    enabled: bool
    max_nodes: int
    daily_limit: int
    updated_at: datetime


class PrivateRoutedPolicyUpdate(BaseModel):
    enabled: bool
    max_nodes: int = Field(default=2, ge=1, le=20)
    daily_limit: int = Field(default=5, ge=1, le=100)


class PrivateRoutedCandidateRead(BaseModel):
    id: UUID
    name: str
    server_id: UUID
    protocol: str
    can_parent: bool
    can_target: bool


class PrivateRoutedNodeRead(BaseModel):
    id: UUID
    username: str
    name: str
    status: PrivateRoutedNodeStatus
    action: PrivateRoutedNodeAction
    server_id: UUID
    protocol: str
    parent_id: UUID
    parent_name: str
    target_node_id: UUID
    target_name: str
    change_set_id: UUID | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class PrivateRoutedNodeCreate(BaseModel):
    label: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9-]+$")
    parent_id: UUID
    target_node_id: UUID
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return _strip_required_text(value.strip(), "private route label")


class PrivateRoutedNodesResponse(BaseModel):
    policy: PrivateRoutedPolicyRead
    nodes: list[PrivateRoutedNodeRead]
    candidates: list[PrivateRoutedCandidateRead] = Field(default_factory=list)
    used_nodes: int
    actions_today: int
    license_required: Literal[False] = False


class PrivateRoutedNodeMutationResponse(BaseModel):
    node: PrivateRoutedNodeRead | None = None
    deleted_id: UUID | None = None
    commands: list[AgentCommandRead] = Field(default_factory=list)
    license_required: Literal[False] = False
