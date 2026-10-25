from app.config import Settings
from app.services.llm.base import LLMClient


def build_llm(settings: Settings) -> LLMClient:
    """Pick the LLM implementation from LLM_MODE (Settings guarantees the real one is configured)."""
    if settings.llm_mode == "fake":
        from app.services.llm.fake import FakeLLM

        return FakeLLM(failure_rate=settings.llm_fake_failure_rate)

    from app.services.llm.openai_compat import OpenAICompatibleLLM

    assert settings.llm_base_url and settings.llm_model and settings.llm_api_key
    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )
