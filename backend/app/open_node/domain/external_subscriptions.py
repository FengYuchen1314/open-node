"""External-source management contracts; ordinary responses never contain credentials."""

import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, field_validator


def external_name(value: str) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    if not value or len(value) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("A name requires 1-160 characters without control characters")
    return value


class ExternalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ExternalSourceCreate(ExternalRequest):
    owner_username: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    url: SecretStr
    user_agent: SecretStr = SecretStr("")
    enabled: StrictBool = True

    _name = field_validator("name")(external_name)

    @field_validator("owner_username")
    @classmethod
    def owner(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("An existing subscriber is required")
        return value

    @field_validator("url")
    @classmethod
    def source_url(cls, value: SecretStr) -> SecretStr:
        from open_node.services.external_fetch import normalize_external_url

        return SecretStr(normalize_external_url(value.get_secret_value()))

    @field_validator("user_agent")
    @classmethod
    def source_agent(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) > 256 or any(not 32 <= ord(char) <= 126 for char in raw):
            raise ValueError("User agent must contain at most 256 printable ASCII characters")
        return value


class ExternalSourceUpdate(ExternalRequest):
    expected_revision: StrictInt = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    enabled: StrictBool
    # None preserves the secret. An empty user agent restores the documented default.
    url: SecretStr | None = None
    user_agent: SecretStr | None = None

    _name = field_validator("name")(external_name)

    @field_validator("url")
    @classmethod
    def source_url(cls, value: SecretStr | None) -> SecretStr | None:
        return ExternalSourceCreate.source_url(value) if value is not None else None

    @field_validator("user_agent")
    @classmethod
    def source_agent(cls, value: SecretStr | None) -> SecretStr | None:
        return ExternalSourceCreate.source_agent(value) if value is not None else None


class ExternalRevisionRequest(ExternalRequest):
    expected_revision: StrictInt = Field(ge=1)


class ExternalSourceDelete(ExternalRevisionRequest):
    confirm: StrictBool

    @field_validator("confirm")
    @classmethod
    def acknowledged(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit confirmation is required")
        return value


class ExternalNodeUpdate(ExternalRevisionRequest):
    name: str = Field(min_length=1, max_length=160)
    enabled: StrictBool

    _name = field_validator("name")(external_name)


class ExternalSourceRead(BaseModel):
    id: UUID
    owner_username: str
    name: str
    enabled: bool
    revision: int
    has_custom_user_agent: bool
    node_count: int
    available_node_count: int
    metadata: dict[str, int]
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExternalSourcesResponse(BaseModel):
    sources: list[ExternalSourceRead]
    license_required: Literal[False] = False


class ExternalNodeRead(BaseModel):
    id: UUID
    source_id: UUID
    upstream_name: str
    name: str
    protocol: str
    enabled: bool
    present: bool
    available: bool
    reason: str | None


class ExternalSourceDetail(BaseModel):
    source: ExternalSourceRead
    nodes: list[ExternalNodeRead]
    license_required: Literal[False] = False


class ExternalPreviewNode(BaseModel):
    node_id: UUID
    upstream_name: str
    name: str
    protocol: str
    change: Literal["new", "updated", "unchanged", "missing", "unavailable"]
    existing: bool
    selectable: bool
    reason: str | None = None
    changed_fields: list[str] = Field(default_factory=list)


class ExternalConfirmationRead(BaseModel):
    source_id: UUID
    preview_id: UUID
    revision: int
    imported_count: int
    updated_count: int
    missing_count: int
    applied_at: datetime


class ExternalPreviewRead(BaseModel):
    id: UUID
    source_id: UUID
    source_revision: int
    created_at: datetime
    expires_at: datetime
    metadata: dict[str, int]
    nodes: list[ExternalPreviewNode]
    receipt: ExternalConfirmationRead | None = None
    license_required: Literal[False] = False


class ExternalPreviewConfirm(ExternalRevisionRequest):
    # Only newly discovered, supported nodes are selectable. The displayed updates
    # and missing-node states are applied together after this explicit acknowledgment.
    selected_node_ids: list[UUID] = Field(max_length=1000)
    accept_changes: StrictBool

    @field_validator("accept_changes")
    @classmethod
    def acknowledged(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Explicit confirmation of the preview changes is required")
        return value

    @field_validator("selected_node_ids")
    @classmethod
    def unique_selection(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Selected node IDs must be unique")
        return value
