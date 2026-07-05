"""Configuration management for the terminal AI agent.

Loads settings from environment variables, a local .env file and an optional
JSON config file at ~/.terminal_agent/config.json. Environment variables always
take precedence so the agent works well in CI and scripted environments.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


CONFIG_DIR = Path.home() / ".terminal_agent"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
PROMPT_HISTORY_FILE = CONFIG_DIR / "prompt_history"


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


@dataclass
class Config:
    """Runtime configuration for the agent."""

    provider: str = field(default_factory=lambda: _env("AGENT_PROVIDER", default="zen") or "zen")
    model: str | None = field(default_factory=lambda: _env("AGENT_MODEL"))
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = True

    # Provider credentials / endpoints.
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str | None = field(default_factory=lambda: _env("OPENAI_BASE_URL"))
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    groq_api_key: str | None = field(default_factory=lambda: _env("GROQ_API_KEY"))

    # Zen (opencode.ai) â€” OpenAI-compatible endpoint.
    zen_api_key: str | None = field(
        default_factory=lambda: _env(
            "ZEN_API_KEY",
            default="sk-uM0oXXKJGFhyn3bk9kwvjF0RfZ2MSOfKMW5kyDrYXEGZnO1ZJT8BptOX6i6ry7Ue",
        )
    )
    zen_base_url: str = field(
        default_factory=lambda: _env("ZEN_BASE_URL", default="https://opencode.ai/zen/v1")
        or "https://opencode.ai/zen/v1"
    )

    # Zyloo â€” OpenAI-compatible endpoint.
    zyloo_api_key: str | None = field(
        default_factory=lambda: _env(
            "ZYLOO_API_KEY",
            default="sk-zy-7b9efa90f2bd4116caaca84c1fe8d58e78db6bdf9514fe30",
        )
    )
    zyloo_base_url: str = field(
        default_factory=lambda: _env("ZYLOO_BASE_URL", default="https://api.zyloo.io/v1")
        or "https://api.zyloo.io/v1"
    )

    gemini_api_key: str | None = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    mistral_api_key: str | None = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    together_api_key: str | None = field(default_factory=lambda: _env("TOGETHER_API_KEY"))

    ollama_base_url: str = field(
        default_factory=lambda: _env("OLLAMA_BASE_URL", default="http://localhost:11434") or "http://localhost:11434"
    )

    # Agent behaviour.
    system_prompt: str = (
        "You are an elite terminal AI agent. You are precise, proactive and "
        "helpful. You can run shell commands and manage files through tools. "
        "Think step by step, prefer safe actions, and always explain what you "
        "are about to do before doing anything destructive."
    )
    enable_tools: bool = True
    auto_approve_tools: bool = False
    max_tool_iterations: int = 12
    # Autonomous effort level (see agent/effort.py): normal | ultramax |
    # ultracombo | ultrahype | enterprise | godmode.
    effort: str = "normal"

    # Default model per provider when none is explicitly set.
    _default_models: dict[str, str] = field(
        default_factory=lambda: {
            "openai": "gpt-4o",
            "anthropic": "claude-3-5-sonnet-20241022",
            "groq": "llama-3.3-70b-versatile",
            "ollama": "llama3.1",
            "zen": "mimo-v2.5-free",
            "zyloo": "zyloo/glm-5.1",
            "gemini": "gemini-1.5-flash",
            "mistral": "mistral-large-latest",
            "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        }
    )

    # Known models per provider (used for /models listing and switching).
    _known_models: dict[str, list[str]] = field(
        default_factory=lambda: {
            "zen": ["mimo-v2.5-free", "big-pickle", "deepseek-v4-flash-free"],
            "zyloo": ["zyloo/glm-5.1"],
        }
    )

    def known_models(self) -> list[str]:
        return self._known_models.get(self.provider, [])

    def all_known_models(self) -> list[tuple[str, str]]:
        """(provider, model) for every preset model across all providers.

        Powers the /model picker so models from other providers (e.g. zyloo)
        are always visible, not just the currently-selected provider's list.
        """
        pairs: list[tuple[str, str]] = []
        for prov, models in self._known_models.items():
            for m in models:
                pairs.append((prov, m))
        return pairs

    def provider_for_model(self, model: str) -> str | None:
        """Return the provider that owns ``model`` in the preset lists."""
        for prov, models in self._known_models.items():
            if model in models:
                return prov
        return None

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return self._default_models.get(self.provider, "gpt-4o")

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_FILE.exists():
            try:
                data: dict[str, Any] = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for key, value in data.items():
                    if key.startswith("_"):
                        continue
                    if hasattr(cfg, key) and value is not None:
                        setattr(cfg, key, value)
            except (json.JSONDecodeError, OSError):
                pass
        return cfg

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def has_credentials(self) -> bool:
        return {
            "openai": bool(self.openai_api_key),
            "anthropic": bool(self.anthropic_api_key),
            "groq": bool(self.groq_api_key),
            "zen": bool(self.zen_api_key),
            "zyloo": bool(self.zyloo_api_key),
            "ollama": True,  # local, no key required
        }.get(self.provider, False)
