from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import AgentCommandCreate, AgentCommandRead


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
