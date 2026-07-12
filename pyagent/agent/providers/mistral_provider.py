"""Mistral AI provider (OpenAI-compatible endpoint)."""

from __future__ import annotations

from .openai_provider import OpenAIProvider

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"


class MistralProvider(OpenAIProvider):
    provider_key = "mistral"

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        thinking_level: str | None = None,
    ) -> None:
        super().__init__(
            model, temperature, max_tokens, api_key, MISTRAL_BASE_URL,
            thinking_level=thinking_level,
        )
