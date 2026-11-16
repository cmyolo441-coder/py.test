"""OpenCode Console bridge — free models (hy4-flash-free) inside FullAgent.

Free-tier OpenCode models can only be reached through the OpenCode harness,
so this module talks to the local OpenCode desktop service instead of a
plain OpenAI-compatible endpoint. The password is read from
~/.config/opencode/service.json (env OPENCODE_API_KEY wins).
"""

from __future__ import annotations

import base64
import time
from typing import Any, Callable

import requests

from . import config
from .client import APIError, StreamResult

DEFAULT_BASE_URL = "http://127.0.0.1:28894"


def is_opencode(provider) -> bool:
    return getattr(provider, "key", "") == "opencode"


def _headers(provider) -> dict[str, str]:
    token = base64.b64encode(
        f"opencode:{provider.api_key or ''}".encode("utf-8")
    ).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                out.append(str(part.get("text", "")))
            elif isinstance(part, str):
                out.append(part)
        return "".join(out)
    return ""


def _flatten(messages: list[dict]) -> str:
    parts = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        text = _content_text(msg.get("content", ""))
        parts.append(f"{role.capitalize()}: {text}")
    return "\n\n".join(parts)


def _raise(r: requests.Response, what: str) -> None:
    if r.status_code < 200 or r.status_code >= 300:
        raise APIError(
            f"OpenCode {what} failed ({r.status_code}): {r.text[:500]}",
            status=r.status_code,
        )


def _run(provider, model, effort, messages: list[dict], tools,
         timeout: float, on_token, on_reasoning, on_tool_start,
         on_tool_args, should_cancel, on_status) -> StreamResult:
    if not provider.api_key:
        raise APIError(
            "OpenCode bridge needs a service password. Set OPENCODE_API_KEY "
            "or make sure the OpenCode desktop app is running.", status=401)
    base = (provider.base_url or DEFAULT_BASE_URL).rstrip("/")
    session = requests.Session()
    session.headers.update(_headers(provider))
    sid = ""
    try:
        r = session.post(base + "/api/session", json={}, timeout=timeout)
        _raise(r, "create session")
        try:
            sid = r.json()["data"]["id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise APIError(f"OpenCode create session: bad response: {r.text[:500]}") from exc

        r = session.post(
            base + f"/api/session/{sid}/model",
            json={"model": {"id": model.id, "providerID": "opencode"}},
            timeout=timeout,
        )
        _raise(r, "switch model")

        r = session.post(
            base + f"/api/session/{sid}/prompt",
            json={"text": _flatten(messages)},
            timeout=timeout,
        )
        _raise(r, "send prompt")

        deadline = time.monotonic() + max(timeout, 30)
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise APIError("cancelled")
            r = session.get(base + f"/api/session/{sid}/message", timeout=timeout)
            _raise(r, "read messages")
            for msg in r.json().get("data", []):
                kind = msg.get("type", "")
                if kind == "error":
                    detail = msg.get("error") or msg.get("message") or msg
                    raise APIError(f"OpenCode: {detail}")
                if kind != "assistant":
                    continue
                parts = msg.get("content", [])
                reasoning = "".join(
                    str(p.get("text", "")) for p in parts
                    if isinstance(p, dict) and p.get("type") == "reasoning"
                )
                content = "".join(
                    str(p.get("text", "")) for p in parts
                    if isinstance(p, dict) and p.get("type") == "text"
                )
                if not content:
                    continue
                if on_reasoning and reasoning:
                    on_reasoning(reasoning)
                if on_token:
                    for i in range(0, len(content), 24):
                        if should_cancel and should_cancel():
                            break
                        on_token(content[i:i + 24])
                tokens = msg.get("tokens") or {}
                usage = {
                    "prompt_tokens": tokens.get("input", 0),
                    "completion_tokens": tokens.get("output", 0),
                    "total_tokens": tokens.get("input", 0) + tokens.get("output", 0),
                }
                return StreamResult(
                    content=content,
                    reasoning=reasoning,
                    finish_reason="stop",
                    usage=usage,
                    model=model.id,
                )
            time.sleep(0.25)
        raise APIError("OpenCode response timed out")
    finally:
        if sid:
            try:
                session.delete(base + f"/api/session/{sid}", timeout=5)
            except Exception:
                pass


def chat_stream(provider, model, effort, messages, tools,
                on_token=None, on_reasoning=None, on_tool_start=None,
                on_tool_args=None, should_cancel=None,
                timeout: float = config.DEFAULT_TIMEOUT,
                on_status=None, **_ignored) -> StreamResult:
    """Drop-in for client.chat_stream when provider.key == 'opencode'."""
    return _run(provider, model, effort, messages, tools, timeout,
                on_token, on_reasoning, on_tool_start, on_tool_args,
                should_cancel, on_status)


def chat_blocking(provider, model, effort, messages, tools,
                  timeout: float = config.DEFAULT_TIMEOUT,
                  should_cancel=None, **_ignored) -> StreamResult:
    """Drop-in for client.chat_blocking when provider.key == 'opencode'."""
    return _run(provider, model, effort, messages, tools, timeout,
                None, None, None, None, should_cancel, None)
