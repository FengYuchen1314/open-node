from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from calendar import monthrange
from collections.abc import Iterable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from secrets import token_bytes, token_urlsafe
from typing import Any, Literal
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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from open_node.domain.changes import (
    AgentChangeSetAcceptRequest,
    AgentChangeSetCreate,
    AgentChangeSetRead,
    AgentChangeSetRollbackRequest,
    AgentChangeSetStatus,
    AgentChangeSetStepCreate,
    AgentChangeSetStepRead,
    AgentRoutedOutboundChangeSetCreate,
)
from open_node.domain.inventory import (
    AgentCapabilities,
    AgentCommandCreate,
    AgentCommandPayloadError,
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
    TrafficData,
    TrafficSource,
    TrafficStatsMode,
    XrayConfigSnapshotRead,
    XrayConfigSnapshotRecoveryAcceptResponse,
    XrayConfigSnapshotRecoveryStatusResponse,
    XrayConfigSnapshotSource,
    XrayConfigSnapshotStatus,
    XrayMode,
    XrayRuntimeInboundRead,
    XrayRuntimeInventoryResponse,
    XrayRuntimeTunnelChainCreateCommand,
    XrayRuntimeTunnelChainCreateRequest,
    XrayRuntimeTunnelChainCreateResponse,
    XrayRuntimeTunnelChainHopRead,
    XrayRuntimeTunnelChainRead,
    XrayRuntimeTunnelDeleteCommand,
    XrayRuntimeTunnelDeleteRequest,
    XrayRuntimeTunnelDeleteResponse,
    XrayRuntimeTunnelDeployCommand,
    XrayRuntimeTunnelDeployRequest,
    XrayRuntimeTunnelDeployResponse,
    XrayRuntimeTunnelHopRead,
    XrayRuntimeTunnelInventoryResponse,
    XrayRuntimeTunnelRead,
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
from open_node.domain.subscription_links import SubscriptionShortCodeUpdate
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
    SubscriptionFormatNode,
    SubscriptionFormatPreview,
    SubscriptionPlanAssignRequest,
    SubscriptionPlanCreate,
    SubscriptionPlanRead,
    SubscriptionProvisionBatch,
    SubscriptionQuotaStatusRead,
    SubscriptionTemplatePresetApplyRequest,
    SubscriptionTemplatePresetRead,
    SubscriptionTrafficEntryRead,
    SubscriptionTrafficMode,
    XrayRuntimeCredentialCleanupCommand,
    XrayRuntimeCredentialCleanupEntry,
    XrayRuntimeCredentialCleanupRequest,
    XrayRuntimeCredentialCleanupResponse,
    XrayRuntimeCredentialReconciliationEntry,
    XrayRuntimeCredentialReconciliationResponse,
    XrayRuntimeCredentialRepairEntry,
    XrayRuntimeCredentialRepairRequest,
    XrayRuntimeCredentialRepairResponse,
    XrayRuntimeNodeCreateRequest,
    XrayRuntimeNodeDraft,
    XrayRuntimeNodeDraftsResponse,
    XrayRuntimeNodeImportRequest,
    XrayRuntimeNodeImportResponse,
    XrayRuntimeNodeImportSkipped,
    XrayRuntimeNodeReconciliationDrift,
    XrayRuntimeNodeReconciliationManagedEntry,
    XrayRuntimeNodeReconciliationResponse,
    XrayRuntimeNodeReconciliationRuntimeEntry,
    XrayRuntimeNodeSyncRequest,
    XrayRuntimeNodeSyncResponse,
)
from open_node.services import subscription_clients
from open_node.services.tunnel_config import managed_tunnel_bundle


class InvalidAgentTokenError(ValueError):
    """Raised when an agent presents an unknown bootstrap token."""


class DuplicateServerNameError(ValueError):
    """Raised when a server name would no longer be a stable inventory key."""


class ServerNotFoundError(ValueError):
    """Raised when an inventory lookup targets an unknown server."""


class CommandNotFoundError(ValueError):
    """Raised when an agent command cannot be found for the requesting server."""


class CommandNotReadyError(ValueError):
    """Raised when a result arrives before a command's prerequisite succeeds."""


class XrayConfigSnapshotNotFoundError(ValueError):
    """Raised when an Xray config snapshot lookup targets an unknown snapshot."""


class XrayConfigSnapshotRecoveryUnavailableError(ValueError):
    """Raised when an Xray config recovery decision has no usable snapshot."""


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


class ProductUserConflict(ValueError):
    """Raised when a user change conflicts with removal or protected identity."""


class DuplicateSubscriptionPlanNameError(ValueError):
    """Raised when a subscription plan name is already taken."""


class SubscriptionPlanNotFoundError(ValueError):
    """Raised when a subscription plan lookup targets an unknown plan."""


class ManagedNodeNotFoundError(ValueError):
    """Raised when a managed node lookup targets an unknown node."""


class ManagedNodeConflict(ValueError):
    """Raised when a node mutation conflicts with managed resources."""


class SubscriptionTokenNotFoundError(ValueError):
    """Raised when a public subscription token or short code is unknown."""


class SubscriptionUnavailableError(ValueError):
    """Raised when a product user has no active renderable subscription."""


class SubscriptionTemplatePresetNotFoundError(ValueError):
    """Raised when a subscription node preset lookup targets an unknown preset."""


class XrayRuntimeInboundNotFoundError(ValueError):
    """Raised when no scan-derived Xray inbound matches a runtime node request."""


class XrayRuntimeNodeDraftUnavailableError(ValueError):
    """Raised when a scan-derived inbound cannot become a managed node."""


class XrayRuntimeTunnelNotFoundError(ValueError):
    """Raised when a snapshot-derived runtime tunnel target is unavailable."""


class XrayRuntimeTunnelChainUnavailableError(ValueError):
    """Raised when a runtime tunnel chain cannot be planned safely."""


class XrayRuntimeTunnelDeployUnavailableError(ValueError):
    """Raised when a runtime tunnel deployment cannot be planned safely."""


_PROBE_SERIES_RANGES = {
    "1h": (12, 300),
    "6h": (36, 600),
    "24h": (48, 1800),
}

_TUNNEL_NGINX_CONFIG = """user root;
worker_processes auto;

error_log /var/log/nginx/error.log notice;
pid /var/run/nginx.pid;

events {
    worker_connections 4096;
}

http {
    include       mime.types;
    log_format main '[$time_local] $proxy_protocol_addr "$http_referer" "$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    map $http_upgrade $connection_upgrade {
        default upgrade;
        ""      close;
    }
    map $proxy_protocol_addr $proxy_forwarded_elem {
        ~^[0-9.]+$        "for=$proxy_protocol_addr";
        ~^[0-9A-Fa-f:.]+$ "for=\\"[$proxy_protocol_addr]\\"";
        default           "for=unknown";
    }
    map $http_forwarded $proxy_add_forwarded {
        default "$proxy_forwarded_elem";
    }
    server {
        listen 80;
        listen [::]:80;
        return 301 https://$host$request_uri;
    }

    server {
        listen                  127.0.0.1:8001 ssl proxy_protocol default_server;
        listen                  127.0.0.1:8001       quic;

        ssl_reject_handshake    on;

        ssl_protocols           TLSv1.2 TLSv1.3;

        ssl_session_timeout     1h;
        ssl_session_cache       shared:SSL:10m;
    }

    include servers/*.conf;
}
stream {
    include stream_servers/*.conf;
}
"""

_TUNNEL_SSL_CIPHERS = (
    "TLS13_AES_128_GCM_SHA256:TLS13_AES_256_GCM_SHA384:"
    "TLS13_CHACHA20_POLY1305_SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:"
    "ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305"
)

_TUNNEL_CORS_HEADERS = (
    "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,"
    "Content-Type,Range,Authorization"
)

_TUNNEL_DOMAIN_PROXY_CONFIG = """server {
    listen                     127.0.0.1:8001 ssl proxy_protocol;
    http2                      on;
    client_max_body_size       512m;
    set_real_ip_from           127.0.0.1;
    real_ip_header             proxy_protocol;

    server_name                {domain};
    ssl_certificate            cert/{cert_name}.pem;
    ssl_certificate_key        cert/{cert_name}.key;
    ssl_protocols              TLSv1.2 TLSv1.3;
    ssl_ciphers                {ssl_ciphers};
    ssl_prefer_server_ciphers  on;
    ssl_stapling               on;
    ssl_stapling_verify        on;
    resolver                   1.1.1.1 valid=60s;
    resolver_timeout           2s;
    location / {
        proxy_pass              {site_value};
        proxy_http_version      1.1;
        proxy_cache_bypass      $http_upgrade;
        proxy_set_header Host                 $host;
        proxy_set_header Upgrade              $http_upgrade;
        proxy_set_header Connection           $connection_upgrade;
        proxy_set_header X-Real-IP            $proxy_protocol_addr;
        proxy_set_header Forwarded            $proxy_add_forwarded;
        proxy_set_header X-Forwarded-For      $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto    https;
        proxy_set_header X-Forwarded-Host     $host;
        proxy_set_header X-Forwarded-Port     $server_port;
        proxy_connect_timeout   60s;
        proxy_send_timeout      60s;
        proxy_read_timeout      60s;
    }
}
"""

_TUNNEL_DOMAIN_STATIC_CONFIG = """server {
    listen                     127.0.0.1:8001 http2 ssl proxy_protocol;
    client_max_body_size       512m;
    set_real_ip_from           127.0.0.1;
    real_ip_header             proxy_protocol;

    server_name                {domain};
    ssl_certificate            cert/{cert_name}.pem;
    ssl_certificate_key        cert/{cert_name}.key;
    ssl_protocols              TLSv1.2 TLSv1.3;
    ssl_ciphers                {ssl_ciphers};
    ssl_prefer_server_ciphers  on;

    ssl_stapling               on;
    ssl_stapling_verify        on;
    resolver                   1.1.1.1 valid=60s;
    resolver_timeout           2s;
    location / {
        sub_filter                            $proxy_host $host;
        sub_filter_once                       off;
        root {site_value};
        index index.html;
        resolver                              1.1.1.1;

        proxy_set_header Host                 $proxy_host;

        proxy_http_version                    1.1;
        proxy_cache_bypass                    $http_upgrade;
        proxy_ssl_server_name                 on;
        proxy_set_header Upgrade              $http_upgrade;
        proxy_set_header Connection           $connection_upgrade;
        proxy_set_header X-Real-IP            $proxy_protocol_addr;
        proxy_set_header Forwarded            $proxy_add_forwarded;
        proxy_set_header X-Forwarded-For      $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto    $scheme;
        proxy_set_header X-Forwarded-Host     $host;
        proxy_set_header X-Forwarded-Port     $server_port;
        add_header 'X-Content-Type-Options' nosniff;
        add_header 'Access-Control-Allow-Origin' '*';
        add_header 'Access-Control-Allow-Methods' 'GET, POST, OPTIONS';
        add_header 'Access-Control-Allow-Headers' '{cors_headers}';
        proxy_connect_timeout                 60s;
        proxy_send_timeout                    60s;
        proxy_read_timeout                    60s;
    }
}
"""

_XRAY_TUNNEL_FORWARD_PORT = 46_174
_XRAY_API_PORT = 46_736
_XRAY_METRICS_PORT = 38_889
_XRAY_SNAPSHOT_REFRESH_QUERY = "snapshot_source=master_write"
_XRAY_SNAPSHOT_REFRESH_TIMEOUT_MS = 60_000
_XRAY_RECONNECT_SYNC_TIMEOUT_MS = 60_000
_XRAY_MUTATING_PATH_PREFIXES = (
    "/api/child/xray/install",
    "/api/child/xray/install-stream",
    "/api/child/xray/rollback",
    "/api/child/tunnel/deploy",
    "/api/child/inbounds",
    "/api/child/outbounds",
    "/api/child/routing",
    "/api/child/batch-apply",
    "/api/child/subscription-access",
    "/api/child/xray/config",
    "/api/child/xray/config-files",
    "/api/child/xray/system-config",
    "/api/child/external-xray/takeover",
)

_XRAY_CLIENT_CONTAINER_BY_PROTOCOL = {
    "vless": "clients",
    "vmess": "clients",
    "trojan": "clients",
    "shadowsocks": "clients",
    "hysteria": "clients",
    "anytls": "users",
    "snell": "users",
    "mieru": "users",
    "socks": "accounts",
    "http": "accounts",
}

_XRAY_MANAGED_NODE_PROTOCOLS = {
    "vless": "vless",
    "vmess": "vmess",
    "trojan": "trojan",
    "shadowsocks": "shadowsocks",
    "ss": "shadowsocks",
    "hysteria": "hysteria2",
    "hysteria2": "hysteria2",
    "hy2": "hysteria2",
    "anytls": "anytls",
    "snell": "snell",
    "mieru": "mieru",
    "socks": "socks",
    "http": "http",
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
        "id": "anytls",
        "name": "AnyTLS",
        "description": "AnyTLS node for the active Xray fork with generated passwords.",
        "protocol": "anytls",
        "node_type": "physical",
        "inbound_tag": "anytls-443",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "anytls",
        "tags": ["anytls", "tls"],
        "client_template": {"email": "{username}__anytls-443"},
        "config": {
            "name": "{server_name} AnyTLS",
            "type": "anytls",
            "server": "{server_domain}",
            "port": 443,
            "udp": True,
            "idle-session-check-interval": 30,
            "idle-session-timeout": 30,
            "min-idle-session": 0,
        },
    },
    {
        "id": "snell-v4",
        "name": "Snell v4",
        "description": "Snell v4 node with generated per-user PSK credentials.",
        "protocol": "snell",
        "node_type": "physical",
        "inbound_tag": "snell-443",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "snell",
        "tags": ["snell"],
        "client_template": {
            "email": "{username}__snell-443",
            "version": 4,
            "obfsMode": "none",
        },
        "config": {
            "name": "{server_name} Snell",
            "type": "snell",
            "server": "{server_domain}",
            "port": 443,
            "version": 4,
            "udp": True,
            "reuse": True,
        },
    },
    {
        "id": "snell-v6",
        "name": "Snell v6",
        "description": "Snell v6 node using the fork's per-user PSK identification.",
        "protocol": "snell",
        "node_type": "physical",
        "inbound_tag": "snell-v6-443",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "snell-v6",
        "tags": ["snell", "v6"],
        "client_template": {
            "email": "{username}__snell-v6-443",
            "version": 6,
            "v6Mode": "default",
        },
        "config": {
            "name": "{server_name} Snell v6",
            "type": "snell",
            "server": "{server_domain}",
            "port": 443,
            "version": 6,
            "mode": "default",
            "udp": True,
        },
    },
    {
        "id": "mieru",
        "name": "Mieru",
        "description": "Mieru node for the active Xray fork with username/password credentials.",
        "protocol": "mieru",
        "node_type": "physical",
        "inbound_tag": "mieru-2999",
        "routed_outbound_tag": None,
        "routed_rule_marktag": None,
        "tag": "mieru",
        "tags": ["mieru"],
        "client_template": {"email": "{username}__mieru-2999"},
        "config": {
            "name": "{server_name} Mieru",
            "type": "mieru",
            "server": "{server_domain}",
            "port": 2999,
            "transport": "TCP",
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
    included_nodes: int
    excluded_nodes: int


@dataclass(frozen=True)
class SubscriptionTokenRecord:
    username: str
    token: str
    short_code: str
    generated_short_code: str
    custom_short_code: str | None
    revision: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ExpectedRuntimeCredential:
    user: ProductUserModel
    plan: SubscriptionPlanModel
    node: ManagedNodeModel
    credential: SubscriptionCredentialModel | None
    email: str


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
    traffic_reset_day: Mapped[int] = mapped_column(Integer, default=0)
    last_traffic_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class ServerTrafficModel(Base):
    __tablename__ = "server_traffic"

    server_id: Mapped[str] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    upload: Mapped[int] = mapped_column(BigInteger, default=0)
    download: Mapped[int] = mapped_column(BigInteger, default=0)
    baseline_upload: Mapped[int] = mapped_column(BigInteger, default=0)
    baseline_download: Mapped[int] = mapped_column(BigInteger, default=0)
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServerTrafficDailyModel(Base):
    __tablename__ = "server_traffic_daily"

    server_id: Mapped[str] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    upload: Mapped[int] = mapped_column(BigInteger, default=0)
    download: Mapped[int] = mapped_column(BigInteger, default=0)


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
    capability_native_limiter: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_user_auto_speed_rules: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_subscription_access: Mapped[bool] = mapped_column(Boolean, default=False)
    capability_node_cleanup: Mapped[bool] = mapped_column(Boolean, default=False)
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
    nginx: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    http01: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
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
    depends_on_command_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_commands.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
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
    resolution_reason: Mapped[str] = mapped_column(Text, default="")
    coordination_version: Mapped[int] = mapped_column(Integer, default=1)
    archived_steps: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChangeSetServerLockModel(Base):
    __tablename__ = "change_set_server_locks"

    server_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    change_set_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_change_sets.id", ondelete="CASCADE"),
        index=True,
    )


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
    rollback_history_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
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
    remark: Mapped[str] = mapped_column(Text, default="")
    traffic_limit_override_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    speed_limit_override_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_limit_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_speed_limit_overrides: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    node_device_limit_overrides: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    removal_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("product_user_removals.id"), nullable=True
    )
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


class ProductUserRemovalModel(Base):
    __tablename__ = "product_user_removals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fingerprints: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    servers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


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
    parent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("managed_nodes.id", ondelete="SET NULL"), nullable=True
    )
    target_node_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("managed_nodes.id", ondelete="SET NULL"), nullable=True
    )
    removal_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("managed_node_removals.id"), nullable=True
    )
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


class PrivateRoutedPolicyModel(Base):
    __tablename__ = "private_routed_policy"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_nodes: Mapped[int] = mapped_column(Integer, default=2)
    daily_limit: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrivateRoutedNodeModel(Base):
    __tablename__ = "private_routed_nodes"

    node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("managed_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    username: Mapped[str] = mapped_column(
        String(80), ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), index=True)
    action: Mapped[str] = mapped_column(String(16))
    change_set_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_change_sets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    outbound: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    client: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrivateRoutedActionModel(Base):
    __tablename__ = "private_routed_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80), ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ManagedNodeRemovalModel(Base):
    __tablename__ = "managed_node_removals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    fingerprints: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    servers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)


class SubscriptionPlanModel(Base):
    __tablename__ = "subscription_plans"

    clash_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscription_templates.id", ondelete="SET NULL"), nullable=True
    )
    surge_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscription_templates.id", ondelete="SET NULL"), nullable=True
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger)
    cycle_days: Mapped[int] = mapped_column(Integer)
    is_reset: Mapped[bool] = mapped_column(Boolean, default=False)
    reset_day: Mapped[int] = mapped_column(Integer, default=0)
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    node_multipliers: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    node_name_overrides: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    node_name_override_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_speed_rules: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
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
    custom_short_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("uq_subscription_custom_short_code", func.lower(custom_short_code), unique=True),
    )


class SubscriptionProfileModel(Base):
    __tablename__ = "subscription_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_username: Mapped[str] = mapped_column(
        String(80), ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    clash_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscription_templates.id", ondelete="SET NULL"), nullable=True
    )
    surge_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("subscription_templates.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    source_type: Mapped[str] = mapped_column(String(24), default="managed")
    source_filename: Mapped[str] = mapped_column(String(255), default="")
    source_template_filename: Mapped[str] = mapped_column(String(255), default="")
    legacy_source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legacy_file_short_code: Mapped[str | None] = mapped_column(
        String(24), nullable=True, index=True
    )
    legacy_custom_short_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    legacy_selected_node_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    legacy_selected_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    migration_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("legacy_source_id", name="uq_subscription_profile_legacy_source"),
        Index(
            "uq_subscription_profile_file_short_code",
            func.lower(legacy_file_short_code),
            unique=True,
        ),
        Index(
            "uq_subscription_profile_custom_short_code",
            func.lower(legacy_custom_short_code),
            unique=True,
        ),
    )


class SubscriptionProfileAssignmentModel(Base):
    __tablename__ = "subscription_profile_assignments"

    profile_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("subscription_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    username: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("product_users.username", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LegacySubscriptionPlanCodeModel(Base):
    __tablename__ = "legacy_subscription_plan_codes"

    code: Mapped[str] = mapped_column(String(24), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("subscription_plans.id", ondelete="CASCADE"), index=True
    )
    source_package_id: Mapped[int] = mapped_column(Integer, unique=True)
    source_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("uq_legacy_subscription_plan_code", func.lower(code), unique=True),)


class TemporarySubscriptionModel(Base):
    __tablename__ = "temporary_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(
        String(80), ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))
    node_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_access: Mapped[int] = mapped_column(Integer)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
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


