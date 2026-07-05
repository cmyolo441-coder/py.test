"""In-process metrics collection (counters, gauges, histograms).

A tiny, dependency-free metrics registry suitable for exporting to logs or a
Prominateus-style scrape endpoint.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Histogram:
    count: int = 0
    total: float = 0.0
    min: float = float("inf")
    max: float = float("-inf")

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.min = min(self.min, value)
        self.max = max(self.max, value)

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0.0


class MetricsRegistry:
    """Thread-safe registry of counters, gauges and histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, Histogram] = defaultdict(Histogram)

    def incr(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += amount

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._histograms[name].observe(value)

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    k: {"count": h.count, "avg": h.avg, "min": h.min, "max": h.max}
                    for k, h in self._histograms.items()
                },
            }


class Timer:
    """Context manager that records elapsed time into a histogram."""

    def __init__(self, registry: MetricsRegistry, name: str) -> None:
        self.registry = registry
        self.name = name
        self._start = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.registry.observe(self.name, time.perf_counter() - self._start)


# Global default registry.
metrics = MetricsRegistry()
