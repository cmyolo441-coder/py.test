"""Visual effects & animations for the terminal UI.

Everything here is pure-Rich, cross-platform and degrades gracefully. These
helpers power the gradient banner, typewriter reveals, shimmer/pulse thinking
states and animated tool progress that make this TUI feel alive.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from rich.align import Align
from rich.console import Console, Group
from rich.text import Text

from . import themes


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:  # shorthand like #f0f -> #ff00ff
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c))) for c in rgb))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def blend(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex((lerp(r1, r2, t), lerp(g1, g2, t), lerp(b1, b2, t)))


def gradient_text(
    text: str, stops: list[str] | None = None, offset: float = 0.0, bold: bool = True
) -> Text:
    """Apply a smooth multi-stop horizontal gradient across visible chars.

    When ``stops`` is omitted the active theme's gradient is used, so callers can
    simply do ``gradient_text("hi")`` and get an on-theme result.
    """
    if not stops:
        try:
            stops = themes.current().grad()
        except Exception:  # pragma: no cover - defensive
            stops = GRADIENT_STOPS
    out = Text()
    chars = [c for c in text]
    n = max(1, len([c for c in chars if c != "\n"]) - 1)
    idx = 0
    for ch in chars:
        if ch == "\n":
            out.append("\n")
            continue
        pos = (idx / n + offset) % 1.0
        seg = pos * (len(stops) - 1)
        i = int(seg)
        t = seg - i
        j = min(i + 1, len(stops) - 1)
        color = blend(stops[i], stops[j], t)
        style = f"bold {color}" if bold else color
        out.append(ch, style=style)
        idx += 1
    return out


def _hsv_to_hex(h: float, s: float = 1.0, v: float = 1.0) -> str:
    """Convert an HSV triple (h in [0,1)) to a #rrggbb hex string."""
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))[i]
    return _rgb_to_hex((r * 255, g * 255, b * 255))


def rainbow_text(text: str, offset: float = 0.0, bold: bool = True) -> Text:
    """Color each visible character along a full-spectrum rainbow.

    The returned ``Text`` preserves the input exactly, so ``.plain`` equals the
    original string.
    """
    out = Text()
    n = max(1, len([c for c in text if c != "\n"]))
    idx = 0
    for ch in text:
        if ch == "\n":
            out.append("\n")
            continue
        hue = (idx / n + offset) % 1.0
        color = _hsv_to_hex(hue)
        out.append(ch, style=f"bold {color}" if bold else color)
        idx += 1
    return out


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
BANNER_ART = r"""
   â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—  â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•— â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ–ˆâ•—   â–ˆâ–ˆâ•—â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—    â–ˆâ–ˆâ•—  â–ˆâ–ˆâ•—
  â–ˆâ–ˆâ•”â•â•â–ˆâ–ˆâ•—â–ˆâ–ˆâ•”â•â•â•â•â• â–ˆâ–ˆâ•”â•â•â•â•â•â–ˆâ–ˆâ–ˆâ–ˆâ•—  â–ˆâ–ˆâ•‘â•šâ•â•â–ˆâ–ˆâ•”â•â•â•    â•šâ–ˆâ–ˆâ•—â–ˆâ–ˆâ•”â•
  â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•‘â–ˆâ–ˆâ•‘  â–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—  â–ˆâ–ˆâ•”â–ˆâ–ˆâ•— â–ˆâ–ˆâ•‘   â–ˆâ–ˆâ•‘        â•šâ–ˆâ–ˆâ–ˆâ•”â•
  â–ˆâ–ˆâ•”â•â•â–ˆâ–ˆâ•‘â–ˆâ–ˆâ•‘   â–ˆâ–ˆâ•‘â–ˆâ–ˆâ•”â•â•â•  â–ˆâ–ˆâ•‘â•šâ–ˆâ–ˆâ•—â–ˆâ–ˆâ•‘   â–ˆâ–ˆâ•‘        â–ˆâ–ˆâ•”â–ˆâ–ˆâ•—
  â–ˆâ–ˆâ•‘  â–ˆâ–ˆâ•‘â•šâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•”â•â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ•—â–ˆâ–ˆâ•‘ â•šâ–ˆâ–ˆâ–ˆâ–ˆâ•‘   â–ˆâ–ˆâ•‘       â–ˆâ–ˆâ•”â• â–ˆâ–ˆâ•—
  â•šâ•â•  â•šâ•â• â•šâ•â•â•â•â•â• â•šâ•â•â•â•â•â•â•â•šâ•â•  â•šâ•â•â•â•   â•šâ•â•       â•šâ•â•  â•šâ•â•
""".strip("\n")

GRADIENT_STOPS = ["#7c3aed", "#8b5cf6", "#06b6d4", "#22d3ee", "#a78bfa", "#7c3aed"]


