"""Free self-hosted server sharing and federation contracts."""

import re
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from open_node.domain.inventory import AgentNginxScan

MAX_REVISION = 2**53 - 1
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
MESSAGES = {
    "server_share_invalid_request": "服务器共享请求无效。",
    "server_share_not_found": "服务器分享不存在或已被吊销。",
    "server_share_token_invalid": "分享令牌无效或已被吊销。",
    "server_share_forbidden": "此分享无权执行该服务器操作。",
    "server_share_conflict": "服务器共享记录已变化，请重新读取。",
    "server_share_storage_unavailable": "服务器共享存储暂时不可用。",
    "server_share_owner_unavailable": "无法安全连接拥有方主控。",
    "server_share_owner_response_invalid": "拥有方返回了无效的联邦响应。",
    "server_share_busy": "服务器共享操作过于频繁，请稍后重试。",
}


class ServerSharingError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code if code in MESSAGES else "server_share_storage_unavailable"
        super().__init__(MESSAGES[self.code])


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True,
        frozen=True, revalidate_instances="always",
    )


class ServerShareCreate(StrictModel):
    server_id: UUID
    label: str = Field(default="", max_length=80)
    allow_manage_xray: bool = False

    @field_validator("server_id", mode="before")
    @classmethod
    def server_identifier(cls, value):
        if isinstance(value, str):
            try:
                return UUID(value)
            except ValueError:
                pass
        return value

    @field_validator("label")
    @classmethod
    def label_text(cls, value):
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid label")
        return value


class ServerShareRead(StrictModel):
    id: UUID
    server_id: UUID
    label: str
    allow_manage_xray: bool
    revision: int = Field(ge=0, le=MAX_REVISION)
    created_at: datetime
    license_required: Literal[False] = False


class ServerShareCreated(StrictModel):
    share: ServerShareRead
    share_token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)
    license_required: Literal[False] = False


class ServerSharesResponse(StrictModel):
    shares: list[ServerShareRead]
    license_required: Literal[False] = False


class ServerShareRevoke(StrictModel):
    expected_revision: int = Field(ge=0, le=MAX_REVISION)
    delete_inbounds: bool = True


class FederationProbeSys(StrictModel):
    cpu_pct: float = Field(default=0, ge=0)
    loadavg: str = Field(default="", max_length=120)
    mem_used: int = Field(default=0, ge=0)
    mem_total: int = Field(default=0, ge=0)
    disk_used: int = Field(default=0, ge=0)
    disk_total: int = Field(default=0, ge=0)
    uptime: int = Field(default=0, ge=0)
    cpu_model: str = Field(default="", max_length=255)
    cpu_cores: int = Field(default=0, ge=0)
    cpu_threads: int = Field(default=0, ge=0)
    os: str = Field(default="", max_length=255)
    kernel: str = Field(default="", max_length=255)
    arch: str = Field(default="", max_length=120)
    upload_speed: int = Field(default=0, ge=0)
    download_speed: int = Field(default=0, ge=0)
    cumulative_up: int = Field(default=0, ge=0)
    cumulative_down: int = Field(default=0, ge=0)
    has_cpu: bool = False
    has_mem: bool = False
    has_disk: bool = False
    has_network: bool = False
    at: int = Field(default=0, ge=0)


class FederationServerInfo(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    status: Literal["pending", "connected", "offline"]
    ip_address: str | None = Field(default=None, max_length=255)
    ip_address_v6: str | None = Field(default=None, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    domain_v6: str | None = Field(default=None, max_length=255)
    ipv6_enabled: bool
    xray_mode: Literal["external", "embedded"]
    traffic_limit: int = Field(ge=0)
    traffic_reset_day: int = Field(default=0, ge=0, le=31)
    traffic_used: int = Field(ge=0)
    current_upload_speed: int = Field(ge=0)
    current_download_speed: int = Field(ge=0)
    xray_running: bool | None = None
    xray_version: str | None = Field(default=None, max_length=120)
    nginx: AgentNginxScan | None = None
    probe_sys: FederationProbeSys | None = None
    last_heartbeat: datetime | None = None
    allow_manage_xray: bool = False
    license_required: Literal[False] = False

    @field_validator("last_heartbeat", mode="before")
    @classmethod
    def heartbeat_time(cls, value):
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return value


class FederationCommandCreate(StrictModel):
    method: Literal["GET", "POST"]
    path: str = Field(min_length=1, max_length=255)
    body: dict[str, Any] | None = None
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("method", mode="before")
    @classmethod
    def method_name(cls, value):
        return value.upper() if isinstance(value, str) else value

    @field_validator("path")
    @classmethod
    def child_path(cls, value):
        if not value.startswith("/api/child/") or "?" in value or "#" in value:
            raise ValueError("Invalid Agent path")
        return value


class FederationCommandRead(StrictModel):
    id: UUID
    method: Literal["GET", "POST"]
    path: str
    status: Literal["pending", "leased", "succeeded", "failed", "skipped", "waiting"]
    result_status: int | None = None
    result_body: Any = None
    failed: bool = False
    created_at: datetime
    completed_at: datetime | None = None
    license_required: Literal[False] = False


class ServerShareRevoked(StrictModel):
    revoked: Literal[True] = True
    cleanup_commands: list[FederationCommandRead]
    license_required: Literal[False] = False


class FederatedServerCreate(StrictModel):
    owner_url: str = Field(min_length=1, max_length=2048)
    share_token: SecretStr
    name: str = Field(default="", max_length=120)
    prefix: str = Field(default="", max_length=40)

    @field_validator("name", "prefix")
    @classmethod
    def visible_text(cls, value):
        value = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid text")
        return value

    @model_validator(mode="after")
    def valid_token(self) -> Self:
        if TOKEN_PATTERN.fullmatch(self.share_token.get_secret_value()) is None:
            raise ValueError("Invalid share token")
        return self


class FederatedServerRead(StrictModel):
    id: UUID
    name: str
    owner_url: str
    prefix: str
    revision: int = Field(ge=0, le=MAX_REVISION)
    info: FederationServerInfo
    last_synced_at: datetime
    created_at: datetime
    license_required: Literal[False] = False


class FederatedServersResponse(StrictModel):
    servers: list[FederatedServerRead]
    license_required: Literal[False] = False


class FederatedServerDelete(StrictModel):
    expected_revision: int = Field(ge=0, le=MAX_REVISION)
    confirm: Literal[True]


class FederatedServerRefresh(StrictModel):
    expected_revision: int = Field(ge=0, le=MAX_REVISION)
