"""Dependency-light OpenAI-compatible provider using httpx."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from ..cancellation import StopStreaming as _StopStreaming
from .base import LLMProvider, LLMResponse, ToolCall


class HttpOpenAIProvider(LLMProvider):
    """OpenAI-compatible provider that does not require the `openai` package."""

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str | None = None,
        provider_key: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        super().__init__(model, temperature, max_tokens)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages, tools) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def chat(self, messages, tools=None, on_delta=None) -> LLMResponse:
        payload = self._payload(messages, tools)
        if on_delta is None:
            return self._chat_blocking(payload)
        return self._chat_stream(payload, on_delta)

    def _chat_blocking(self, payload: dict[str, Any]) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
            resp = client.post(url, headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"Provider HTTP {resp.status_code}: {resp.text[:1000]}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=self._parse_tool_calls(msg.get("tool_calls") or []),
            finish_reason=choice.get("finish_reason"),
        )

    def _chat_stream(self, payload: dict[str, Any], on_delta: Callable[[str], None]) -> LLMResponse:
        payload["stream"] = True
        url = f"{self.base_url}/chat/completions"
        content_parts: list[str] = []
        tool_fragments: dict[int, dict[str, Any]] = {}
        finish_reason = None
        cancelled = False

        with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
            with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"Provider HTTP {resp.status_code}: {body[:1000]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        try:
                            on_delta(piece)
                        except _StopStreaming:
                            cancelled = True
                            break
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", len(tool_fragments)))
                        frag = tool_fragments.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            frag["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            frag["name"] = fn["name"]
                        if fn.get("arguments"):
                            frag["arguments"] += fn["arguments"]
                if cancelled:
                    resp.close()

        if cancelled:
            return LLMResponse(content="".join(content_parts), tool_calls=[], finish_reason="cancelled")
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=[self._frag_to_tc(f) for f in tool_fragments.values()],
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for raw in raw_calls:
            fn = raw.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"__malformed_arguments__": fn.get("arguments", "")}
            calls.append(ToolCall(id=raw.get("id") or "", name=fn.get("name") or "", arguments=args))
        return calls

    @staticmethod
    def _frag_to_tc(frag: dict[str, Any]) -> ToolCall:
        raw = frag.get("arguments") or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"__malformed_arguments__": raw}
        return ToolCall(id=frag.get("id") or "", name=frag.get("name") or "", arguments=args)
