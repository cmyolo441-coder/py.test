"""Tool registry: stores tools and exposes provider-specific schemas."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult
from .catalog import get_all_tools


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(output=f"Unknown tool: {name}", success=False)
        return tool.run(**arguments)

    def openai_schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def anthropic_schemas(self) -> list[dict[str, Any]]:
        return [t.to_anthropic_schema() for t in self._tools.values()]


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in get_all_tools():
        registry.register(tool)
    return registry
