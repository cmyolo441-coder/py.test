"""Advanced file-editing tools: replace, insert, delete lines, view ranges."""

from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult

_MAX = 10000


def view_lines(path: str, start: int = 1, end: int | None = None) -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    end = end or len(lines)
    start = max(1, start)
    selected = lines[start - 1:end]
    numbered = [f"{i:>5} | {ln}" for i, ln in enumerate(selected, start)]
    return ToolResult(output="\n".join(numbered)[:_MAX], metadata={"total": len(lines)})


def replace_in_file(path: str, old: str, new: str, count: int = -1) -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    content = p.read_text(encoding="utf-8")
    occurrences = content.count(old)
    if occurrences == 0:
        return ToolResult(output="old text not found; nothing changed", success=False)
    content = content.replace(old, new, count if count > 0 else -1)
    p.write_text(content, encoding="utf-8")
    replaced = occurrences if count < 0 else min(count, occurrences)
    return ToolResult(output=f"Replaced {replaced} occurrence(s) in {path}")


def insert_line(path: str, line_number: int, text: str) -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    lines = p.read_text(encoding="utf-8").splitlines()
    idx = max(0, min(line_number - 1, len(lines)))
    lines.insert(idx, text)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ToolResult(output=f"Inserted line at {line_number} in {path}")


def delete_lines(path: str, start: int, end: int | None = None) -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    lines = p.read_text(encoding="utf-8").splitlines()
    end = end or start
    del lines[start - 1:end]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ToolResult(output=f"Deleted lines {start}-{end} in {path}")


def get_edit_tools() -> list[Tool]:
    return [
        Tool("view_lines", "View a numbered range of lines from a file.",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "start": {"type": "integer", "default": 1},
                 "end": {"type": "integer"}}, "required": ["path"]}, view_lines),
        Tool("replace_in_file", "Replace occurrences of a substring in a file.",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "old": {"type": "string"},
                 "new": {"type": "string"}, "count": {"type": "integer", "default": -1}},
              "required": ["path", "old", "new"]}, replace_in_file, dangerous=True),
        Tool("insert_line", "Insert a line of text at a given line number.",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "line_number": {"type": "integer"},
                 "text": {"type": "string"}}, "required": ["path", "line_number", "text"]},
             insert_line, dangerous=True),
        Tool("delete_lines", "Delete a range of lines from a file.",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "start": {"type": "integer"},
                 "end": {"type": "integer"}}, "required": ["path", "start"]},
             delete_lines, dangerous=True),
    ]
