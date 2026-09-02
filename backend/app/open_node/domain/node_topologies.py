from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class NodeTopologyPoint(BaseModel):
    model_config = {"extra": "forbid"}

    x: float = Field(ge=-100_000, le=100_000, allow_inf_nan=False)
    y: float = Field(ge=-100_000, le=100_000, allow_inf_nan=False)


class NodeTopologyStage(BaseModel):
    model_config = {"extra": "forbid"}

    node_ids: list[UUID] = Field(min_length=1, max_length=16)
    load_balance_strategy: Literal["round-robin"] = "round-robin"

    @field_validator("node_ids")
    @classmethod
    def unique_nodes(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("A topology stage cannot contain duplicate nodes")
        return value


class NodeTopologyWrite(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=120)
    enabled: bool = Field(default=True, strict=True)
    stages: list[NodeTopologyStage] = Field(min_length=2, max_length=8)
    layout: dict[str, NodeTopologyPoint] = Field(default_factory=dict, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(ord(char) < 32 for char in normalized):
            raise ValueError("Topology name is invalid")
        return normalized

    @model_validator(mode="after")
    def validate_shape(self):
        flattened = [node_id for stage in self.stages for node_id in stage.node_ids]
        if len(set(flattened)) != len(flattened):
            raise ValueError("A node can appear only once in a topology")
        if len(self.stages[-1].node_ids) != 1:
            raise ValueError("The final topology stage must contain exactly one exit node")
        known = {str(node_id) for node_id in flattened}
        if set(self.layout) - known:
            raise ValueError("Topology layout references an unknown node")
        return self


class NodeTopologyCreate(NodeTopologyWrite):
    pass


class NodeTopologyUpdate(NodeTopologyWrite):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class NodeTopologyDelete(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_name: str = Field(min_length=1, max_length=120)


class NodeTopologyCandidate(BaseModel):
    id: UUID
    name: str
    server_id: UUID
    server_name: str
    server_kind: Literal["direct", "leased-line", "residential"]
    protocol: str


class NodeTopologyRead(NodeTopologyWrite):
    id: UUID
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    updated_at: datetime


class NodeTopologiesResponse(BaseModel):
    topologies: list[NodeTopologyRead]
    candidates: list[NodeTopologyCandidate]
    license_required: Literal[False] = False


class NodeTopologyResponse(BaseModel):
    topology: NodeTopologyRead
    license_required: Literal[False] = False
