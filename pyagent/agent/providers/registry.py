"""Registry describing every supported provider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    default_model: str
    needs_key: bool
    env_key: str | None
    description: str


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec("openai", "gpt-4o", True, "OPENAI_API_KEY", "OpenAI GPT models"),
    "anthropic": ProviderSpec("anthropic", "claude-sonnet-4-20250514", True, "ANTHROPIC_API_KEY", "Anthropic Claude"),
    "groq": ProviderSpec("groq", "llama-3.3-70b-versatile", True, "GROQ_API_KEY", "Groq (fast Llama)"),
    "gemini": ProviderSpec("gemini", "gemini-2.5-flash", True, "GEMINI_API_KEY", "Google Gemini"),
    "mistral": ProviderSpec("mistral", "mistral-large-latest", True, "MISTRAL_API_KEY", "Mistral AI"),
    "together": ProviderSpec("together", "meta-llama/Llama-3.3-70B-Instruct-Turbo", True, "TOGETHER_API_KEY", "Together AI"),
    "ollama": ProviderSpec("ollama", "llama3.1", False, None, "Local Ollama (offline)"),
}


def list_providers() -> list[ProviderSpec]:
    return list(PROVIDERS.values())


def get_spec(name: str) -> ProviderSpec | None:
    return PROVIDERS.get(name.lower())
