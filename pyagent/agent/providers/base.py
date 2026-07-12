"""Provider-agnostic interfaces for chat completion with tool calling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from dataclasses import dataclass, field

from ..capabilities import ModelCapability, resolve_capability


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    # Reasoning / chain-of-thought text, when the model exposes it separately
    # (e.g. DeepSeek ``reasoning_content``). Empty for models without it.
    reasoning: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(ABC):
    """Abstract chat provider.

    Implementations must support tool calling and streaming. ``chat`` takes a
    list of provider-neutral messages (role/content dicts) plus tool schemas.
    """

    #: Provider key used to resolve capability fallbacks (e.g. "anthropic",
    #: "openai", "gemini"). Subclasses may override.
    provider_key: str | None = None

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        thinking_level: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_level = thinking_level
        # Resolve the model's real capabilities once. Governs how the requested
        # max_tokens/temperature/thinking level become actual request kwargs so
        # every model runs at its true full capacity without sending a param the
        # API rejects.
        self.cap: ModelCapability = resolve_capability(model, self.provider_key)

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Send a chat request and return the assistant response.

        If ``on_delta`` is provided the provider should stream text chunks to it
        as they arrive (for live rendering).
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__
