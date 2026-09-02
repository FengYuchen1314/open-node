import re
from ipaddress import ip_address
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from open_node.domain.inventory import ServerRead, _strip_required_text


class ServerSettings(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=120)
    ip_address: str | None = Field(default=None, max_length=255)
    ip_address_v6: str | None = Field(default=None, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    domain_v6: str | None = Field(default=None, max_length=255)
    ipv6_enabled: bool = False

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        return _strip_required_text(value, "server name")

    @field_validator("ip_address", "ip_address_v6")
    @classmethod
    def address(cls, value, info):
        if not value or not value.strip():
            return None
        address = ip_address(value.strip())
        if address.version != (6 if info.field_name.endswith("_v6") else 4):
            raise ValueError("IP address family does not match the field")
        return str(address)

    @field_validator("domain", "domain_v6")
    @classmethod
    def hostname(cls, value):
        if not value or not value.strip():
            return None
        try:
            normalized = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Invalid hostname") from exc
        label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        if len(normalized) > 253 or not re.fullmatch(rf"{label}(?:\.{label})*", normalized):
            raise ValueError("Use a hostname without a scheme, port or path")
        return normalized


class ServerSettingsUpdate(ServerSettings):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    sync_node_hosts: bool = True


class ServerSettingsResponse(BaseModel):
    server: ServerRead
    revision: str
    updated_node_ids: list[UUID] = Field(default_factory=list)
    license_required: Literal[False] = False


class RemovalItem(BaseModel):
    id: str
    name: str


class ServerRemovalPreview(BaseModel):
    server_id: UUID
    server_name: str
    revision: str
    nodes: list[RemovalItem]
    plans: list[RemovalItem]
    change_sets: list[RemovalItem]
    certificates: list[RemovalItem]
    command_count: int
    unfinished_command_count: int
    telemetry_count: int
    user_count: int
    blockers: list[str]
    license_required: Literal[False] = False


class ServerRemovalRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_name: str = Field(min_length=1, max_length=120)
    acknowledge_remote_runtime: Literal[True]


class ServerRemovalResponse(BaseModel):
    server_id: UUID
    removed_node_count: int
    updated_plan_count: int
    license_required: Literal[False] = False
