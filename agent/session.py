"""Session management: multiple named conversations persisted to disk."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Session:
    name: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    """Load, save and list sessions in a directory of JSON files."""

    def __init__(self, directory: str | Path = "chat_history") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe = "".join(c for c in name if c.isalnum() or c in ("-", "_")) or "session"
        return self.directory / f"{safe}.json"

    def save(self, session: Session) -> Path:
        session.touch()
        path = self._path(session.name)
        path.write_text(json.dumps(asdict(session), indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> Session:
        path = self._path(name)
        if not path.exists():
            raise FileNotFoundError(f"No session named '{name}'")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session(**data)

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def list(self) -> list[dict[str, Any]]:
        sessions = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            sessions.append(
                {
                    "name": data.get("name", path.stem),
                    "messages": len(data.get("messages", [])),
                    "updated_at": data.get("updated_at", 0),
                }
            )
        return sessions
