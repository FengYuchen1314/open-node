from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

BROWSER_RESTORE_MESSAGES = {
    "restore_upload_invalid": "恢复上传无效、损坏或超出支持范围。",
    "restore_upload_not_found": "恢复上传不存在、已过期或不属于当前会话。",
    "restore_upload_busy": "已有恢复正在准备或等待重启，请勿重复提交。",
    "restore_upload_unavailable": "当前部署不支持浏览器恢复，请使用离线恢复命令。",
    "restore_prepare_failed": "恢复未能准备完成；当前实例未被覆盖，请检查备份和所需密钥。",
}


class BrowserRestoreError(ValueError):
    def __init__(self, code: str, status_code: int):
        self.code = code if code in BROWSER_RESTORE_MESSAGES else "restore_prepare_failed"
        self.status_code = status_code
        super().__init__(BROWSER_RESTORE_MESSAGES[self.code])


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


class RestoreUploadRead(BaseModel):
    id: UUID
    size: int = Field(ge=22)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: datetime
    license_required: Literal[False] = False


class RestorePreparedRead(BaseModel):
    id: UUID
    restart_required: Literal[True] = True
    automatic_restart: bool
    license_required: Literal[False] = False


class RestorePrepareBase(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)

    format: Literal["age", "plain"]
    identity: SecretStr = Field(default=SecretStr(""), max_length=4096)
    subscriber_totp_key: SecretStr = Field(default=SecretStr(""), max_length=44)
    confirm_replace_instance: Literal[True]
    confirm_trusted_backup: Literal[True]

    @field_validator("confirm_replace_instance", "confirm_trusted_backup", mode="before")
    @classmethod
    def confirmed(cls, value):
        if value is not True:
            raise ValueError("Explicit restore confirmation is required")
        return value

    @field_validator("identity")
    @classmethod
    def identity_is_text(cls, value):
        # Cross-field enforcement is repeated by the service after full parsing.
        if "\x00" in value.get_secret_value():
            raise ValueError("Invalid age identity")
        return value


class AdministratorRestorePrepareRequest(RestorePrepareBase):
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: SecretStr = Field(default=SecretStr(""), max_length=64)


class SetupRestorePrepareRequest(RestorePrepareBase):
    setup_token: SecretStr = Field(min_length=43, max_length=43)
