"""fx — pure visual-effects layer for the TUI (v4 "aurora" polish).

Everything here is additive chrome: a function that fails or a terminal
that can't render it must never take a session down. Callers wrap uses
in try/except and fall back to plain output.

Effects:
- gradient_text      per-character truecolor gradient (rainbow / custom)
- spinner_color      color-cycling spinner so the busy glyph shimmers
- aurora_color       seamless looping gradient (flows when phase advances)
- gradient_fragments prompt_toolkit fragments with a per-char gradient
- boot_reveal        line-by-line reveal of the welcome card (typewriter)
- shimmer_flash      one-shot "✦" flash with a rising gradient

Self-test: python -m fullagent.fx
"""

from __future__ import annotations

import time
from typing import Callable

# Rainbow used for gradients (truecolor, degrades via rich's color system
# to the nearest 256/16 color on limited terminals).
RAINBOW = ["#ff5f6d", "#ff7b54", "#ffb26b", "#ffd56b", "#7bed9f",
           "#4ecdc4", "#45aaf2", "#a55eea"]

# Spinner glyph colors, cycled by frame index — the busy spinner breathes
# through the palette instead of sitting on one flat color.
SPINNER_COLORS = ["#45aaf2", "#4ecdc4", "#7bed9f", "#ffd56b",
                  "#ff7b54", "#a55eea"]


def _hex(c: str) -> str:
    """Truecolor hex passthrough; rich accepts these directly."""
    return c


def gradient_text(text: str, palette: list[str] | None = None) -> object:
    """Return a rich Text with a per-character horizontal gradient.

    Uses a lazy import so headless modules that import fx never require
    rich (the TUI always has it).
    """
    from rich.text import Text

    pal = palette or RAINBOW
    t = Text()
    n = max(1, len(text))
    for i, ch in enumerate(text):
        t.append(ch, style=_hex(pal[int(i * (len(pal) - 1) / max(1, n - 1))]))
    return t


# Aurora palette for the prompt-box border and brand marks (truecolor).
AURORA = ["#c084fc", "#818cf8", "#22d3ee", "#34d399", "#f472b6"]


def lerp_hex(a: str, b: str, t: float) -> str:
    """Linear blend of two #rrggbb colors; bad input returns ``a``."""
    try:
        t = max(0.0, min(1.0, float(t)))
        ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
        br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
        return "#%02x%02x%02x" % (round(ar + (br - ar) * t),
                                  round(ag + (bg - ag) * t),
                                  round(ab + (bb - ab) * t))
    except Exception:
        return a


def aurora_color(pos: float, phase: float = 0.0,
                 palette: list[str] | None = None) -> str:
    """Color at ``pos`` (0..1) along a SEAMLESS looping gradient.

    ``phase`` shifts the loop, so advancing it every frame makes the
    gradient flow. The palette wraps (last blends back into first), so
    there is never a hard seam.
    """
    pal = palette or AURORA
    n = len(pal)
    if n == 0:
        return "#ffffff"
    if n == 1:
        return pal[0]
    x = ((float(pos) + float(phase)) % 1.0) * n
    i = int(x) % n
    return lerp_hex(pal[i], pal[(i + 1) % n], x - int(x))


def gradient_fragments(text: str, palette: list[str] | None = None,
                       phase: float = 0.0, extra: str = "bold",
                       span: int | None = None, offset: int = 0) -> list:
    """prompt_toolkit ``(style, text)`` fragments with a per-character
    aurora gradient. ``span``/``offset`` place the text inside a wider
    gradient (so several pieces on one row share a single sweep)."""
    total = max(1, span if span else len(text))
    out = []
    for i, ch in enumerate(text):
        color = aurora_color((offset + i) / total, phase, palette)
        out.append((f"{extra} {color}".strip(), ch))
    return out


def spinner_color(i: int) -> str:
    """Color for spinner frame index i (cycles through the palette)."""
    if not SPINNER_COLORS:
        return "cyan"
    return SPINNER_COLORS[i % len(SPINNER_COLORS)]


def boot_reveal(console, render: Callable[[], object],
                delay: float = 0.028) -> None:
    """Reveal a rendered block line-by-line (Devin/Qoder-style boot).

    `render()` returns a rich renderable (already built once, so layout
    never changes mid-reveal); we capture its lines via the console
    export and print them back one at a time. Falls back to a single
    plain print on any failure.
    """
    try:
        from rich.text import Text

        with console.capture() as cap:
            console.print(render())
        lines = cap.get().splitlines()
        for line in lines:
            # Colors are enabled for the real TUI, so capture returns ANSI
            # sequences. Printing that raw string would make Rich escape the
            # codes (and lose the gradient); parse them back into a Text so
            # the reveal preserves every truecolor span.
            console.print(Text.from_ansi(line))
            if delay > 0:
                time.sleep(delay)
    except Exception:
        try:
            console.print(render())
        except Exception:
            pass


def shimmer_flash(console, text: str, steps: int = 8,
                  delay: float = 0.035) -> None:
    """Print `text` with a one-shot brightness shimmer (rising edge)."""
    try:
        from rich.text import Text

        for k in range(max(1, steps)):
            t = Text()
            color = _hex(SPINNER_COLORS[k % len(SPINNER_COLORS)])
            t.append(text, style=f"bold {color}")
            console.print(t, end="\r", soft_wrap=False)
            time.sleep(delay)
        t = Text()
        t.append(text, style=f"bold {_hex(RAINBOW[-1])}")
        console.print(t)
    except Exception:
        console.print(text)


# ── self-test ────────────────────────────────────────────────────────────
def _self_test() -> int:
    from rich.console import Console
    import io

    console = Console(file=io.StringIO(), force_terminal=False, width=60)

    t = gradient_text("FULLAGENT")
    assert len(str(t)) == len("FULLAGENT"), "gradient length mismatch"

    assert spinner_color(0) == SPINNER_COLORS[0]
    assert spinner_color(len(SPINNER_COLORS)) == SPINNER_COLORS[0], \
        "spinner colors must cycle"

    # boot_reveal prints every line and never raises
    boot_reveal(console, lambda: "hello", delay=0)
    out = console.file.getvalue()
    assert "hello" in out, "boot_reveal lost content"

    # broken renderer must not raise
    def _boom():
        raise RuntimeError("boom")
    boot_reveal(console, _boom, delay=0)

    # bad spinner index wraps
    assert spinner_color(999) in SPINNER_COLORS

    print("FX SELF-TEST PASS (gradient, spinner cycle, reveal, fallbacks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
