"""Tool registry: stores tools and exposes provider-specific schemas."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult, validate_arguments
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
            known = ", ".join(sorted(self._tools)) or "(none)"
            return ToolResult(
                output=f"Unknown tool: {name!r}. Available tools: {known}",
                success=False,
            )

        # When a write_file/append_file call arrives with __malformed_arguments__
        # (meaning the streaming JSON was truncated because the content exceeded
        # the model's output window), try to extract path and partial content
        # directly from the raw string so the file still gets created.
        if "__malformed_arguments__" in arguments and name in ("write_file", "append_file"):
            from ..providers.openai_provider import _salvage_arguments
            raw_saved = arguments["__malformed_arguments__"]
            salvaged = _salvage_arguments(raw_saved, name)
            if "path" in salvaged and "content" in salvaged:
                arguments = salvaged
                # Add a note so the model knows the file may be incomplete.
                path = salvaged["path"]
                content = salvaged["content"]
                result = tool.run(path=path, content=content)
                return ToolResult(
                    output=(
                        f"{result.output}\n"
                        f"NOTE: The tool call was truncated (content too large). "
                        f"Only {len(content)} chars were written. "
                        f"Use append_file to write the remaining content in chunks."
                    ),
                    success=result.success,
                )

        # Validate arguments against the tool's JSON schema BEFORE calling the
        # Python function. This turns a hard `TypeError: missing positional
        # argument` crash into a clear, actionable message the model can fix on
        # the next turn — and guarantees no tool is ever invoked with bad args.
        error = validate_arguments(tool, arguments)
        if error is not None:
            return ToolResult(output=error, success=False)

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
