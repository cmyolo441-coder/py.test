"""Provider-agnostic interfaces for chat completion with tool calling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


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

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(ABC):
    """Abstract chat provider.

    Implementations must support tool calling and streaming. ``chat`` takes a
    list of provider-neutral messages (role/content dicts) plus tool schemas.
    """

    def __init__(self, model: str, temperature: float, max_tokens: int) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

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
