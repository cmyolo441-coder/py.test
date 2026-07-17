"""Configuration management for the terminal AI agent."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .systemprompts import DEFAULT_SYSTEM_PROMPT

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
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
    provider: str = field(default_factory=lambda: _env("AGENT_PROVIDER", default="openai") or "openai")
    model: str | None = field(default_factory=lambda: _env("AGENT_MODEL"))
    temperature: float = 0.7
    max_tokens: int = 128000
    stream: bool = True

    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str | None = field(default_factory=lambda: _env("OPENAI_BASE_URL"))
    anthropic_api_key: str | None = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    groq_api_key: str | None = field(default_factory=lambda: _env("GROQ_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    mistral_api_key: str | None = field(default_factory=lambda: _env("MISTRAL_API_KEY"))
    together_api_key: str | None = field(default_factory=lambda: _env("TOGETHER_API_KEY"))
    ollama_base_url: str = field(
        default_factory=lambda: _env("OLLAMA_BASE_URL", default="http://localhost:11434") or "http://localhost:11434"
    )

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    enable_tools: bool = True
    auto_approve_tools: bool = False
    max_tool_iterations: int = 12

    _default_models: dict[str, str] = field(
        default_factory=lambda: {
            "openai": "gpt-4o",
            "anthropic": "claude-sonnet-4-20250514",
            "groq": "llama-3.3-70b-versatile",
            "ollama": "llama3.1",
            "gemini": "gemini-2.5-flash",
            "mistral": "mistral-large-latest",
            "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        }
    )

    _known_models: dict[str, list[str]] = field(
        default_factory=lambda: {
            "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o4-mini"],
            "anthropic": ["claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022"],
            "groq": ["llama-3.3-70b-versatile"],
            "gemini": ["gemini-2.5-flash", "gemini-2.5-pro"],
            "mistral": ["mistral-large-latest"],
            "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
        }
    )

    def known_models(self) -> list[str]:
        return self._known_models.get(self.provider, [])

    def all_known_models(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for prov, models in self._known_models.items():
            for m in models:
                pairs.append((prov, m))
        return pairs

    def provider_for_model(self, model: str) -> str | None:
        for prov, models in self._known_models.items():
            if model in models:
                return prov
        return None

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        return self._default_models.get(self.provider, "gpt-4o")

    @classmethod
    def load(cls) -> Config:
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
            "gemini": bool(self.gemini_api_key),
            "mistral": bool(self.mistral_api_key),
            "together": bool(self.together_api_key),
            "ollama": True,
        }.get(self.provider, False)
