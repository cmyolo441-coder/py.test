"""Text manipulation helpers used across the agent."""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def truncate_middle(text: str, max_len: int = 2000, marker: str = "\n…[truncated]…\n") -> str:
    """Keep the head and tail of long text, eliding the middle."""
    if len(text) <= max_len:
        return text
    keep = (max_len - len(marker)) // 2
    return text[:keep] + marker + text[-keep:]


def truncate_end(text: str, max_len: int = 2000, marker: str = "\n…[truncated]") -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - len(marker)] + marker


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def slugify(text: str, max_len: int = 60) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def word_count(text: str) -> int:
    return len(text.split())


def first_line(text: str, max_len: int = 80) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:max_len]
