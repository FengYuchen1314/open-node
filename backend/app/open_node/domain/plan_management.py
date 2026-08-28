from math import isfinite
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from open_node.domain.inventory import AgentCommandRead
from open_node.domain.subscriptions import SubscriptionPlanCreate, SubscriptionPlanRead


class PlanUpdate(SubscriptionPlanCreate):
    model_config = {"extra": "forbid", "allow_inf_nan": False}

    traffic_limit_gb: float = Field(ge=1 / (1024**3), le=((1 << 63) - 1) // (1024**3))
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_runtime_restart: Literal[True]

    @model_validator(mode="after")
    def valid_nodes(self):
        selected = set(self.node_ids)
        if len(selected) != len(self.node_ids):
            raise ValueError("Plan nodes must be distinct")
        for mapping in (self.node_multipliers, self.node_speed_limits, self.node_device_limits):
            if not set(mapping).issubset(selected):
                raise ValueError("Per-node overrides must refer to selected nodes")
        if any(not isfinite(value) or value <= 0 for value in self.node_multipliers.values()):
            raise ValueError("Node multipliers must be finite positive numbers")
        if self.is_reset and not self.reset_day:
            raise ValueError("Choose a monthly reset day")
        return self


class PlanRemoval(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_name: str = Field(min_length=1, max_length=120)
    acknowledge_runtime_restart: Literal[True]


class PlanSubscriber(BaseModel):
    username: str
    display_name: str
    is_active: bool
    managed: bool


class PlanManagementRead(BaseModel):
    plan: SubscriptionPlanRead
    revision: str
    users: list[PlanSubscriber]
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


class PlanManagementResult(BaseModel):
    plan: SubscriptionPlanRead | None = None
    revision: str | None = None
    affected_users: list[str]
    commands: list[AgentCommandRead]
    warnings: list[str]
    license_required: Literal[False] = False
