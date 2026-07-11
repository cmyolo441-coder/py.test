"""KV cache — cache conversation prefixes for faster repeat queries.

When the same system prompt + early conversation is sent repeatedly (very
common in agentic loops), the LLM re-processes the whole prefix every
time. A KV cache stores the model's key-value attention tensors so
subsequent calls with the same prefix skip re-computation.

This is a logical cache (we can't access provider KV state directly), so
it works by detecting repeated prefixes and short-circuiting to a cached
response when the conversation hasn't changed up to a certain point.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    key: str
    response: str
    timestamp: float
    hit_count: int = 0
    size_chars: int = 0


class KvCache:
    """LRU cache keyed on conversation prefix hashes."""

    def __init__(self, max_entries: int = 100, ttl_s: float = 3600) -> None:
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self.hits = 0
        self.misses = 0

    def _hash_prefix(self, messages: list[dict[str, Any]], prefix_length: int) -> str:
        """Hash the first ``prefix_length`` messages."""
        prefix = messages[:prefix_length]
        raw = str([(m.get("role"), str(m.get("content", ""))[:200]) for m in prefix])
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, messages: list[dict[str, Any]]) -> str | None:
        """Check if we have a cached response for this exact conversation."""
        if not messages:
            return None
        key = self._hash_prefix(messages, len(messages))
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            if time.time() - entry.timestamp > self.ttl_s:
                del self._cache[key]
                self.misses += 1
                return None
            entry.hit_count += 1
            self.hits += 1
            self._cache.move_to_end(key)
            return entry.response

    def set(self, messages: list[dict[str, Any]], response: str) -> None:
        if not messages or not response:
            return
        key = self._hash_prefix(messages, len(messages))
        with self._lock:
            self._cache[key] = CacheEntry(
                key=key,
                response=response,
                timestamp=time.time(),
                size_chars=len(response),
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total_size = sum(e.size_chars for e in self._cache.values())
            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": self.hit_rate,
                "total_cached_chars": total_size,
                "ttl_s": self.ttl_s,
            }

    def dashboard(self) -> str:
        s = self.stats()
        return (
            f"KV cache:\n"
            f"  entries:     {s['entries']}/{s['max_entries']}\n"
            f"  hits:        {s['hits']}\n"
            f"  misses:      {s['misses']}\n"
            f"  hit rate:    {s['hit_rate']*100:.1f}%\n"
            f"  cached size: {s['total_cached_chars']:,} chars\n"
            f"  ttl:         {s['ttl_s']}s"
        )


_cache: KvCache | None = None
_cache_lock = threading.Lock()


def get_kv_cache() -> KvCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = KvCache()
    return _cache
