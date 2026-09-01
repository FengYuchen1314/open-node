"""Administrator DDNS contracts with fixed, secret-free errors."""

from datetime import datetime
from ipaddress import ip_address
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_node.domain.certificates import dns_name

DDNS_MESSAGES = {
    "ddns_server_not_found": "服务器不存在。",
    "ddns_invalid_request": "DDNS 请求无效。",
    "ddns_revision_conflict": "DDNS 设置已被其他页面修改，请刷新后重试。",
    "ddns_provider_not_found": "DNS 服务商不存在。",
    "ddns_provider_unsupported": "该 DNS 服务商暂不支持动态 A/AAAA 更新。",
    "ddns_provider_credentials_invalid": "DNS 服务商凭据不完整或无法解密。",
    "ddns_domain_invalid": "DDNS 地址必须是有效的完整域名，不能填写 IP、通配符或单段名称。",
    "ddns_not_enabled": "这台服务器尚未启用 DDNS。",
    "ddns_no_public_address": "Agent 尚未上报可同步的公网 IPv4 或 IPv6。",
    "ddns_provider_cannot_manage": "所选 DNS 服务商无法管理该域名。",
    "ddns_provider_unavailable": "DNS 服务商暂时不可用，系统稍后会自动重试。",
    "ddns_provider_rejected": "DNS 服务商拒绝了更新，请检查域名权限和凭据。",
    "ddns_provider_invalid_response": "DNS 服务商返回了无法识别的响应，系统稍后会重试。",
    "ddns_busy": "DDNS 同步正在执行，请稍后刷新状态。",
    "ddns_storage_unavailable": "DDNS 状态暂时不可用。",
}


class DDNSError(ValueError):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code, self.code = status_code, code


class DDNSInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DDNSConfig(DDNSInput):
    enabled: bool
    provider_id: UUID | None = None
    pull_address: str | None = Field(default=None, max_length=255)
    pull_address_v6: str | None = Field(default=None, max_length=255)
    expected_revision: int = Field(ge=0)

    @field_validator("pull_address", "pull_address_v6")
    @classmethod
    def domain(cls, value):
        if value is None or not value.strip():
            return None
        try:
            normalized = dns_name(value)
        except ValueError:
            raise ValueError("DDNS requires a valid DNS name") from None
        try:
            ip_address(normalized)
        except ValueError:
            pass
        else:
            raise ValueError("DDNS requires a DNS name") from None
        if normalized.startswith("*.") or "." not in normalized:
            raise ValueError("DDNS requires a full non-wildcard DNS name")
        return normalized

    @model_validator(mode="after")
    def enabled_domain(self):
        if self.enabled and not (self.pull_address or self.pull_address_v6):
            raise ValueError("Enabled DDNS requires at least one DNS name")
        return self


class DDNSProviderRead(BaseModel):
    id: UUID
    name: str
    provider: str
    supported: bool


class DDNSServerRead(BaseModel):
    server_id: UUID
    server_name: str
    server_status: str
    enabled: bool
    provider_id: UUID | None = None
    provider_name: str | None = None
    provider_type: str | None = None
    pull_address: str | None = None
    pull_address_v6: str | None = None
    ip_address: str | None = None
    ip_address_v6: str | None = None
    ipv6_enabled: bool
    last_synced_at: datetime | None = None
    last_error: str | None = None
    pending: bool
    revision: int
    license_required: Literal[False] = False


class DDNSWorkspaceRead(BaseModel):
    servers: list[DDNSServerRead]
    providers: list[DDNSProviderRead]
    license_required: Literal[False] = False


class DDNSSyncRead(BaseModel):
    server: DDNSServerRead
    queued: bool
    license_required: Literal[False] = False
