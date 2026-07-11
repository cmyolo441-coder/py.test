"""Clipboard integration for copy/paste of long text.

Uses pyperclip when available, with a graceful no-op fallback.
"""
from __future__ import annotations

try:
    import pyperclip

    _HAS_CLIPBOARD = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_CLIPBOARD = False


def copy(text: str) -> bool:
    """Copy text to the system clipboard. Returns True on success."""
    if not _HAS_CLIPBOARD:
        return False
    try:
        pyperclip.copy(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def paste() -> str:
    """Return clipboard contents, or empty string if unavailable."""
    if not _HAS_CLIPBOARD:
        return ""
    try:
        return pyperclip.paste()
    except Exception:  # noqa: BLE001
        return ""


def available() -> bool:
    return _HAS_CLIPBOARD
