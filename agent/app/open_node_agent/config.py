import os
import ssl
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    master_url: str
    token: SecretStr = Field(min_length=1)
    connection_mode: Literal["auto", "websocket", "http"] = "auto"
    allow_insecure_http: bool = False
    ca_file: Path | None = None
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    state_dir: Path = Path("/var/lib/open-node-agent")
    xray_binary: Path = Path("/usr/local/bin/xray")
    xray_config: Path = Path("/etc/open-node-agent/xray.json")
    runtime_mode: Literal["managed", "systemd"] = "managed"
    xray_service: str = Field(
        default="open-node-xray.service", pattern=r"^[a-zA-Z0-9_.@-]+\.service$"
    )
    auto_start: bool = True
    stats_address: str | None = None
    heartbeat_seconds: float = Field(default=15, ge=1, le=300)
    telemetry_seconds: float = Field(default=30, ge=1, le=300)
    poll_seconds: float = Field(default=3, ge=0.2, le=60)

    @field_validator("state_dir", "xray_binary", "xray_config", "ca_file")
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Agent filesystem paths must be absolute")
        return value

    @model_validator(mode="after")
    def validate_master(self):
        url = urlsplit(self.master_url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("master_url must be an HTTP(S) URL")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError(
                "master_url cannot contain credentials, query parameters, or fragments"
            )
        if url.scheme == "http" and not self.allow_insecure_http:
            raise ValueError("Use HTTPS; allow_insecure_http is only for a trusted test network")
        self.master_url = self.master_url.rstrip("/")
        return self

    def websocket_url(self) -> str:
        parts = urlsplit(self.master_url)
        return urlunsplit(
            (
                "wss" if parts.scheme == "https" else "ws",
                parts.netloc,
                parts.path + "/api/v1/agents/ws",
                "",
                "",
            )
        )

    def tls_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=str(self.ca_file) if self.ca_file else None)


def load_config(path: Path) -> AgentConfig:
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Agent configuration contains a token; set its permissions to 0600")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        raise ValueError("Invalid YAML configuration syntax") from None
    return AgentConfig.model_validate(data)
