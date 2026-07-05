"""Timing utilities: a context-manager timer and a simple rate limiter."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Timer:
    """Context manager that measures wall-clock elapsed time.

    Usage:
        with Timer() as t:
            do_work()
        print(t.elapsed)
    """

    label: str = ""
    start: float = 0.0
    end: float = 0.0

    def __enter__(self) -> "Timer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.end = time.perf_counter()

    @property
    def elapsed(self) -> float:
        end = self.end or time.perf_counter()
        return end - self.start

    @property
    def pretty(self) -> str:
        e = self.elapsed
        if e < 1:
            return f"{e * 1000:.0f}ms"
        if e < 60:
            return f"{e:.2f}s"
        return f"{int(e // 60)}m {e % 60:.0f}s"


@dataclass
class RateLimiter:
    """Sliding-window rate limiter (max_calls per period seconds)."""

    max_calls: int = 20
    period: float = 60.0
    _calls: deque = field(default_factory=deque)

    def acquire(self) -> None:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > self.period:
            self._calls.popleft()
        if len(self._calls) >= self.max_calls:
            sleep_for = self.period - (now - self._calls[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        self._calls.append(time.monotonic())
