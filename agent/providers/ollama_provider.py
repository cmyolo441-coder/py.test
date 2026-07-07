"""Ollama provider for fully local, offline models (no API key required)."""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx

from .base import LLMProvider, LLMResponse, ToolCall
from ..cancellation import StopStreaming as _StopStreaming


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, temperature: float, max_tokens: int, base_url: str) -> None:
        super().__init__(model, temperature, max_tokens)
        self.base_url = base_url.rstrip("/")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
            "stream": on_delta is not None,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/api/chat"

        if on_delta is None:
            resp = httpx.post(url, json=payload, timeout=700000)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_message(data.get("message", {}))

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        with httpx.stream("POST", url, json=payload, timeout=700000) as resp:
            resp.raise_for_status()
            try:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    msg = data.get("message", {})
                    if msg.get("content"):
                        content_parts.append(msg["content"])
                        on_delta(msg["content"])
                    for tc in msg.get("tool_calls", []) or []:
                        fn = tc.get("function", {})
                        tool_calls.append(
                            ToolCall(
                                id=fn.get("name", "tool"),
                                name=fn.get("name", ""),
                                arguments=fn.get("arguments", {}) or {},
                            )
                        )
            except _StopStreaming:
                # User pressed ESC: stop and return partial text cleanly.
                return LLMResponse(
                    content="".join(content_parts),
                    tool_calls=[],
                    finish_reason="cancelled",
                )
        return LLMResponse(content="".join(content_parts), tool_calls=tool_calls)

    def _parse_message(self, msg: dict[str, Any]) -> LLMResponse:
        tool_calls = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(id=fn.get("name", "tool"), name=fn.get("name", ""), arguments=args))
        return LLMResponse(content=msg.get("content", ""), tool_calls=tool_calls)
