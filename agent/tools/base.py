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


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def validate_arguments(tool: "Tool", arguments: Any) -> str | None:
    """Validate ``arguments`` against ``tool.parameters`` (JSON schema).

    Returns ``None`` when the arguments are usable, or a clear, model-actionable
    error string describing exactly what is wrong (missing required keys, wrong
    types, or unknown keys). This runs before the tool function is called so a
    malformed/empty argument object never crashes the Python callable.
    """
    schema = tool.parameters or {}
    props: dict[str, Any] = schema.get("properties", {}) or {}
    required: list[str] = list(schema.get("required", []) or [])

    if not isinstance(arguments, dict):
        return (
            f"Tool '{tool.name}' expected a JSON object of arguments but got "
            f"{type(arguments).__name__}. Required: {required or 'none'}. "
            f"Re-issue the call with a valid arguments object."
        )

    # Missing required keys — the classic cause of the write_file crash.
    missing = [k for k in required if k not in arguments or arguments[k] is None]
    if missing:
        expected = ", ".join(
            f"{k} ({props.get(k, {}).get('type', 'any')})" for k in props
        ) or "none"
        hint = ""
        if tool.name in ("write_file", "append_file") and any("content" in m for m in missing):
            hint = (
                " Re-issue write_file with the COMPLETE file content in a single call."
                " The output limit is 128000 tokens — do NOT split into chunks."
            )
        return (
            f"Tool '{tool.name}' is missing required argument(s): "
            f"{', '.join(missing)}. Expected arguments: {expected}. "
            f"Re-issue the call including every required argument.{hint}"
        )

    # Basic type checks for provided values (best-effort; unknown types skip).
    for key, value in arguments.items():
        spec = props.get(key)
        if not spec:
            continue  # unknown key — tolerated, forwarded to the tool
        expected_type = spec.get("type")
        allowed = _JSON_TYPES.get(expected_type) if expected_type else None
        if allowed and not isinstance(value, allowed):
            # bool is a subclass of int — guard the integer/number case.
            if expected_type in ("integer", "number") and isinstance(value, bool):
                pass
            else:
                return (
                    f"Tool '{tool.name}' argument '{key}' should be "
                    f"{expected_type} but got {type(value).__name__}. "
                    f"Re-issue the call with the correct type."
                )
    return None
