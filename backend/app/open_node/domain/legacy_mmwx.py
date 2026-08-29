import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class LegacyMMWXIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=80)
    password_hash: SecretStr = Field(min_length=59, max_length=60)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)
    source_role: Literal["user", "admin"] = "user"
    is_active: bool = Field(default=True, strict=True)
    totp_enabled: bool = Field(default=False, strict=True)
    totp_secret: SecretStr | None = Field(default=None, max_length=128)
    recovery_code_hashes: list[SecretStr] = Field(default_factory=list, max_length=100)
    token: SecretStr | None = Field(default=None, min_length=8, max_length=96)
    generated_short_code: SecretStr | None = Field(default=None, min_length=1, max_length=24)
    custom_short_code: SecretStr | None = Field(default=None, min_length=1, max_length=16)
    created_at: datetime | None = None

    @field_validator("username")
    @classmethod
    def valid_username(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or char == "\x7f" for char in value):
            raise ValueError("Legacy username contains invalid characters")
        return value

    @field_validator("password_hash")
    @classmethod
    def valid_password_hash(cls, value):
        hashed = value.get_secret_value()
        if not re.fullmatch(r"\$2[ab]\$[0-9]{2}\$[./A-Za-z0-9]{53}", hashed):
            raise ValueError("Legacy password must be a bcrypt hash")
        if not 4 <= int(hashed[4:6]) <= 31:
            raise ValueError("Legacy bcrypt cost is invalid")
        return value

    @field_validator("token")
    @classmethod
    def valid_token(cls, value):
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9._~-]{8,96}", value.get_secret_value()
        ):
            raise ValueError("Legacy subscription token contains unsafe URL characters")
        return value

    @field_validator("generated_short_code", "custom_short_code")
    @classmethod
    def valid_short_code(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", value.get_secret_value()):
            raise ValueError("Legacy short code contains unsupported characters")
        return value

    @field_validator("recovery_code_hashes")
    @classmethod
    def valid_recovery_hashes(cls, values):
        if any(not re.fullmatch(r"[a-fA-F0-9]{64}", value.get_secret_value()) for value in values):
            raise ValueError("Legacy recovery-code hashes must be SHA-256 hex strings")
        return values

    @model_validator(mode="after")
    def consistent_security_fields(self):
        if self.totp_enabled and self.totp_secret is None:
            raise ValueError("Enabled legacy TOTP requires a secret")
        if not self.totp_enabled and self.recovery_code_hashes:
            raise ValueError("Legacy recovery hashes require enabled TOTP")
        if (self.token is None) != (self.generated_short_code is None):
            raise ValueError("Legacy token and generated short code must be supplied together")
        return self


class LegacyMMWXIdentityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: Literal[1] = 1
    source_revision: str | None = Field(default=None, max_length=64)
    users: list[LegacyMMWXIdentity] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_users(self):
        usernames = [entry.username for entry in self.users]
        if len(set(usernames)) != len(usernames):
            raise ValueError("Legacy identity usernames must be distinct")
        return self


class LegacyMMWXPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    bundle: LegacyMMWXIdentityBundle
    replace_existing: bool = Field(default=False, strict=True)


class LegacyMMWXImportRequest(LegacyMMWXPreviewRequest):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_user_count: int = Field(ge=1, le=10000)


class LegacyMMWXImportPreview(BaseModel):
    revision: str
    ready: bool
    total_users: int
    new_users: int
    existing_users: int
    imported_accounts: int
    replaced_accounts: int
    skipped_accounts: int
    imported_tokens: int
    replaced_tokens: int
    skipped_tokens: int
    imported_totp: int
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


class LegacyMMWXImportResponse(BaseModel):
    preview: LegacyMMWXImportPreview
    applied: Literal[True] = True
