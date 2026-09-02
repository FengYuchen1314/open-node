from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CamouflageRegion = Literal[
    "los-angeles",
    "san-jose",
    "tokyo",
    "singapore",
    "germany",
    "united-kingdom",
    "netherlands",
]


class CamouflagePoolRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    region: CamouflageRegion
    region_label: str
    label: str
    server_name: str
    target: str
    tls_version: Literal["TLSv1.3"]
    alpn: Literal["h2"]
    cloudflare: Literal[False]
    gfw_verdict: Literal["not_blocked"]
    gfw_last_tested: str


class CamouflagePoolCatalogRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    reviewed_at: str
    probe_vantage: str
    measurement_notice: str
    sources: dict[str, str]
    pools: list[CamouflagePoolRead]
    license_required: Literal[False] = False
