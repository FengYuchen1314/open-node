import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


def _strip_required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is empty")
    if any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{field_name} must not contain control characters")
    return normalized


def _validate_required_content(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} is empty")
    allowed_controls = {"\n", "\r", "\t"}
    if any(ord(char) < 32 and char not in allowed_controls for char in value):
        raise ValueError(f"{field_name} must not contain unsafe control characters")
    return value


def _strip_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _strip_required_text(value, field_name)


def _ensure_json_serializable_config(value: Any, field_name: str) -> Any:
    if isinstance(value, str):
        return _validate_required_content(value, field_name)

    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc
    return value


def _validate_xray_config_file(value: str) -> str:
    normalized = _strip_required_text(value, "file")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError("file must be a config filename, not a path")
    return normalized


def _validate_log_file_name(value: str) -> str:
    normalized = _strip_required_text(value, "name")
    if (
        "/" in normalized
        or "\\" in normalized
        or ".." in normalized
        or normalized in {".", ".."}
    ):
        raise ValueError("name must be a log filename, not a path")
    return normalized


def _validate_agent_url(value: str) -> str:
    normalized = _strip_required_text(value, "master_url").rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("master_url must be a valid HTTP(S) URL")
    return normalized


def _strip_lower_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().lower()
    return value


class ConnectionMode(StrEnum):
    AUTO = "auto"
    WEBSOCKET = "websocket"
    HTTP = "http"
    PULL = "pull"


class ServerStatus(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    OFFLINE = "offline"


class TrafficSource(StrEnum):
    XRAY = "xray"
    SYSTEM = "system"


class TrafficStatsMode(StrEnum):
    BOTH = "both"
    UPLOAD = "upload"
    DOWNLOAD = "download"


class XrayMode(StrEnum):
    EXTERNAL = "external"
    EMBEDDED = "embedded"


class RenewalCycle(StrEnum):
    MONTH = "month"
    QUARTER = "quarter"
    HALF_YEAR = "half_year"
    YEAR = "year"


class AgentServiceName(StrEnum):
    XRAY = "xray"
    NGINX = "nginx"


class AgentServiceAction(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"


class AgentLogService(StrEnum):
    AGENT = "agent"
    XRAY = "xray"
    NGINX = "nginx"


class AgentCommandStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class XrayConfigSnapshotStatus(StrEnum):
    CURRENT = "current"
    OLD = "old"
    PENDING_RECOVERY = "pending_recovery"


class XrayConfigSnapshotSource(StrEnum):
    AGENT_REPORT = "agent_report"
    MASTER_WRITE = "master_write"
    MANUAL_ACCEPT = "manual_accept"


class AgentOperationKind(StrEnum):
    SYSTEM_INFO = "system_info"
    TRAFFIC = "traffic"
    SPEED = "speed"
    DOMAIN_LATENCY = "domain_latency"
    INBOUNDS_LIST = "inbounds_list"
    INBOUNDS_MANAGE = "inbounds_manage"
    OUTBOUNDS_LIST = "outbounds_list"
    OUTBOUNDS_MANAGE = "outbounds_manage"
    ROUTING_READ = "routing_read"
    ROUTING_MANAGE = "routing_manage"
    BATCH_APPLY = "batch_apply"
    CERT_DEPLOY = "cert_deploy"
    NGINX_SETUP_SSL = "nginx_setup_ssl"
    NGINX_SERVERS_LIST = "nginx_servers_list"
    NGINX_WEBSITES_LIST = "nginx_websites_list"
    NGINX_WEBSITE_DELETE = "nginx_website_delete"
    RETURN_ROUTE_TEST = "return_route_test"
    VALIDATE_SITE = "validate_site"
    LIMITER = "limiter"
    SERVICES_STATUS = "services_status"
    SERVICE_CONTROL = "service_control"
    SYSTEM_NICS = "system_nics"
    LOGS = "logs"
    LOG_FILES_LIST = "log_files_list"
    LOG_FILES_DELETE = "log_files_delete"
    SCAN = "scan"
    XRAY_TEST_CONFIG = "xray_test_config"
    XRAY_CONFIG_READ = "xray_config_read"
    XRAY_CONFIG_WRITE = "xray_config_write"
    XRAY_SYSTEM_CONFIG_READ = "xray_system_config_read"
    XRAY_SYSTEM_CONFIG_WRITE = "xray_system_config_write"
    XRAY_CONFIG_FILES_LIST = "xray_config_files_list"
    XRAY_CONFIG_FILE_READ = "xray_config_file_read"
    XRAY_CONFIG_FILE_WRITE = "xray_config_file_write"
    XRAY_TAKEOVER_EXTERNAL = "xray_takeover_external"
    XRAY_INSTALL_LEGACY = "xray_install_legacy"
    XRAY_REMOVE_LEGACY = "xray_remove_legacy"
    XRAY_INSTALL = "xray_install"
    XRAY_REMOVE = "xray_remove"
    NGINX_CONFIG_READ = "nginx_config_read"
    NGINX_CONFIG_WRITE = "nginx_config_write"
    NGINX_CONFIG_FILES_LIST = "nginx_config_files_list"
    NGINX_CONFIG_FILE_READ = "nginx_config_file_read"
    NGINX_CONFIG_FILE_WRITE = "nginx_config_file_write"
    NGINX_INSTALL_LEGACY = "nginx_install_legacy"
    NGINX_REMOVE_LEGACY = "nginx_remove_legacy"
    NGINX_INSTALL = "nginx_install"
    NGINX_REMOVE = "nginx_remove"
    NGINX_CLEAR_STREAM_PORT = "nginx_clear_stream_port"
    WARP_INSTALL = "warp_install"
    WARP_STATUS = "warp_status"
    WARP_LICENSE = "warp_license"
    WARP_REMOVE = "warp_remove"
    AGENT_SWITCH_XRAY_MODE = "agent_switch_xray_mode"
    AGENT_SWITCH_LISTEN_PORT = "agent_switch_listen_port"
    AGENT_PROBE_MASTER_URL = "agent_probe_master_url"
    AGENT_UPDATE_MASTER_URL = "agent_update_master_url"
    AGENT_UPGRADE = "agent_upgrade"
    AGENT_UNINSTALL = "agent_uninstall"


class AgentCapabilities(BaseModel):
    rpc: bool = False
    stream: bool = False
    return_route_test: bool = False


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ip_address: str | None = Field(default=None, max_length=255)
    ip_address_v6: str | None = Field(default=None, max_length=255)
    domain: str | None = Field(default=None, max_length=255)
    domain_v6: str | None = Field(default=None, max_length=255)
    connection_mode: ConnectionMode = ConnectionMode.AUTO
    listen_port: int = Field(default=23889, ge=0, le=65535)
    pull_address: str | None = Field(default=None, max_length=255)
    pull_address_v6: str | None = Field(default=None, max_length=255)
    pull_port: int = Field(default=0, ge=0, le=65535)
    ipv6_enabled: bool = True
    traffic_limit: int = Field(default=0, ge=0)
    traffic_stats_mode: TrafficStatsMode = TrafficStatsMode.BOTH
    traffic_source: TrafficSource = TrafficSource.XRAY
    xray_mode: XrayMode = XrayMode.EXTERNAL
    region: str | None = Field(default=None, max_length=120)
    region_country: str | None = Field(default=None, max_length=120)
    region_name: str | None = Field(default=None, max_length=120)
    region_city: str | None = Field(default=None, max_length=120)
    provider_name: str | None = Field(default=None, max_length=120)
    provider_url: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    renewal_price: float | None = Field(default=None, ge=0)
    renewal_price_cny: float | None = Field(default=None, ge=0)
    renewal_cycle: RenewalCycle | None = None
    renewal_currency: str | None = Field(default=None, max_length=12)
    telecom_paid_peer: bool | None = None

    @field_validator(
        "region",
        "region_country",
        "region_name",
        "region_city",
        "provider_name",
        "renewal_currency",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "server metadata") or None

    @field_validator("provider_url")
    @classmethod
    def validate_provider_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _strip_optional_text(value, "provider_url")
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("provider_url must be a valid HTTP(S) URL")
        return normalized


class ServerProbeMetadataUpdate(BaseModel):
    region: str | None = Field(default=None, max_length=120)
    region_country: str | None = Field(default=None, max_length=120)
    region_name: str | None = Field(default=None, max_length=120)
    region_city: str | None = Field(default=None, max_length=120)
    provider_name: str | None = Field(default=None, max_length=120)
    provider_url: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None
    renewal_price: float | None = Field(default=None, ge=0)
    renewal_price_cny: float | None = Field(default=None, ge=0)
    renewal_cycle: RenewalCycle | None = None
    renewal_currency: str | None = Field(default=None, max_length=12)
    telecom_paid_peer: bool | None = None

    @field_validator(
        "region",
        "region_country",
        "region_name",
        "region_city",
        "provider_name",
        "renewal_currency",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "server metadata") or None

    @field_validator("provider_url")
    @classmethod
    def validate_provider_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _strip_optional_text(value, "provider_url")
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("provider_url must be a valid HTTP(S) URL")
        return normalized


class ServerRead(BaseModel):
    id: UUID
    name: str
    status: ServerStatus
    ip_address: str | None = None
    ip_address_v6: str | None = None
    domain: str | None = None
    domain_v6: str | None = None
    connection_mode: ConnectionMode
    listen_port: int
    pull_address: str | None = None
    pull_address_v6: str | None = None
    pull_port: int
    ipv6_enabled: bool
    traffic_limit: int
    traffic_stats_mode: TrafficStatsMode
    traffic_source: TrafficSource
    xray_mode: XrayMode
    region: str | None = None
    region_country: str | None = None
    region_name: str | None = None
    region_city: str | None = None
    provider_name: str | None = None
    provider_url: str | None = None
    expires_at: datetime | None = None
    renewal_price: float | None = None
    renewal_price_cny: float | None = None
    renewal_cycle: RenewalCycle | None = None
    renewal_currency: str | None = None
    telecom_paid_peer: bool | None = None
    current_upload_speed: int = 0
    current_download_speed: int = 0
    last_heartbeat: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ServerRecord(ServerRead):
    agent_token: str


class ServerCreateResponse(BaseModel):
    server: ServerRead
    agent_token: str
    license_required: Literal[False] = False


class ServerResponse(BaseModel):
    server: ServerRead
    license_required: Literal[False] = False


class AgentRegistrationRequest(BaseModel):
    token: str = Field(min_length=1)
    hostname: str = Field(min_length=1, max_length=255)
    agent_version: str | None = Field(default=None, max_length=80)
    connection_mode: ConnectionMode = ConnectionMode.AUTO
    listen_port: int = Field(default=23889, ge=0, le=65535)
    public_ipv4: str | None = Field(default=None, max_length=255)
    public_ipv6: str | None = Field(default=None, max_length=255)
    xray_mode: XrayMode = XrayMode.EXTERNAL
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    warp_installed: bool = False
    same_host_as_master: bool | None = None


class AgentHeartbeatRequest(BaseModel):
    token: str = Field(min_length=1)
    upload_speed: int = Field(default=0, ge=0)
    download_speed: int = Field(default=0, ge=0)
    listen_port: int | None = Field(default=None, ge=0, le=65535)
    public_ipv4: str | None = Field(default=None, max_length=255)
    public_ipv6: str | None = Field(default=None, max_length=255)


class TrafficData(BaseModel):
    uplink: int = Field(default=0, ge=0)
    downlink: int = Field(default=0, ge=0)


class XrayStats(BaseModel):
    inbound: dict[str, TrafficData] = Field(default_factory=dict)
    outbound: dict[str, TrafficData] = Field(default_factory=dict)
    user: dict[str, TrafficData] = Field(default_factory=dict)


class SystemTraffic(BaseModel):
    rx_total: int = Field(ge=0)
    tx_total: int = Field(ge=0)
    boot_time_unix: int = Field(ge=0)


class ProbeSysMetrics(BaseModel):
    cpu_pct: float = Field(default=0, ge=0)
    loadavg: str = ""
    mem_used: int = Field(default=0, ge=0)
    mem_total: int = Field(default=0, ge=0)
    disk_used: int = Field(default=0, ge=0)
    disk_total: int = Field(default=0, ge=0)
    uptime: int = Field(default=0, ge=0)
    cpu_model: str = ""
    cpu_cores: int = Field(default=0, ge=0)
    cpu_threads: int = Field(default=0, ge=0)
    os: str = ""
    kernel: str = ""
    arch: str = ""
    has_cpu: bool = False
    has_mem: bool = False
    has_disk: bool = False


class ProbeLatencySample(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    success: bool
    latency_ms: int = Field(default=0, ge=0)
    at: int | None = Field(default=None, ge=0)


class AgentTelemetryReport(BaseModel):
    token: str = Field(min_length=1)
    reported_at: datetime | None = None
    stats: XrayStats | None = None
    online_users: dict[str, list[str]] = Field(default_factory=dict)
    user_speeds: dict[str, int] = Field(default_factory=dict)
    conn_counts: dict[str, int] = Field(default_factory=dict)
    system: SystemTraffic | None = None
    sysmetrics: ProbeSysMetrics | None = None
    latency: list[ProbeLatencySample] = Field(default_factory=list)


class AgentTelemetryRead(BaseModel):
    id: UUID
    server_id: UUID
    reported_at: datetime
    received_at: datetime
    stats: XrayStats | None = None
    online_users: dict[str, list[str]] = Field(default_factory=dict)
    user_speeds: dict[str, int] = Field(default_factory=dict)
    conn_counts: dict[str, int] = Field(default_factory=dict)
    system: SystemTraffic | None = None
    sysmetrics: ProbeSysMetrics | None = None
    latency: list[ProbeLatencySample] = Field(default_factory=list)


class AgentScanResultPayload(BaseModel):
    xray_running: bool = False
    xray_version: str | None = Field(default=None, max_length=120)
    api_port: int | None = Field(default=None, ge=0, le=65535)
    config_path: str | None = Field(default=None, max_length=512)
    inbounds: list[dict[str, Any]] = Field(default_factory=list, max_length=1000)
    device_kicks: dict[str, int] = Field(default_factory=dict)
    config_modified: bool = False
    config_added_sections: list[str] = Field(default_factory=list, max_length=100)
    message: str | None = Field(default=None, max_length=2048)

    @field_validator("xray_version", "config_path", "message")
    @classmethod
    def validate_optional_scan_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "scan field") or None

    @field_validator("inbounds")
    @classmethod
    def validate_inbounds(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("inbound entries must be objects")
            normalized.append(_ensure_json_serializable_config(item, "inbound"))
        return normalized

    @field_validator("device_kicks")
    @classmethod
    def validate_device_kicks(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for raw_key, count in value.items():
            key = _strip_required_text(raw_key, "device kick key")
            if count < 0:
                raise ValueError("device kick counts must be non-negative")
            normalized[key] = count
        return normalized

    @field_validator("config_added_sections")
    @classmethod
    def validate_added_sections(cls, value: list[str]) -> list[str]:
        sections: list[str] = []
        seen: set[str] = set()
        for raw_section in value:
            section = _strip_required_text(raw_section, "config section")
            if section in seen:
                continue
            seen.add(section)
            sections.append(section)
        return sections


class AgentScanResultReport(AgentScanResultPayload):
    token: str = Field(min_length=1)
    reported_at: datetime | None = None


class AgentScanResultRead(AgentScanResultPayload):
    server_id: UUID
    reported_at: datetime
    updated_at: datetime


class AgentRead(BaseModel):
    id: UUID
    server_id: UUID
    hostname: str
    agent_version: str | None = None
    connection_mode: ConnectionMode
    listen_port: int
    public_ipv4: str | None = None
    public_ipv6: str | None = None
    xray_mode: XrayMode
    capabilities: AgentCapabilities
    warp_installed: bool
    same_host_as_master: bool | None = None
    registered_at: datetime
    last_seen_at: datetime


class AgentRegistrationResponse(BaseModel):
    agent: AgentRead
    server: ServerRead
    license_required: Literal[False] = False


class AgentHeartbeatResponse(BaseModel):
    server: ServerRead
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    license_required: Literal[False] = False


class AgentTelemetryResponse(BaseModel):
    server: ServerRead
    telemetry: AgentTelemetryRead
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    license_required: Literal[False] = False


class ServerTelemetryResponse(BaseModel):
    server_id: UUID
    latest: AgentTelemetryRead | None = None
    license_required: Literal[False] = False


class ServerScanResultResponse(BaseModel):
    server_id: UUID
    scan: AgentScanResultRead | None = None
    license_required: Literal[False] = False


class XrayRuntimeInboundRead(BaseModel):
    source_index: int = Field(ge=0)
    tag: str | None = None
    display_name: str
    protocol: str
    port: int | None = None
    listen: str | None = None
    network: str | None = None
    security: str | None = None
    client_container: str | None = None
    client_count: int = 0
    user_emails: list[str] = Field(default_factory=list)
    sniffing_enabled: bool = False
    sniffing_dest_override: list[str] = Field(default_factory=list)
    sniffing_exclude_domains: list[str] = Field(default_factory=list)
    traffic: TrafficData = Field(default_factory=TrafficData)
    user_traffic: TrafficData = Field(default_factory=TrafficData)
    remarks: list[str] = Field(default_factory=list)


class XrayRuntimeInventoryResponse(BaseModel):
    server_id: UUID
    has_scan: bool = False
    xray_running: bool = False
    xray_version: str | None = None
    api_port: int | None = None
    config_path: str | None = None
    config_modified: bool = False
    config_added_sections: list[str] = Field(default_factory=list)
    message: str | None = None
    inbound_count: int = 0
    client_count: int = 0
    protocol_counts: dict[str, int] = Field(default_factory=dict)
    traffic: TrafficData = Field(default_factory=TrafficData)
    user_traffic: TrafficData = Field(default_factory=TrafficData)
    traffic_reported_at: datetime | None = None
    inbounds: list[XrayRuntimeInboundRead] = Field(default_factory=list)
    reported_at: datetime | None = None
    updated_at: datetime | None = None
    license_required: Literal[False] = False


class XrayRuntimeTunnelRead(BaseModel):
    kind: Literal["inbound", "routed"]
    tag: str
    listen_port: int | None = Field(default=None, ge=0, le=65535)
    target_address: str | None = None
    target_port: int | None = Field(default=None, ge=0, le=65535)
    network: str | None = None
    inbound_tag: str | None = None
    match_domains: list[str] = Field(default_factory=list)
    match_ips: list[str] = Field(default_factory=list)
    rule_index: int | None = Field(default=None, ge=0)


class XrayRuntimeTunnelHopRead(BaseModel):
    tag: str
    listen_port: int | None = Field(default=None, ge=0, le=65535)
    target_address: str | None = None
    target_port: int | None = Field(default=None, ge=0, le=65535)


class XrayRuntimeTunnelChainRead(BaseModel):
    label: str
    hops: list[XrayRuntimeTunnelHopRead] = Field(default_factory=list)
    entry_port: int | None = Field(default=None, ge=0, le=65535)
    final_target: str | None = None


class XrayRuntimeTunnelInventoryResponse(BaseModel):
    server_id: UUID
    has_config: bool = False
    source_snapshot_id: UUID | None = None
    tunnel_count: int = 0
    chain_count: int = 0
    tunnels: list[XrayRuntimeTunnelRead] = Field(default_factory=list)
    chains: list[XrayRuntimeTunnelChainRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


class XrayConfigSnapshotRead(BaseModel):
    id: UUID
    server_id: UUID
    source_command_id: UUID | None = None
    config_hash: str
    source: XrayConfigSnapshotSource
    status: XrayConfigSnapshotStatus
    size_bytes: int
    config: str | None = None
    created_at: datetime


class ServerXrayConfigSnapshotsResponse(BaseModel):
    server_id: UUID
    snapshots: list[XrayConfigSnapshotRead]
    license_required: Literal[False] = False


class AgentCommandCreate(BaseModel):
    method: str = Field(default="GET", max_length=12)
    path: str = Field(min_length=1, max_length=255)
    query: str = Field(default="", max_length=2048)
    body: Any = None
    timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)
    stream: bool = False

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        normalized = value.upper()
        allowed = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if normalized not in allowed:
            raise ValueError(f"method must be one of {', '.join(sorted(allowed))}")
        return normalized

    @field_validator("path")
    @classmethod
    def validate_child_path(cls, value: str) -> str:
        if not value.startswith("/api/child/"):
            raise ValueError("path must target an agent /api/child/ endpoint")
        return value


class AgentCommandRead(BaseModel):
    id: UUID
    server_id: UUID
    request_id: str
    method: str
    path: str
    query: str = ""
    body: Any = None
    timeout_ms: int
    stream: bool
    status: AgentCommandStatus
    attempts: int
    result_status: int | None = None
    result_body: Any = None
    result_error: str | None = None
    created_at: datetime
    leased_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class AgentCommandStreamFrameRead(BaseModel):
    id: UUID
    command_id: UUID
    server_id: UUID
    request_id: str
    sequence: int
    data: str
    received_at: datetime


class AgentCommandCreateResponse(BaseModel):
    command: AgentCommandRead
    license_required: Literal[False] = False


class AgentDomainLatencyProbeRequest(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=200)
    timeout_ms: int = Field(default=2_000, ge=200, le=10_000)
    allow_icmp: bool = False
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        domains: list[str] = []
        seen: set[str] = set()
        for raw in value:
            normalized = cls._normalize_domain(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            domains.append(normalized)

        if not domains:
            raise ValueError("at least one domain target is required")
        return domains

    @staticmethod
    def _normalize_domain(raw: str) -> str:
        value = raw.strip()
        if not value:
            return ""
        if "://" in value:
            value = value.split("://", 1)[1]
        if "/" in value:
            value = value.split("/", 1)[0]
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
        return value


class AgentNginxInstallOperationRequest(BaseModel):
    domain: str | None = Field(default=None, max_length=255)
    command_timeout_ms: int = Field(default=300_000, ge=1_000, le=300_000)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = AgentDomainLatencyProbeRequest._normalize_domain(value)
        return normalized or None


class AgentServiceControlOperationRequest(BaseModel):
    service: AgentServiceName
    action: AgentServiceAction


class AgentLogsOperationRequest(BaseModel):
    service: AgentLogService = AgentLogService.AGENT
    lines: int = Field(default=200, ge=1, le=2000)


class AgentLogFilesDeleteOperationRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    all: bool = False
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_log_file_name(value)

    @model_validator(mode="after")
    def require_name_or_all(self) -> Self:
        if not self.all and not self.name:
            raise ValueError("name is required unless all is true")
        return self


class AgentInboundsManageOperationRequest(BaseModel):
    action: Literal[
        "add",
        "remove",
        "replace",
        "add-client",
        "remove-client",
        "add-sniffing-exclude",
    ] = "add"
    inbound: dict[str, Any] | None = None
    tag: str | None = Field(default=None, max_length=255)
    client: dict[str, Any] | None = None
    domains: list[str] = Field(default_factory=list, max_length=200)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        return _strip_lower_value(value)

    @field_validator("inbound", "client")
    @classmethod
    def validate_optional_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _ensure_json_serializable_config(value, "payload")

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "tag")

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        domains: list[str] = []
        seen: set[str] = set()
        for raw in value:
            domain = AgentDomainLatencyProbeRequest._normalize_domain(raw).lower()
            if domain and domain not in seen:
                seen.add(domain)
                domains.append(domain)
        return domains


class AgentOutboundsManageOperationRequest(BaseModel):
    action: Literal["add", "remove", "update", "reorder"] = "add"
    outbound: dict[str, Any] | None = None
    tag: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list, max_length=500)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        return _strip_lower_value(value)

    @field_validator("outbound")
    @classmethod
    def validate_outbound(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _ensure_json_serializable_config(value, "outbound")

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "tag")

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return [_strip_required_text(item, "tags") for item in value]


class AgentRoutingManageOperationRequest(BaseModel):
    action: Literal[
        "set",
        "add_rule",
        "remove_rule",
        "add_user_to_rule",
        "remove_user_from_rule",
    ] = "set"
    routing: dict[str, Any] | None = None
    rule: dict[str, Any] | None = None
    index: int = Field(default=0, ge=0)
    observatory: Any | None = None
    burst_observatory: Any | None = None
    marktag: str | None = Field(default=None, max_length=255)
    user_email: str | None = Field(default=None, max_length=255)
    no_restart: bool = False
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: Any) -> Any:
        return _strip_lower_value(value)

    @field_validator("routing", "rule")
    @classmethod
    def validate_optional_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return _ensure_json_serializable_config(value, "payload")

    @field_validator("observatory", "burst_observatory")
    @classmethod
    def validate_observatory(cls, value: Any | None) -> Any | None:
        if value is None:
            return None
        return _ensure_json_serializable_config(value, "observatory")

    @field_validator("marktag", "user_email")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "routing field")


class AgentBatchInboundClient(BaseModel):
    tag: str = Field(min_length=1, max_length=255)
    client: dict[str, Any]

    @field_validator("tag")
    @classmethod
    def validate_tag(cls, value: str) -> str:
        return _strip_required_text(value, "tag")

    @field_validator("client")
    @classmethod
    def validate_client(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _ensure_json_serializable_config(value, "client")


class AgentBatchRoutingAddition(BaseModel):
    marktag: str | None = Field(default=None, max_length=255)
    outbound_tag: str | None = Field(default=None, max_length=255)
    user_email: str = Field(min_length=1, max_length=255)

    @field_validator("marktag", "outbound_tag")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "routing tag")

    @field_validator("user_email")
    @classmethod
    def validate_user_email(cls, value: str) -> str:
        return _strip_required_text(value, "user_email")


class AgentBatchApplyOperationRequest(BaseModel):
    inbound_clients: list[AgentBatchInboundClient] = Field(default_factory=list, max_length=500)
    routing_user_additions: list[AgentBatchRoutingAddition] = Field(
        default_factory=list,
        max_length=500,
    )
    no_restart: bool = False
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)


class AgentCertDeployOperationRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=255)
    cert_pem: str = Field(min_length=1)
    key_pem: str = Field(min_length=1)
    cert_path: str = Field(min_length=1, max_length=512)
    key_path: str = Field(min_length=1, max_length=512)
    reload: Literal["nginx", "xray", "both", "none"] = "none"
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("reload", mode="before")
    @classmethod
    def normalize_reload(cls, value: Any) -> Any:
        return _strip_lower_value(value)

    @field_validator("domain", "cert_path", "key_path")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _strip_required_text(value, "cert field")

    @field_validator("cert_pem", "key_pem")
    @classmethod
    def validate_pem(cls, value: str) -> str:
        return _validate_required_content(value, "pem")


class AgentNginxSetupSSLOperationRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=255)
    nginx_config: str | None = None
    domain_config: str | None = None
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return _strip_required_text(value, "domain").lower()

    @field_validator("nginx_config", "domain_config")
    @classmethod
    def validate_optional_config(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_required_content(value, "config")


class AgentNginxWebsiteDeleteOperationRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=255)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        return _strip_required_text(
            AgentDomainLatencyProbeRequest._normalize_domain(value).lower(),
            "domain",
        )


class AgentNginxClearStreamPortOperationRequest(BaseModel):
    port: int = Field(ge=1, le=65_535)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)


class AgentReturnRouteTarget(BaseModel):
    carrier: Literal["telecom", "unicom", "mobile"]
    region: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=80, ge=1, le=65_535)

    @field_validator("carrier", mode="before")
    @classmethod
    def normalize_carrier(cls, value: Any) -> Any:
        return _strip_lower_value(value)

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        return value.strip()

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return _strip_required_text(value, "host")


class AgentReturnRouteTestOperationRequest(BaseModel):
    ip_version: Literal[4, 6] = 4
    timeout_seconds: int = Field(default=25, ge=10, le=45)
    targets: list[AgentReturnRouteTarget] = Field(min_length=1, max_length=3)
    command_timeout_ms: int = Field(default=90_000, ge=1_000, le=300_000)


class AgentValidateSiteOperationRequest(BaseModel):
    site_type: Literal["static", "proxy"]
    site_value: str = Field(min_length=1, max_length=512)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("site_type", mode="before")
    @classmethod
    def normalize_site_type(cls, value: Any) -> Any:
        return _strip_lower_value(value)

    @field_validator("site_value")
    @classmethod
    def validate_site_value(cls, value: str) -> str:
        return _strip_required_text(value, "site_value")


class AgentLimiterUser(BaseModel):
    uid: int = Field(ge=0)
    email: str = Field(min_length=1, max_length=255)
    speed_limit: int = Field(default=0, ge=0)
    device_limit: int = Field(default=0, ge=0)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _strip_required_text(value, "email")


