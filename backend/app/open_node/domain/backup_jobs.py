"""Administrator-facing backup requests and safe status projections."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class BackupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    request_id: str = Field(min_length=36, max_length=36)
    recipient: str = Field(
        min_length=62, max_length=62, pattern=r"^age1[023456789acdefghjklmnpqrstuvwxyz]{58}$",
    )
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: SecretStr = Field(default=SecretStr(""), max_length=64)

    @field_validator("request_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        identifier = UUID(value)
        if identifier.version != 4 or str(identifier) != value:
            raise ValueError("A canonical UUID4 request identifier is required")
        return value


class BackupJobRead(BaseModel):
    id: str
    status: Literal["queued", "running", "ready", "failed", "expired", "cancelled"]
    created_at: datetime
    expires_at: datetime
    size: int | None = None
    sha256: str | None = None
    error_code: str | None = None
    restoration_ready: Literal[False] = False


class BackupJobsRead(BaseModel):
    available: bool
    unavailable_code: str | None
    jobs: list[BackupJobRead]
    max_completed: Literal[2] = 2
    ttl_seconds: Literal[900] = 900
    requires_two_factor: bool
    restoration_supported: Literal[False] = False
