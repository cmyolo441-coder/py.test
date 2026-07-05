"""Conversation memory with optional persistence to disk."""

from __future__ import annotations

import json
from typing import Any

from .config import HISTORY_FILE


class Conversation:
    """Holds the message list and handles provider-neutral message shapes."""

    def __init__(self, system_prompt: str) -> None:
        self.messages: list[dict[str, Any]] = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    def add_user(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str, tool_calls: list[dict[str, Any]] | None = None) -> None:
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}
        )

    def reset(self, system_prompt: str) -> None:
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

    # Persistence --------------------------------------------------------
    def save(self) -> None:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(self.messages, indent=2), encoding="utf-8")

    def load(self) -> bool:
        if not HISTORY_FILE.exists():
            return False
        try:
            self.messages = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def token_estimate(self) -> int:
        """Rough token estimate (~4 chars/token) for context awareness."""
        chars = sum(len(str(m.get("content", ""))) for m in self.messages)
        return chars // 4
