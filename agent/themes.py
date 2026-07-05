"""Theme system for the terminal UI.

A ``Theme`` is a named palette of colors plus a gradient used across the banner,
headings, prompt box, spinners and message bubbles. Themes can be switched live
at runtime via the ``/theme`` command; the active theme is a process-global that
every UI helper reads from, so a switch instantly restyles the whole interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    """A complete color palette for the UI."""

    name: str
    # Core accents.
    accent: str          # primary brand color (frames, rules)
    accent2: str         # secondary accent (tool cards, highlights)
    # Semantic colors.
    ok: str
    warn: str
    err: str
    dim: str
    # Text / bubbles.
    user_bubble: str
    ai_bubble: str
    text: str
    # Multi-stop gradient for banner + headings.
    gradient: tuple[str, ...] = field(default=())

    def grad(self) -> list[str]:
        return list(self.gradient) if self.gradient else [self.accent, self.accent2, self.accent]


# ---------------------------------------------------------------------------
# Built-in themes
# ---------------------------------------------------------------------------
THEMES: dict[str, Theme] = {
    "neon": Theme(
        name="neon",
        accent="#a855f7",
        accent2="#22d3ee",
        ok="#22c55e",
        warn="#f59e0b",
        err="#ef4444",
        dim="#9e9e9e",
        user_bubble="#22d3ee",
        ai_bubble="#a855f7",
        text="#ededed",
        gradient=("#a855f7", "#d946ef", "#22d3ee", "#38bdf8", "#a855f7"),
    ),
    "cyberpunk": Theme(
        name="cyberpunk",
        accent="#ff00ff",
        accent2="#00ffff",
        ok="#39ff14",
        warn="#ffd300",
        err="#ff2a6d",
        dim="#6b5b95",
        user_bubble="#00ffff",
        ai_bubble="#ff2a6d",
        text="#f8f8f2",
        gradient=("#ff2a6d", "#d900ff", "#00b3ff", "#05ffa1", "#ffd300", "#ff2a6d"),
    ),
    "pastel": Theme(
        name="pastel",
        accent="#c4a7e7",
        accent2="#9ccfd8",
        ok="#a6da95",
        warn="#eed49f",
        err="#ed8796",
        dim="#8087a2",
        user_bubble="#9ccfd8",
        ai_bubble="#c4a7e7",
        text="#e0def4",
        gradient=("#c4a7e7", "#f6c1cd", "#9ccfd8", "#a6da95", "#c4a7e7"),
    ),
    "matrix": Theme(
        name="matrix",
        accent="#00ff41",
        accent2="#008f11",
        ok="#00ff41",
        warn="#9dff00",
        err="#ff5555",
        dim="#0d5c1a",
        user_bubble="#00ff41",
        ai_bubble="#00cf35",
        text="#c8ffc8",
        gradient=("#003b00", "#008f11", "#00ff41", "#9dff00", "#00ff41", "#008f11"),
    ),
    "solarized": Theme(
        name="solarized",
        accent="#268bd2",
        accent2="#2aa198",
        ok="#859900",
        warn="#b58900",
        err="#dc322f",
        dim="#657b83",
        user_bubble="#2aa198",
        ai_bubble="#268bd2",
        text="#eee8d5",
        gradient=("#268bd2", "#2aa198", "#859900", "#b58900", "#268bd2"),
    ),
}

DEFAULT_THEME = "neon"

_active: Theme = THEMES[DEFAULT_THEME]


def current() -> Theme:
    """Return the process-wide active theme."""
    return _active


def set_theme(name: str) -> bool:
    """Switch the active theme. Returns True if the name is known."""
    global _active
    key = name.strip().lower()
    if key not in THEMES:
        return False
    _active = THEMES[key]
    return True


def names() -> list[str]:
    return list(THEMES.keys())
