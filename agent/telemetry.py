"""Opt-in telemetry — anonymous usage analytics for self-improvement.

Collects aggregated, non-PII usage patterns (turn count, tool usage frequency,
error rates, latency) to help identify friction points. Strictly opt-in:
telemetry is OFF by default and only enabled via ``/telemetry on`` or the
``TELEMETRY_ENABLED=1`` env var.

No user prompts, file contents, or model responses are ever recorded — only
counts and durations.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class TelemetryEvent:
    name: str
    timestamp: float = field(default_factory=time.time)
    duration_s: float = 0.0
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class TelemetryCollector:
    """Aggregates events in memory, persists to a local JSONL file."""

    def __init__(self, persist_path: Path | None = None) -> None:
        self.enabled = os.getenv("TELEMETRY_ENABLED", "0") == "1"
        self._lock = threading.Lock()
        self._events: list[TelemetryEvent] = []
        self._counters: dict[str, int] = defaultdict(int)
        self._persist_path = persist_path or Path.home() / ".terminal_agent" / "telemetry.jsonl"

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        return self.enabled

    def record(self, name: str, duration_s: float = 0.0, success: bool = True, **metadata: Any) -> None:
        if not self.enabled:
            return
        event = TelemetryEvent(
            name=name, duration_s=duration_s, success=success, metadata=metadata
        )
        with self._lock:
            self._events.append(event)
            self._counters[name] += 1
            # Keep memory bounded.
            if len(self._events) > 5000:
                self._events = self._events[-2500:]
            self._append_to_file(event)

    def _append_to_file(self, event: TelemetryEvent) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self._persist_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(event)) + "\n")
        except OSError:
            pass

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total = len(self._events)
            success = sum(1 for e in self._events if e.success)
            fail = total - success
            avg_dur = sum(e.duration_s for e in self._events) / total if total else 0
            return {
                "enabled": self.enabled,
                "total_events": total,
                "success_count": success,
                "failure_count": fail,
                "success_rate": (success / total * 100) if total else 0,
                "avg_duration_s": avg_dur,
                "top_events": sorted(self._counters.items(), key=lambda x: -x[1])[:10],
            }

    def dashboard(self) -> str:
        s = self.summary()
        if not s["enabled"]:
            return "Telemetry: OFF (enable with /telemetry on)"
        lines = [
            "Telemetry dashboard (anonymous, opt-in):",
            f"  status:        {'ENABLED' if s['enabled'] else 'DISABLED'}",
            f"  total events:  {s['total_events']:,}",
            f"  success rate:  {s['success_rate']:.1f}%",
            f"  avg duration:  {s['avg_duration_s']:.3f}s",
            "",
            "  Top events:",
        ]
        for name, count in s["top_events"]:
            lines.append(f"    {name:<30} {count:>5}")
        return "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._counters.clear()
        try:
            if self._persist_path.exists():
                self._persist_path.unlink()
        except OSError:
            pass


_collector: TelemetryCollector | None = None
_collector_lock = threading.Lock()


def get_telemetry() -> TelemetryCollector:
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = TelemetryCollector()
    return _collector