def stops() -> list[str]:
    """Current theme gradient stops (falls back to the classic palette)."""
    try:
        return themes.current().grad()
    except Exception:  # pragma: no cover - defensive
        return GRADIENT_STOPS


def heading(text: str, offset: float = 0.0) -> Text:
    """A gradient-styled heading using the active theme."""
    return gradient_text(text, stops(), offset=offset)


def animate_banner(console: Console, frames: int = 26, fps: int = 60) -> None:
    """Play a flowing gradient wave across the ASCII banner once."""
    from rich.live import Live

    delay = 1.0 / fps
    palette = stops()
    with Live(console=console, refresh_per_second=fps, transient=False) as live:
        for f in range(frames):
            offset = f / frames
            art = gradient_text(BANNER_ART, palette, offset=offset)
            live.update(Align.center(art))
            time.sleep(delay)


def typewriter(console: Console, text: str, style: str = "", delay: float = 0.012) -> None:
    """Reveal text one character at a time (Cursor-style intro line)."""
    rendered = Text()
    from rich.live import Live

    with Live(console=console, refresh_per_second=90, transient=False) as live:
        for ch in text:
            rendered.append(ch, style=style)
            live.update(Align.center(rendered))
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Thinking indicator (shimmer + orbiting glyphs)
# ---------------------------------------------------------------------------
ORBIT = ["â ‹", "â ™", "â ¹", "â ¸", "â ¼", "â ´", "â ¦", "â §", "â ‡", "â "]
THINKING_WORDS = [
    "thinking", "reasoning", "planning", "analyzing",
    "synthesizing", "computing", "orchestrating", "reflecting",
]


# Selectable spinner styles.
SPINNERS: dict[str, list[str]] = {
    "braille": ["â ‹", "â ™", "â ¹", "â ¸", "â ¼", "â ´", "â ¦", "â §", "â ‡", "â "],
    "dots": ["Â·  ", "Â·Â· ", "Â·Â·Â·", " Â·Â·", "  Â·", "   "],
    "moon": ["ðŸŒ‘", "ðŸŒ’", "ðŸŒ“", "ðŸŒ”", "ðŸŒ•", "ðŸŒ–", "ðŸŒ—", "ðŸŒ˜"],
    "line": ["|", "/", "-", "\\"],
    "arc": ["â—œ", "â— ", "â—", "â—ž", "â—¡", "â—Ÿ"],
    "star": ["âœ¶", "âœ¸", "âœ¹", "âœº", "âœ¹", "âœ·"],
    "bounce": ["â ", "â ‚", "â „", "â ‚"],
}


def spinner_frames(name: str = "braille") -> list[str]:
    """Return the animation frames for a named spinner (falls back to braille)."""
    return SPINNERS.get(name, SPINNERS["braille"])


@dataclass
class Shimmer:
    """A moving highlight that sweeps across a word for a 'loading' feel."""

    lo: str = "#4c1d95"
    hi: str = "#a78bfa"

    def render(self, word: str, phase: float) -> Text:
        out = Text()
        n = max(1, len(word) - 1)
        for i, ch in enumerate(word):
            # Triangle wave centered on the moving phase.
            d = abs(((i / n) - phase) % 1.0)
            d = min(d, 1.0 - d) * 2  # 0 at center, 1 far away
            t = 1.0 - d
            color = blend(self.lo, self.hi, t)
            out.append(ch, style=f"bold {color}")
        return out


