from datetime import datetime

from pydantic import BaseModel, Field, SecretStr


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