class SubscriptionAccessModel(Base):
    __tablename__ = "subscription_access"
    __table_args__ = (
        UniqueConstraint("username", "server_id", name="uq_subscription_access_server"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80), ForeignKey("product_users.username", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("servers.id", ondelete="CASCADE"), index=True
    )
    bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    applied_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    command_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_commands.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class SubscriptionArchivedTrafficModel(Base):
    __tablename__ = "subscription_archived_traffic"

    username: Mapped[str] = mapped_column(
        ForeignKey("product_users.username", ondelete="CASCADE"), primary_key=True
    )
    server_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_name: Mapped[str] = mapped_column(String(120))
    upload: Mapped[int] = mapped_column(BigInteger, default=0)
    download: Mapped[int] = mapped_column(BigInteger, default=0)
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
        from open_node.services.subscriber_auth import SubscriberAccount
        from open_node.services.subscription_templates import TemplateRecord

        SubscriberAccount.metadata.create_all(self._engine)
        TemplateRecord.metadata.create_all(self._engine)
        self._migrate_schema()
        self._change_sets().migrate_legacy()
        self._server_traffic().backfill()

    def _migrate_schema(self) -> None:
        if self._engine.dialect.name != "sqlite":
            return
        inspector = inspect(self._engine)
        table_names = set(inspector.get_table_names())
        if "subscription_plans" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "subscription_plans",
                {
                    "node_name_overrides": "JSON NOT NULL DEFAULT '{}'",
                    "node_name_override_enabled": "BOOLEAN NOT NULL DEFAULT 0",
                    "auto_speed_rules": "JSON NOT NULL DEFAULT '[]'",
                    "clash_template_id": (
                        "VARCHAR(36) REFERENCES subscription_templates(id) ON DELETE SET NULL"
                    ),
                    "surge_template_id": (
                        "VARCHAR(36) REFERENCES subscription_templates(id) ON DELETE SET NULL"
                    ),
                },
            )
        if "product_user_subscription_tokens" in table_names:
            self._sqlite_add_missing_columns(
                inspector, "product_user_subscription_tokens", {"custom_short_code": "VARCHAR(16)"}
            )
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_custom_short_code "
                        "ON product_user_subscription_tokens (lower(custom_short_code))"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS "
                        "ix_product_user_subscription_tokens_custom_short_code "
                        "ON product_user_subscription_tokens (custom_short_code)"
                    )
                )
        if "agents" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "agents",
                {
                    "capability_native_limiter": "BOOLEAN NOT NULL DEFAULT 0",
                    "capability_user_auto_speed_rules": "BOOLEAN NOT NULL DEFAULT 0",
                    "capability_subscription_access": "BOOLEAN NOT NULL DEFAULT 0",
                    "capability_node_cleanup": "BOOLEAN NOT NULL DEFAULT 0",
                },
            )
        if "agent_change_sets" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "agent_change_sets",
                {
                    "resolution_reason": "TEXT NOT NULL DEFAULT ''",
                    "coordination_version": "INTEGER NOT NULL DEFAULT 0",
                    "archived_steps": "JSON NOT NULL DEFAULT '[]'",
                },
            )
            self._sqlite_add_missing_columns(
                inspector,
                "agent_change_set_steps",
                {
                    "rollback_history_ids": "JSON NOT NULL DEFAULT '[]'",
                },
            )
        if "agent_scan_results" in table_names:
            self._sqlite_add_missing_columns(
                inspector, "agent_scan_results", {"nginx": "JSON", "http01": "JSON"}
            )
        if "agent_commands" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "agent_commands",
                {
                    "depends_on_command_id": (
                        "VARCHAR(36) REFERENCES agent_commands(id) ON DELETE CASCADE"
                    )
                },
            )
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_agent_commands_depends_on_command_id "
                        "ON agent_commands (depends_on_command_id)"
                    )
                )
        if "product_users" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "product_users",
                {
                    "last_traffic_reset_at": "DATETIME",
                    "remark": "TEXT NOT NULL DEFAULT ''",
                    "removal_id": "VARCHAR(36) REFERENCES product_user_removals(id)",
                    "traffic_limit_override_bytes": "BIGINT",
                    "speed_limit_override_mbps": "FLOAT",
                    "device_limit_override": "INTEGER",
                    "node_speed_limit_overrides": "JSON NOT NULL DEFAULT '{}'",
                    "node_device_limit_overrides": "JSON NOT NULL DEFAULT '{}'",
                },
            )
        if "managed_nodes" in table_names:
            self._sqlite_add_missing_columns(
                inspector,
                "managed_nodes",
                {
                    "parent_id": "VARCHAR(36) REFERENCES managed_nodes(id) ON DELETE SET NULL",
                    "target_node_id": "VARCHAR(36) REFERENCES managed_nodes(id) ON DELETE SET NULL",
                    "removal_id": "VARCHAR(36) REFERENCES managed_node_removals(id)",
                },
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
                    "traffic_reset_day": "INTEGER NOT NULL DEFAULT 0",
                    "last_traffic_reset_at": "DATETIME",
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
                traffic_reset_day=payload.traffic_reset_day,
                last_traffic_reset_at=now,
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

    def authenticate_agent(self, token: str) -> ServerRead:
        with self._session() as session:
            return self._public_server(self._server_by_token(session, token))

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
            agent.capability_native_limiter = payload.capabilities.native_limiter
            agent.capability_user_auto_speed_rules = payload.capabilities.user_auto_speed_rules
            agent.capability_subscription_access = payload.capabilities.subscription_access
            agent.capability_node_cleanup = payload.capabilities.node_cleanup
            agent.warp_installed = payload.warp_installed
            agent.same_host_as_master = payload.same_host_as_master
            agent.last_seen_at = now

            self._queue_xray_snapshot_sync_on_agent_register(session, server, now)

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
                if payload.warp_installed is not None:
                    agent.warp_installed = payload.warp_installed
                agent.public_ipv4 = payload.public_ipv4 or agent.public_ipv4
                agent.public_ipv6 = payload.public_ipv6 or agent.public_ipv6

            session.commit()
            session.refresh(server)
            return self._public_server(server)

    def record_telemetry(
        self,
        payload: AgentTelemetryReport,
    ) -> tuple[ServerRead, AgentTelemetryRead]:
        with self._coordinated_session() as session:
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
            self._server_traffic().record(session, server, telemetry)
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

    def xray_runtime_inventory(self, server_id: UUID) -> XrayRuntimeInventoryResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            latest_telemetry = self._latest_telemetry_model(session, str(server_id))
            return self._xray_runtime_inventory_response(server_id, scan, latest_telemetry)

    def xray_runtime_tunnel_inventory(
        self,
        server_id: UUID,
    ) -> XrayRuntimeTunnelInventoryResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            snapshot = self._current_xray_config_snapshot(session, server.id)
            return self._xray_runtime_tunnel_inventory_response(server_id, snapshot)

    def delete_xray_runtime_tunnel(
        self,
        server_id: UUID,
        payload: XrayRuntimeTunnelDeleteRequest,
    ) -> XrayRuntimeTunnelDeleteResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            inventory = self._xray_runtime_tunnel_inventory_response(
                server_id,
                self._current_xray_config_snapshot(session, server.id),
            )
            if not inventory.has_config:
                return XrayRuntimeTunnelDeleteResponse(
                    server_id=server_id,
                    target_kind=payload.kind,
                    target_tag=payload.tag,
                    target_label=payload.label,
                    warnings=["current_config_snapshot_not_found"],
                )
            previews = self._xray_runtime_tunnel_delete_commands(inventory, payload)
            return XrayRuntimeTunnelDeleteResponse(
                server_id=server_id,
                has_config=True,
                source_snapshot_id=inventory.source_snapshot_id,
                target_kind=payload.kind,
                target_tag=payload.tag,
                target_label=payload.label,
                command_previews=previews,
                command_count=len(previews),
                warnings=inventory.warnings,
            )

    def create_xray_runtime_tunnel_chain(
        self,
        payload: XrayRuntimeTunnelChainCreateRequest,
    ) -> XrayRuntimeTunnelChainCreateResponse:
        with self._session() as session:
            servers: list[ServerModel] = []
            hosts: list[str] = []
            used_ports: list[set[int]] = []
            warnings: list[str] = []

            for server_id in payload.server_ids:
                server = session.get(ServerModel, str(server_id))
                if not server:
                    raise ServerNotFoundError(f"server not found: {server_id}")
                host = self._server_entry_host(server)
                if not host:
                    raise XrayRuntimeTunnelChainUnavailableError(
                        f"server has no reachable address: {server.name}"
                    )
                snapshot = self._current_xray_config_snapshot(session, server.id)
                ports, port_warnings = self._xray_config_snapshot_used_inbound_ports(snapshot)
                warnings.extend(f"{server.name}:{warning}" for warning in port_warnings)
                servers.append(server)
                hosts.append(host)
                used_ports.append(ports)

            ports: list[int] = []
            wanted_entry = payload.entry_port or self._next_free_xray_tunnel_port(used_ports[0])
            entry_port = self._pick_xray_tunnel_port(used_ports[0], wanted_entry)
            if payload.entry_port and entry_port != payload.entry_port:
                warnings.append(f"{servers[0].name}:port_{payload.entry_port}_in_use")
            ports.append(entry_port)
            used_ports[0].add(entry_port)

            for index in range(1, len(servers)):
                wanted = ports[index - 1]
                if index == len(servers) - 1:
                    wanted = ports[0]
                port = self._pick_xray_tunnel_port(used_ports[index], wanted)
                if port != wanted:
                    warnings.append(f"{servers[index].name}:port_{wanted}_in_use")
                ports.append(port)
                used_ports[index].add(port)

            hops: list[XrayRuntimeTunnelChainHopRead] = []
            previews: list[XrayRuntimeTunnelChainCreateCommand] = []
            for index, server in enumerate(servers):
                target_address = (
                    hosts[index + 1] if index < len(servers) - 1 else payload.target_address
                )
                target_port = ports[index + 1] if index < len(servers) - 1 else payload.target_port
                tag = f"tunnel-{payload.label}-h{index}"
                inbound = {
                    "tag": tag,
                    "protocol": "tunnel",
                    "port": ports[index],
                    "settings": {
                        "address": target_address,
                        "port": target_port,
                        "network": "tcp,udp",
                    },
                }
                hop = XrayRuntimeTunnelChainHopRead(
                    server_id=UUID(server.id),
                    server_name=server.name,
                    tag=tag,
                    listen_port=ports[index],
                    target_address=target_address,
                    target_port=target_port,
                )
                hops.append(hop)
                previews.append(
                    XrayRuntimeTunnelChainCreateCommand(
                        server_id=UUID(server.id),
                        server_name=server.name,
                        hop_index=index,
                        body={"action": "add", "inbound": inbound},
                    )
                )

            return XrayRuntimeTunnelChainCreateResponse(
                label=payload.label,
                entry_server_id=payload.server_ids[0],
                entry_host=hosts[0],
                entry_port=ports[0],
                final_target=self._format_host_port(payload.target_address, payload.target_port)
                or payload.target_address,
                hops=hops,
                command_previews=previews,
                command_count=len(previews),
                warnings=self._dedupe_text(warnings),
            )

    def plan_xray_runtime_tunnel_deploy(
        self,
        server_id: UUID,
        payload: XrayRuntimeTunnelDeployRequest,
    ) -> XrayRuntimeTunnelDeployResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")

            domain = payload.domain or self._normalize_runtime_domain(server.domain or "")
            if not domain:
                raise XrayRuntimeTunnelDeployUnavailableError(
                    "server domain is required for tunnel deployment"
                )

            proxy_domain = (
                payload.proxy_domain
                if payload.proxy_domain is not None
                else self._normalize_runtime_domain(server.pull_address or "")
            )
            if proxy_domain == domain:
                proxy_domain = None
            cert_name = self._cert_deploy_filename(payload.cert_name or domain)
            warnings: list[str] = []
            scan = session.get(AgentScanResultModel, server.id)
            nginx = scan.nginx if scan else None
            managed = bool(nginx and nginx.get("mode") == "managed")
            if managed and (nginx.get("tunnel_deploy") != 1 or not nginx.get("available")):
                if payload.queue_agent_commands:
                    raise XrayRuntimeTunnelDeployUnavailableError(
                        "upgrade/configure the Open Node Agent for atomic tunnel deployment"
                    )
                warnings.append("agent_tunnel_deploy_unavailable")
            ports = (
                payload.listen_port,
                payload.nginx_port,
                payload.forward_port,
                payload.api_port,
                payload.metrics_port,
            )
            if not managed and (
                ports != (443, 8001, 46174, 46736, 38889) or payload.listen_address != "0.0.0.0"
            ):
                raise XrayRuntimeTunnelDeployUnavailableError(
                    "custom tunnel listeners require an Open Node Agent scan"
                )

            snapshot = self._current_xray_config_snapshot(session, server.id)
            if snapshot is None:
                warnings.append("current_config_snapshot_not_found")
                if managed and payload.queue_agent_commands:
                    raise XrayRuntimeTunnelDeployUnavailableError(
                        "read the current Xray configuration before tunnel deployment"
                    )
            elif self._xray_config_snapshot_has_user_content(snapshot):
                warnings.append("current_config_has_user_content")
                if payload.queue_agent_commands and not payload.force:
                    raise XrayRuntimeTunnelDeployUnavailableError(
                        "current Xray config has user content; "
                        "set force=true to queue tunnel deploy"
                    )

            nginx_config = _TUNNEL_NGINX_CONFIG
            domain_config = self._render_tunnel_domain_config(
                payload.site_type,
                payload.site_value or "/usr/local/nginx/html",
                domain,
                cert_name,
            )
            xray_config = self._render_tunnel_xray_config(domain)
            http_nodes = []
            if managed:
                nginx_config, domain_config, http_nodes, xray_config = managed_tunnel_bundle(
                    payload,
                    domain,
                    cert_name,
                    nginx,
                    xray_config,
                )
            xray_config_text = json.dumps(xray_config, indent=4, ensure_ascii=False)

            previews: list[XrayRuntimeTunnelDeployCommand] = []
            if payload.clear_stream_port:
                previews.append(
                    XrayRuntimeTunnelDeployCommand(
                        step="clear_stream_443",
                        path="/api/child/nginx/clear-stream-port",
                        body={"port": 443},
                    )
                )
            previews.extend(
                [
                    XrayRuntimeTunnelDeployCommand(
                        step="setup_tunnel_nginx",
                        path="/api/child/nginx/setup-ssl",
                        body={
                            "domain": domain,
                            "nginx_config": nginx_config,
                            "domain_config": domain_config,
                        },
                    ),
                    XrayRuntimeTunnelDeployCommand(
                        step="write_tunnel_xray_config",
                        path="/api/child/xray/config",
                        body={"config": xray_config_text},
                    ),
                ]
            )
            if payload.restart_xray:
                previews.append(
                    XrayRuntimeTunnelDeployCommand(
                        step="restart_xray",
                        path="/api/child/services/control",
                        body={"service": "xray", "action": "restart"},
                    )
                )

            if managed:
                expected = None
                if snapshot:
                    expected = hashlib.sha256(
                        json.dumps(
                            json.loads(snapshot.config),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                previews = [
                    XrayRuntimeTunnelDeployCommand(
                        step="deploy_owned_tunnel",
                        path="/api/child/tunnel/deploy",
                        body={
                            "domain": domain,
                            "cert_name": cert_name,
                            "nginx_http": http_nodes,
                            "domain_config": domain_config,
                            "xray_config": xray_config,
                            "expected_xray_sha256": expected,
                            "clear_stream_port": payload.clear_stream_port,
                            "listen_port": payload.listen_port,
                            "restart_xray": payload.restart_xray,
                        },
                    )
                ]

            scan_preview = (
                XrayRuntimeTunnelDeployCommand(
                    step="scan_runtime",
                    path="/api/child/scan",
                )
                if payload.queue_scan_after_apply
                else None
            )

            return XrayRuntimeTunnelDeployResponse(
                runtime_profile="open-node" if managed else "legacy",
                server_id=UUID(server.id),
                server_name=server.name,
                domain=domain,
                proxy_domain=proxy_domain,
                cert_name=cert_name,
                nginx_config=nginx_config,
                domain_config=domain_config,
                xray_config=xray_config_text,
                command_previews=previews,
                scan_command_preview=scan_preview,
                command_count=len(previews),
                warnings=self._dedupe_text(warnings),
            )

    def list_xray_runtime_node_drafts(
        self,
        server_id: UUID,
        host: str | None = None,
    ) -> XrayRuntimeNodeDraftsResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            if scan is None:
                return XrayRuntimeNodeDraftsResponse(server_id=server_id)
            drafts = [
                self._xray_runtime_node_draft(
                    session=session,
                    server=server,
                    inbound=inbound,
                    index=index,
                    host=host,
                )
                for index, inbound in enumerate(scan.inbounds or [])
            ]
            return XrayRuntimeNodeDraftsResponse(
                server_id=server_id,
                has_scan=True,
                drafts=drafts,
            )

    def create_managed_node_from_xray_runtime(
        self,
        server_id: UUID,
        payload: XrayRuntimeNodeCreateRequest,
    ) -> ManagedNodeRead:
        now = datetime.now(tz=UTC)
        with self._coordinated_session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            if scan is None:
                raise XrayRuntimeInboundNotFoundError(f"runtime scan not found: {server_id}")
            inbound, index = self._select_xray_runtime_inbound(scan.inbounds or [], payload)
            draft = self._xray_runtime_node_draft(
                session=session,
                server=server,
                inbound=inbound,
                index=index,
                host=payload.host,
                payload=payload,
            )
            if not draft.create_available:
                warnings = ", ".join(draft.warnings) or "draft unavailable"
                raise XrayRuntimeNodeDraftUnavailableError(warnings)
            existing = (
                session.get(ManagedNodeModel, str(draft.existing_node_id))
                if draft.existing_node_id
                else None
            )
            if existing:
                return self._managed_node_read(existing)
            node = self._new_managed_node_model(server, draft.draft, now)
            self._node_management().validate_node(session, node)
            session.add(node)
            session.commit()
            session.refresh(node)
            return self._managed_node_read(node)

    def import_managed_nodes_from_xray_runtime(
        self,
        server_id: UUID,
        payload: XrayRuntimeNodeImportRequest,
    ) -> XrayRuntimeNodeImportResponse:
        now = datetime.now(tz=UTC)
        created_nodes: list[ManagedNodeRead] = []
        existing_nodes: list[ManagedNodeRead] = []
        skipped: list[XrayRuntimeNodeImportSkipped] = []

        with self._coordinated_session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            if scan is None:
                return XrayRuntimeNodeImportResponse(server_id=server_id)

            inbounds = scan.inbounds or []
            indexes = (
                payload.source_indexes
                if payload.source_indexes is not None
                else range(len(inbounds))
            )
            for index in indexes:
                if index >= len(inbounds):
                    skipped.append(
                        XrayRuntimeNodeImportSkipped(
                            source_index=index,
                            source_display_name=f"inbound-{index + 1}",
                            warnings=["not_found"],
                        )
                    )
                    continue
                draft = self._xray_runtime_node_draft(
                    session=session,
                    server=server,
                    inbound=inbounds[index],
                    index=index,
                    host=payload.host,
                    extra_tags=payload.extra_tags,
                    payload=XrayRuntimeNodeCreateRequest(
                        source_index=index,
                        host=payload.host,
                        enabled=payload.enabled,
                    ),
                )
                if draft.existing_node_id:
                    existing = session.get(ManagedNodeModel, str(draft.existing_node_id))
                    if existing:
                        existing_nodes.append(self._managed_node_read(existing))
                    continue
                if not draft.create_available:
                    skipped.append(
                        XrayRuntimeNodeImportSkipped(
                            source_index=draft.source_index,
                            source_tag=draft.source_tag,
                            source_display_name=draft.source_display_name,
                            warnings=draft.warnings,
                        )
                    )
                    continue
                node = self._new_managed_node_model(server, draft.draft, now)
                self._node_management().validate_node(session, node)
                session.add(node)
                session.flush()
                created_nodes.append(self._managed_node_read(node))

            session.commit()

        return XrayRuntimeNodeImportResponse(
            server_id=server_id,
            has_scan=True,
            created_nodes=created_nodes,
            existing_nodes=existing_nodes,
            skipped=skipped,
            created_count=len(created_nodes),
            existing_count=len(existing_nodes),
            skipped_count=len(skipped),
        )

    def xray_runtime_node_reconciliation(
        self,
        server_id: UUID,
    ) -> XrayRuntimeNodeReconciliationResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            managed_nodes = session.scalars(
                select(ManagedNodeModel)
                .where(ManagedNodeModel.server_id == server.id)
                .order_by(ManagedNodeModel.created_at)
            ).all()
            drafts = (
                [
                    self._xray_runtime_node_draft(
                        session=session,
                        server=server,
                        inbound=inbound,
                        index=index,
                    )
                    for index, inbound in enumerate(scan.inbounds or [])
                ]
                if scan
                else []
            )
            nodes_by_id = {node.id: node for node in managed_nodes}
            runtime_entries = [
                self._runtime_reconciliation_entry(draft, nodes_by_id) for draft in drafts
            ]
            drafts_by_node_id = {
                str(draft.existing_node_id): draft for draft in drafts if draft.existing_node_id
            }
            managed_entries = [
                self._managed_node_reconciliation_entry(node, drafts_by_node_id.get(node.id))
                for node in managed_nodes
            ]

        return XrayRuntimeNodeReconciliationResponse(
            server_id=server_id,
            has_scan=scan is not None,
            runtime_count=len(runtime_entries),
            managed_node_count=len(managed_entries),
            managed_runtime_count=sum(1 for entry in runtime_entries if entry.status == "managed"),
            unmanaged_runtime_count=sum(
                1 for entry in runtime_entries if entry.status == "unmanaged"
            ),
            unavailable_runtime_count=sum(
                1 for entry in runtime_entries if entry.status == "unavailable"
            ),
            in_sync_count=sum(1 for entry in managed_entries if entry.status == "in_sync"),
            stale_count=sum(1 for entry in managed_entries if entry.status == "stale"),
            missing_runtime_count=sum(
                1 for entry in managed_entries if entry.status == "missing_runtime"
            ),
            catalog_only_count=sum(
                1 for entry in managed_entries if entry.status == "catalog_only"
            ),
            runtime_entries=runtime_entries,
            managed_entries=managed_entries,
        )

    def sync_managed_node_from_xray_runtime(
        self,
        server_id: UUID,
        node_id: UUID,
        payload: XrayRuntimeNodeSyncRequest,
    ) -> XrayRuntimeNodeSyncResponse:
        now = datetime.now(tz=UTC)
        with self._coordinated_session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            node = session.get(ManagedNodeModel, str(node_id))
            if not node or node.server_id != server.id:
                raise ManagedNodeNotFoundError(f"managed node not found: {node_id}")
            self._node_management().require_editable(session, node)
            if node.node_type != ManagedNodeType.PHYSICAL.value:
                raise XrayRuntimeNodeDraftUnavailableError(
                    "runtime sync is only available for physical managed nodes"
                )
            scan = session.get(AgentScanResultModel, str(server_id))
            if scan is None:
                raise XrayRuntimeInboundNotFoundError(f"runtime scan not found: {server_id}")

            draft = self._runtime_node_sync_draft(session, server, node, scan, payload)
            if not draft.create_available:
                warnings = ", ".join(draft.warnings) or "runtime node draft unavailable"
                raise XrayRuntimeNodeDraftUnavailableError(warnings)
            if draft.existing_node_id and str(draft.existing_node_id) != node.id:
                raise XrayRuntimeNodeDraftUnavailableError(
                    f"runtime inbound already maps to managed node: {draft.existing_node_id}"
                )
            drifts_before = self._runtime_managed_node_drifts(draft.draft, node)
            updated_fields = self._sync_managed_node_public_runtime_fields(node, draft.draft)
            if updated_fields:
                node.updated_at = now
            session.commit()
            session.refresh(node)
            drifts_after = self._runtime_managed_node_drifts(draft.draft, node)
            return XrayRuntimeNodeSyncResponse(
                server_id=server_id,
                node=self._managed_node_read(node),
                source_index=draft.source_index,
                source_tag=draft.source_tag,
                source_display_name=draft.source_display_name,
                updated_fields=updated_fields,
                drifts_before=drifts_before,
                drifts_after=drifts_after,
            )

    def xray_runtime_credential_reconciliation(
        self,
        server_id: UUID,
    ) -> XrayRuntimeCredentialReconciliationResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            managed_nodes = session.scalars(
                select(ManagedNodeModel)
                .where(
                    ManagedNodeModel.server_id == server.id,
                    ManagedNodeModel.node_type == ManagedNodeType.PHYSICAL.value,
                )
                .order_by(ManagedNodeModel.created_at)
            ).all()
            runtime_by_node_id = self._runtime_inbounds_by_managed_node_id(
                session,
                server,
                scan,
            )
            expected_by_node_id = self._expected_runtime_credentials_by_node_id(
                session,
                server,
                managed_nodes,
            )

            entries = [
                self._runtime_credential_reconciliation_entry(
                    node=node,
                    runtime=runtime_by_node_id.get(node.id),
                    expected_emails=[
                        context.email for context in expected_by_node_id.get(node.id, [])
                    ],
                )
                for node in managed_nodes
            ]

        return XrayRuntimeCredentialReconciliationResponse(
            server_id=server_id,
            has_scan=scan is not None,
            node_count=len(entries),
            expected_credential_count=sum(len(entry.expected_emails) for entry in entries),
            matched_runtime_client_count=sum(len(entry.runtime_emails) for entry in entries),
            in_sync_count=sum(1 for entry in entries if entry.status == "in_sync"),
            missing_runtime_count=sum(1 for entry in entries if entry.status == "missing_runtime"),
            out_of_sync_count=sum(1 for entry in entries if entry.status != "in_sync"),
            missing_runtime_client_count=sum(
                len(entry.missing_runtime_emails) for entry in entries
            ),
            extra_runtime_client_count=sum(len(entry.extra_runtime_emails) for entry in entries),
            entries=entries,
        )

    def repair_missing_xray_runtime_credentials(
        self,
        server_id: UUID,
        payload: XrayRuntimeCredentialRepairRequest,
    ) -> XrayRuntimeCredentialRepairResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            if not scan:
                return XrayRuntimeCredentialRepairResponse(
                    server_id=server_id,
                    has_scan=False,
                    warnings=["runtime scan not found"],
                )

            managed_nodes = self._selected_physical_managed_nodes(session, server, payload.node_ids)
            runtime_by_node_id = self._runtime_inbounds_by_managed_node_id(
                session,
                server,
                scan,
            )
            expected_by_node_id = self._expected_runtime_credentials_by_node_id(
                session,
                server,
                managed_nodes,
            )
            body: dict[str, Any] = {
                "inbound_clients": [],
                "routing_user_additions": [],
                "no_restart": payload.no_restart,
            }
            entries: list[XrayRuntimeCredentialRepairEntry] = []
            warnings: list[str] = []

            for node in managed_nodes:
                if not node.enabled:
                    warnings.append(f"node {node.name} is disabled")
                    continue
                if not node.inbound_tag:
                    warnings.append(f"node {node.name} has no inbound tag")
                    continue
                runtime = runtime_by_node_id.get(node.id)
                if not runtime:
                    warnings.append(f"node {node.name} has no matching runtime inbound")
                    continue
                runtime_email_keys = {email.lower() for email in runtime.user_emails}
                missing_contexts = [
                    context
                    for context in expected_by_node_id.get(node.id, [])
                    if context.email.lower() not in runtime_email_keys
                ]
                if not missing_contexts:
                    continue

                emails: list[str] = []
                for context in missing_contexts:
                    credential = context.credential or self._get_or_create_subscription_credential(
                        session,
                        context.user,
                        node,
                        server,
                    )
                    client = self._provisioning_client_from_credential(
                        context.user,
                        context.plan,
                        node,
                        server,
                        credential,
                    )
                    body["inbound_clients"].append({"tag": node.inbound_tag, "client": client})
                    emails.append(credential.email)

                entries.append(
                    XrayRuntimeCredentialRepairEntry(
                        node_id=UUID(node.id),
                        node_name=node.name,
                        protocol=node.protocol,
                        inbound_tag=node.inbound_tag,
                        runtime_source_index=runtime.source_index,
                        runtime_display_name=runtime.display_name,
                        emails=emails,
                    )
                )

            provisioning_batches: list[SubscriptionProvisionBatch] = []
            if body["inbound_clients"]:
                provisioning_batches.append(
                    SubscriptionProvisionBatch(
                        server_id=UUID(server.id),
                        server_name=server.name,
                        body=body,
                    )
                )
            session.commit()

        return XrayRuntimeCredentialRepairResponse(
            server_id=server_id,
            has_scan=True,
            entries=entries,
            provisioning_batches=provisioning_batches,
            planned_client_count=sum(len(entry.emails) for entry in entries),
            batch_count=len(provisioning_batches),
            warnings=warnings,
        )

    def cleanup_extra_xray_runtime_credentials(
        self,
        server_id: UUID,
        payload: XrayRuntimeCredentialCleanupRequest,
    ) -> XrayRuntimeCredentialCleanupResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            scan = session.get(AgentScanResultModel, str(server_id))
            if not scan:
                return XrayRuntimeCredentialCleanupResponse(
                    server_id=server_id,
                    has_scan=False,
                    warnings=["runtime scan not found"],
                )

            managed_nodes = self._selected_physical_managed_nodes(session, server, payload.node_ids)
            runtime_by_node_id = self._runtime_inbounds_by_managed_node_id(
                session,
                server,
                scan,
            )
            expected_by_node_id = self._expected_runtime_credentials_by_node_id(
                session,
                server,
                managed_nodes,
            )
            entries: list[XrayRuntimeCredentialCleanupEntry] = []
            command_previews: list[XrayRuntimeCredentialCleanupCommand] = []
            warnings: list[str] = []

            for node in managed_nodes:
                if not node.inbound_tag:
                    warnings.append(f"node {node.name} has no inbound tag")
                    continue
                runtime = runtime_by_node_id.get(node.id)
                if not runtime:
                    warnings.append(f"node {node.name} has no matching runtime inbound")
                    continue
                expected_keys = {
                    context.email.lower() for context in expected_by_node_id.get(node.id, [])
                }
                extra_emails = [
                    email for email in runtime.user_emails if email.lower() not in expected_keys
                ]
                if not extra_emails:
                    continue
                entries.append(
                    XrayRuntimeCredentialCleanupEntry(
                        node_id=UUID(node.id),
                        node_name=node.name,
                        protocol=node.protocol,
                        inbound_tag=node.inbound_tag,
                        runtime_source_index=runtime.source_index,
                        runtime_display_name=runtime.display_name,
                        emails=extra_emails,
                    )
                )
                for email in extra_emails:
                    command_previews.append(
                        XrayRuntimeCredentialCleanupCommand(
                            node_id=UUID(node.id),
                            node_name=node.name,
                            body={
                                "action": "remove-client",
                                "tag": node.inbound_tag,
                                "client": {"email": email},
                            },
                        )
                    )

        return XrayRuntimeCredentialCleanupResponse(
            server_id=server_id,
            has_scan=True,
            entries=entries,
            command_previews=command_previews,
            planned_client_count=sum(len(entry.emails) for entry in entries),
            command_count=len(command_previews),
            warnings=warnings,
        )

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

    def get_xray_config_snapshot_recovery_status(
        self,
        server_id: UUID,
        include_config: bool = False,
    ) -> XrayConfigSnapshotRecoveryStatusResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            current = self._current_xray_config_snapshot(session, server.id)
            pending = self._pending_xray_config_snapshot(session, server.id)
            return XrayConfigSnapshotRecoveryStatusResponse(
                server_id=server_id,
                has_pending=pending is not None,
                has_current=current is not None,
                pending=self._xray_config_snapshot_read(pending, include_config=include_config)
                if pending
                else None,
                current=self._xray_config_snapshot_read(current, include_config=include_config)
                if current
                else None,
            )

    def accept_xray_config_pending_recovery(
        self,
        server_id: UUID,
    ) -> XrayConfigSnapshotRecoveryAcceptResponse:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            pending = self._pending_xray_config_snapshot(session, server.id)
            if pending is None:
                raise XrayConfigSnapshotRecoveryUnavailableError(
                    f"no pending Xray config recovery for server: {server_id}"
                )

            current_snapshots = session.scalars(
                select(XrayConfigSnapshotModel)
                .where(
                    XrayConfigSnapshotModel.server_id == server.id,
                    XrayConfigSnapshotModel.status == XrayConfigSnapshotStatus.CURRENT.value,
                )
                .order_by(XrayConfigSnapshotModel.created_at.desc())
            ).all()
            for snapshot in current_snapshots:
                snapshot.status = XrayConfigSnapshotStatus.OLD.value
            pending.status = XrayConfigSnapshotStatus.CURRENT.value
            pending.source = XrayConfigSnapshotSource.MANUAL_ACCEPT.value

            session.commit()
            session.refresh(pending)
            snapshots = session.scalars(
                select(XrayConfigSnapshotModel)
                .where(XrayConfigSnapshotModel.server_id == server.id)
                .order_by(XrayConfigSnapshotModel.created_at.desc())
                .limit(20)
            ).all()
            return XrayConfigSnapshotRecoveryAcceptResponse(
                server_id=server_id,
                current=self._xray_config_snapshot_read(pending),
                snapshots=[self._xray_config_snapshot_read(snapshot) for snapshot in snapshots],
            )

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
                traffic = self._server_traffic()
                daily_traffic = traffic.daily(session, server, day_count=7)
                probe_servers.append(
                    self._probe_server(
                        server,
                        latest,
                        ping,
                        daily_traffic,
                        return_routes.get(server.id),
                        traffic.read_in_session(session, server),
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
        with self._coordinated_session() as session:
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
                self._probe_target_comparison(key, rows) for key, rows in sorted(by_target.items())
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
                remark=payload.remark,
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
        with self._coordinated_session() as session:
            server = session.get(ServerModel, str(payload.server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {payload.server_id}")
            node = self._new_managed_node_model(server, payload, now)
            self._node_management().validate_node(session, node)
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
        from open_node.services.user_limits import catalog_overrides

        with self._session() as session:
            from open_node.services.subscription_templates import TemplateRecord

            template_names = dict(
                session.execute(select(TemplateRecord.id, TemplateRecord.name)).all()
            )
            plans = session.scalars(
                select(SubscriptionPlanModel).order_by(SubscriptionPlanModel.created_at)
            ).all()
            plan_names = {plan.id: plan.name for plan in plans}
            nodes = session.scalars(
                select(ManagedNodeModel).order_by(ManagedNodeModel.created_at)
            ).all()
            private_node_ids = set(
                session.scalars(select(PrivateRoutedNodeModel.node_id)).all()
            )
            nodes = [
                node
                for node in nodes
                if not node.removal_id and node.id not in private_node_ids
            ]
            node_names = {node.id: node.name for node in nodes}
            servers = session.scalars(select(ServerModel)).all()
            server_names = {server.id: server.name for server in servers}
            users = session.scalars(
                select(ProductUserModel)
                .where(ProductUserModel.removal_id.is_(None))
                .order_by(ProductUserModel.created_at)
            ).all()

            credentials: list[SubscriptionCatalogCredentialEntry] = []
            if include_credentials:
                credential_rows = session.scalars(
                    select(SubscriptionCredentialModel)
                    .where(
                        SubscriptionCredentialModel.username.in_([user.username for user in users]),
                        SubscriptionCredentialModel.node_id.in_(node_names),
                    )
                    .order_by(
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
                        remark=user.remark,
                        limit_overrides=catalog_overrides(user, node_names),
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
                        parent_name=node_names.get(node.parent_id),
                        target_node_name=node_names.get(node.target_node_id),
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
                    self._subscription_catalog_plan_entry(plan, node_names, template_names)
                    for plan in plans
                ],
                credentials=credentials,
                **self.subscription_templates().export_catalog(session),
            )

    def import_subscription_catalog(
        self,
        payload: SubscriptionCatalogImportRequest,
    ) -> SubscriptionCatalogImportResponse:
        from collections import Counter

        from open_node.services.user_limits import apply_overrides, import_overrides

        now = datetime.now(tz=UTC)
        summary = SubscriptionCatalogImportSummary()
        with self._coordinated_session() as session:
            for user_entry in payload.catalog.users:
                existing = session.get(ProductUserModel, user_entry.username)
                if existing:
                    self._user_management().require_editable(existing)
                    existing.email = user_entry.email
                    existing.display_name = user_entry.display_name or user_entry.username
                    existing.remark = user_entry.remark
                    existing.role = user_entry.role.value
                    if not user_entry.is_active:
                        self._user_management().revoke_login(session, existing.username)
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
                        remark=user_entry.remark,
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
            self.subscription_templates().import_templates(session, payload.catalog.templates)
            for node_entry in payload.catalog.nodes:
                server = self._catalog_server(session, payload.server_map, node_entry.server_name)
                if not server:
                    summary.warnings.append(
                        f"node {node_entry.name} skipped; server {node_entry.server_name} not found"
                    )
                    continue
                node = self._catalog_node_by_name(session, node_entry.name, server.id)
                if node:
                    self._node_management().require_editable(session, node)
                    self._node_management().check_import_update(session, node, node_entry)
                    self._apply_catalog_node(node, node_entry, server.id, now)
                    summary.updated_nodes += 1
                else:
                    node = self._catalog_node_model(node_entry, server.id, now)
                    session.add(node)
                    summary.created_nodes += 1
                node_ids_by_name[node_entry.name] = node.id
                self._node_management().validate_node(session, node)

            session.flush()
            for entry in payload.catalog.nodes:
                if entry.name not in node_ids_by_name:
                    continue
                node = session.get(ManagedNodeModel, node_ids_by_name[entry.name])
                for field, source, name in (
                    ("parent_id", "parent_name", entry.parent_name),
                    ("target_node_id", "target_node_name", entry.target_node_name),
                ):
                    if name and name not in node_ids_by_name:
                        raise ManagedNodeConflict(f"Linked node not found in catalog: {name}")
                    if source in entry.model_fields_set:
                        setattr(node, field, node_ids_by_name.get(name))
                self._node_management().validate_node(session, node)
            for plan_entry in payload.catalog.plans:
                alias_names = set(plan_entry.node_name_overrides)
                if any(
                    count > 1 and name in alias_names
                    for name, count in Counter(
                        entry.name for entry in payload.catalog.nodes
                    ).items()
                ):
                    raise ManagedNodeConflict("Plan aliases require unambiguous catalog node names")
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
                    plan = self._catalog_plan_model(plan_entry, node_ids, node_ids_by_name, now)
                    session.add(plan)
                    summary.created_plans += 1
                for format in ("clash", "surge"):
                    field = format + "_template_name"
                    if field in plan_entry.model_fields_set:
                        setattr(
                            plan,
                            format + "_template_id",
                            self.subscription_templates().id_for(
                                session, getattr(plan_entry, field), format
                            ),
                        )

            session.flush()
            self.subscription_templates().import_preferences(
                session, payload.catalog.template_defaults, payload.catalog.template_preferences
            )
            plan_ids_by_name = dict(
                session.execute(select(SubscriptionPlanModel.name, SubscriptionPlanModel.id)).all()
            )
            ambiguous = {
                name
                for name, count in Counter(entry.name for entry in payload.catalog.nodes).items()
                if count > 1
            }
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
                if user_entry.limit_overrides is not None:
                    values = import_overrides(
                        user_entry.limit_overrides, node_ids_by_name, ambiguous
                    )
                    self._ensure_managed_nodes_exist(
                        session,
                        list(set(values.node_speed_limits) | set(values.node_device_limits)),
                    )
                    apply_overrides(user, values)

            if payload.import_credentials:
                summary.imported_credentials = self._import_subscription_credentials(
                    session,
                    payload.catalog.credentials,
                    payload.server_map,
                    node_ids_by_name,
                    now,
                    summary.warnings,
                )

            for entry in payload.catalog.users:
                if entry.limit_overrides is None:
                    continue
                user = session.get(ProductUserModel, entry.username)
                plan = (
                    session.get(SubscriptionPlanModel, user.current_plan_id)
                    if user.current_plan_id
                    else None
                )
                if self._subscription_quota_status(session, user, plan, now).over_quota:
                    self._plan_management()._track_revocations(session, user, plan, now)
                self._subscription_access().reconcile(session, now, username=user.username)

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
        with self._coordinated_session() as session:
            existing = session.scalar(
                select(SubscriptionPlanModel).where(SubscriptionPlanModel.name == payload.name)
            )
            if existing:
                raise DuplicateSubscriptionPlanNameError(
                    f"subscription plan name already exists: {payload.name}"
                )
            self._ensure_plan_nodes_assignable(session, payload.node_ids)
            plan = SubscriptionPlanModel(
                **self.subscription_templates().validate_selection(session, payload),
                id=str(uuid4()),
                name=payload.name,
                description=payload.description,
                traffic_limit_bytes=int(payload.traffic_limit_gb * 1024 * 1024 * 1024),
                cycle_days=payload.cycle_days,
                is_reset=payload.is_reset,
                reset_day=payload.reset_day,
                node_ids=[str(node_id) for node_id in payload.node_ids],
                node_multipliers=self._uuid_keyed_float_map(payload.node_multipliers),
                node_name_overrides={
                    str(key): value for key, value in payload.node_name_overrides.items()
                },
                node_name_override_enabled=payload.node_name_override_enabled,
                auto_speed_rules=[rule.model_dump() for rule in payload.auto_speed_rules],
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
        *,
        queued_commands: list[AgentCommandRead] | None = None,
    ) -> tuple[ProductUserRead, SubscriptionPlanRead, list[SubscriptionProvisionBatch], list[str]]:
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        now = datetime.now(tz=UTC)
        with self._coordinated_session() as session:
            user = session.get(ProductUserModel, username)
            if not user:
                raise ProductUserNotFoundError(f"user not found: {username}")
            plan = session.get(SubscriptionPlanModel, str(payload.plan_id))
            self._user_management().require_editable(user)
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
            commands = []
            if payload.queue_agent_commands:
                access = self._subscription_access()
                access.authorize(session, user, plan, batches, now)
                commands = access.reconcile(
                    session, now, username=username, timeout_ms=payload.command_timeout_ms
                )
            session.commit()
            if queued_commands is not None:
                queued_commands.extend(self._command_read(command) for command in commands)
            session.refresh(user)
            session.refresh(plan)
            return (
                self._product_user_read(user),
                self._subscription_plan_read(plan),
                batches,
                warnings,
            )

    def get_or_create_subscription_token(self, username: str) -> SubscriptionTokenRecord:
        with self._coordinated_session() as session:
            token = self._issue_subscription_token(session, username)
            session.commit()
            return token

    def reset_subscription_token(self, username: str) -> SubscriptionTokenRecord:
        with self._coordinated_session() as session:
            token = self._issue_subscription_token(session, username, reset=True)
            session.commit()
            return token

    def set_subscription_short_code(
        self, username: str, payload: SubscriptionShortCodeUpdate
    ) -> SubscriptionTokenRecord:
        with self._coordinated_session() as session:
            token = self._set_subscription_short_code(session, username, payload)
            session.commit()
            return token

    def _set_subscription_short_code(self, session, username, payload):
        current = self._issue_subscription_token(session, username)
        if current.revision != payload.expected_revision:
            raise ProductUserConflict("Subscription links changed; reload before saving")
        code = payload.custom_short_code or None
        if code:
            other_user = session.scalar(
                select(ProductUserModel.username).where(
                    func.lower(ProductUserModel.username) == code.lower(),
                    ProductUserModel.username != current.username,
                )
            )
            if other_user or self._subscription_key_in_use(
                session, code, except_user=current.username
            ):
                raise ProductUserConflict("This short code is unavailable")
        token = session.get(ProductUserSubscriptionTokenModel, current.username)
        if token.custom_short_code != code:
            token.custom_short_code = code
            token.updated_at = datetime.now(UTC)
            try:
                session.flush()
            except IntegrityError as exc:
                raise ProductUserConflict("This short code is unavailable") from exc
        session.refresh(token)
        return self._subscription_token_record(token)

    def _issue_subscription_token(self, session, username, *, reset=False):
        username = username.strip()
        if not username:
            raise ProductUserNotFoundError("username is required")
        now = datetime.now(tz=UTC)
        user = session.get(ProductUserModel, username)
        if not user:
            raise ProductUserNotFoundError(f"user not found: {username}")
        self._user_management().require_editable(user)
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
        elif reset:
            token.token = self._unique_subscription_token(session)
            token.short_code = self._unique_subscription_short_code(session)
            token.custom_short_code = None
            token.updated_at = now
        session.flush()
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
        node_id: UUID | None = None,
    ) -> RenderedSubscription:
        key = subscription_key.strip()
        if not key:
            raise SubscriptionTokenNotFoundError("subscription key is required")

        with self._session() as session:
            token = session.scalar(
                select(ProductUserSubscriptionTokenModel).where(
                    (ProductUserSubscriptionTokenModel.token == key)
                    | (ProductUserSubscriptionTokenModel.short_code == key)
                    | (ProductUserSubscriptionTokenModel.custom_short_code == key)
                )
            )
            if not token:
                raise SubscriptionTokenNotFoundError("subscription not found")
            user = session.get(ProductUserModel, token.username)
            plan = self._available_subscription_plan(session, user)
            return self._render_user_subscription(
                session,
                user,
                plan,
                client_format,
                node_id=node_id,
            )

    def _render_user_subscription(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        client_format: SubscriptionClientFormat,
        *,
        node_id: UUID | None = None,
        selected_node_ids: set[str] | None = None,
        template_override=None,
        title: str | None = None,
        extra_warnings: list[str] | None = None,
        include_userinfo: bool = True,
    ) -> RenderedSubscription:
        proxies, report = self._prepare_subscription_format(
            session,
            user,
            plan,
            client_format,
            template_override.content if template_override else None,
            selected_node_ids,
        )
        if node_id is not None:
            allowed_ids = [node.node_id for node in report.nodes if node.available]
            proxies = [
                proxy
                for proxy, identifier in zip(proxies, allowed_ids, strict=True)
                if identifier == node_id
            ]
        if not proxies:
            raise SubscriptionUnavailableError(
                "subscription has no compatible nodes for this format and selection"
            )
        selected = template_override or self.subscription_templates().resolve(
            session, user, plan, client_format.value
        )
        content, media_type, extension = self._render_subscription_content(
            proxies, client_format, selected.content if selected else None
        )
        rendered_title = title or plan.name or user.username
        return RenderedSubscription(
            username=user.username,
            plan_name=rendered_title,
            content=content,
            media_type=media_type,
            filename=f"{self._safe_filename(rendered_title)}.{extension}",
            subscription_userinfo=(
                self._subscription_userinfo_header(session, user, plan)
                if include_userinfo
                else None
            ),
            warnings=list(dict.fromkeys([*report.warnings, *(extra_warnings or [])])),
            included_nodes=len(proxies),
            excluded_nodes=sum(not node.available for node in report.nodes),
        )

    def subscription_format_preview(
        self, username: str, client_format: SubscriptionClientFormat
    ) -> SubscriptionFormatPreview:
        with self._session() as session:
            user = session.get(ProductUserModel, username)
            if user is None:
                raise ProductUserNotFoundError("user not found")
            plan = self._available_subscription_plan(session, user)
            _, report = self._prepare_subscription_format(session, user, plan, client_format)
            return report

    def _available_subscription_plan(
        self, session: Session, user: ProductUserModel | None
    ) -> SubscriptionPlanModel:
        if not user or not user.is_active or user.removal_id:
            raise SubscriptionUnavailableError("subscription user is not active")
        if not user.current_plan_id:
            raise SubscriptionUnavailableError("user has no active subscription plan")
        now = datetime.now(tz=UTC)
        if user.plan_expires_at and now > self._aware_datetime(user.plan_expires_at):
            raise SubscriptionUnavailableError("subscription plan has expired")
        plan = session.get(SubscriptionPlanModel, user.current_plan_id)
        if not plan:
            raise SubscriptionUnavailableError("subscription plan is missing")
        if self._subscription_quota_status(session, user, plan, now).over_quota:
            raise SubscriptionUnavailableError("subscription traffic quota exceeded")
        return plan

    def _prepare_subscription_format(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        client_format: SubscriptionClientFormat,
        template_override: str | None = None,
        selected_node_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], SubscriptionFormatPreview]:
        candidates, warnings = self._subscription_proxy_configs(
            session, user, plan, selected_node_ids
        )
        proxies, nodes = [], []
        used_names = {
            "Proxy",
            "direct",
            "DIRECT",
            "REJECT",
            "REJECT-DROP",
            "PASS",
            "GLOBAL",
            "COMPATIBLE",
        }
        from open_node.services.template_rendering import DEFAULT_SURGE, reserved_names, surge_name

        if client_format.value in {"clash", "surge"}:
            selected = self.subscription_templates().resolve(
                session, user, plan, client_format.value
            )
            content = (
                template_override
                if template_override is not None
                else selected.content
                if selected
                else DEFAULT_SURGE
                if client_format.value == "surge"
                else None
            )
            if content is not None:
                used_names.update(reserved_names(content, client_format.value))
        prepared = []
        name_map: dict[str, str] = {}
        ambiguous_names: set[str] = set()
        for identifier, proxy in candidates:
            original_name = str(proxy.get("name") or "Node")
            name = original_name
            if client_format == SubscriptionClientFormat.SURGE:
                name = surge_name(name)
            unique, suffix = name, 2
            while unique in used_names:
                unique = f"{name} ({suffix})"
                suffix += 1
            used_names.add(unique)
            proxy["name"] = unique
            for alias in {original_name, name}:
                if alias in name_map and name_map[alias] != unique:
                    ambiguous_names.add(alias)
                else:
                    name_map[alias] = unique
            prepared.append((identifier, proxy, unique))
        for identifier, proxy, unique in prepared:
            reason = None
            dialer = proxy.get("dialer-proxy")
            if isinstance(dialer, str) and dialer in ambiguous_names:
                reason = "Dialer proxy reference is ambiguous after node naming"
            elif isinstance(dialer, str) and dialer in name_map:
                proxy["dialer-proxy"] = name_map[dialer]
            reason = reason or subscription_clients.unsupported_reason(proxy, client_format.value)
            if (
                reason is None
                and client_format
                in {SubscriptionClientFormat.URI_LIST, SubscriptionClientFormat.BASE64}
                and self._proxy_uri(proxy) is None
            ):
                reason = "Node cannot be represented as a proxy URI"
            nodes.append(
                SubscriptionFormatNode(
                    node_id=UUID(identifier),
                    name=unique,
                    protocol=subscription_clients.protocol(proxy),
                    available=reason is None,
                    reason=reason,
                )
            )
            if reason is None:
                proxy["port"] = int(proxy["port"])
                proxies.append(proxy)
        return proxies, SubscriptionFormatPreview(
            username=user.username, client_format=client_format, nodes=nodes, warnings=warnings
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
            archived = self._archived_user_traffic(session, username)
            upload = sum(entry.upload for entry in entries)
            download = sum(entry.download for entry in entries)
            upload += sum(entry.upload for entry in archived)
            download += sum(entry.download for entry in archived)
            return ProductUserTrafficResponse(
                username=username,
                upload=upload,
                download=download,
                total=upload + download,
                entries=[self._subscription_traffic_entry_read(entry) for entry in entries]
                + [
                    SubscriptionTrafficEntryRead(
                        username=entry.username,
                        server_id=UUID(entry.server_id),
                        server_name=entry.server_name,
                        archived=True,
                        email="",
                        upload=entry.upload,
                        download=entry.download,
                        total=entry.upload + entry.download,
                        updated_at=entry.updated_at,
                    )
                    for entry in archived
                ],
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
        with self._coordinated_session() as session:
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
        with self._coordinated_session() as session:
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

    @classmethod
    def build_routed_outbound_change_set(
        cls,
        payload: AgentRoutedOutboundChangeSetCreate,
    ) -> AgentChangeSetCreate:
        label_slug = payload.label.lower()
        parent_ref = (payload.parent_ref or f"s{payload.server_id.hex[:8]}").lower()
        outbound_tag = payload.outbound_tag or f"routed:{parent_ref}:{label_slug}"
        marktag = payload.marktag or outbound_tag
        admin_username = cls._safe_credential_username(payload.admin_username)
        admin_email = payload.admin_email or f"{admin_username}__{parent_ref}__{label_slug}"
        timeout_ms = payload.command_timeout_ms

        outbound = deepcopy(payload.outbound)
        outbound["tag"] = outbound_tag
        client = cls._routed_admin_client(payload, admin_username, admin_email)

        steps = [
            AgentChangeSetStepCreate(
                server_id=payload.server_id,
                label=f"Add routed admin client to {payload.inbound_tag}",
                forward=AgentCommandCreate(
                    method="POST",
                    path="/api/child/inbounds",
                    body={
                        "action": "add-client",
                        "tag": payload.inbound_tag,
                        "client": client,
                    },
                    timeout_ms=timeout_ms,
                ),
                rollback=AgentCommandCreate(
                    method="POST",
                    path="/api/child/inbounds",
                    body={
                        "action": "remove-client",
                        "tag": payload.inbound_tag,
                        "client": {"email": admin_email},
                    },
                    timeout_ms=timeout_ms,
                ),
            ),
        ]

        sniffing_excludes = cls._routed_sniffing_exclude_domains(payload, outbound)
        if sniffing_excludes:
            steps.append(
                AgentChangeSetStepCreate(
                    server_id=payload.server_id,
                    label=f"Add sniffing excludes to {payload.inbound_tag}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/inbounds",
                        body={
                            "action": "add-sniffing-exclude",
                            "tag": payload.inbound_tag,
                            "domains": sniffing_excludes,
                        },
                        timeout_ms=timeout_ms,
                    ),
                )
            )

        steps.extend(
            [
                AgentChangeSetStepCreate(
                    server_id=payload.server_id,
                    label=f"Add routed outbound {outbound_tag}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/outbounds",
                        body={"action": "add", "outbound": outbound},
                        timeout_ms=timeout_ms,
                    ),
                    rollback=AgentCommandCreate(
                        method="POST",
                        path="/api/child/outbounds",
                        body={"action": "remove", "tag": outbound_tag},
                        timeout_ms=timeout_ms,
                    ),
                ),
                AgentChangeSetStepCreate(
                    server_id=payload.server_id,
                    label=f"Add routed rule {marktag}",
                    forward=AgentCommandCreate(
                        method="POST",
                        path="/api/child/routing",
                        body={
                            "action": "add_rule",
                            "rule": {
                                "type": "field",
                                "marktag": marktag,
                                "user": [admin_email],
                                "inboundTag": [payload.inbound_tag],
                                "outboundTag": outbound_tag,
                            },
                        },
                        timeout_ms=timeout_ms,
                    ),
                    rollback=AgentCommandCreate(
                        method="POST",
                        path="/api/child/routing",
                        body={
                            "action": "remove_user_from_rule",
                            "marktag": marktag,
                            "user_email": admin_email,
                        },
                        timeout_ms=timeout_ms,
                    ),
                ),
            ]
        )

        note = (
            "Rollback removes the routed admin user from the rule, removes the outbound, "
            "and removes the admin client. Sniffing excludes are additive on the agent "
            "and are intentionally left in place when planned."
        )
        return AgentChangeSetCreate(
            name=f"Create routed outbound {payload.node_name or payload.label}",
            description=(
                f"Plan routed outbound {outbound_tag} for inbound {payload.inbound_tag}. {note}"
            ),
            rollback_on_failure=payload.rollback_on_failure,
            dispatch=payload.dispatch,
            steps=steps,
        )

    @classmethod
    def _routed_admin_client(
        cls,
        payload: AgentRoutedOutboundChangeSetCreate,
        admin_username: str,
        admin_email: str,
    ) -> dict[str, Any]:
        if payload.client is not None:
            client = deepcopy(payload.client)
        else:
            client = cls._generate_subscription_credential(
                protocol=payload.inbound_protocol,
                username=admin_username,
                email=admin_email,
                node_config=payload.outbound,
            )
        client["email"] = admin_email
        client.setdefault("level", 0)
        return client

    @classmethod
    def _routed_sniffing_exclude_domains(
        cls,
        payload: AgentRoutedOutboundChangeSetCreate,
        outbound: dict[str, Any],
    ) -> list[str]:
        domains: list[str] = []
        seen: set[str] = set()

        def append_domain(value: str) -> None:
            domain = value.strip().lower()
            if domain and domain not in seen:
                seen.add(domain)
                domains.append(domain)

        for domain in payload.sniffing_exclude_domains:
            append_domain(domain)
        if payload.add_reality_sniffing_excludes:
            for domain in cls._extract_reality_sni_domains(outbound):
                append_domain(domain)
        return domains

    @staticmethod
    def _extract_reality_sni_domains(outbound: dict[str, Any]) -> list[str]:
        protocol = str(outbound.get("protocol") or "").strip().lower()
        if protocol != "vless":
            return []

        stream = outbound.get("streamSettings") or outbound.get("stream_settings")
        if not isinstance(stream, dict):
            return []
        if str(stream.get("security") or "").strip().lower() != "reality":
            return []

        reality = stream.get("realitySettings") or stream.get("reality_settings")
        if not isinstance(reality, dict):
            return []

        def collect(value: Any) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [item for item in value if isinstance(item, str)]
            return []

        domains = collect(reality.get("serverName"))
        if domains:
            return domains
        return collect(reality.get("serverNames"))

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
        with self._coordinated_session() as session:
            change_set = self._create_change_set_model(session, payload)
            session.commit()
            session.refresh(change_set)
            return self._change_set_read(session, change_set)

    def _create_change_set_model(
        self,
        session: Session,
        payload: AgentChangeSetCreate,
        now: datetime | None = None,
    ) -> AgentChangeSetModel:
        for step in payload.steps:
            step.forward.validate_wire_payload()
            if step.rollback:
                step.rollback.validate_wire_payload()
        now = now or datetime.now(tz=UTC)
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
        session.flush()
        return change_set

    def dispatch_change_set(
        self,
        change_set_id: UUID,
    ) -> tuple[AgentChangeSetRead, list[AgentCommandRead]]:
        return self._change_sets().dispatch(change_set_id)

    def rollback_change_set(
        self,
        change_set_id: UUID,
        payload: AgentChangeSetRollbackRequest,
    ) -> tuple[AgentChangeSetRead, list[AgentCommandRead], list[str]]:
        return self._change_sets().rollback(change_set_id, payload)

    def accept_change_set(
        self,
        change_set_id: UUID,
        payload: AgentChangeSetAcceptRequest,
    ) -> AgentChangeSetRead:
        return self._change_sets().accept(change_set_id, payload)

    def create_command(self, server_id: UUID, payload: AgentCommandCreate) -> AgentCommandRead:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")

            command = self._create_command_model(session, server, payload)
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def create_command_sequence(
        self,
        server_id: UUID,
        payloads: Iterable[AgentCommandCreate],
    ) -> list[AgentCommandRead]:
        with self._session() as session:
            server = session.get(ServerModel, str(server_id))
            if not server:
                raise ServerNotFoundError(f"server not found: {server_id}")
            commands: list[CommandModel] = []
            previous = None
            for payload in payloads:
                command = self._create_command_model(session, server, payload, depends_on=previous)
                session.flush()
                commands.append(command)
                previous = command
            session.commit()
            return [self._command_read(command) for command in commands]

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

    def list_dispatchable_commands(self, server_id: UUID) -> list[AgentCommandRead]:
        now = datetime.now(tz=UTC)
        with self._session() as session:
            commands = session.scalars(
                select(CommandModel)
                .where(
                    CommandModel.server_id == str(server_id),
                    CommandModel.status.in_(
                        [AgentCommandStatus.PENDING.value, AgentCommandStatus.LEASED.value]
                    ),
                )
                .order_by(CommandModel.created_at)
            ).all()
            return [
                self._command_read(command)
                for command in commands
                if command.status == AgentCommandStatus.PENDING.value
                or self._lease_expired(command, now)
            ]

    def lease_commands(
        self,
        token: str,
        max_commands: int,
    ) -> tuple[ServerRead, list[AgentCommandRead]]:
        with self._coordinated_session() as session:
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
                if self._claim_command_lease(session, command, now):
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
        with self._coordinated_session() as session:
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
        with self._coordinated_session() as session:
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
                AgentCommandStatus.SKIPPED.value,
                AgentCommandStatus.WAITING.value,
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

    def lease_command_for_push(self, command_id: UUID) -> AgentCommandRead | None:
        with self._coordinated_session() as session:
            command = session.get(CommandModel, str(command_id))
            if not command:
                raise CommandNotFoundError(f"command not found: {command_id}")
            now = datetime.now(tz=UTC)
            if not self._claim_command_lease(session, command, now):
                session.commit()
                return None
            session.commit()
            session.refresh(command)
            return self._command_read(command)

    def release_command_lease(self, command_id: UUID, attempts: int) -> AgentCommandRead:
        with self._coordinated_session() as session:
            command = session.get(CommandModel, str(command_id))
            if not command:
                raise CommandNotFoundError(f"command not found: {command_id}")
            session.execute(
                update(CommandModel)
                .where(
                    CommandModel.id == str(command_id),
                    CommandModel.status == AgentCommandStatus.LEASED.value,
                    CommandModel.attempts == attempts,
                )
                .values(
                    status=AgentCommandStatus.PENDING.value,
                    leased_at=None,
                    updated_at=datetime.now(tz=UTC),
                )
            )
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
        traffic=None,
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

        if traffic and traffic.last_reported_at is not None:
            probe.traffic_used_up = traffic.upload
            probe.traffic_used_down = traffic.download
            probe.traffic_used_total = traffic.upload + traffic.download
            probe.traffic_used = traffic.used

        if latest:
            if latest.system_tx_total is not None or latest.system_rx_total is not None:
                probe.cumulative_up = latest.system_tx_total or 0
                probe.cumulative_down = latest.system_rx_total or 0

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
            key: self._probe_ping_series_from_state(key, state) for key, state in series.items()
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

            ledger.upload += uplink - ledger.last_uplink if uplink >= ledger.last_uplink else uplink
            ledger.download += (
                downlink - ledger.last_downlink if downlink >= ledger.last_downlink else downlink
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

    @classmethod
    def _traffic_data_for_key(cls, source: dict[str, Any] | None, key: str | None) -> TrafficData:
        if not source or not key:
            return TrafficData()
        return cls._traffic_data_from_record(source.get(key))

    @classmethod
    def _traffic_data_for_keys(cls, source: dict[str, Any] | None, keys: list[str]) -> TrafficData:
        if not source or not keys:
            return TrafficData()
        uplink = 0
        downlink = 0
        for key in keys:
            traffic = cls._traffic_data_from_record(source.get(key))
            uplink += traffic.uplink
            downlink += traffic.downlink
        return TrafficData(uplink=uplink, downlink=downlink)

    @classmethod
    def _traffic_data_from_record(cls, value: Any) -> TrafficData:
        item = cls._record_value(value)
        return TrafficData(
            uplink=cls._traffic_counter_value(item.get("uplink")),
            downlink=cls._traffic_counter_value(item.get("downlink")),
        )

    @staticmethod
    def _sum_traffic_data(values: list[TrafficData]) -> TrafficData:
        return TrafficData(
            uplink=sum(value.uplink for value in values),
            downlink=sum(value.downlink for value in values),
        )

    @staticmethod
    def _product_user_read(user: ProductUserModel) -> ProductUserRead:
        from open_node.services.user_limits import overrides

        return ProductUserRead(
            username=user.username,
            email=user.email,
            display_name=user.display_name or user.username,
            remark=user.remark,
            limit_overrides=overrides(user),
            removal_id=user.removal_id,
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
    def _new_managed_node_model(
        server: ServerModel,
        payload: ManagedNodeCreate,
        now: datetime,
    ) -> ManagedNodeModel:
        return ManagedNodeModel(
            id=str(uuid4()),
            name=payload.name,
            server_id=server.id,
            protocol=payload.protocol.lower(),
            node_type=payload.node_type.value,
            parent_id=str(payload.parent_id) if payload.parent_id else None,
            target_node_id=str(payload.target_node_id) if payload.target_node_id else None,
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

    @staticmethod
    def _managed_node_read(node: ManagedNodeModel) -> ManagedNodeRead:
        return ManagedNodeRead(
            id=UUID(node.id),
            name=node.name,
            server_id=UUID(node.server_id),
            protocol=node.protocol,
            node_type=node.node_type,
            parent_id=node.parent_id,
            target_node_id=node.target_node_id,
            removal_id=node.removal_id,
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
            clash_template_id=plan.clash_template_id,
            surge_template_id=plan.surge_template_id,
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
            node_name_overrides={
                UUID(node_id): name for node_id, name in (plan.node_name_overrides or {}).items()
            },
            node_name_override_enabled=plan.node_name_override_enabled,
            auto_speed_rules=plan.auto_speed_rules or [],
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
        template_names: dict[str, str] | None = None,
    ) -> SubscriptionCatalogPlanEntry:
        from collections import Counter

        counts = Counter(node_names.values())
        if any(
            identifier not in node_names or counts[node_names[identifier]] != 1
            for identifier in (plan.node_name_overrides or {})
        ):
            raise ManagedNodeConflict(
                "Plan aliases require unique, existing node names for catalog export"
            )
        return SubscriptionCatalogPlanEntry(
            clash_template_name=(template_names or {}).get(plan.clash_template_id),
            surge_template_name=(template_names or {}).get(plan.surge_template_id),
            name=plan.name,
            description=plan.description,
            traffic_limit_gb=plan.traffic_limit_bytes / (1024 * 1024 * 1024),
            cycle_days=plan.cycle_days,
            is_reset=plan.is_reset,
            reset_day=plan.reset_day,
            node_names=[
                node_names[node_id] for node_id in (plan.node_ids or []) if node_id in node_names
            ],
            node_multipliers=InventoryStore._catalog_map_keys_to_names(
                plan.node_multipliers or {},
                node_names,
            ),
            node_name_overrides=InventoryStore._catalog_map_keys_to_names(
                plan.node_name_overrides or {},
                node_names,
            ),
            node_name_override_enabled=plan.node_name_override_enabled,
            auto_speed_rules=plan.auto_speed_rules or [],
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
        return {
            node_names[node_id]: value
            for node_id, value in values.items()
            if node_id in node_names
        }

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
            node_name_overrides=cls._catalog_map_keys_to_ids(
                entry.node_name_overrides, node_ids_by_name
            ),
            node_name_override_enabled=entry.node_name_override_enabled,
            auto_speed_rules=[rule.model_dump() for rule in entry.auto_speed_rules],
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
        if "auto_speed_rules" in entry.model_fields_set:
            plan.auto_speed_rules = [rule.model_dump() for rule in entry.auto_speed_rules]
        if "node_name_overrides" in entry.model_fields_set:
            plan.node_name_overrides = cls._catalog_map_keys_to_ids(
                entry.node_name_overrides, node_ids_by_name
            )
        else:
            plan.node_name_overrides = {
                key: value
                for key, value in (plan.node_name_overrides or {}).items()
                if key in node_ids
            }
        if "node_name_override_enabled" in entry.model_fields_set:
            plan.node_name_override_enabled = entry.node_name_override_enabled
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
            user = session.get(ProductUserModel, entry.username)
            if not user:
                warnings.append(
                    f"credential {entry.email} skipped; user {entry.username} not found"
                )
                continue
            self._user_management().require_editable(user)
            server = self._catalog_server(session, server_map, entry.server_name)
            if not server:
                warnings.append(
                    f"credential {entry.email} skipped; server {entry.server_name} not found"
                )
                continue
            self._user_management().check_imported_credential(session, server.id, entry)
            self._node_management().check_imported_credential(session, server.id, entry)
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
            node = session.get(ManagedNodeModel, str(node_id))
            if not node:
                raise ManagedNodeNotFoundError(f"managed node not found: {node_id}")
            if node.removal_id:
                raise ManagedNodeConflict("A selected node is being removed")

    @staticmethod
    def _ensure_plan_nodes_assignable(session: Session, node_ids: list[UUID]) -> None:
        InventoryStore._ensure_managed_nodes_exist(session, node_ids)
        if session.scalar(
            select(PrivateRoutedNodeModel.node_id)
            .where(PrivateRoutedNodeModel.node_id.in_([str(node_id) for node_id in node_ids]))
            .limit(1)
        ):
            raise ManagedNodeConflict("Private routed nodes cannot be assigned to shared plans")

    @staticmethod
    def _effective_subscription_node_ids(
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        selected_node_ids: set[str] | None = None,
    ) -> list[str]:
        plan_node_ids = plan.node_ids or []
        private_node_ids = set(
            session.scalars(
                select(PrivateRoutedNodeModel.node_id).where(
                    PrivateRoutedNodeModel.node_id.in_(plan_node_ids)
                )
            ).all()
        )
        identifiers = [
            node_id
            for node_id in plan_node_ids
            if node_id not in private_node_ids
            and (selected_node_ids is None or node_id in selected_node_ids)
        ]
        owned = session.scalars(
            select(PrivateRoutedNodeModel)
            .where(
                PrivateRoutedNodeModel.username == user.username,
                PrivateRoutedNodeModel.status == "active",
            )
            .order_by(PrivateRoutedNodeModel.created_at, PrivateRoutedNodeModel.node_id)
        ).all()
        identifiers.extend(
            row.node_id
            for row in owned
            if selected_node_ids is None or row.node_id in selected_node_ids
        )
        return list(dict.fromkeys(identifiers))

    @staticmethod
    def _subscription_node_allowed(
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        node_id: str,
    ) -> bool:
        private = session.get(PrivateRoutedNodeModel, node_id)
        if private is not None:
            return private.username == user.username and private.status == "active"
        return node_id in plan.node_ids

    def _subscription_provision_batches(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel,
        no_restart: bool,
    ) -> tuple[list[SubscriptionProvisionBatch], list[str]]:
        from open_node.services.user_limits import effective_limits

        warnings: list[str] = []
        effective_node_ids = self._effective_subscription_node_ids(session, user, plan)
        if not effective_node_ids:
            return [], warnings

        nodes = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(effective_node_ids))
        ).all()
        nodes_by_id = {node.id: node for node in nodes}
        batches: dict[str, dict[str, Any]] = {}
        server_names: dict[str, str] = {}
        seen_inbound: set[tuple[str, str, str]] = set()
        seen_route: set[tuple[str, str, str, str]] = set()
        limiter_bindings: dict[tuple[str, str, str], dict[str, Any]] = {}

        for node_id in effective_node_ids:
            node = nodes_by_id.get(node_id)
            if not node:
                warnings.append(f"node {node_id} no longer exists")
                continue
            if not node.enabled or node.removal_id:
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

                limits = effective_limits(user, plan, node)
                speed = int(limits.speed_limit_mbps * 125000)
                connections = limits.device_limit
                agent = session.scalar(select(AgentModel).where(AgentModel.server_id == server.id))
                native = agent is not None and agent.capability_native_limiter
                if speed or connections or native or plan.auto_speed_rules:
                    group_data = json.dumps(
                        [user.username, server.id, node.inbound_tag], separators=(",", ":")
                    ).encode()
                    binding = {
                        "inbound_tag": node.inbound_tag,
                        "user": {
                            "uid": 0,
                            "email": client_email,
                            "speed_limit": speed,
                            "device_limit": connections,
                            "conn_group": "account-" + hashlib.sha256(group_data).hexdigest(),
                        },
                    }
                    previous = limiter_bindings.get(inbound_key)
                    if plan.auto_speed_rules:
                        binding["user"]["auto_speed_rules"] = deepcopy(plan.auto_speed_rules)
                    if previous:
                        for field in ("speed_limit", "device_limit"):
                            values = [previous["user"][field], binding["user"][field]]
                            previous["user"][field] = min(
                                (value for value in values if value), default=0
                            )
                    else:
                        limiter_bindings[inbound_key] = binding
                        body.setdefault("limiter_users", []).append(binding)
                    if not native and (speed or connections or plan.auto_speed_rules):
                        warnings.append(
                            f"node {node.name}: limits require a native-limiter Open Node Agent; "
                            "unsupported agents will not receive this provisioning batch"
                        )
                    elif plan.auto_speed_rules and not agent.capability_user_auto_speed_rules:
                        warnings.append(
                            f"node {node.name}: upgrade the Agent for "
                            "per-user automatic speed rules"
                        )

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
        selected_node_ids: set[str] | None = None,
    ) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
        warnings: list[str] = []
        plan_node_ids = self._effective_subscription_node_ids(
            session, user, plan, selected_node_ids
        )
        if not plan_node_ids:
            return [], warnings

        nodes = session.scalars(
            select(ManagedNodeModel).where(ManagedNodeModel.id.in_(plan_node_ids))
        ).all()
        nodes_by_id = {node.id: node for node in nodes}
        proxies: list[tuple[str, dict[str, Any]]] = []

        for node_id in plan_node_ids:
            node = nodes_by_id.get(node_id)
            if not node:
                warnings.append(f"node {node_id} no longer exists")
                continue
            if not node.enabled or node.removal_id:
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
            runtime_key_required = proxy.pop("server-key-source", None) == "runtime"
            server_key = None
            if runtime_key_required:
                scan = session.get(AgentScanResultModel, server.id)
                server_key = self._runtime_shadowsocks_server_key(scan, node)
                if server_key:
                    proxy["password"] = server_key
                else:
                    warnings.append(
                        f"node {node.name} needs a current matching Shadowsocks server key"
                    )
            provisioned_client = self._provisioning_client_from_credential(
                user, plan, node, server, credential
            )
            self._apply_credential_to_proxy(proxy, node.protocol, provisioned_client)
            if runtime_key_required and not server_key:
                proxy["password"] = None
            proxy["name"] = self._subscription_proxy_name(plan, node, str(proxy["name"]))
            proxies.append((node.id, proxy))

        session.flush()
        return proxies, warnings

    @classmethod
    def _runtime_shadowsocks_server_key(
        cls,
        scan: AgentScanResultModel | None,
        node: ManagedNodeModel,
    ) -> str | None:
        if scan is None or not node.inbound_tag:
            return None
        matches = [
            inbound
            for inbound in scan.inbounds or []
            if isinstance(inbound, dict)
            and inbound.get("tag") == node.inbound_tag
            and inbound.get("protocol") == "shadowsocks"
        ]
        if len(matches) != 1:
            return None
        settings = cls._record_value(matches[0].get("settings"))
        if settings.get("method") != (node.config or {}).get("cipher"):
            return None
        return cls._text_value(settings.get("password"))

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
        if node.protocol == "vless" and (node.config or {}).get("flow"):
            client.setdefault("flow", node.config["flow"])
        if node.protocol in {"ss", "shadowsocks"}:
            method = str(
                (node.config or {}).get("cipher") or (node.config or {}).get("method") or ""
            )
            if method and not method.startswith("2022-"):
                client.setdefault("method", method)
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
        if session.scalar(
            select(ProductUserRemovalModel.id)
            .where(
                ProductUserRemovalModel.username == user.username,
                ProductUserRemovalModel.completed_at.is_not(None),
            )
            .limit(1)
        ):
            incarnation = self._aware_datetime(user.created_at).isoformat()
            email += "--" + hashlib.sha256(incarnation.encode()).hexdigest()[:12]
        shared = session.scalar(
            select(SubscriptionCredentialModel)
            .where(
                SubscriptionCredentialModel.username == user.username,
                SubscriptionCredentialModel.server_id == server.id,
                SubscriptionCredentialModel.email == email,
            )
            .order_by(SubscriptionCredentialModel.created_at)
        )
        source_node = session.get(ManagedNodeModel, shared.node_id) if shared else None
        can_share = bool(
            shared
            and source_node
            and not source_node.removal_id
            and all(
                getattr(source_node, field) == getattr(node, field)
                for field in (
                    "protocol",
                    "node_type",
                    "inbound_tag",
                    "routed_outbound_tag",
                    "routed_rule_marktag",
                )
            )
        )
        retired_label = any(
            item["server_id"] == server.id and item["email"] == email
            for job in self._node_management().jobs(session)
            for item in job.fingerprints
        )
        if (shared and not can_share) or retired_label:
            email += "--" + node.id.replace("-", "")[:12]
            can_share = False
        credential = SubscriptionCredentialModel(
            id=str(uuid4()),
            username=user.username,
            node_id=node.id,
            server_id=server.id,
            inbound_tag=node.inbound_tag,
            protocol=node.protocol,
            email=email,
            credential=deepcopy(shared.credential)
            if can_share
            else self._generate_subscription_credential(
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
                if normalized == "vless" and credential.get("flow"):
                    proxy["flow"] = credential["flow"]
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
        template_content: str | None = None,
    ) -> tuple[str, str, str]:
        from open_node.services.template_rendering import DEFAULT_SURGE, render

        if client_format == SubscriptionClientFormat.SURGE:
            return (
                render(template_content or DEFAULT_SURGE, "surge", proxies)[0],
                "text/plain; charset=utf-8",
                "conf",
            )
        if template_content is not None and client_format == SubscriptionClientFormat.CLASH:
            return render(template_content, "clash", proxies)[0], "text/yaml; charset=utf-8", "yaml"
        match client_format:
            case SubscriptionClientFormat.CLASH:
                return cls._render_clash_subscription(proxies), "text/yaml; charset=utf-8", "yaml"
            case SubscriptionClientFormat.SING_BOX:
                return (
                    cls._render_sing_box_subscription(proxies),
                    "application/json; charset=utf-8",
                    "json",
                )
            case SubscriptionClientFormat.XRAY:
                payload = {
                    "log": {"loglevel": "warning"},
                    "inbounds": [
                        {
                            "tag": "socks-in",
                            "listen": "127.0.0.1",
                            "port": 1080,
                            "protocol": "socks",
                            "settings": {"auth": "noauth", "udp": True},
                        }
                    ],
                    "outbounds": [subscription_clients.xray_outbound(proxy) for proxy in proxies],
                }
                return (
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
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
        proxies = [subscription_clients.clash_proxy(proxy) for proxy in proxies]
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
            "inbounds": [
                {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 7890}
            ],
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
        if subscription_clients.unsupported_reason(proxy, "sing-box"):
            return None
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
            if proxy_type == "hysteria2":
                outbound.update(subscription_clients.sing_box_hysteria_options(proxy))
        elif proxy_type == "anytls":
            outbound["password"] = str(proxy.get("password") or "")
            for source_keys, target_key in (
                (
                    (
                        "idle_session_check_interval",
                        "idleSessionCheckInterval",
                        "idle-session-check-interval",
                    ),
                    "idle_session_check_interval",
                ),
                (
                    ("idle_session_timeout", "idleSessionTimeout", "idle-session-timeout"),
                    "idle_session_timeout",
                ),
            ):
                if duration := cls._sing_box_duration(cls._proxy_value(proxy, *source_keys)):
                    outbound[target_key] = duration
            min_idle_session = cls._proxy_nonnegative_int(
                cls._proxy_value(proxy, "min_idle_session", "minIdleSession", "min-idle-session")
            )
            if min_idle_session is not None:
                outbound["min_idle_session"] = min_idle_session
            client_metadata = cls._proxy_value(
                proxy,
                "client_metadata",
                "clientMetadata",
                "client-metadata",
            )
            if client_metadata is not None:
                outbound["client_metadata"] = str(client_metadata)
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
        transport = subscription_clients.sing_box_transport(proxy)
        if transport:
            outbound["transport"] = transport
        return outbound

    @staticmethod
    def _sing_box_tls(proxy: dict[str, Any]) -> dict[str, Any] | None:
        return subscription_clients.sing_box_tls(proxy)

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
                **subscription_clients.uri_options(proxy),
                "flow": proxy.get("flow"),
                "encryption": proxy.get("encryption") or "none",
            }
        )
        return (
            f"vless://{quote(str(uuid), safe='')}@{subscription_clients.uri_server(server)}:{port}"
            f"{query}{cls._uri_fragment(proxy)}"
        )

    @classmethod
    def _vmess_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        uuid = proxy.get("uuid") or proxy.get("id")
        if not isinstance(server, str) or not server or not port or not uuid:
            return None
        options = subscription_clients.uri_options(proxy)
        payload = {
            "v": "2",
            "ps": str(proxy.get("name") or server),
            "add": server,
            "port": str(port),
            "id": str(uuid),
            "aid": str(cls._proxy_int(proxy.get("alterId")) or 0),
            "scy": str(proxy.get("cipher") or proxy.get("security") or "auto"),
            "net": str(options["type"]),
            "type": str(proxy.get("headerType") or "none"),
            "host": str(options.get("host") or ""),
            "path": str(options.get("serviceName") or options.get("path") or ""),
            "tls": "tls" if options["security"] == "tls" else "",
            "sni": str(options.get("sni") or ""),
            "alpn": str(options.get("alpn") or ""),
            "fp": str(options.get("fp") or ""),
            "allowInsecure": options.get("allowInsecure") or "0",
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        return f"vmess://{encoded}"

    @classmethod
    def _trojan_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        password = proxy.get("password")
        if not isinstance(server, str) or not server or not port or not password:
            return None
        query = cls._uri_query(subscription_clients.uri_options(proxy))
        return (
            f"trojan://{quote(str(password), safe='')}@"
            f"{subscription_clients.uri_server(server)}:{port}"
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
        userinfo = (
            base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode("ascii").rstrip("=")
        )
        return f"ss://{userinfo}@{subscription_clients.uri_server(server)}:{port}{cls._uri_fragment(proxy)}"

    @classmethod
    def _hysteria2_uri(cls, proxy: dict[str, Any]) -> str | None:
        server = proxy.get("server")
        port = cls._proxy_int(proxy.get("port"))
        password = proxy.get("password") or proxy.get("auth")
        if not isinstance(server, str) or not server or not port or not password:
            return None
        tls = subscription_clients.sing_box_tls(proxy) or {}
        query = cls._uri_query(
            {
                "sni": tls.get("server_name"),
                "alpn": ",".join(tls.get("alpn") or []),
                "insecure": "1" if tls.get("insecure") else None,
                "obfs": proxy.get("obfs"),
                "obfs-password": proxy.get("obfs-password"),
                "mport": proxy.get("ports"),
            }
        )
        return (
            f"hysteria2://{quote(str(password), safe='')}@"
            f"{subscription_clients.uri_server(server)}:{port}"
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
        return f"{scheme}://{auth}{subscription_clients.uri_server(server)}:{port}{cls._uri_fragment(proxy)}"

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
    def _proxy_nonnegative_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, str) and value.isdigit():
            parsed = int(value)
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    def _proxy_value(proxy: dict[str, Any], *keys: str) -> object:
        for key in keys:
            value = proxy.get(key)
            if value is not None and value != "":
                return value
        return None

    @staticmethod
    def _sing_box_duration(value: object) -> str | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            return f"{value:g}s" if value > 0 else None
        if isinstance(value, str):
            duration = value.strip()
            if not duration:
                return None
            return f"{duration}s" if duration.isdigit() else duration
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
        from open_node.services.user_limits import traffic_limit

        total = traffic_limit(user, plan)
        if total <= 0:
            return None
        upload, download = self._subscription_user_traffic(session, user.username)
        if plan.traffic_mode == "oneway":
            upload = 0
        expire = (
            int(self._aware_datetime(user.plan_expires_at).timestamp())
            if user.plan_expires_at
            else 4_102_444_800
        )
        return f"upload={upload}; download={download}; total={total}; expire={expire}"

    def _subscription_quota_status(
        self,
        session: Session,
        user: ProductUserModel,
        plan: SubscriptionPlanModel | None,
        now: datetime,
    ) -> SubscriptionQuotaStatusRead:
        from open_node.services.user_limits import traffic_limit

        upload, download = self._subscription_user_traffic(session, user.username)
        traffic_mode = SubscriptionTrafficMode(plan.traffic_mode) if plan else None
        traffic_limit_bytes = traffic_limit(user, plan)
        charged_usage_bytes = (
            download if traffic_mode == SubscriptionTrafficMode.ONEWAY else upload + download
        )
        expired = bool(user.plan_expires_at and now > self._aware_datetime(user.plan_expires_at))
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
            available=bool(
                user.is_active and not user.removal_id and plan and not expired and not over_quota
            ),
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
        for entry in self._archived_user_traffic(session, user.username):
            entry.upload = entry.download = 0
            entry.updated_at = now
            touched += 1
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
            cls._aware_datetime(user.last_traffic_reset_at) if user.last_traffic_reset_at else None
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
        archived = self._archived_user_traffic(session, username)
        if ledgers:
            upload, download = (
                sum(ledger.upload for ledger in ledgers),
                sum(ledger.download for ledger in ledgers),
            )
        else:
            upload, download = self._subscription_latest_user_traffic(session, username)
        return (
            upload + sum(row.upload for row in archived),
            download + sum(row.download for row in archived),
        )

    @staticmethod
    def _archived_user_traffic(session, username):
        return session.scalars(
            select(SubscriptionArchivedTrafficModel)
            .where(SubscriptionArchivedTrafficModel.username == username)
            .order_by(SubscriptionArchivedTrafficModel.server_id)
        ).all()

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
        values = [
            token.username,
            token.token,
            token.short_code,
            token.custom_short_code,
            InventoryStore._aware_datetime(token.updated_at).isoformat(),
        ]
        return SubscriptionTokenRecord(
            username=token.username,
            token=token.token,
            short_code=token.custom_short_code or token.short_code,
            generated_short_code=token.short_code,
            custom_short_code=token.custom_short_code,
            revision=hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest(),
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
    def _subscription_key_in_use(session, key, *, except_user=None):
        model = ProductUserSubscriptionTokenModel
        statement = select(model.username).where(
            (func.lower(model.token) == key.lower())
            | (func.lower(model.short_code) == key.lower())
            | (func.lower(model.custom_short_code) == key.lower())
        )
        if except_user is not None:
            statement = statement.where(model.username != except_user)
        return session.scalar(statement.limit(1)) is not None

    @staticmethod
    def _unique_subscription_token(session: Session) -> str:
        while True:
            token = token_urlsafe(32)
            if not InventoryStore._subscription_key_in_use(session, token):
                return token

    @staticmethod
    def _unique_subscription_short_code(session: Session) -> str:
        while True:
            short_code = uuid4().hex[:8]
            if not InventoryStore._subscription_key_in_use(session, short_code):
                return short_code

    @staticmethod
    def _default_client_email(user: ProductUserModel, node: ManagedNodeModel) -> str:
        label = (
            node.inbound_tag or node.routed_rule_marktag or node.routed_outbound_tag or node.name
        )
        suffix = "".join(
            char if char.isalnum() or char in {"-", "_", "."} else "_" for char in label.strip()
        ).strip("_")
        return f"{user.username}__{suffix or node.protocol}"

    @staticmethod
    def _template_context(
        user: ProductUserModel,
        plan: SubscriptionPlanModel | None,
        node: ManagedNodeModel,
        server: ServerModel,
        credential: SubscriptionCredentialModel,
    ) -> dict[str, str]:
        context = {
            "username": user.username,
            "user_email": user.email or user.username,
            "display_name": user.display_name or user.username,
            "client_email": credential.email,
            "plan_id": plan.id if plan else "",
            "plan_name": plan.name if plan else "",
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
        if plan.node_name_override_enabled:
            name = (plan.node_name_overrides or {}).get(node.id) or name
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

    def subscription_templates(self):
        from open_node.services.subscription_templates import TemplateStore

        return TemplateStore(self)

    @contextmanager
    def _coordinated_session(self):
        with self._session() as session:
            # Reservations and command leases must observe one serialized state transition.
            if self._engine.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                session.execute(
                    select(ServerModel.id).order_by(ServerModel.id).with_for_update()
                ).all()
            yield session

    def _change_sets(self):
        from open_node.services.change_sets import ChangeSetCoordinator

        return ChangeSetCoordinator(self)

    def _subscription_access(self):
        from open_node.services.subscription_access import SubscriptionAccessCoordinator

        return SubscriptionAccessCoordinator(self)

    def _subscription_profiles(self):
        from open_node.services.subscription_profiles import SubscriptionProfiles

        return SubscriptionProfiles(self)

    def _temporary_subscriptions(self):
        from open_node.services.temporary_subscriptions import TemporarySubscriptions

        return TemporarySubscriptions(self)

    def _private_routed_nodes(self):
        from open_node.services.private_routed_nodes import PrivateRoutedNodes

        return PrivateRoutedNodes(self)

    def _server_traffic(self):
        from open_node.services.server_traffic import ServerTrafficCoordinator

        return ServerTrafficCoordinator(self)

    def _server_management(self):
        from open_node.services.server_management import ServerManagement

        return ServerManagement(self)

    def _plan_management(self):
        from open_node.services.plan_management import PlanManagement

        return PlanManagement(self)

    def _user_management(self):
        from open_node.services.user_management import UserManagement

        return UserManagement(self)

    def _node_management(self):
        from open_node.services.node_management import NodeManagement

        return NodeManagement(self)

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
            traffic_reset_day=server.traffic_reset_day,
            last_traffic_reset_at=server.last_traffic_reset_at,
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
                native_limiter=agent.capability_native_limiter,
                user_auto_speed_rules=agent.capability_user_auto_speed_rules,
                subscription_access=agent.capability_subscription_access,
                node_cleanup=agent.capability_node_cleanup,
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
            resolution_reason=change_set.resolution_reason,
            steps=sorted(
                [self._change_set_step_read(session, step) for step in steps]
                + [
                    AgentChangeSetStepRead.model_validate(step)
                    for step in (change_set.archived_steps or [])
                ],
                key=lambda step: step.sequence,
            ),
            created_at=change_set.created_at,
            updated_at=change_set.updated_at,
            **self._change_sets().read_state(session, change_set, steps),
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
            rollback_history=[
                InventoryStore._command_read(previous)
                for identifier in (step.rollback_history_ids or [])
                if (previous := session.get(CommandModel, identifier)) is not None
            ],
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
        *,
        depends_on: CommandModel | None = None,
    ) -> CommandModel:
        payload.validate_wire_payload()
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
            status=(
                AgentCommandStatus.WAITING.value
                if depends_on and depends_on.status != AgentCommandStatus.SUCCEEDED.value
                else AgentCommandStatus.PENDING.value
            ),
            depends_on_command_id=depends_on.id if depends_on else None,
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
    def _current_xray_config_snapshot(
        session: Session,
        server_id: str,
    ) -> XrayConfigSnapshotModel | None:
        return session.scalar(
            select(XrayConfigSnapshotModel)
            .where(
                XrayConfigSnapshotModel.server_id == server_id,
                XrayConfigSnapshotModel.status == XrayConfigSnapshotStatus.CURRENT.value,
            )
            .order_by(XrayConfigSnapshotModel.created_at.desc())
        )

    @staticmethod
    def _pending_xray_config_snapshot(
        session: Session,
        server_id: str,
    ) -> XrayConfigSnapshotModel | None:
        return session.scalar(
            select(XrayConfigSnapshotModel)
            .where(
                XrayConfigSnapshotModel.server_id == server_id,
                XrayConfigSnapshotModel.status == XrayConfigSnapshotStatus.PENDING_RECOVERY.value,
            )
            .order_by(XrayConfigSnapshotModel.created_at.desc())
        )

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
    def _upsert_agent_report_xray_config_snapshot(
        session: Session,
        server: ServerModel,
        config: str,
        source_command_id: str | None,
        created_at: datetime,
    ) -> XrayConfigSnapshotModel | None:
        config_hash = InventoryStore._hash_xray_config(config)
        current = InventoryStore._current_xray_config_snapshot(session, server.id)
        if current is None:
            return InventoryStore._upsert_current_xray_config_snapshot(
                session,
                server,
                config=config,
                source=XrayConfigSnapshotSource.AGENT_REPORT,
                source_command_id=source_command_id,
                created_at=created_at,
            )
        if current.config_hash == config_hash:
            InventoryStore._discard_pending_xray_recovery(session, server.id)
            return current

        InventoryStore._discard_pending_xray_recovery(session, server.id)
        pending = XrayConfigSnapshotModel(
            id=str(uuid4()),
            server_id=server.id,
            source_command_id=source_command_id,
            config=config,
            config_hash=config_hash,
            source=XrayConfigSnapshotSource.AGENT_REPORT.value,
            status=XrayConfigSnapshotStatus.PENDING_RECOVERY.value,
            size_bytes=len(config.encode("utf-8")),
            created_at=created_at,
        )
        session.add(pending)
        return pending

    @staticmethod
    def _discard_pending_xray_recovery(session: Session, server_id: str) -> None:
        pending_snapshots = session.scalars(
            select(XrayConfigSnapshotModel).where(
                XrayConfigSnapshotModel.server_id == server_id,
                XrayConfigSnapshotModel.status == XrayConfigSnapshotStatus.PENDING_RECOVERY.value,
            )
        ).all()
        for snapshot in pending_snapshots:
            session.delete(snapshot)

    @classmethod
    def _queue_xray_snapshot_sync_on_agent_register(
        cls,
        session: Session,
        server: ServerModel,
        now: datetime,
    ) -> None:
        existing = session.scalar(
            select(CommandModel)
            .where(
                CommandModel.server_id == server.id,
                CommandModel.method == "GET",
                CommandModel.path == "/api/child/xray/config",
                CommandModel.query == "",
                CommandModel.status.in_(
                    [AgentCommandStatus.PENDING.value, AgentCommandStatus.LEASED.value]
                ),
            )
            .order_by(CommandModel.created_at.desc())
        )
        if existing is not None:
            return

        sync = AgentCommandCreate(
            method="GET",
            path="/api/child/xray/config",
            timeout_ms=_XRAY_RECONNECT_SYNC_TIMEOUT_MS,
        )
        cls._create_command_model(session, server, sync, now=now)

    @classmethod
    def _queue_xray_snapshot_refresh_after_mutation(
        cls,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
        now: datetime,
    ) -> None:
        if payload.error or payload.status >= 400:
            return
        if not cls._should_refresh_xray_snapshot_after(command.method, command.path, command.body):
            return

        existing = session.scalar(
            select(CommandModel)
            .where(
                CommandModel.server_id == server.id,
                CommandModel.method == "GET",
                CommandModel.path == "/api/child/xray/config",
                CommandModel.query == _XRAY_SNAPSHOT_REFRESH_QUERY,
                CommandModel.status.in_(
                    [AgentCommandStatus.PENDING.value, AgentCommandStatus.LEASED.value]
                ),
            )
            .order_by(CommandModel.created_at.desc())
        )
        if existing is not None:
            return

        refresh = AgentCommandCreate(
            method="GET",
            path="/api/child/xray/config",
            query=_XRAY_SNAPSHOT_REFRESH_QUERY,
            timeout_ms=_XRAY_SNAPSHOT_REFRESH_TIMEOUT_MS,
        )
        cls._create_command_model(session, server, refresh, now=now)

    @staticmethod
    def _should_refresh_xray_snapshot_after(method: str, path: str, body=None) -> bool:
        normalized_method = method.upper()
        if normalized_method in {"", "GET", "HEAD", "OPTIONS"}:
            return False
        normalized_path = path.split("?", 1)[0]
        if normalized_path == "/api/child/node-cleanup":
            return isinstance(body, dict) and body.get("action") == "apply"
        return any(
            normalized_path == prefix or normalized_path.startswith(f"{prefix}/")
            for prefix in _XRAY_MUTATING_PATH_PREFIXES
        )

    @staticmethod
    def merge_agent_only_inbounds_outbounds(
        base_config: str,
        agent_config: str,
    ) -> tuple[str, int, list[str]]:
        try:
            base = json.loads(base_config)
            agent = json.loads(agent_config)
        except json.JSONDecodeError:
            return base_config, 0, ["agent_only_merge_skipped_invalid_json"]
        if not isinstance(base, dict) or not isinstance(agent, dict):
            return base_config, 0, ["agent_only_merge_skipped_invalid_shape"]

        added = 0
        for key in ("inbounds", "outbounds"):
            base_items = base.get(key)
            agent_items = agent.get(key)
            if base_items is None:
                base_items = []
            if not isinstance(base_items, list) or not isinstance(agent_items, list):
                continue

            base_tags = {
                tag
                for item in base_items
                if isinstance(item, dict)
                if (tag := InventoryStore._text_value(item.get("tag")))
            }
            for item in agent_items:
                if not isinstance(item, dict):
                    continue
                tag = InventoryStore._text_value(item.get("tag"))
                if not tag or tag in base_tags:
                    continue
                base_items.append(deepcopy(item))
                base_tags.add(tag)
                added += 1
            base[key] = base_items

        if added == 0:
            return base_config, 0, []
        return json.dumps(base, ensure_ascii=False, separators=(",", ":")), added, []

    @staticmethod
    def _xray_config_snapshot_source(
        command: CommandModel,
        payload: AgentCommandResultRequest,
    ) -> tuple[str | None, XrayConfigSnapshotSource]:
        if command.method.upper() == "GET":
            body = payload.body if isinstance(payload.body, dict) else {}
            if body.get("success") is False:
                return None, XrayConfigSnapshotSource.AGENT_REPORT
            source = (
                XrayConfigSnapshotSource.MASTER_WRITE
                if command.query == _XRAY_SNAPSHOT_REFRESH_QUERY
                else XrayConfigSnapshotSource.AGENT_REPORT
            )
            return InventoryStore._xray_config_text(body.get("config")), (source)

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

    @classmethod
    def _xray_runtime_inventory_response(
        cls,
        server_id: UUID,
        scan: AgentScanResultModel | None,
        latest_telemetry: TelemetrySnapshotModel | None = None,
    ) -> XrayRuntimeInventoryResponse:
        if scan is None:
            return XrayRuntimeInventoryResponse(server_id=server_id)

        stats = cls._record_value(latest_telemetry.stats if latest_telemetry else None)
        inbound_stats = cls._record_value(stats.get("inbound"))
        user_stats = cls._record_value(stats.get("user"))
        inbounds = [
            cls._xray_runtime_inbound_read(inbound, index, inbound_stats, user_stats)
            for index, inbound in enumerate(scan.inbounds or [])
        ]
        protocol_counts: dict[str, int] = {}
        for inbound in inbounds:
            protocol_counts[inbound.protocol] = protocol_counts.get(inbound.protocol, 0) + 1
        traffic = cls._sum_traffic_data([inbound.traffic for inbound in inbounds])
        user_traffic = cls._sum_traffic_data([inbound.user_traffic for inbound in inbounds])
        traffic_reported_at = latest_telemetry.reported_at if latest_telemetry and stats else None

        return XrayRuntimeInventoryResponse(
            server_id=server_id,
            has_scan=True,
            xray_running=scan.xray_running,
            xray_version=scan.xray_version,
            api_port=scan.api_port,
            config_path=scan.config_path,
            config_modified=scan.config_modified,
            config_added_sections=scan.config_added_sections or [],
            message=scan.message,
            inbound_count=len(inbounds),
            client_count=sum(inbound.client_count for inbound in inbounds),
            protocol_counts=protocol_counts,
            traffic=traffic,
            user_traffic=user_traffic,
            traffic_reported_at=traffic_reported_at,
            inbounds=inbounds,
            reported_at=scan.reported_at,
            updated_at=scan.updated_at,
        )

    @classmethod
    def _xray_runtime_tunnel_inventory_response(
        cls,
        server_id: UUID,
        snapshot: XrayConfigSnapshotModel | None,
    ) -> XrayRuntimeTunnelInventoryResponse:
        if snapshot is None:
            return XrayRuntimeTunnelInventoryResponse(server_id=server_id)

        try:
            config = json.loads(snapshot.config)
        except json.JSONDecodeError:
            return XrayRuntimeTunnelInventoryResponse(
                server_id=server_id,
                has_config=True,
                source_snapshot_id=UUID(snapshot.id),
                warnings=["invalid_config_json"],
            )

        if not isinstance(config, dict):
            return XrayRuntimeTunnelInventoryResponse(
                server_id=server_id,
                has_config=True,
                source_snapshot_id=UUID(snapshot.id),
                warnings=["invalid_config_shape"],
            )

        tunnels, chains, warnings = cls._xray_runtime_tunnels_from_config(config)
        return XrayRuntimeTunnelInventoryResponse(
            server_id=server_id,
            has_config=True,
            source_snapshot_id=UUID(snapshot.id),
            tunnel_count=len(tunnels),
            chain_count=len(chains),
            tunnels=tunnels,
            chains=chains,
            warnings=warnings,
        )

    @classmethod
    def _xray_runtime_tunnels_from_config(
        cls,
        config: dict[str, Any],
    ) -> tuple[
        list[XrayRuntimeTunnelRead],
        list[XrayRuntimeTunnelChainRead],
        list[str],
    ]:
        inbounds_value = config.get("inbounds")
        outbounds_value = config.get("outbounds")
        routing_value = cls._record_value(config.get("routing"))
        rules_value = routing_value.get("rules")
        inbounds = cls._list_value(inbounds_value)
        outbounds = cls._list_value(outbounds_value)
        rules = cls._list_value(rules_value)
        warnings: list[str] = []

        if inbounds_value is not None and not isinstance(inbounds_value, list):
            warnings.append("invalid_inbounds")
        if outbounds_value is not None and not isinstance(outbounds_value, list):
            warnings.append("invalid_outbounds")
        if rules_value is not None and not isinstance(rules_value, list):
            warnings.append("invalid_routing_rules")

        inbound_by_tag: dict[str, dict[str, Any]] = {}
        tunnels: list[XrayRuntimeTunnelRead] = []
        for inbound_value in inbounds:
            inbound = cls._record_value(inbound_value)
            if not inbound:
                continue
            tag = cls._text_value(inbound.get("tag"))
            if tag:
                inbound_by_tag[tag] = inbound
            protocol = (cls._text_value(inbound.get("protocol")) or "").lower()
            if protocol != "tunnel":
                continue
            if not tag:
                warnings.append("tunnel_inbound_missing_tag")
                continue
            if tag in {"api", "tunnel-in"}:
                continue
            settings = cls._record_value(inbound.get("settings"))
            tunnels.append(
                XrayRuntimeTunnelRead(
                    kind="inbound",
                    tag=tag,
                    listen_port=cls._port_value(inbound.get("port")),
                    target_address=cls._text_value(settings.get("address")),
                    target_port=cls._port_value(settings.get("port")),
                    network=cls._text_value(settings.get("network")),
                )
            )

        outbound_by_tag: dict[str, dict[str, Any]] = {}
        for outbound_value in outbounds:
            outbound = cls._record_value(outbound_value)
            tag = cls._text_value(outbound.get("tag"))
            if tag:
                outbound_by_tag[tag] = outbound

        for rule_index, rule_value in enumerate(rules):
            rule = cls._record_value(rule_value)
            outbound_tag = cls._text_value(rule.get("outboundTag"))
            if not outbound_tag or not outbound_tag.startswith("tunnel-"):
                continue
            inbound_tag = cls._first_xray_rule_inbound_tag(rule.get("inboundTag"))
            source_inbound = inbound_by_tag.get(inbound_tag) if inbound_tag else None
            target_address = None
            target_port = None
            outbound = outbound_by_tag.get(outbound_tag)
            if outbound is not None:
                settings = cls._record_value(outbound.get("settings"))
                target_address, target_port = cls._xray_redirect_target(settings.get("redirect"))
            tunnels.append(
                XrayRuntimeTunnelRead(
                    kind="routed",
                    tag=outbound_tag,
                    listen_port=cls._port_value(source_inbound.get("port"))
                    if source_inbound
                    else None,
                    target_address=target_address,
                    target_port=target_port,
                    inbound_tag=inbound_tag,
                    match_domains=cls._text_list_value(rule.get("domain")),
                    match_ips=cls._text_list_value(rule.get("ip")),
                    rule_index=rule_index,
                )
            )

        chains, flat_tunnels = cls._group_xray_tunnel_chains(tunnels)
        return flat_tunnels, chains, cls._dedupe_text(warnings)

    @staticmethod
    def _xray_runtime_tunnel_delete_commands(
        inventory: XrayRuntimeTunnelInventoryResponse,
        payload: XrayRuntimeTunnelDeleteRequest,
    ) -> list[XrayRuntimeTunnelDeleteCommand]:
        if payload.kind == "chain":
            chain = next(
                (item for item in inventory.chains if item.label == payload.label),
                None,
            )
            if chain is None:
                raise XrayRuntimeTunnelNotFoundError(
                    f"runtime tunnel chain not found: {payload.label}"
                )
            return [
                XrayRuntimeTunnelDeleteCommand(
                    path="/api/child/inbounds",
                    body={"action": "remove", "tag": hop.tag},
                )
                for hop in chain.hops
            ]

        tunnel = next(
            (
                item
                for item in inventory.tunnels
                if item.kind == payload.kind and item.tag == payload.tag
            ),
            None,
        )
        if tunnel is None:
            raise XrayRuntimeTunnelNotFoundError(
                f"runtime {payload.kind} tunnel not found: {payload.tag}"
            )

        if payload.kind == "inbound":
            return [
                XrayRuntimeTunnelDeleteCommand(
                    path="/api/child/inbounds",
                    body={"action": "remove", "tag": tunnel.tag},
                )
            ]

        if tunnel.rule_index is None:
            raise XrayRuntimeTunnelNotFoundError(
                f"runtime routed tunnel has no routing rule index: {tunnel.tag}"
            )
        if payload.rule_index is not None and payload.rule_index != tunnel.rule_index:
            raise XrayRuntimeTunnelNotFoundError(
                f"runtime routed tunnel rule index changed: {tunnel.tag}"
            )
        return [
            XrayRuntimeTunnelDeleteCommand(
                path="/api/child/routing",
                body={"action": "remove_rule", "index": tunnel.rule_index},
            ),
            XrayRuntimeTunnelDeleteCommand(
                path="/api/child/outbounds",
                body={"action": "remove", "tag": tunnel.tag},
            ),
        ]

    @classmethod
    def _xray_config_snapshot_used_inbound_ports(
        cls,
        snapshot: XrayConfigSnapshotModel | None,
    ) -> tuple[set[int], list[str]]:
        if snapshot is None:
            return set(), ["current_config_snapshot_not_found"]
        try:
            config = json.loads(snapshot.config)
        except json.JSONDecodeError:
            return set(), ["invalid_config_json"]
        if not isinstance(config, dict):
            return set(), ["invalid_config_shape"]
        inbounds_value = config.get("inbounds")
        inbounds = cls._list_value(inbounds_value)
        warnings: list[str] = []
        if inbounds_value is not None and not isinstance(inbounds_value, list):
            warnings.append("invalid_inbounds")
        ports = {
            port
            for inbound in inbounds
            if isinstance(inbound, dict)
            if (port := cls._port_value(inbound.get("port"))) is not None
        }
        return ports, warnings

    @classmethod
    def _xray_config_snapshot_has_user_content(
        cls,
        snapshot: XrayConfigSnapshotModel,
    ) -> bool:
        try:
            config = json.loads(snapshot.config)
        except json.JSONDecodeError:
            return True
        if not isinstance(config, dict):
            return True
        for inbound in cls._list_value(config.get("inbounds")):
            if not isinstance(inbound, dict):
                continue
            tag = cls._text_value(inbound.get("tag")) or ""
            if tag not in {"api", "tunnel-in"}:
                return True
        for outbound in cls._list_value(config.get("outbounds")):
            if not isinstance(outbound, dict):
                continue
            tag = cls._text_value(outbound.get("tag")) or ""
            if tag and tag not in {"direct", "block", "nginx"}:
                return True
        return False

    @staticmethod
    def _normalize_runtime_domain(raw: str) -> str:
        value = raw.strip().lower()
        if not value:
            return ""
        if "://" in value:
            value = value.split("://", 1)[1]
        if "/" in value:
            value = value.split("/", 1)[0]
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        return value.strip(" .")

    @staticmethod
    def _cert_deploy_filename(domain: str) -> str:
        normalized = domain.strip().lower()
        if normalized.startswith("*."):
            return "_." + normalized[2:]
        return normalized

    @staticmethod
    def _render_tunnel_domain_config(
        site_type: Literal["static", "proxy"],
        site_value: str,
        domain: str,
        cert_name: str,
    ) -> str:
        template = (
            _TUNNEL_DOMAIN_PROXY_CONFIG if site_type == "proxy" else _TUNNEL_DOMAIN_STATIC_CONFIG
        )
        return (
            template.replace("{domain}", domain)
            .replace("{cert_name}", cert_name)
            .replace("{site_value}", site_value)
            .replace("{ssl_ciphers}", _TUNNEL_SSL_CIPHERS)
            .replace("{cors_headers}", _TUNNEL_CORS_HEADERS)
        )

    @staticmethod
    def _render_tunnel_xray_config(domain: str) -> dict[str, Any]:
        return {
            "log": {"loglevel": "error"},
            "dns": {},
            "api": {
                "tag": "api",
                "services": [
                    "HandlerService",
                    "LoggerService",
                    "StatsService",
                    "RoutingService",
                ],
            },
            "stats": {},
            "policy": {
                "levels": {
                    "0": {
                        "handshake": 5,
                        "connIdle": 300,
                        "uplinkOnly": 2,
                        "downlinkOnly": 2,
                        "statsUserUplink": True,
                        "statsUserDownlink": True,
                    }
                },
                "system": {
                    "statsInboundUplink": True,
                    "statsInboundDownlink": True,
                    "statsOutboundUplink": True,
                    "statsOutboundDownlink": True,
                },
            },
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {
                        "inboundTag": ["tunnel-in"],
                        "domain": [domain],
                        "outboundTag": "nginx",
                    },
                    {
                        "inboundTag": ["tunnel-in"],
                        "outboundTag": "direct",
                    },
                    {
                        "type": "field",
                        "inboundTag": ["api"],
                        "outboundTag": "api",
                    },
                    {
                        "type": "field",
                        "ip": ["geoip:private"],
                        "outboundTag": "block",
                    },
                ],
            },
            "inbounds": [
                {
                    "tag": "tunnel-in",
                    "port": 443,
                    "protocol": "tunnel",
                    "settings": {
                        "address": "127.0.0.1",
                        "port": _XRAY_TUNNEL_FORWARD_PORT,
                        "network": "tcp",
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["tls"],
                        "routeOnly": True,
                    },
                },
                {
                    "tag": "api",
                    "port": _XRAY_API_PORT,
                    "listen": "127.0.0.1",
                    "protocol": "tunnel",
                    "settings": {"address": "127.0.0.1"},
                },
            ],
            "outbounds": [
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
                {
                    "protocol": "freedom",
                    "settings": {
                        "redirect": "127.0.0.1:8001",
                        "domainStrategy": "UseIP",
                        "proxyProtocol": 1,
                    },
                    "tag": "nginx",
                },
            ],
            "metrics": {
                "tag": "Metrics",
                "listen": f"127.0.0.1:{_XRAY_METRICS_PORT}",
            },
        }

    @staticmethod
    def _server_entry_host(server: ServerModel) -> str:
        for value in [
            server.ip_address,
            server.domain,
            server.pull_address,
            server.ip_address_v6,
            server.domain_v6,
            server.pull_address_v6,
        ]:
            if value and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _pick_xray_tunnel_port(cls, used: set[int], wanted: int) -> int:
        if 0 < wanted <= 65535 and wanted not in used:
            return wanted
        return cls._next_free_xray_tunnel_port(used)

    @staticmethod
    def _next_free_xray_tunnel_port(used: set[int]) -> int:
        for port in range(20_000, 60_000):
            if port not in used:
                return port
        raise XrayRuntimeTunnelChainUnavailableError(
            "no free runtime tunnel port in range 20000-59999"
        )

    @classmethod
    def _group_xray_tunnel_chains(
        cls,
        tunnels: list[XrayRuntimeTunnelRead],
    ) -> tuple[list[XrayRuntimeTunnelChainRead], list[XrayRuntimeTunnelRead]]:
        grouped: dict[str, list[tuple[int, XrayRuntimeTunnelRead]]] = {}
        flat: list[XrayRuntimeTunnelRead] = []

        for tunnel in tunnels:
            chain_tag = cls._xray_tunnel_chain_tag(tunnel.tag) if tunnel.kind == "inbound" else None
            if chain_tag is None:
                flat.append(tunnel)
                continue
            label, hop_index = chain_tag
            grouped.setdefault(label, []).append((hop_index, tunnel))

        chains: list[XrayRuntimeTunnelChainRead] = []
        for label in sorted(grouped):
            hops = [
                XrayRuntimeTunnelHopRead(
                    tag=tunnel.tag,
                    listen_port=tunnel.listen_port,
                    target_address=tunnel.target_address,
                    target_port=tunnel.target_port,
                )
                for _hop_index, tunnel in sorted(grouped[label], key=lambda item: item[0])
            ]
            final_target = None
            if hops:
                last = hops[-1]
                final_target = cls._format_host_port(last.target_address, last.target_port)
            chains.append(
                XrayRuntimeTunnelChainRead(
                    label=label,
                    hops=hops,
                    entry_port=hops[0].listen_port if hops else None,
                    final_target=final_target,
                )
            )
        return chains, flat

    @staticmethod
    def _xray_tunnel_chain_tag(tag: str) -> tuple[str, int] | None:
        prefix = "tunnel-"
        if not tag.startswith(prefix):
            return None
        hop_marker = tag.rfind("-h")
        if hop_marker < len(prefix):
            return None
        label = tag[len(prefix) : hop_marker]
        hop_index = tag[hop_marker + 2 :]
        if not label or not hop_index.isdigit():
            return None
        return label, int(hop_index)

    @classmethod
    def _first_xray_rule_inbound_tag(cls, value: Any) -> str | None:
        if isinstance(value, str):
            return cls._text_value(value)
        for item in cls._list_value(value):
            text = cls._text_value(item)
            if text:
                return text
        return None

    @classmethod
    def _xray_redirect_target(cls, value: Any) -> tuple[str | None, int | None]:
        redirect = cls._text_value(value)
        if not redirect:
            return None, None
        if redirect.startswith("["):
            end = redirect.find("]")
            if end > 1:
                port = (
                    cls._port_value(redirect[end + 2 :])
                    if redirect[end + 1 : end + 2] == ":"
                    else None
                )
                return redirect[1:end], port
        if ":" not in redirect:
            return redirect, None
        host, port_value = redirect.rsplit(":", 1)
        port = cls._port_value(port_value)
        if port is None:
            return redirect, None
        return host.strip() or None, port

    @classmethod
    def _format_host_port(cls, address: str | None, port: int | None) -> str | None:
        if address is None and port is None:
            return None
        if address is None:
            return str(port)
        if port is None:
            return address
        host = f"[{address}]" if ":" in address and not address.startswith("[") else address
        return f"{host}:{port}"

    @classmethod
    def _port_value(cls, value: Any) -> int | None:
        port = cls._int_value(value)
        if port is None or port < 0 or port > 65535:
            return None
        return port

    @classmethod
    def _xray_runtime_inbound_read(
        cls,
        inbound: Any,
        index: int,
        inbound_stats: dict[str, Any] | None = None,
        user_stats: dict[str, Any] | None = None,
    ) -> XrayRuntimeInboundRead:
        inbound = cls._record_value(inbound)
        protocol = (cls._text_value(inbound.get("protocol")) or "unknown").lower()
        tag = cls._text_value(inbound.get("tag"))
        port = cls._int_value(inbound.get("port"))
        listen = cls._text_value(inbound.get("listen"))
        settings = cls._record_value(inbound.get("settings"))
        stream_settings = cls._record_value(inbound.get("streamSettings"))
        sniffing = cls._record_value(inbound.get("sniffing"))
        client_container = _XRAY_CLIENT_CONTAINER_BY_PROTOCOL.get(protocol)
        client_values = cls._list_value(settings.get(client_container)) if client_container else []
        client_records = [item for item in client_values if isinstance(item, dict)]
        user_emails = cls._client_email_list(client_records)
        remarks: list[str] = []

        if not tag:
            remarks.append("missing_tag")
        if protocol == "unknown":
            remarks.append("missing_protocol")
        elif client_container is None:
            remarks.append("unsupported_protocol")

        return XrayRuntimeInboundRead(
            source_index=index,
            tag=tag,
            display_name=tag or cls._generated_inbound_name(protocol, port, index),
            protocol=protocol,
            port=port,
            listen=listen,
            network=cls._text_value(stream_settings.get("network")),
            security=cls._text_value(stream_settings.get("security")),
            client_container=client_container,
            client_count=len(client_values),
            user_emails=user_emails,
            sniffing_enabled=cls._bool_value(sniffing.get("enabled")),
            sniffing_dest_override=cls._text_list_value(sniffing.get("destOverride")),
            sniffing_exclude_domains=cls._text_list_value(sniffing.get("excludeDomains")),
            traffic=cls._traffic_data_for_key(inbound_stats, tag),
            user_traffic=cls._traffic_data_for_keys(user_stats, user_emails),
            remarks=remarks,
        )

    @classmethod
    def _xray_runtime_node_draft(
        cls,
        session: Session,
        server: ServerModel,
        inbound: Any,
        index: int,
        host: str | None = None,
        extra_tags: Iterable[str | None] = (),
        payload: XrayRuntimeNodeCreateRequest | None = None,
    ) -> XrayRuntimeNodeDraft:
        runtime = cls._xray_runtime_inbound_read(inbound, index)
        inbound = cls._record_value(inbound)
        settings = cls._record_value(inbound.get("settings"))
        stream_settings = cls._record_value(inbound.get("streamSettings"))
        managed_protocol = _XRAY_MANAGED_NODE_PROTOCOLS.get(runtime.protocol)
        warnings = list(runtime.remarks)
        create_available = managed_protocol is not None

        if managed_protocol is None and "unsupported_protocol" not in warnings:
            warnings.append("unsupported_protocol")
        if runtime.port is None:
            create_available = False
            warnings.append("missing_port")
        if managed_protocol == "snell":
            options = cls._snell_runtime_options(settings)
            if options.get("v6Mode") == "unsafe-raw":
                create_available = False
                warnings.append("snell_unauthenticated_mode")
            users = cls._list_value(settings.get("users"))
            if any(cls._snell_runtime_options({"users": [user]}) != options for user in users):
                create_available = False
                warnings.append("snell_mixed_transport_options")

        protocol = managed_protocol or "unsupported"
        node_name = cls._runtime_node_name(server, runtime, payload.name if payload else None)
        draft = ManagedNodeCreate(
            name=node_name,
            server_id=UUID(server.id),
            protocol=protocol,
            node_type=ManagedNodeType.PHYSICAL,
            inbound_tag=runtime.tag,
            routed_outbound_tag=None,
            routed_rule_marktag=None,
            tag=runtime.protocol[:120] if runtime.protocol != "unknown" else None,
            tags=payload.tags
            if payload and payload.tags is not None
            else cls._runtime_node_tags(runtime, protocol, extra_tags),
            enabled=payload.enabled if payload else True,
            client_template=cls._runtime_node_client_template(runtime, settings, protocol),
            config=cls._runtime_node_config(
                server=server,
                runtime=runtime,
                settings=settings,
                stream_settings=stream_settings,
                protocol=protocol,
                node_name=node_name,
                host=(payload.host if payload else host),
                warnings=warnings,
            ),
        )
        existing = cls._existing_xray_runtime_node(
            session,
            server=server,
            source_tag=runtime.tag,
            source_display_name=runtime.display_name,
            protocol=protocol,
            port=runtime.port,
        )
        return XrayRuntimeNodeDraft(
            source_index=index,
            source_tag=runtime.tag,
            source_display_name=runtime.display_name,
            draft=draft,
            create_available=create_available,
            existing_node_id=UUID(existing.id) if existing else None,
            warnings=cls._dedupe_text(warnings),
        )

    @classmethod
    def _select_xray_runtime_inbound(
        cls,
        inbounds: list[Any],
        payload: XrayRuntimeNodeCreateRequest,
    ) -> tuple[Any, int]:
        if payload.source_index is not None:
            if payload.source_index < len(inbounds):
                return inbounds[payload.source_index], payload.source_index
            raise XrayRuntimeInboundNotFoundError(
                f"runtime inbound not found at index {payload.source_index}"
            )

        for index, inbound in enumerate(inbounds):
            runtime = cls._xray_runtime_inbound_read(inbound, index)
            if payload.inbound_tag and runtime.tag == payload.inbound_tag:
                return inbound, index
            if payload.display_name and runtime.display_name == payload.display_name:
                return inbound, index

        if payload.inbound_tag:
            raise XrayRuntimeInboundNotFoundError(
                f"runtime inbound not found: {payload.inbound_tag}"
            )
        if payload.display_name:
            raise XrayRuntimeInboundNotFoundError(
                f"runtime inbound not found: {payload.display_name}"
            )
        if len(inbounds) == 1:
            return inbounds[0], 0
        raise XrayRuntimeInboundNotFoundError("runtime inbound selector is required")

    @classmethod
    def _runtime_node_config(
        cls,
        server: ServerModel,
        runtime: XrayRuntimeInboundRead,
        settings: dict[str, Any],
        stream_settings: dict[str, Any],
        protocol: str,
        node_name: str,
        host: str | None,
        warnings: list[str],
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "name": node_name,
            "type": cls._proxy_type_for_protocol(protocol),
            "server": cls._text_value(host) or cls._server_subscription_host(server),
        }
        if runtime.port is not None:
            config["port"] = runtime.port
        network = cls._runtime_subscription_network(runtime.network)
        if network and not (protocol == "hysteria2" and network == "hysteria"):
            config["network"] = network
        security = (runtime.security or "").lower()
        if security in {"tls", "reality"}:
            config["tls"] = True
        server_name = cls._runtime_server_name(stream_settings)
        if server_name:
            config["sni"] = server_name
        alpn = cls._runtime_alpn(stream_settings)
        if alpn:
            config["alpn"] = alpn
        if security == "reality":
            reality_options = cls._runtime_reality_options(stream_settings)
            if reality_options:
                config["reality-opts"] = reality_options
            else:
                warnings.append("missing_reality_public_key")
        cls._add_runtime_transport_options(config, stream_settings)
        cls._add_protocol_runtime_options(config, settings, protocol)
        return config

    @classmethod
    def _runtime_node_client_template(
        cls,
        runtime: XrayRuntimeInboundRead,
        settings: dict[str, Any],
        protocol: str,
    ) -> dict[str, Any]:
        suffix = cls._safe_runtime_suffix(runtime.tag or runtime.display_name or protocol)
        template: dict[str, Any] = {"email": f"{{username}}__{suffix}"}
        container = _XRAY_CLIENT_CONTAINER_BY_PROTOCOL.get(runtime.protocol)
        clients = cls._list_value(settings.get(container)) if container else []
        client_records = [item for item in clients if isinstance(item, dict)]
        if protocol == "vless":
            flow = cls._first_text_client_value(client_records, "flow")
            if flow:
                template["flow"] = flow
        if protocol == "snell":
            template.update(cls._snell_runtime_options(settings))
        return template

    @classmethod
    def _snell_runtime_options(cls, settings: dict[str, Any]) -> dict[str, Any]:
        users = cls._list_value(settings.get("users"))
        # This core takes shared transport settings from its first user.
        source = cls._record_value(users[0]) if users else settings
        version = cls._int_value(source.get("version")) or 4
        if version == 6:
            return {
                "version": version,
                "v6Mode": cls._text_value(source.get("v6Mode")) or "default",
            }
        return {
            "version": version,
            "obfsMode": cls._text_value(source.get("obfsMode")) or "none",
            "obfsHost": cls._text_value(source.get("obfsHost")) or "",
        }

    @classmethod
    def _add_protocol_runtime_options(
        cls,
        config: dict[str, Any],
        settings: dict[str, Any],
        protocol: str,
    ) -> None:
        if protocol == "shadowsocks":
            clients = [
                item for item in cls._list_value(settings.get("clients")) if isinstance(item, dict)
            ]
            cipher = cls._text_value(
                settings.get("method") or settings.get("security")
            ) or cls._first_text_client_value(clients, "method")
            if cipher:
                config["cipher"] = cipher
                if cipher.startswith("2022-"):
                    config["server-key-source"] = "runtime"
            return
        if protocol == "snell":
            options = cls._snell_runtime_options(settings)
            config["version"] = options["version"]
            config["udp"] = True
            if options["version"] == 6:
                config["mode"] = options["v6Mode"]
            elif options["obfsMode"] != "none":
                config["obfs-opts"] = {"mode": options["obfsMode"]}
                if options["obfsHost"]:
                    config["obfs-opts"]["host"] = options["obfsHost"]
            return
        if protocol == "mieru":
            transport = cls._text_value(settings.get("transport"))
            config["transport"] = (transport or "TCP").upper()
            config["udp"] = False
            return
        if protocol in {"anytls", "hysteria2"}:
            config.setdefault("udp", True)

    @classmethod
    def _add_runtime_transport_options(
        cls,
        config: dict[str, Any],
        stream_settings: dict[str, Any],
    ) -> None:
        ws_settings = cls._record_value(
            stream_settings.get("wsSettings") or stream_settings.get("websocketSettings")
        )
        if ws_settings:
            ws_options: dict[str, Any] = {}
            path = cls._text_value(ws_settings.get("path"))
            if path:
                ws_options["path"] = path
            headers = dict(cls._record_value(ws_settings.get("headers")))
            host = cls._text_value(
                ws_settings.get("host") or headers.get("Host") or headers.get("host")
            )
            if host:
                headers["Host"] = host
            if headers:
                ws_options["headers"] = headers
            if ws_options:
                config["ws-opts"] = ws_options

        grpc_settings = cls._record_value(stream_settings.get("grpcSettings"))
        service_name = cls._text_value(grpc_settings.get("serviceName"))
        if service_name:
            config["grpc-opts"] = {"grpc-service-name": service_name}

        http_settings = cls._record_value(stream_settings.get("httpSettings"))
        hosts = cls._text_list_value(http_settings.get("host"))
        path = cls._text_value(http_settings.get("path"))
        if hosts or path:
            config["h2-opts"] = {}
            if hosts:
                config["h2-opts"]["host"] = hosts
            if path:
                config["h2-opts"]["path"] = path
        upgrade = cls._record_value(stream_settings.get("httpupgradeSettings"))
        if upgrade:
            config["http-upgrade-opts"] = {
                key: value for key, value in upgrade.items() if key in {"host", "path", "headers"}
            }

    @classmethod
    def _runtime_reality_options(cls, stream_settings: dict[str, Any]) -> dict[str, Any]:
        reality = cls._record_value(stream_settings.get("realitySettings"))
        options: dict[str, Any] = {}
        public_key = cls._text_value(reality.get("publicKey") or reality.get("public_key"))
        if public_key:
            options["public-key"] = public_key
        short_id = cls._text_value(reality.get("shortId") or reality.get("short_id"))
        if not short_id:
            short_ids = cls._text_list_value(reality.get("shortIds") or reality.get("short_ids"))
            short_id = short_ids[0] if short_ids else None
        if short_id:
            options["short-id"] = short_id
        fingerprint = cls._text_value(reality.get("fingerprint"))
        if fingerprint:
            options["fingerprint"] = fingerprint
        spider_x = cls._text_value(reality.get("spiderX") or reality.get("spider_x"))
        if spider_x:
            options["spider-x"] = spider_x
        return options

    @classmethod
    def _runtime_server_name(cls, stream_settings: dict[str, Any]) -> str | None:
        tls = cls._record_value(stream_settings.get("tlsSettings"))
        reality = cls._record_value(stream_settings.get("realitySettings"))
        server_name = cls._text_value(
            tls.get("serverName")
            or tls.get("server_name")
            or reality.get("serverName")
            or reality.get("server_name")
        )
        if server_name:
            return server_name
        server_names = cls._text_list_value(
            tls.get("serverNames")
            or tls.get("server_names")
            or reality.get("serverNames")
            or reality.get("server_names")
        )
        return server_names[0] if server_names else None

    @classmethod
    def _runtime_alpn(cls, stream_settings: dict[str, Any]) -> list[str]:
        tls = cls._record_value(stream_settings.get("tlsSettings"))
        reality = cls._record_value(stream_settings.get("realitySettings"))
        return cls._text_list_value(tls.get("alpn") or reality.get("alpn"))

    @staticmethod
    def _runtime_subscription_network(network: str | None) -> str | None:
        if not network:
            return None
        normalized = network.lower()
        if normalized == "websocket":
            return "ws"
        if normalized == "httpupgrade":
            return "httpupgrade"
        if normalized == "http":
            return "h2"
        return normalized

    @staticmethod
    def _runtime_node_name(
        server: ServerModel,
        runtime: XrayRuntimeInboundRead,
        override: str | None,
    ) -> str:
        value = override or f"{server.name} {runtime.display_name}"
        return value[:120].rstrip() or runtime.display_name[:120] or "Runtime node"

    @classmethod
    def _runtime_node_tags(
        cls,
        runtime: XrayRuntimeInboundRead,
        protocol: str,
        extra_tags: Iterable[str | None] = (),
    ) -> list[str]:
        return cls._dedupe_text(
            [
                "runtime",
                protocol,
                runtime.protocol,
                runtime.network,
                runtime.security,
                *extra_tags,
            ]
        )[:24]

    @staticmethod
    def _safe_runtime_suffix(value: str) -> str:
        suffix = "".join(
            char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value
        )
        return suffix.strip("_")[:80] or "runtime"

    @classmethod
    def _first_text_client_value(cls, clients: list[dict[str, Any]], key: str) -> str | None:
        for client in clients:
            value = cls._text_value(client.get(key))
            if value:
                return value
        return None

    @staticmethod
    def _dedupe_text(values: Iterable[str | None]) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not value:
                continue
            normalized = value.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(normalized)
        return items

    @classmethod
    def _existing_xray_runtime_node(
        cls,
        session: Session,
        server: ServerModel,
        source_tag: str | None,
        source_display_name: str,
        protocol: str,
        port: int | None = None,
    ) -> ManagedNodeModel | None:
        statement = select(ManagedNodeModel).where(
            ManagedNodeModel.server_id == server.id,
            ManagedNodeModel.protocol == protocol,
        )
        if source_tag:
            statement = statement.where(ManagedNodeModel.inbound_tag == source_tag)
            return session.scalar(statement.order_by(ManagedNodeModel.created_at))
        candidates = session.scalars(
            statement.where(ManagedNodeModel.inbound_tag.is_(None)).order_by(
                ManagedNodeModel.created_at
            )
        ).all()
        fallback_name = f"{server.name} {source_display_name}"[:120].rstrip()
        for candidate in candidates:
            if candidate.name == fallback_name:
                return candidate
        if port is None:
            return None
        for candidate in candidates:
            config = candidate.config if isinstance(candidate.config, dict) else {}
            if cls._int_value(config.get("port")) == port:
                return candidate
        return None

    @staticmethod
    def _runtime_reconciliation_entry(
        draft: XrayRuntimeNodeDraft,
        nodes_by_id: dict[str, ManagedNodeModel],
    ) -> XrayRuntimeNodeReconciliationRuntimeEntry:
        status: Literal["managed", "unmanaged", "unavailable"]
        managed_node = (
            nodes_by_id.get(str(draft.existing_node_id)) if draft.existing_node_id else None
        )
        if not draft.create_available:
            status = "unavailable"
        elif managed_node:
            status = "managed"
        else:
            status = "unmanaged"
        return XrayRuntimeNodeReconciliationRuntimeEntry(
            source_index=draft.source_index,
            source_tag=draft.source_tag,
            source_display_name=draft.source_display_name,
            protocol=draft.draft.tag or draft.draft.protocol,
            port=draft.draft.config.get("port")
            if isinstance(draft.draft.config.get("port"), int)
            else None,
            status=status,
            managed_node_id=UUID(managed_node.id) if managed_node else None,
            managed_node_name=managed_node.name if managed_node else None,
            warnings=draft.warnings,
        )

    @classmethod
    def _managed_node_reconciliation_entry(
        cls,
        node: ManagedNodeModel,
        draft: XrayRuntimeNodeDraft | None,
    ) -> XrayRuntimeNodeReconciliationManagedEntry:
        status: Literal["in_sync", "stale", "missing_runtime", "catalog_only"]
        drifts: list[XrayRuntimeNodeReconciliationDrift] = []
        if node.node_type != ManagedNodeType.PHYSICAL.value:
            status = "catalog_only"
        elif draft is None:
            status = "missing_runtime"
        else:
            drifts = cls._runtime_managed_node_drifts(draft.draft, node)
            status = "stale" if drifts else "in_sync"
        return XrayRuntimeNodeReconciliationManagedEntry(
            node_id=UUID(node.id),
            node_name=node.name,
            protocol=node.protocol,
            node_type=node.node_type,
            inbound_tag=node.inbound_tag,
            enabled=node.enabled,
            status=status,
            runtime_source_index=draft.source_index if draft else None,
            runtime_display_name=draft.source_display_name if draft else None,
            drifts=drifts,
        )

    @classmethod
    def _runtime_managed_node_drifts(
        cls,
        draft: ManagedNodeCreate,
        node: ManagedNodeModel,
    ) -> list[XrayRuntimeNodeReconciliationDrift]:
        managed_config = cls._record_value(node.config)
        managed_template = cls._record_value(node.client_template)
        comparisons = [
            ("protocol", draft.protocol, node.protocol),
            ("inbound_tag", draft.inbound_tag, node.inbound_tag),
            ("config.type", draft.config.get("type"), managed_config.get("type")),
            ("config.port", draft.config.get("port"), managed_config.get("port")),
            ("config.network", draft.config.get("network"), managed_config.get("network")),
            ("config.tls", draft.config.get("tls"), managed_config.get("tls")),
            ("config.sni", draft.config.get("sni"), managed_config.get("sni")),
            ("config.alpn", draft.config.get("alpn"), managed_config.get("alpn")),
            (
                "config.ws_path",
                cls._nested_config_value(draft.config, "ws-opts", "path"),
                cls._nested_config_value(managed_config, "ws-opts", "path"),
            ),
            (
                "config.grpc_service",
                cls._nested_config_value(draft.config, "grpc-opts", "grpc-service-name"),
                cls._nested_config_value(managed_config, "grpc-opts", "grpc-service-name"),
            ),
            (
                "config.http_path",
                cls._nested_config_value(draft.config, "http-opts", "path"),
                cls._nested_config_value(managed_config, "http-opts", "path"),
            ),
            ("config.cipher", draft.config.get("cipher"), managed_config.get("cipher")),
            (
                "client_template.flow",
                draft.client_template.get("flow"),
                managed_template.get("flow"),
            ),
            (
                "client_template.version",
                draft.client_template.get("version"),
                managed_template.get("version"),
            ),
            (
                "client_template.obfsMode",
                draft.client_template.get("obfsMode"),
                managed_template.get("obfsMode"),
            ),
            (
                "client_template.obfsHost",
                draft.client_template.get("obfsHost"),
                managed_template.get("obfsHost"),
            ),
        ]
        drifts: list[XrayRuntimeNodeReconciliationDrift] = []
        for field, runtime_value, managed_value in comparisons:
            runtime_public = cls._reconciliation_public_value(runtime_value)
            managed_public = cls._reconciliation_public_value(managed_value)
            if runtime_public == managed_public:
                continue
            drifts.append(
                XrayRuntimeNodeReconciliationDrift(
                    field=field,
                    runtime_value=runtime_public,
                    managed_value=managed_public,
                )
            )
        return drifts

    @classmethod
    def _runtime_node_sync_draft(
        cls,
        session: Session,
        server: ServerModel,
        node: ManagedNodeModel,
        scan: AgentScanResultModel,
        payload: XrayRuntimeNodeSyncRequest,
    ) -> XrayRuntimeNodeDraft:
        inbounds = scan.inbounds or []
        if payload.source_index is not None:
            inbound, index = cls._select_xray_runtime_inbound(
                inbounds,
                XrayRuntimeNodeCreateRequest(source_index=payload.source_index),
            )
            return cls._xray_runtime_node_draft(
                session=session,
                server=server,
                inbound=inbound,
                index=index,
            )
        for index, inbound in enumerate(inbounds):
            draft = cls._xray_runtime_node_draft(
                session=session,
                server=server,
                inbound=inbound,
                index=index,
            )
            if draft.existing_node_id and str(draft.existing_node_id) == node.id:
                return draft
        raise XrayRuntimeInboundNotFoundError(
            f"runtime inbound not found for managed node: {node.id}"
        )

    @classmethod
    def _sync_managed_node_public_runtime_fields(
        cls,
        node: ManagedNodeModel,
        draft: ManagedNodeCreate,
    ) -> list[str]:
        updated_fields: list[str] = []
        if node.protocol != draft.protocol:
            node.protocol = draft.protocol
            updated_fields.append("protocol")
        if node.inbound_tag != draft.inbound_tag:
            node.inbound_tag = draft.inbound_tag
            updated_fields.append("inbound_tag")

        next_config = dict(cls._record_value(node.config))
        for field in [
            "type",
            "port",
            "network",
            "tls",
            "sni",
            "alpn",
            "cipher",
        ]:
            if cls._sync_json_public_value(next_config, [field], draft.config.get(field)):
                updated_fields.append(f"config.{field}")
        for label, path in [
            ("config.ws_path", ["ws-opts", "path"]),
            ("config.grpc_service", ["grpc-opts", "grpc-service-name"]),
            ("config.http_path", ["http-opts", "path"]),
        ]:
            if cls._sync_json_public_value(
                next_config,
                path,
                cls._nested_config_value(draft.config, *path),
            ):
                updated_fields.append(label)
        if next_config != cls._record_value(node.config):
            node.config = next_config

        next_template = dict(cls._record_value(node.client_template))
        for field in ["flow", "version", "obfsMode", "obfsHost"]:
            if cls._sync_json_public_value(
                next_template,
                [field],
                draft.client_template.get(field),
            ):
                updated_fields.append(f"client_template.{field}")
        if next_template != cls._record_value(node.client_template):
            node.client_template = next_template

        return updated_fields

    @classmethod
    def _sync_json_public_value(
        cls,
        target: dict[str, Any],
        path: list[str],
        value: Any,
    ) -> bool:
        normalized = cls._reconciliation_public_value(value)
        current = target
        for key in path[:-1]:
            next_value = current.get(key)
            if normalized is None and not isinstance(next_value, dict):
                return False
            if not isinstance(next_value, dict):
                next_value = {}
                current[key] = next_value
            current = next_value
        key = path[-1]
        existing = cls._reconciliation_public_value(current.get(key))
        if existing == normalized:
            return False
        if normalized is None:
            current.pop(key, None)
        else:
            current[key] = value
        return True

    @staticmethod
    def _selected_physical_managed_nodes(
        session: Session,
        server: ServerModel,
        node_ids: list[UUID] | None,
    ) -> list[ManagedNodeModel]:
        nodes = session.scalars(
            select(ManagedNodeModel)
            .where(
                ManagedNodeModel.server_id == server.id,
                ManagedNodeModel.node_type == ManagedNodeType.PHYSICAL.value,
            )
            .order_by(ManagedNodeModel.created_at)
        ).all()
        if node_ids is None:
            return nodes
        nodes_by_id = {node.id: node for node in nodes}
        selected = []
        for node_id in node_ids:
            node = nodes_by_id.get(str(node_id))
            if not node:
                raise ManagedNodeNotFoundError(f"physical managed node not found: {node_id}")
            selected.append(node)
        return selected

    @classmethod
    def _runtime_inbounds_by_managed_node_id(
        cls,
        session: Session,
        server: ServerModel,
        scan: AgentScanResultModel | None,
    ) -> dict[str, XrayRuntimeInboundRead]:
        runtime_by_node_id: dict[str, XrayRuntimeInboundRead] = {}
        if not scan:
            return runtime_by_node_id
        for index, inbound in enumerate(scan.inbounds or []):
            draft = cls._xray_runtime_node_draft(
                session=session,
                server=server,
                inbound=inbound,
                index=index,
            )
            if not draft.existing_node_id:
                continue
            node_id = str(draft.existing_node_id)
            runtime_by_node_id.setdefault(
                node_id,
                cls._xray_runtime_inbound_read(inbound, index),
            )
        return runtime_by_node_id

    def _expected_runtime_credentials_by_node_id(
        self,
        session: Session,
        server: ServerModel,
        nodes: list[ManagedNodeModel],
    ) -> dict[str, list[ExpectedRuntimeCredential]]:
        contexts_by_node_id: dict[str, list[ExpectedRuntimeCredential]] = {
            node.id: [] for node in nodes
        }
        if not nodes:
            return contexts_by_node_id
        node_ids = [node.id for node in nodes]
        nodes_by_id = {node.id: node for node in nodes}
        users = session.scalars(
            select(ProductUserModel)
            .where(
                ProductUserModel.is_active.is_(True),
                ProductUserModel.current_plan_id.is_not(None),
            )
            .order_by(ProductUserModel.username)
        ).all()
        if not users:
            return contexts_by_node_id
        plan_ids = sorted({user.current_plan_id for user in users if user.current_plan_id})
        plans = session.scalars(
            select(SubscriptionPlanModel).where(SubscriptionPlanModel.id.in_(plan_ids))
        ).all()
        plans_by_id = {plan.id: plan for plan in plans}
        credentials = session.scalars(
            select(SubscriptionCredentialModel)
            .where(
                SubscriptionCredentialModel.server_id == server.id,
                SubscriptionCredentialModel.node_id.in_(node_ids),
            )
            .order_by(SubscriptionCredentialModel.created_at)
        ).all()
        credentials_by_user_node = {
            (credential.username, credential.node_id): credential for credential in credentials
        }
        now = datetime.now(tz=UTC)
        for user in users:
            plan = plans_by_id.get(user.current_plan_id or "")
            if not plan:
                continue
            quota = self._subscription_quota_status(session, user, plan, now)
            if not quota.available:
                continue
            for node_id in plan.node_ids or []:
                node = nodes_by_id.get(node_id)
                if not node or not node.enabled:
                    continue
                credential = credentials_by_user_node.get((user.username, node.id))
                email = credential.email if credential else self._default_client_email(user, node)
                contexts_by_node_id.setdefault(node.id, []).append(
                    ExpectedRuntimeCredential(
                        user=user,
                        plan=plan,
                        node=node,
                        credential=credential,
                        email=email,
                    )
                )
        return contexts_by_node_id

    @classmethod
    def _runtime_credential_reconciliation_entry(
        cls,
        node: ManagedNodeModel,
        runtime: XrayRuntimeInboundRead | None,
        expected_emails: list[str],
    ) -> XrayRuntimeCredentialReconciliationEntry:
        expected_emails = cls._dedupe_text(expected_emails)
        runtime_emails = cls._dedupe_text(runtime.user_emails if runtime else [])
        expected_keys = {email.lower() for email in expected_emails}
        runtime_keys = {email.lower() for email in runtime_emails}
        missing_runtime_emails = [
            email for email in expected_emails if email.lower() not in runtime_keys
        ]
        extra_runtime_emails = [
            email for email in runtime_emails if email.lower() not in expected_keys
        ]
        if runtime is None:
            status = "missing_runtime"
        elif missing_runtime_emails and extra_runtime_emails:
            status = "drift"
        elif missing_runtime_emails:
            status = "missing_runtime_clients"
        elif extra_runtime_emails:
            status = "extra_runtime_clients"
        else:
            status = "in_sync"
        return XrayRuntimeCredentialReconciliationEntry(
            node_id=UUID(node.id),
            node_name=node.name,
            protocol=node.protocol,
            inbound_tag=node.inbound_tag,
            enabled=node.enabled,
            runtime_source_index=runtime.source_index if runtime else None,
            runtime_display_name=runtime.display_name if runtime else None,
            expected_emails=expected_emails,
            runtime_emails=runtime_emails,
            missing_runtime_emails=missing_runtime_emails,
            extra_runtime_emails=extra_runtime_emails,
            status=status,
        )

    @staticmethod
    def _nested_config_value(config: dict[str, Any], *keys: str) -> Any:
        current: Any = config
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _reconciliation_public_value(value: Any) -> str | int | bool | list[str] | None:
        if value is None:
            return None
        if isinstance(value, bool | int | str):
            return value
        if isinstance(value, float):
            return int(value) if value.is_integer() else str(value)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items or None
        return None

    @staticmethod
    def _generated_inbound_name(protocol: str, port: int | None, index: int) -> str:
        if protocol != "unknown" and port is not None:
            return f"{protocol}-{port}"
        if protocol != "unknown":
            return protocol
        return f"inbound-{index + 1}"

    @staticmethod
    def _record_value(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _list_value(value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _text_value(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _int_value(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized.isdigit():
                return int(normalized)
        return None

    @staticmethod
    def _bool_value(value: Any) -> bool:
        return value if isinstance(value, bool) else False

    @classmethod
    def _text_list_value(cls, value: Any) -> list[str]:
        items = value if isinstance(value, list) else []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = cls._text_value(item)
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized

    @classmethod
    def _client_email_list(cls, clients: list[dict[str, Any]]) -> list[str]:
        emails: list[str] = []
        seen: set[str] = set()
        for client in clients:
            email = cls._text_value(client.get("email"))
            if not email:
                continue
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            emails.append(email)
        return emails

    @staticmethod
    def _scan_result_read(scan: AgentScanResultModel) -> AgentScanResultRead:
        return AgentScanResultRead(
            server_id=UUID(scan.server_id),
            nginx=scan.nginx,
            http01=scan.http01,
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
            depends_on_command_id=UUID(command.depends_on_command_id)
            if command.depends_on_command_id
            else None,
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

    def _claim_command_lease(self, session: Session, command: CommandModel, now: datetime) -> bool:
        if command.status != AgentCommandStatus.PENDING.value and (
            command.status != AgentCommandStatus.LEASED.value
            or not InventoryStore._lease_expired(command, now)
        ):
            return False
        if not self._change_sets().can_lease(session, command):
            return False
        if not self._subscription_access().can_lease(session, command, now):
            return False
        needs_limiter = command.path == "/api/child/limiter" or (
            command.path == "/api/child/batch-apply"
            and isinstance(command.body, dict)
            and command.body.get("limiter_users")
        )
        body = command.body if isinstance(command.body, dict) else {}
        raw_users = (
            body.get("users", [])
            if command.path == "/api/child/limiter"
            else body.get("limiter_users", [])
            if command.path == "/api/child/batch-apply"
            else []
        )
        rule_users = raw_users if isinstance(raw_users, list) else []
        if command.path == "/api/child/batch-apply":
            rule_users = [item.get("user", {}) for item in rule_users if isinstance(item, dict)]
        needs_user_rules = any(
            isinstance(user, dict) and user.get("auto_speed_rules") for user in rule_users
        )
        required_capabilities = [
            capability
            for capability, needed in (
                ("native_limiter", needs_limiter),
                ("user_auto_speed_rules", needs_user_rules),
                ("node_cleanup", command.path == "/api/child/node-cleanup"),
            )
            if needed
        ]
        for required_capability in required_capabilities:
            agent = session.scalar(
                select(AgentModel).where(AgentModel.server_id == command.server_id)
            )
            if not agent or not getattr(agent, "capability_" + required_capability):
                command.result_error = (
                    "Not sent: this command requires an Open Node Agent with "
                    + required_capability.replace("_", " ")
                    + " support"
                )
                command.updated_at = now
                if command.attempts == 0:
                    command.status = AgentCommandStatus.SKIPPED.value
                    command.result_status = 501
                    command.completed_at = now
                    self._advance_command_dependents(session, command, now)
                    self._change_sets().advance_after_result(session, command, now)
                return False
        try:
            AgentCommandCreate(
                method=command.method,
                path=command.path,
                query=command.query,
                body=command.body,
                timeout_ms=command.timeout_ms,
                stream=command.stream,
            ).validate_wire_payload()
        except AgentCommandPayloadError as exc:
            command.result_error = f"Not sent: {exc}"
            command.updated_at = now
            if command.attempts == 0:
                command.status = AgentCommandStatus.SKIPPED.value
                command.completed_at = now
                self._advance_command_dependents(session, command, now)
                self._change_sets().advance_after_result(session, command, now)
            return False
        # Both transports must claim the same persisted version before dispatching.
        result = session.execute(
            update(CommandModel)
            .where(
                CommandModel.id == command.id,
                CommandModel.status == command.status,
                CommandModel.attempts == command.attempts,
            )
            .values(
                status=AgentCommandStatus.LEASED.value,
                attempts=CommandModel.attempts + 1,
                leased_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def _apply_command_result(
        self,
        session: Session,
        server: ServerModel,
        command: CommandModel,
        payload: AgentCommandResultRequest,
    ) -> None:
        if command.status in {
            AgentCommandStatus.SUCCEEDED.value,
            AgentCommandStatus.FAILED.value,
            AgentCommandStatus.SKIPPED.value,
        }:
            return
        if command.status == AgentCommandStatus.WAITING.value:
            raise CommandNotReadyError(f"command is waiting for prerequisite: {command.id}")
        self._change_sets().validate_result(session, command)
        now = datetime.now(tz=UTC)
        result_error = payload.error
        body = payload.body if isinstance(payload.body, dict) else {}
        if not result_error and body.get("success") is False:
            result_error = "Agent reported an unsuccessful result"
        if not result_error and payload.status < 400:
            result_error = self._subscription_access().confirmation_error(command, body)
        if not result_error and payload.status < 400:
            from open_node.services.node_cleanup import confirmation_error

            result_error = confirmation_error(command, body)
        if (
            not result_error
            and payload.status < 400
            and command.path == "/api/child/batch-apply"
            and isinstance(command.body, dict)
            and command.body.get("limiter_users")
        ):
            confirmation = body.get("limiter")
            requested_limits = any(
                not isinstance(item, dict)
                or not isinstance(item.get("user"), dict)
                or item["user"].get("speed_limit", 0) != 0
                or item["user"].get("device_limit", 0) != 0
                or bool(item["user"].get("auto_speed_rules"))
                for item in command.body["limiter_users"]
            )
            revision = confirmation.get("revision") if isinstance(confirmation, dict) else None
            if (
                not isinstance(confirmation, dict)
                or confirmation.get("applied") is not True
                or (
                    not (isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{64}", revision))
                    and (requested_limits or confirmation.get("unlimited") is not True)
                )
            ):
                result_error = "Agent did not confirm native limiter enforcement"
        if (
            not result_error
            and command.path == "/api/child/xray/test-config"
            and body.get("ok") is not True
        ):
            result_error = "Xray configuration validation did not return ok=true"
        result_status = (
            AgentCommandStatus.FAILED.value
            if result_error or payload.status >= 400
            else AgentCommandStatus.SUCCEEDED.value
        )
        result = session.execute(
            update(CommandModel)
            .where(
                CommandModel.id == command.id,
                CommandModel.status == command.status,
                CommandModel.attempts == command.attempts,
            )
            .values(
                status=result_status,
                result_status=payload.status,
                result_body=payload.body,
                result_error=result_error,
                completed_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        session.refresh(command)
        if result.rowcount != 1:
            return
        server.last_heartbeat = now
        server.updated_at = now
        if (
            command.status == AgentCommandStatus.SUCCEEDED.value
            and command.path in {"/api/child/agent/uninstall", "/api/child/agent/uninstall-stream"}
            and body.get("installation_status") == "removed"
        ):
            server.status = ServerStatus.OFFLINE.value
        self._advance_command_dependents(session, command, now)
        if command.status != AgentCommandStatus.FAILED.value:
            self._upsert_return_route_results(session, server, command, payload, now)
            self._record_domain_latency_result(session, server, command, payload, now)
            self._record_scan_result_from_command(session, server, command, payload, now)
            self._record_xray_config_snapshot_from_command(session, server, command, payload, now)
            self._queue_xray_snapshot_refresh_after_mutation(session, server, command, payload, now)
        self._change_sets().advance_after_result(session, command, now)
        self._subscription_access().after_result(session, command, now)

    @staticmethod
    def _advance_command_dependents(session: Session, command: CommandModel, now: datetime) -> None:
        predecessors = [command]
        while predecessors:
            predecessor = predecessors.pop()
            children = session.scalars(
                select(CommandModel).where(
                    CommandModel.depends_on_command_id == predecessor.id,
                    CommandModel.status == AgentCommandStatus.WAITING.value,
                )
            ).all()
            for child in children:
                child.updated_at = now
                if predecessor.status == AgentCommandStatus.SUCCEEDED.value:
                    child.status = AgentCommandStatus.PENDING.value
                else:
                    child.status = AgentCommandStatus.SKIPPED.value
                    child.completed_at = now
                    child.result_error = (
                        f"Skipped because prerequisite {predecessor.id} did not succeed"
                    )
                    predecessors.append(child)

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
        if source == XrayConfigSnapshotSource.AGENT_REPORT:
            self._upsert_agent_report_xray_config_snapshot(
                session,
                server,
                config=config,
                source_command_id=command.id,
                created_at=created_at,
            )
            return

        self._upsert_current_xray_config_snapshot(
            session,
            server,
            config=config,
            source=source,
            source_command_id=command.id,
            created_at=created_at,
        )
        self._discard_pending_xray_recovery(session, server.id)

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
