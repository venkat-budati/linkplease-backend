from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    aiven_ca_path: str | None = None
    pseudogram_base_url: str = "https://pseudogram-api.onrender.com"
    pseudogram_api_key: str = Field(validation_alias=AliasChoices("PSEUDOGRAM_API_KEY", "pseudogram_api_key"))
    worker_enabled: bool = True
    worker_poll_seconds: float = 1.0
    max_dm_attempts: int = 6
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 60
    reconcile_after_seconds: int = 5
    http_timeout_seconds: float = 10.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", populate_by_name=True)

    @field_validator("pseudogram_api_key")
    @classmethod
    def normalize_pseudogram_api_key(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
            normalized = normalized[1:-1].strip()
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()
