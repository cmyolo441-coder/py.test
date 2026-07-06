"""Tool learning — tracks which tools succeed/fail and recommends the best
tool for a given task.

Over time the agent learns:
  * which tools are most reliable
  * which tools are fastest
  * which tools to try first for a given query class
  * which tools to avoid (high failure rate)

Persists to ~/.terminal_agent/tool_learning.json so learning survives
restarts.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class ToolStat:
    name: str
    total_calls: int = 0
    successes: int = 0
    failures: int = 0
    total_duration_s: float = 0.0
    last_used: float = 0.0
    # Query classes where this tool was useful.
    useful_for: dict[str, int] = field(default_factory=dict)  # {query_class: count}

    @property
    def success_rate(self) -> float:
        return (self.successes / self.total_calls) if self.total_calls else 0.0

    @property
    def avg_duration(self) -> float:
        return (self.total_duration_s / self.total_calls) if self.total_calls else 0.0

    @property
    def reliability_score(self) -> float:
        """0-1 score combining success rate and speed."""
        if self.total_calls == 0:
            return 0.5  # unknown — neutral
        # Success rate weighted 70%, speed (inverse) weighted 30%.
        speed_score = 1.0 / (1.0 + self.avg_duration)  # faster = higher
        return 0.7 * self.success_rate + 0.3 * speed_score


class ToolLearner:
    """Learns which tools work best and recommends them."""

    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, ToolStat] = {}
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".terminal_agent" / "tool_learning.json"
        self._load()

    def record(self, tool_name: str, success: bool, duration_s: float = 0.0, query_class: str = "default") -> None:
        with self._lock:
            stat = self._stats.setdefault(tool_name, ToolStat(name=tool_name))
            stat.total_calls += 1
            if success:
                stat.successes += 1
            else:
                stat.failures += 1
            stat.total_duration_s += duration_s
            stat.last_used = time.time()
            if success:
                stat.useful_for[query_class] = stat.useful_for.get(query_class, 0) + 1
            self._save()

    def recommend(self, query_class: str = "default", available_tools: list[str] | None = None, top_k: int = 5) -> list[tuple[str, float]]:
        """Recommend the best tools for a query class. Returns [(name, score)]."""
        with self._lock:
            candidates = available_tools or list(self._stats.keys())
            scored = []
            for name in candidates:
                stat = self._stats.get(name)
                if stat is None:
                    scored.append((name, 0.5))  # unknown — neutral
                    continue
                # Boost tools useful for this query class.
                class_boost = stat.useful_for.get(query_class, 0) / max(1, stat.total_calls)
                score = 0.6 * stat.reliability_score + 0.4 * class_boost
                scored.append((name, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def dashboard(self) -> str:
        with self._lock:
            if not self._stats:
                return "No tool usage recorded yet."
            lines = [f"Tool learning dashboard ({len(self._stats)} tools tracked):"]
            sorted_stats = sorted(self._stats.values(), key=lambda s: -s.total_calls)
            for s in sorted_stats[:20]:
                lines.append(
                    f"  {s.name:<22} calls={s.total_calls:>4}  "
                    f"success={s.success_rate*100:>5.1f}%  "
                    f"avg={s.avg_duration:.2f}s  "
                    f"reliability={s.reliability_score:.2f}"
                )
        return "\n".join(lines)

    def stats_for(self, tool_name: str) -> ToolStat | None:
        with self._lock:
            return self._stats.get(tool_name)

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()
            self._save()

    def _save(self) -> None:
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {name: asdict(stat) for name, stat in self._stats.items()}
            self.persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            for name, stat_data in data.items():
                self._stats[name] = ToolStat(**stat_data)
        except (json.JSONDecodeError, OSError, TypeError):
            pass


_learner: ToolLearner | None = None
_learner_lock = threading.Lock()


def get_tool_learner() -> ToolLearner:
    global _learner
    if _learner is None:
        with _learner_lock:
            if _learner is None:
                _learner = ToolLearner()
    return _learner
