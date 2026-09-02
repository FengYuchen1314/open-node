from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TemplateFormat = Literal["clash"]
MAX_TEMPLATE_BYTES = 2 * 1024 * 1024


class TemplateDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    format: TemplateFormat
    content: str = Field(min_length=1, max_length=MAX_TEMPLATE_BYTES)

    @model_validator(mode="after")
    def valid_document(self):
        from open_node.services.template_rendering import parse_template

        parse_template(self.content, self.format)
        return self


class TemplateWrite(TemplateDocument):
    name: str = Field(min_length=1, max_length=160)
    owner_username: str | None = Field(default=None, max_length=80)
    is_public: bool = Field(default=False, strict=True)

    @field_validator("name")
    @classmethod
    def filename(cls, value):
        value = value.strip()
        if not value or any(c in value for c in "/\\\x00\r\n") or ".." in value:
            raise ValueError("Template name must be a filename without path components")
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("Template name contains invalid characters")
        return value

    @model_validator(mode="after")
    def extension(self):
        if not self.name.lower().endswith((".yaml", ".yml")):
            raise ValueError("Template filename extension must match its format")
        return self


class TemplateUpdate(TemplateWrite):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class TemplateRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_name: str


class TemplateRead(BaseModel):
    id: UUID
    name: str
    format: TemplateFormat
    owner_username: str | None
    is_public: bool
    editable: bool
    revision: str
    content: str | None = None
    size_bytes: int
    plan_names: list[str] = Field(default_factory=list)
    default_scopes: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TemplateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clash_template_id: UUID | None = None


class TemplateSettings(TemplateSelection):
    enabled: bool = True
    revision: str


class TemplateSettingsUpdate(TemplateSelection):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    enabled: bool | None = Field(default=None, strict=True)


class TemplateList(BaseModel):
    templates: list[TemplateRead]
    settings: TemplateSettings
    can_manage: bool
    license_required: Literal[False] = False


class TemplatePreview(TemplateDocument):
    username: str | None = Field(default=None, max_length=80)


class TemplatePreviewRead(BaseModel):
    content: str
    warnings: list[str]
    included_nodes: int
    excluded_nodes: int


class CatalogTemplateSettings(BaseModel):
    clash_template_name: str | None = None


class CatalogTemplatePreference(CatalogTemplateSettings):
    username: str
    enabled: bool = False
