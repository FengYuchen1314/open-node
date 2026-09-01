"""Global subscriber feature policy and per-user creation quotas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

SubscriberFeature = Literal[
    "templates", "external_subscriptions", "private_routes", "renewals"
]
FEATURES: tuple[SubscriberFeature, ...] = (
    "templates", "external_subscriptions", "private_routes", "renewals",
)
MAX_REVISION = 2**53 - 1
MAX_QUOTA = 1000

MESSAGES = {
    "subscriber_permissions_invalid_request": "用户功能权限请求无效。",
    "subscriber_permissions_revision_conflict": "用户功能权限已变化，请重新读取。",
    "subscriber_permissions_storage_unavailable": "用户功能权限暂时不可用。",
    "subscriber_feature_disabled": "管理员未开放此账户功能。",
    "subscriber_quota_exceeded": "此账户已达到管理员设置的数量上限。",
}


class SubscriberPermissionsError(ValueError):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code if code in MESSAGES else "subscriber_permissions_storage_unavailable"
        super().__init__(MESSAGES[self.code])


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True,
        frozen=True, revalidate_instances="always",
    )


class SubscriberPermissionsPolicy(StrictModel):
    pages: list[SubscriberFeature]
    template_quota: StrictInt = Field(ge=0, le=MAX_QUOTA)
    external_source_quota: StrictInt = Field(ge=0, le=MAX_QUOTA)
    license_required: Literal[False] = False

    @field_validator("pages")
    @classmethod
    def canonical_pages(cls, value):
        if len(value) != len(set(value)) or value != [item for item in FEATURES if item in value]:
            raise ValueError("Pages must be unique and canonical")
        return value


class SubscriberPermissionsSettings(SubscriberPermissionsPolicy):
    revision: StrictInt = Field(ge=0, le=MAX_REVISION)


class SubscriberPermissionsUpdate(SubscriberPermissionsPolicy):
    expected_revision: StrictInt = Field(ge=0, le=MAX_REVISION)


class SubscriberQuotaUsage(StrictModel):
    used: StrictInt = Field(ge=0)
    maximum: StrictInt = Field(ge=0, le=MAX_QUOTA)


class SubscriberPermissionsAccount(StrictModel):
    pages: list[SubscriberFeature]
    templates: SubscriberQuotaUsage
    external_sources: SubscriberQuotaUsage
    license_required: Literal[False] = False
