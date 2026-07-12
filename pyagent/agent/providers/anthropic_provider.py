"""Anthropic (Claude) provider with tool calling and streaming."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anthropic

from ..cancellation import StopStreaming as _StopStreaming
from ..capabilities import clamp_output_tokens, output_kwargs, reasoning_kwargs, temperature_kwargs
from .base import LLMProvider, LLMResponse, ToolCall


class AnthropicProvider(LLMProvider):
    provider_key = "anthropic"

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        thinking_level: str | None = None,
    ) -> None:
        super().__init__(model, temperature, max_tokens, thinking_level)
        self.client = anthropic.Anthropic(api_key=api_key)

    def _split_system(self, messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system = ""
        converted: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                system += (m.get("content") or "") + "\n"
                continue
            converted.append(m)
        return system.strip(), converted

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        system, msgs = self._split_system(messages)
        cap = self.cap

        # Build the real request params from the model's capabilities so the
        # model runs at full capacity: its true output ceiling, real extended
        # thinking (adaptive effort or budget_tokens), and a temperature the API
        # actually accepts (omitted where the model rejects it, e.g. Opus 4.7+).
        reason_top, _extra = reasoning_kwargs(
            cap, self.thinking_level, clamp_output_tokens(cap, self.max_tokens)
        )
        thinking_enabled = bool(reason_top)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
        }
        kwargs.update(output_kwargs(cap, self.max_tokens))
        kwargs.update(temperature_kwargs(cap, self.temperature, thinking_enabled))
        kwargs.update(reason_top)
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        if on_delta is None:
            # No live-render callback (e.g. sub-agents, consensus, reflection).
            # We still stream under the hood: the Anthropic SDK rejects a plain
            # ``messages.create`` when ``max_tokens`` is large enough that the
            # request could exceed its 10-minute non-streaming limit, raising
            # "Streaming is required for operations that may take longer than 10
            # minutes." Streaming and discarding the deltas avoids that while
            # returning the same complete response.
            with self.client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    content_parts.append(text)
                final = stream.get_final_message()
            for block in final.content:
                if block.type == "tool_use":
                    tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
            return LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                finish_reason=final.stop_reason,
            )

        # Streaming path.
        with self.client.messages.stream(**kwargs) as stream:
            try:
                for text in stream.text_stream:
                    content_parts.append(text)
                    on_delta(text)
            except _StopStreaming:
                # User pressed ESC: stop consuming the stream and return the
                # partial text produced so far as a clean cancellation.
                return LLMResponse(
                    content="".join(content_parts),
                    tool_calls=[],
                    finish_reason="cancelled",
                )
            final = stream.get_final_message()
        for block in final.content:
            if block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=final.stop_reason,
        )
