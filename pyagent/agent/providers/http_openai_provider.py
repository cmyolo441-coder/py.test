"""Dependency-light OpenAI-compatible provider using httpx.

The official `openai` package is preferred when installed, but the agent should
still run from source with just the dependencies commonly present in the repo's
runtime.  This provider implements the Chat Completions subset needed by the
agent (messages, tools, streaming, and tool-call parsing) directly over HTTP.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from ..cancellation import StopStreaming as _StopStreaming
from ..capabilities import clamp_output_tokens, output_kwargs, reasoning_kwargs, temperature_kwargs
from .base import LLMProvider, LLMResponse, ToolCall


class HttpOpenAIProvider(LLMProvider):
    """OpenAI-compatible provider that does not require the `openai` package."""

    provider_key = "openai"

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        base_url: str | None = None,
        thinking_level: str | None = None,
        provider_key: str | None = None,
        verbosity: str | None = None,
        reasoning_pro: bool = False,
    ) -> None:
        if provider_key is not None:
            self.provider_key = provider_key
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.verbosity = verbosity
        self.reasoning_pro = reasoning_pro
        super().__init__(model, temperature, max_tokens, thinking_level)

    def _build_payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        cap = self.cap
        reason_top, extra = reasoning_kwargs(
            cap, self.thinking_level, clamp_output_tokens(cap, self.max_tokens)
        )
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        payload.update(output_kwargs(cap, self.max_tokens))
        payload.update(temperature_kwargs(cap, self.temperature, thinking_enabled=bool(reason_top or extra)))
        payload.update(reason_top)
        if cap.supports_verbosity and self.verbosity:
            payload["verbosity"] = self.verbosity
        if cap.reasoning_mode_pro and self.reasoning_pro:
            payload["reasoning"] = {"mode": "pro"}
        if extra:
            payload["extra_body"] = extra
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        payload = self._build_payload(messages, tools)
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
            reasoning=msg.get("reasoning_content") or "",
        )

    def _chat_stream(self, payload: dict[str, Any], on_delta: Callable[[str], None]) -> LLMResponse:
        payload = dict(payload)
        payload["stream"] = True
        url = f"{self.base_url}/chat/completions"
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
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
                        finish_reason = choice.get("finish_reason")
                    delta = choice.get("delta") or {}
                    rc = delta.get("reasoning_content")
                    if rc:
                        reasoning_parts.append(rc)
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        try:
                            on_delta(piece)
                        except _StopStreaming:
                            cancelled = True
                            finish_reason = "cancelled"
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
            tool_calls=[self._fragment_to_tool_call(frag) for frag in tool_fragments.values()],
            finish_reason=finish_reason,
            reasoning="".join(reasoning_parts),
        )

    @staticmethod
    def _parse_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for raw in raw_calls:
            fn = raw.get("function") or {}
            calls.append(
                ToolCall(
                    id=raw.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=HttpOpenAIProvider._loads_args(fn.get("arguments") or "{}"),
                )
            )
        return calls

    @staticmethod
    def _fragment_to_tool_call(frag: dict[str, Any]) -> ToolCall:
        return ToolCall(
            id=frag.get("id") or "",
            name=frag.get("name") or "",
            arguments=HttpOpenAIProvider._loads_args(frag.get("arguments") or "{}"),
        )

    @staticmethod
    def _loads_args(raw: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            # Best-effort fallback for truncated tool arguments.  The agent core
            # will surface the malformed payload as a normal tool error instead
            # of crashing the whole turn.
            return {"__malformed_arguments__": raw}
