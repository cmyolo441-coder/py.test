"""Goal history — persists completed/aborted goal runs for resume and review.

Each goal run is saved with its full transcript, steps, status and metadata.
Users can:
  * ``/goal-history`` — list past goals
  * ``/goal-resume <id>`` — resume an interrupted goal
  * ``/goal-show <id>`` — view a past goal's transcript
  * ``/goal-diff <id1> <id2>`` — compare two goal runs

Goals are stored as JSON under ``~/.terminal_agent/goals/``.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GOALS_DIR = Path.home() / ".terminal_agent" / "goals"
MAX_HISTORY = 100


@dataclass
class GoalRecord:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    goal: str = ""
    effort: str = "ultrahype"
    status: str = "running"  # running | complete | failed | cancelled | interrupted
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    rounds: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)  # {kind, round, text}
    final: str = ""
    cost_usd: float = 0.0
    total_tokens: int = 0
    checkpoint_messages: list[dict[str, Any]] = field(default_factory=list)
    last_round_completed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class GoalHistory:
    """Persists goal runs and supports resume."""

    def __init__(self) -> None:
        GOALS_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self, goal_id: str) -> Path:
        return GOALS_DIR / f"{goal_id}.json"

    def save(self, record: GoalRecord) -> Path:
        path = self._path(record.id)
        path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
        self._rotate()
        return path

    def load(self, goal_id: str) -> GoalRecord | None:
        path = self._path(goal_id)
        if not path.exists():
            return None
        try:
            return GoalRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def list_all(self) -> list[GoalRecord]:
        records: list[GoalRecord] = []
        for path in GOALS_DIR.glob("*.json"):
            try:
                records.append(GoalRecord.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError, TypeError):
                continue
        records.sort(key=lambda r: -r.created_at)
        return records[:MAX_HISTORY]

    def delete(self, goal_id: str) -> bool:
        path = self._path(goal_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def find_interrupted(self) -> GoalRecord | None:
        """Return the most recent interrupted goal (for auto-resume prompts)."""
        for r in self.list_all():
            if r.status in ("interrupted", "running"):
                return r
        return None

    def _rotate(self) -> None:
        files = sorted(GOALS_DIR.glob("*.json"), key=lambda p: -p.stat().st_mtime)
        for path in files[MAX_HISTORY:]:
            try:
                path.unlink()
            except OSError:
                pass

    def dashboard(self) -> str:
        records = self.list_all()
        if not records:
            return "No goal history yet. Start one with /goal <description>."
        lines = [f"Goal history ({len(records)} most recent):"]
        for r in records[:20]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.created_at))
            status_icon = {
                "complete": "✓",
                "failed": "✗",
                "cancelled": "⊘",
                "interrupted": "⏸",
                "running": "▶",
            }.get(r.status, "?")
            goal_preview = r.goal[:60] + ("…" if len(r.goal) > 60 else "")
            lines.append(
                f"  {status_icon} {r.id}  {ts}  rounds={r.rounds}  "
                f"cost=${r.cost_usd:.4f}  {goal_preview}"
            )
        lines.append("")
        lines.append("Use /goal-show <id> to view, /goal-resume <id> to resume.")
        return "\n".join(lines)

    def show(self, goal_id: str) -> str:
        r = self.load(goal_id)
        if r is None:
            return f"No goal with id '{goal_id}'."
        lines = [
            f"Goal: {r.goal}",
            f"ID: {r.id}",
            f"Effort: {r.effort}",
            f"Status: {r.status}",
            f"Rounds: {r.rounds}",
            f"Cost: ${r.cost_usd:.4f}",
            f"Tokens: {r.total_tokens:,}",
            f"Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r.created_at))}",
            "",
            "Steps:",
        ]
        for s in r.steps:
            kind = s.get("kind", "?")
            rnd = s.get("round", 0)
            text = s.get("text", "")[:300]
            lines.append(f"  [{kind} #{rnd}] {text}")
            if len(s.get("text", "")) > 300:
                lines.append("    …(truncated)")
        if r.final:
            lines.append("")
            lines.append("Final output:")
            lines.append(r.final[:2000])
        return "\n".join(lines)


_history: GoalHistory | None = None


def get_goal_history() -> GoalHistory:
    global _history
    if _history is None:
        _history = GoalHistory()
    return _history
