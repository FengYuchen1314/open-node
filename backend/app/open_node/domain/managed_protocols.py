"""Strict controller-to-Agent declarations for the five managed protocols."""

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManagedProtocolWireProfile(StrEnum):
    VLESS_REALITY_VISION = "vless_reality_vision"
    VLESS_XHTTP_REALITY_XMUX = "vless_xhttp_reality_xmux"
    ANYTLS_SHADOWTLS = "anytls_shadowtls"
    MIERU = "mieru"
    SOCKS5 = "socks5"


class ManagedProtocolUser(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=255)
    uuid: UUID | None = None
    password: str | None = Field(default=None, min_length=1, max_length=512)


class ManagedProtocolListener(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    tag: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    node_id: UUID
    profile: ManagedProtocolWireProfile
    listen: Literal["127.0.0.1", "::1", "0.0.0.0", "::"]
    port: int = Field(ge=1_024, le=65_535)
    enabled: bool = True
    client_config: dict[str, Any] = Field(default_factory=dict)
    server_config: dict[str, Any] = Field(default_factory=dict)
    users: list[ManagedProtocolUser] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_profile_contract(self):
        shared = self.profile in {
            ManagedProtocolWireProfile.VLESS_REALITY_VISION,
            ManagedProtocolWireProfile.VLESS_XHTTP_REALITY_XMUX,
            ManagedProtocolWireProfile.ANYTLS_SHADOWTLS,
        }
        if shared and self.listen not in {"127.0.0.1", "::1"}:
            raise ValueError("shared 443 listeners must bind a loopback address")
        if not shared and self.listen not in {"0.0.0.0", "::"}:
            raise ValueError("Mieru and SOCKS5 listeners must bind a public address")
        return self


class ManagedProtocolSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    revision: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    listeners: list[ManagedProtocolListener] = Field(default_factory=list, max_length=512)
