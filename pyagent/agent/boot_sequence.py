"""Animated boot sequence — cinematic startup experience.

Plays a multi-stage boot animation when the agent starts:
  1. Fade-in logo with gradient
  2. Subsystem init lines (each ticks off as it loads)
  3. Provider health check
  4. "Ready" banner

Skipped automatically when --no-anim is passed or stdin isn't a TTY.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from rich.align import Align
from rich.console import Console
from rich.text import Text

from . import effects, themes

BOOT_STAGES: list[tuple[str, str]] = [
    ("Initialising core", "agent.core"),
    ("Loading tool registry", "agent.tools"),
    ("Wiring command framework", "agent.commands"),
    ("Connecting provider", "agent.providers"),
    ("Starting token counter", "agent.token_counter"),
    ("Restoring session state", "agent.recovery"),
    ("Booting UI subsystems", "agent.ui"),
    ("Ready", ""),
]


def play_boot_sequence(
    console: Console,
    provider: str,
    model: str,
    on_stage: Callable[[int, str], None] | None = None,
    fast: bool = False,
) -> None:
    """Play the animated boot sequence.

    ``on_stage`` is called with (stage_index, stage_name) as each completes,
    so the caller can do real init work between animation frames.
    """
    theme = themes.current()
    delay = 0.0 if fast else 0.15

    # 1. Minimal header — a single gradient title line, no giant banner.
    console.print()
    title = effects.gradient_text("terminal-agent")
    console.print(Align.center(title))

    # 2. Subtitle: provider/model on a dim line.
    subtitle = f"{provider}/{model}"
    console.print(Align.center(Text(subtitle, style=theme.dim)))
    console.print()

    # 3. Subsystem init lines with check marks.
    for i, (label, _module) in enumerate(BOOT_STAGES):
        if on_stage is not None:
            on_stage(i, label)
        # Animated "loading -> done" transition.
        text = Text()
        text.append("  [", style="dim")
        spinner_frame = effects.SPINNERS["braille"][i % len(effects.SPINNERS["braille"])]
        if not fast:
            text.append(spinner_frame, style=f"bold {theme.accent2}")
        text.append("] ", style="dim")
        text.append(label, style=theme.text)
        if not fast:
            # Show "loading" briefly then overwrite with "done". Clear the line
            # first (\r + blank pad + \r) so a wide glyph can never leave a tail.
            console.print(text, end="\r")
            time.sleep(delay)
            console.print(" " * (console.size.width - 1), end="\r")
            done_text = Text()
            done_text.append("  [", style="dim")
            done_text.append("✓", style="bold green")
            done_text.append("] ", style="dim")
            done_text.append(label, style=theme.text)
            console.print(done_text)
        else:
            text.append("  ✓", style="green")
            console.print(text)

    console.print()

    # 4. Double-line ready box (matches Codex-CLI style).
    width = min(72, console.size.width)
    hline = "═" * (width - 2)
    ready_text = "ready"
    hints_text = "/help for commands \u00b7 Enter to send \u00b7 /exit to quit"
    console.print(f"[bold {theme.accent}]╔{hline}╗[/]")
    console.print(f"[bold {theme.accent}]║[/][bold {theme.accent2}]{ready_text.center(width - 4)}[/][bold {theme.accent}]║[/]")
    console.print(f"[bold {theme.accent}]║[/][dim]{hints_text.center(width - 4)}[/][bold {theme.accent}]║[/]")
    console.print(f"[bold {theme.accent}]╚{hline}╝[/]")
    console.print()


def show_goodbye(console: Console) -> None:
    """Play a short farewell animation on exit."""
    themes.current()
    text = effects.gradient_text("Goodbye! 👋")
    console.print(Align.center(text))
