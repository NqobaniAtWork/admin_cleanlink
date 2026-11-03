import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ADMIN_ROOT = Path(__file__).resolve().parent.parent
ADMIN_ENV_FILE = ADMIN_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=ADMIN_ENV_FILE if ADMIN_ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="production", alias="CLEANLINK_ADMIN_ENV")
    session_secret: str = Field(
        default="cleanlink_admin_session_secret_key_8f3a9b2c1d4e5f60718293a4b5c6d7e8",
        min_length=32,
        alias="CLEANLINK_ADMIN_SESSION_SECRET",
    )
    secure_cookies: bool = Field(default=False, alias="CLEANLINK_ADMIN_SECURE_COOKIES")
    supabase_url: str = Field(
        default="https://yqeroiikkywsegokoncp.supabase.co",
        alias="SUPABASE_URL",
    )
    supabase_service_role_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    mapbox_public_token: str | None = Field(default=None, alias="MAPBOX_PUBLIC_TOKEN")


@lru_cache
def get_settings() -> Settings:
    return Settings()
