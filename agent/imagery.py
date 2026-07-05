"""Terminal image rendering using braille / block art.

Converts brightness values into Unicode blocks so simple images or generated
patterns can be displayed directly in the terminal without any GUI.
"""
from __future__ import annotations

from rich.console import Console
from rich.text import Text

_BLOCKS = " .:-=+*#%@"


def brightness_to_char(value: float) -> str:
    """Map a 0..1 brightness to an ASCII block character."""
    idx = min(len(_BLOCKS) - 1, max(0, int(value * (len(_BLOCKS) - 1))))
    return _BLOCKS[idx]


def render_matrix(console: Console, grid: list[list[float]]) -> None:
    """Render a 2D grid of 0..1 brightness values as block art."""
    body = Text()
    for row in grid:
        body.append("".join(brightness_to_char(v) for v in row) + "\n")
    console.print(body)


def sparkline(values: list[float]) -> str:
    """Return a compact unicode sparkline for a series of numbers."""
    if not values:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(bars[int((v - lo) / span * (len(bars) - 1))] for v in values)
