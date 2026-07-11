"""Token-bucket rate limiter.

Protects downstream APIs (and your budget) by capping request throughput.
Thread-safe and dependency-free.
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    """Classic token-bucket rate limiter."""

    def __init__(self, rate: float, capacity: int) -> None:
        """``rate`` tokens are added per second, up to ``capacity``."""
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    def try_acquire(self, tokens: int = 1) -> bool:
        """Return True if ``tokens`` were available and consumed."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """Block until tokens are available or timeout elapses."""
        deadline = time.monotonic() + timeout
        while True:
            if self.try_acquire(tokens):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens
