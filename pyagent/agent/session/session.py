"""A named chat session with metadata and message list."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "Untitled session"
    provider: str = ""
    model: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = time.time()

    def add(self, role: str, content: Any) -> None:
        self.messages.append({"role": role, "content": content})
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "provider": self.provider,
            "model": self.model,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            title=data.get("title", "Untitled session"),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            messages=data.get("messages", []),
        )

    @property
    def message_count(self) -> int:
        return len([m for m in self.messages if m.get("role") != "system"])
