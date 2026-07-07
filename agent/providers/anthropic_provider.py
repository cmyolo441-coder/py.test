"""Anthropic (Claude) provider with tool calling and streaming."""

from __future__ import annotations

from typing import Any, Callable

import anthropic

from .base import LLMProvider, LLMResponse, ToolCall
from ..cancellation import StopStreaming as _StopStreaming


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str, temperature: float, max_tokens: int, api_key: str) -> None:
        super().__init__(model, temperature, max_tokens)
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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        if on_delta is None:
            resp = self.client.messages.create(**kwargs)
            for block in resp.content:
                if block.type == "text":
                    content_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
            return LLMResponse(
                content="".join(content_parts),
                tool_calls=tool_calls,
                finish_reason=resp.stop_reason,
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
