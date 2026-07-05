"""Base classes for agent tools.

Every tool exposes a JSON schema (OpenAI/Anthropic compatible) and a callable
``run`` method. Tools return a ``ToolResult`` so the agent loop can render
success/failure consistently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolResult:
    """Result returned by a tool execution."""

    output: str
    success: bool = True
    metadata: dict[str, Any] | None = None

    def as_message(self) -> str:
        prefix = "" if self.success else "ERROR: "
        return f"{prefix}{self.output}"


@dataclass
class Tool:
    """A callable tool that the LLM may invoke.

    Attributes:
        name: Unique identifier used by the model.
        description: Natural-language description shown to the model.
        parameters: JSON schema describing the arguments.
        func: The Python callable implementing the tool.
        dangerous: Whether the tool requires explicit user approval.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., ToolResult]
    dangerous: bool = False

    def run(self, **kwargs: Any) -> ToolResult:
        try:
            return self.func(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any tool error to the model
            return ToolResult(output=f"{type(exc).__name__}: {exc}", success=False)

    # Schema helpers -----------------------------------------------------
    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }
