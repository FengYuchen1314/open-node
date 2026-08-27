from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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


class AgentCommandStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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


class AgentCommandCreateResponse(BaseModel):
    command: AgentCommandRead
    license_required: Literal[False] = False


class ServerCommandsResponse(BaseModel):
    server_id: UUID
    commands: list[AgentCommandRead]
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


class AgentCommandResultResponse(BaseModel):
    command: AgentCommandRead
    license_required: Literal[False] = False
