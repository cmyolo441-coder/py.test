"""Factory that builds the right provider from a Config object."""

from __future__ import annotations

import os

from ..config import Config
from .base import LLMProvider


class ProviderError(RuntimeError):
    pass


def _openai_compatible_provider():
    try:
        from .openai_provider import OpenAIProvider
        return OpenAIProvider
    except ModuleNotFoundError as exc:
        if exc.name != "openai":
            raise
        from .http_openai_provider import HttpOpenAIProvider
        return HttpOpenAIProvider


def get_provider(config: Config) -> LLMProvider:
    provider = config.provider.lower()
    model = config.resolved_model()
    temp = config.temperature
    max_tokens = config.max_tokens

    if provider == "openai":
        if not config.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is not set.")
        Cls = _openai_compatible_provider()
        return Cls(model, temp, max_tokens, config.openai_api_key, config.openai_base_url)

    if provider == "groq":
        if not config.groq_api_key:
            raise ProviderError("GROQ_API_KEY is not set.")
        Cls = _openai_compatible_provider()
        return Cls(model, temp, max_tokens, config.groq_api_key, "https://api.groq.com/openai/v1")

    if provider == "anthropic":
        if not config.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set.")
        try:
            from .anthropic_provider import AnthropicProvider
        except ModuleNotFoundError:
            raise ProviderError("Install anthropic SDK: pip install anthropic")
        return AnthropicProvider(model, temp, max_tokens, config.anthropic_api_key)

    if provider == "gemini":
        key = config.gemini_api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not set.")
        Cls = _openai_compatible_provider()
        return Cls(model, temp, max_tokens, key,
                   "https://generativelanguage.googleapis.com/v1beta/openai/",
                   provider_key="gemini")

    if provider == "mistral":
        key = config.mistral_api_key or os.getenv("MISTRAL_API_KEY")
        if not key:
            raise ProviderError("MISTRAL_API_KEY is not set.")
        Cls = _openai_compatible_provider()
        return Cls(model, temp, max_tokens, key, "https://api.mistral.ai/v1", provider_key="mistral")

    if provider == "together":
        key = config.together_api_key or os.getenv("TOGETHER_API_KEY")
        if not key:
            raise ProviderError("TOGETHER_API_KEY is not set.")
        Cls = _openai_compatible_provider()
        return Cls(model, temp, max_tokens, key, "https://api.together.xyz/v1", provider_key="together")

    if provider == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider(model, temp, max_tokens, config.ollama_base_url)

    raise ProviderError(f"Unknown provider: {config.provider}")
