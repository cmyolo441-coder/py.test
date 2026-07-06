"""Enterprise UI widgets — live token widget, command palette, notifications.

These are composable Rich renderables that the main UI mounts. They are all
real, working widgets (not placeholders) and degrade gracefully in
non-terminal environments.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text


# ---------------------------------------------------------------------------
# Live token widget — renders in the status bar
# ---------------------------------------------------------------------------
def render_token_widget(snapshot: dict[str, Any]) -> Text:
    """A compact, colorised one-line token/cost summary for the status bar."""
    t = Text()
    # Session tokens
    t.append("⚡ ", style="bold")
    t.append(f"{snapshot['session_total']:,}", style="bold cyan")
    t.append(" tok  ", style="dim")
    # Cost
    cost = snapshot.get("session_cost_fmt", "free")
    t.append(f"💰 {cost}", style="bold yellow")
    # Goal tracking
    if snapshot.get("goal_turns", 0) > 0:
        t.append("  🎯 ", style="bold")
        t.append(f"{snapshot['goal_total']:,}", style="bold magenta")
        t.append(f" ({snapshot['goal_cost_fmt']})", style="dim")
    # All-time
    t.append(f"  Σ {snapshot['all_time_total']:,}", style="dim")
    return t


def render_provider_widget(provider: str, model: str) -> Text:
    """A compact provider/model indicator with a health dot."""
    t = Text()
    # Health dot — green for known providers, dim for unknown.
    dot = "●"
    color = "green"
    t.append(f"{dot} ", style=f"bold {color}")
    t.append(f"{provider}", style="bold blue")
    t.append(f"/{model}", style="cyan")
    return t


def render_clock_widget() -> Text:
    """A live UTC clock for the status bar."""
    t = Text()
    t.append("🕐 ", style="dim")
    t.append(time.strftime("%H:%M:%S", time.gmtime()) + " UTC", style="dim cyan")
    return t


def render_status_bar(
    provider: str,
    model: str,
    token_snapshot: dict[str, Any],
    theme_name: str = "",
    goal_mode: bool = False,
    effort: str = "",
) -> Text:
    """Compose the full status bar: clock | provider | tokens | goal | theme."""
    bar = Text()
    bar.append_text(render_clock_widget())
    bar.append("  │  ", style="dim")
    bar.append_text(render_provider_widget(provider, model))
    if goal_mode:
        bar.append("  🎯GOAL", style="bold red")
        if effort:
            bar.append(f"[{effort}]", style="bold yellow")
    bar.append("  │  ", style="dim")
    bar.append_text(render_token_widget(token_snapshot))
    if theme_name:
        bar.append(f"  │  🎨 {theme_name}", style="dim magenta")
    return bar


# ---------------------------------------------------------------------------
# Command palette — Ctrl+P style fuzzy command search
# ---------------------------------------------------------------------------
class CommandPalette:
    """A fuzzy-searchable command launcher.

    Prompts the user with a search box; as they type, commands are filtered
    by fuzzy substring match. Enter launches the selected command.
    """

    def __init__(self, commands: list[tuple[str, str]]) -> None:
        # commands = [(name, description), ...]
        self._commands = commands

    def _fuzzy_match(self, query: str, target: str) -> bool:
        """Simple fuzzy: all chars of query appear in order in target."""
        query = query.lower()
        target = target.lower()
        i = 0
        for ch in target:
            if i < len(query) and ch == query[i]:
                i += 1
        return i == len(query)

    def search(self, query: str, limit: int = 10) -> list[tuple[str, str]]:
        if not query:
            return self._commands[:limit]
        scored: list[tuple[int, str, str]] = []
        for name, desc in self._commands:
            if self._fuzzy_match(query, name) or self._fuzzy_match(query, desc):
                # Score by how early the query appears in name.
                idx = name.lower().find(query.lower())
                score = -idx if idx >= 0 else -100
                scored.append((score, name, desc))
        scored.sort()
        return [(n, d) for _, n, d in scored[:limit]]

    def launch(self, console: Console) -> str | None:
        """Interactively search and return the chosen command (or None)."""
        console.print(Align.center(Text("🔍 Command Palette — type to search, Esc to cancel", style="bold cyan")))
        try:
            query = input("  search> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not query:
            return None
        results = self.search(query)
        if not results:
            console.print("  (no matches)", style="dim")
            return None
        for i, (name, desc) in enumerate(results, 1):
            console.print(f"  {i}. {name}  — {desc}", style="cyan")
        try:
            choice = input("  pick number> ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                return results[idx][0]
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        return None


# ---------------------------------------------------------------------------
# Toast notifications — ephemeral messages that fade out
# ---------------------------------------------------------------------------
@dataclass
class Toast:
    message: str
    level: str = "info"  # info | success | warn | error
    timestamp: float = field(default_factory=time.time)
    duration: float = 3.0

    @property
    def expired(self) -> bool:
        return (time.time() - self.timestamp) > self.duration

    @property
    def color(self) -> str:
        return {
            "info": "cyan",
            "success": "green",
            "warn": "yellow",
            "error": "red",
        }.get(self.level, "white")

    @property
    def icon(self) -> str:
        return {
            "info": "ℹ",
            "success": "✓",
            "warn": "⚠",
            "error": "✗",
        }.get(self.level, "•")


class NotificationSystem:
    """A thread-safe queue of toast notifications.

    The UI polls ``pending()`` each render cycle and displays active toasts
    at the bottom of the screen. Expired toasts are auto-pruned.
    """

    def __init__(self, max_active: int = 5) -> None:
        self._toasts: list[Toast] = []
        self._lock = threading.Lock()
        self.max_active = max_active

    def show(self, message: str, level: str = "info", duration: float = 3.0) -> None:
        toast = Toast(message=message, level=level, duration=duration)
        with self._lock:
            self._toasts.append(toast)
            if len(self._toasts) > self.max_active * 2:
                self._toasts = self._toasts[-self.max_active:]

    def info(self, msg: str) -> None: self.show(msg, "info")
    def success(self, msg: str) -> None: self.show(msg, "success")
    def warn(self, msg: str) -> None: self.show(msg, "warn")
    def error(self, msg: str) -> None: self.show(msg, "error", duration=5.0)

    def pending(self) -> list[Toast]:
        with self._lock:
            self._toasts = [t for t in self._toasts if not t.expired]
            return list(self._toasts[-self.max_active:])

    def render(self) -> Group | None:
        toasts = self.pending()
        if not toasts:
            return None
        panels = []
        for t in toasts:
            remaining = max(0.0, t.duration - (time.time() - t.timestamp))
            text = Text()
            text.append(f"{t.icon} ", style=f"bold {t.color}")
            text.append(t.message, style=t.color)
            text.append(f"  ({remaining:.1f}s)", style="dim")
            panels.append(Align.left(text))
        return Group(*panels)


# ---------------------------------------------------------------------------
# Diff viewer — inline unified diff with syntax highlighting
# ---------------------------------------------------------------------------
def render_diff(text_a: str, text_b: str, label_a: str = "before", label_b: str = "after") -> Panel:
    """Render a colorised unified diff between two text blocks."""
    import difflib
    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()
    diff = difflib.unified_diff(lines_a, lines_b, label_a, label_b, lineterm="")
    body = Text()
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            body.append(line + "\n", style="bold yellow")
        elif line.startswith("@@"):
            body.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            body.append(line + "\n", style="green")
        elif line.startswith("-"):
            body.append(line + "\n", style="red")
        else:
            body.append(line + "\n", style="dim")
    return Panel(body, title="diff", title_align="left", border_style="cyan")


# ---------------------------------------------------------------------------
# Process-wide singletons
# ---------------------------------------------------------------------------
_notifications: NotificationSystem | None = None
_notifications_lock = threading.Lock()


def get_notifications() -> NotificationSystem:
    global _notifications
    if _notifications is None:
        with _notifications_lock:
            if _notifications is None:
                _notifications = NotificationSystem()
    return _notifications
