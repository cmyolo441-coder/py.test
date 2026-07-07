"""Built-in tool implementations: shell, filesystem and web helpers.

These are deliberately cross-platform (Windows/macOS/Linux) and defensive.
Destructive tools are marked ``dangerous=True`` so the UI can ask for
confirmation before running them.
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

import httpx

from .base import Tool, ToolResult

_MAX_OUTPUT = 12_000  # characters returned to the model


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT:
        return text[:_MAX_OUTPUT] + f"\n... [truncated {len(text) - _MAX_OUTPUT} chars]"
    return text


# ----------------------------------------------------------------------
# Shell
# ----------------------------------------------------------------------
def run_shell(command: str, timeout: int = 700000) -> ToolResult:
    """Execute a shell command and capture combined output."""
    is_windows = platform.system() == "Windows"
    shell_exe = None
    if is_windows:
        # Prefer PowerShell on Windows for a richer experience.
        shell_exe = ["powershell", "-NoProfile", "-Command", command]
    try:
        proc = subprocess.run(
            shell_exe if is_windows else command,
            shell=not is_windows,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return ToolResult(output=f"Command timed out after {timeout}s", success=False)

    output = (proc.stdout or "") + (proc.stderr or "")
    output = output.strip() or "(no output)"
    return ToolResult(
        output=_truncate(output),
        success=proc.returncode == 0,
        metadata={"returncode": proc.returncode},
    )


# ----------------------------------------------------------------------
# Filesystem
# ----------------------------------------------------------------------
def read_file(path: str) -> ToolResult:
    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(output=f"File not found: {path}", success=False)
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(output=str(exc), success=False)
    return ToolResult(output=_truncate(content), metadata={"bytes": p.stat().st_size})


def write_file(path: str, content: str) -> ToolResult:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return ToolResult(output=f"Wrote {len(content)} chars to {path}")


def append_file(path: str, content: str) -> ToolResult:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(content)
    return ToolResult(output=f"Appended {len(content)} chars to {path}")


def list_dir(path: str = ".") -> ToolResult:
    p = Path(path).expanduser()
    if not p.exists():
        return ToolResult(output=f"Directory not found: {path}", success=False)
    entries = []
    for item in sorted(p.iterdir()):
        marker = "/" if item.is_dir() else ""
        size = "" if item.is_dir() else f" ({item.stat().st_size} B)"
        entries.append(f"{item.name}{marker}{size}")
    return ToolResult(output="\n".join(entries) or "(empty)")


# ----------------------------------------------------------------------
# Web
# ----------------------------------------------------------------------
def http_get(url: str) -> ToolResult:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(output=f"HTTP error: {exc}", success=False)
    return ToolResult(output=_truncate(resp.text), metadata={"status": resp.status_code})


# ----------------------------------------------------------------------
# Tool definitions
# ----------------------------------------------------------------------
def get_builtin_tools() -> list[Tool]:
    return [
        Tool(
            name="run_shell",
            description=(
                "Execute a shell command on the user's machine and return its "
                "output. Use for running programs, git, package managers, etc."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds.", "default": 120},
                },
                "required": ["command"],
            },
            func=run_shell,
            dangerous=True,
        ),
        Tool(
            name="read_file",
            description="Read the full contents of a text file.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            func=read_file,
        ),
        Tool(
            name="write_file",
            description=(
                "Create or overwrite a file with the given content. "
                "For very large files (over 2000 chars), the content argument may be "
                "truncated by the model's output limit. Use append_file to write "
                "large files in multiple smaller chunks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write."},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
            func=write_file,
            dangerous=True,
        ),
        Tool(
            name="append_file",
            description=(
                "Append content to the end of a file. "
                "Use this to write large files in smaller chunks: "
                "call write_file first to create the file, "
                "then append_file for each additional chunk."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to append to."},
                    "content": {"type": "string", "description": "Content chunk to append."},
                },
                "required": ["path", "content"],
            },
            func=append_file,
        ),
        Tool(
            name="list_dir",
            description="List files and directories at the given path.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
            func=list_dir,
        ),
        Tool(
            name="http_get",
            description="Fetch the raw text/HTML content of a URL over HTTP GET.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            func=http_get,
        ),
    ]
