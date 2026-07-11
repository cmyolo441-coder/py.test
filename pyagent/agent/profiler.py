"""Performance profiler — measures where time is spent in a turn.

Wraps key agent operations (provider call, tool exec, streaming, context
prep) in a profiler that records durations. The ``/profile`` command shows
the breakdown so users can see why a turn was slow.

Zero-overhead when disabled (the default). Enable with ``/profile on``.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class ProfileSpan:
    name: str
    start: float
    end: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end - self.start


class Profiler:
    """Lightweight timing profiler. Thread-safe, opt-in."""

    def __init__(self) -> None:
        self.enabled = False
        self._lock = threading.Lock()
        self._current_turn: list[ProfileSpan] = []
        self._turn_history: list[list[ProfileSpan]] = []
        self._totals: dict[str, float] = defaultdict(float)
        self._counts: dict[str, int] = defaultdict(int)

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        return self.enabled

    def start_turn(self) -> None:
        with self._lock:
            self._current_turn = []

    def end_turn(self) -> None:
        with self._lock:
            if self._current_turn:
                self._turn_history.append(self._current_turn)
                if len(self._turn_history) > 50:
                    self._turn_history = self._turn_history[-25:]
                for span in self._current_turn:
                    self._totals[span.name] += span.duration_s
                    self._counts[span.name] += 1
            self._current_turn = []

    def span(self, name: str, **metadata: Any) -> ProfileSpan:
        """Context-manager-style span: ``with profiler.span('llm_call'): ...``"""
        span = ProfileSpan(name=name, start=time.perf_counter(), metadata=metadata)
        return span

    def record(self, name: str, duration_s: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._totals[name] += duration_s
            self._counts[name] += 1

    def time(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator that profiles a function."""
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args: Any, **kwargs: Any) -> T:
                if not self.enabled:
                    return func(*args, **kwargs)
                start = time.perf_counter()
                try:
                    return func(*args, **kwargs)
                finally:
                    self.record(name, time.perf_counter() - start)
            return wrapper
        return decorator

    def last_turn_summary(self) -> str:
        with self._lock:
            if not self._turn_history:
                return "No profiled turns yet. Enable with /profile on."
            spans = self._turn_history[-1]
        if not spans:
            return "(empty turn)"
        total = sum(s.duration_s for s in spans)
        lines = [f"Last turn profile (total {total:.3f}s):"]
        for s in sorted(spans, key=lambda x: -x.duration_s):
            pct = (s.duration_s / total * 100) if total else 0
            bar_len = int(pct / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"  {s.name:<20} {s.duration_s:>7.3f}s  [{bar}] {pct:5.1f}%")
        return "\n".join(lines)

    def aggregate_summary(self) -> str:
        with self._lock:
            if not self._totals:
                return "No profiled operations yet."
            lines = ["Aggregate profile (all turns):"]
            for name in sorted(self._totals, key=lambda x: -self._totals[x]):
                total = self._totals[name]
                count = self._counts[name]
                avg = total / count if count else 0
                lines.append(f"  {name:<20} total={total:>7.3f}s  count={count:>4}  avg={avg:.3f}s")
        return "\n".join(lines)


_profiler: Profiler | None = None
_lock = threading.Lock()


def get_profiler() -> Profiler:
    global _profiler
    if _profiler is None:
        with _lock:
            if _profiler is None:
                _profiler = Profiler()
    return _profiler
