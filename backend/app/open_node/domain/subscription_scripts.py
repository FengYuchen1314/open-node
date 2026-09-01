"""Owner-scoped JavaScript subscription override scripts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class OverrideScriptFields(ScriptModel):
    name: str = Field(min_length=1, max_length=120)
    hook: Literal["post_fetch", "pre_save_nodes"]
    content: str = Field(min_length=1, max_length=262_144)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=-10_000, le=10_000)


class OverrideScriptCreate(OverrideScriptFields):
    owner_username: str = Field(min_length=1, max_length=80)


class AccountOverrideScriptCreate(OverrideScriptFields):
    pass


class OverrideScriptUpdate(OverrideScriptFields):
    expected_revision: int = Field(ge=1, le=2**53 - 1)


class OverrideScriptRead(OverrideScriptFields):
    id: UUID
    owner_username: str
    revision: int
    created_at: datetime
    updated_at: datetime


class OverrideScriptsResponse(ScriptModel):
    scripts: list[OverrideScriptRead]
    runtime: Literal["quickjs-subprocess"] = "quickjs-subprocess"
    license_required: Literal[False] = False


class OverrideScriptDelete(ScriptModel):
    expected_revision: int = Field(ge=1, le=2**53 - 1)

