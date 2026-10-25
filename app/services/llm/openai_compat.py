"""LLM client for any OpenAI-compatible chat completions API.

Works with OpenRouter, OpenAI, Azure OpenAI (v1 endpoint), Ollama, vLLM, LM Studio, ... by
changing LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.
"""

from __future__ import annotations

from typing import Any

import openai

from app.services.llm.base import LLMResponse, PermanentLLMError, ToolCall, TransientLLMError


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0) -> None:
        # max_retries=0: retries are owned by the Temporal activity retry policy.
        self._client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)
        self._model = model

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )
        except (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError, openai.InternalServerError) as e:
            raise TransientLLMError(f"{type(e).__name__}: {e}") from e
        except openai.APIStatusError as e:
            # 408 and 409 are temporary too (the SDK itself would retry them).
            if e.status_code in (408, 409):
                raise TransientLLMError(f"{type(e).__name__} (HTTP {e.status_code}): {e}") from e
            raise PermanentLLMError(f"{type(e).__name__} (HTTP {e.status_code}): {e}") from e

        if not response.choices:
            raise TransientLLMError("empty response from model")
        message = response.choices[0].message
        return LLMResponse(
            content=message.content,
            tool_calls=[
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
                for tc in (message.tool_calls or [])
                if tc.type == "function"
            ],
        )
