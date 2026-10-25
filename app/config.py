"""Settings from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Temporal
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "tickets"

    # LLM: "fake" needs no key and no network; "real" talks to any OpenAI-compatible
    # chat completions endpoint (OpenRouter, OpenAI, Azure OpenAI v1, Ollama, ...).
    # No provider is assumed: in "real" mode the endpoint, model and key must all be set.
    llm_mode: Literal["fake", "real"] = "fake"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_fake_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    # Fake Helpdesk API
    helpdesk_failure_rate: float = Field(default=0.2, ge=0.0, le=1.0)

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _real_llm_is_configured(self) -> Settings:
        if self.llm_mode == "real":
            missing = [
                name.upper()
                for name in ("llm_base_url", "llm_model", "llm_api_key")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"LLM_MODE=real needs {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
