from functools import lru_cache

from pydantic import Field, field_validator
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
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
