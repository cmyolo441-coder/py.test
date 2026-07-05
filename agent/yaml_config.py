"""YAML/TOML-style configuration loading with env-var overrides.

Merges layered settings: defaults -> config file -> environment. Keeps a single
source of truth for runtime options.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import yaml

    _HAS_YAML = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_YAML = False


DEFAULTS: dict[str, Any] = {
    "provider": "openai",
    "model": "",
    "temperature": 0.7,
    "enable_tools": True,
    "theme": "midnight",
    "max_history_messages": 40,
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists() or not _HAS_YAML:
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    mapping = {
        "AI_PROVIDER": "provider",
        "AI_MODEL": "model",
        "AI_THEME": "theme",
    }
    for env_key, cfg_key in mapping.items():
        val = os.getenv(env_key)
        if val:
            overrides[cfg_key] = val
    return overrides


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """Return merged config: defaults <- file <- environment."""
    merged = _deep_merge(DEFAULTS, load_yaml(path))
    merged = _deep_merge(merged, _env_overrides())
    return merged
