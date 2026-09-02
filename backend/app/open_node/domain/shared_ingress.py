"""Validated declarations for the managed TCP 443 ingress."""

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from open_node.domain.inventory import AgentCommandRead

_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def normalize_sni(value: str) -> str:
    """Return one exact, non-wildcard DNS name suitable for ssl_preread."""

    try:
        normalized = value.strip().rstrip(".").encode("idna").decode().lower()
    except (AttributeError, UnicodeError):
        raise ValueError("sni must be a valid DNS hostname") from None
    if (
        not normalized
        or len(normalized) > 253
        or any(not _HOST_LABEL.fullmatch(label) for label in normalized.split("."))
    ):
        raise ValueError("sni must be a valid DNS hostname")
    return normalized


class SharedIngressProfile(StrEnum):
    VLESS_REALITY_VISION = "vless-reality-vision"
    VLESS_XHTTP_REALITY_XMUX = "vless-xhttp-reality-xmux"
    ANYTLS_SHADOWTLS = "anytls-shadowtls"


class SharedIngressRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    node_id: UUID
    profile: SharedIngressProfile
    sni: str = Field(min_length=1, max_length=253)
    upstream_address: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    upstream_port: int = Field(ge=49_152, le=65_535)

    @field_validator("sni")
    @classmethod
    def validate_sni(cls, value: str) -> str:
        return normalize_sni(value)


class SharedIngressWebsite(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    sni: str = Field(min_length=1, max_length=253)
    upstream_url: str = Field(min_length=1, max_length=2_048)
    tls_address: Literal["127.0.0.1", "::1"] = "127.0.0.1"
    tls_port: int = Field(ge=49_152, le=65_535)
    certificate_name: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    redirect_http: bool = True

    @field_validator("sni")
    @classmethod
    def validate_sni(cls, value: str) -> str:
        return normalize_sni(value)

    @field_validator("upstream_url")
    @classmethod
    def validate_upstream_url(cls, value: str) -> str:
        normalized = value.strip()
        if any(character.isspace() or ord(character) < 0x20 for character in normalized) or any(
            character in normalized for character in "$;{}#"
        ):
            raise ValueError("upstream_url contains unsafe characters")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError(
                "upstream_url must be an absolute HTTP(S) URL without credentials or fragment"
            )
        try:
            _ = parsed.port
            parsed.hostname.encode("idna")
        except (UnicodeError, ValueError):
            raise ValueError("upstream_url contains an invalid host or port") from None
        return normalized


class SharedIngressConfiguration(BaseModel):
    """One auditable owner for public TCP 443 on an Agent host."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    listen_port: Literal[443] = 443
    listen_ipv6: bool = False
    routes: list[SharedIngressRoute] = Field(default_factory=list, max_length=32)
    website: SharedIngressWebsite | None = None

    @model_validator(mode="after")
    def validate_routes(self) -> Self:
        if not self.routes and self.website is None:
            raise ValueError("at least one protocol route or website is required")

        snis: set[str] = set()
        ports: set[int] = set()
        nodes: set[UUID] = set()
        for route in self.routes:
            if route.sni in snis:
                raise ValueError(f"duplicate SNI: {route.sni}")
            if route.upstream_port in ports:
                raise ValueError(f"duplicate internal port: {route.upstream_port}")
            if route.node_id in nodes:
                raise ValueError(f"duplicate node route: {route.node_id}")
            snis.add(route.sni)
            ports.add(route.upstream_port)
            nodes.add(route.node_id)

        if self.website is not None:
            if self.website.sni in snis:
                raise ValueError(f"duplicate SNI: {self.website.sni}")
            if self.website.tls_port in ports:
                raise ValueError(f"duplicate internal port: {self.website.tls_port}")
        return self


class SharedIngressApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    configuration: SharedIngressConfiguration
    expected_revision: int | None = Field(default=None, ge=0)
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)


class SharedIngressDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    expected_revision: int | None = Field(default=None, ge=0)
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)


class SharedIngressState(BaseModel):
    server_id: UUID
    configuration: SharedIngressConfiguration | None = None
    revision: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    license_required: Literal[False] = False


class SharedIngressMutationResponse(BaseModel):
    state: SharedIngressState
    command: AgentCommandRead
    license_required: Literal[False] = False
