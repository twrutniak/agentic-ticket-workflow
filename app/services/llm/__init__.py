from app.config import Settings
from app.services.llm.base import LLMClient


def build_llm(settings: Settings) -> LLMClient:
    """Pick the LLM implementation from LLM_MODE."""
    if settings.llm_mode == "fake":
        from app.services.llm.fake import FakeLLM

        return FakeLLM(failure_rate=settings.llm_fake_failure_rate)

    from app.services.llm.openai_compat import OpenAICompatibleLLM

    if settings.llm_api_key is None:
        raise ValueError("LLM_MODE=real needs LLM_API_KEY")
    return OpenAICompatibleLLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout=settings.llm_timeout_seconds,
    )
