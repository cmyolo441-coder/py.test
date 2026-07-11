"""A priority task queue with worker threads."""
from __future__ import annotations

import heapq
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(order=True)
class Task:
    priority: int
    created: float = field(compare=True)
    func: Callable[..., Any] = field(compare=False)
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)


class TaskQueue:
    """Thread-safe priority queue with a pool of workers."""

    def __init__(self, workers: int = 2) -> None:
        self._heap: list[Task] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._running = False
        self._threads: list[threading.Thread] = []
        self._worker_count = workers
        self.completed = 0

    def submit(self, func: Callable[..., Any], *args, priority: int = 5, **kwargs) -> None:
        with self._cv:
            heapq.heappush(self._heap, Task(priority, time.time(), func, args, kwargs))
            self._cv.notify()

    def _worker(self) -> None:
        while self._running:
            with self._cv:
                while self._running and not self._heap:
                    self._cv.wait(timeout=0.5)
                if not self._running:
                    return
                task = heapq.heappop(self._heap) if self._heap else None
            if task is not None:
                try:
                    task.func(*task.args, **task.kwargs)
                finally:
                    with self._lock:
                        self.completed += 1

    def start(self) -> None:
        self._running = True
        for _ in range(self._worker_count):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify_all()
        for t in self._threads:
            t.join(timeout=1)
        self._threads.clear()

    def pending(self) -> int:
        with self._lock:
            return len(self._heap)
