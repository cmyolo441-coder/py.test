"""OpenAI-compatible provider (works for OpenAI and Groq via base_url)."""

from __future__ import annotations

import json
from typing import Any, Callable

from openai import OpenAI

from ..cancellation import StopStreaming as _StopStreaming
from .base import LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        super().__init__(model, temperature, max_tokens)
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if on_delta is None:
            return self._chat_blocking(kwargs)
        return self._chat_stream(kwargs, on_delta)

    # ------------------------------------------------------------------
    def _chat_blocking(self, kwargs: dict[str, Any]) -> LLMResponse:
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message
        tool_calls = []
        for tc in msg.tool_calls or []:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
            )
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
        )

    def _chat_stream(self, kwargs: dict[str, Any], on_delta: Callable[[str], None]) -> LLMResponse:
        kwargs["stream"] = True
        content_parts: list[str] = []
        # Accumulate tool call fragments by index.
        tool_fragments: dict[int, dict[str, Any]] = {}
        finish_reason = None

        stream = self.client.chat.completions.create(**kwargs)
        cancelled = False
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason
            if delta.content:
                content_parts.append(delta.content)
                try:
                    on_delta(delta.content)
                except _StopStreaming:
                    # User pressed ESC: stop consuming the stream cleanly and
                    # return whatever text was produced so far.
                    cancelled = True
                    finish_reason = "cancelled"
                    break
            for tc in delta.tool_calls or []:
                frag = tool_fragments.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    frag["id"] = tc.id
                if tc.function and tc.function.name:
                    frag["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    frag["arguments"] += tc.function.arguments

        if cancelled:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            return LLMResponse(
                content="".join(content_parts),
                tool_calls=[],
                finish_reason="cancelled",
            )

        tool_calls = []
        for frag in tool_fragments.values():
            raw = frag["arguments"] or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                # Streamed arguments were truncated/malformed. Surface a marker
                # instead of a silent empty dict so the validation layer reports
                # a clear error and the model re-issues a well-formed call.
                args = {"__malformed_arguments__": raw}
            if not isinstance(args, dict):
                args = {"__malformed_arguments__": raw}
            tool_calls.append(ToolCall(id=frag["id"], name=frag["name"], arguments=args))

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
