from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from open_node.domain.inventory import AgentCommandRead


def _strip_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{field_name} must not contain control characters")
    return normalized


def _strip_required_text(value: str, field_name: str) -> str:
    normalized = _strip_optional_text(value, field_name)
    if not normalized:
        raise ValueError(f"{field_name} is empty")
    return normalized


class ProbeAppearance(BaseModel):
    theme: str = Field(default="open-node", max_length=80)
    color_mode: Literal["light", "dark", "system"] = "light"
    revision: str = Field(default="open-node", max_length=120)

    @field_validator("theme", "revision")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _strip_required_text(value, "appearance field")


class ProbeAppearanceUpdate(BaseModel):
    theme: str | None = Field(default=None, max_length=80)
    color_mode: Literal["light", "dark", "system"] | None = None
    revision: str | None = Field(default=None, max_length=120)

    @field_validator("theme", "revision")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "appearance field")


class ProbeSettingsRead(BaseModel):
    enabled: bool = True
    show_globe: bool = False
    show_daily_trend: bool = False
    show_traffic_hotspots: bool = False
    show_traffic_7d: bool = False
    show_return_route: bool = False
    show_resource_heatmap: bool = True
    show_traffic_quota: bool = True
    show_renewal_timeline: bool = False
    show_health_score: bool = True
    title: str = Field(default="Open Node Probe", max_length=120)
    description: str = Field(
        default="MMWX probe-compatible node status without license gates.",
        max_length=500,
    )
    logo: str = Field(default="", max_length=2000)
    refresh_interval_sec: int = Field(default=5, ge=1, le=60)
    appearance: ProbeAppearance = Field(default_factory=ProbeAppearance)
    updated_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _strip_required_text(value, "title")

    @field_validator("description", "logo")
    @classmethod
    def validate_optional_text(cls, value: str) -> str:
        return _strip_optional_text(value, "probe field") or ""


class ProbeSettingsUpdate(BaseModel):
    enabled: bool | None = None
    show_globe: bool | None = None
    show_daily_trend: bool | None = None
    show_traffic_hotspots: bool | None = None
    show_traffic_7d: bool | None = None
    show_return_route: bool | None = None
    show_resource_heatmap: bool | None = None
    show_traffic_quota: bool | None = None
    show_renewal_timeline: bool | None = None
    show_health_score: bool | None = None
    title: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    logo: str | None = Field(default=None, max_length=2000)
    refresh_interval_sec: int | None = Field(default=None, ge=1, le=60)
    appearance: ProbeAppearanceUpdate | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip_required_text(value, "title")

    @field_validator("description", "logo")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "probe field")


class ProbeSettingsResponse(BaseModel):
    settings: ProbeSettingsRead
    license_required: Literal[False] = False


class ProbeBucket(BaseModel):
    ms: int
    loss: float


class ProbePingSeries(BaseModel):
    key: str | None = None
    label: str
    isp: str | None = None
    current_ms: int
    loss_pct: float
    buckets: list[ProbeBucket] = Field(default_factory=list)


class ProbeDailyTraffic(BaseModel):
    date: str
    uplink: int = 0
    downlink: int = 0
    total: int = 0


class ProbeReturnRoute(BaseModel):
    carrier: Literal["telecom", "unicom", "mobile"]
    region: str | None = Field(default=None, max_length=120)
    route_type: str = Field(default="Unknown", max_length=80)
    tested_at: str | None = None

    @field_validator("region", "tested_at")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional_text(value, "return route field")

    @field_validator("route_type")
    @classmethod
    def validate_route_type(cls, value: str) -> str:
        return _strip_optional_text(value, "return route field") or "Unknown"


class ProbeServer(BaseModel):
    name: str | None = None
    region: str | None = None
    region_country: str | None = None
    region_name: str | None = None
    region_city: str | None = None
    online: bool
    upload_speed: int | None = None
    download_speed: int | None = None
    traffic_used: int | None = None
    traffic_used_up: int | None = None
    traffic_used_down: int | None = None
    traffic_used_total: int | None = None
    traffic_limit: int | None = None
    period_start: str | None = None
    period_end: str | None = None
    cumulative_up: int | None = None
    cumulative_down: int | None = None
    daily_traffic: list[ProbeDailyTraffic] | None = None
    cpu_pct: float | None = None
    loadavg: str | None = None
    mem_used: int | None = None
    mem_total: int | None = None
    disk_used: int | None = None
    disk_total: int | None = None
    uptime: int | None = None
    cpu_model: str | None = None
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    os: str | None = None
    kernel: str | None = None
    arch: str | None = None
    ping: list[ProbePingSeries] | None = None
    expires_at: str | None = None
    renewal_price: float | None = None
    renewal_price_cny: float | None = None
    renewal_cycle: Literal["month", "quarter", "half_year", "year"] | None = None
    renewal_currency: str | None = None
    provider_name: str | None = None
    provider_url: str | None = None
    telecom_paid_peer: bool | None = None
    return_routes: list[ProbeReturnRoute] | None = None


class ProbePayload(ProbeSettingsRead):
    servers: list[ProbeServer] = Field(default_factory=list)
    license_required: Literal[False] = False


class ProbeMetricPoint(BaseModel):
    t: int
    value: float


