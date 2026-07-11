"""Text-processing tools: case conversion, counting, diff, sort, dedupe."""

from __future__ import annotations

import difflib

from .base import Tool, ToolResult

_MAX = 10000


def text_stats(text: str) -> ToolResult:
    lines = text.splitlines()
    words = text.split()
    stats = {
        "characters": len(text),
        "words": len(words),
        "lines": len(lines),
        "unique_words": len(set(w.lower() for w in words)),
    }
    return ToolResult(output="\n".join(f"{k}: {v}" for k, v in stats.items()))


def change_case(text: str, mode: str = "upper") -> ToolResult:
    fns = {
        "upper": str.upper,
        "lower": str.lower,
        "title": str.title,
        "capitalize": str.capitalize,
        "swap": str.swapcase,
    }
    fn = fns.get(mode)
    if fn is None:
        return ToolResult(output=f"Unknown mode: {mode}", success=False)
    return ToolResult(output=fn(text)[:_MAX])


def sort_lines(text: str, reverse: bool = False, unique: bool = False) -> ToolResult:
    lines = text.splitlines()
    if unique:
        seen: set[str] = set()
        lines = [x for x in lines if not (x in seen or seen.add(x))]
    lines.sort(reverse=reverse)
    return ToolResult(output="\n".join(lines)[:_MAX])


def text_diff(a: str, b: str) -> ToolResult:
    diff = difflib.unified_diff(
        a.splitlines(), b.splitlines(), lineterm="", fromfile="a", tofile="b"
    )
    return ToolResult(output="\n".join(diff)[:_MAX] or "(identical)")


def dedupe_lines(text: str) -> ToolResult:
    seen: set[str] = set()
    out = [x for x in text.splitlines() if not (x in seen or seen.add(x))]
    return ToolResult(output="\n".join(out)[:_MAX], metadata={"kept": len(out)})


def get_text_tools() -> list[Tool]:
    return [
        Tool("text_stats", "Count characters, words, lines and unique words.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, text_stats),
        Tool("change_case", "Convert text case (upper/lower/title/capitalize/swap).",
             {"type": "object", "properties": {
                 "text": {"type": "string"}, "mode": {"type": "string", "default": "upper"}},
              "required": ["text"]}, change_case),
        Tool("sort_lines", "Sort lines of text, optionally reversed / unique.",
             {"type": "object", "properties": {
                 "text": {"type": "string"}, "reverse": {"type": "boolean", "default": False},
                 "unique": {"type": "boolean", "default": False}}, "required": ["text"]}, sort_lines),
        Tool("text_diff", "Produce a unified diff between two texts.",
             {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
              "required": ["a", "b"]}, text_diff),
        Tool("dedupe_lines", "Remove duplicate lines, preserving order.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, dedupe_lines),
    ]