def thinking_frame(tick: int, label: str | None = None, spinner: str = "braille") -> Text:
    theme = themes.current()
    frames = SPINNERS.get(spinner, SPINNERS["braille"])
    glyph = frames[tick % len(frames)]
    word = label or THINKING_WORDS[(tick // 10) % len(THINKING_WORDS)]
    phase = (tick % 20) / 20.0
    shimmer = Shimmer(lo=theme.dim if theme.dim.startswith("#") else "#4c1d95", hi=theme.accent).render(
        word + "\u2026", phase
    )
    line = Text()
    line.append(f" {glyph} ", style=f"bold {theme.accent2}")
    line.append_text(shimmer)
    return line


# ---------------------------------------------------------------------------
# Progress bar (smooth animated gradient fill)
# ---------------------------------------------------------------------------
def progress_bar(fraction: float, width: int = 30, label: str = "", offset: float = 0.0) -> Text:
    """Render a single gradient-filled progress bar frame."""
    fraction = max(0.0, min(1.0, fraction))
    filled = int(round(fraction * width))
    palette = stops()
    bar = Text()
    if label:
        bar.append(f"{label} ", style=themes.current().dim)
    bar.append("â”‚", style=themes.current().dim)
    for i in range(width):
        if i < filled:
            pos = ((i / max(1, width - 1)) + offset) % 1.0
            seg = pos * (len(palette) - 1)
            k = int(seg)
            color = blend(palette[k], palette[min(k + 1, len(palette) - 1)], seg - k)
            bar.append("â–ˆ", style=color)
        else:
            bar.append("â–‘", style=themes.current().dim)
    bar.append("â”‚ ", style=themes.current().dim)
    bar.append(f"{int(fraction * 100):3d}%", style=f"bold {themes.current().accent2}")
    return bar


def run_progress(console: Console, label: str, seconds: float = 1.2, width: int = 30) -> None:
    """Play a self-completing animated progress bar (for indeterminate steps)."""
    from rich.live import Live

    steps = 40
    with Live(console=console, refresh_per_second=45, transient=True) as live:
        for s in range(steps + 1):
            frac = s / steps
            live.update(progress_bar(frac, width=width, label=label, offset=s / steps))
            time.sleep(seconds / steps)
    console.print(progress_bar(1.0, width=width, label=label))


# ---------------------------------------------------------------------------
# Fade-in / slide-in reveals
# ---------------------------------------------------------------------------
def fade_in(console: Console, renderable, steps: int = 6, delay: float = 0.02) -> None:
    """Approximate a fade-in by ramping brightness of a Text renderable."""
    from rich.live import Live

    if not isinstance(renderable, Text):
        console.print(renderable)
        return
    plain = renderable.plain
    theme = themes.current()
    with Live(console=console, refresh_per_second=60, transient=True) as live:
        for s in range(1, steps + 1):
            t = s / steps
            color = blend("#101014", theme.text if theme.text.startswith("#") else "#e0e0e0", t)
            live.update(Text(plain, style=color))
            time.sleep(delay)
    console.print(renderable)


def slide_in(console: Console, text: str, style: str = "", steps: int = 8, delay: float = 0.015) -> None:
    """Slide a line in from the left with decreasing indentation."""
    from rich.live import Live

    with Live(console=console, refresh_per_second=60, transient=True) as live:
        for s in range(steps, -1, -1):
            pad = " " * s
            live.update(Text(pad + text, style=style))
            time.sleep(delay)
    console.print(Text(text, style=style))


# ---------------------------------------------------------------------------
# Celebration confetti
# ---------------------------------------------------------------------------
CONFETTI = ["âœ¦", "âœ§", "âœ©", "âœª", "â‹", "â„", "â—", "â—†", "â–²", "ðŸŽ‰", "âœ¨"]


def confetti(console: Console, frames: int = 16, width: int | None = None) -> None:
    """Burst of falling colored confetti to celebrate task completion."""
    from rich.live import Live

    w = width or min(70, console.size.width)
    palette = stops()
    height = 6
    with Live(console=console, refresh_per_second=30, transient=True) as live:
        for f in range(frames):
            lines = []
            for _ in range(height):
                line = Text()
                for c in range(w):
                    if random.random() < 0.10:
                        color = random.choice(palette)
                        line.append(random.choice(CONFETTI), style=color)
                    else:
                        line.append(" ")
                lines.append(line)
            live.update(Align.center(Group(*lines)))
            time.sleep(0.05)
    console.print(Align.center(heading("ðŸŽ‰  done!  ðŸŽ‰")))


# ---------------------------------------------------------------------------
# Matrix rain (idle / splash effect)
# ---------------------------------------------------------------------------
_MATRIX_CHARS = "ã‚¢ã‚«ã‚µã‚¿ãƒŠãƒãƒžãƒ¤ãƒ©ãƒ¯0123456789ABCDEFï½¦ï½§ï½¨ï½©ï½ª<>=*+-"


def matrix_rain(console: Console, seconds: float = 2.5, fps: int = 24) -> None:
    """Play a Matrix-style digital rain for the given duration."""
    from rich.live import Live

    w = min(90, console.size.width)
    h = min(18, console.size.height - 2)
    palette = ["#00ff41", "#00cf35", "#008f11", "#003b00"]
    drops = [random.randint(-h, 0) for _ in range(w)]
    total = int(seconds * fps)
    with Live(console=console, refresh_per_second=fps, transient=True) as live:
        for _ in range(total):
            grid = [[(" ", "")] * w for _ in range(h)]
            for x in range(w):
                y = drops[x]
                for trail in range(6):
                    yy = y - trail
                    if 0 <= yy < h:
                        color = "#c8ffc8" if trail == 0 else palette[min(trail, len(palette) - 1)]
                        grid[yy][x] = (random.choice(_MATRIX_CHARS), color)
                drops[x] += 1
                if drops[x] - 6 > h and random.random() < 0.5:
                    drops[x] = random.randint(-h, 0)
            lines = []
            for row in grid:
                line = Text()
                for ch, color in row:
                    line.append(ch, style=color)
                lines.append(line)
            live.update(Group(*lines))
            time.sleep(1.0 / fps)
