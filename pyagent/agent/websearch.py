"""Web search tool using the DuckDuckGo Instant Answer + HTML endpoints.

No API key required. Returns a concise, plain-text list of results suitable to
feed back into the model.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from html import unescape
from typing import Any

import httpx

_RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>', re.DOTALL
)
_SNIPPET_RE = re.compile(
    r'<a class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return unescape(_TAG_RE.sub("", text)).strip()


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return the top results as text."""
    if not query.strip():
        return "Error: empty query"
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (compatible; termianlagent/1.0)"},
            timeout=700000,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return f"Error performing web search: {exc}"

    html = resp.text
    links = _RESULT_RE.findall(html)
    snippets = _SNIPPET_RE.findall(html)
    if not links:
        return "No results found."

    lines: list[str] = []
    for i, (url, title) in enumerate(links[:max_results]):
        snippet = _strip_html(snippets[i]) if i < len(snippets) else ""
        lines.append(f"{i + 1}. {_strip_html(title)}\n   {url}\n   {snippet}")
    return "\n".join(lines)


def register() -> list[tuple[dict[str, Any], Callable[..., str]]]:
    schema = {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for up-to-date information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "Default 5."},
                },
                "required": ["query"],
            },
        },
    }
    return [(schema, web_search)]
