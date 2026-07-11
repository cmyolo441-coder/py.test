"""HTTP tools: GET/POST with headers and JSON, plus a text extractor."""

from __future__ import annotations

import json as _json
import re

import httpx

from .base import Tool, ToolResult

_MAX = 128000
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n\s*\n+")


def http_request(method: str, url: str, headers: dict | None = None, body: str | None = None) -> ToolResult:
    try:
        resp = httpx.request(
            method.upper(),
            url,
            headers=headers or {},
            content=body.encode("utf-8") if body else None,
            timeout=700000,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return ToolResult(output=f"HTTP error: {exc}", success=False)
    text = resp.text[:_MAX]
    return ToolResult(
        output=f"[{resp.status_code}] {text}",
        success=resp.is_success,
        metadata={"status": resp.status_code, "headers": dict(resp.headers)},
    )


def http_json(url: str, method: str = "GET", payload: dict | None = None) -> ToolResult:
    try:
        resp = httpx.request(
            method.upper(), url, json=payload, timeout=700000, follow_redirects=True
        )
        data = resp.json()
    except httpx.HTTPError as exc:
        return ToolResult(output=f"HTTP error: {exc}", success=False)
    except _json.JSONDecodeError:
        return ToolResult(output="Response was not valid JSON", success=False)
    pretty = _json.dumps(data, indent=2)[:_MAX]
    return ToolResult(output=pretty, success=resp.is_success)


def fetch_text(url: str) -> ToolResult:
    """Fetch a URL and strip HTML tags to return readable text."""
    try:
        resp = httpx.get(url, timeout=700000, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(output=f"HTTP error: {exc}", success=False)
    text = _TAG_RE.sub(" ", resp.text)
    text = _WS_RE.sub("\n\n", text)
    text = " ".join(text.split())
    return ToolResult(output=text[:_MAX])


def get_http_tools() -> list[Tool]:
    return [
        Tool(
            name="http_request",
            description="Make an arbitrary HTTP request (GET/POST/PUT/DELETE) and return the raw response.",
            parameters={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "default": "GET"},
                    "url": {"type": "string"},
                    "headers": {"type": "object"},
                    "body": {"type": "string"},
                },
                "required": ["url"],
            },
            func=http_request,
        ),
        Tool(
            name="http_json",
            description="Call a JSON API and return the pretty-printed JSON response.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string", "default": "GET"},
                    "payload": {"type": "object"},
                },
                "required": ["url"],
            },
            func=http_json,
        ),
        Tool(
            name="fetch_text",
            description="Fetch a web page and return its readable text (HTML tags stripped).",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            func=fetch_text,
        ),
    ]
