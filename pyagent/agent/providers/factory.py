"""Factory that builds the right provider from a Config object."""

from __future__ import annotations

import os

from ..config import Config
from .base import LLMProvider


def _env(name: str) -> str | None:
    return os.getenv(name)


class ProviderError(RuntimeError):
    pass


def _openai_compatible_provider():
    """Return the best OpenAI-compatible provider implementation available."""
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
            raise ProviderError("OPENAI_API_KEY is not set. Set it or run `/config`.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(model, temp, max_tokens, config.openai_api_key, config.openai_base_url)

    if provider == "groq":
        if not config.groq_api_key:
            raise ProviderError("GROQ_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, config.groq_api_key, "https://api.groq.com/openai/v1"
        )

    if provider == "zen":
        if not config.zen_api_key:
            raise ProviderError("ZEN_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(model, temp, max_tokens, config.zen_api_key, config.zen_base_url)

    if provider == "zyloo":
        key = getattr(config, "zyloo_api_key", None) or _env("ZYLOO_API_KEY")
        if not key:
            raise ProviderError("ZYLOO_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, key, getattr(config, "zyloo_base_url", "https://api.zyloo.io/v1")
        )

    if provider == "lovable":
        key = getattr(config, "lovable_api_key", None) or _env("LOVABLE_API_KEY")
        if not key:
            raise ProviderError("LOVABLE_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, key,
            getattr(config, "lovable_base_url", "https://ai.gateway.lovable.dev/v1"),
            provider_key="lovable",
        )

    if provider == "anthropic":
        if not config.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set.")
        try:
            from .anthropic_provider import AnthropicProvider
        except ModuleNotFoundError as exc:
            if exc.name == "anthropic":
                raise ProviderError(
                    "The Anthropic SDK is not bundled in the lightweight binary. "
                    "Install optional providers with: pip install 'terminal-agent[providers]'"
                ) from exc
            raise

        return AnthropicProvider(model, temp, max_tokens, config.anthropic_api_key)

    if provider == "gemini":
        key = getattr(config, "gemini_api_key", None) or _env("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, key,
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            provider_key="gemini",
        )

    if provider == "mistral":
        key = getattr(config, "mistral_api_key", None) or _env("MISTRAL_API_KEY")
        if not key:
            raise ProviderError("MISTRAL_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, key,
            "https://api.mistral.ai/v1",
            provider_key="mistral",
        )

    if provider == "together":
        key = getattr(config, "together_api_key", None) or _env("TOGETHER_API_KEY")
        if not key:
            raise ProviderError("TOGETHER_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, key,
            "https://api.together.xyz/v1",
            provider_key="together",
        )

    if provider == "nvapi":
        key = getattr(config, "nvapi_api_key", None) or _env("NVAPI_API_KEY")
        if not key:
            raise ProviderError("NVAPI_API_KEY is not set.")
        OpenAIProvider = _openai_compatible_provider()

        return OpenAIProvider(
            model, temp, max_tokens, key,
            getattr(config, "nvapi_base_url", "https://integrate.api.nvidia.com/v1"),
        )

    if provider == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(model, temp, max_tokens, config.ollama_base_url)

    raise ProviderError(f"Unknown provider: {config.provider}")
