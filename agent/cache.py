"""Caching utilities: an in-memory TTL LRU cache and a decorator.

Caching identical prompts avoids redundant, costly API calls.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable


class TTLCache:
    """Thread-safe LRU cache with per-entry time-to-live."""

    def __init__(self, maxsize: int = 256, ttl: float = 300.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _expired(self, ts: float) -> bool:
        return (time.monotonic() - ts) > self.ttl

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                self.misses += 1
                return None
            ts, value = item
            if self._expired(ts):
                del self._store[key]
                self.misses += 1
                return None
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.monotonic(), value)
            self._store.move_to_end(key)
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


def make_key(*args: Any, **kwargs: Any) -> str:
    raw = json.dumps([args, kwargs], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached(cache: TTLCache) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = make_key(func.__name__, *args, **kwargs)
            hit = cache.get(key)
            if hit is not None:
                return hit
            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        return wrapper

    return decorator
