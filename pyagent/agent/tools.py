"""Tool/function-calling implementations for the agent.

Each tool is a small, safe capability the model can request. Tools return
plain strings that are fed back into the conversation.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

# A conservative allow-list of shell commands the agent may run.
SAFE_COMMANDS = {
    "ls", "pwd", "cat", "echo", "date", "whoami", "head", "tail",
    "wc", "grep", "find", "git", "python", "python3", "pip", "uname",
}


def read_file(path: str, max_bytes: int = 128000) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    data = p.read_text(encoding="utf-8", errors="replace")
    if len(data) > max_bytes:
        return data[:max_bytes] + "\n... [truncated]"
    return data


def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


def list_dir(path: str = ".") -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: path not found: {path}"
    entries = sorted(
        ("[dir]  " + e.name if e.is_dir() else "[file] " + e.name) for e in p.iterdir()
    )
    return "\n".join(entries) or "(empty)"


def run_shell(command: str, timeout: int = 700000) -> str:
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"Error parsing command: {exc}"
    if not parts:
        return "Error: empty command"
    if parts[0] not in SAFE_COMMANDS:
        return (
            f"Refused: '{parts[0]}' is not in the safe command allow-list. "
            f"Allowed: {', '.join(sorted(SAFE_COMMANDS))}"
        )
    try:
        result = subprocess.run(
            parts, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return f"Error running command: {exc}"
    out = result.stdout.strip()
    err = result.stderr.strip()
    combined = out
    if err:
        combined += ("\n[stderr]\n" + err) if combined else ("[stderr]\n" + err)
    return combined or f"(exit code {result.returncode}, no output)"


# Registry mapping tool name -> callable
TOOL_FUNCTIONS: dict[str, Callable[..., str]] = {
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "run_shell": run_shell,
}

# JSON schema definitions (OpenAI-style) shared by all providers.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories at a path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path. Defaults to current dir."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a safe, allow-listed shell command and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."}
                },
                "required": ["command"],
            },
        },
    },
]


def load_plugins() -> int:
    """Discover plugin tools and register them into the global registry.

    Returns the number of tools added. Safe to call multiple times.
    """
    try:
        from .plugins import discover_plugins
    except Exception:  # noqa: BLE001
        return 0
    added = 0
    for schema, func in discover_plugins():
        tool_name = schema.get("function", {}).get("name")
        if not tool_name or tool_name in TOOL_FUNCTIONS:
            continue
        TOOL_FUNCTIONS[tool_name] = func
        TOOL_SCHEMAS.append(schema)
        added += 1
    # Register first-party optional tools (web search, python sandbox).
    for module_name in ("websearch", "sandbox", "fileops", "shell"):
        try:
            module = __import__(f"agent.{module_name}", fromlist=["register"])
            for schema, func in module.register():
                tool_name = schema.get("function", {}).get("name")
                if tool_name and tool_name not in TOOL_FUNCTIONS:
                    TOOL_FUNCTIONS[tool_name] = func
                    TOOL_SCHEMAS.append(schema)
                    added += 1
        except Exception:  # noqa: BLE001
            continue
    return added


def execute_tool(name: str, arguments: str | dict[str, Any]) -> str:
    """Execute a tool by name with JSON or dict arguments."""
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return f"Error: unknown tool '{name}'"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"Error: invalid tool arguments: {exc}"
    try:
        return func(**arguments)
    except TypeError as exc:
        return f"Error: bad arguments for '{name}': {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error executing '{name}': {exc}"
