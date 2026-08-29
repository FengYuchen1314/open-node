import re
from datetime import datetime
from typing import Literal
from uuid import UUID

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
    source_package_id: int | None = Field(default=None, ge=1)
    package_started_at: datetime | None = None
    package_expires_at: datetime | None = None
    is_reset: bool = Field(default=False, strict=True)
    reset_day: int = Field(default=0, ge=0, le=31)
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


class LegacyMMWXPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    short_code: str | None = Field(default=None, min_length=1, max_length=24)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        return value.strip()

    @field_validator("short_code")
    @classmethod
    def valid_short_code(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Legacy package short code contains unsupported characters")
        return value


class LegacyMMWXSubscriptionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int = Field(ge=1)
    owner_username: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    source_type: Literal["create", "import", "upload", "package"]
    filename: str = Field(default="", max_length=255)
    template_filename: str = Field(default="", max_length=255)
    file_short_code: str = Field(min_length=1, max_length=24)
    custom_short_code: str | None = Field(default=None, min_length=1, max_length=64)
    selected_tags: list[str] = Field(default_factory=list, max_length=100)
    selected_node_ids: list[int] = Field(default_factory=list, max_length=10000)
    selected_custom_rule_ids: list[int] = Field(default_factory=list, max_length=10000)
    selected_override_script_ids: list[int] = Field(default_factory=list, max_length=10000)
    raw_output: bool = Field(default=False, strict=True)
    sort_order: int = 0
    expires_at: datetime | None = None
    assigned_usernames: list[str] = Field(default_factory=list, max_length=10000)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("owner_username", "name")
    @classmethod
    def clean_required_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Legacy subscription profile text is required")
        return value

    @field_validator("description", "filename", "template_filename")
    @classmethod
    def clean_optional_text(cls, value):
        return value.strip()

    @field_validator("file_short_code", "custom_short_code")
    @classmethod
    def valid_profile_short_code(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Legacy file short code contains unsupported characters")
        return value

    @model_validator(mode="after")
    def unique_assignments(self):
        if len(set(self.assigned_usernames)) != len(self.assigned_usernames):
            raise ValueError("Legacy subscription assignments must be distinct")
        return self


class LegacyMMWXIdentityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    version: Literal[1] = 1
    source_revision: str | None = Field(default=None, max_length=64)
    users: list[LegacyMMWXIdentity] = Field(min_length=1, max_length=10000)
    packages: list[LegacyMMWXPackage] = Field(default_factory=list, max_length=10000)
    subscription_profiles: list[LegacyMMWXSubscriptionProfile] = Field(
        default_factory=list, max_length=10000
    )

    @model_validator(mode="after")
    def unique_users(self):
        usernames = [entry.username for entry in self.users]
        if len(set(usernames)) != len(usernames):
            raise ValueError("Legacy identity usernames must be distinct")
        package_ids = [entry.source_id for entry in self.packages]
        if len(set(package_ids)) != len(package_ids):
            raise ValueError("Legacy package IDs must be distinct")
        profile_ids = [entry.source_id for entry in self.subscription_profiles]
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("Legacy subscription profile IDs must be distinct")
        known_users = set(usernames)
        if any(
            entry.owner_username not in known_users
            or any(username not in known_users for username in entry.assigned_usernames)
            for entry in self.subscription_profiles
        ):
            raise ValueError("Legacy subscription profiles must reference bundled users")
        return self


class LegacyMMWXPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    bundle: LegacyMMWXIdentityBundle
    replace_existing: bool = Field(default=False, strict=True)
    package_mappings: dict[int, UUID] = Field(default_factory=dict, max_length=10000)


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
    mapped_packages: int = 0
    assigned_plans: int = 0
    imported_profiles: int = 0
    replaced_profiles: int = 0
    skipped_profiles: int = 0
    imported_profile_assignments: int = 0
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False


class LegacyMMWXImportResponse(BaseModel):
    preview: LegacyMMWXImportPreview
    applied: Literal[True] = True
