"""Turn latency tracking — monitor and display response times.

Inspired by gsd-pi's turn-latency module, this module provides:
  - Track latency for each turn
  - Display latency statistics
  - Identify slow turns
  - Export latency data
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TurnLatencyRecord:
    """Record of latency for a single turn."""
    turn_index: int
    start_time: float
    end_time: float | None = None
    model: str = ""
    provider: str = ""
    first_token_time: float | None = None
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    
    @property
    def duration_ms(self) -> float:
        """Total duration in milliseconds."""
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000
    
    @property
    def time_to_first_token_ms(self) -> float | None:
        """Time to first token in milliseconds."""
        if self.first_token_time is None:
            return None
        return (self.first_token_time - self.start_time) * 1000
    
    @property
    def duration_str(self) -> str:
        """Human-readable duration."""
        ms = self.duration_ms
        if ms < 1000:
            return f"{ms:.0f}ms"
        return f"{ms / 1000:.1f}s"
    
    @property
    def ttft_str(self) -> str | None:
        """Human-readable time to first token."""
        ms = self.time_to_first_token_ms
        if ms is None:
            return None
        if ms < 1000:
            return f"{ms:.0f}ms"
        return f"{ms / 1000:.1f}s"


@dataclass
class LatencyStats:
    """Aggregated latency statistics."""
    total_turns: int = 0
    total_duration_ms: float = 0
    min_duration_ms: float = float("inf")
    max_duration_ms: float = 0
    avg_duration_ms: float = 0
    p50_duration_ms: float = 0
    p95_duration_ms: float = 0
    p99_duration_ms: float = 0
    avg_ttft_ms: float = 0
    total_tool_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class TurnLatencyTracker:
    """Track and analyze turn latency."""
    
    def __init__(self, max_records: int = 1000):
        self.records: list[TurnLatencyRecord] = []
        self.max_records = max_records
        self._current: TurnLatencyRecord | None = None
    
    def start_turn(
        self,
        turn_index: int,
        model: str = "",
        provider: str = "",
    ) -> None:
        """Start tracking a new turn.
        
        Args:
            turn_index: The turn number.
            model: Model name.
            provider: Provider name.
        """
        self._current = TurnLatencyRecord(
            turn_index=turn_index,
            start_time=time.time(),
            model=model,
            provider=provider,
        )
    
    def mark_first_token(self) -> None:
        """Mark when the first token was received."""
        if self._current and self._current.first_token_time is None:
            self._current.first_token_time = time.time()
    
    def end_turn(
        self,
        tool_calls: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> TurnLatencyRecord | None:
        """End the current turn and record latency.
        
        Args:
            tool_calls: Number of tool calls made.
            tokens_in: Input tokens.
            tokens_out: Output tokens.
        
        Returns:
            The completed latency record.
        """
        if self._current is None:
            return None
        
        self._current.end_time = time.time()
        self._current.tool_calls = tool_calls
        self._current.tokens_in = tokens_in
        self._current.tokens_out = tokens_out
        
        self.records.append(self._current)
        
        # Trim old records
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]
        
        record = self._current
        self._current = None
        return record
    
    def get_stats(self) -> LatencyStats:
        """Calculate aggregated latency statistics."""
        if not self.records:
            return LatencyStats()
        
        durations = [r.duration_ms for r in self.records]
        ttfts = [r.time_to_first_token_ms for r in self.records if r.time_to_first_token_ms is not None]
        
        durations.sort()
        n = len(durations)
        
        stats = LatencyStats(
            total_turns=n,
            total_duration_ms=sum(durations),
            min_duration_ms=durations[0],
            max_duration_ms=durations[-1],
            avg_duration_ms=sum(durations) / n,
            p50_duration_ms=durations[n // 2],
            p95_duration_ms=durations[int(n * 0.95)] if n >= 20 else durations[-1],
            p99_duration_ms=durations[int(n * 0.99)] if n >= 100 else durations[-1],
            avg_ttft_ms=sum(ttfts) / len(ttfts) if ttfts else 0,
            total_tool_calls=sum(r.tool_calls for r in self.records),
            total_tokens_in=sum(r.tokens_in for r in self.records),
            total_tokens_out=sum(r.tokens_out for r in self.records),
        )
        
        return stats
    
    def get_slow_turns(self, threshold_ms: float = 5000) -> list[TurnLatencyRecord]:
        """Get turns that exceeded the latency threshold.
        
        Args:
            threshold_ms: Threshold in milliseconds.
        
        Returns:
            List of slow turn records.
        """
        return [r for r in self.records if r.duration_ms > threshold_ms]
    
    def get_recent(self, n: int = 10) -> list[TurnLatencyRecord]:
        """Get the most recent turn records.
        
        Args:
            n: Number of records to return.
        
        Returns:
            List of recent records.
        """
        return self.records[-n:]
    
    def format_stats(self) -> str:
        """Format latency statistics as a string."""
        stats = self.get_stats()
        
        lines = [
            "Turn Latency Statistics:",
            f"  Total turns: {stats.total_turns}",
            f"  Total time: {stats.total_duration_ms / 1000:.1f}s",
            f"  Avg: {stats.avg_duration_ms:.0f}ms",
            f"  Min: {stats.min_duration_ms:.0f}ms",
            f"  Max: {stats.max_duration_ms:.0f}ms",
            f"  P50: {stats.p50_duration_ms:.0f}ms",
            f"  P95: {stats.p95_duration_ms:.0f}ms",
            f"  P99: {stats.p99_duration_ms:.0f}ms",
        ]
        
        if stats.avg_ttft_ms > 0:
            lines.append(f"  Avg TTFT: {stats.avg_ttft_ms:.0f}ms")
        
        lines.append(f"  Total tool calls: {stats.total_tool_calls}")
        lines.append(f"  Total tokens: {stats.total_tokens_in + stats.total_tokens_out}")
        
        return "\n".join(lines)
    
    def save(self, path: Path) -> None:
        """Save latency records to a JSON file."""
        data = {
            "records": [asdict(r) for r in self.records],
            "stats": asdict(self.get_stats()),
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    def load(self, path: Path) -> None:
        """Load latency records from a JSON file."""
        if not path.exists():
            return
        
        data = json.loads(path.read_text(encoding="utf-8"))
        
        self.records = [
            TurnLatencyRecord(**r)
            for r in data.get("records", [])
        ]


# Global instance
_tracker: TurnLatencyTracker | None = None


def get_latency_tracker() -> TurnLatencyTracker:
    """Get the global latency tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = TurnLatencyTracker()
    return _tracker
