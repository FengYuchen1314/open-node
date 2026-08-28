import re

from pydantic import BaseModel, Field, field_validator

RESERVED_SHORT_CODES = frozenset(
    {
        "admin",
        "root",
        "system",
        "api",
        "share",
        "test",
        "user",
        "guest",
        "null",
        "www",
        "mmw",
        "mmwx",
        "open-node",
        "opennode",
        "account",
        "subscribe",
        "assets",
        "healthz",
    }
)


class SubscriptionShortCodeUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    custom_short_code: str = Field(strict=True, max_length=128)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("custom_short_code")
    @classmethod
    def validate_code(cls, value):
        value = value.strip()
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{2,16}", value):
            raise ValueError("Use 2-16 ASCII letters, digits, underscores or hyphens")
        if value.lower() in RESERVED_SHORT_CODES:
            raise ValueError("This short code is reserved")
        return value
