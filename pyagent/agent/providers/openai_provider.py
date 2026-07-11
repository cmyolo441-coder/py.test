"""OpenAI-compatible provider (works for OpenAI and Groq via base_url)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from ..cancellation import StopStreaming as _StopStreaming
from .base import LLMProvider, LLMResponse, ToolCall


def _extract_json_value(raw: str, key: str) -> str | None:
    """Extract the string value for ``key`` from a possibly-malformed JSON object.

    Handles cases where the model's tool call JSON has unescaped quotes inside
    the value (a common issue with large ``write_file`` content). Uses a
    character-by-character scanner instead of regex or ``json.loads`` so it
    tolerates common JSON encoding errors.
    """
    pattern = rf'"{key}"\s*:\s*"'
    m = re.search(pattern, raw)
    if not m:
        return None
    start = m.end()
    chars = []
    i = start
    # JSON string escape map. The previous implementation appended the raw
    # escape character (so "\n" became the literal letter "n"), which silently
    # destroyed every newline/tab in salvaged file content. Decode properly.
    _simple = {
        '"': '"', '\\': '\\', '/': '/',
        'n': '\n', 't': '\t', 'r': '\r',
        'b': '\b', 'f': '\f',
    }
    while i < len(raw):
        c = raw[i]
        if c == '\\':
            # Escape sequence — decode it to the character it represents.
            if i + 1 >= len(raw):
                break
            nxt = raw[i + 1]
            if nxt == 'u' and i + 5 < len(raw):
                try:
                    chars.append(chr(int(raw[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            chars.append(_simple.get(nxt, nxt))
            i += 2
        elif c == '"':
            # End of value — but only if followed by , or } or whitespace+}
            # (handles unescaped quotes inside content like Python's \"\"\")
            rest = raw[i + 1:].lstrip()
            if not rest or rest[0] in (',', '}', ']'):
                break
            # Embedded unescaped quote — include it literally
            chars.append(c)
            i += 1
        else:
            chars.append(c)
            i += 1
    return ''.join(chars)


def _salvage_arguments(raw: str, tool_name: str) -> dict[str, Any]:
    """Best-effort extraction of arguments from a truncated/malformed JSON string.

    When a tool call argument JSON is truncated mid-stream (e.g. a very large
    ``write_file`` content that exceeds the model's output limit), standard
    ``json.loads`` fails with ``JSONDecodeError``. This function tries to
    recover partial arguments via character-level heuristics so the tool can
    still do useful work instead of simply failing.

    Returns a dict with whatever keys were recoverable, or the original
    ``__malformed_arguments__`` marker if nothing useful could be extracted.
    """
    # Attempt full parse first.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # For write_file/append_file: try to extract path and partial content.
    if tool_name in ("write_file", "append_file"):
        path_val = _extract_json_value(raw, "path")
        content_val = _extract_json_value(raw, "content")
        if path_val and content_val is not None:
            return {"path": path_val, "content": content_val}
        if path_val:
            return {"path": path_val, "content": content_val or ""}

    # For run_shell: the command string is the one required argument and is the
    # usual victim of a truncated heredoc. Recover it with the same tolerant
    # scanner so a large command still runs instead of failing on a missing arg.
    if tool_name == "run_shell":
        cmd_val = _extract_json_value(raw, "command")
        if cmd_val is not None:
            return {"command": cmd_val}

    # Generic fallback: extract every string key via the tolerant scanner (which
    # decodes escapes correctly), falling back to a strict regex for scalars.
    result: dict[str, Any] = {}
    for key in re.findall(r'"(\w+)"\s*:\s*"', raw):
        val = _extract_json_value(raw, key)
        if val is not None:
            result[key] = val
    for m in re.finditer(r'"(\w+)"\s*:\s*(null|true|false|-?\d+(?:\.\d+)?)', raw):
        key, val = m.group(1), m.group(2)
        if key not in result:
            try:
                result[key] = json.loads(val)
            except json.JSONDecodeError:
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
            args = _salvage_arguments(raw, frag["name"])
            tool_calls.append(ToolCall(id=frag["id"], name=frag["name"], arguments=args))

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
