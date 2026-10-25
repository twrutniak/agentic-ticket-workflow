import pytest
from pydantic import ValidationError

from app.config import Settings


def test_fake_mode_needs_no_llm_settings():
    assert Settings(_env_file=None, llm_mode="fake").llm_base_url is None


def test_real_mode_requires_endpoint_model_and_key():
    with pytest.raises(ValidationError, match="LLM_BASE_URL, LLM_MODEL, LLM_API_KEY"):
        Settings(_env_file=None, llm_mode="real")
    with pytest.raises(ValidationError, match="needs LLM_API_KEY"):
        Settings(_env_file=None, llm_mode="real", llm_base_url="http://llm/v1", llm_model="m")


def test_real_mode_with_everything_set():
    settings = Settings(_env_file=None, llm_mode="real", llm_base_url="http://llm/v1", llm_model="m", llm_api_key="k")
    assert settings.llm_api_key.get_secret_value() == "k"
