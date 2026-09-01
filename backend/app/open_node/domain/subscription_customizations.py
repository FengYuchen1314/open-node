"""Owner-scoped Clash custom rules and proxy-provider configuration."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CustomizationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class CustomRuleFields(CustomizationModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["dns", "rules", "rule-providers"]
    mode: Literal["replace", "prepend", "append"]
    content: str = Field(min_length=1, max_length=524_288)
    enabled: bool = True


class CustomRuleCreate(CustomRuleFields):
    owner_username: str = Field(min_length=1, max_length=80)


class AccountCustomRuleCreate(CustomRuleFields):
    pass


class CustomRuleUpdate(CustomRuleFields):
    expected_revision: int = Field(ge=1, le=2**53 - 1)


class CustomRuleRead(CustomRuleFields):
    id: UUID
    owner_username: str
    revision: int
    created_at: datetime
    updated_at: datetime


class CustomRulesResponse(CustomizationModel):
    rules: list[CustomRuleRead]
    license_required: Literal[False] = False


class ProxyProviderFields(CustomizationModel):
    external_source_id: UUID
    name: str = Field(min_length=1, max_length=120)
    type: Literal["http"] = "http"
    interval: int = Field(default=3600, ge=60, le=604_800)
    proxy: str = Field(default="DIRECT", min_length=1, max_length=120)
    size_limit: int = Field(default=0, ge=0, le=1024)
    # Header values may contain credentials even though the UI warns against it.
    # Accept the raw JSON shape here and validate it in the service so a 422
    # response never includes the submitted values in Pydantic's error input.
    header: Any = Field(default_factory=dict)
    health_check_enabled: bool = True
    health_check_url: str = Field(
        default="https://www.gstatic.com/generate_204", min_length=1, max_length=2048
    )
    health_check_interval: int = Field(default=300, ge=30, le=604_800)
    health_check_timeout: int = Field(default=5000, ge=500, le=60_000)
    health_check_lazy: bool = True
    health_check_expected_status: int = Field(default=204, ge=100, le=599)
    filter: str = Field(default="", max_length=1000)
    exclude_filter: str = Field(default="", max_length=1000)
    exclude_type: str = Field(default="", max_length=1000)
    geo_ip_filter: str = Field(default="", max_length=512)
    override: dict[str, Any] = Field(default_factory=dict)
    process_mode: Literal["client", "mmw"] = "client"
    enabled: bool = True

    @field_validator("health_check_url")
    @classmethod
    def require_https_health_check(cls, value: str):
        if not value.startswith("https://"):
            raise ValueError("Health-check URL must use HTTPS")
        return value

    @field_validator("override")
    @classmethod
    def bound_override(cls, value: dict[str, Any]):
        if len(value) > 64:
            raise ValueError("Provider override has too many fields")
        return value

    @field_validator("geo_ip_filter")
    @classmethod
    def country_codes(cls, value: str):
        import re

        codes = [item.strip().upper() for item in re.split(r"[,\s]+", value) if item.strip()]
        if len(codes) > 64 or any(re.fullmatch(r"[A-Z]{2}", item) is None for item in codes):
            raise ValueError("GeoIP filter must contain comma-separated two-letter country codes")
        return ",".join(dict.fromkeys(codes))


class ProxyProviderCreate(ProxyProviderFields):
    owner_username: str = Field(min_length=1, max_length=80)


class AccountProxyProviderCreate(ProxyProviderFields):
    pass


class ProxyProviderUpdate(ProxyProviderFields):
    expected_revision: int = Field(ge=1, le=2**53 - 1)


class ProxyProviderRead(ProxyProviderFields):
    id: UUID
    owner_username: str
    revision: int
    created_at: datetime
    updated_at: datetime


class ProxyProvidersResponse(CustomizationModel):
    providers: list[ProxyProviderRead]
    license_required: Literal[False] = False


class CustomizationDelete(CustomizationModel):
    expected_revision: int = Field(ge=1, le=2**53 - 1)
