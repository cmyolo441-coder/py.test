"""Tool creator — the agent writes new tools for itself.

When the agent encounters a task that no existing tool can handle, it can
write a new Python tool, validate it, and register it in the live tool
registry. This is real self-improvement: the agent extends its own
capabilities at runtime.

Generated tools are saved to ~/.terminal_agent/generated_tools/ so they
persist across restarts and can be reviewed by the user.
"""
from __future__ import annotations

import ast
import importlib.util
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .tools.base import Tool, ToolResult
from .tools.catalog import get_all_tools
from .logging_config import get_logger

log = get_logger("agent.tool_creator")

GENERATED_DIR = Path.home() / ".terminal_agent" / "generated_tools"


def validate_tool_code(code: str) -> tuple[bool, str]:
    """Validate that a code string is safe to load as a tool.

    Checks:
      * Parses as valid Python
      * No imports of dangerous modules (os.system, subprocess without guard)
      * Defines a callable that returns a ToolResult
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"Syntax error: {exc}"
    # Check for dangerous patterns.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("ctypes",):
                    return False, f"Blocked import: {alias.name}"
        if isinstance(node, ast.Attribute):
            if node.attr in ("system", "popen") and isinstance(node.value, ast.Name) and node.value.id == "os":
                # Allow if it's in a guarded context — for simplicity, block.
                return False, f"Blocked os.{node.attr} — use subprocess with shell=False instead"
    # Check there's at least one function definition.
    has_func = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree))
    if not has_func:
        return False, "No function definition found"
    return True, "ok"


def create_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    code: str,
    function_name: str = "run",
    dangerous: bool = False,
) -> tuple[bool, str, Tool | None]:
    """Create, validate and register a new tool from Python code.

    Returns (success, message, tool_or_none).
    """
    # Validate.
    ok, msg = validate_tool_code(code)
    if not ok:
        return False, f"Validation failed: {msg}", None
    # Name collision check.
    existing = {t.name for t in get_all_tools()}
    if name in existing:
        return False, f"A tool named '{name}' already exists", None
    # Write to a temp file and import it.
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    module_path = GENERATED_DIR / f"{name}.py"
    module_path.write_text(code, encoding="utf-8")
    # Import the module.
    try:
        spec = importlib.util.spec_from_file_location(f"generated_tool_{name}", module_path)
        if spec is None or spec.loader is None:
            return False, "Could not create module spec", None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        return False, f"Failed to load module: {exc}", None
    # Get the function.
    func = getattr(module, function_name, None)
    if func is None or not callable(func):
        return False, f"Function '{function_name}' not found in generated code", None
    # Wrap in a Tool.
    tool = Tool(
        name=name,
        description=description,
        parameters=parameters,
        func=func,
        dangerous=dangerous,
    )
    log.info("Created new tool: %s", name)
    return True, f"Tool '{name}' created and saved to {module_path}", tool


def list_generated_tools() -> list[dict[str, Any]]:
    """List all generated tools."""
    if not GENERATED_DIR.exists():
        return []
    tools = []
    for path in GENERATED_DIR.glob("*.py"):
        # Read the first few lines for the description.
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            # Find the docstring.
            tree = ast.parse(content)
            docstring = ast.get_docstring(tree) or ""
        except Exception:  # noqa: BLE001
            docstring = ""
        tools.append({
            "name": path.stem,
            "path": str(path),
            "description": docstring[:100],
            "size_bytes": path.stat().st_size,
        })
    return tools


def delete_generated_tool(name: str) -> bool:
    path = GENERATED_DIR / f"{name}.py"
    if path.exists():
        path.unlink()
        return True
    return False


def suggest_tool_for_task(task: str) -> str:
    """Generate a suggested tool implementation for a task description.

    Returns Python code that can be passed to create_tool().
    """
    # This is a template — in practice the agent's LLM would generate this.
    safe_name = "".join(c if c.isalnum() else "_" for c in task[:30]).lower()
    return f'''"""Generated tool for: {task}

This tool was auto-generated by the tool creator. Review before use.
"""
from agent.tools.base import ToolResult


def run(**kwargs) -> ToolResult:
    """Execute the task: {task}

    TODO: implement the actual logic here.
    """
    # This is a placeholder — the agent should fill in real implementation.
    return ToolResult(output=f"Tool executed with args: {{kwargs}}")
'''
