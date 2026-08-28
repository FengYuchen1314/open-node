from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr

from open_node.domain.subscriptions import SubscriptionQuotaStatusRead


class SubscriberLogin(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=1, max_length=1024)


class SubscriberProof(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: SecretStr = Field(default=SecretStr(""), max_length=64)


class SubscriberPasswordChange(SubscriberProof):
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class SubscriberSecondFactor(BaseModel):
    challenge: SecretStr = Field(min_length=1, max_length=128)
    code: SecretStr = Field(min_length=1, max_length=64)


class SubscriberCode(BaseModel):
    code: SecretStr = Field(min_length=1, max_length=64)


class SubscriberSessionRead(BaseModel):
    authenticated: bool = False
    username: str | None = None
    csrf_token: str | None = None
    requires_2fa: bool = False
    challenge: str | None = None


class SubscriberDeviceRead(BaseModel):
    id: UUID
    current: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    peer: str
    user_agent: str


class SubscriberSecurityRead(BaseModel):
    totp_enabled: bool
    totp_available: bool
    recovery_codes_remaining: int


class SubscriberEnrollment(BaseModel):
    secret: str
    provisioning_uri: str
    expires_at: datetime


class SubscriberRecoveryCodes(BaseModel):
    recovery_codes: list[str]


class SubscriberProfile(BaseModel):
    username: str
    display_name: str
    email: str | None
    quota: SubscriptionQuotaStatusRead
    speed_limit_mbps: float
    device_limit: int
    license_required: Literal[False] = False


class SubscriberAccountRead(BaseModel):
    username: str
    configured: bool
    totp_enabled: bool
    revision: str


class SubscriberAccountUpdate(BaseModel):
    expected_revision: str = Field(min_length=1, max_length=64)
    new_password: SecretStr = Field(min_length=12, max_length=1024)
    reset_totp: bool = False
