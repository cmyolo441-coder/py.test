"""OpenAI-compatible provider (works for OpenAI, Groq, Gemini, etc.)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from ..cancellation import StopStreaming as _StopStreaming
from .base import LLMProvider, LLMResponse, ToolCall


def _salvage_arguments(raw: str, tool_name: str) -> dict[str, Any]:
    """Best-effort extraction from truncated/malformed JSON."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    def _extract(key: str) -> str | None:
        pattern = rf'"{key}"\s*:\s*"'
        m = re.search(pattern, raw)
        if not m:
            return None
        start = m.end()
        chars = []
        i = start
        _esc = {'"': '"', '\\': '\\', '/': '/', 'n': '\n', 't': '\t', 'r': '\r'}
        while i < len(raw):
            c = raw[i]
            if c == '\\' and i + 1 < len(raw):
                chars.append(_esc.get(raw[i + 1], raw[i + 1]))
                i += 2
            elif c == '"':
                rest = raw[i + 1:].lstrip()
                if not rest or rest[0] in (',', '}', ']'):
                    break
                chars.append(c)
                i += 1
            else:
                chars.append(c)
                i += 1
        return ''.join(chars)

    if tool_name in ("write_file", "append_file"):
        path_val = _extract("path")
        content_val = _extract("content")
        if path_val:
            return {"path": path_val, "content": content_val or ""}

    if tool_name == "run_shell":
        cmd_val = _extract("command")
        if cmd_val is not None:
            return {"command": cmd_val}

    result: dict[str, Any] = {}
    for key in re.findall(r'"(\w+)"\s*:\s*"', raw):
        val = _extract(key)
        if val is not None:
            result[key] = val
    if result:
        return result

    return {"__malformed_arguments__": raw}


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str | None = None,
        provider_key: str | None = None,
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
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if on_delta is None:
            return self._chat_blocking(kwargs)
        return self._chat_stream(kwargs, on_delta)

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
                    cancelled = True
                    break
            for tc in delta.tool_calls or []:
                frag = tool_fragments.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    frag["id"] = tc.id
                if tc.function and tc.function.name:
                    frag["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    frag["arguments"] += tc.function.arguments

        if cancelled:
            try:
                stream.close()
            except Exception:
                pass
            return LLMResponse(content="".join(content_parts), tool_calls=[], finish_reason="cancelled")

        tool_calls = []
        for frag in tool_fragments.values():
            raw = frag["arguments"] or "{}"
            args = _salvage_arguments(raw, frag["name"])
            tool_calls.append(ToolCall(id=frag["id"], name=frag["name"], arguments=args))

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
