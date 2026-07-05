"""File search tools: find files by glob, grep text content, count lines."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .base import Tool, ToolResult

_MAX = 8000
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", "dist", "build"}


def find_files(pattern: str = "*", root: str = ".", limit: int = 200) -> ToolResult:
    matches: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if fnmatch.fnmatch(name, pattern):
                matches.append(os.path.relpath(os.path.join(dirpath, name), root))
                if len(matches) >= limit:
                    return ToolResult(output="\n".join(matches) + f"\n… (limited to {limit})")
    return ToolResult(output="\n".join(matches) or "(no matches)", metadata={"count": len(matches)})


def grep(query: str, root: str = ".", glob: str = "*", ignore_case: bool = True, limit: int = 100) -> ToolResult:
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(query, flags)
    except re.error as exc:
        return ToolResult(output=f"Invalid regex: {exc}", success=False)
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not fnmatch.fnmatch(name, glob):
                continue
            fp = Path(dirpath) / name
            try:
                for i, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        rel = os.path.relpath(fp, root)
                        hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                        if len(hits) >= limit:
                            return ToolResult(output="\n".join(hits) + f"\n… (limited to {limit})")
            except OSError:
                continue
    return ToolResult(output="\n".join(hits)[:_MAX] or "(no matches)", metadata={"count": len(hits)})


def count_lines(path: str) -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    try:
        n = sum(1 for _ in p.open("r", encoding="utf-8", errors="ignore"))
    except OSError as exc:
        return ToolResult(output=str(exc), success=False)
    return ToolResult(output=f"{n} lines in {path}", metadata={"lines": n})


def get_search_tools() -> list[Tool]:
    return [
        Tool("find_files", "Recursively find files matching a glob pattern.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string", "default": "*"},
                 "root": {"type": "string", "default": "."},
                 "limit": {"type": "integer", "default": 200}}}, find_files),
        Tool("grep", "Search file contents with a regex across a directory tree.",
             {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "root": {"type": "string", "default": "."},
                 "glob": {"type": "string", "default": "*"},
                 "ignore_case": {"type": "boolean", "default": True},
                 "limit": {"type": "integer", "default": 100}}, "required": ["query"]}, grep),
        Tool("count_lines", "Count the number of lines in a file.",
             {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, count_lines),
    ]
