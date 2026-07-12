"""Lovable AI Gateway provider (OpenAI-compatible endpoint).

The gateway's gpt-5.x models reject ``max_tokens`` (they require
``max_completion_tokens``) and reject any ``temperature`` other than the default.
That behaviour is now handled generically by the capability registry — the
model id (e.g. ``openai/gpt-5.5``) resolves to a reasoning capability that emits
``max_completion_tokens`` and omits ``temperature`` — so this provider only needs
to point the base OpenAI client at the gateway URL.
"""

from __future__ import annotations

from .openai_provider import OpenAIProvider

LOVABLE_BASE_URL = "https://ai.gateway.lovable.dev/v1"


class LovableProvider(OpenAIProvider):
    provider_key = "lovable"

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str = LOVABLE_BASE_URL,
        thinking_level: str | None = None,
    ) -> None:
        super().__init__(
            model, temperature, max_tokens, api_key, base_url,
            thinking_level=thinking_level,
        )
