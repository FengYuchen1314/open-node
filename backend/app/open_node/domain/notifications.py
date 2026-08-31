"""Secret-free notification contracts and fixed, safe operational error codes."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

NOTIFICATION_ERROR_MESSAGES = {
    "notification_invalid_request": "Invalid notification request.",
    "notification_revision_conflict": "Notification settings changed; reload before continuing.",
    "notification_request_conflict": "This request identifier was used for another operation.",
    "notification_attempt_conflict": "The delivery attempt changed; reload before continuing.",
    "notification_not_found": "Notification delivery was not found.",
    "notification_request_not_found": "No receipt was found for this request identifier.",
    "notification_not_configured": "Save a bot token and chat identifier before sending.",
    "notification_disabled": "Package expiry notifications are disabled.",
    "notification_storage_unavailable": "Notification secret storage is unavailable.",
    "notification_storage_key_missing": "Restore the original notification key before continuing.",
    "notification_storage_key_invalid": "The notification key cannot decrypt the saved settings.",
    "notification_storage_permissions": "Notification secret storage must be private and owned.",
    "notification_retry_not_allowed": "This delivery cannot be retried in its current state.",
    "notification_retry_too_early": "Wait for the previous attempt deadline before retrying.",
    "notification_duplicate_risk_required": "Acknowledge the risk of duplicate delivery to retry.",
    "notification_no_longer_eligible": "This package expiry notification is no longer eligible.",
    "notification_database_unavailable": "Notification storage is temporarily unavailable.",
    "notification_already_accepted": (
        "An earlier attempt was accepted; duplicate queued work was cancelled."
    ),
}


class NotificationError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code if code in NOTIFICATION_ERROR_MESSAGES else "notification_invalid_request"
        super().__init__(NOTIFICATION_ERROR_MESSAGES[self.code])


def validate_bot_token(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,19}:[A-Za-z0-9_-]{20,128}", value, flags=re.ASCII):
        raise ValueError("Invalid Telegram bot token.")
    return value


def validate_chat_id(value: str) -> str:
    if value and (
        not re.fullmatch(r"-?[1-9][0-9]{0,18}", value, flags=re.ASCII)
        or abs(int(value)) > 2**52 - 1
    ):
        raise ValueError("The Telegram chat identifier must be a numeric string.")
    return value


class NotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class NotificationRevisionRequest(NotificationRequest):
    expected_revision: StrictInt = Field(ge=0)


class NotificationSettingsUpdate(NotificationRevisionRequest):
    enabled: StrictBool
    chat_id: StrictStr = Field(max_length=20)
    advance_days: StrictInt = Field(default=7, ge=1, le=365)
    timezone: StrictStr = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    local_time: Literal["09:00"] = "09:00"
    token_action: Literal["keep", "replace", "clear"] = "keep"
    token: SecretStr | None = Field(default=None, repr=False)

    _chat = field_validator("chat_id")(validate_chat_id)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("An installed IANA time zone is required.") from None
        return value

    @field_validator("token")
    @classmethod
    def valid_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            validate_bot_token(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def valid_combination(self) -> Self:
        if (self.token_action == "replace") != (self.token is not None):
            raise ValueError("Provide a token only when replacing it.")
        if self.enabled and (not self.chat_id or self.token_action == "clear"):
            raise ValueError("Enabled notifications require a chat identifier and a saved token.")
        return self


class NotificationTestRequest(NotificationRevisionRequest):
    request_id: UUID


class NotificationRetryRequest(NotificationTestRequest):
    expected_attempt_id: UUID
    confirm_duplicate_risk: StrictBool = False


class NotificationSettingsRead(BaseModel):
    revision: int = Field(ge=0)
    enabled: bool
    has_token: bool
    chat_id: str
    advance_days: int
    timezone: str
    local_time: Literal["09:00"] = "09:00"
    destination_revision: int
    storage_ready: bool
    storage_error: str | None = None
    license_required: Literal[False] = False


class NotificationCandidate(BaseModel):
    username: str
    plan_id: UUID
    plan_name: str
    expires_at: datetime


class NotificationPreviewRead(BaseModel):
    revision: int
    as_of: datetime
    timezone: str
    local_time: Literal["09:00"] = "09:00"
    enabled: bool
    chat_id: str
    total: int
    candidates: list[NotificationCandidate] = Field(max_length=20)
    sample_message: str
    is_sample: bool
    license_required: Literal[False] = False


NotificationState = Literal["queued", "sending", "accepted", "failed", "unknown", "cancelled"]


class NotificationAttemptRead(BaseModel):
    id: UUID
    delivery_id: UUID
    state: Literal["sending", "accepted", "failed", "unknown"]
    attempt_number: int
    config_revision: int
    destination_revision: int
    chat_id: str
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime | None
    code: str | None
    message_id: int | None
    retry_after: int | None
    retryable: bool
    # Recovery can mark an attempt unknown before a late worker receipt arrives.
    late_receipt_at: datetime | None = None


class NotificationDeliveryRead(BaseModel):
    id: UUID
    kind: Literal["package_expiry", "test"]
    state: NotificationState
    config_revision: int
    destination_revision: int
    request_id: UUID | None
    chat_id: str
    username: str | None
    plan_id: UUID | None
    plan_name: str | None
    expires_at: datetime | None
    last_attempt_id: UUID | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime | None
    retry_available_at: datetime | None
    manual_retry_allowed: bool
    code: str | None
    message_id: int | None
    license_required: Literal[False] = False


class NotificationDeliveryDetail(BaseModel):
    delivery: NotificationDeliveryRead
    attempts: list[NotificationAttemptRead]
    license_required: Literal[False] = False


class NotificationDeliveriesResponse(BaseModel):
    deliveries: list[NotificationDeliveryRead]
    license_required: Literal[False] = False


@dataclass(frozen=True, slots=True, repr=False)
class ClaimedNotification:
    delivery_id: UUID
    attempt_id: UUID
    token: SecretStr
    chat_id: str
    text: str
    deadline_at: datetime


class NotificationOutcome(Protocol):
    state: Literal["accepted", "failed", "unknown"]
    code: str
    message_id: int | None
    retry_after: int | None
    retryable: bool
