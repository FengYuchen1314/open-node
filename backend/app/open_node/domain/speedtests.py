"""Administrator node speed-test contracts matching the official MMWX workflow."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

SPEEDTEST_MESSAGES = {
    "speedtest_node_not_found": "节点不存在。",
    "speedtest_node_unavailable": "节点未启用，无法测速。",
    "speedtest_credential_unavailable": (
        "该节点还没有可用于测速的已下发凭据，请先给套餐用户分配节点并完成同步。"
    ),
    "speedtest_invalid_request": "测速请求无效。",
    "speedtest_tester_not_found": "测速端不存在。",
    "speedtest_tester_offline": "测速端当前不在线。",
    "speedtest_tester_busy": "测速端正在执行其他任务，请稍后重试。",
    "speedtest_runtime_unavailable": "本机测速核心暂不可用。",
    "speedtest_download_failed": "测速下载失败，请检查节点连通性。",
    "speedtest_latency_failed": "延迟测试失败，请检查节点连通性。",
    "speedtest_dispatch_failed": "测速任务发送失败，请检查测速端连接。",
    "speedtest_timeout": "测速任务超时。",
    "speedtest_storage_unavailable": "测速记录暂时不可用。",
}


class SpeedTestError(ValueError):
    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code, self.code = status_code, code


class SpeedTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SpeedTestRunRequest(SpeedTestInput):
    node_id: UUID
    bytes: int = Field(default=0, ge=0, le=2_147_483_648)
    url: HttpUrl | None = Field(default=None, max_length=2048)
    tester_id: UUID | None = None
    threads: int = Field(default=1, ge=1, le=64)
    buf_size: int = Field(default=1_048_576, ge=65_536, le=16_777_216)
    latency_only: bool = False

    @field_validator("url")
    @classmethod
    def https_download_url(cls, value):
        if value is not None and value.scheme != "https":
            raise ValueError("Speed-test URL must use HTTPS")
        return value


class SpeedTestResultRead(BaseModel):
    id: UUID
    node_id: UUID
    node_name: str
    source: Literal["master", "tester"]
    tester_id: UUID | None = None
    tester_name: str | None = None
    status: Literal["running", "ok", "failed"]
    down_mbps: float | None = None
    latency_ms: float | None = None
    egress_ip: str | None = None
    bytes: int
    error_code: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    license_required: Literal[False] = False


class SpeedTestRunAccepted(BaseModel):
    result: SpeedTestResultRead
    queued: Literal[True] = True
    license_required: Literal[False] = False


class SpeedTestResultsRead(BaseModel):
    results: list[SpeedTestResultRead]
    license_required: Literal[False] = False


class SpeedTesterCreate(SpeedTestInput):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Tester name is required")
        return normalized


class SpeedTesterMutation(SpeedTestInput):
    id: UUID


class SpeedTesterRead(BaseModel):
    id: UUID
    name: str
    online: bool
    caps: list[str]
    version: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    created_by: str
    license_required: Literal[False] = False


class SpeedTesterSecret(BaseModel):
    tester: SpeedTesterRead
    token: str
    websocket_path: str = "/api/speedtest/ws"
    license_required: Literal[False] = False


class SpeedTestersRead(BaseModel):
    testers: list[SpeedTesterRead]
    license_required: Literal[False] = False


class MihomoStatusRead(BaseModel):
    supported: bool
    ready: bool
    version: str
    platform: str
    downloading: bool = False
    message: str
    license_required: Literal[False] = False
