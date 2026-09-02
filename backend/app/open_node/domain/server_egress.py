from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_tls_certificate_pins(value: Any) -> str:
    """Return the exact comma-separated SHA-256 format accepted by Xray.

    Xray-core-mmwx accepts OpenSSL-style colons and comma-separated pins.  The
    control plane stores one canonical representation so a preview revision is
    stable even when the caller changes case or formatting.
    """

    if not isinstance(value, str):
        raise ValueError("pinned_peer_cert_sha256 must be a string")
    raw_pins = [item.strip() for item in value.split(",") if item.strip()]
    if not raw_pins or len(raw_pins) > 8:
        raise ValueError("pinned_peer_cert_sha256 must contain between 1 and 8 hashes")
    normalized: list[str] = []
    for raw in raw_pins:
        pin = raw.replace(":", "").lower()
        if len(pin) != 64 or any(character not in "0123456789abcdef" for character in pin):
            raise ValueError(
                "Each pinned_peer_cert_sha256 value must be a 32-byte SHA-256 hash"
            )
        if pin not in normalized:
            normalized.append(pin)
    return ",".join(normalized)


class ServerEgressTLSProbeDescriptor(BaseModel):
    """Non-secret public coordinates for a target Agent TLS probe."""

    model_config = {"extra": "forbid"}

    protocol: Literal[
        "vless", "vmess", "trojan", "shadowsocks", "socks", "http", "anytls"
    ]
    address: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65_535)
    server_name: str | None = Field(default=None, min_length=1, max_length=253)
    alpn: list[str] = Field(default_factory=list, max_length=8)


class ServerEgressRoutingSelector(BaseModel):
    """A safe subset of Xray field-rule selectors.

    The service owns ``outboundTag`` and ``marktag`` so callers cannot redirect
    traffic to an arbitrary or secret-bearing outbound.
    """

    model_config = {"extra": "forbid"}

    domains: list[str] = Field(default_factory=list, max_length=200)
    ips: list[str] = Field(default_factory=list, max_length=200)
    inbound_tags: list[str] = Field(default_factory=list, max_length=100)
    users: list[str] = Field(default_factory=list, max_length=500)
    protocols: list[str] = Field(default_factory=list, max_length=50)
    port: str | None = Field(default=None, min_length=1, max_length=255)
    network: Literal["tcp", "udp", "tcp,udp"] | None = None

    @model_validator(mode="after")
    def require_selector(self):
        collections = (
            self.domains,
            self.ips,
            self.inbound_tags,
            self.users,
            self.protocols,
        )
        if not any(collections) and self.port is None and self.network is None:
            raise ValueError("At least one routing selector is required")
        for values in collections:
            if len(set(values)) != len(values):
                raise ValueError("Routing selector values must be distinct")
            if any(not value.strip() for value in values):
                raise ValueError("Routing selector values must not be blank")
        return self


class ServerEgressPreviewRequest(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}

    target_node_id: UUID
    promote_to_default: bool = False
    routing: ServerEgressRoutingSelector | None = None
    # Official three-state semantics: omitted keeps the current value, null
    # removes it, and an object replaces it.
    observatory: dict | None = None
    burst_observatory: dict | None = Field(default=None, alias="burstObservatory")
    pinned_peer_cert_sha256: str | None = Field(default=None, max_length=1024)

    @field_validator("pinned_peer_cert_sha256")
    @classmethod
    def validate_pinned_peer_cert_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_tls_certificate_pins(value)


class ServerEgressApplyRequest(ServerEgressPreviewRequest):
    expected_preview_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    dispatch: Literal[True] = True


class ServerEgressRemovePreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}

    target_node_id: UUID


class ServerEgressRemoveRequest(ServerEgressRemovePreviewRequest):
    expected_preview_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    dispatch: Literal[True] = True


class ServerEgressCandidateRead(BaseModel):
    node_id: UUID
    node_name: str
    server_id: UUID
    server_name: str
    protocol: str
    available: bool
    unavailable_reason: str | None = None
    configured: bool = False
    is_default: bool = False
    has_routing_rule: bool = False
    has_target_client: bool = False
    needs_repair: bool = False
    tls_probe: ServerEgressTLSProbeDescriptor | None = None


class ServerEgressCatalogRead(BaseModel):
    server_id: UUID
    candidates: list[ServerEgressCandidateRead]
    source_snapshot_id: UUID | None = None
    source_snapshot_revision: str | None = None


class ServerEgressPreviewRead(BaseModel):
    source_server_id: UUID
    source_server_name: str
    target_node_id: UUID
    target_node_name: str
    target_server_id: UUID
    target_server_name: str
    protocol: str
    action: Literal["create", "update", "repair", "remove"]
    outbound_tag: str
    routing_marktag: str
    promote_to_default: bool
    will_be_default: bool
    routing: ServerEgressRoutingSelector | None = None
    routing_action: Literal["keep", "set", "remove"] = "keep"
    observatory_action: Literal["keep", "set", "remove"] = "keep"
    burst_observatory_action: Literal["keep", "set", "remove"] = "keep"
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    preview_revision: str
    tls_probe: ServerEgressTLSProbeDescriptor | None = None
    pinned_peer_cert_sha256: str | None = None


class ServerEgressApplyResponse(BaseModel):
    preview: ServerEgressPreviewRead
    change_set_id: UUID
    change_set_status: str
    command_ids: list[UUID]
    license_required: Literal[False] = False
