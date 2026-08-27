from __future__ import annotations

import base64
import hashlib
import hmac
import json
from calendar import monthrange
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from secrets import token_bytes, token_urlsafe
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

import yaml
from pydantic import ValidationError
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from open_node.domain.changes import (
    AgentChangeSetCreate,
    AgentChangeSetRead,
    AgentChangeSetRollbackRequest,
    AgentChangeSetStatus,
    AgentChangeSetStepCreate,
    AgentChangeSetStepRead,
)
from open_node.domain.inventory import (
    AgentCapabilities,
    AgentCommandCreate,
    AgentCommandRead,
    AgentCommandResultRequest,
    AgentCommandStatus,
    AgentCommandStreamDataRequest,
    AgentCommandStreamFrameRead,
    AgentHeartbeatRequest,
    AgentRead,
    AgentRegistrationRequest,
    AgentScanResultPayload,
    AgentScanResultRead,
    AgentScanResultReport,
    AgentTelemetryRead,
    AgentTelemetryReport,
    ConnectionMode,
    ProbeLatencySample,
    ProbeSysMetrics,
    RenewalCycle,
    ServerCreate,
    ServerProbeMetadataUpdate,
    ServerRead,
    ServerRecord,
    ServerStatus,
    SystemTraffic,
    TrafficSource,
    TrafficStatsMode,
    XrayConfigSnapshotRead,
    XrayConfigSnapshotSource,
    XrayConfigSnapshotStatus,
    XrayMode,
    XrayStats,
)
from open_node.domain.probe import (
    ProbeAccessTokenCreateResponse,
    ProbeAppearance,
    ProbeBucket,
    ProbeDailyTraffic,
    ProbeMetricPoint,
    ProbePayload,
    ProbePingSeries,
    ProbeReturnRoute,
    ProbeSeriesResponse,
    ProbeServer,
    ProbeSettingsRead,
    ProbeSettingsResponse,
    ProbeSettingsUpdate,
    ProbeSystemSeries,
    ProbeTargetComparison,
    ProbeTargetComparisonResponse,
    ProbeTargetServerComparison,
    ProbeTaskCreate,
    ProbeTaskRead,
    ProbeTaskReturnRouteTarget,
    ProbeTaskUpdate,
)
from open_node.domain.subscriptions import (
    ManagedNodeCreate,
    ManagedNodeRead,
    ManagedNodeType,
    ProductUserCreate,
    ProductUserRead,
    ProductUserRole,
    ProductUserTrafficResponse,
    SubscriptionCatalogBundle,
    SubscriptionCatalogCredentialEntry,
    SubscriptionCatalogImportRequest,
    SubscriptionCatalogImportResponse,
    SubscriptionCatalogImportSummary,
    SubscriptionCatalogNodeEntry,
    SubscriptionCatalogPlanEntry,
    SubscriptionCatalogUserEntry,
    SubscriptionClientFormat,
    SubscriptionCredentialRead,
    SubscriptionDueTrafficResetRequest,
    SubscriptionDueTrafficResetResponse,
    SubscriptionDueTrafficResetSummary,
    SubscriptionPlanAssignRequest,
    SubscriptionPlanCreate,
    SubscriptionPlanRead,
    SubscriptionProvisionBatch,
    SubscriptionQuotaStatusRead,
    SubscriptionTemplatePresetApplyRequest,
    SubscriptionTemplatePresetRead,
    SubscriptionTrafficEntryRead,
    SubscriptionTrafficMode,
)


class InvalidAgentTokenError(ValueError):
    """Raised when an agent presents an unknown bootstrap token."""


class DuplicateServerNameError(ValueError):
    """Raised when a server name would no longer be a stable inventory key."""


class ServerNotFoundError(ValueError):
    """Raised when an inventory lookup targets an unknown server."""


class CommandNotFoundError(ValueError):
    """Raised when an agent command cannot be found for the requesting server."""


class XrayConfigSnapshotNotFoundError(ValueError):
    """Raised when an Xray config snapshot lookup targets an unknown snapshot."""


class ChangeSetNotFoundError(ValueError):
    """Raised when an agent change set lookup targets an unknown change set."""


class ProbeNotFoundError(ValueError):
    """Raised when a public probe lookup targets data outside the public list."""


class ProbeTaskNotFoundError(ValueError):
    """Raised when a probe task lookup targets an unknown schedule."""


class DuplicateProductUserError(ValueError):
    """Raised when a product username is already taken."""


class ProductUserNotFoundError(ValueError):
    """Raised when a product user lookup targets an unknown username."""


class DuplicateSubscriptionPlanNameError(ValueError):
    """Raised when a subscription plan name is already taken."""


class SubscriptionPlanNotFoundError(ValueError):
    """Raised when a subscription plan lookup targets an unknown plan."""


class ManagedNodeNotFoundError(ValueError):
    """Raised when a managed node lookup targets an unknown node."""


class SubscriptionTokenNotFoundError(ValueError):
    """Raised when a public subscription token or short code is unknown."""


class SubscriptionUnavailableError(ValueError):
    """Raised when a product user has no active renderable subscription."""


class SubscriptionTemplatePresetNotFoundError(ValueError):
    """Raised when a subscription node preset lookup targets an unknown preset."""


_PROBE_SERIES_RANGES = {
    "1h": (12, 300),
    "6h": (36, 600),
    "24h": (48, 1800),
}

_SUBSCRIPTION_NODE_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "vless-vision-tls",
        "name": "VLESS Vision TLS",
        "description": "VLESS TCP TLS node with Vision flow and UUID credentials.",
        "protocol": "vless",
        "node_type": "physical",
        "inbound_tag": "vless-443",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "vless",
        "tags": ["vless", "tls"],
        "client_template": {"email": "{username}__vless-443", "flow": "xtls-rprx-vision"},
        "config": {
            "name": "{server_name} VLESS",
            "type": "vless",
            "server": "{server_domain}",
            "port": 443,
            "tls": True,
            "network": "tcp",
            "flow": "xtls-rprx-vision",
        },
    },
    {
        "id": "trojan-tls",
        "name": "Trojan TLS",
        "description": "Trojan TLS node with generated password credentials.",
        "protocol": "trojan",
        "node_type": "physical",
        "inbound_tag": "trojan-443",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "trojan",
        "tags": ["trojan", "tls"],
        "client_template": {"email": "{username}__trojan-443"},
        "config": {
            "name": "{server_name} Trojan",
            "type": "trojan",
            "server": "{server_domain}",
            "port": 443,
            "tls": True,
        },
    },
    {
        "id": "shadowsocks-2022",
        "name": "Shadowsocks 2022",
        "description": "Shadowsocks 2022 node with per-user generated 32 byte keys.",
        "protocol": "shadowsocks",
        "node_type": "physical",
        "inbound_tag": "ss-2022",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "ss",
        "tags": ["ss"],
        "client_template": {"email": "{username}__ss-2022"},
        "config": {
            "name": "{server_name} SS",
            "type": "ss",
            "server": "{server_domain}",
            "port": 8388,
            "cipher": "2022-blake3-aes-256-gcm",
        },
    },
    {
        "id": "hysteria2",
        "name": "Hysteria2",
        "description": "Hysteria2 node with per-user auth credentials.",
        "protocol": "hysteria2",
        "node_type": "physical",
        "inbound_tag": "hy2-443",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "hy2",
        "tags": ["hy2", "udp"],
        "client_template": {"email": "{username}__hy2-443"},
        "config": {
            "name": "{server_name} Hy2",
            "type": "hysteria2",
            "server": "{server_domain}",
            "port": 443,
            "tls": True,
        },
    },
    {
        "id": "routed-outbound",
        "name": "Routed outbound",
        "description": "Catalog-only routed node that adds users to an outbound route.",
        "protocol": "vless",
        "node_type": "routed",
        "inbound_tag": None,
        "routed_outbound_tag": "proxy-out",
        "routed_rule_marktag": "route-proxy",
        "tag": "routed",
        "tags": ["routed"],
        "client_template": {"email": "{username}__routed"},
        "config": {
            "name": "{server_name} Routed",
            "type": "vless",
            "server": "{server_domain}",
            "port": 443,
            "tls": True,
        },
    },
)


@dataclass(frozen=True)
class RenderedSubscription:
    username: str
    plan_name: str
    content: str
    media_type: str
    filename: str
    subscription_userinfo: str | None
    warnings: list[str]


@dataclass(frozen=True)
class SubscriptionTokenRecord:
    username: str
    token: str
    short_code: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProbeTrafficTotals:
    source: str
    uplink: int
    downlink: int
    boot_time_unix: int | None = None


class Base(DeclarativeBase):
    pass


