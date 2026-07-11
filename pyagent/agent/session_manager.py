"""Session manager — save and resume agent sessions.

Inspired by gsd-pi's session management, this module provides:
  - Session persistence with unique IDs
  - Session listing and search
  - Session resume from any point
  - Session metadata (duration, tokens, tools used)

Sessions are stored in ~/.terminal_agent/sessions/
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SESSIONS_DIR = Path.home() / ".terminal_agent" / "sessions"


@dataclass
class SessionInfo:
    """Metadata about a saved session."""
    id: str
    created_at: float
    updated_at: float
    provider: str
    model: str
    message_count: int
    token_estimate: int
    tool_calls: int
    duration_s: float
    summary: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def age_str(self) -> str:
        """Human-readable age string."""
        age = time.time() - self.updated_at
        if age < 60:
            return "just now"
        elif age < 3600:
            return f"{int(age / 60)}m ago"
        elif age < 86400:
            return f"{int(age / 3600)}h ago"
        else:
            return f"{int(age / 86400)}d ago"


@dataclass
class SessionData:
    """Full session data including messages."""
    info: SessionInfo
    messages: list[dict[str, Any]]
    system_prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """Manages agent sessions — save, load, list, resume."""

    def __init__(self, sessions_dir: Path | None = None):
        self.sessions_dir = sessions_dir or SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        """Get the file path for a session."""
        return self.sessions_dir / f"{session_id}.json"

    def save(
        self,
        session_id: str | None,
        messages: list[dict[str, Any]],
        provider: str,
        model: str,
        system_prompt: str = "",
        summary: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionInfo:
        """Save a session to disk.

        Args:
            session_id: Session ID (generated if None).
            messages: Conversation messages.
            provider: Provider name.
            model: Model name.
            system_prompt: System prompt used.
            summary: Session summary.
            tags: Tags for categorization.
            metadata: Additional metadata.

        Returns:
            SessionInfo with the saved session's metadata.
        """
        now = time.time()

        if session_id is None:
            session_id = uuid.uuid4().hex[:12]

        # Calculate token estimate
        token_estimate = sum(
            len(m.get("content", "").split()) * 1.3
            for m in messages
            if isinstance(m.get("content"), str)
        )

        # Count tool calls
        tool_calls = sum(
            1 for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        )

        info = SessionInfo(
            id=session_id,
            created_at=now,
            updated_at=now,
            provider=provider,
            model=model,
            message_count=len(messages),
            token_estimate=int(token_estimate),
            tool_calls=tool_calls,
            duration_s=0,  # Will be updated on close
            summary=summary,
            tags=tags or [],
        )

        data = SessionData(
            info=info,
            messages=messages,
            system_prompt=system_prompt,
            metadata=metadata or {},
        )

        # Save to disk
        path = self._session_path(session_id)
        path.write_text(json.dumps(asdict(data), indent=2), encoding="utf-8")

        return info

    def update(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        duration_s: float = 0,
        summary: str | None = None,
    ) -> SessionInfo | None:
        """Update an existing session.

        Args:
            session_id: Session ID to update.
            messages: Updated messages.
            duration_s: Total session duration.
            summary: Updated summary.

        Returns:
            Updated SessionInfo or None if session not found.
        """
        path = self._session_path(session_id)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        info = SessionInfo(**data["info"])

        # Update fields
        info.updated_at = time.time()
        info.message_count = len(messages)
        info.duration_s = duration_s
        if summary is not None:
            info.summary = summary

        # Recalculate tokens
        info.token_estimate = int(sum(
            len(m.get("content", "").split()) * 1.3
            for m in messages
            if isinstance(m.get("content"), str)
        ))

        # Update data
        data["info"] = asdict(info)
        data["messages"] = messages

        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        return info

    def load(self, session_id: str) -> SessionData | None:
        """Load a session from disk.

        Args:
            session_id: Session ID to load.

        Returns:
            SessionData or None if not found.
        """
        path = self._session_path(session_id)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))

        return SessionData(
            info=SessionInfo(**data["info"]),
            messages=data["messages"],
            system_prompt=data.get("system_prompt", ""),
            metadata=data.get("metadata", {}),
        )

    def list_sessions(
        self,
        limit: int = 20,
        tag: str | None = None,
        provider: str | None = None,
    ) -> list[SessionInfo]:
        """List saved sessions.

        Args:
            limit: Maximum number of sessions to return.
            tag: Filter by tag.
            provider: Filter by provider.

        Returns:
            List of SessionInfo, most recent first.
        """
        sessions: list[SessionInfo] = []

        for path in self.sessions_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                info = SessionInfo(**data["info"])

                # Apply filters
                if tag and tag not in info.tags:
                    continue
                if provider and info.provider != provider:
                    continue

                sessions.append(info)
            except (json.JSONDecodeError, KeyError):
                continue

        # Sort by updated_at descending
        sessions.sort(key=lambda s: s.updated_at, reverse=True)

        return sessions[:limit]

    def delete(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: Session ID to delete.

        Returns:
            True if deleted, False if not found.
        """
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def search(
        self,
        query: str,
        limit: int = 10,
    ) -> list[SessionInfo]:
        """Search sessions by summary or tags.

        Args:
            query: Search query.
            limit: Maximum results.

        Returns:
            Matching sessions.
        """
        query_lower = query.lower()
        results: list[SessionInfo] = []

        for session in self.list_sessions(limit=100):
            if (
                query_lower in session.summary.lower()
                or any(query_lower in tag.lower() for tag in session.tags)
            ):
                results.append(session)
                if len(results) >= limit:
                    break

        return results


# Global instance
_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
