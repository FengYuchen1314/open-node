import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator


class AdministratorCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: SecretStr = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=1024)


class LoginSecondFactorRequest(BaseModel):
    challenge: SecretStr = Field(min_length=1, max_length=128)
    code: SecretStr = Field(min_length=1, max_length=64)


class PasswordChangeRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class SessionResponse(BaseModel):
    configured: bool
    authenticated: bool = False
    username: str | None = None
    csrf_token: str | None = None


class AdministratorTotpEnrollment(BaseModel):
    secret: str
    provisioning_uri: str
    expires_at: datetime


class LoginResponse(SessionResponse):
    requires_2fa: bool = False
    challenge: str | None = None
    enrollment_required: bool = False
    enrollment: AdministratorTotpEnrollment | None = None
    recovery_codes: list[str] = Field(default_factory=list)


class AdministratorProof(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: SecretStr = Field(default=SecretStr(""), max_length=64)


class AdministratorCode(BaseModel):
    code: SecretStr = Field(min_length=1, max_length=64)


class AdministratorSecurityRead(BaseModel):
    totp_enabled: bool
    totp_available: bool
    recovery_codes_remaining: int
    require_totp: bool


class AdministratorRecoveryCodes(BaseModel):
    recovery_codes: list[str]


class AdministratorPolicyUpdate(AdministratorProof):
    required: bool


def _profile_text(value: str, maximum: int, *, optional: bool = True) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Invalid administrator profile")
    normalized = " ".join(value.split())
    if not normalized and optional:
        return ""
    if not normalized or len(normalized) > maximum:
        raise ValueError("Invalid administrator profile")
    return normalized


class AdministratorProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)

    email: str = Field(default="", max_length=254)
    nickname: str = Field(default="", max_length=120)
    avatar_url: str = Field(default="", max_length=2048)
    expected_revision: int = Field(ge=0, le=2_147_483_647)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = value.strip()
        if normalized and (
            re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized) is None
            or any(ord(char) < 33 or ord(char) == 127 for char in normalized)
        ):
            raise ValueError("Invalid administrator email")
        return normalized

    @field_validator("nickname")
    @classmethod
    def valid_nickname(cls, value: str) -> str:
        return _profile_text(value, 120)

    @field_validator("avatar_url")
    @classmethod
    def valid_avatar(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""
        parsed = HttpUrl(normalized)
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise ValueError("Administrator avatar must use HTTPS")
        return str(parsed)


class AdministratorProfileRead(BaseModel):
    username: str
    email: str
    nickname: str
    avatar_url: str
    revision: int