class AgentLimiterOperationRequest(BaseModel):
    inbound_tag: str = Field(min_length=1, max_length=255)
    node_limit: int = Field(default=0, ge=0)
    users: list[AgentLimiterUser] = Field(default_factory=list, max_length=1000)
    auto_speed_rules: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("inbound_tag")
    @classmethod
    def validate_inbound_tag(cls, value: str) -> str:
        return _strip_required_text(value, "inbound_tag")

    @field_validator("auto_speed_rules")
    @classmethod
    def validate_auto_speed_rules(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _ensure_json_serializable_config(value, "auto_speed_rules")


class AgentXrayTestConfigOperationRequest(BaseModel):
    config: Any
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: Any) -> Any:
        return _ensure_json_serializable_config(value, "config")


class AgentXrayConfigOperationRequest(BaseModel):
    config: Any
    path: str | None = Field(default=None, max_length=512)
    force: bool = False
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: Any) -> Any:
        return _ensure_json_serializable_config(value, "config")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "path")


class AgentXraySystemConfigOperationRequest(BaseModel):
    metrics_enabled: bool = False
    metrics_listen: str = Field(default="127.0.0.1:11111", max_length=255)
    stats_enabled: bool = True
    grpc_enabled: bool = True
    grpc_port: int = Field(default=46_736, ge=1, le=65_535)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("metrics_listen")
    @classmethod
    def validate_metrics_listen(cls, value: str) -> str:
        return _strip_required_text(value, "metrics_listen")


class AgentXrayConfigFileReadOperationRequest(BaseModel):
    file: str = Field(min_length=1, max_length=255)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        return _validate_xray_config_file(value)


class AgentXrayConfigFileWriteOperationRequest(BaseModel):
    file: str = Field(min_length=1, max_length=255)
    content: Any
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        return _validate_xray_config_file(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: Any) -> Any:
        return _ensure_json_serializable_config(value, "content")


class AgentXrayTakeoverExternalOperationRequest(BaseModel):
    command_timeout_ms: int = Field(default=120_000, ge=1_000, le=300_000)


class AgentNginxConfigOperationRequest(BaseModel):
    config: str = Field(min_length=1)
    path: str | None = Field(default=None, max_length=512)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("config")
    @classmethod
    def validate_config(cls, value: str) -> str:
        return _validate_required_content(value, "config")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "path")


class AgentNginxConfigFileReadOperationRequest(BaseModel):
    file: str = Field(min_length=1, max_length=512)

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str) -> str:
        return _strip_required_text(value, "file")


class AgentNginxConfigFileWriteOperationRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    command_timeout_ms: int = Field(default=30_000, ge=1_000, le=300_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _strip_required_text(value, "path")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return _validate_required_content(value, "content")


class AgentWarpLicenseOperationRequest(BaseModel):
    license: str = Field(min_length=1, max_length=255)
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("license")
    @classmethod
    def validate_license(cls, value: str) -> str:
        return _strip_required_text(value, "license")


class AgentSwitchXrayModeOperationRequest(BaseModel):
    xray_mode: XrayMode
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)


