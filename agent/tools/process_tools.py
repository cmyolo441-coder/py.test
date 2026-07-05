"""Process tools: list processes and inspect the current process (stdlib-only)."""

from __future__ import annotations

import os
import platform
import subprocess

from .base import Tool, ToolResult

_MAX = 8000


def list_processes(filter_name: str = "") -> ToolResult:
    is_windows = platform.system() == "Windows"
    try:
        if is_windows:
            proc = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=20)
        else:
            proc = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return ToolResult(output=f"Error: {exc}", success=False)
    lines = proc.stdout.splitlines()
    if filter_name:
        header = lines[:1]
        matched = [ln for ln in lines[1:] if filter_name.lower() in ln.lower()]
        lines = header + matched
    return ToolResult(output="\n".join(lines)[:_MAX])


def process_info() -> ToolResult:
    info = {
        "pid": os.getpid(),
        "ppid": os.getppid() if hasattr(os, "getppid") else "n/a",
        "cwd": os.getcwd(),
        "python": platform.python_version(),
        "executable": os.sys.executable,
    }
    return ToolResult(output="\n".join(f"{k}: {v}" for k, v in info.items()))


def get_process_tools() -> list[Tool]:
    return [
        Tool("list_processes", "List running processes, optionally filtered by name.",
             {"type": "object", "properties": {"filter_name": {"type": "string"}}}, list_processes),
        Tool("process_info", "Show information about the current agent process.",
             {"type": "object", "properties": {}}, process_info),
    ]
