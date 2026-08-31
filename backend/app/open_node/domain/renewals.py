from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    UUID4,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from open_node.domain.inventory import AgentCommandRead

RenewalStatus = Literal["pending", "approved", "rejected", "cancelled"]

RENEWAL_MESSAGES = {
    "renewal_invalid_request": "续费申请内容不正确，请检查后重试。",
    "renewal_not_found": "续费申请不存在。",
    "renewal_unavailable": "当前没有可续费的套餐。",
    "renewal_pending": "已有待审核的续费申请，请先查看处理结果。",
    "renewal_conflict": "申请已处理或套餐已变更，请重新读取后核对。",
    "renewal_wrong_passphrase": "续费口令不匹配，请与用户核对。",
    "renewal_access_conflict": "套餐访问权限无法安全更新，请先检查节点和用户状态。",
    "renewal_rate_limited": "续费操作过于频繁，请稍后重试。",
}


class RenewalError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(RENEWAL_MESSAGES[code])


def renewal_passphrase(value: SecretStr) -> SecretStr:
    text = value.get_secret_value().strip()
    if not 1 <= len(text) <= 256 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("Invalid renewal passphrase")
    return SecretStr(text)


class RenewalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID4
    passphrase: SecretStr

    _passphrase = field_validator("passphrase")(renewal_passphrase)


class RenewalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    confirm_reviewed: bool = Field(strict=True)
    passphrase: SecretStr | None = None

    @model_validator(mode="after")
    def validate_decision(self):
        if not self.confirm_reviewed:
            raise ValueError("Review confirmation required")
        if self.decision == "approve":
            if self.passphrase is None:
                raise ValueError("Renewal passphrase required")
            self.passphrase = renewal_passphrase(self.passphrase)
        elif self.passphrase is not None:
            raise ValueError("Rejection does not accept a passphrase")
        return self


class RenewalRead(BaseModel):
    id: UUID
    username: str
    plan_id: UUID
    plan_name: str
    previous_end_date: datetime | None
    renew_days: int
    status: RenewalStatus
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    new_end_date: datetime | None


class RenewalsResponse(BaseModel):
    requests: list[RenewalRead]
    total: int
    limit: int
    offset: int
    license_required: Literal[False] = False


class AccountRenewalsResponse(RenewalsResponse):
    eligible: bool
    unavailable_code: str | None
    plan_id: UUID | None
    plan_name: str | None
    renew_days: int | None
    plan_expires_at: datetime | None


class RenewalDecisionResponse(BaseModel):
    request: RenewalRead
    processed: bool
    commands: list[AgentCommandRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    license_required: Literal[False] = False
