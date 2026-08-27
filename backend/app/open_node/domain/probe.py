from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
    return_routes: list[dict[str, Any]] | None = None


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
