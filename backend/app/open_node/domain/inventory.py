from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
