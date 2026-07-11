"""Google Gemini provider (via the OpenAI-compatible endpoint)."""

from __future__ import annotations

from .openai_provider import OpenAIProvider

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class GeminiProvider(OpenAIProvider):
    """Gemini exposes an OpenAI-compatible API, so we reuse that client."""

    def __init__(self, model: str, temperature: float, max_tokens: int, api_key: str) -> None:
        super().__init__(model, temperature, max_tokens, api_key, GEMINI_BASE_URL)
