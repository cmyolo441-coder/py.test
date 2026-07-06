"""Session crash recovery — auto-save and restore conversation state.

Writes a recovery checkpoint after every turn so that if the agent crashes
(killed, OOM, terminal closed), the user can resume right where they left
off with ``/recover``.

Checkpoints are stored under ``~/.terminal_agent/recovery/`` as JSON files,
one per session, rotated to keep the most recent 10.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

RECOVERY_DIR = Path.home() / ".terminal_agent" / "recovery"
MAX_CHECKPOINTS = 10


@dataclass
class Checkpoint:
    session_id: str
    timestamp: float = field(default_factory=time.time)
    provider: str = ""
    model: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    goal_mode: bool = False
    turn_count: int = 0


class RecoveryManager:
    """Auto-saves and restores conversation checkpoints."""

    def __init__(self, session_id: str = "default") -> None:
        self.session_id = session_id
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self) -> Path:
        return RECOVERY_DIR / f"{self.session_id}.json"

    def save(self, checkpoint: Checkpoint) -> Path:
        path = self._path()
        path.write_text(json.dumps(asdict(checkpoint), indent=2), encoding="utf-8")
        self._rotate()
        return path

    def load(self) -> Checkpoint | None:
        path = self._path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint(**data)
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """List all recovery files across sessions."""
        results = []
        for path in sorted(RECOVERY_DIR.glob("*.json"), key=lambda p: -p.stat().st_mtime):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                results.append({
                    "session_id": data.get("session_id", path.stem),
                    "timestamp": data.get("timestamp", 0),
                    "turn_count": data.get("turn_count", 0),
                    "provider": data.get("provider", ""),
                    "model": data.get("model", ""),
                    "goal_mode": data.get("goal_mode", False),
                    "path": str(path),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def delete(self, session_id: str | None = None) -> bool:
        target = session_id or self.session_id
        path = RECOVERY_DIR / f"{target}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def _rotate(self) -> None:
        """Keep only the most recent MAX_CHECKPOINTS recovery files."""
        files = sorted(RECOVERY_DIR.glob("*.json"), key=lambda p: -p.stat().st_mtime)
        for path in files[MAX_CHECKPOINTS:]:
            try:
                path.unlink()
            except OSError:
                pass

    def dashboard(self) -> str:
        checkpoints = self.list_checkpoints()
        if not checkpoints:
            return "No recovery checkpoints found."
        lines = ["Recovery checkpoints (most recent first):"]
        for c in checkpoints[:MAX_CHECKPOINTS]:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c["timestamp"]))
            goal = " [GOAL]" if c["goal_mode"] else ""
            lines.append(
                f"  {c['session_id']:<20} {ts}  turns={c['turn_count']:>3}  "
                f"{c['provider']}/{c['model']}{goal}"
            )
        lines.append("")
        lines.append("Use /recover <session_id> to restore a checkpoint.")
        return "\n".join(lines)


_manager: RecoveryManager | None = None


def get_recovery_manager(session_id: str = "default") -> RecoveryManager:
    global _manager
    if _manager is None or _manager.session_id != session_id:
        _manager = RecoveryManager(session_id)
    return _manager
