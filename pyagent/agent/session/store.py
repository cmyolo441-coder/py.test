"""Persistent storage for sessions under ~/.terminal_agent/sessions."""

from __future__ import annotations

import json
from pathlib import Path

from .session import Session

SESSIONS_DIR = Path.home() / ".terminal_agent" / "sessions"


class SessionStore:
    def __init__(self, directory: Path = SESSIONS_DIR) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.dir / f"{session_id}.json"

    def save(self, session: Session) -> None:
        self._path(session.id).write_text(
            json.dumps(session.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def load(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return None

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_all(self) -> list[Session]:
        sessions: list[Session] = []
        for path in self.dir.glob("*.json"):
            try:
                sessions.append(Session.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)
