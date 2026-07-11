"""Progress tracker — show progress during long operations.

Inspired by gsd-pi's progress indicators, this module provides:
  - Rich progress bars for multi-agent operations
  - Real-time status updates
  - ETA calculation
  - Spinner animations

Uses Rich library for beautiful terminal output.
"""
from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text


@dataclass
class TaskProgress:
    """Progress tracking for a single task."""
    name: str
    status: str = "pending"  # pending | running | completed | failed
    start_time: float | None = None
    end_time: float | None = None
    result: str | None = None
    error: str | None = None

    @property
    def duration_s(self) -> float | None:
        if self.start_time is None:
            return None
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def duration_str(self) -> str:
        d = self.duration_s
        if d is None:
            return "-"
        if d < 60:
            return f"{d:.1f}s"
        return f"{int(d / 60)}m {int(d % 60)}s"


@dataclass
class ProgressState:
    """Overall progress state."""
    title: str
    tasks: list[TaskProgress] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "completed")

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "failed")

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def progress_str(self) -> str:
        return f"{self.completed_count}/{self.total_count}"

    @property
    def elapsed_str(self) -> str:
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        return f"{int(elapsed / 60)}m {int(elapsed % 60)}s"

    @property
    def eta_str(self) -> str:
        if self.completed_count == 0:
            return "calculating..."

        elapsed = time.time() - self.start_time
        avg_per_task = elapsed / self.completed_count
        remaining = self.total_count - self.completed_count
        eta = avg_per_task * remaining

        if eta < 60:
            return f"~{eta:.0f}s"
        return f"~{int(eta / 60)}m"


class ProgressTracker:
    """Track and display progress for multi-step operations."""

    def __init__(self, console: Console | None = None, use_live: bool = True):
        self.console = console or Console()
        self.use_live = use_live
        self._live: Live | None = None
        self._state: ProgressState | None = None

    def _make_table(self) -> Table:
        """Build a Rich table showing current progress."""
        if self._state is None:
            return Table()

        table = Table(
            title=self._state.title,
            show_header=True,
            header_style="bold",
            border_style="dim",
        )

        table.add_column("Task", style="cyan", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Duration", justify="right")
        table.add_column("Result", max_width=40)

        for task in self._state.tasks:
            # Status with icon
            if task.status == "pending":
                status = Text("⏳ pending", style="dim")
            elif task.status == "running":
                status = Text("🔄 running", style="yellow")
            elif task.status == "completed":
                status = Text("✅ done", style="green")
            elif task.status == "failed":
                status = Text("❌ failed", style="red")
            else:
                status = Text(task.status)

            # Result preview
            result = ""
            if task.result:
                result = task.result[:40] + ("..." if len(task.result) > 40 else "")
            elif task.error:
                result = Text(task.error[:40], style="red")

            table.add_row(
                task.name,
                status,
                task.duration_str,
                result,
            )

        # Add summary row
        table.add_section()
        summary = Text()
        summary.append(f"Progress: {self._state.progress_str}", style="bold")
        summary.append(f" | Elapsed: {self._state.elapsed_str}")
        summary.append(f" | ETA: {self._state.eta_str}")
        table.add_row("", summary, "", "")

        return table

    def _update_display(self) -> None:
        """Update the live display."""
        if self._live is not None and self._state is not None:
            self._live.update(self._make_table())

    def start(self, title: str, tasks: list[str]) -> None:
        """Start tracking progress.

        Args:
            title: Title for the progress display.
            tasks: List of task names to track.
        """
        self._state = ProgressState(
            title=title,
            tasks=[TaskProgress(name=name) for name in tasks],
        )

        if self.use_live:
            self._live = Live(
                self._make_table(),
                console=self.console,
                refresh_per_second=4,
            )
            self._live.start()
        else:
            # Just print the initial state
            self.console.print(self._make_table())

    def task_start(self, task_name: str) -> None:
        """Mark a task as started."""
        if self._state is None:
            return

        for task in self._state.tasks:
            if task.name == task_name and task.status == "pending":
                task.status = "running"
                task.start_time = time.time()
                break

        self._update_display()

    def task_complete(self, task_name: str, result: str = "") -> None:
        """Mark a task as completed."""
        if self._state is None:
            return

        for task in self._state.tasks:
            if task.name == task_name and task.status == "running":
                task.status = "completed"
                task.end_time = time.time()
                task.result = result
                break

        self._update_display()

    def task_fail(self, task_name: str, error: str) -> None:
        """Mark a task as failed."""
        if self._state is None:
            return

        for task in self._state.tasks:
            if task.name == task_name and task.status == "running":
                task.status = "failed"
                task.end_time = time.time()
                task.error = error
                break

        self._update_display()

    def finish(self) -> ProgressState | None:
        """Finish tracking and return final state."""
        if self._live is not None:
            self._live.stop()
            self._live = None

        state = self._state

        # Print final state
        if state is not None:
            self.console.print(self._make_table())

        self._state = None
        return state

    @contextmanager
    def track(
        self,
        title: str,
        tasks: list[str],
    ) -> Generator[ProgressTracker, None, None]:
        """Context manager for tracking progress.

        Usage:
            with tracker.track("Processing", ["task1", "task2"]) as t:
                t.task_start("task1")
                # ... do work ...
                t.task_complete("task1", "done")
        """
        self.start(title, tasks)
        try:
            yield self
        finally:
            self.finish()


def format_multi_agent_progress(
    title: str,
    specialists: list[str],
    results: dict[str, Any],
) -> Table:
    """Format multi-agent progress as a Rich table.

    Args:
        title: Title for the table.
        specialists: List of specialist names.
        results: Dict of specialist -> result info.

    Returns:
        Rich Table with the progress.
    """
    table = Table(
        title=title,
        show_header=True,
        header_style="bold",
        border_style="dim",
    )

    table.add_column("Specialist", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Output Preview", max_width=50)

    for specialist in specialists:
        info = results.get(specialist, {})

        if info.get("success"):
            status = Text("✅ done", style="green")
        elif info.get("error"):
            status = Text("❌ failed", style="red")
        else:
            status = Text("⏳ pending", style="dim")

        duration = info.get("duration_s", 0)
        if duration < 60:
            duration_str = f"{duration:.1f}s"
        else:
            duration_str = f"{int(duration / 60)}m {int(duration % 60)}s"

        output = info.get("output", "")[:50]
        if len(info.get("output", "")) > 50:
            output += "..."

        table.add_row(
            specialist,
            status,
            duration_str if duration > 0 else "-",
            output,
        )

    return table
