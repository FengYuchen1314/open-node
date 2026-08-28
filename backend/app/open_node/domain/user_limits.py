from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field


def byte_rate(value: float) -> float:
    if 0 < value * 125000 < 1:
        raise ValueError("A positive speed limit must be at least one byte per second")
    return value


def byte_quota(value: float) -> float:
    if 0 < value * 1024**3 < 1:
        raise ValueError("A positive traffic limit must be at least one byte")
    return value


Speed = Annotated[
    float,
    Field(ge=0, le=(1 << 50) / 125000, allow_inf_nan=False, strict=True),
    AfterValidator(byte_rate),
]
Connections = Annotated[int, Field(ge=0, le=1_000_000, strict=True)]
Traffic = Annotated[
    float,
    Field(ge=0, le=((1 << 53) - 1) / 1024**3, allow_inf_nan=False, strict=True),
    AfterValidator(byte_quota),
]


class UserLimitValues(BaseModel):
    model_config = {"extra": "forbid"}

    traffic_limit_gb: Traffic | None = None
    speed_limit_mbps: Speed | None = None
    device_limit: Connections | None = None


class UserLimitOverrides(UserLimitValues):
    node_speed_limits: dict[UUID, Speed] = Field(default_factory=dict, max_length=1000)
    node_device_limits: dict[UUID, Connections] = Field(default_factory=dict, max_length=1000)


class CatalogUserLimitOverrides(UserLimitValues):
    node_speed_limits: dict[str, Speed] = Field(default_factory=dict, max_length=1000)
    node_device_limits: dict[str, Connections] = Field(default_factory=dict, max_length=1000)


LimitSource = Literal[
    "user_node", "user_parent", "user", "plan_node", "plan_parent", "plan", "unlimited", "shared"
]


class UserEffectiveLimits(BaseModel):
    speed_limit_mbps: float
    device_limit: int
    speed_source: LimitSource
    device_source: LimitSource


class UserNodeLimitsRead(UserEffectiveLimits):
    node_id: UUID
    name: str
    enabled: bool


class UserLimitsRead(UserEffectiveLimits):
    traffic_limit_bytes: int
    nodes: list[UserNodeLimitsRead]
    warnings: list[str] = Field(default_factory=list)
