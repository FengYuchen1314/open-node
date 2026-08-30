from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

AgentBootstrapTransport = Literal["auto", "websocket", "http"]
AgentBootstrapState = Literal["issued", "claimed", "expired", "revoked", "not_issued"]


class AgentBootstrapIssued(BaseModel):
    """One-time administrative result; ordinary serialization masks the ticket."""

    model_config = ConfigDict(frozen=True)

    server_id: UUID
    server_name: str
    ticket: SecretStr = Field(repr=False)
    control_url: str
    transport: AgentBootstrapTransport
    issued_at: datetime
    expires_at: datetime


class AgentBootstrapStatus(BaseModel):
    """Ticket state and observed Agent state are deliberately separate."""

    model_config = ConfigDict(frozen=True)

    server_id: UUID
    server_name: str
    status: AgentBootstrapState
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    claimed_at: datetime | None = None
    agent_registered: bool = False
    agent_registered_at: datetime | None = None
    agent_last_seen_at: datetime | None = None
    agent_version: str | None = None
    server_last_heartbeat: datetime | None = None


class AgentBootstrapConfig(BaseModel):
    """Redeemed configuration; only the redemption response may unwrap the token."""

    model_config = ConfigDict(frozen=True)

    server_id: UUID
    server_name: str
    control_url: str
    agent_token: SecretStr = Field(repr=False)
    transport: AgentBootstrapTransport
    expires_at: datetime
