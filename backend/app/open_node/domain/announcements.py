"""Bounded plain-text Web announcements derived from the official instance flow."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AnnouncementType = Literal["general", "maintenance", "sub_update"]
ANNOUNCEMENT_TITLES = {
    "general": "公告",
    "maintenance": "系统维护",
    "sub_update": "订阅更新",
}
ANNOUNCEMENT_MESSAGES = {
    "announcement_invalid_request": "公告内容不正确，请检查后重试。",
    "announcement_not_found": "公告不存在或已被删除。",
    "announcement_storage_unavailable": "公告暂时不可用，请稍后重试。",
    "announcement_rate_limited": "公告操作过于频繁，请稍后重试。",
}


class AnnouncementError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code if code in ANNOUNCEMENT_MESSAGES else "announcement_invalid_request"
        self.status_code = status_code
        super().__init__(ANNOUNCEMENT_MESSAGES[self.code])


def _title(value: str) -> str:
    result = value.strip()
    if len(result) > 100 or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("Invalid announcement title")
    return result


def _body(value: str) -> str:
    result = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not 1 <= len(result) <= 2000:
        raise ValueError("Invalid announcement body")
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in result):
        raise ValueError("Invalid announcement body")
    return result


class AnnouncementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: AnnouncementType = "general"
    title: str = ""
    body: str
    expires_minutes: int = Field(default=0, ge=0, le=525_600, strict=True)

    _safe_title = field_validator("title")(_title)
    _safe_body = field_validator("body")(_body)

    @model_validator(mode="after")
    def default_title(self):
        if not self.title:
            self.title = ANNOUNCEMENT_TITLES[self.type]
        return self


class AnnouncementRead(BaseModel):
    id: UUID
    type: AnnouncementType
    title: str
    body: str
    created_at: datetime
    expires_at: datetime | None

    _safe_title = field_validator("title")(_title)
    _safe_body = field_validator("body")(_body)


class AnnouncementsResponse(BaseModel):
    announcements: list[AnnouncementRead]
    license_required: Literal[False] = False


class AnnouncementDeleteResponse(BaseModel):
    id: UUID
    deleted: Literal[True] = True
    license_required: Literal[False] = False
