"""Theme system for the terminal UI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str
    accent2: str
    ok: str
    warn: str
    err: str
    dim: str
    text: str


THEMES: dict[str, Theme] = {
    "default": Theme("default", "#c9d1d9", "#58a6ff", "#7ee787", "#f2cc60", "#ff7b72", "#8b949e", "#f0f6fc"),
    "neon": Theme("neon", "#a855f7", "#22d3ee", "#22c55e", "#f59e0b", "#ef4444", "#6b7280", "#f3f4f6"),
    "pastel": Theme("pastel", "#f9a8d4", "#93c5fd", "#86efac", "#fcd34d", "#fca5a5", "#9ca3af", "#f9fafb"),
    "matrix": Theme("matrix", "#00ff00", "#00cc00", "#00ff00", "#ffff00", "#ff0000", "#006600", "#00ff00"),
}

_current: str = "default"


def current() -> Theme:
    return THEMES.get(_current, THEMES["default"])


def set_theme(name: str) -> bool:
    global _current
    if name in THEMES:
        _current = name
        return True
    return False


def names() -> list[str]:
    return list(THEMES.keys())
