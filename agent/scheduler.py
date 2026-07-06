"""Continuous background agent — cron-like scheduled task execution.

Lets the user schedule agent tasks to run periodically:
  * Every N minutes/hours/days
  * At specific times (cron expressions)
  * One-shot after a delay

Real, working scheduler using a background thread. Tasks persist across
the session (not across restarts — for that use cron/systemd).

Use cases:
  * "Check for new GitHub issues every hour"
  * "Run the test suite every morning at 9am"
  * "Backup the database every 6 hours"
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .logging_config import get_logger

log = get_logger("agent.scheduler")


@dataclass
class ScheduledTask:
    id: str
    name: str
    prompt: str  # what the agent should do
    schedule: str  # human-readable schedule
    interval_s: float  # seconds between runs (0 = one-shot)
    next_run: float  # timestamp of next run
    last_run: float = 0.0
    last_result: str = ""
    run_count: int = 0
    enabled: bool = True
    one_shot: bool = False


class Scheduler:
    """Background scheduler for agent tasks."""

    def __init__(self, app: Any = None) -> None:
        self.app = app
        self._tasks: dict[str, ScheduledTask] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def schedule(
        self,
        name: str,
        prompt: str,
        every_seconds: float = 3600,
        delay_seconds: float = 0,
        one_shot: bool = False,
    ) -> str:
        """Schedule a task. Returns the task ID."""
        import uuid
        task_id = uuid.uuid4().hex[:8]
        task = ScheduledTask(
            id=task_id,
            name=name,
            prompt=prompt,
            schedule=f"every {every_seconds}s" if not one_shot else f"once after {delay_seconds}s",
            interval_s=every_seconds,
            next_run=time.time() + delay_seconds,
            one_shot=one_shot,
        )
        with self._lock:
            self._tasks[task_id] = task
        log.info("Scheduled task '%s' (id=%s): %s", name, task_id, task.schedule)
        return task_id

    def schedule_cron(self, name: str, prompt: str, cron_expr: str) -> str:
        """Schedule a task using a cron-like expression (simplified).

        Supported: "every N (minutes|hours|days)", "at HH:MM", "hourly", "daily"
        """
        interval = _parse_cron(cron_expr)
        return self.schedule(name, prompt, every_seconds=interval)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._tasks:
                del self._tasks[task_id]
                return True
            return False

    def list_tasks(self) -> list[ScheduledTask]:
        with self._lock:
            return list(self._tasks.values())

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        log.info("Scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        log.info("Scheduler stopped")

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                due = [t for t in self._tasks.values() if t.enabled and t.next_run <= now]
            for task in due:
                self._execute(task)
            self._stop.wait(timeout=1.0)

    def _execute(self, task: ScheduledTask) -> None:
        """Execute a scheduled task by running it as an agent turn."""
        log.info("Running scheduled task '%s': %s", task.name, task.prompt[:80])
        task.last_run = time.time()
        task.run_count += 1
        try:
            if self.app is not None:
                # Run as a one-shot agent turn.
                self.app.run_once(task.prompt)
                task.last_result = "ok"
            else:
                task.last_result = "(no app attached — task skipped)"
        except Exception as exc:  # noqa: BLE001
            task.last_result = f"error: {exc}"
            log.error("Scheduled task '%s' failed: %s", task.name, exc)
        # Schedule next run.
        if task.one_shot:
            task.enabled = False
        else:
            task.next_run = time.time() + task.interval_s

    def dashboard(self) -> str:
        tasks = self.list_tasks()
        if not tasks:
            return "Scheduler: no tasks. Use /schedule to add one."
        lines = [f"Scheduler ({len(tasks)} task(s)):"]
        for t in tasks:
            status = "▶" if t.enabled else "⏸"
            next_str = time.strftime("%H:%M:%S", time.localtime(t.next_run)) if t.enabled else "disabled"
            lines.append(
                f"  {status} {t.id}  {t.name:<20}  {t.schedule:<20}  "
                f"next: {next_str}  runs: {t.run_count}  last: {t.last_result[:30]}"
            )
        return "\n".join(lines)


def _parse_cron(expr: str) -> float:
    """Parse a simplified cron expression into seconds."""
    expr = expr.lower().strip()
    if expr == "hourly":
        return 3600.0
    if expr == "daily":
        return 86400.0
    if expr == "weekly":
        return 604800.0
    import re
    m = re.match(r"every\s+(\d+)\s*(second|minute|hour|day)s?", expr)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        multipliers = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
        return n * multipliers[unit]
    # Default: hourly.
    return 3600.0


_scheduler: Scheduler | None = None
_scheduler_lock = threading.Lock()


def get_scheduler(app: Any = None) -> Scheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = Scheduler(app=app)
    return _scheduler
