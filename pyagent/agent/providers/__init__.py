"""LLM provider abstraction layer.

Each provider implements the ``LLMProvider`` interface so the agent core is
provider-agnostic. Use ``get_provider`` to instantiate the correct one from
config.
"""

from __future__ import annotations

from .base import LLMProvider, LLMResponse, ToolCall
from .factory import get_provider

__all__ = ["LLMProvider", "LLMResponse", "ToolCall", "get_provider"]
