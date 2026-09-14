from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root .env (this file lives at backend/app/core/config.py).
_PROJECT_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Backend configuration loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    MARITALK_API_KEY: SecretStr
    MARITALK_API_BASE: str
    MARITALK_MODEL: str

    ADMIN_API_TOKEN: SecretStr
    WHATSAPP_NUMBER: str = "5500000000000"

    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ]

    RATE_LIMIT_PER_MINUTE: int = 15
    RATE_LIMIT_PER_SESSION: int = 60

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value
