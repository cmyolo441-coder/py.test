"""Tests for provider registry metadata and factory error handling."""

from __future__ import annotations

import pytest

from agent.config import Config
from agent.providers.factory import ProviderError, get_provider
from agent.providers.registry import PROVIDERS, get_spec, list_providers


def test_provider_specs_present():
    assert "zen" in PROVIDERS
    assert "openai" in PROVIDERS
    assert get_spec("ZEN").name == "zen"
    assert len(list_providers()) >= 6


def test_zen_is_openai_compatible():
    assert get_spec("zen").openai_compatible is True


def test_factory_missing_key_raises():
    cfg = Config()
    cfg.provider = "openai"
    cfg.openai_api_key = None
    with pytest.raises(ProviderError):
        get_provider(cfg)


def test_factory_unknown_provider():
    cfg = Config()
    cfg.provider = "nonexistent"
    with pytest.raises(ProviderError):
        get_provider(cfg)
