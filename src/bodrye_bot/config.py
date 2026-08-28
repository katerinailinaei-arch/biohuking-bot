from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderName(StrEnum):
    GROQ = "groq"
    OPENAI = "openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    telegram_bot_token: SecretStr
    telegram_owner_id: int
    telegram_channel_id: int
    groq_api_key: SecretStr
    openai_api_key: SecretStr | None = None

    llm_provider: ProviderName = ProviderName.GROQ
    llm_model: str = "openai/gpt-oss-120b"
    paid_fallback_enabled: Literal[False] = False
    llm_connect_timeout_seconds: Literal[5] = 5
    llm_total_timeout_seconds: Literal[60] = 60
    llm_max_retries: Literal[2] = 2

    budget_soft_rub: Decimal = Decimal("3500")
    budget_hard_rub: Decimal = Decimal("5000")
    timezone: Literal["Europe/Moscow"] = "Europe/Moscow"

    database_url: SecretStr | None = None
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_safety_constraints(self) -> Self:
        if self.budget_soft_rub >= self.budget_hard_rub:
            raise ValueError("soft budget must be below hard budget")
        if self.llm_provider is ProviderName.OPENAI and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Required values are supplied by BaseSettings from the environment.
    return Settings()  # type: ignore[call-arg]
