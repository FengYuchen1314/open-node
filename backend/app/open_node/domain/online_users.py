from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Literal

from pydantic import BaseModel, Field

OnlineStatus = Literal["ready", "limited", "not_configured", "stopped", "unsupported", "error"]


class OnlineCollectionReport(BaseModel):
    status: OnlineStatus
    source: Literal["xray_stats_api"] = "xray_stats_api"
    interval_seconds: float = Field(default=30, ge=1, le=300, allow_inf_nan=False)


class OnlineCollectionRead(BaseModel):
    status: OnlineStatus | Literal["unknown", "stale"] = "unknown"
    source: Literal["xray_stats_api"] | None = None
    received_at: datetime | None = None
    expires_at: datetime | None = None


def validate_online_users(value):
    if not isinstance(value, dict) or len(value) > 256:
        raise ValueError("Online users must be an object with at most 256 entries")
    result = {}
    count = 0
    for email, values in value.items():
        if not isinstance(email, str) or not 1 <= len(email) <= 255:
            raise ValueError("Invalid online user identity")
        if ">>>" in email or any(ord(char) < 32 or ord(char) == 127 for char in email):
            raise ValueError("Invalid online user identity")
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError("Online IP list must contain at most 64 entries")
        count += len(values)
        if count > 4096:
            raise ValueError("Online sample exceeds 4096 IP entries")
        ips = set()
        for value in values:
            if not isinstance(value, str) or len(value) > 45 or "%" in value:
                raise ValueError("Invalid online IP literal")
            try:
                address = ip_address(value)
            except ValueError:
                raise ValueError("Invalid online IP literal") from None
            ips.add(str(getattr(address, "ipv4_mapped", None) or address))
        if ips:
            result[email] = sorted(ips)
    return result


def read_online_collection(value: dict | None, received_at: datetime) -> OnlineCollectionRead:
    if not value:
        return OnlineCollectionRead()
    collection = OnlineCollectionReport.model_validate(value)
    received_at = received_at.replace(tzinfo=UTC) if received_at.tzinfo is None else received_at
    expires = received_at + timedelta(seconds=max(90, collection.interval_seconds * 3))
    return OnlineCollectionRead(
        status="stale" if datetime.now(UTC) >= expires else collection.status,
        source=collection.source,
        received_at=received_at,
        expires_at=expires,
    )
