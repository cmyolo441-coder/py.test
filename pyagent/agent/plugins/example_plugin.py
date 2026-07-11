"""Example plugin (reference only, not auto-loaded from the repo).

Copy this file to ~/.terminal_agent/plugins/ to enable it. It demonstrates how
to expose custom tools to the agent via a ``register()`` function.
"""

from __future__ import annotations

from agent.tools.base import Tool, ToolResult


def reverse_text(text: str) -> ToolResult:
    return ToolResult(output=text[::-1])


def register() -> list[Tool]:
    return [
        Tool(
            name="reverse_text",
            description="Reverse a string (example plugin tool).",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            func=reverse_text,
        )
    ]
