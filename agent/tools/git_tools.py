"""Git operations tool: status, diff, log, branch, add, commit (read-mostly)."""

from __future__ import annotations

import subprocess

from .base import Tool, ToolResult

_MAX = 8000


def _git(args: list[str], timeout: int = 700000) -> ToolResult:
    try:
        proc = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return ToolResult(output="git is not installed or not on PATH", success=False)
    except subprocess.TimeoutExpired:
        return ToolResult(output="git command timed out", success=False)
    out = (proc.stdout + proc.stderr).strip() or "(no output)"
    return ToolResult(output=out[:_MAX], success=proc.returncode == 0, metadata={"rc": proc.returncode})


def git_status() -> ToolResult:
    return _git(["status", "--short", "--branch"])


def git_diff(staged: bool = False, path: str | None = None) -> ToolResult:
    args = ["diff"]
    if staged:
        args.append("--staged")
    if path:
        args.append(path)
    return _git(args)


def git_log(limit: int = 10) -> ToolResult:
    return _git(["log", f"-{limit}", "--oneline", "--decorate", "--graph"])


def git_branch() -> ToolResult:
    return _git(["branch", "-a", "-vv"])


def get_git_tools() -> list[Tool]:
    return [
        Tool(
            name="git_status",
            description="Show the working tree status (branch + changed files).",
            parameters={"type": "object", "properties": {}},
            func=git_status,
        ),
        Tool(
            name="git_diff",
            description="Show git diff of unstaged (or staged) changes.",
            parameters={
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "default": False},
                    "path": {"type": "string"},
                },
            },
            func=git_diff,
        ),
        Tool(
            name="git_log",
            description="Show recent commit history as a graph.",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
            func=git_log,
        ),
        Tool(
            name="git_branch",
            description="List local and remote branches with tracking info.",
            parameters={"type": "object", "properties": {}},
            func=git_branch,
        ),
    ]
