"""Administrator security events, subscription-probe bans and bounded settings."""

from datetime import datetime
from ipaddress import ip_address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

SECURITY_MESSAGES = {
    "security_invalid_request": "安全管理请求无效。",
    "security_unavailable": "安全管理暂时不可用。",
    "security_revision_conflict": "安全设置已被其他会话修改，请重新读取。",
    "security_ban_not_found": "该 IP 当前没有生效的封禁。",
}

SecurityEventKind = Literal[
    "probe", "ban", "unban", "ban_manual", "login_fail", "login_locked"
]


class SecurityError(ValueError):
    def __init__(self, code: str, status_code: int):
        self.code = code if code in SECURITY_MESSAGES else "security_unavailable"
        self.status_code = status_code
        super().__init__(SECURITY_MESSAGES[self.code])


def canonical_ip(value: str) -> str:
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        raise ValueError("Invalid IP address") from None


class SecurityInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)


class SecurityEventRead(BaseModel):
    id: int
    at: datetime
    ip: str
    kind: SecurityEventKind
    path: str
    username: str
    detail: str
    actor: str


class SecurityEventsRead(BaseModel):
    events: list[SecurityEventRead]
    offset: int
    limit: int
    has_more: bool
    license_required: Literal[False] = False


class SecurityBanRead(BaseModel):
    ip: str
    reason: Literal["brute_force", "manual"]
    banned_at: datetime
    expires_at: datetime | None
    permanent: bool
    fail_count: int
    actor: str


class SecurityBansRead(BaseModel):
    bans: list[SecurityBanRead]
    license_required: Literal[False] = False


class SecurityBanCreate(SecurityInput):
    ip: str = Field(min_length=2, max_length=64)
    permanent: StrictBool = False

    @field_validator("ip")
    @classmethod
    def valid_ip(cls, value: str) -> str:
        return canonical_ip(value)


class SecuritySettingsRead(BaseModel):
    revision: int
    brute_force_enabled: bool
    brute_force_max_failures: int
    brute_force_window_minutes: int
    brute_force_block_minutes: int
    skip_local_ip: bool
    license_required: Literal[False] = False


class SecuritySettingsUpdate(SecurityInput):
    expected_revision: int = Field(ge=0, le=2_147_483_647)
    brute_force_enabled: StrictBool
    brute_force_max_failures: int = Field(ge=2, le=100)
    brute_force_window_minutes: int = Field(ge=1, le=10_080)
    brute_force_block_minutes: int = Field(ge=1, le=43_200)
    skip_local_ip: StrictBool
