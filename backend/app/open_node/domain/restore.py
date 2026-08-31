from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class RestoreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    id: UUID
    status: Literal["review_required", "reviewed"] = "review_required"
    created_at: datetime
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    invalidated_sessions: int = Field(ge=0)
    cancelled_agent_commands: int = Field(ge=0)
    cancelled_certificate_jobs: int = Field(ge=0)
    quarantined_files: int = Field(ge=0)
    reviewed_at: datetime | None = None


class RestoreStatus(BaseModel):
    blocked: bool = False
    restart_required: bool = False
    record: RestoreRecord | None = None


class RestoreReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    id: UUID
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: SecretStr = Field(default=SecretStr(""), max_length=64)
    confirm_original_stopped: Literal[True]
    confirm_configuration: Literal[True]
    confirm_trusted_backup: Literal[True]

    @field_validator("confirm_original_stopped", "confirm_configuration", "confirm_trusted_backup",
                     mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value
