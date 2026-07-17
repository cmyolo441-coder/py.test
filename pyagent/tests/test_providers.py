"""Tests for provider registry and factory."""

from __future__ import annotations

import pytest

from agent.config import Config
from agent.providers.factory import ProviderError, get_provider
from agent.providers.registry import PROVIDERS, get_spec, list_providers


def test_provider_specs_present():
    assert "openai" in PROVIDERS
    assert "anthropic" in PROVIDERS
    assert "ollama" in PROVIDERS
    assert "gemini" in PROVIDERS


def test_list_providers():
    specs = list_providers()
    assert len(specs) >= 7


def test_get_spec():
    spec = get_spec("openai")
    assert spec is not None
    assert spec.name == "openai"


def test_unknown_provider_raises():
    config = Config(provider="nonexistent_provider_xyz")
    with pytest.raises(ProviderError):
        get_provider(config)


def test_missing_key_raises():
    config = Config(provider="openai", openai_api_key=None)
    with pytest.raises(ProviderError):
        get_provider(config)
