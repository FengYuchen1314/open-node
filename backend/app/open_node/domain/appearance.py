"""Public presentation settings, not arbitrary configuration or executable CSS."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

MAX_REVISION = 2**53 - 1
ASSET_BASE = "/api/v1/appearance/assets"
ASSET_LIMITS = {"logo": 2 * 1024 * 1024, "wallpaper": 10 * 1024 * 1024}
MESSAGES = {
    "appearance_invalid_request": "Invalid appearance settings.",
    "appearance_revision_conflict": "Appearance settings changed. Read them again.",
    "appearance_invalid_image": "Unsupported, invalid or excessive image content.",
    "appearance_storage_unavailable": "Appearance storage is temporarily unavailable.",
    "appearance_asset_missing": "Appearance image is not available.",
}


class AppearanceError(ValueError):
    def __init__(self, status_code, code):
        self.status_code = status_code
        self.code = code if code in MESSAGES else "appearance_storage_unavailable"
        super().__init__(MESSAGES[self.code])


def image_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("Invalid public image URL")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ValueError("Use an encoded public image URL")
    if not value or re.fullmatch(ASSET_BASE + r"/(logo|wallpaper)/[a-f0-9]{64}", value):
        return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or "\\" in value):
            raise ValueError()
        if parsed.port not in (None, 443):
            raise ValueError()
        # These are browser image URLs, never server-side fetch targets. Do not
        # accept alternate parser interpretations or local paths as image URLs.
        if not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname):
            raise ValueError()
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith(".local"):
            raise ValueError()
    except ValueError:
        raise ValueError("Invalid public HTTPS image URL") from None
    return value


class AppearancePublic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True,
                              frozen=True, revalidate_instances="always")
    default_theme: Literal["light", "dark", "system"] = "light"
    logo_url: str = ""
    wallpaper_url: str = ""
    license_required: Literal[False] = False

    @field_validator("logo_url", "wallpaper_url", mode="before")
    @classmethod
    def url(cls, value):
        return image_url(value)


class AppearanceSettings(AppearancePublic):
    revision: StrictInt = Field(ge=0, le=MAX_REVISION)


class AppearanceUpdate(AppearancePublic):
    expected_revision: StrictInt = Field(ge=0, le=MAX_REVISION)
