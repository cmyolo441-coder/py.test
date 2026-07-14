"""Registry describing every supported provider and its defaults.

Keeping this metadata in one place lets the UI list providers, validate
switching and pick sensible default models without hard-coding logic in the
factory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    default_model: str
    needs_key: bool
    env_key: str | None
    openai_compatible: bool
    description: str


PROVIDERS: dict[str, ProviderSpec] = {
    "zen": ProviderSpec("zen", "mimo-v2.5-free", True, "ZEN_API_KEY", True, "opencode.ai Zen (free models)"),
    "zyloo": ProviderSpec("zyloo", "zyloo/glm-5.1", True, "ZYLOO_API_KEY", True, "Zyloo (GLM models)"),
    "lovable": ProviderSpec("lovable", "openai/gpt-5.5", True, "LOVABLE_API_KEY", True, "Lovable AI Gateway"),
    "openai": ProviderSpec("openai", "gpt-4o", True, "OPENAI_API_KEY", True, "OpenAI GPT models"),
    "anthropic": ProviderSpec("anthropic", "claude-3-5-sonnet-20241022", True, "ANTHROPIC_API_KEY", False, "Anthropic Claude"),
    "groq": ProviderSpec("groq", "llama-3.3-70b-versatile", True, "GROQ_API_KEY", True, "Groq (fast Llama)"),
    "gemini": ProviderSpec("gemini", "gemini-1.5-flash", True, "GEMINI_API_KEY", True, "Google Gemini"),
    "mistral": ProviderSpec("mistral", "mistral-large-latest", True, "MISTRAL_API_KEY", True, "Mistral AI"),
    "together": ProviderSpec("together", "meta-llama/Llama-3.3-70B-Instruct-Turbo", True, "TOGETHER_API_KEY", True, "Together AI"),
    "ollama": ProviderSpec("ollama", "llama3.1", False, None, True, "Local Ollama (offline)"),
    "nvapi": ProviderSpec("nvapi", "z-ai/glm-5.2", True, "NVAPI_API_KEY", True, "NVIDIA API (OpenAI-compatible)"),
    "sambanova": ProviderSpec("sambanova", "MiniMax-M2.7", True, "SAMBANOVA_API_KEY", True, "SambaNova Cloud (OpenAI-compatible)"),
    "agnes": ProviderSpec("agnes", "agnes-2.5-flash", True, "AGNES_API_KEY", True, "Agnes AI API Hub (OpenAI-compatible)"),
}


def list_providers() -> list[ProviderSpec]:
    return list(PROVIDERS.values())


def get_spec(name: str) -> ProviderSpec | None:
    return PROVIDERS.get(name.lower())
