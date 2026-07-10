"""Lovable AI Gateway provider (OpenAI-compatible endpoint).

The gateway's gpt-5.x models differ from the vanilla OpenAI chat API in two
ways, so we override ``chat`` rather than reuse the base implementation as-is:

* they reject ``max_tokens`` and require ``max_completion_tokens`` instead;
* they reject any ``temperature`` other than the default (1).
"""

from __future__ import annotations

from typing import Any, Callable

from .base import LLMResponse
from .openai_provider import OpenAIProvider

LOVABLE_BASE_URL = "https://ai.gateway.lovable.dev/v1"


class LovableProvider(OpenAIProvider):
    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str = LOVABLE_BASE_URL,
    ) -> None:
        super().__init__(model, temperature, max_tokens, api_key, base_url)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            # Gateway wants max_completion_tokens; temperature is fixed at 1.
            "max_completion_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if on_delta is None:
            return self._chat_blocking(kwargs)
        return self._chat_stream(kwargs, on_delta)
