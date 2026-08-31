"""One-time first-administrator setup; no input is reflected in public errors."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator

from open_node.domain.auth import AdministratorCredentials
from open_node.domain.branding import (
    DEFAULT_BRAND_TITLE,
    DEFAULT_SITE_TITLE,
    validate_branding_title,
)

SETUP_MESSAGES = {
    "setup_invalid_request": "Invalid initial setup request.",
    "setup_already_completed": "Initial setup is already completed. Sign in or use local recovery.",
    "setup_ticket_invalid": "Initial setup credential is invalid or expired.",
    "setup_unavailable": "Initial setup is temporarily unavailable.",
    "setup_rate_limited": "Too many initial setup attempts. Try again later.",
}


class InitialSetupError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code if code in SETUP_MESSAGES else "setup_unavailable"
        super().__init__(SETUP_MESSAGES[self.code])


class InitialSetupRequest(AdministratorCredentials):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)

    setup_token: SecretStr = Field(min_length=43, max_length=43)
    site_title: str = DEFAULT_SITE_TITLE
    brand_title: str = DEFAULT_BRAND_TITLE
    confirm_new_install: StrictBool

    @field_validator("setup_token")
    @classmethod
    def token(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value.get_secret_value()):
            raise ValueError("Invalid initial setup credential")
        return value

    @field_validator("site_title", mode="before")
    @classmethod
    def site(cls, value):
        return validate_branding_title(value, 80)

    @field_validator("brand_title", mode="before")
    @classmethod
    def brand(cls, value):
        return validate_branding_title(value, 40)

    @field_validator("confirm_new_install")
    @classmethod
    def confirm(cls, value):
        if not value:
            raise ValueError("Explicit first-administrator confirmation is required")
        return value


class InitialSetupStatus(BaseModel):
    configured: bool
    available: bool
    expires_at: datetime | None = None
    token_required: Literal[True] = True


class InitialSetupResult(BaseModel):
    configured: Literal[True] = True
    login_required: Literal[True] = True