class AgentSwitchListenPortOperationRequest(BaseModel):
    listen_port: int = Field(ge=0, le=65_535)
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("listen_port")
    @classmethod
    def validate_listen_port(cls, value: int) -> int:
        if value != 0 and value < 1024:
            raise ValueError("listen_port must be 0 or in 1024-65535")
        return value


class AgentProbeMasterURLOperationRequest(BaseModel):
    master_url: str = Field(min_length=1, max_length=512)
    command_timeout_ms: int = Field(default=15_000, ge=1_000, le=300_000)

    @field_validator("master_url")
    @classmethod
    def validate_master_url(cls, value: str) -> str:
        return _validate_agent_url(value)


class AgentUpdateMasterURLOperationRequest(BaseModel):
    master_url: str = Field(min_length=1, max_length=512)
    only_if_recovery: bool = False
    command_timeout_ms: int = Field(default=60_000, ge=1_000, le=300_000)

    @field_validator("master_url")
    @classmethod
    def validate_master_url(cls, value: str) -> str:
        return _validate_agent_url(value)


class ServerCommandsResponse(BaseModel):
    server_id: UUID
    commands: list[AgentCommandRead]
    license_required: Literal[False] = False


class AgentCommandStreamFramesResponse(BaseModel):
    server_id: UUID
    command_id: UUID
    frames: list[AgentCommandStreamFrameRead]
    license_required: Literal[False] = False


class AgentCommandLeaseRequest(BaseModel):
    token: str = Field(min_length=1)
    max_commands: int = Field(default=1, ge=1, le=10)


class AgentCommandLeaseResponse(BaseModel):
    server: ServerRead
    commands: list[AgentCommandRead]
    license_required: Literal[False] = False


class AgentCommandResultRequest(BaseModel):
    token: str = Field(min_length=1)
    status: int = Field(ge=100, le=599)
    body: Any = None
    error: str | None = Field(default=None, max_length=2048)


class AgentCommandStreamDataRequest(BaseModel):
    token: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=120)
    data: str


class AgentCommandResultResponse(BaseModel):
    command: AgentCommandRead
    license_required: Literal[False] = False
