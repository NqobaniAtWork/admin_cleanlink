from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ADMIN_ROOT = Path(__file__).resolve().parent.parent
ADMIN_ENV_FILE = ADMIN_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=ADMIN_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    environment: str = Field(default="development", alias="CLEANLINK_ADMIN_ENV")
    session_secret: str = Field(min_length=32, alias="CLEANLINK_ADMIN_SESSION_SECRET")
    secure_cookies: bool = Field(default=True, alias="CLEANLINK_ADMIN_SECURE_COOKIES")
    supabase_url: str = Field(min_length=12, alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(
        min_length=20,
        validation_alias=AliasChoices("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    mapbox_public_token: str | None = Field(default=None, alias="MAPBOX_PUBLIC_TOKEN")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