class ProbeSystemSeries(BaseModel):
    cpu_pct: list[ProbeMetricPoint] = Field(default_factory=list)
    mem_used: list[ProbeMetricPoint] = Field(default_factory=list)
    mem_total: list[ProbeMetricPoint] = Field(default_factory=list)
    upload_speed: list[ProbeMetricPoint] = Field(default_factory=list)
    download_speed: list[ProbeMetricPoint] = Field(default_factory=list)
    cumulative_up: list[ProbeMetricPoint] = Field(default_factory=list)
    cumulative_down: list[ProbeMetricPoint] = Field(default_factory=list)


class ProbeSeriesResponse(BaseModel):
    success: bool
    series: ProbePingSeries | ProbeSystemSeries | None = None
    all_series: list[ProbePingSeries] | None = None
    bucket_sec: int | None = None
    generated_at: int | None = None
    license_required: Literal[False] = False


class ProbeTargetServerComparison(BaseModel):
    server_index: int
    server_name: str | None = None
    region: str | None = None
    current_ms: int
    loss_pct: float
    buckets: list[ProbeBucket] = Field(default_factory=list)


class ProbeTargetComparison(BaseModel):
    key: str
    label: str
    server_count: int
    healthy_count: int
    average_ms: int | None = None
    best_ms: int | None = None
    worst_ms: int | None = None
    average_loss_pct: float
    servers: list[ProbeTargetServerComparison] = Field(default_factory=list)


class ProbeTargetComparisonResponse(BaseModel):
    success: bool
    targets: list[ProbeTargetComparison] = Field(default_factory=list)
    bucket_sec: int | None = None
    generated_at: int | None = None
    license_required: Literal[False] = False


ProbeTaskKind = Literal["system", "domain_latency", "return_route"]


class ProbeTaskReturnRouteTarget(BaseModel):
    carrier: Literal["telecom", "unicom", "mobile"]
    region: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=80, ge=1, le=65_535)

    @field_validator("carrier", mode="before")
    @classmethod
    def normalize_carrier(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        return _strip_optional_text(value, "return route target region") or ""

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        return _strip_required_text(value, "return route target host")


class ProbeTaskCreate(BaseModel):
    server_id: UUID
    kind: ProbeTaskKind
    enabled: bool = True
    interval_sec: int = Field(default=300, ge=60, le=86_400)
    domains: list[str] = Field(default_factory=list, max_length=200)
    domain_timeout_ms: int = Field(default=2_000, ge=200, le=10_000)
    allow_icmp: bool = False
    return_route_targets: list[ProbeTaskReturnRouteTarget] = Field(
        default_factory=list,
        max_length=3,
    )
    return_route_timeout_seconds: int = Field(default=25, ge=10, le=45)
    ip_version: Literal[4, 6] = 4
    command_timeout_ms: int = Field(default=90_000, ge=1_000, le=300_000)
    next_run_at: datetime | None = None

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        return _normalize_probe_domains(value)

    @model_validator(mode="after")
    def validate_kind_config(self) -> "ProbeTaskCreate":
        _validate_probe_task_config(self.kind, self.domains, self.return_route_targets)
        return self


class ProbeTaskUpdate(BaseModel):
    enabled: bool | None = None
    interval_sec: int | None = Field(default=None, ge=60, le=86_400)
    domains: list[str] | None = Field(default=None, max_length=200)
    domain_timeout_ms: int | None = Field(default=None, ge=200, le=10_000)
    allow_icmp: bool | None = None
    return_route_targets: list[ProbeTaskReturnRouteTarget] | None = Field(
        default=None,
        max_length=3,
    )
    return_route_timeout_seconds: int | None = Field(default=None, ge=10, le=45)
    ip_version: Literal[4, 6] | None = None
    command_timeout_ms: int | None = Field(default=None, ge=1_000, le=300_000)
    next_run_at: datetime | None = None

    @field_validator("domains")
    @classmethod
    def normalize_optional_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_probe_domains(value)


class ProbeTaskRead(BaseModel):
    id: UUID
    server_id: UUID
    kind: ProbeTaskKind
    enabled: bool
    interval_sec: int
    domains: list[str] = Field(default_factory=list)
    domain_timeout_ms: int
    allow_icmp: bool
    return_route_targets: list[ProbeTaskReturnRouteTarget] = Field(default_factory=list)
    return_route_timeout_seconds: int
    ip_version: Literal[4, 6]
    command_timeout_ms: int
    last_dispatched_at: datetime | None = None
    next_run_at: datetime
    created_at: datetime
    updated_at: datetime


class ProbeTaskResponse(BaseModel):
    task: ProbeTaskRead
    license_required: Literal[False] = False


class ProbeTaskListResponse(BaseModel):
    tasks: list[ProbeTaskRead] = Field(default_factory=list)
    license_required: Literal[False] = False


class ProbeTaskDispatchItem(BaseModel):
    task: ProbeTaskRead
    command: AgentCommandRead


class ProbeTaskDispatchResponse(BaseModel):
    checked_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    dispatched: list[ProbeTaskDispatchItem] = Field(default_factory=list)
    license_required: Literal[False] = False


def _normalize_probe_domain(raw: str) -> str:
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


def _normalize_probe_domains(value: list[str]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for raw in value:
        normalized = _normalize_probe_domain(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        domains.append(normalized)
    return domains


def _validate_probe_task_config(
    kind: ProbeTaskKind,
    domains: list[str],
    return_route_targets: list[ProbeTaskReturnRouteTarget],
) -> None:
    if kind == "domain_latency" and not domains:
        raise ValueError("domain latency probe tasks require at least one domain")
    if kind == "return_route" and not return_route_targets:
        raise ValueError("return-route probe tasks require at least one target")
