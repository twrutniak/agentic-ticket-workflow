"""Provider-independent LLM interface.

Messages and tool definitions use the OpenAI chat-completions shape, because it is the common
format most providers and gateways accept. Nothing outside app/services/llm imports a provider SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, exactly as the model produced it


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class TransientLLMError(Exception):
    """Network error, timeout, rate limit, 5xx: worth retrying."""


class PermanentLLMError(Exception):
    """Bad request, auth error, unknown model: retrying will not help."""


class LLMClient(Protocol):
    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...
