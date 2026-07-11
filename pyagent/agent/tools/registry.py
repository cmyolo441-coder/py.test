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

        # When a call arrives carrying the __malformed_arguments__ marker the
        # streaming JSON was truncated (usually because the content exceeded the
        # model's output window). Re-run the salvage scanner against the raw
        # string so we can recover usable arguments for ANY tool instead of
        # failing with a bogus "missing required argument".
        if "__malformed_arguments__" in arguments:
            from ..providers.openai_provider import _salvage_arguments
            raw_saved = arguments["__malformed_arguments__"]
            salvaged = _salvage_arguments(raw_saved, name)
            if "__malformed_arguments__" in salvaged:
                return ToolResult(
                    output=(
                        f"Tool '{name}' could not be executed: the model's tool call was "
                        f"truncated or malformed and no arguments could be recovered. "
                        f"Try again with a shorter response or restructure the call."
                    ),
                    success=False,
                )
            if "__malformed_arguments__" not in salvaged:
                arguments = salvaged
                # write_file/append_file: warn the model the file may be partial.
                if name in ("write_file", "append_file") and "content" in salvaged:
                    result = tool.run(**salvaged)
                    return ToolResult(
                        output=(
                            f"{result.output}\n"
                            f"NOTE: The tool call was truncated (content too large "
                            f"for one response). Only {len(salvaged['content'])} chars "
                            f"were written. Increase max_tokens, or use append_file to "
                            f"add the remaining content."
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
