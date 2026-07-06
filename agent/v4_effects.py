"""v4 TUI enhancements — beautiful animations, live effects, gorgeous prompt box.

Adds:
  * Live goal progress bar (animated, real-time)
  * Particle effects
  * Typing indicator with multiple styles
  * Beautiful boxed prompt with gradient border
  * Live token counter widget (animated)
  * Phase transition animations
  * Celebration effects (success, failure, milestone)
"""
from __future__ import annotations

import math
import random
import time
from typing import Any

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table


def render_goal_progress_bar(
    current_round: int,
    max_rounds: int,
    tokens_used: int,
    cost_usd: float,
    quality_score: int,
    effort: str,
    unlimited: bool = False,
) -> Panel:
    """A beautiful live progress panel for Goal Mode."""
    # Build the progress display.
    round_str = f"{current_round}" if unlimited else f"{current_round}/{max_rounds}"
    # Quality grade.
    grade = "A" if quality_score >= 90 else "B" if quality_score >= 80 else "C" if quality_score >= 70 else "D" if quality_score >= 60 else "F"
    grade_color = "green" if quality_score >= 80 else "yellow" if quality_score >= 60 else "red"

    table = Table(show_header=False, show_lines=False, box=None, padding=(0, 2))
    table.add_column("label", style="bold cyan", width=12)
    table.add_column("value", style="white")
    table.add_row("🎯 Round", round_str)
    table.add_row("📊 Tokens", f"{tokens_used:,}")
    table.add_row("💰 Cost", f"${cost_usd:.4f}")
    table.add_row("📈 Quality", f"{quality_score}/100 [{grade}]", style=grade_color)
    table.add_row("⚡ Effort", effort)
    table.add_row("♾️  Mode", "UNLIMITED" if unlimited else "bounded")

    return Panel(
        table,
        title="[bold magenta]🎯 Goal Mode Live[/]",
        title_align="center",
        border_style="bold magenta",
        padding=(1, 2),
    )


def render_animated_prompt_box(console: Console, text: str = "") -> None:
    """Render a beautiful glowing prompt box."""
    from . import themes
    theme = themes.current()
    # Gradient border using theme colors.
    border = f"bold {theme.accent}"
    panel = Panel(
        Text(text or "Type your message…", style=theme.text),
        title=f"[{theme.accent2}]you ❯[{theme.accent2}]",
        title_align="left",
        border_style=border,
        padding=(0, 1),
    )
    console.print(panel)


def particle_burst(console: Console, color: str = "cyan", count: int = 30, frames: int = 15) -> None:
    """Burst of particles from center (celebration effect)."""
    width = min(60, console.size.width)
    height = 10
    cx, cy = width // 2, height // 2
    particles = [
        {"x": cx, "y": cy, "vx": random.uniform(-2, 2), "vy": random.uniform(-2, 2), "life": frames}
        for _ in range(count)
    ]
    with Live(console=console, refresh_per_second=30, transient=True) as live:
        for frame in range(frames):
            grid = [[(" ", "")] * width for _ in range(height)]
            for p in particles:
                p["x"] += p["vx"]
                p["y"] += p["vy"]
                p["vy"] += 0.1  # gravity
                p["life"] -= 1
                x, y = int(p["x"]), int(p["y"])
                if 0 <= x < width and 0 <= y < height and p["life"] > 0:
                    grid[y][x] = ("●", color)
            lines = []
            for row in grid:
                line = Text()
                for ch, c in row:
                    line.append(ch, style=c)
                lines.append(line)
            live.update(Align.center(Group(*lines)))
            time.sleep(0.033)


def success_celebration(console: Console, message: str = "Success!") -> None:
    """Green success burst with checkmark."""
    from . import effects
    particle_burst(console, color="green", count=40, frames=20)
    console.print(Align.center(effects.gradient_text(f"✓ {message} ✓", offset=0.0)))


def failure_effect(console: Console, message: str = "Failed") -> None:
    """Red failure shake effect."""
    from . import effects
    text = effects.gradient_text(f"✗ {message} ✗")
    with Live(console=console, refresh_per_second=20, transient=True) as live:
        for i in range(10):
            offset = math.sin(i * 0.5) * 3
            live.update(Align.center(Text(" " * int(offset)) + text))
            time.sleep(0.05)
    console.print(Align.center(text))


