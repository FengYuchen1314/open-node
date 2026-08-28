import os
import re
import ssl
from ipaddress import ip_address
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
    lifecycle_socket: Path | None = None
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    state_dir: Path = Path("/var/lib/open-node-agent")
    xray_binary: Path = Path("/usr/local/bin/xray")
    xray_config: Path = Path("/etc/open-node-agent/xray.json")
    runtime_mode: Literal["managed", "systemd"] = "managed"
    allow_xray_takeover: bool = False
    xray_service: str = Field(
        default="open-node-xray.service", pattern=r"^[a-zA-Z0-9_.@-]+\.service$"
    )
    auto_start: bool = True
    nginx_binary: Path | None = None
    nginx_modules: list[Path] = Field(default_factory=list, max_length=16)
    nginx_site_roots: list[Path] = Field(default_factory=list, max_length=16)
    nginx_http_port: int = Field(default=80, ge=1, le=65535)
    nginx_https_port: int = Field(default=443, ge=1, le=65535)
    nginx_listen_address: str = "0.0.0.0"
    nexttrace_binary: Path | None = None
    nexttrace_geoip: bool = True
    certificate_http_address: str | None = None
    certificate_webroots: list[str] = Field(default_factory=list, max_length=16)
    stats_address: str | None = None
    heartbeat_seconds: float = Field(default=15, ge=1, le=300)
    telemetry_seconds: float = Field(default=30, ge=1, le=300)
    poll_seconds: float = Field(default=3, ge=0.2, le=60)

    @field_validator("certificate_http_address")
    @classmethod
    def challenge_listener(cls, value):
        if value is None:
            return value
        address = urlsplit("http://" + value)
        if (
            not address.hostname
            or not address.port
            or address.username
            or address.password
            or address.path
            or address.query
            or address.fragment
        ):
            raise ValueError("HTTP challenge listener requires a literal IP address and port")
        ip_address(address.hostname)
        return value

    @field_validator("certificate_webroots")
    @classmethod
    def challenge_sites(cls, values):
        if len(set(values)) != len(values) or any(
            not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value) for value in values
        ):
            raise ValueError("HTTP challenge webroot IDs must be distinct safe directory names")
        return values

    @field_validator("nginx_listen_address")
    @classmethod
    def listen_address(cls, value: str) -> str:
        return str(ip_address(value))

    @field_validator(
        "state_dir",
        "xray_binary",
        "xray_config",
        "ca_file",
        "nginx_binary",
        "lifecycle_socket",
        "nexttrace_binary",
    )
    @classmethod
    def absolute_path(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Agent filesystem paths must be absolute")
        return value

    @field_validator("nginx_modules", "nginx_site_roots")
    @classmethod
    def absolute_paths(cls, value: list[Path]) -> list[Path]:
        if any(not path.is_absolute() or ".." in path.parts for path in value):
            raise ValueError("Nginx module and site paths must be absolute without traversal")
        return value

    @model_validator(mode="after")
    def validate_master(self):
        if self.nginx_modules and self.nginx_binary is None:
            raise ValueError("nginx_modules requires nginx_binary")
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
