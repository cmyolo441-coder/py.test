"""System introspection tools: platform info, env vars, process list, disk."""

from __future__ import annotations

import os
import platform
import shutil

from .base import Tool, ToolResult


def system_info() -> ToolResult:
    info = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "cwd": os.getcwd(),
    }
    lines = [f"{k:12}: {v}" for k, v in info.items()]
    return ToolResult(output="\n".join(lines))


def get_env(name: str = "") -> ToolResult:
    if name:
        val = os.getenv(name)
        return ToolResult(output=f"{name}={val}" if val is not None else f"{name} is not set")
    keys = sorted(os.environ.keys())
    return ToolResult(output="\n".join(keys), metadata={"count": len(keys)})


def disk_usage(path: str = ".") -> ToolResult:
    try:
        total, used, free = shutil.disk_usage(path)
    except OSError as exc:
        return ToolResult(output=str(exc), success=False)
    gb = 1024 ** 3
    return ToolResult(
        output=f"total: {total/gb:.1f} GB\nused:  {used/gb:.1f} GB\nfree:  {free/gb:.1f} GB"
    )


def which(program: str) -> ToolResult:
    path = shutil.which(program)
    return ToolResult(output=path or f"'{program}' not found on PATH", success=bool(path))


def get_system_tools() -> list[Tool]:
    return [
        Tool("system_info", "Show OS, CPU, Python and machine information.",
             {"type": "object", "properties": {}}, system_info),
        Tool("get_env", "Read an environment variable, or list all names if none given.",
             {"type": "object", "properties": {"name": {"type": "string"}}}, get_env),
        Tool("disk_usage", "Show disk usage for a path.",
             {"type": "object", "properties": {"path": {"type": "string", "default": "."}}}, disk_usage),
        Tool("which", "Locate an executable on PATH.",
             {"type": "object", "properties": {"program": {"type": "string"}}, "required": ["program"]}, which),
    ]
