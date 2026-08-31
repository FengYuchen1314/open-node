"""Plain-text public branding contracts with fixed, non-echoing errors."""

import unicodedata
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

DEFAULT_SITE_TITLE = "Open Node"
DEFAULT_BRAND_TITLE = "Open Node"
BRANDING_MAX_REVISION = 2**53 - 1
BRANDING_ERROR_MESSAGES = {
    "branding_invalid_request": "Invalid branding settings request.",
    "branding_revision_conflict": "Branding settings changed; reload before saving.",
    "branding_storage_unavailable": "Branding settings storage is temporarily unavailable.",
}


class BrandingError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code if code in BRANDING_ERROR_MESSAGES else "branding_invalid_request"
        super().__init__(BRANDING_ERROR_MESSAGES[self.code])


def validate_branding_title(value: object, maximum: int) -> str:
    """Preserve Unicode text; reject hidden controls before trimming outer spaces."""
    invalid = BRANDING_ERROR_MESSAGES["branding_invalid_request"]
    if type(value) is not str:
        raise ValueError(invalid)
    visible = False
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cs", "Zl", "Zp"} or (
            category == "Cf" and character not in {"\u200c", "\u200d"}
        ):
            raise ValueError(invalid)
        visible = visible or category[0] in {"L", "N", "P", "S"}
    result = value.strip()
    if not visible or not 1 <= len(result) <= maximum:
        raise ValueError(invalid)
    return result


class _BrandingModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True,
        frozen=True, revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_unknown_fields(cls, value):
        # Even an attacker-controlled *field name* must not appear in a rendered
        # validation error. API callers still translate validation to the fixed
        # branding_invalid_request response instead of serializing error details.
        if isinstance(value, dict) and any(key not in cls.model_fields for key in value):
            raise ValueError(BRANDING_ERROR_MESSAGES["branding_invalid_request"])
        return value


class _BrandingText(_BrandingModel):
    site_title: StrictStr
    brand_title: StrictStr

    @field_validator("site_title", mode="before")
    @classmethod
    def site_text(cls, value):
        return validate_branding_title(value, 80)

    @field_validator("brand_title", mode="before")
    @classmethod
    def brand_text(cls, value):
        return validate_branding_title(value, 40)


class BrandingPublicRead(_BrandingText):
    license_required: Literal[False] = False

    @field_validator("license_required", mode="before")
    @classmethod
    def free_branding(cls, value):
        if value is not False:
            raise ValueError(BRANDING_ERROR_MESSAGES["branding_invalid_request"])
        return value


class BrandingSettingsRead(BrandingPublicRead):
    revision: StrictInt = Field(ge=0, le=BRANDING_MAX_REVISION)


class BrandingSettingsUpdate(_BrandingText):
    expected_revision: StrictInt = Field(ge=0, le=BRANDING_MAX_REVISION)
