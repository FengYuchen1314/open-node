"""Validated automatic speed rules shared by plan and native policy APIs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AutoSpeedRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["sustained", "burst"]
    threshold_mbps: float = Field(ge=1 / 125000, le=(1 << 50) / 125000, allow_inf_nan=False)
    sustained_seconds: int = Field(ge=1, le=86400)
    window_seconds: int = Field(default=0, ge=0, le=86400)
    burst_count: int = Field(default=0, ge=0, le=10000)
    limit_mbps: float = Field(ge=1 / 125000, le=(1 << 50) / 125000, allow_inf_nan=False)
    limit_duration: int = Field(ge=1, le=86400)

    @model_validator(mode="after")
    def valid_burst(self):
        if self.type == "burst" and (
            self.window_seconds < self.sustained_seconds or self.burst_count < 1
        ):
            raise ValueError("Burst rules require a valid window and occurrence count")
        return self