class ServerModel(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    agent_token: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address_v6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_v6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connection_mode: Mapped[str] = mapped_column(String(24))
    listen_port: Mapped[int] = mapped_column(Integer)
    pull_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pull_address_v6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pull_port: Mapped[int] = mapped_column(Integer)
    ipv6_enabled: Mapped[bool] = mapped_column(Boolean)
    traffic_limit: Mapped[int] = mapped_column(Integer)
    traffic_stats_mode: Mapped[str] = mapped_column(String(24))
    traffic_source: Mapped[str] = mapped_column(String(24))
    xray_mode: Mapped[str] = mapped_column(String(24))
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region_country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region_city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewal_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    renewal_price_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    renewal_cycle: Mapped[str | None] = mapped_column(String(24), nullable=True)
    renewal_currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    telecom_paid_peer: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    current_upload_speed: Mapped[int] = mapped_column(Integer, default=0)
    current_download_speed: Mapped[int] = mapped_column(Integer, default=0)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    hostname: Mapped[str] = mapped_column(String(255))
    agent_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    connection_mode: Mapped[str] = mapped_column(String(24))
    listen_port: Mapped[int] = mapped_column(Integer)
    public_ipv4: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_ipv6: Mapped[str | None] = mapped_column(String(255), nullable=True)
    xray_mode: Mapped[str] = mapped_column(String(24))
    capability_rpc: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_return_route_test: Mapped[bool] = mapped_column(Boolean, default=False)
    warp_installed: Mapped[bool] = mapped_column(Boolean, default=False)
    same_host_as_master: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelemetrySnapshotModel(Base):
    __tablename__ = "telemetry_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    online_users: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    user_speeds: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    conn_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    system_rx_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    system_tx_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    system_boot_time_unix: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sysmetrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    latency: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class AgentScanResultModel(Base):
    __tablename__ = "agent_scan_results"

    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    xray_running: Mapped[bool] = mapped_column(Boolean, default=False)
    xray_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    api_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    inbounds: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    device_kicks: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    config_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    config_added_sections: Mapped[list[str]] = mapped_column(JSON, default=list)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class XrayConfigSnapshotModel(Base):
    __tablename__ = "xray_config_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    source_command_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_commands.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    config: Mapped[str] = mapped_column(Text)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CommandModel(Base):
    __tablename__ = "agent_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    request_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    method: Mapped[str] = mapped_column(String(12))
    path: Mapped[str] = mapped_column(String(255))
    query: Mapped[str] = mapped_column(String(2048), default="")
    body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer)
    stream: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    result_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CommandStreamFrameModel(Base):
    __tablename__ = "agent_command_stream_frames"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    command_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_commands.id", ondelete="CASCADE"),
        index=True,
    )
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    data: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ServerReturnRouteModel(Base):
    __tablename__ = "server_return_routes"

    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    carrier: Mapped[str] = mapped_column(String(16), primary_key=True)
    region: Mapped[str] = mapped_column(String(120), default="")
    route_type: Mapped[str] = mapped_column(String(80), default="Unknown")
    entry_ip: Mapped[str] = mapped_column(String(255), default="")
    entry_asn: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(2048), default="")
    tested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentChangeSetModel(Base):
    __tablename__ = "agent_change_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), index=True)
    rollback_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    rollback_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AgentChangeSetStepModel(Base):
    __tablename__ = "agent_change_set_steps"
    __table_args__ = (
        UniqueConstraint("change_set_id", "sequence", name="uq_change_set_step_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    change_set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_change_sets.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    label: Mapped[str] = mapped_column(String(160), default="")
    forward_method: Mapped[str] = mapped_column(String(12))
    forward_path: Mapped[str] = mapped_column(String(255))
    forward_query: Mapped[str] = mapped_column(String(2048), default="")
    forward_body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    forward_timeout_ms: Mapped[int] = mapped_column(Integer)
    forward_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    rollback_method: Mapped[str | None] = mapped_column(String(12), nullable=True)
    rollback_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rollback_query: Mapped[str] = mapped_column(String(2048), default="")
    rollback_body: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    rollback_timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rollback_stream: Mapped[bool] = mapped_column(Boolean, default=False)
    forward_command_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_commands.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rollback_command_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_commands.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProductUserModel(Base):
    __tablename__ = "product_users"

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(24))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    current_plan_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("subscription_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_day: Mapped[int] = mapped_column(Integer, default=0)
    last_traffic_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ManagedNodeModel(Base):
    __tablename__ = "managed_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    protocol: Mapped[str] = mapped_column(String(40))
    node_type: Mapped[str] = mapped_column(String(24), default="physical", index=True)
    inbound_tag: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    routed_outbound_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routed_rule_marktag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    client_template: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionPlanModel(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    cycle_days: Mapped[int] = mapped_column(Integer)
    is_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_day: Mapped[int] = mapped_column(Integer, default=0)
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    node_multipliers: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    node_speed_limits: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    node_device_limits: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    speed_limit_mbps: Mapped[float] = mapped_column(Float, default=0)
    device_limit: Mapped[int] = mapped_column(Integer, default=0)
    traffic_mode: Mapped[str] = mapped_column(String(24), default="oneway")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProductUserSubscriptionTokenModel(Base):
    __tablename__ = "product_user_subscription_tokens"

    username: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("product_users.username", ondelete="CASCADE"),
        primary_key=True,
    )
    token: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    short_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionCredentialModel(Base):
    __tablename__ = "subscription_credentials"
    __table_args__ = (
        UniqueConstraint("username", "node_id", name="uq_subscription_credential_user_node"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("product_users.username", ondelete="CASCADE"),
        index=True,
    )
    node_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("managed_nodes.id", ondelete="CASCADE"),
        index=True,
    )
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    inbound_tag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    protocol: Mapped[str] = mapped_column(String(40))
    email: Mapped[str] = mapped_column(String(255), index=True)
    credential: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubscriptionTrafficLedgerModel(Base):
    __tablename__ = "subscription_traffic_ledger"
    __table_args__ = (
        UniqueConstraint("username", "server_id", "email", name="uq_subscription_traffic_email"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("product_users.username", ondelete="CASCADE"),
        index=True,
    )
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), index=True)
    upload: Mapped[int] = mapped_column(BigInteger, default=0)
    download: Mapped[int] = mapped_column(BigInteger, default=0)
    last_uplink: Mapped[int] = mapped_column(BigInteger, default=0)
    last_downlink: Mapped[int] = mapped_column(BigInteger, default=0)
    last_reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProbeSettingsModel(Base):
    __tablename__ = "probe_settings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    access_token_hash: Mapped[str] = mapped_column(String(64), default="")
    require_access_token: Mapped[bool] = mapped_column(Boolean, default=False)
    show_globe: Mapped[bool] = mapped_column(Boolean, default=False)
    show_daily_trend: Mapped[bool] = mapped_column(Boolean, default=False)
    show_traffic_hotspots: Mapped[bool] = mapped_column(Boolean, default=False)
    show_traffic_7d: Mapped[bool] = mapped_column(Boolean, default=False)
    show_return_route: Mapped[bool] = mapped_column(Boolean, default=False)
    show_resource_heatmap: Mapped[bool] = mapped_column(Boolean, default=True)
    show_traffic_quota: Mapped[bool] = mapped_column(Boolean, default=True)
    show_renewal_timeline: Mapped[bool] = mapped_column(Boolean, default=False)
    show_health_score: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str] = mapped_column(String(120), default="Open Node Probe")
    description: Mapped[str] = mapped_column(
        Text,
        default="MMWX probe-compatible node status without license gates.",
    )
    logo: Mapped[str] = mapped_column(Text, default="")
    refresh_interval_sec: Mapped[int] = mapped_column(Integer, default=5)
    appearance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProbeTaskModel(Base):
    __tablename__ = "probe_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    domain_timeout_ms: Mapped[int] = mapped_column(Integer, default=2_000)
    allow_icmp: Mapped[bool] = mapped_column(Boolean, default=False)
    return_route_targets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    return_route_timeout_seconds: Mapped[int] = mapped_column(Integer, default=25)
    ip_version: Mapped[int] = mapped_column(Integer, default=4)
    command_timeout_ms: Mapped[int] = mapped_column(Integer, default=90_000)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InventoryStore:
    def __init__(self, database_url: str) -> None:
        self._engine = create_inventory_engine(database_url)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self._engine)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        if self._engine.dialect.name != "sqlite":
            return
        inspector = inspect(self._engine)
        table_names = set(inspector.get_table_names())
        if "product_users" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "product_users",
                {"last_traffic_reset_at": "DATETIME"},
            )
        if "servers" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "servers",
                {
                    "region": "VARCHAR(120)",
                    "region_country": "VARCHAR(120)",
                    "region_name": "VARCHAR(120)",
                    "region_city": "VARCHAR(120)",
                    "provider_name": "VARCHAR(120)",
                    "provider_url": "VARCHAR(500)",
                    "expires_at": "DATETIME",
                    "renewal_price": "FLOAT",
                    "renewal_price_cny": "FLOAT",
                    "renewal_cycle": "VARCHAR(24)",
                    "renewal_currency": "VARCHAR(12)",
                    "telecom_paid_peer": "BOOLEAN",
                },
            )
        if "probe_settings" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "probe_settings",
                {
                    "show_return_route": "BOOLEAN",
                    "access_token_hash": "VARCHAR(64)",
                    "require_access_token": "BOOLEAN",
                },
            )

    def _sqlite_add_missing_columns(
        self,
        inspector: Any,
        table_name: str,
        columns: dict[str, str],
    ) -> None:
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        missing = [(name, kind) for name, kind in columns.items() if name not in existing]
        if not missing:
            return
        with self._engine.begin() as connection:
            for name, kind in missing:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {kind}"))

    def list_servers(self) -> list[ServerRead]:
        with self._session() as session:
            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            return [self._public_server(server) for server in servers]

    def create_server(self, payload: ServerCreate) -> ServerRecord:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            existing = session.scalar(select(ServerModel).where(ServerModel.name == payload.name))
            if existing:
                raise DuplicateServerNameError(f"server name already exists: {payload.name}")

            server = ServerModel(
                id=str(uuid4()),
                name=payload.name,
                status=ServerStatus.PENDING.value,
                ip_address=payload.ip_address,
                ip_address_v6=payload.ip_address_v6,
                domain=payload.domain,
                domain_v6=payload.domain_v6,
                connection_mode=payload.connection_mode.value,
                listen_port=payload.listen_port,
                pull_address=payload.pull_address,
                pull_address_v6=payload.pull_address_v6,
                pull_port=payload.pull_port,
                ipv6_enabled=payload.ipv6_enabled,
                traffic_limit=payload.traffic_limit,
                traffic_stats_mode=payload.traffic_stats_mode.value,
                traffic_source=payload.traffic_source.value,
                xray_mode=payload.xray_mode.value,
                region=payload.region,
                region_country=payload.region_country,
                region_name=payload.region_name,
                region_city=payload.region_city,
                provider_name=payload.provider_name,
                provider_url=payload.provider_url,
                expires_at=payload.expires_at,
                renewal_price=payload.renewal_price,
                renewal_price_cny=payload.renewal_price_cny,
                renewal_cycle=payload.renewal_cycle.value if payload.renewal_cycle else None,
                renewal_currency=payload.renewal_currency,
                telecom_paid_peer=payload.telecom_paid_peer,
                current_upload_speed=0,
                current_download_speed=0,
                created_at=now,
                updated_at=now,
                agent_token=token_urlsafe(32),
            )
            session.add(server)
            session.commit()
            session.refresh(server)
            return self._server_record(server)

    def public_server(self, server: ServerRecord) -> ServerRead:
        return ServerRead(**server.model_dump(exclude={"agent_token"}))

    def update_server_probe_metadata(
        self,
        server_id: UUID,
        payload: ServerProbeMetadataUpdate,
    ) -> ServerRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            for field, value in payload.model_dump(exclude_unset=True).items():
                if field == "renewal_cycle" and value is not None:
                    value = RenewalCycle(value).value
                setattr(server, field, value)
            server.updated_at = now
            session.commit()
            session.refresh(server)
            return self._public_server(server)

    def list_agents(self) -> list[AgentRead]:
        with self._session() as session:
            agents = session.scalars(select(AgentModel).order_by(AgentModel.registered_at)).all()
            return [self._agent_read(agent) for agent in agents]

    def register_agent(self, payload: AgentRegistrationRequest) -> tuple[AgentRead, ServerRead]:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.ip_address = payload.public_ipv4 or server.ip_address
            server.ip_address_v6 = payload.public_ipv6 or server.ip_address_v6
            server.connection_mode = payload.connection_mode.value
            server.listen_port = payload.listen_port
            server.xray_mode = payload.xray_mode.value
            server.updated_at = now

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if not agent:
                agent = AgentModel(
                    id=str(uuid4()),
                    server_id=server.id,
                    registered_at=now,
                    last_seen_at=now,
                    hostname=payload.hostname,
                    connection_mode=payload.connection_mode.value,
                    listen_port=payload.listen_port,
                    xray_mode=payload.xray_mode.value,
                )
                session.add(agent)

            agent.hostname = payload.hostname
            agent.agent_version = payload.agent_version
            agent.connection_mode = payload.connection_mode.value
            agent.listen_port = payload.listen_port
            agent.public_ipv4 = payload.public_ipv4
            agent.public_ipv6 = payload.public_ipv6
            agent.xray_mode = payload.xray_mode.value
            agent.capability_rpc = payload.capabilities.rpc
            agent.capability_stream = payload.capabilities.stream
            agent.capability_return_route_test = payload.capabilities.return_route_test
            agent.warp_installed = payload.warp_installed
            agent.same_host_as_master = payload.same_host_as_master
            agent.last_seen_at = now

            session.commit()
            session.refresh(agent)
            session.refresh(server)
            return self._agent_read(agent), self._public_server(server)

    def record_heartbeat(self, payload: AgentHeartbeatRequest) -> ServerRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.current_upload_speed = payload.upload_speed
            server.current_download_speed = payload.download_speed
            server.listen_port = payload.listen_port or server.listen_port
            server.ip_address = payload.public_ipv4 or server.ip_address
            server.ip_address_v6 = payload.public_ipv6 or server.ip_address_v6
            server.updated_at = now

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now
                agent.listen_port = server.listen_port
                agent.public_ipv4 = payload.public_ipv4 or agent.public_ipv4
                agent.public_ipv6 = payload.public_ipv6 or agent.public_ipv6

            session.commit()
            session.refresh(server)
            return self._public_server(server)

    def record_telemetry(
        self,
        payload: AgentTelemetryReport,
    ) -> tuple[ServerRead, AgentTelemetryRead]:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            reported_at = self._aware_datetime(payload.reported_at or now)
            previous = self._latest_telemetry_model(session, server.id)

            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now
            self._update_server_speed_from_system_traffic(server, previous, payload)

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now

            telemetry = TelemetrySnapshotModel(
                id=str(uuid4()),
                server_id=server.id,
                reported_at=reported_at,
                received_at=now,
                stats=payload.stats.model_dump(mode="json") if payload.stats else None,
                online_users=payload.online_users,
                user_speeds=payload.user_speeds,
                conn_counts=payload.conn_counts,
                system_rx_total=payload.system.rx_total if payload.system else None,
                system_tx_total=payload.system.tx_total if payload.system else None,
                system_boot_time_unix=payload.system.boot_time_unix if payload.system else None,
                sysmetrics=payload.sysmetrics.model_dump(mode="json")
                if payload.sysmetrics
                else None,
                latency=[sample.model_dump(mode="json") for sample in payload.latency],
            )
            session.add(telemetry)
            self._record_subscription_traffic_ledger(session, server, payload, reported_at, now)
            session.commit()
            session.refresh(server)
            session.refresh(telemetry)
            return self._public_server(server), self._telemetry_read(telemetry)

    def latest_telemetry(self, server_id: UUID) -> AgentTelemetryRead | None:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            telemetry = self._latest_telemetry_model(session, str(server_id))
            return self._telemetry_read(telemetry) if telemetry else None

    def record_scan_result(
        self,
        payload: AgentScanResultReport,
    ) -> tuple[ServerRead, AgentScanResultRead]:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            now = datetime.now(tz=UTC)
            reported_at = self._aware_datetime(payload.reported_at or now)

            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now

            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now

            scan = self._upsert_agent_scan_result(session, server, payload, reported_at, now)
            session.commit()
            session.refresh(server)
            session.refresh(scan)
            return self._public_server(server), self._scan_result_read(scan)

    def latest_scan_result(self, server_id: UUID) -> AgentScanResultRead | None:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            return self._scan_result_read(scan) if scan else None

    def list_xray_config_snapshots(
        self,
        server_id: UUID,
        limit: int = 20,
        include_config: bool = False,
    ) -> list[XrayConfigSnapshotRead]:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            query = (
                select(XrayConfigSnapshotModel)
                .where(XrayConfigSnapshotModel.server_id == server.id)
                .order_by(XrayConfigSnapshotModel.created_at.desc())
            )
            if limit > 0:
                query = query.limit(min(limit, 100))
            snapshots = session.scalars(query).all()
            return [
                self._xray_config_snapshot_read(snapshot, include_config=include_config)
                for snapshot in snapshots
            ]

    def get_xray_config_snapshot(
        self,
        server_id: UUID,
        snapshot_id: UUID,
        include_config: bool = False,
    ) -> XrayConfigSnapshotRead:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            snapshot = session.get(XrayConfigSnapshotModel, str(snapshot_id))
            if not snapshot or snapshot.server_id != server.id:
                raise XrayConfigSnapshotNotFoundError(
                    f"xray config snapshot not found: {snapshot_id}"
                )
            return self._xray_config_snapshot_read(snapshot, include_config=include_config)

    def public_probe_payload(self) -> ProbePayload:
        with self._session() as session:
            settings = self._probe_settings_read(session)
            if not settings.enabled:
                return ProbePayload(
                    **settings.model_dump(exclude={"updated_at"}),
                    updated_at=settings.updated_at,
                    servers=[],
                )

            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            return_routes = (
                self._probe_return_routes(session, [server.id for server in servers])
                if settings.show_return_route
                else {}
            )
            probe_servers = []
            for server in servers:
                latest = self._latest_telemetry_model(session, server.id)
                ping = self._probe_ping_series(session, server.id, bucket_count=12, bucket_sec=300)
                daily_traffic = self._probe_daily_traffic(session, server.id, day_count=7)
                probe_servers.append(
                    self._probe_server(
                        server,
                        latest,
                        ping,
                        daily_traffic,
                        return_routes.get(server.id),
                    )
                )
            return ProbePayload(
                **settings.model_dump(exclude={"updated_at"}),
                updated_at=settings.updated_at,
                servers=probe_servers,
            )

    def probe_settings(self) -> ProbeSettingsRead:
        with self._session() as session:
            return self._probe_settings_read(session)

    def probe_access_allowed(self, token: str | None) -> bool:
        with self._session() as session:
            settings = session.get(ProbeSettingsModel, "default")
            if not settings:
                return True
            if not self._stored_bool(settings.require_access_token, False):
                return True
            stored_hash = settings.access_token_hash or ""
            if not stored_hash or not token:
                return False
            candidate_hash = self._hash_probe_access_token(token.strip())
            return hmac.compare_digest(candidate_hash, stored_hash)

    def create_probe_access_token(self) -> ProbeAccessTokenCreateResponse:
        now = datetime.now(tz=UTC)
        token = f"probe_{token_urlsafe(32)}"
        with self._session() as session:
            settings = self._probe_settings_model(session, now)
            settings.access_token_hash = self._hash_probe_access_token(token)
            settings.require_access_token = True
            settings.updated_at = now
            session.commit()
            session.refresh(settings)
            return ProbeAccessTokenCreateResponse(
                token=token,
                settings=self._probe_settings_read(session, settings),
            )

    def clear_probe_access_token(self) -> ProbeSettingsResponse:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            settings = self._probe_settings_model(session, now)
            settings.access_token_hash = ""
            settings.require_access_token = False
            settings.updated_at = now
            session.commit()
            session.refresh(settings)
            return ProbeSettingsResponse(settings=self._probe_settings_read(session, settings))

    def update_probe_settings(self, payload: ProbeSettingsUpdate) -> ProbeSettingsResponse:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            settings = self._probe_settings_model(session, now)
            update = payload.model_dump(exclude_unset=True)
            appearance = update.pop("appearance", None)
            for field, value in update.items():
                if value is None:
                    if field in {"description", "logo"}:
                        setattr(settings, field, "")
                    continue
                setattr(settings, field, value)
            if appearance is not None:
                current = dict(settings.appearance or {})
                current.update(
                    {key: value for key, value in appearance.items() if value is not None}
                )
                settings.appearance = ProbeAppearance.model_validate(current).model_dump()
            settings.updated_at = now
            session.commit()
            session.refresh(settings)
            return ProbeSettingsResponse(settings=self._probe_settings_read(session, settings))

    def list_probe_tasks(self) -> list[ProbeTaskRead]:
        with self._session() as session:
            tasks = session.scalars(
                select(ProbeTaskModel).order_by(ProbeTaskModel.created_at)
            ).all()
            return [self._probe_task_read(task) for task in tasks]

    def create_probe_task(self, payload: ProbeTaskCreate) -> ProbeTaskRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            server = session.get(ServerModel, str(payload.server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {payload.server_id}")
            task = ProbeTaskModel(
                id=str(uuid4()),
                server_id=server.id,
                kind=payload.kind,
                enabled=payload.enabled,
                interval_sec=payload.interval_sec,
                domains=list(payload.domains),
                domain_timeout_ms=payload.domain_timeout_ms,
                allow_icmp=payload.allow_icmp,
                return_route_targets=[
                    target.model_dump(mode="json") for target in payload.return_route_targets
                ],
                return_route_timeout_seconds=payload.return_route_timeout_seconds,
                ip_version=payload.ip_version,
                command_timeout_ms=payload.command_timeout_ms,
                last_dispatched_at=None,
                next_run_at=(
                    self._aware_datetime(payload.next_run_at) if payload.next_run_at else now
                ),
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            session.commit()
            session.refresh(task)
            return self._probe_task_read(task)

    def update_probe_task(self, task_id: UUID, payload: ProbeTaskUpdate) -> ProbeTaskRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            task = session.get(ProbeTaskModel, str(task_id))
            if not task:
                raise ProbeTaskNotFoundError(f"probe task not found: {task_id}")

            update = payload.model_dump(exclude_unset=True)
            if "return_route_targets" in update and update["return_route_targets"] is not None:
                update["return_route_targets"] = [
                    target.model_dump(mode="json")
                    if isinstance(target, ProbeTaskReturnRouteTarget)
                    else target
                    for target in update["return_route_targets"]
                ]
            if "next_run_at" in update and update["next_run_at"] is None:
                update["next_run_at"] = now
            for field, value in update.items():
                if value is not None:
                    next_value = self._aware_datetime(value) if field == "next_run_at" else value
                    setattr(task, field, next_value)

            self._validate_probe_task_model(task)
            task.updated_at = now
            session.commit()
            session.refresh(task)
            return self._probe_task_read(task)

    def dispatch_due_probe_tasks(
        self,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[datetime, list[tuple[ProbeTaskRead, AgentCommandRead]]]:
        active_now = self._aware_datetime(now or datetime.now(tz=UTC))
        with self._session() as session:
            due_tasks = session.scalars(
                select(ProbeTaskModel)
                .where(
                    ProbeTaskModel.enabled.is_(True),
                    ProbeTaskModel.next_run_at <= active_now,
                )
                .order_by(ProbeTaskModel.next_run_at, ProbeTaskModel.created_at)
                .limit(limit)
            ).all()

            dispatched: list[tuple[ProbeTaskModel, CommandModel]] = []
            for task in due_tasks:
                server = session.get(ServerModel, task.server_id)
                if not server:
                    task.enabled = False
                    task.updated_at = active_now
                    continue
                command = self._create_command_model(
                    session,
                    server,
                    self._probe_task_command(task),
                    active_now,
                )
                task.last_dispatched_at = active_now
                task.next_run_at = active_now + timedelta(seconds=task.interval_sec)
                task.updated_at = active_now
                dispatched.append((task, command))

            session.commit()
            for task, command in dispatched:
                session.refresh(task)
                session.refresh(command)
            return active_now, [
                (self._probe_task_read(task), self._command_read(command))
                for task, command in dispatched
            ]

    def public_probe_series(
        self,
        server_index: int,
        metric: str,
        range_name: str,
        target: str,
        all_targets: bool,
    ) -> ProbeSeriesResponse:
        if server_index < 0:
            raise ProbeNotFoundError("probe server not found")
        buckets, bucket_sec = _PROBE_SERIES_RANGES.get(range_name, _PROBE_SERIES_RANGES["1h"])

        with self._session() as session:
            if not self._probe_settings_read(session).enabled:
                raise ProbeNotFoundError("probe is disabled")
            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            if server_index >= len(servers):
                raise ProbeNotFoundError("probe server not found")
            server = servers[server_index]
            generated_at = int(datetime.now(tz=UTC).timestamp())

            if metric == "system":
                return ProbeSeriesResponse(
                    success=True,
                    series=self._probe_system_series(session, server.id, buckets, bucket_sec),
                    bucket_sec=bucket_sec,
                    generated_at=generated_at,
                )
            if metric != "ping":
                raise ProbeNotFoundError("probe metric not found")

            series_by_key = self._probe_ping_series(session, server.id, buckets, bucket_sec)
            if target and target not in {"__avg__", "__all__"}:
                series = series_by_key.get(target)
                if not series:
                    raise ProbeNotFoundError("probe target not found")
            else:
                series = self._average_probe_ping_series(series_by_key.values(), buckets)

            response = ProbeSeriesResponse(
                success=True,
                series=series,
                bucket_sec=bucket_sec,
                generated_at=generated_at,
            )
            if all_targets:
                response.all_series = sorted(
                    series_by_key.values(),
                    key=lambda item: (item.label, item.key or ""),
                )
            return response

    def public_probe_target_comparison(self, range_name: str) -> ProbeTargetComparisonResponse:
        buckets, bucket_sec = _PROBE_SERIES_RANGES.get(range_name, _PROBE_SERIES_RANGES["1h"])
        generated_at = int(datetime.now(tz=UTC).timestamp())

        with self._session() as session:
            if not self._probe_settings_read(session).enabled:
                raise ProbeNotFoundError("probe is disabled")
            servers = session.scalars(select(ServerModel).order_by(ServerModel.created_at)).all()
            by_target: dict[str, list[ProbeTargetServerComparison]] = {}

            for index, server in enumerate(servers):
                series_by_key = self._probe_ping_series(session, server.id, buckets, bucket_sec)
                for key, series in series_by_key.items():
                    by_target.setdefault(key, []).append(
                        ProbeTargetServerComparison(
                            server_index=index,
                            server_name=server.name,
                            region=server.region
                            or server.region_city
                            or server.region_name
                            or server.region_country,
                            current_ms=series.current_ms,
                            loss_pct=series.loss_pct,
                            buckets=series.buckets,
                        )
                    )

            targets = [
                self._probe_target_comparison(key, rows)
                for key, rows in sorted(by_target.items())
            ]
            return ProbeTargetComparisonResponse(
                success=True,
                targets=targets,
                bucket_sec=bucket_sec,
                generated_at=generated_at,
            )

    def list_product_users(self) -> list[ProductUserRead]:
        with self._session() as session:
            users = session.scalars(
                select(ProductUserModel).order_by(ProductUserModel.created_at)
            ).all()
            return [self._product_user_read(user) for user in users]

    def create_product_user(self, payload: ProductUserCreate) -> ProductUserRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            if session.get(ProductUserModel, payload.username):
                raise DuplicateProductUserError(f"username already exists: {payload.username}")
            user = ProductUserModel(
                username=payload.username,
                email=payload.email,
                display_name=payload.display_name or payload.username,
                role=payload.role.value,
                is_active=payload.is_active,
                current_plan_id=None,
                plan_started_at=None,
                plan_expires_at=None,
                is_reset=False,
                reset_day=0,
                last_traffic_reset_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return self._product_user_read(user)

    def list_managed_nodes(self) -> list[ManagedNodeRead]:
        with self._session() as session:
            nodes = session.scalars(
                select(ManagedNodeModel).order_by(ManagedNodeModel.created_at)
            ).all()
            return [self._managed_node_read(node) for node in nodes]

    def create_managed_node(self, payload: ManagedNodeCreate) -> ManagedNodeRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            server = session.get(ServerModel, str(payload.server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {payload.server_id}")
            node = ManagedNodeModel(
                id=str(uuid4()),
                name=payload.name,
                server_id=server.id,
                protocol=payload.protocol.lower(),
                node_type=payload.node_type.value,
                inbound_tag=payload.inbound_tag,
                routed_outbound_tag=payload.routed_outbound_tag,
                routed_rule_marktag=payload.routed_rule_marktag,
                tag=payload.tag,
                tags=payload.tags,
                enabled=payload.enabled,
                client_template=payload.client_template,
                config=payload.config,
                created_at=now,
                updated_at=now,
            )
            session.add(node)
            session.commit()
            session.refresh(node)
            return self._managed_node_read(node)

    def list_subscription_template_presets(self) -> list[SubscriptionTemplatePresetRead]:
        return [
            SubscriptionTemplatePresetRead.model_validate(deepcopy(preset))
            for preset in _SUBSCRIPTION_NODE_PRESETS
        ]

    def create_managed_node_from_preset(
        self,
        preset_id: str,
        payload: SubscriptionTemplatePresetApplyRequest,
    ) -> ManagedNodeRead:
        preset = self._subscription_template_preset(preset_id)
        with self._session() as session:
            server = session.get(ServerModel, str(payload.server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {payload.server_id}")
            config = deepcopy(preset.config)
            config["server"] = payload.host or self._server_subscription_host(server)
            if payload.port:
                config["port"] = payload.port
            node_payload = ManagedNodeCreate(
                name=payload.name or preset.name,
                server_id=payload.server_id,
                protocol=preset.protocol,
                node_type=preset.node_type,
                inbound_tag=payload.inbound_tag
                if payload.inbound_tag is not None
                else preset.inbound_tag,
                routed_outbound_tag=payload.routed_outbound_tag
                if payload.routed_outbound_tag is not None
                else preset.routed_outbound_tag,
                routed_rule_marktag=payload.routed_rule_marktag
                if payload.routed_rule_marktag is not None
                else preset.routed_rule_marktag,
                tag=payload.tag if payload.tag is not None else preset.tag,
                tags=payload.tags if payload.tags is not None else preset.tags,
                enabled=payload.enabled,
                client_template=deepcopy(preset.client_template),
                config=config,
            )
        return self.create_managed_node(node_payload)

    def export_subscription_catalog(
        self,
        include_credentials: bool = False,
    ) -> SubscriptionCatalogBundle:
        with self._session() as session:
            plans = session.scalars(
                select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.created_at)
            ).all()
            plan_names = {plan.id: plan.name for plan in plans}
            nodes = session.scalars(
                select(ManagedNodeModel).order_by(ManagedNodeModel.created_at)
            ).all()
            node_names = {node.id: node.name for node in nodes}
            servers = session.scalars(select(ServerModel)).all()
            server_names = {server.id: server.name for server in servers}
            users = session.scalars(
                select(ProductUserModel).order_by(ProductUserModel.created_at)
            ).all()

            credentials: list[SubscriptionCatalogCredentialEntry] = []
            if include_credentials:
                credential_rows = session.scalars(
                    select(SubscriptionCredentialModel).order_by(
                        SubscriptionCredentialModel.username,
                        SubscriptionCredentialModel.created_at,
                    )
                ).all()
                credentials = [
                    SubscriptionCatalogCredentialEntry(
                        username=credential.username,
                        node_name=node_names.get(credential.node_id, credential.node_id),
                        server_name=server_names.get(credential.server_id, credential.server_id),
                        inbound_tag=credential.inbound_tag,
                        protocol=credential.protocol,
                        email=credential.email,
                        credential=credential.credential or {},
                    )
                    for credential in credential_rows
                ]

            return SubscriptionCatalogBundle(
                version=1,
                exported_at=datetime.now(tz=UTC),
                users=[
                    SubscriptionCatalogUserEntry(
                        username=user.username,
                        email=user.email,
                        display_name=user.display_name,
                        role=ProductUserRole(user.role),
                        is_active=user.is_active,
                        current_plan_name=plan_names.get(user.current_plan_id or ""),
                        plan_started_at=user.plan_started_at,
                        plan_expires_at=user.plan_expires_at,
                        is_reset=user.is_reset,
                        reset_day=user.reset_day,
                        last_traffic_reset_at=user.last_traffic_reset_at,
                    )
                    for user in users
                ],
                nodes=[
                    SubscriptionCatalogNodeEntry(
                        name=node.name,
                        server_name=server_names.get(node.server_id, node.server_id),
                        protocol=node.protocol,
                        node_type=ManagedNodeType(node.node_type),
                        inbound_tag=node.inbound_tag,
                        routed_outbound_tag=node.routed_outbound_tag,
                        routed_rule_marktag=node.routed_rule_marktag,
                        tag=node.tag,
                        tags=node.tags or [],
                        enabled=node.enabled,
                        client_template=node.client_template or {},
                        config=node.config or {},
                    )
                    for node in nodes
                ],
                plans=[
                    self._subscription_catalog_plan_entry(plan, node_names)
                    for plan in plans
                ],
                credentials=credentials,
            )

    def import_subscription_catalog(
        self,
        payload: SubscriptionCatalogImportRequest,
    ) -> SubscriptionCatalogImportResponse:
        now = datetime.now(tz=UTC)
        summary = SubscriptionCatalogImportSummary()
        with self._session() as session:
            for user_entry in payload.catalog.users:
                existing = session.get(ProductUserModel, user_entry.username)
                if existing:
                    existing.email = user_entry.email
                    existing.display_name = user_entry.display_name or user_entry.username
                    existing.role = user_entry.role.value
                    existing.is_active = user_entry.is_active
                    existing.is_reset = user_entry.is_reset
                    existing.reset_day = user_entry.reset_day
                    existing.last_traffic_reset_at = user_entry.last_traffic_reset_at
                    existing.updated_at = now
                    summary.updated_users += 1
                    continue
                session.add(
                    ProductUserModel(
                        username=user_entry.username,
                        email=user_entry.email,
                        display_name=user_entry.display_name or user_entry.username,
                        role=user_entry.role.value,
                        is_active=user_entry.is_active,
                        current_plan_id=None,
                        plan_started_at=None,
                        plan_expires_at=None,
                        is_reset=user_entry.is_reset,
                        reset_day=user_entry.reset_day,
                        last_traffic_reset_at=user_entry.last_traffic_reset_at,
                        created_at=now,
                        updated_at=now,
                    )
                )
                summary.created_users += 1

            session.flush()
            node_ids_by_name: dict[str, str] = {}
            for node_entry in payload.catalog.nodes:
                server = self._catalog_server(session, payload.server_map, node_entry.server_name)
                if not server:
                    summary.warnings.append(
                        f"node {node_entry.name} skipped; server {node_entry.server_name} not found"
                    )
                    continue
                node = self._catalog_node_by_name(session, node_entry.name, server.id)
                if node:
                    self._apply_catalog_node(node, node_entry, server.id, now)
                    summary.updated_nodes += 1
                else:
                    node = self._catalog_node_model(node_entry, server.id, now)
                    session.add(node)
                    summary.created_nodes += 1
                node_ids_by_name[node_entry.name] = node.id

            session.flush()
            for plan_entry in payload.catalog.plans:
                plan = session.scalar(
                    select(SubscriptionPlanModel).where(
                        SubscriptionPlanModel.name == plan_entry.name
                    )
                )
                node_ids = [
                    node_ids_by_name[name]
                    for name in plan_entry.node_names
                    if name in node_ids_by_name
                ]
                missing_nodes = sorted(set(plan_entry.node_names) - set(node_ids_by_name))
                for node_name in missing_nodes:
                    summary.warnings.append(
                        f"plan {plan_entry.name} skipped missing node {node_name}"
                    )
                if plan:
                    self._apply_catalog_plan(plan, plan_entry, node_ids, node_ids_by_name, now)
                    summary.updated_plans += 1
                else:
                    session.add(
                        self._catalog_plan_model(plan_entry, node_ids, node_ids_by_name, now)
                    )
                    summary.created_plans += 1

            session.flush()
            plan_ids_by_name = dict(
                session.execute(select(SubscriptionPlanModel.name, SubscriptionPlanModel.id)).all()
            )
            for user_entry in payload.catalog.users:
                user = session.get(ProductUserModel, user_entry.username)
                if not user:
                    continue
                user.current_plan_id = (
                    plan_ids_by_name.get(user_entry.current_plan_name)
                    if user_entry.current_plan_name
                    else None
                )
                user.plan_started_at = user_entry.plan_started_at
                user.plan_expires_at = user_entry.plan_expires_at
                user.last_traffic_reset_at = user_entry.last_traffic_reset_at
                user.updated_at = now

            if payload.import_credentials:
                summary.imported_credentials = self._import_subscription_credentials(
                    session,
                    payload.catalog.credentials,
                    payload.server_map,
                    node_ids_by_name,
                    now,
                    summary.warnings,
                )

            session.commit()
        return SubscriptionCatalogImportResponse(summary=summary)

    def list_subscription_plans(self) -> list[SubscriptionPlanRead]:
        with self._session() as session:
            plans = session.scalars(
                select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.created_at)
            ).all()
            return [self._subscription_plan_read(plan) for plan in plans]

    def create_subscription_plan(self, payload: SubscriptionPlanCreate) -> SubscriptionPlanRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            existing = session.scalar(
                select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == payload.name)
            )
            if existing:
                raise DuplicateSubscriptionPlanNameError(
                    f"subscription plan name already exists: {payload.name}"
                )
            self._ensure_managed_nodes_exist(session, payload.node_ids)
            plan = SubscriptionPlanModel(
                id=str(uuid4()),
                name=payload.name,
                description=payload.description,
                traffic_limit_bytes=int(payload.traffic_limit_gb * 1024 * 1024 * 1024),
                cycle_days=payload.cycle_days,
                is_reset=payload.is_reset,
                reset_day=payload.reset_day,
                node_ids=[str(node_id) for node_id in payload.node_ids],
                node_multipliers=self._uuid_keyed_float_map(payload.node_multipliers),
                node_speed_limits=self._uuid_keyed_float_map(payload.node_speed_limits),
                node_device_limits=self._uuid_keyed_int_map(payload.node_device_limits),
                speed_limit_mbps=payload.speed_limit_mbps,
                device_limit=payload.device_limit,
                traffic_mode=payload.traffic_mode.value,
                created_at=now,
                updated_at=now,
            )
            session.add(plan)
            session.commit()
            session.refresh(plan)
            return self._subscription_plan_read(plan)

    def assign_subscription_plan(
        self,
        username: str,
        payload: SubscriptionPlanAssignRequest,
    ) -> tuple[ProductUserRead, SubscriptionPlanRead, list[SubscriptionProvisionBatch], list[str]]:
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        now = datetime.now(tz=UTC)
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            plan = session.get(SubscriptionPlanModel, str(payload.plan_id))
            if not plan:
                raise SubscriptionPlanNotFoundError(
                    f"subscription plan not found: {payload.plan_id}"
                )

            started_at = self._date_to_utc_start(payload.start_date) if payload.start_date else now
            expires_at = (
                self._date_to_utc_start(payload.expire_date)
                if payload.expire_date
                else started_at + timedelta(days=plan.cycle_days)
            )
            is_reset = payload.is_reset if payload.is_reset is not None else plan.is_reset
            reset_day = payload.reset_day if payload.reset_day is not None else plan.reset_day
            if is_reset and reset_day == 0:
                reset_day = min(now.day, 28)

            user.current_plan_id = plan.id
            user.plan_started_at = started_at
            user.plan_expires_at = expires_at
            user.is_reset = is_reset
            user.reset_day = reset_day
            user.updated_at = now

            batches, warnings = self._subscription_provision_batches(
                session,
                user,
                plan,
                no_restart=payload.no_restart,
            )
            session.commit()
            session.refresh(user)
            session.refresh(plan)
            return (
                self._product_user_read(user),
                self._subscription_plan_read(plan),
                batches,
                warnings,
            )

    def get_or_create_subscription_token(self, username: str) -> SubscriptionTokenRecord:
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        now = datetime.now(tz=UTC)
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            token = session.get(ProductUserSubscriptionTokenModel, username)
            if not token:
                token = ProductUserSubscriptionTokenModel(
                    username=username,
                    token=self._unique_subscription_token(session),
                    short_code=self._unique_subscription_short_code(session),
                    created_at=now,
                    updated_at=now,
                )
                session.add(token)
                session.commit()
                session.refresh(token)
            return self._subscription_token_record(token)

    def reset_subscription_token(self, username: str) -> SubscriptionTokenRecord:
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        now = datetime.now(tz=UTC)
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            token = session.get(ProductUserSubscriptionTokenModel, username)
            if not token:
                token = ProductUserSubscriptionTokenModel(
                    username=username,
                    token=self._unique_subscription_token(session),
                    short_code=self._unique_subscription_short_code(session),
                    created_at=now,
                    updated_at=now,
                )
                session.add(token)
            else:
                token.token = self._unique_subscription_token(session)
                token.short_code = self._unique_subscription_short_code(session)
                token.updated_at = now
            session.commit()
            session.refresh(token)
            return self._subscription_token_record(token)

    def list_subscription_credentials(self, username: str) -> list[SubscriptionCredentialRead]:
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            credentials = session.scalars(
                select(SubscriptionCredentialModel)
                .where(SubscriptionCredentialModel.username == username)
                .order_by(SubscriptionCredentialModel.created_at)
            ).all()
            return [self._subscription_credential_read(credential) for credential in credentials]

    def render_subscription(
        self,
        subscription_key: str,
        client_format: SubscriptionClientFormat = SubscriptionClientFormat.CLASH,
    ) -> RenderedSubscription:
        key = subscription_key.strip()
        if not key:
            raise SubscriptionTokenNotFoundError("subscription key is required")

        with self._session() as session:
            token = session.scalar(
                select(ProductUserSubscriptionTokenModel).where(
                    (ProductUserSubscriptionTokenModel.token == key)
                    | (ProductUserSubscriptionTokenModel.short_code == key)
                )
            )
            if not token:
                raise SubscriptionTokenNotFoundError("subscription not found")
            user = session.get(ProductUserModel, token.username)
            if not user or not user.is_active:
                raise SubscriptionUnavailableError("subscription user is not active")
            if not user.current_plan_id:
                raise SubscriptionUnavailableError("user has no active subscription plan")
            if user.plan_expires_at and datetime.now(tz=UTC) > self._aware_datetime(
                user.plan_expires_at
            ):
                raise SubscriptionUnavailableError("subscription plan has expired")
            plan = session.get(SubscriptionPlanModel, user.current_plan_id)
            if not plan:
                raise SubscriptionUnavailableError("subscription plan is missing")
            quota = self._subscription_quota_status(
                session,
                user,
                plan,
                datetime.now(tz=UTC),
            )
            if quota.over_quota:
                raise SubscriptionUnavailableError("subscription traffic quota exceeded")

            proxies, warnings = self._subscription_proxy_configs(session, user, plan)
            if not proxies:
                raise SubscriptionUnavailableError("subscription has no renderable nodes")

            content, media_type, extension = self._render_subscription_content(
                proxies,
                client_format,
            )
            filename = f"{self._safe_filename(plan.name or user.username)}.{extension}"
            return RenderedSubscription(
                username=user.username,
                plan_name=plan.name,
                content=content,
                media_type=media_type,
                filename=filename,
                subscription_userinfo=self._subscription_userinfo_header(session, user, plan),
                warnings=warnings,
            )

    def subscription_user_traffic(self, username: str) -> ProductUserTrafficResponse:
        with self._session() as session:
            if not session.get(ProductUserModel, username):
                raise ProductUserNotFoundError(f"user not found: {username}")
            entries = session.scalars(
                select(SubscriptionTrafficLedgerModel)
                .where(SubscriptionTrafficLedgerModel.username == username)
                .order_by(
                    SubscriptionTrafficLedgerModel.server_id,
                    SubscriptionTrafficLedgerModel.email,
                )
            ).all()
            upload = sum(entry.upload for entry in entries)
            download = sum(entry.download for entry in entries)
            return ProductUserTrafficResponse(
                username=username,
                upload=upload,
                download=download,
                total=upload + download,
                entries=[self._subscription_traffic_entry_read(entry) for entry in entries],
            )

    def subscription_user_quota(
        self,
        username: str,
        now: datetime | None = None,
    ) -> SubscriptionQuotaStatusRead:
        active_now = self._aware_datetime(now or datetime.now(tz=UTC))
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            plan = (
                session.get(SubscriptionPlanModel, user.current_plan_id)
                if user.current_plan_id
                else None
            )
            return self._subscription_quota_status(session, user, plan, active_now)

    def reset_subscription_traffic(
        self,
        username: str,
        now: datetime | None = None,
    ) -> SubscriptionQuotaStatusRead:
        active_now = self._aware_datetime(now or datetime.now(tz=UTC))
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            self._reset_subscription_traffic_for_user(session, user, active_now)
            plan = (
                session.get(SubscriptionPlanModel, user.current_plan_id)
                if user.current_plan_id
                else None
            )
            session.commit()
            session.refresh(user)
            return self._subscription_quota_status(session, user, plan, active_now)

    def reset_due_subscription_traffic(
        self,
        payload: SubscriptionDueTrafficResetRequest,
    ) -> SubscriptionDueTrafficResetResponse:
        active_now = self._aware_datetime(payload.now or datetime.now(tz=UTC))
        summary = SubscriptionDueTrafficResetSummary(dry_run=payload.dry_run)
        with self._session() as session:
            users = session.scalars(
                select(ProductUserModel).order_by(ProductUserModel.username)
            ).all()
            for user in users:
                summary.checked_users += 1
                plan = (
                    session.get(SubscriptionPlanModel, user.current_plan_id)
                    if user.current_plan_id
                    else None
                )
                quota = self._subscription_quota_status(session, user, plan, active_now)
                if not quota.reset_due:
                    summary.skipped_users += 1
                    continue
                summary.reset_users += 1
                summary.usernames.append(user.username)
                if not payload.dry_run:
                    self._reset_subscription_traffic_for_user(session, user, active_now)

            if not payload.dry_run:
                session.commit()
        return SubscriptionDueTrafficResetResponse(summary=summary)

    def list_change_sets(self) -> list[AgentChangeSetRead]:
        with self._session() as session:
            change_sets = session.scalars(
                select(AgentChangeSetModel).order_by(AgentChangeSetModel.created_at.desc())
            ).all()
            return [self._change_set_read(session, change_set) for change_set in change_sets]

    def get_change_set(self, change_set_id: UUID) -> AgentChangeSetRead:
        with self._session() as session:
            change_set = self._change_set_model(session, change_set_id)
            return self._change_set_read(session, change_set)

    def create_change_set(self, payload: AgentChangeSetCreate) -> AgentChangeSetRead:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            self._ensure_step_servers_exist(session, payload.steps)
            change_set = AgentChangeSetModel(
                id=str(uuid4()),
                name=payload.name,
                description=payload.description,
                status=AgentChangeSetStatus.PLANNED.value,
                rollback_on_failure=payload.rollback_on_failure,
                rollback_reason="",
                created_at=now,
                updated_at=now,
            )
            session.add(change_set)
            for index, step in enumerate(payload.steps, start=1):
                rollback = step.rollback
                session.add(
                    AgentChangeSetStepModel(
                        id=str(uuid4()),
                        change_set_id=change_set.id,
                        sequence=index,
                        server_id=str(step.server_id),
                        label=step.label or f"Step {index}",
                        forward_method=step.forward.method,
                        forward_path=step.forward.path,
                        forward_query=step.forward.query,
                        forward_body=step.forward.body,
                        forward_timeout_ms=step.forward.timeout_ms,
                        forward_stream=step.forward.stream,
                        rollback_method=rollback.method if rollback else None,
                        rollback_path=rollback.path if rollback else None,
                        rollback_query=rollback.query if rollback else "",
                        rollback_body=rollback.body if rollback else None,
                        rollback_timeout_ms=rollback.timeout_ms if rollback else None,
                        rollback_stream=rollback.stream if rollback else False,
                        created_at=now,
                        updated_at=now,
                    )
                )
            session.commit()
            session.refresh(change_set)
            return self._change_set_read(session, change_set)

    def dispatch_change_set(
        self,
        change_set_id: UUID,
    ) -> tuple[AgentChangeSetRead, list[AgentCommandRead]]:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            change_set = self._change_set_model(session, change_set_id)
            commands: list[CommandModel] = []
            for step in self._change_set_steps(session, change_set.id):
                if step.forward_command_id:
                    continue
                server = session.get(ServerModel, step.server_id)
                if not server:
                    raise ServerNotFoundError(f"server not found: {step.server_id}")
                command = self._create_command_model(
                    session,
                    server,
                    self._step_forward_command(step),
                    now,
                )
                step.forward_command_id = command.id
                step.updated_at = now
                commands.append(command)

            change_set.status = AgentChangeSetStatus.DISPATCHED.value
            change_set.updated_at = now
            session.commit()
            for command in commands:
                session.refresh(command)
            session.refresh(change_set)
            return self._change_set_read(session, change_set), [
                self._command_read(command) for command in commands
            ]

    def rollback_change_set(
        self,
        change_set_id: UUID,
        payload: AgentChangeSetRollbackRequest,
    ) -> tuple[AgentChangeSetRead, list[AgentCommandRead], list[str]]:
        now = datetime.now(tz=UTC)
        warnings: list[str] = []
        with self._session() as session:
            change_set = self._change_set_model(session, change_set_id)
            commands: list[CommandModel] = []
            steps = list(reversed(self._change_set_steps(session, change_set.id)))
            for step in steps:
                if step.rollback_command_id:
                    continue
                rollback = self._step_rollback_command(step)
                if not rollback:
                    warnings.append(f"step {step.sequence} has no rollback command")
                    continue
                server = session.get(ServerModel, step.server_id)
                if not server:
                    raise ServerNotFoundError(f"server not found: {step.server_id}")
                command = self._create_command_model(session, server, rollback, now)
                step.rollback_command_id = command.id
                step.updated_at = now
                commands.append(command)

            change_set.status = AgentChangeSetStatus.ROLLBACK_QUEUED.value
            change_set.rollback_reason = payload.reason
            change_set.updated_at = now
            session.commit()
            for command in commands:
                session.refresh(command)
            session.refresh(change_set)
            return self._change_set_read(session, change_set), [
                self._command_read(command) for command in commands
            ], warnings

    def create_command(self, server_id: UUID, payload: AgentCommandCreate) -> AgentCommandRead:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")

            command = self._create_command_model(session, server, payload)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def list_commands(self, server_id: UUID) -> list[AgentCommandRead]:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            commands = session.scalars(
                select(CommandModel)
                .where(CommandModel.server_id == str(server_id))
                .order_by(CommandModel.created_at.desc())
            ).all()
            return [self._command_read(command) for command in commands]

    def list_command_stream_frames(
        self,
        server_id: UUID,
        command_id: UUID,
    ) -> list[AgentCommandStreamFrameRead]:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")

            command = session.get(CommandModel, str(command_id))
            if not command or command.server_id != server.id:
                raise CommandNotFoundError(f"command not found: {command_id}")

            frames = session.scalars(
                select(CommandStreamFrameModel)
                .where(CommandStreamFrameModel.command_id == command.id)
                .order_by(CommandStreamFrameModel.sequence)
            ).all()
            return [self._stream_frame_read(frame, command) for frame in frames]

    def lease_commands(
        self,
        token: str,
        max_commands: int,
    ) -> tuple[ServerRead, list[AgentCommandRead]]:
        with self._session() as session:
            server = self._server_by_token(session, token)
            now = datetime.now(tz=UTC)
            candidates = session.scalars(
                select(CommandModel)
                .where(
                    CommandModel.server_id == server.id,
                    CommandModel.status.in_(
                        [AgentCommandStatus.PENDING.value, AgentCommandStatus.LEASED.value]
                    ),
                )
                .order_by(CommandModel.created_at)
            ).all()

            leased: list[CommandModel] = []
            for command in candidates:
                if len(leased) >= max_commands:
                    break
                if command.status == AgentCommandStatus.LEASED.value and not self._lease_expired(
                    command,
                    now,
                ):
                    continue
                command.status = AgentCommandStatus.LEASED.value
                command.attempts += 1
                command.leased_at = now
                command.updated_at = now
                leased.append(command)

            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now
            session.commit()
            for command in leased:
                session.refresh(command)
            session.refresh(server)
            return self._public_server(server), [self._command_read(command) for command in leased]

    def complete_command(
        self,
        command_id: UUID,
        payload: AgentCommandResultRequest,
    ) -> AgentCommandRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            command = session.get(CommandModel, str(command_id))
            if not command or command.server_id != server.id:
                raise CommandNotFoundError(f"command not found: {command_id}")

            self._apply_command_result(session, server, command, payload)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def complete_command_by_request_id(
        self,
        request_id: str,
        payload: AgentCommandResultRequest,
    ) -> AgentCommandRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            command = session.scalar(
                select(CommandModel).where(
                    CommandModel.request_id == request_id,
                    CommandModel.server_id == server.id,
                )
            )
            if not command:
                raise CommandNotFoundError(f"command not found: {request_id}")

            self._apply_command_result(session, server, command, payload)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def append_command_stream_frame(
        self,
        payload: AgentCommandStreamDataRequest,
    ) -> AgentCommandStreamFrameRead:
        with self._session() as session:
            server = self._server_by_token(session, payload.token)
            command = session.scalar(
                select(CommandModel).where(
                    CommandModel.request_id == payload.request_id,
                    CommandModel.server_id == server.id,
                )
            )
            completed_statuses = {
                AgentCommandStatus.SUCCEEDED.value,
                AgentCommandStatus.FAILED.value,
            }
            if not command or not command.stream or command.status in completed_statuses:
                raise CommandNotFoundError(f"stream command not found: {payload.request_id}")

            last_sequence = (
                session.scalar(
                    select(CommandStreamFrameModel.sequence)
                    .where(CommandStreamFrameModel.command_id == command.id)
                    .order_by(CommandStreamFrameModel.sequence.desc())
                    .limit(1)
                )
                or 0
            )
            now = datetime.now(tz=UTC)
            frame = CommandStreamFrameModel(
                id=str(uuid4()),
                command_id=command.id,
                server_id=server.id,
                sequence=last_sequence + 1,
                data=payload.data,
                received_at=now,
            )
            session.add(frame)
            command.updated_at = now
            server.status = ServerStatus.CONNECTED.value
            server.last_heartbeat = now
            server.updated_at = now
            agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
            if agent:
                agent.last_seen_at = now
            session.commit()
            session.refresh(frame)
            session.refresh(command)
            return self._stream_frame_read(frame, command)

    def lease_command_for_push(self, command_id: UUID) -> AgentCommandRead:
        with self._session() as session:
            command = session.get(CommandModel, str(command_id))
            if not command:
                raise CommandNotFoundError(f"command not found: {command_id}")
            if command.status in {
                AgentCommandStatus.SUCCEEDED.value,
                AgentCommandStatus.FAILED.value,
            }:
                return self._command_read(command)

            now = datetime.now(tz=UTC)
            command.status = AgentCommandStatus.LEASED.value
            command.attempts += 1
            command.leased_at = now
            command.updated_at = now
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    @staticmethod
    def _probe_settings_model(session: Session, now: datetime) -> ProbeSettingsModel:
        settings = session.get(ProbeSettingsModel, "default")
        if settings:
            return settings
        defaults = ProbeSettingsRead()
        settings = ProbeSettingsModel(
            id="default",
            enabled=defaults.enabled,
            access_token_hash="",
            require_access_token=defaults.require_access_token,
            show_globe=defaults.show_globe,
            show_daily_trend=defaults.show_daily_trend,
            show_traffic_hotspots=defaults.show_traffic_hotspots,
            show_traffic_7d=defaults.show_traffic_7d,
            show_return_route=defaults.show_return_route,
            show_resource_heatmap=defaults.show_resource_heatmap,
            show_traffic_quota=defaults.show_traffic_quota,
            show_renewal_timeline=defaults.show_renewal_timeline,
            show_health_score=defaults.show_health_score,
            title=defaults.title,
            description=defaults.description,
            logo=defaults.logo,
            refresh_interval_sec=defaults.refresh_interval_sec,
            appearance=defaults.appearance.model_dump(),
            created_at=now,
            updated_at=now,
        )
        session.add(settings)
        return settings

    @staticmethod
    def _probe_settings_read(
        session: Session,
        settings: ProbeSettingsModel | None = None,
    ) -> ProbeSettingsRead:
        settings = settings or session.get(ProbeSettingsModel, "default")
        if not settings:
            return ProbeSettingsRead()
        return ProbeSettingsRead(
            enabled=settings.enabled,
            has_access_token=bool(settings.access_token_hash),
            require_access_token=InventoryStore._stored_bool(
                settings.require_access_token,
                False,
            ),
            show_globe=InventoryStore._stored_bool(settings.show_globe, False),
            show_daily_trend=InventoryStore._stored_bool(settings.show_daily_trend, False),
            show_traffic_hotspots=InventoryStore._stored_bool(
                settings.show_traffic_hotspots,
                False,
            ),
            show_traffic_7d=InventoryStore._stored_bool(settings.show_traffic_7d, False),
            show_return_route=InventoryStore._stored_bool(settings.show_return_route, False),
            show_resource_heatmap=InventoryStore._stored_bool(
                settings.show_resource_heatmap,
                True,
            ),
            show_traffic_quota=InventoryStore._stored_bool(settings.show_traffic_quota, True),
            show_renewal_timeline=InventoryStore._stored_bool(
                settings.show_renewal_timeline,
                False,
            ),
            show_health_score=InventoryStore._stored_bool(settings.show_health_score, True),
            title=settings.title,
            description=settings.description,
            logo=settings.logo,
            refresh_interval_sec=settings.refresh_interval_sec,
            appearance=ProbeAppearance.model_validate(settings.appearance or {}),
            updated_at=InventoryStore._aware_datetime(settings.updated_at),
        )

    @staticmethod
    def _probe_task_read(task: ProbeTaskModel) -> ProbeTaskRead:
        return ProbeTaskRead(
            id=UUID(task.id),
            server_id=UUID(task.server_id),
            kind=task.kind,
            enabled=InventoryStore._stored_bool(task.enabled, True),
            interval_sec=task.interval_sec,
            domains=list(task.domains or []),
            domain_timeout_ms=task.domain_timeout_ms,
            allow_icmp=InventoryStore._stored_bool(task.allow_icmp, False),
            return_route_targets=[
                ProbeTaskReturnRouteTarget.model_validate(target)
                for target in (task.return_route_targets or [])
            ],
            return_route_timeout_seconds=task.return_route_timeout_seconds,
            ip_version=task.ip_version,
            command_timeout_ms=task.command_timeout_ms,
            last_dispatched_at=InventoryStore._aware_datetime(task.last_dispatched_at)
            if task.last_dispatched_at
            else None,
            next_run_at=InventoryStore._aware_datetime(task.next_run_at),
            created_at=InventoryStore._aware_datetime(task.created_at),
            updated_at=InventoryStore._aware_datetime(task.updated_at),
        )

    @staticmethod
    def _validate_probe_task_model(task: ProbeTaskModel) -> None:
        ProbeTaskCreate(
            server_id=UUID(task.server_id),
            kind=task.kind,
            enabled=task.enabled,
            interval_sec=task.interval_sec,
            domains=list(task.domains or []),
            domain_timeout_ms=task.domain_timeout_ms,
            allow_icmp=task.allow_icmp,
            return_route_targets=[
                ProbeTaskReturnRouteTarget.model_validate(target)
                for target in (task.return_route_targets or [])
            ],
            return_route_timeout_seconds=task.return_route_timeout_seconds,
            ip_version=task.ip_version,
            command_timeout_ms=task.command_timeout_ms,
            next_run_at=task.next_run_at,
        )

    @staticmethod
    def _probe_task_command(task: ProbeTaskModel) -> AgentCommandCreate:
        match task.kind:
            case "system":
                return AgentCommandCreate(
                    method="GET",
                    path="/api/child/system/info",
                    timeout_ms=task.command_timeout_ms,
                )
            case "domain_latency":
                return AgentCommandCreate(
                    method="POST",
                    path="/api/child/domains/latency",
                    body={
                        "domains": list(task.domains or []),
                        "timeout_ms": task.domain_timeout_ms,
                        "allow_icmp": InventoryStore._stored_bool(task.allow_icmp, False),
                    },
                    timeout_ms=task.command_timeout_ms,
                )
            case "return_route":
                return AgentCommandCreate(
                    method="POST",
                    path="/api/child/network/return-route-test",
                    body={
                        "ip_version": task.ip_version,
                        "timeout_seconds": task.return_route_timeout_seconds,
                        "targets": list(task.return_route_targets or []),
                    },
                    timeout_ms=task.command_timeout_ms,
                )
        raise ValueError(f"unsupported probe task kind: {task.kind}")

    @staticmethod
    def _stored_bool(value: bool | None, default: bool) -> bool:
        return default if value is None else bool(value)

    def _probe_server(
        self,
        server: ServerModel,
        latest: TelemetrySnapshotModel | None,
        ping_by_key: dict[str, ProbePingSeries],
        daily_traffic: list[ProbeDailyTraffic] | None = None,
        return_routes: list[ProbeReturnRoute] | None = None,
    ) -> ProbeServer:
        probe = ProbeServer(
            name=server.name,
            region=server.region,
            region_country=server.region_country,
            region_name=server.region_name,
            region_city=server.region_city,
            online=server.status == ServerStatus.CONNECTED.value,
            upload_speed=server.current_upload_speed,
            download_speed=server.current_download_speed,
            traffic_limit=server.traffic_limit,
            daily_traffic=daily_traffic,
            return_routes=return_routes,
            expires_at=server.expires_at.date().isoformat() if server.expires_at else None,
            renewal_price=server.renewal_price,
            renewal_price_cny=server.renewal_price_cny,
            renewal_cycle=server.renewal_cycle,
            renewal_currency=server.renewal_currency,
            provider_name=server.provider_name,
            provider_url=server.provider_url,
            telecom_paid_peer=server.telecom_paid_peer,
        )

        if latest:
            if latest.system_tx_total is not None or latest.system_rx_total is not None:
                probe.cumulative_up = latest.system_tx_total or 0
                probe.cumulative_down = latest.system_rx_total or 0

            if latest.stats:
                up, down = self._traffic_totals_from_stats(latest.stats)
                if up or down:
                    probe.traffic_used_up = up
                    probe.traffic_used_down = down
                    probe.traffic_used_total = up + down
                    probe.traffic_used = up + down

            if latest.sysmetrics:
                metrics = ProbeSysMetrics.model_validate(latest.sysmetrics)
                probe.uptime = metrics.uptime or None
                probe.cpu_model = metrics.cpu_model or None
                probe.cpu_cores = metrics.cpu_cores or None
                probe.cpu_threads = metrics.cpu_threads or None
                probe.os = metrics.os or None
                probe.kernel = metrics.kernel or None
                probe.arch = metrics.arch or None
                if metrics.has_cpu:
                    probe.cpu_pct = metrics.cpu_pct
                    probe.loadavg = metrics.loadavg
                if metrics.has_mem:
                    probe.mem_used = metrics.mem_used
                    probe.mem_total = metrics.mem_total
                if metrics.has_disk:
                    probe.disk_used = metrics.disk_used
                    probe.disk_total = metrics.disk_total

        if ping_by_key:
            probe.ping = sorted(ping_by_key.values(), key=lambda item: (item.label, item.key or ""))
        return probe

    def _probe_return_routes(
        self,
        session: Session,
        server_ids: list[str],
    ) -> dict[str, list[ProbeReturnRoute]]:
        if not server_ids:
            return {}
        rows = session.scalars(
            select(ServerReturnRouteModel)
            .where(ServerReturnRouteModel.server_id.in_(server_ids))
            .order_by(ServerReturnRouteModel.server_id, ServerReturnRouteModel.carrier)
        ).all()
        order = {"telecom": 0, "unicom": 1, "mobile": 2}
        by_server: dict[str, list[ProbeReturnRoute]] = {}
        for row in rows:
            if row.carrier not in order:
                continue
            route = ProbeReturnRoute(
                carrier=row.carrier,
                region=row.region or None,
                route_type=row.route_type or "Unknown",
                tested_at=self._aware_datetime(row.tested_at).isoformat(),
            )
            by_server.setdefault(row.server_id, []).append(route)
        for routes in by_server.values():
            routes.sort(key=lambda item: order.get(item.carrier, 99))
        return by_server

    def _probe_daily_traffic(
        self,
        session: Session,
        server_id: str,
        day_count: int,
    ) -> list[ProbeDailyTraffic] | None:
        day_count = max(1, day_count)
        last_day = datetime.now(tz=UTC).date()
        first_day = last_day - timedelta(days=day_count - 1)
        seed_day = first_day - timedelta(days=1)
        since = datetime.combine(seed_day, datetime.min.time(), tzinfo=UTC)
        snapshots = session.scalars(
            select(TelemetrySnapshotModel)
            .where(
                TelemetrySnapshotModel.server_id == server_id,
                TelemetrySnapshotModel.reported_at >= since,
            )
            .order_by(TelemetrySnapshotModel.reported_at)
        ).all()
        if len(snapshots) < 2:
            return None

        totals_by_day: dict[date, dict[str, int]] = {
            first_day + timedelta(days=offset): {"uplink": 0, "downlink": 0}
            for offset in range(day_count)
        }
        previous = snapshots[0]
        previous_totals = self._probe_snapshot_traffic_totals(previous)
        for current in snapshots[1:]:
            current_totals = self._probe_snapshot_traffic_totals(current)
            if previous_totals and current_totals:
                delta = self._probe_traffic_delta(previous_totals, current_totals)
                day = self._aware_datetime(current.reported_at).date()
                if delta and first_day <= day <= last_day:
                    totals_by_day[day]["uplink"] += delta.uplink
                    totals_by_day[day]["downlink"] += delta.downlink
            previous = current
            previous_totals = current_totals

        rows = [
            ProbeDailyTraffic(
                date=day.isoformat(),
                uplink=totals["uplink"],
                downlink=totals["downlink"],
                total=totals["uplink"] + totals["downlink"],
            )
            for day, totals in sorted(totals_by_day.items())
        ]
        return rows if any(row.total > 0 for row in rows) else None

    @staticmethod
    def _probe_snapshot_traffic_totals(
        snapshot: TelemetrySnapshotModel,
    ) -> ProbeTrafficTotals | None:
        if snapshot.stats is not None:
            uplink, downlink = InventoryStore._traffic_totals_from_stats(snapshot.stats)
            return ProbeTrafficTotals(source="xray", uplink=uplink, downlink=downlink)
        if snapshot.system_tx_total is not None and snapshot.system_rx_total is not None:
            return ProbeTrafficTotals(
                source="system",
                uplink=snapshot.system_tx_total,
                downlink=snapshot.system_rx_total,
                boot_time_unix=snapshot.system_boot_time_unix,
            )
        return None

    @staticmethod
    def _probe_traffic_delta(
        previous: ProbeTrafficTotals,
        current: ProbeTrafficTotals,
    ) -> ProbeTrafficTotals | None:
        if previous.source != current.source:
            return None
        reset = (
            current.source == "system"
            and previous.boot_time_unix is not None
            and current.boot_time_unix is not None
            and previous.boot_time_unix != current.boot_time_unix
        )
        uplink = (
            current.uplink
            if reset or current.uplink < previous.uplink
            else current.uplink - previous.uplink
        )
        downlink = (
            current.downlink
            if reset or current.downlink < previous.downlink
            else current.downlink - previous.downlink
        )
        return ProbeTrafficTotals(source=current.source, uplink=uplink, downlink=downlink)

    def _probe_ping_series(
        self,
        session: Session,
        server_id: str,
        bucket_count: int,
        bucket_sec: int,
    ) -> dict[str, ProbePingSeries]:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        last_bucket = now_ts - now_ts % bucket_sec
        first_bucket = last_bucket - (bucket_count - 1) * bucket_sec
        since = datetime.fromtimestamp(first_bucket, tz=UTC)
        snapshots = session.scalars(
            select(TelemetrySnapshotModel)
            .where(
                TelemetrySnapshotModel.server_id == server_id,
                TelemetrySnapshotModel.reported_at >= since,
            )
            .order_by(TelemetrySnapshotModel.reported_at)
        ).all()

        series: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            for raw_sample in snapshot.latency or []:
                key = str(raw_sample.get("key") or "").strip()
                if not key:
                    continue
                timestamp = self._probe_sample_timestamp(raw_sample, snapshot)
                bucket_start = timestamp - timestamp % bucket_sec
                bucket_index = int((bucket_start - first_bucket) / bucket_sec)
                if bucket_index < 0 or bucket_index >= bucket_count:
                    continue

                state = series.setdefault(
                    key,
                    {
                        "current_at": -1,
                        "current_ms": -1,
                        "success": 0,
                        "fail": 0,
                        "buckets": [
                            {"sum": 0, "success": 0, "fail": 0} for _ in range(bucket_count)
                        ],
                    },
                )
                bucket = state["buckets"][bucket_index]
                success = bool(raw_sample.get("success"))
                if success:
                    latency_ms = int(raw_sample.get("latency_ms") or 0)
                    bucket["sum"] += latency_ms
                    bucket["success"] += 1
                    state["success"] += 1
                    if timestamp >= state["current_at"]:
                        state["current_at"] = timestamp
                        state["current_ms"] = latency_ms
                else:
                    bucket["fail"] += 1
                    state["fail"] += 1
                    if timestamp >= state["current_at"]:
                        state["current_at"] = timestamp
                        state["current_ms"] = -1

        return {
            key: self._probe_ping_series_from_state(key, state)
            for key, state in series.items()
        }

    @staticmethod
    def _probe_ping_series_from_state(key: str, state: dict[str, Any]) -> ProbePingSeries:
        buckets = []
        for bucket in state["buckets"]:
            total = bucket["success"] + bucket["fail"]
            if total == 0:
                buckets.append(ProbeBucket(ms=-1, loss=-1))
                continue
            ms = int(bucket["sum"] / bucket["success"]) if bucket["success"] else -1
            buckets.append(ProbeBucket(ms=ms, loss=bucket["fail"] * 100 / total))

        total = state["success"] + state["fail"]
        loss_pct = state["fail"] * 100 / total if total else 0
        return ProbePingSeries(
            key=key,
            label=key,
            current_ms=state["current_ms"],
            loss_pct=loss_pct,
            buckets=buckets,
        )

    @staticmethod
    def _average_probe_ping_series(
        source_series: Iterable[ProbePingSeries],
        bucket_count: int,
    ) -> ProbePingSeries:
        series = list(source_series)
        if not series:
            return ProbePingSeries(
                key="__avg__",
                label="Average",
                current_ms=-1,
                loss_pct=0,
                buckets=[ProbeBucket(ms=-1, loss=-1) for _ in range(bucket_count)],
            )

        current_values = [item.current_ms for item in series if item.current_ms >= 0]
        current_ms = int(sum(current_values) / len(current_values)) if current_values else -1
        loss_pct = sum(item.loss_pct for item in series) / len(series)
        buckets = []
        for index in range(bucket_count):
            ms_values = [
                item.buckets[index].ms
                for item in series
                if index < len(item.buckets) and item.buckets[index].ms >= 0
            ]
            loss_values = [
                item.buckets[index].loss
                for item in series
                if index < len(item.buckets) and item.buckets[index].loss >= 0
            ]
            ms = int(sum(ms_values) / len(ms_values)) if ms_values else -1
            loss = sum(loss_values) / len(loss_values) if loss_values else -1
            buckets.append(ProbeBucket(ms=ms, loss=loss))

        return ProbePingSeries(
            key="__avg__",
            label="Average",
            current_ms=current_ms,
            loss_pct=loss_pct,
            buckets=buckets,
        )

    @staticmethod
    def _probe_target_comparison(
        key: str,
        rows: list[ProbeTargetServerComparison],
    ) -> ProbeTargetComparison:
        healthy = [row.current_ms for row in rows if row.current_ms >= 0]
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                row.current_ms < 0,
                row.current_ms if row.current_ms >= 0 else 0,
                row.loss_pct,
                row.server_name or "",
                row.server_index,
            ),
        )
        return ProbeTargetComparison(
            key=key,
            label=key,
            server_count=len(rows),
            healthy_count=len(healthy),
            average_ms=int(sum(healthy) / len(healthy)) if healthy else None,
            best_ms=min(healthy) if healthy else None,
            worst_ms=max(healthy) if healthy else None,
            average_loss_pct=sum(row.loss_pct for row in rows) / len(rows) if rows else 0,
            servers=sorted_rows,
        )

    def _probe_system_series(
        self,
        session: Session,
        server_id: str,
        bucket_count: int,
        bucket_sec: int,
    ) -> ProbeSystemSeries:
        now_ts = int(datetime.now(tz=UTC).timestamp())
        last_bucket = now_ts - now_ts % bucket_sec
        first_bucket = last_bucket - (bucket_count - 1) * bucket_sec
        since = datetime.fromtimestamp(first_bucket, tz=UTC)
        snapshots = session.scalars(
            select(TelemetrySnapshotModel)
            .where(
                TelemetrySnapshotModel.server_id == server_id,
                TelemetrySnapshotModel.reported_at >= since,
            )
            .order_by(TelemetrySnapshotModel.reported_at)
        ).all()

        buckets: dict[int, dict[str, Any]] = {}
        previous: TelemetrySnapshotModel | None = None
        for snapshot in snapshots:
            timestamp = int(self._aware_datetime(snapshot.reported_at).timestamp())
            bucket_start = timestamp - timestamp % bucket_sec
            bucket_index = int((bucket_start - first_bucket) / bucket_sec)
            if bucket_index < 0 or bucket_index >= bucket_count:
                continue

            bucket = buckets.setdefault(
                bucket_index,
                {
                    "t": bucket_start,
                    "cpu_sum": 0.0,
                    "cpu_count": 0,
                    "mem_sum": 0,
                    "mem_count": 0,
                    "mem_total": 0,
                    "up_sum": 0,
                    "down_sum": 0,
                    "net_count": 0,
                    "cumulative_up": 0,
                    "cumulative_down": 0,
                },
            )
            if snapshot.sysmetrics:
                metrics = ProbeSysMetrics.model_validate(snapshot.sysmetrics)
                if metrics.has_cpu:
                    bucket["cpu_sum"] += metrics.cpu_pct
                    bucket["cpu_count"] += 1
                if metrics.has_mem:
                    bucket["mem_sum"] += metrics.mem_used
                    bucket["mem_count"] += 1
                    bucket["mem_total"] = metrics.mem_total

            if snapshot.system_tx_total is not None or snapshot.system_rx_total is not None:
                bucket["cumulative_up"] = snapshot.system_tx_total or 0
                bucket["cumulative_down"] = snapshot.system_rx_total or 0
                speed = self._system_speed_between(previous, snapshot)
                if speed:
                    upload_speed, download_speed = speed
                    bucket["up_sum"] += upload_speed
                    bucket["down_sum"] += download_speed
                    bucket["net_count"] += 1
            previous = snapshot

        output = ProbeSystemSeries()
        for index in range(bucket_count):
            bucket = buckets.get(index)
            if not bucket:
                continue
            timestamp = bucket["t"]
            if bucket["cpu_count"]:
                output.cpu_pct.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["cpu_sum"] / bucket["cpu_count"])
                )
            if bucket["mem_count"]:
                output.mem_used.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["mem_sum"] / bucket["mem_count"])
                )
                output.mem_total.append(ProbeMetricPoint(t=timestamp, value=bucket["mem_total"]))
            if bucket["net_count"]:
                output.upload_speed.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["up_sum"] / bucket["net_count"])
                )
                output.download_speed.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["down_sum"] / bucket["net_count"])
                )
            if bucket["cumulative_up"] or bucket["cumulative_down"]:
                output.cumulative_up.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["cumulative_up"])
                )
                output.cumulative_down.append(
                    ProbeMetricPoint(t=timestamp, value=bucket["cumulative_down"])
                )
        return output

    @staticmethod
    def _system_speed_between(
        previous: TelemetrySnapshotModel | None,
        current: TelemetrySnapshotModel,
    ) -> tuple[int, int] | None:
        if not previous:
            return None
        if (
            previous.system_rx_total is None
            or previous.system_tx_total is None
            or current.system_rx_total is None
            or current.system_tx_total is None
            or previous.system_boot_time_unix != current.system_boot_time_unix
            or current.system_rx_total < previous.system_rx_total
            or current.system_tx_total < previous.system_tx_total
        ):
            return None

        previous_at = InventoryStore._aware_datetime(previous.reported_at)
        current_at = InventoryStore._aware_datetime(current.reported_at)
        elapsed = (current_at - previous_at).total_seconds()
        if elapsed <= 0:
            return None
        upload_speed = int((current.system_tx_total - previous.system_tx_total) / elapsed)
        download_speed = int((current.system_rx_total - previous.system_rx_total) / elapsed)
        return upload_speed, download_speed

    @staticmethod
    def _probe_sample_timestamp(
        sample: dict[str, Any],
        snapshot: TelemetrySnapshotModel,
    ) -> int:
        raw_at = sample.get("at")
        if isinstance(raw_at, (int, float)) and raw_at > 0:
            return int(raw_at)
        return int(InventoryStore._aware_datetime(snapshot.reported_at).timestamp())

    def _record_subscription_traffic_ledger(
        self,
        session: Session,
        server: ServerModel,
        payload: AgentTelemetryReport,
        reported_at: datetime,
        now: datetime,
    ) -> None:
        if not payload.stats:
            return
        stats = payload.stats.model_dump(mode="json")
        user_stats = stats.get("user")
        if not isinstance(user_stats, dict) or not user_stats:
            return

        username_by_email = self._subscription_username_by_email(session)
        if not username_by_email:
            return

        for email, item in user_stats.items():
            if not isinstance(email, str) or not isinstance(item, dict):
                continue
            username = username_by_email.get(email)
            if not username:
                continue
            uplink = self._traffic_counter_value(item.get("uplink"))
            downlink = self._traffic_counter_value(item.get("downlink"))
            ledger = session.scalar(
                select(SubscriptionTrafficLedgerModel).where(
                    SubscriptionTrafficLedgerModel.username == username,
                    SubscriptionTrafficLedgerModel.server_id == server.id,
                    SubscriptionTrafficLedgerModel.email == email,
                )
            )
            if not ledger:
                session.add(
                    SubscriptionTrafficLedgerModel(
                        id=str(uuid4()),
                        username=username,
                        server_id=server.id,
                        email=email,
                        upload=uplink,
                        download=downlink,
                        last_uplink=uplink,
                        last_downlink=downlink,
                        last_reported_at=reported_at,
                        created_at=now,
                        updated_at=now,
                    )
                )
                continue

            if ledger.last_reported_at and reported_at <= self._aware_datetime(
                ledger.last_reported_at
            ):
                continue

            ledger.upload += (
                uplink - ledger.last_uplink if uplink >= ledger.last_uplink else uplink
            )
            ledger.download += (
                downlink - ledger.last_downlink
                if downlink >= ledger.last_downlink
                else downlink
            )
            ledger.last_uplink = uplink
            ledger.last_downlink = downlink
            ledger.last_reported_at = reported_at
            ledger.updated_at = now

    @staticmethod
    def _subscription_username_by_email(session: Session) -> dict[str, str]:
        credentials = session.scalars(select(SubscriptionCredentialModel)).all()
        index: dict[str, str] = {}
        for credential in credentials:
            for email in InventoryStore._subscription_credential_emails(credential):
                index[email] = credential.username
        return index

    @staticmethod
    def _subscription_credential_emails(
        credential: SubscriptionCredentialModel,
    ) -> list[str]:
        emails = [credential.email]
        raw_email = credential.credential.get("email")
        if isinstance(raw_email, str) and raw_email and raw_email not in emails:
            emails.append(raw_email)
        return emails

    @staticmethod
    def _traffic_counter_value(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int | float):
            return max(0, int(value))
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return 0

    @staticmethod
    def _traffic_totals_from_stats(stats: dict[str, Any]) -> tuple[int, int]:
        source = stats.get("inbound") or stats.get("user") or {}
        if not isinstance(source, dict):
            return 0, 0
        uplink = 0
        downlink = 0
        for item in source.values():
            if not isinstance(item, dict):
                continue
            uplink += int(item.get("uplink") or 0)
            downlink += int(item.get("downlink") or 0)
        return uplink, downlink

    @staticmethod
    def _product_user_read(user: ProductUserModel) -> ProductUserRead:
        return ProductUserRead(
            username=user.username,
            email=user.email,
            display_name=user.display_name or user.username,
            role=user.role,
            is_active=user.is_active,
            current_plan_id=UUID(user.current_plan_id) if user.current_plan_id else None,
            plan_started_at=user.plan_started_at,
            plan_expires_at=user.plan_expires_at,
            is_reset=user.is_reset,
            reset_day=user.reset_day,
            last_traffic_reset_at=InventoryStore._aware_datetime(user.last_traffic_reset_at)
            if user.last_traffic_reset_at
            else None,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    @staticmethod
    def _managed_node_read(node: ManagedNodeModel) -> ManagedNodeRead:
        return ManagedNodeRead(
            id=UUID(node.id),
            name=node.name,
            server_id=UUID(node.server_id),
            protocol=node.protocol,
            node_type=node.node_type,
            inbound_tag=node.inbound_tag,
            routed_outbound_tag=node.routed_outbound_tag,
            routed_rule_marktag=node.routed_rule_marktag,
            tag=node.tag,
            tags=node.tags or [],
            enabled=node.enabled,
            client_template=node.client_template or {},
            config=node.config or {},
            created_at=node.created_at,
            updated_at=node.updated_at,
        )

    @staticmethod
    def _subscription_template_preset(preset_id: str) -> SubscriptionTemplatePresetRead:
        for preset in _SUBSCRIPTION_NODE_PRESETS:
            if preset["id"] == preset_id:
                return SubscriptionTemplatePresetRead.model_validate(deepcopy(preset))
        raise SubscriptionTemplatePresetNotFoundError(f"subscription preset not found: {preset_id}")

    @staticmethod
    def _server_subscription_host(server: ServerModel) -> str:
        return (
            server.domain
            or server.ip_address
            or server.domain_v6
            or server.ip_address_v6
            or server.name
        )

    @staticmethod
    def _subscription_plan_read(plan: SubscriptionPlanModel) -> SubscriptionPlanRead:
        return SubscriptionPlanRead(
            id=UUID(plan.id),
            name=plan.name,
            description=plan.description,
            traffic_limit_gb=plan.traffic_limit_bytes / (1024 * 1024 * 1024),
            traffic_limit_bytes=plan.traffic_limit_bytes,
            cycle_days=plan.cycle_days,
            is_reset=plan.is_reset,
            reset_day=plan.reset_day,
            node_ids=[UUID(node_id) for node_id in (plan.node_ids or [])],
            node_multipliers={
                UUID(node_id): multiplier
                for node_id, multiplier in (plan.node_multipliers or {}).items()
            },
            node_speed_limits={
                UUID(node_id): limit for node_id, limit in (plan.node_speed_limits or {}).items()
            },
            node_device_limits={
                UUID(node_id): limit for node_id, limit in (plan.node_device_limits or {}).items()
            },
            speed_limit_mbps=plan.speed_limit_mbps,
            device_limit=plan.device_limit,
            traffic_mode=plan.traffic_mode,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )

    @staticmethod
    def _subscription_catalog_plan_entry(
        plan: SubscriptionPlanModel,
        node_names: dict[str, str],
    ) -> SubscriptionCatalogPlanEntry:
        return SubscriptionCatalogPlanEntry(
            name=plan.name,
            description=plan.description,
            traffic_limit_gb=plan.traffic_limit_bytes / (1024 * 1024 * 1024),
            cycle_days=plan.cycle_days,
            is_reset=plan.is_reset,
            reset_day=plan.reset_day,
            node_names=[node_names.get(node_id, node_id) for node_id in (plan.node_ids or [])],
            node_multipliers=InventoryStore._catalog_map_keys_to_names(
                plan.node_multipliers or {},
                node_names,
            ),
            node_speed_limits=InventoryStore._catalog_map_keys_to_names(
                plan.node_speed_limits or {},
                node_names,
            ),
            node_device_limits=InventoryStore._catalog_map_keys_to_names(
                plan.node_device_limits or {},
                node_names,
            ),
            speed_limit_mbps=plan.speed_limit_mbps,
            device_limit=plan.device_limit,
            traffic_mode=SubscriptionTrafficMode(plan.traffic_mode),
        )

    @staticmethod
    def _catalog_map_keys_to_names(
        values: dict[str, Any],
        node_names: dict[str, str],
    ) -> dict[str, Any]:
        return {node_names.get(node_id, node_id): value for node_id, value in values.items()}

    @staticmethod
    def _catalog_server(
        session: Session,
        server_map: dict[str, UUID],
        server_name: str,
    ) -> ServerModel | None:
        mapped_id = server_map.get(server_name)
        if mapped_id:
            server = session.get(ServerModel, str(mapped_id))
            if server:
                return server
        return session.scalar(select(ServerModel).where(ServerModel.name == server_name))

    @staticmethod
    def _catalog_node_by_name(
        session: Session,
        name: str,
        server_id: str,
    ) -> ManagedNodeModel | None:
        return session.scalar(
            select(ManagedNodeModel).where(
                ManagedNodeModel.name == name,
                ManagedNodeModel.server_id == server_id,
            )
        )

    @staticmethod
    def _catalog_node_model(
        entry: SubscriptionCatalogNodeEntry,
        server_id: str,
        now: datetime,
    ) -> ManagedNodeModel:
        return ManagedNodeModel(
            id=str(uuid4()),
            name=entry.name,
            server_id=server_id,
            protocol=entry.protocol.lower(),
            node_type=entry.node_type.value,
            inbound_tag=entry.inbound_tag,
            routed_outbound_tag=entry.routed_outbound_tag,
            routed_rule_marktag=entry.routed_rule_marktag,
            tag=entry.tag,
            tags=entry.tags,
            enabled=entry.enabled,
            client_template=entry.client_template,
            config=entry.config,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _apply_catalog_node(
        node: ManagedNodeModel,
        entry: SubscriptionCatalogNodeEntry,
        server_id: str,
        now: datetime,
    ) -> None:
        node.server_id = server_id
        node.protocol = entry.protocol.lower()
        node.node_type = entry.node_type.value
        node.inbound_tag = entry.inbound_tag
        node.routed_outbound_tag = entry.routed_outbound_tag
        node.routed_rule_marktag = entry.routed_rule_marktag
        node.tag = entry.tag
        node.tags = entry.tags
        node.enabled = entry.enabled
        node.client_template = entry.client_template
        node.config = entry.config
        node.updated_at = now

    @classmethod
    def _catalog_plan_model(
        cls,
        entry: SubscriptionCatalogPlanEntry,
        node_ids: list[str],
        node_ids_by_name: dict[str, str],
        now: datetime,
    ) -> SubscriptionPlanModel:
        return SubscriptionPlanModel(
            id=str(uuid4()),
            name=entry.name,
            description=entry.description,
            traffic_limit_bytes=int(entry.traffic_limit_gb * 1024 * 1024 * 1024),
            cycle_days=entry.cycle_days,
            is_reset=entry.is_reset,
            reset_day=entry.reset_day,
            node_ids=node_ids,
            node_multipliers=cls._catalog_map_keys_to_ids(
                entry.node_multipliers,
                node_ids_by_name,
            ),
            node_speed_limits=cls._catalog_map_keys_to_ids(
                entry.node_speed_limits,
                node_ids_by_name,
            ),
            node_device_limits=cls._catalog_map_keys_to_ids(
                entry.node_device_limits,
                node_ids_by_name,
            ),
            speed_limit_mbps=entry.speed_limit_mbps,
            device_limit=entry.device_limit,
            traffic_mode=entry.traffic_mode.value,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def _apply_catalog_plan(
        cls,
        plan: SubscriptionPlanModel,
        entry: SubscriptionCatalogPlanEntry,
        node_ids: list[str],
        node_ids_by_name: dict[str, str],
        now: datetime,
    ) -> None:
        plan.description = entry.description
        plan.traffic_limit_bytes = int(entry.traffic_limit_gb * 1024 * 1024 * 1024)
        plan.cycle_days = entry.cycle_days
        plan.is_reset = entry.is_reset
        plan.reset_day = entry.reset_day
        plan.node_ids = node_ids
        plan.node_multipliers = cls._catalog_map_keys_to_ids(
            entry.node_multipliers,
            node_ids_by_name,
        )
        plan.node_speed_limits = cls._catalog_map_keys_to_ids(
            entry.node_speed_limits,
            node_ids_by_name,
        )
        plan.node_device_limits = cls._catalog_map_keys_to_ids(
            entry.node_device_limits,
            node_ids_by_name,
        )
        plan.speed_limit_mbps = entry.speed_limit_mbps
        plan.device_limit = entry.device_limit
        plan.traffic_mode = entry.traffic_mode.value
        plan.updated_at = now

    @staticmethod
    def _catalog_map_keys_to_ids(
        values: dict[str, Any],
        node_ids_by_name: dict[str, str],
    ) -> dict[str, Any]:
        return {
            node_ids_by_name[node_name]: value
            for node_name, value in values.items()
            if node_name in node_ids_by_name
        }

    def _import_subscription_credentials(
        self,
        session: Session,
        entries: list[SubscriptionCatalogCredentialEntry],
        server_map: dict[str, UUID],
        node_ids_by_name: dict[str, str],
        now: datetime,
        warnings: list[str],
    ) -> int:
        imported = 0
        for entry in entries:
            if not session.get(ProductUserModel, entry.username):
                warnings.append(
                    f"credential {entry.email} skipped; user {entry.username} not found"
                )
                continue
            server = self._catalog_server(session, server_map, entry.server_name)
            if not server:
                warnings.append(
                    f"credential {entry.email} skipped; server {entry.server_name} not found"
                )
                continue
            node_id = node_ids_by_name.get(entry.node_name)
            if not node_id:
                node = self._catalog_node_by_name(session, entry.node_name, server.id)
                node_id = node.id if node else None
            if not node_id:
                warnings.append(
                    f"credential {entry.email} skipped; node {entry.node_name} not found"
                )
                continue

            existing = session.scalar(
                select(SubscriptionCredentialModel).where(
                    SubscriptionCredentialModel.username == entry.username,
                    SubscriptionCredentialModel.node_id == node_id,
                )
            )
            if existing:
                existing.server_id = server.id
                existing.inbound_tag = entry.inbound_tag
                existing.protocol = entry.protocol.lower()
                existing.email = entry.email
                existing.credential = entry.credential
                existing.updated_at = now
            else:
                session.add(
                    SubscriptionCredentialModel(
                        id=str(uuid4()),
                        username=entry.username,
                        node_id=node_id,
                        server_id=server.id,
                        inbound_tag=entry.inbound_tag,
                        protocol=entry.protocol.lower(),
                        email=entry.email,
                        credential=entry.credential,
                        created_at=now,
                        updated_at=now,
                    )
                )
            imported += 1
        return imported

    @staticmethod
    def _uuid_keyed_float_map(values: dict[UUID, float]) -> dict[str, float]:
        return {str(key): value for key, value in values.items()}

    @staticmethod
    def _uuid_keyed_int_map(values: dict[UUID, int]) -> dict[str, int]:
        return {str(key): value for key, value in values.items()}

    @staticmethod
    def _date_to_utc_start(value: date) -> datetime:
        return datetime(value.year, value.month, value.day, tzinfo=UTC)

    @staticmethod
    def _ensure_managed_nodes_exist(session: Session, node_ids: list[UUID]) -> None:
        for node_id in node_ids:
            if not session.get(ManagedNodeModel, str(node_id)):
                raise ManagedNodeNotFoundError(f"managed node not found: {node_id}")

    def _subscription_provision_batches(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        no_restart: bool,
    ) -> tuple[list[SubscriptionProvisionBatch], list[str]]:
        warnings: list[str] = []
        if not plan.node_ids:
            return [], warnings

        nodes = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(plan.node_ids))
        ).all()
        nodes_by_id = {node.id: node for node in nodes}
        batches: dict[str, dict[str, Any]] = {}
        server_names: dict[str, str] = {}
        seen_inbound: set[tuple[str, str, str]] = set()
        seen_route: set[tuple[str, str, str, str]] = set()

        for node_id in plan.node_ids:
            node = nodes_by_id.get(node_id)
            if not node:
                warnings.append(f"node {node_id} no longer exists")
                continue
            if not node.enabled:
                continue
            server = session.get(ServerModel, node.server_id)
            if not server:
                warnings.append(f"node {node.name} points to a missing server")
                continue

            body = batches.setdefault(
                server.id,
                {"inbound_clients": [], "routing_user_additions": [], "no_restart": no_restart},
            )
            server_names[server.id] = server.name
            credential = self._get_or_create_subscription_credential(session, user, node, server)
            email = credential.email

            if node.inbound_tag:
                client = self._provisioning_client_from_credential(
                    user,
                    plan,
                    node,
                    server,
                    credential,
                )
                client_email = str(client.get("email") or email)
                inbound_key = (server.id, node.inbound_tag, client_email)
                if inbound_key not in seen_inbound:
                    body["inbound_clients"].append({"tag": node.inbound_tag, "client": client})
                    seen_inbound.add(inbound_key)
                    email = client_email

            if node.routed_rule_marktag or node.routed_outbound_tag:
                route_key = (
                    server.id,
                    node.routed_rule_marktag or "",
                    node.routed_outbound_tag or "",
                    email,
                )
                if route_key not in seen_route:
                    item = {"user_email": email}
                    if node.routed_rule_marktag:
                        item["marktag"] = node.routed_rule_marktag
                    if node.routed_outbound_tag:
                        item["outbound_tag"] = node.routed_outbound_tag
                    body["routing_user_additions"].append(item)
                    seen_route.add(route_key)

        result = []
        for server_id, body in batches.items():
            if not body["inbound_clients"] and not body["routing_user_additions"]:
                continue
            result.append(
                SubscriptionProvisionBatch(
                    server_id=UUID(server_id),
                    server_name=server_names[server_id],
                    body=body,
                )
            )
        return result, warnings

    def _subscription_proxy_configs(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        if not plan.node_ids:
            return [], warnings

        nodes = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(plan.node_ids))
        ).all()
        nodes_by_id = {node.id: node for node in nodes}
        proxies: list[dict[str, Any]] = []

        for node_id in plan.node_ids:
            node = nodes_by_id.get(node_id)
            if not node:
                warnings.append(f"node {node_id} no longer exists")
                continue
            if not node.enabled:
                continue
            if not node.config:
                warnings.append(f"node {node.name} has no subscription proxy config")
                continue
            server = session.get(ServerModel, node.server_id)
            if not server:
                warnings.append(f"node {node.name} points to a missing server")
                continue

            credential = self._get_or_create_subscription_credential(session, user, node, server)
            context = self._template_context(user, plan, node, server, credential)
            rendered = self._render_template(node.config, context)
            if not isinstance(rendered, dict) or not rendered:
                warnings.append(f"node {node.name} subscription proxy config is not usable")
                continue

            proxy = dict(rendered)
            proxy.setdefault("name", node.name)
            proxy.setdefault("type", self._proxy_type_for_protocol(node.protocol))
            self._apply_credential_to_proxy(proxy, node.protocol, credential.credential)
            proxy["name"] = self._subscription_proxy_name(plan, node, str(proxy["name"]))
            proxies.append(proxy)

        session.flush()
        return proxies, warnings

    def _provisioning_client_from_credential(
        self,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        node: ManagedNodeModel,
        server: ServerModel,
        credential: SubscriptionCredentialModel,
    ) -> dict[str, Any]:
        client: dict[str, Any] = {}
        if node.client_template:
            rendered = self._render_template(
                node.client_template,
                self._template_context(user, plan, node, server, credential),
            )
            if isinstance(rendered, dict):
                client.update(rendered)
        client.update(credential.credential or {})
        client["email"] = credential.email
        return client

    def _get_or_create_subscription_credential(
        self,
        session: Session,
        user: ProductUserModel,
        node: ManagedNodeModel,
        server: ServerModel,
    ) -> SubscriptionCredentialModel:
        existing = session.scalar(
            select(SubscriptionCredentialModel).where(
                SubscriptionCredentialModel.username == user.username,
                SubscriptionCredentialModel.node_id == node.id,
            )
        )
        if existing:
            existing.server_id = server.id
            existing.inbound_tag = node.inbound_tag
            existing.protocol = node.protocol
            return existing

        now = datetime.now(tz=UTC)
        email = self._default_client_email(user, node)
        credential = SubscriptionCredentialModel(
            id=str(uuid4()),
            username=user.username,
            node_id=node.id,
            server_id=server.id,
            inbound_tag=node.inbound_tag,
            protocol=node.protocol,
            email=email,
            credential=self._generate_subscription_credential(
                protocol=node.protocol,
                username=user.username,
                email=email,
                node_config=node.config or {},
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(credential)
        session.flush()
        return credential

    @staticmethod
    def _generate_subscription_credential(
        protocol: str,
        username: str,
        email: str,
        node_config: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = protocol.strip().lower()
        match normalized:
            case "vless" | "vmess":
                return {"id": str(uuid4()), "email": email, "level": 0}
            case "trojan" | "anytls":
                return {"password": str(uuid4()), "email": email, "level": 0}
            case "snell":
                return {"psk": str(uuid4()), "email": email, "level": 0}
            case "mieru":
                return {
                    "username": InventoryStore._safe_credential_username(username),
                    "password": str(uuid4()),
                    "email": email,
                    "level": 0,
                }
            case "hysteria" | "hysteria2" | "hy2":
                return {"auth": str(uuid4()), "email": email, "level": 0}
            case "shadowsocks" | "ss":
                key_len = InventoryStore._shadowsocks_key_length(
                    str(node_config.get("method") or node_config.get("cipher") or "")
                )
                return {
                    "password": base64.b64encode(token_bytes(key_len)).decode("ascii"),
                    "email": email,
                    "level": 0,
                }
            case "socks" | "http":
                return {
                    "user": InventoryStore._safe_credential_username(username),
                    "pass": token_urlsafe(12),
                }
        return {"id": str(uuid4()), "email": email, "level": 0}

    @staticmethod
    def _apply_credential_to_proxy(
        proxy: dict[str, Any],
        protocol: str,
        credential: dict[str, Any],
    ) -> None:
        normalized = protocol.strip().lower()
        match normalized:
            case "vless" | "vmess":
                if credential.get("id"):
                    proxy["uuid"] = credential["id"]
            case "trojan" | "anytls":
                if credential.get("password"):
                    proxy["password"] = credential["password"]
            case "snell":
                if credential.get("psk"):
                    proxy["psk"] = credential["psk"]
            case "mieru":
                if credential.get("username"):
                    proxy["username"] = credential["username"]
                if credential.get("password"):
                    proxy["password"] = credential["password"]
            case "hysteria" | "hysteria2" | "hy2":
                if credential.get("auth"):
                    proxy["password"] = credential["auth"]
            case "shadowsocks" | "ss":
                password = credential.get("password")
                if not password:
                    return
                cipher = str(proxy.get("cipher") or "")
                if cipher.startswith("2022-") and isinstance(proxy.get("password"), str):
                    master_password = str(proxy["password"]).split(":", 1)[0]
                    proxy["password"] = f"{master_password}:{password}"
                else:
                    proxy["password"] = password
            case "socks" | "http":
                if credential.get("user"):
                    proxy["username"] = credential["user"]
                if credential.get("pass"):
                    proxy["password"] = credential["pass"]

    @staticmethod
    def _proxy_type_for_protocol(protocol: str) -> str:
        normalized = protocol.strip().lower()
        if normalized == "shadowsocks":
            return "ss"
        if normalized == "socks":
            return "socks5"
        if normalized == "hysteria":
            return "hysteria2"
        if normalized == "hy2":
            return "hysteria2"
        return normalized or "vless"

    @classmethod
    def _render_subscription_content(
        cls,
        proxies: list[dict[str, Any]],
        client_format: SubscriptionClientFormat,
    ) -> tuple[str, str, str]:
        match client_format:
            case SubscriptionClientFormat.CLASH:
                return cls._render_clash_subscription(proxies), "text/yaml; charset=utf-8", "yaml"
            case SubscriptionClientFormat.SING_BOX:
                return (
                    cls._render_sing_box_subscription(proxies),
                    "application/json; charset=utf-8",
                    "json",
                )
            case SubscriptionClientFormat.URI_LIST:
                return (
                    cls._render_uri_list_subscription(proxies),
                    "text/plain; charset=utf-8",
                    "txt",
                )
            case SubscriptionClientFormat.BASE64:
                uri_list = cls._render_uri_list_subscription(proxies)
                encoded = base64.b64encode(uri_list.encode("utf-8")).decode("ascii")
                return encoded + "\n", "text/plain; charset=utf-8", "txt"

    @staticmethod
    def _render_clash_subscription(proxies: list[dict[str, Any]]) -> str:
        proxy_names = [str(proxy.get("name") or "proxy") for proxy in proxies]
        payload = {
            "mixed-port": 7890,
            "allow-lan": False,
            "mode": "rule",
            "log-level": "info",
            "proxies": proxies,
            "proxy-groups": [
                {
                    "name": "Proxy",
                    "type": "select",
                    "proxies": proxy_names,
                }
            ],
            "rules": ["MATCH,Proxy"],
        }
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)

    @classmethod
    def _render_sing_box_subscription(cls, proxies: list[dict[str, Any]]) -> str:
        outbounds = [outbound for proxy in proxies if (outbound := cls._sing_box_outbound(proxy))]
        tags = [str(outbound["tag"]) for outbound in outbounds]
        payload = {
            "log": {"level": "info"},
            "outbounds": [
                {
                    "type": "selector",
                    "tag": "Proxy",
                    "outbounds": tags,
                    "default": tags[0] if tags else "",
                },
                {"type": "direct", "tag": "direct"},
                *outbounds,
            ],
            "route": {"final": "Proxy"},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def _sing_box_outbound(cls, proxy: dict[str, Any]) -> dict[str, Any] | None:
        proxy_type = cls._normalized_proxy_type(proxy)
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        if not proxy_type or not isinstance(server, str) or not server or not port:
            return None

        outbound: dict[str, Any] = {
            "type": proxy_type,
            "tag": str(proxy.get("name") or server),
            "server": server,
            "server_port": port,
        }

        if proxy_type in {"vless", "vmess"}:
            outbound["uuid"] = str(proxy.get("uuid") or proxy.get("id") or "")
            if proxy_type == "vmess":
                outbound["security"] = str(proxy.get("cipher") or proxy.get("security") or "auto")
                outbound["alter_id"] = cls._proxy_int(proxy.get("alterId")) or 0
            if proxy.get("flow"):
                outbound["flow"] = str(proxy["flow"])
        elif proxy_type in {"trojan", "hysteria2"}:
            outbound["password"] = str(proxy.get("password") or proxy.get("auth") or "")
        elif proxy_type == "shadowsocks":
            outbound["method"] = str(proxy.get("cipher") or proxy.get("method") or "aes-128-gcm")
            outbound["password"] = str(proxy.get("password") or "")
        elif proxy_type in {"socks", "http"}:
            if proxy.get("username"):
                outbound["username"] = str(proxy["username"])
            if proxy.get("password"):
                outbound["password"] = str(proxy["password"])

        tls = cls._sing_box_tls(proxy)
        if tls:
            outbound["tls"] = tls
        return outbound

    @staticmethod
    def _sing_box_tls(proxy: dict[str, Any]) -> dict[str, Any] | None:
        tls_value = proxy.get("tls")
        if tls_value is False or tls_value is None:
            return None
        tls: dict[str, Any] = {"enabled": True}
        server_name = proxy.get("servername") or proxy.get("sni")
        if isinstance(server_name, str) and server_name:
            tls["server_name"] = server_name
        if isinstance(tls_value, dict):
            tls.update(tls_value)
            tls["enabled"] = bool(tls.get("enabled", True))
        return tls

    @classmethod
    def _render_uri_list_subscription(cls, proxies: list[dict[str, Any]]) -> str:
        uris = [uri for proxy in proxies if (uri := cls._proxy_uri(proxy))]
        return "\n".join(uris) + ("\n" if uris else "")

    @classmethod
    def _proxy_uri(cls, proxy: dict[str, Any]) -> str | None:
        proxy_type = cls._normalized_proxy_type(proxy)
        match proxy_type:
            case "vless":
                return cls._vless_uri(proxy)
            case "vmess":
                return cls._vmess_uri(proxy)
            case "trojan":
                return cls._trojan_uri(proxy)
            case "shadowsocks":
                return cls._shadowsocks_uri(proxy)
            case "hysteria2":
                return cls._hysteria2_uri(proxy)
            case "socks" | "http":
                return cls._userpass_uri(proxy, proxy_type)
        return None

    @classmethod
    def _vless_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        uuid = proxy.get("uuid") or proxy.get("id")
        if not isinstance(server, str) or not server or not port or not uuid:
            return None
        query = cls._uri_query(
            {
                "type": proxy.get("network") or "tcp",
                "security": "tls" if cls._proxy_bool(proxy.get("tls")) else None,
                "sni": proxy.get("servername") or proxy.get("sni"),
                "flow": proxy.get("flow"),
                "encryption": proxy.get("encryption") or "none",
            }
        )
        return (
            f"vless://{quote(str(uuid), safe='')}@{server}:{port}"
            f"{query}{cls._uri_fragment(proxy)}"
        )

    @classmethod
    def _vmess_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        uuid = proxy.get("uuid") or proxy.get("id")
        if not isinstance(server, str) or not server or not port or not uuid:
            return None
        ws_options = proxy.get("ws-opts")
        ws_options = ws_options if isinstance(ws_options, dict) else {}
        ws_headers = ws_options.get("headers")
        ws_headers = ws_headers if isinstance(ws_headers, dict) else {}
        payload = {
            "v": "2",
            "ps": str(proxy.get("name") or server),
            "add": server,
            "port": str(port),
            "id": str(uuid),
            "aid": str(cls._proxy_int(proxy.get("alterId")) or 0),
            "scy": str(proxy.get("cipher") or proxy.get("security") or "auto"),
            "net": str(proxy.get("network") or "tcp"),
            "type": str(proxy.get("headerType") or "none"),
            "host": str(ws_headers.get("Host") or ""),
            "path": str(ws_options.get("path") or ""),
            "tls": "tls" if cls._proxy_bool(proxy.get("tls")) else "",
            "sni": str(proxy.get("servername") or proxy.get("sni") or ""),
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return f"vmess://{encoded}"

    @classmethod
    def _trojan_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        password = proxy.get("password")
        if not isinstance(server, str) or not server or not port or not password:
            return None
        query = cls._uri_query(
            {
                "security": "tls" if cls._proxy_bool(proxy.get("tls")) else None,
                "sni": proxy.get("servername") or proxy.get("sni"),
            }
        )
        return (
            f"trojan://{quote(str(password), safe='')}@{server}:{port}"
            f"{query}{cls._uri_fragment(proxy)}"
        )

    @classmethod
    def _shadowsocks_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        method = proxy.get("cipher") or proxy.get("method")
        password = proxy.get("password")
        if not isinstance(server, str) or not server or not port or not method or not password:
            return None
        userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode("ascii").rstrip(
            "="
        )
        return f"ss://{userinfo}@{server}:{port}{cls._uri_fragment(proxy)}"

    @classmethod
    def _hysteria2_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        password = proxy.get("password") or proxy.get("auth")
        if not isinstance(server, str) or not server or not port or not password:
            return None
        query = cls._uri_query({"sni": proxy.get("servername") or proxy.get("sni")})
        return (
            f"hysteria2://{quote(str(password), safe='')}@{server}:{port}"
            f"{query}{cls._uri_fragment(proxy)}"
        )

    @classmethod
    def _userpass_uri(cls, proxy: dict[str, Any], scheme: str) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        if not isinstance(server, str) or not server or not port:
            return None
        username = quote(str(proxy.get("username") or ""), safe="")
        password = quote(str(proxy.get("password") or ""), safe="")
        auth = f"{username}:{password}@" if username or password else ""
        return f"{scheme}://{auth}{server}:{port}{cls._uri_fragment(proxy)}"

    @staticmethod
    def _normalized_proxy_type(proxy: dict[str, Any]) -> str:
        raw_type = str(proxy.get("type") or "").strip().lower()
        match raw_type:
            case "ss" | "shadowsocks":
                return "shadowsocks"
            case "socks5" | "socks":
                return "socks"
            case "hy2" | "hysteria" | "hysteria2":
                return "hysteria2"
        return raw_type

    @staticmethod
    def _uri_query(params: dict[str, object]) -> str:
        filtered = {
            key: str(value)
            for key, value in params.items()
            if value is not None and value != "" and value is not False
        }
        return f"?{urlencode(filtered)}" if filtered else ""

    @staticmethod
    def _uri_fragment(proxy: dict[str, Any]) -> str:
        name = str(proxy.get("name") or "").strip()
        return f"#{quote(name)}" if name else ""

    @staticmethod
    def _proxy_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str) and value.isdigit():
            parsed = int(value)
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _proxy_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "tls", "yes"}
        return bool(value)

    def _subscription_userinfo_header(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
    ) -> str | None:
        if plan.traffic_limit_bytes <= 0:
            return None
        upload, download = self._subscription_user_traffic(session, user.username)
        if plan.traffic_mode == "oneway":
            upload = 0
        expire = (
            int(self._aware_datetime(user.plan_expires_at).timestamp())
            if user.plan_expires_at
            else 4_102_444_800
        )
        return (
            f"upload={upload}; download={download}; total={plan.traffic_limit_bytes}; "
            f"expire={expire}"
        )

    def _subscription_quota_status(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel | None,
        now: datetime,
    ) -> SubscriptionQuotaStatusRead:
        upload, download = self._subscription_user_traffic(session, user.username)
        traffic_mode = SubscriptionTrafficMode(plan.traffic_mode) if plan else None
        traffic_limit_bytes = plan.traffic_limit_bytes if plan else 0
        charged_usage_bytes = (
            download
            if traffic_mode == SubscriptionTrafficMode.ONEWAY
            else upload + download
        )
        expired = bool(
            user.plan_expires_at and now > self._aware_datetime(user.plan_expires_at)
        )
        over_quota = bool(
            plan and traffic_limit_bytes > 0 and charged_usage_bytes >= traffic_limit_bytes
        )
        reset_due_at, next_reset_at = self._subscription_reset_boundaries(user, now)
        reset_due = self._subscription_reset_due(
            user,
            plan,
            expired=expired,
            reset_due_at=reset_due_at,
        )
        remaining_bytes = (
            max(traffic_limit_bytes - charged_usage_bytes, 0) if traffic_limit_bytes else 0
        )
        percent_used = (
            round((charged_usage_bytes / traffic_limit_bytes) * 100, 2)
            if traffic_limit_bytes
            else 0
        )
        return SubscriptionQuotaStatusRead(
            username=user.username,
            is_active=user.is_active,
            has_plan=plan is not None,
            available=bool(user.is_active and plan and not expired and not over_quota),
            expired=expired,
            over_quota=over_quota,
            reset_enabled=user.is_reset,
            reset_due=reset_due,
            upload=upload,
            download=download,
            charged_usage_bytes=charged_usage_bytes,
            traffic_limit_bytes=traffic_limit_bytes,
            remaining_bytes=remaining_bytes,
            percent_used=percent_used,
            reset_day=user.reset_day,
            plan_id=UUID(plan.id) if plan else None,
            plan_name=plan.name if plan else None,
            traffic_mode=traffic_mode,
            plan_started_at=self._aware_datetime(user.plan_started_at)
            if user.plan_started_at
            else None,
            plan_expires_at=self._aware_datetime(user.plan_expires_at)
            if user.plan_expires_at
            else None,
            reset_due_at=reset_due_at,
            next_reset_at=next_reset_at,
            last_traffic_reset_at=self._aware_datetime(user.last_traffic_reset_at)
            if user.last_traffic_reset_at
            else None,
        )

    def _reset_subscription_traffic_for_user(
        self,
        session: Session,
        user: ProductUserModel,
        now: datetime,
    ) -> int:
        ledgers = session.scalars(
            select(SubscriptionTrafficLedgerModel).where(
                SubscriptionTrafficLedgerModel.username == user.username
            )
        ).all()
        ledger_keys = {(ledger.server_id, ledger.email) for ledger in ledgers}
        for ledger in ledgers:
            baseline = self._subscription_traffic_baseline(
                session,
                ledger.server_id,
                ledger.email,
            )
            if baseline:
                last_uplink, last_downlink, reported_at = baseline
                ledger.last_uplink = last_uplink
                ledger.last_downlink = last_downlink
                ledger.last_reported_at = reported_at
            ledger.upload = 0
            ledger.download = 0
            ledger.updated_at = now

        touched = len(ledgers)
        credentials = session.scalars(
            select(SubscriptionCredentialModel).where(
                SubscriptionCredentialModel.username == user.username
            )
        ).all()
        for credential in credentials:
            email, last_uplink, last_downlink, reported_at = self._subscription_credential_baseline(
                session,
                credential,
            )
            key = (credential.server_id, email)
            if key in ledger_keys:
                continue
            session.add(
                SubscriptionTrafficLedgerModel(
                    id=str(uuid4()),
                    username=user.username,
                    server_id=credential.server_id,
                    email=email,
                    upload=0,
                    download=0,
                    last_uplink=last_uplink,
                    last_downlink=last_downlink,
                    last_reported_at=reported_at,
                    created_at=now,
                    updated_at=now,
                )
            )
            ledger_keys.add(key)
            touched += 1

        user.last_traffic_reset_at = now
        user.updated_at = now
        return touched

    def _subscription_credential_baseline(
        self,
        session: Session,
        credential: SubscriptionCredentialModel,
    ) -> tuple[str, int, int, datetime | None]:
        for email in self._subscription_credential_emails(credential):
            baseline = self._subscription_traffic_baseline(session, credential.server_id, email)
            if baseline:
                last_uplink, last_downlink, reported_at = baseline
                return email, last_uplink, last_downlink, reported_at
        return credential.email, 0, 0, None

    def _subscription_traffic_baseline(
        self,
        session: Session,
        server_id: str,
        email: str,
    ) -> tuple[int, int, datetime] | None:
        latest = self._latest_telemetry_model(session, server_id)
        user_stats = latest.stats.get("user") if latest and latest.stats else None
        if not isinstance(user_stats, dict):
            return None
        item = user_stats.get(email)
        if not isinstance(item, dict):
            return None
        return (
            self._traffic_counter_value(item.get("uplink")),
            self._traffic_counter_value(item.get("downlink")),
            self._aware_datetime(latest.reported_at),
        )

    @classmethod
    def _subscription_reset_due(
        cls,
        user: ProductUserModel,
        plan: SubscriptionPlanModel | None,
        expired: bool,
        reset_due_at: datetime | None,
    ) -> bool:
        if not user.is_active or not plan or expired or not user.is_reset or not reset_due_at:
            return False
        active_since = cls._aware_datetime(user.plan_started_at or user.created_at)
        if reset_due_at <= active_since:
            return False
        last_reset = (
            cls._aware_datetime(user.last_traffic_reset_at)
            if user.last_traffic_reset_at
            else None
        )
        return last_reset is None or last_reset < reset_due_at

    @classmethod
    def _subscription_reset_boundaries(
        cls,
        user: ProductUserModel,
        now: datetime,
    ) -> tuple[datetime | None, datetime | None]:
        if not user.is_reset or user.reset_day <= 0:
            return None, None
        current = cls._monthly_reset_at(now.year, now.month, user.reset_day)
        if now >= current:
            next_year, next_month = cls._month_delta(now.year, now.month, 1)
            return current, cls._monthly_reset_at(next_year, next_month, user.reset_day)
        previous_year, previous_month = cls._month_delta(now.year, now.month, -1)
        return (
            cls._monthly_reset_at(previous_year, previous_month, user.reset_day),
            current,
        )

    @staticmethod
    def _month_delta(year: int, month: int, delta: int) -> tuple[int, int]:
        absolute = year * 12 + month - 1 + delta
        return absolute // 12, absolute % 12 + 1

    @staticmethod
    def _monthly_reset_at(year: int, month: int, day: int) -> datetime:
        return datetime(year, month, min(day, monthrange(year, month)[1]), tzinfo=UTC)

    def _subscription_user_traffic(self, session: Session, username: str) -> tuple[int, int]:
        ledgers = session.scalars(
            select(SubscriptionTrafficLedgerModel).where(
                SubscriptionTrafficLedgerModel.username == username
            )
        ).all()
        if ledgers:
            return (
                sum(ledger.upload for ledger in ledgers),
                sum(ledger.download for ledger in ledgers),
            )
        return self._subscription_latest_user_traffic(session, username)

    def _subscription_latest_user_traffic(
        self,
        session: Session,
        username: str,
    ) -> tuple[int, int]:
        credentials = session.scalars(
            select(SubscriptionCredentialModel).where(
                SubscriptionCredentialModel.username == username
            )
        ).all()
        emails = {credential.email for credential in credentials}
        for credential in credentials:
            raw_email = credential.credential.get("email")
            if isinstance(raw_email, str) and raw_email:
                emails.add(raw_email)
        if not emails:
            return 0, 0

        servers = session.scalars(select(ServerModel.id)).all()
        upload = 0
        download = 0
        for server_id in servers:
            latest = self._latest_telemetry_model(session, server_id)
            user_stats = latest.stats.get("user") if latest and latest.stats else None
            if not isinstance(user_stats, dict):
                continue
            for email in emails:
                item = user_stats.get(email)
                if not isinstance(item, dict):
                    continue
                upload += int(item.get("uplink") or 0)
                download += int(item.get("downlink") or 0)
        return upload, download

    @staticmethod
    def _subscription_token_record(
        token: ProductUserSubscriptionTokenModel,
    ) -> SubscriptionTokenRecord:
        return SubscriptionTokenRecord(
            username=token.username,
            token=token.token,
            short_code=token.short_code,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )

    @staticmethod
    def _subscription_credential_read(
        credential: SubscriptionCredentialModel,
    ) -> SubscriptionCredentialRead:
        return SubscriptionCredentialRead(
            id=UUID(credential.id),
            username=credential.username,
            node_id=UUID(credential.node_id),
            server_id=UUID(credential.server_id),
            inbound_tag=credential.inbound_tag,
            protocol=credential.protocol,
            email=credential.email,
            credential=credential.credential or {},
            created_at=credential.created_at,
            updated_at=credential.updated_at,
        )

    @staticmethod
    def _subscription_traffic_entry_read(
        entry: SubscriptionTrafficLedgerModel,
    ) -> SubscriptionTrafficEntryRead:
        return SubscriptionTrafficEntryRead(
            username=entry.username,
            server_id=UUID(entry.server_id),
            email=entry.email,
            upload=entry.upload,
            download=entry.download,
            total=entry.upload + entry.download,
            last_reported_at=InventoryStore._aware_datetime(entry.last_reported_at)
            if entry.last_reported_at
            else None,
            updated_at=InventoryStore._aware_datetime(entry.updated_at),
        )

    @staticmethod
    def _unique_subscription_token(session: Session) -> str:
        while True:
            token = token_urlsafe(32)
            exists = session.scalar(
                select(ProductUserSubscriptionTokenModel).where(
                    ProductUserSubscriptionTokenModel.token == token
                )
            )
            if not exists:
                return token

    @staticmethod
    def _unique_subscription_short_code(session: Session) -> str:
        while True:
            short_code = uuid4().hex[:8]
            exists = session.scalar(
                select(ProductUserSubscriptionTokenModel).where(
                    ProductUserSubscriptionTokenModel.short_code == short_code
                )
            )
            if not exists:
                return short_code

    @staticmethod
    def _default_client_email(user: ProductUserModel, node: ManagedNodeModel) -> str:
        label = (
            node.inbound_tag
            or node.routed_rule_marktag
            or node.routed_outbound_tag
            or node.name
        )
        suffix = "".join(
            char if char.isalnum() or char in {"-", "_", "."} else "_"
            for char in label.strip()
        ).strip("_")
        return f"{user.username}__{suffix or node.protocol}"

    @staticmethod
    def _template_context(
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        node: ManagedNodeModel,
        server: ServerModel,
        credential: SubscriptionCredentialModel,
    ) -> dict[str, str]:
        context = {
            "username": user.username,
            "user_email": user.email or user.username,
            "display_name": user.display_name or user.username,
            "client_email": credential.email,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "node_id": node.id,
            "node_name": node.name,
            "protocol": node.protocol,
            "server_id": server.id,
            "server_name": server.name,
            "server_domain": InventoryStore._server_subscription_host(server),
            "server_host": InventoryStore._server_subscription_host(server),
        }
        for key, value in (credential.credential or {}).items():
            if isinstance(value, str | int | float | bool):
                context[f"credential_{key}"] = str(value)
                if key in {"id", "password", "auth", "psk", "user", "pass"}:
                    context[key] = str(value)
        return context

    @classmethod
    def _render_template(cls, value: Any, context: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {key: cls._render_template(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._render_template(item, context) for item in value]
        if isinstance(value, str):
            rendered = value
            for key, replacement in context.items():
                rendered = rendered.replace("{" + key + "}", replacement)
            return rendered
        return value

    @staticmethod
    def _subscription_proxy_name(
        plan: SubscriptionPlanModel,
        node: ManagedNodeModel,
        name: str,
    ) -> str:
        multiplier = float((plan.node_multipliers or {}).get(node.id, 1))
        if multiplier == 1:
            return name
        if multiplier == int(multiplier):
            multiplier_text = str(int(multiplier))
        else:
            multiplier_text = f"{multiplier:g}"
        return f"[{multiplier_text}] {name}"

    @staticmethod
    def _shadowsocks_key_length(method: str) -> int:
        match method.strip().lower():
            case "2022-blake3-aes-128-gcm":
                return 16
            case "2022-blake3-aes-256-gcm" | "2022-blake3-chacha20-poly1305":
                return 32
        return 16

    @staticmethod
    def _safe_credential_username(username: str) -> str:
        cleaned = "".join(
            char if char.isalnum() or char in {"-", "_"} else "_" for char in username
        )
        return cleaned.strip("_") or "user"

    @staticmethod
    def _safe_filename(name: str) -> str:
        cleaned = "".join(
            char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name
        )
        return cleaned.strip("_") or "subscription"

    @staticmethod
    def subscription_content_disposition(filename: str) -> str:
        return "attachment; filename*=UTF-8''" + quote(filename)

    def _session(self) -> Session:
        return self._session_factory()

    @staticmethod
    def _server_record(server: ServerModel) -> ServerRecord:
        return ServerRecord(
            id=UUID(server.id),
            name=server.name,
            status=ServerStatus(server.status),
            ip_address=server.ip_address,
            ip_address_v6=server.ip_address_v6,
            domain=server.domain,
            domain_v6=server.domain_v6,
            connection_mode=ConnectionMode(server.connection_mode),
            listen_port=server.listen_port,
            pull_address=server.pull_address,
            pull_address_v6=server.pull_address_v6,
            pull_port=server.pull_port,
            ipv6_enabled=server.ipv6_enabled,
            traffic_limit=server.traffic_limit,
            traffic_stats_mode=TrafficStatsMode(server.traffic_stats_mode),
            traffic_source=TrafficSource(server.traffic_source),
            xray_mode=XrayMode(server.xray_mode),
            region=server.region,
            region_country=server.region_country,
            region_name=server.region_name,
            region_city=server.region_city,
            provider_name=server.provider_name,
            provider_url=server.provider_url,
            expires_at=InventoryStore._aware_datetime(server.expires_at)
            if server.expires_at
            else None,
            renewal_price=server.renewal_price,
            renewal_price_cny=server.renewal_price_cny,
            renewal_cycle=RenewalCycle(server.renewal_cycle) if server.renewal_cycle else None,
            renewal_currency=server.renewal_currency,
            telecom_paid_peer=server.telecom_paid_peer,
            current_upload_speed=server.current_upload_speed,
            current_download_speed=server.current_download_speed,
            last_heartbeat=server.last_heartbeat,
            created_at=server.created_at,
            updated_at=server.updated_at,
            agent_token=server.agent_token,
        )

    @staticmethod
    def _public_server(server: ServerModel) -> ServerRead:
        payload = InventoryStore._server_record(server).model_dump(exclude={"agent_token"})
        return ServerRead(**payload)

    @staticmethod
    def _agent_read(agent: AgentModel) -> AgentRead:
        return AgentRead(
            id=UUID(agent.id),
            server_id=UUID(agent.server_id),
            hostname=agent.hostname,
            agent_version=agent.agent_version,
            connection_mode=ConnectionMode(agent.connection_mode),
            listen_port=agent.listen_port,
            public_ipv4=agent.public_ipv4,
            public_ipv6=agent.public_ipv6,
            xray_mode=XrayMode(agent.xray_mode),
            capabilities=AgentCapabilities(
                rpc=agent.capability_rpc,
                stream=agent.capability_stream,
                return_route_test=agent.capability_return_route_test,
            ),
            warp_installed=agent.warp_installed,
            same_host_as_master=agent.same_host_as_master,
            registered_at=agent.registered_at,
            last_seen_at=agent.last_seen_at,
        )

    @staticmethod
    def _ensure_step_servers_exist(
        session: Session,
        steps: Iterable[AgentChangeSetStepCreate],
    ) -> None:
        server_ids = {str(step.server_id) for step in steps}
        existing_ids = set(
            session.scalars(select(ServerModel.id).where(ServerModel.id.in_(server_ids))).all()
        )
        missing_ids = sorted(server_ids - existing_ids)
        if missing_ids:
            raise ServerNotFoundError(f"server not found: {missing_ids[0]}")

    @staticmethod
    def _change_set_model(session: Session, change_set_id: UUID) -> AgentChangeSetModel:
        change_set = session.get(AgentChangeSetModel, str(change_set_id))
        if change_set:
            return change_set
        raise ChangeSetNotFoundError(f"change set not found: {change_set_id}")

    @staticmethod
    def _change_set_steps(session: Session, change_set_id: str) -> list[AgentChangeSetStepModel]:
        return list(
            session.scalars(
                select(AgentChangeSetStepModel)
                .where(AgentChangeSetStepModel.change_set_id == change_set_id)
                .order_by(AgentChangeSetStepModel.sequence)
            ).all()
        )

    def _change_set_read(
        self,
        session: Session,
        change_set: AgentChangeSetModel,
    ) -> AgentChangeSetRead:
        steps = self._change_set_steps(session, change_set.id)
        return AgentChangeSetRead(
            id=UUID(change_set.id),
            name=change_set.name,
            description=change_set.description,
            status=AgentChangeSetStatus(change_set.status),
            rollback_on_failure=change_set.rollback_on_failure,
            rollback_reason=change_set.rollback_reason,
            steps=[self._change_set_step_read(session, step) for step in steps],
            created_at=change_set.created_at,
            updated_at=change_set.updated_at,
        )

    @staticmethod
    def _change_set_step_read(
        session: Session,
        step: AgentChangeSetStepModel,
    ) -> AgentChangeSetStepRead:
        forward_command = (
            session.get(CommandModel, step.forward_command_id) if step.forward_command_id else None
        )
        rollback_command = (
            session.get(CommandModel, step.rollback_command_id)
            if step.rollback_command_id
            else None
        )
        return AgentChangeSetStepRead(
            id=UUID(step.id),
            change_set_id=UUID(step.change_set_id),
            sequence=step.sequence,
            server_id=UUID(step.server_id),
            label=step.label,
            forward=InventoryStore._step_forward_command(step),
            rollback=InventoryStore._step_rollback_command(step),
            forward_command=InventoryStore._command_read(forward_command)
            if forward_command
            else None,
            rollback_command=InventoryStore._command_read(rollback_command)
            if rollback_command
            else None,
            created_at=step.created_at,
            updated_at=step.updated_at,
        )

    @staticmethod
    def _step_forward_command(step: AgentChangeSetStepModel) -> AgentCommandCreate:
        return AgentCommandCreate(
            method=step.forward_method,
            path=step.forward_path,
            query=step.forward_query,
            body=step.forward_body,
            timeout_ms=step.forward_timeout_ms,
            stream=step.forward_stream,
        )

    @staticmethod
    def _step_rollback_command(step: AgentChangeSetStepModel) -> AgentCommandCreate | None:
        if not step.rollback_method or not step.rollback_path:
            return None
        return AgentCommandCreate(
            method=step.rollback_method,
            path=step.rollback_path,
            query=step.rollback_query,
            body=step.rollback_body,
            timeout_ms=step.rollback_timeout_ms or 30_000,
            stream=step.rollback_stream,
        )

    @staticmethod
    def _create_command_model(
        session: Session,
        server: ServerModel,
        payload: AgentCommandCreate,
        now: datetime | None = None,
    ) -> CommandModel:
        active_now = now or datetime.now(tz=UTC)
        command = CommandModel(
            id=str(uuid4()),
            server_id=server.id,
            request_id=f"{server.id}-{uuid4().hex}",
            method=payload.method,
            path=payload.path,
            query=payload.query,
            body=payload.body,
            timeout_ms=payload.timeout_ms,
            stream=payload.stream,
            status=AgentCommandStatus.PENDING.value,
            attempts=0,
            created_at=active_now,
            updated_at=active_now,
        )
        session.add(command)
        return command

    @staticmethod
    def _upsert_agent_scan_result(
        session: Session,
        server: ServerModel,
        payload: AgentScanResultPayload,
        reported_at: datetime,
        updated_at: datetime,
    ) -> AgentScanResultModel:
        values = payload.model_dump(mode="json", exclude={"token", "reported_at"})
        scan = session.get(AgentScanResultModel, server.id)
        if scan is None:
            scan = AgentScanResultModel(
                server_id=server.id,
                reported_at=reported_at,
                updated_at=updated_at,
                **values,
            )
            session.add(scan)
            return scan

        for field, value in values.items():
            setattr(scan, field, value)
        scan.reported_at = reported_at
        scan.updated_at = updated_at
        return scan

    @staticmethod
    def _upsert_current_xray_config_snapshot(
        session: Session,
        server: ServerModel,
        config: str,
        source: XrayConfigSnapshotSource,
        source_command_id: str | None,
        created_at: datetime,
    ) -> XrayConfigSnapshotModel:
        config_hash = InventoryStore._hash_xray_config(config)
        current_snapshots = session.scalars(
            select(XrayConfigSnapshotModel)
            .where(
                XrayConfigSnapshotModel.server_id == server.id,
                XrayConfigSnapshotModel.status == XrayConfigSnapshotStatus.CURRENT.value,
            )
            .order_by(XrayConfigSnapshotModel.created_at.desc())
        ).all()
        if current_snapshots and current_snapshots[0].config_hash == config_hash:
            return current_snapshots[0]

        for snapshot in current_snapshots:
            snapshot.status = XrayConfigSnapshotStatus.OLD.value

        snapshot = XrayConfigSnapshotModel(
            id=str(uuid4()),
            server_id=server.id,
            source_command_id=source_command_id,
            config=config,
            config_hash=config_hash,
            source=source.value,
            status=XrayConfigSnapshotStatus.CURRENT.value,
            size_bytes=len(config.encode("utf-8")),
            created_at=created_at,
        )
        session.add(snapshot)
        return snapshot

    @staticmethod
    def _xray_config_snapshot_source(
        command: CommandModel,
        payload: AgentCommandResultRequest,
    ) -> tuple[str | None, XrayConfigSnapshotSource]:
        if command.method.upper() == "GET":
            body = payload.body if isinstance(payload.body, dict) else {}
            if body.get("success") is False:
                return None, XrayConfigSnapshotSource.AGENT_REPORT
            return InventoryStore._xray_config_text(body.get("config")), (
                XrayConfigSnapshotSource.AGENT_REPORT
            )

        body = command.body if isinstance(command.body, dict) else {}
        return InventoryStore._xray_config_text(body.get("config")), (
            XrayConfigSnapshotSource.MASTER_WRITE
        )

    @staticmethod
    def _xray_config_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            return None

    @staticmethod
    def _hash_xray_config(config: str) -> str:
        return hashlib.sha256(config.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_probe_access_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _xray_config_snapshot_read(
        snapshot: XrayConfigSnapshotModel,
        include_config: bool = False,
    ) -> XrayConfigSnapshotRead:
        return XrayConfigSnapshotRead(
            id=UUID(snapshot.id),
            server_id=UUID(snapshot.server_id),
            source_command_id=UUID(snapshot.source_command_id)
            if snapshot.source_command_id
            else None,
            config_hash=snapshot.config_hash,
            source=XrayConfigSnapshotSource(snapshot.source),
            status=XrayConfigSnapshotStatus(snapshot.status),
            size_bytes=snapshot.size_bytes,
            config=snapshot.config if include_config else None,
            created_at=snapshot.created_at,
        )

    @staticmethod
    def _scan_result_read(scan: AgentScanResultModel) -> AgentScanResultRead:
        return AgentScanResultRead(
            server_id=UUID(scan.server_id),
            xray_running=scan.xray_running,
            xray_version=scan.xray_version,
            api_port=scan.api_port,
            config_path=scan.config_path,
            inbounds=scan.inbounds or [],
            device_kicks=scan.device_kicks or {},
            config_modified=scan.config_modified,
            config_added_sections=scan.config_added_sections or [],
            message=scan.message,
            reported_at=scan.reported_at,
            updated_at=scan.updated_at,
        )

    @staticmethod
    def _telemetry_read(snapshot: TelemetrySnapshotModel) -> AgentTelemetryRead:
        system = None
        if (
            snapshot.system_rx_total is not None
            and snapshot.system_tx_total is not None
            and snapshot.system_boot_time_unix is not None
        ):
            system = SystemTraffic(
                rx_total=snapshot.system_rx_total,
                tx_total=snapshot.system_tx_total,
                boot_time_unix=snapshot.system_boot_time_unix,
            )

        return AgentTelemetryRead(
            id=UUID(snapshot.id),
            server_id=UUID(snapshot.server_id),
            reported_at=snapshot.reported_at,
            received_at=snapshot.received_at,
            stats=XrayStats.model_validate(snapshot.stats) if snapshot.stats else None,
            online_users=snapshot.online_users or {},
            user_speeds=snapshot.user_speeds or {},
            conn_counts=snapshot.conn_counts or {},
            system=system,
            sysmetrics=ProbeSysMetrics.model_validate(snapshot.sysmetrics)
            if snapshot.sysmetrics
            else None,
            latency=[
                ProbeLatencySample.model_validate(sample) for sample in (snapshot.latency or [])
            ],
        )

    @staticmethod
    def _command_read(command: CommandModel) -> AgentCommandRead:
        return AgentCommandRead(
            id=UUID(command.id),
            server_id=UUID(command.server_id),
            request_id=command.request_id,
            method=command.method,
            path=command.path,
            query=command.query,
            body=command.body,
            timeout_ms=command.timeout_ms,
            stream=command.stream,
            status=AgentCommandStatus(command.status),
            attempts=command.attempts,
            result_status=command.result_status,
            result_body=command.result_body,
            result_error=command.result_error,
            created_at=command.created_at,
            leased_at=command.leased_at,
            completed_at=command.completed_at,
            updated_at=command.updated_at,
        )

    @staticmethod
    def _stream_frame_read(
        frame: CommandStreamFrameModel,
        command: CommandModel,
    ) -> AgentCommandStreamFrameRead:
        return AgentCommandStreamFrameRead(
            id=UUID(frame.id),
            command_id=UUID(frame.command_id),
            server_id=UUID(frame.server_id),
            request_id=command.request_id,
            sequence=frame.sequence,
            data=frame.data,
            received_at=frame.received_at,
        )

    @staticmethod
    def _server_by_token(session: Session, token: str) -> ServerModel:
        server = session.scalar(select(ServerModel).where(ServerModel.agent_token == token))
        if server:
            return server
        raise InvalidAgentTokenError("invalid agent token")

    @staticmethod
    def _latest_telemetry_model(
        session: Session,
        server_id: str,
    ) -> TelemetrySnapshotModel | None:
        return session.scalar(
            select(TelemetrySnapshotModel)
            .where(TelemetrySnapshotModel.server_id == server_id)
            .order_by(
                TelemetrySnapshotModel.reported_at.desc(),
                TelemetrySnapshotModel.received_at.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _update_server_speed_from_system_traffic(
        server: ServerModel,
        previous: TelemetrySnapshotModel | None,
        payload: AgentTelemetryReport,
    ) -> None:
        if not payload.system or not previous:
            return
        if (
            previous.system_rx_total is None
            or previous.system_tx_total is None
            or previous.system_boot_time_unix != payload.system.boot_time_unix
        ):
            return
        if (
            payload.system.rx_total < previous.system_rx_total
            or payload.system.tx_total < previous.system_tx_total
        ):
            return

        current_at = InventoryStore._aware_datetime(payload.reported_at or datetime.now(tz=UTC))
        previous_at = InventoryStore._aware_datetime(previous.reported_at)
        elapsed = (current_at - previous_at).total_seconds()
        if elapsed <= 0:
            return
        server.current_download_speed = int(
            (payload.system.rx_total - previous.system_rx_total) / elapsed
        )
        server.current_upload_speed = int(
            (payload.system.tx_total - previous.system_tx_total) / elapsed
        )

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _lease_expired(command: CommandModel, now: datetime) -> bool:
        if command.leased_at is None:
            return True
        leased_at = InventoryStore._aware_datetime(command.leased_at)
        elapsed_ms = (now - leased_at).total_seconds() * 1000
        return elapsed_ms >= command.timeout_ms

    def _apply_command_result(
        self,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
    ) -> None:
        now = datetime.now(tz=UTC)
        command.status = (
            AgentCommandStatus.FAILED.value
            if payload.error or payload.status >= 400
            else AgentCommandStatus.SUCCEEDED.value
        )
        command.result_status = payload.status
        command.result_body = payload.body
        command.result_error = payload.error
        command.completed_at = now
        command.updated_at = now
        server.last_heartbeat = now
        server.updated_at = now
        self._upsert_return_route_results(session, server, command, payload, now)
        self._record_domain_latency_result(session, server, command, payload, now)
        self._record_scan_result_from_command(session, server, command, payload, now)
        self._record_xray_config_snapshot_from_command(session, server, command, payload, now)

    def _record_scan_result_from_command(
        self,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
        received_at: datetime,
    ) -> None:
        if command.path != "/api/child/scan" or payload.error or payload.status >= 400:
            return
        if not isinstance(payload.body, dict):
            return
        try:
            scan_payload = AgentScanResultPayload.model_validate(payload.body)
        except ValidationError:
            return
        server.status = ServerStatus.CONNECTED.value
        agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
        if agent:
            agent.last_seen_at = received_at
        self._upsert_agent_scan_result(session, server, scan_payload, received_at, received_at)

    def _record_xray_config_snapshot_from_command(
        self,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
        created_at: datetime,
    ) -> None:
        if command.path != "/api/child/xray/config" or payload.error or payload.status >= 400:
            return
        config, source = self._xray_config_snapshot_source(command, payload)
        if not config or not config.strip():
            return
        self._upsert_current_xray_config_snapshot(
            session,
            server,
            config=config,
            source=source,
            source_command_id=command.id,
            created_at=created_at,
        )

    def _upsert_return_route_results(
        self,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
        tested_at: datetime,
    ) -> None:
        if (
            command.path != "/api/child/network/return-route-test"
            or payload.error
            or payload.status >= 400
        ):
            return
        results = self._return_route_results(payload.body)
        if not results:
            return
        for item in results:
            route = self._return_route_values(item)
            if route is None:
                continue
            record = session.get(
                ServerReturnRouteModel,
                {"server_id": server.id, "carrier": route["carrier"]},
            )
            if record is None:
                record = ServerReturnRouteModel(
                    server_id=server.id,
                    carrier=route["carrier"],
                    region=route["region"],
                    route_type=route["route_type"],
                    entry_ip=route["entry_ip"],
                    entry_asn=route["entry_asn"],
                    reason=route["reason"],
                    tested_at=tested_at,
                    updated_at=tested_at,
                )
                session.add(record)
                continue
            record.region = route["region"]
            record.route_type = route["route_type"]
            record.entry_ip = route["entry_ip"]
            record.entry_asn = route["entry_asn"]
            record.reason = route["reason"]
            record.tested_at = tested_at
            record.updated_at = tested_at

    @staticmethod
    def _return_route_results(body: Any) -> list[Any]:
        if isinstance(body, dict):
            results = body.get("results")
            return results if isinstance(results, list) else []
        return body if isinstance(body, list) else []

    @staticmethod
    def _return_route_values(item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None
        carrier = str(item.get("carrier") or "").strip().lower()
        if carrier not in {"telecom", "unicom", "mobile"}:
            return None
        entry_hop = item.get("entry_hop") if isinstance(item.get("entry_hop"), dict) else {}
        route_type = InventoryStore._return_route_text(
            item.get("route_type") or item.get("route"),
            80,
        )
        return {
            "carrier": carrier,
            "region": InventoryStore._return_route_text(item.get("region"), 120),
            "route_type": route_type or "Unknown",
            "entry_ip": InventoryStore._return_route_text(
                item.get("entry_ip") or entry_hop.get("ip"),
                255,
            ),
            "entry_asn": InventoryStore._return_route_text(
                item.get("entry_asn") or entry_hop.get("asn"),
                32,
            ),
            "reason": InventoryStore._return_route_text(
                item.get("reason") or item.get("error"),
                2048,
            ),
        }

    @staticmethod
    def _return_route_text(value: Any, max_length: int) -> str:
        if value is None:
            return ""
        normalized = str(value).strip()
        normalized = "".join(char for char in normalized if ord(char) >= 32)
        return normalized[:max_length]

    def _record_domain_latency_result(
        self,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
        received_at: datetime,
    ) -> None:
        if command.path != "/api/child/domains/latency" or payload.error or payload.status >= 400:
            return
        samples = self._domain_latency_result_samples(payload.body, received_at)
        if not samples:
            return
        telemetry = TelemetrySnapshotModel(
            id=str(uuid4()),
            server_id=server.id,
            reported_at=received_at,
            received_at=received_at,
            stats=None,
            online_users={},
            user_speeds={},
            conn_counts={},
            system_rx_total=None,
            system_tx_total=None,
            system_boot_time_unix=None,
            sysmetrics=None,
            latency=[sample.model_dump(mode="json") for sample in samples],
        )
        session.add(telemetry)

    @staticmethod
    def _domain_latency_result_samples(
        body: Any,
        received_at: datetime,
    ) -> list[ProbeLatencySample]:
        samples: list[ProbeLatencySample] = []
        for item in InventoryStore._domain_latency_result_items(body):
            if not isinstance(item, dict):
                continue
            key = InventoryStore._domain_latency_key(item)
            if not key:
                continue
            at = InventoryStore._nonnegative_int(item.get("at"))
            samples.append(
                ProbeLatencySample(
                    key=key,
                    success=bool(item.get("success")),
                    latency_ms=InventoryStore._nonnegative_int(item.get("latency_ms")) or 0,
                    at=at if at is not None else int(received_at.timestamp()),
                )
            )
        return samples

    @staticmethod
    def _domain_latency_result_items(body: Any) -> list[Any]:
        if isinstance(body, dict):
            results = body.get("results")
            return results if isinstance(results, list) else []
        return body if isinstance(body, list) else []

    @staticmethod
    def _domain_latency_key(item: dict[str, Any]) -> str:
        raw = item.get("key") or item.get("domain") or item.get("target")
        if raw is None:
            return ""
        normalized = str(raw).strip()
        normalized = "".join(char for char in normalized if ord(char) >= 32)
        return normalized[:120]

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(parsed, 0)


def create_inventory_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    connect_args = {}
    if url.drivername.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args=connect_args, future=True)