def milestone_celebration(console: Console, milestone: str) -> None:
    """Golden milestone celebration."""
    from . import effects
    particle_burst(console, color="yellow", count=50, frames=25)
    text = effects.gradient_text(f"🏆 MILESTONE: {milestone} 🏆")
    console.print(Align.center(text))


def phase_transition(console: Console, from_phase: str, to_phase: str) -> None:
    """Animated transition between phases (plan → execute → verify)."""
    from . import effects, themes
    theme = themes.current()
    with Live(console=console, refresh_per_second=20, transient=True) as live:
        for i in range(15):
            # Slide out old, slide in new.
            progress = i / 14
            old_alpha = 1.0 - progress
            new_alpha = progress
            text = Text()
            if old_alpha > 0.2:
                text.append(f"  {from_phase}  ", style=f"dim {theme.dim}")
            text.append(" → ", style=theme.accent2)
            if new_alpha > 0.2:
                text.append(f"  {to_phase}  ", style=f"bold {theme.accent}")
            live.update(Align.center(text))
            time.sleep(0.04)
    console.print(Align.center(effects.gradient_text(f"▶ {to_phase}")))


def live_token_widget_animated(console: Console, snapshot: dict[str, Any], duration: float = 2.0) -> None:
    """Animate the token counter going up (for visual feedback)."""
    from . import themes
    themes.current()
    start_total = snapshot.get("session_total", 0)
    # Simulate counting up (in practice this would show real-time updates).
    with Live(console=console, refresh_per_second=30, transient=True) as live:
        steps = 30
        for i in range(steps + 1):
            t = i / steps
            display = int(start_total * t)
            text = Text()
            text.append("⚡ ", style="bold")
            text.append(f"{display:,}", style="bold cyan")
            text.append(" tokens  ", style="dim")
            text.append(f"💰 {snapshot.get('session_cost_fmt', 'free')}", style="bold yellow")
            live.update(Align.center(text))
            time.sleep(duration / steps)


def spinner_with_label(console: Console, label: str, spinner_name: str = "dots", duration: float = 2.0) -> None:
    """Show an animated spinner with a label."""
    from rich.spinner import Spinner
    from . import themes
    theme = themes.current()
    spinner = Spinner(spinner_name, text=Text(f" {label}…", style=f"bold {theme.accent2}"))
    with Live(console=console, refresh_per_second=12, transient=True) as live:
        end = time.time() + duration
        while time.time() < end:
            live.update(spinner)
            time.sleep(0.08)


def dashboard_panel(console: Console, sections: dict[str, str]) -> None:
    """Render a multi-section dashboard panel."""
    from . import themes
    theme = themes.current()
    body = Text()
    for i, (title, content) in enumerate(sections.items()):
        if i > 0:
            body.append("\n")
        body.append(f"  {title}\n", style=f"bold {theme.accent}")
        body.append(f"  {content}\n", style=theme.text)
    panel = Panel(
        body,
        title=f"[bold {theme.accent2}]📊 Dashboard[/]",
        title_align="center",
        border_style=theme.accent,
        padding=(1, 2),
    )
    console.print(panel)


def typing_indicator(console: Console, text: str = "thinking", style: str = "cyan", duration: float = 1.0) -> None:
    """Animated typing dots."""
    from . import themes
    theme = themes.current()
    dots = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    with Live(console=console, refresh_per_second=12, transient=True) as live:
        end = time.time() + duration
        i = 0
        while time.time() < end:
            glyph = dots[i % len(dots)]
            t = Text()
            t.append(f" {glyph} ", style=f"bold {style}")
            t.append(f"{text}…", style=theme.text)
            live.update(t)
            time.sleep(0.08)
            i += 1


def glow_text(text: str, color: str = "cyan", frames: int = 20) -> Text:
    """Create a glowing text effect (pulsing brightness)."""
    result = Text()
    for i, ch in enumerate(text):
        brightness = 0.5 + 0.5 * math.sin(i * 0.3)
        if brightness > 0.7:
            result.append(ch, style=f"bold bright_{color}")
        elif brightness > 0.4:
            result.append(ch, style=f"bold {color}")
        else:
            result.append(ch, style=f"dim {color}")
    return result
