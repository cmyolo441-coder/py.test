"""Extended theme catalog with vivid presets for live switching."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VividTheme:
    name: str
    primary: str
    secondary: str
    accent: str
    bg: str
    gradient_start: tuple[int, int, int]
    gradient_end: tuple[int, int, int]


VIVID_THEMES: dict[str, VividTheme] = {
    "neon": VividTheme("neon", "#39ff14", "#ff073a", "#00e5ff", "#0a0a0a",
                        (57, 255, 20), (0, 229, 255)),
    "cyberpunk": VividTheme("cyberpunk", "#f637ec", "#00f0ff", "#fbff00", "#1a0033",
                            (246, 55, 236), (0, 240, 255)),
    "pastel": VividTheme("pastel", "#ffb3ba", "#bae1ff", "#baffc9", "#fffdf7",
                         (255, 179, 186), (186, 225, 255)),
    "matrix": VividTheme("matrix", "#00ff41", "#008f11", "#00ff41", "#000000",
                         (0, 255, 65), (0, 143, 17)),
    "sunset": VividTheme("sunset", "#ff6b6b", "#feca57", "#ff9ff3", "#2c2c54",
                         (255, 107, 107), (255, 159, 243)),
    "ocean": VividTheme("ocean", "#00b4d8", "#0077b6", "#90e0ef", "#03045e",
                        (0, 180, 216), (144, 224, 239)),
}

_active = "cyberpunk"


def get_vivid(name: str | None = None) -> VividTheme:
    return VIVID_THEMES.get(name or _active, VIVID_THEMES["cyberpunk"])


def set_vivid(name: str) -> bool:
    global _active
    if name in VIVID_THEMES:
        _active = name
        return True
    return False


def list_vivid() -> list[str]:
    return sorted(VIVID_THEMES)
