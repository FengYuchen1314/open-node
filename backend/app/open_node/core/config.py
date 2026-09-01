import re
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Open Node"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./data/open-node.db"
    license_required: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]
    session_cookie_secure: bool = True
    session_lifetime_seconds: int = Field(default=43200, ge=60, le=604800)
    session_idle_seconds: int = Field(default=1800, ge=60, le=86400)
    subscriber_totp_key: SecretStr | None = None
    backup_temporary_directory: Path | None = None
    external_subscriptions_state_dir: Path | None = None
    geoip_ipinfo_token: SecretStr | None = None
    notifications_state_dir: Path | None = None
    notifications_poll_seconds: float = Field(default=1, ge=0.25, le=60)
    short_links_enabled: bool = False
    certificate_state_dir: Path = Path("./data/certificates")
    certificate_lego_binary: Path | None = None
    certificate_ca_file: Path | None = None
    certificate_acme_directories: list[str] = [
        "https://acme-v02.api.letsencrypt.org/directory",
        "https://acme-staging-v02.api.letsencrypt.org/directory",
    ]
    certificate_dns_resolvers: list[str] = []
    certificate_http_address: str | None = None
    certificate_webroots: dict[str, Path] = {}
    certificate_allow_loopback_http: bool = False
    certificate_poll_seconds: float = Field(default=30, ge=1, le=3600)
    certificate_job_timeout: int = Field(default=240, ge=5, le=600)
    frontend_dir: Path | None = None
    agent_identity_file: Path | None = None
    agent_bootstrap_public_url: str | None = None
    source_revision: str = "unknown"
    application_update_dir: Path | None = None
    application_update_state_owner_uid: int = Field(default=0, ge=0, le=2_147_483_647)
    application_update_state_group_gid: int = Field(default=10001, ge=0, le=2_147_483_647)
    subscription_access_poll_seconds: float = Field(default=10, ge=1, le=300)
    server_traffic_poll_seconds: float = Field(default=60, ge=1, le=300)

    @field_validator("agent_bootstrap_public_url", mode="before")
    @classmethod
    def bootstrap_public_url(cls, value):
        if value in (None, ""):
            return None
        from open_node.services.agent_bootstrap import normalize_control_url

        return normalize_control_url(value)

    @field_validator("backup_temporary_directory")
    @classmethod
    def backup_temporary_path(cls, value: Path | None) -> Path | None:
        if value is not None and (
            not value.is_absolute() or value == Path(value.anchor) or ".." in value.parts
        ):
            raise ValueError("Backup temporary directory requires an absolute non-root path")
        return value

    @field_validator("certificate_http_address")
    @classmethod
    def http_listener(cls, value):
        if value is None:
            return value
        host, separator, port = value.rpartition(":")
        if not separator or not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("HTTP challenge listener requires an IP address and port")
        if host.startswith("[") and host.endswith("]"):
            if ip_address(host[1:-1]).version != 6:
                raise ValueError("Bracketed HTTP challenge addresses must be IPv6")
        elif host:
            if ip_address(host).version != 4:
                raise ValueError("IPv6 HTTP challenge addresses require brackets")
        return value

    @field_validator("subscriber_totp_key")
    @classmethod
    def totp_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            return None
        if value is not None:
            from cryptography.fernet import Fernet

            try:
                Fernet(value.get_secret_value())
            except (ValueError, TypeError):
                raise ValueError("Subscriber TOTP key must be a Fernet key") from None
        return value

    @field_validator("geoip_ipinfo_token")
    @classmethod
    def geoip_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        token = value.get_secret_value()
        if len(token) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise ValueError("IPinfo token must contain 1-256 visible ASCII characters")
        return value

    @field_validator("certificate_webroots")
    @classmethod
    def webroot_paths(cls, values):
        for identifier, path in values.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
                raise ValueError("Webroot IDs require 1-64 letters, digits, underscores or hyphens")
            if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
                raise ValueError("Webroots must be absolute non-root paths without traversal")
        if len(set(values.values())) != len(values):
            raise ValueError("Webroot paths must be distinct")
        return values

    @field_validator("agent_identity_file", mode="before")
    @classmethod
    def optional_identity_file(cls, value):
        return None if value == "" else value

    @field_validator("external_subscriptions_state_dir", mode="before")
    @classmethod
    def optional_external_state(cls, value):
        return None if value == "" else value

    @field_validator("external_subscriptions_state_dir")
    @classmethod
    def external_state_path(cls, value: Path | None) -> Path | None:
        if value is not None and (
            not value.is_absolute() or value == Path(value.anchor) or ".." in value.parts
        ):
            raise ValueError("External subscription state requires an absolute non-root path")
        return value

    @field_validator("notifications_state_dir", mode="before")
    @classmethod
    def optional_notification_state(cls, value):
        return None if value == "" else value

    @field_validator("application_update_dir", mode="before")
    @classmethod
    def optional_application_update_dir(cls, value):
        return None if value == "" else value

    @field_validator("application_update_dir")
    @classmethod
    def application_update_path(cls, value: Path | None) -> Path | None:
        if value is not None and (
            not value.is_absolute() or value == Path(value.anchor) or ".." in value.parts
        ):
            raise ValueError("Application update state requires an absolute non-root path")
        return value

    @field_validator("source_revision")
    @classmethod
    def source_revision_value(cls, value: str) -> str:
        if value == "unknown" or re.fullmatch(r"[0-9a-f]{40}", value):
            return value
        raise ValueError("Source revision must be unknown or a full lowercase Git commit")

    @field_validator("notifications_state_dir")
    @classmethod
    def notification_state_path(cls, value: Path | None) -> Path | None:
        if value is not None and (
            not value.is_absolute() or value == Path(value.anchor) or ".." in value.parts
        ):
            raise ValueError("Notification state requires an absolute non-root path")
        return value

    @field_validator("agent_identity_file")
    @classmethod
    def absolute_identity_file(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Agent identity path must be absolute")
        return value

    @field_validator("frontend_dir")
    @classmethod
    def frontend_path(cls, value: Path | None) -> Path | None:
        if value is not None and (not value.is_absolute() or value == Path(value.anchor)):
            raise ValueError(
                "Frontend directory must be absolute and cannot be the filesystem root"
            )
        return value

    @field_validator("certificate_lego_binary", "certificate_ca_file")
    @classmethod
    def absolute_certificate_file(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("Certificate runtime paths must be absolute")
        return value

    @field_validator("cors_origins")
    @classmethod
    def no_wildcard_origins(cls, origins: list[str]) -> list[str]:
        if any("*" in origin for origin in origins):
            raise ValueError("Authenticated CORS requires explicit origins")
        return origins

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OPEN_NODE_",
        extra="ignore",
        hide_input_in_errors=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
