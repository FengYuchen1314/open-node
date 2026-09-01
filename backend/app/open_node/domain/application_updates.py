"""Bounded contracts for installer-managed control-plane updates."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

UpdateStatus = Literal[
    "unavailable", "idle", "checking", "current", "available",
    "updating", "succeeded", "failed", "recovery_required",
]

MESSAGES = {
    "application_update_invalid_request": "更新请求不正确，请重新检查后再试。",
    "application_update_unavailable": "当前部署没有可用的宿主机更新助手，请使用安装脚本更新。",
    "application_update_busy": "已有更新操作正在处理，请等待当前操作完成。",
    "application_update_target_changed": "目标版本已经变化，请重新检查更新。",
    "application_update_rate_limited": "更新操作过于频繁，请稍后重试。",
    "application_update_state_unavailable": "更新状态暂时不可用，请稍后重新读取。",
}


class ApplicationUpdateError(RuntimeError):
    def __init__(self, code: str, status_code: int):
        self.code = code
        self.status_code = status_code
        super().__init__(MESSAGES[code])


class ApplicationUpdateState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    managed: bool
    status: UpdateStatus
    request_id: UUID | None
    current_revision: str = Field(pattern=r"^(?:unknown|[0-9a-f]{40})$")
    latest_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    has_update: bool | None
    checked_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    message: str = Field(min_length=1, max_length=200)
    release_url: str | None
    license_required: Literal[False] = False


class ApplicationUpdateApply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    confirmed: Literal[True]


class ApplicationUpdateAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    accepted: Literal[True] = True
    request_id: UUID
    action: Literal["check", "apply"]
    license_required: Literal[False] = False
