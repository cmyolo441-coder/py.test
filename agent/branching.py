"""Conversation branching — fork the conversation from any past message.

Lets the user explore "what if" paths without losing the main thread. Each
branch is a full copy of the conversation up to the fork point; the original
remains untouched. Branches are stored in-memory and can be listed/switched.

Use cases:
  * "Try that again with a different approach"
  * "Go back to before I asked about X"
  * Compare two model answers side by side
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Branch:
    id: str
    name: str
    parent_id: str | None
    forked_at_index: int  # message index where we forked
    messages: list[dict[str, Any]]
    created_at: float = field(default_factory=time.time)
    note: str = ""


class BranchManager:
    """Manages a tree of conversation branches."""

    def __init__(self, initial_messages: list[dict[str, Any]] | None = None) -> None:
        self._branches: dict[str, Branch] = {}
        self._active_id: str = "main"
        main = Branch(
            id="main",
            name="main",
            parent_id=None,
            forked_at_index=0,
            messages=list(initial_messages or []),
        )
        self._branches["main"] = main

    @property
    def active(self) -> Branch:
        return self._branches[self._active_id]

    @property
    def active_id(self) -> str:
        return self._active_id

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.active.messages

    def set_messages(self, messages: list[dict[str, Any]]) -> None:
        """Update the active branch's messages (called by Conversation)."""
        self.active.messages = list(messages)

    def fork(self, name: str | None = None, at_index: int | None = None, note: str = "") -> Branch:
        """Create a new branch from the active branch.

        ``at_index`` is the message index to fork at (defaults to current end).
        """
        parent = self.active
        fork_idx = at_index if at_index is not None else len(parent.messages)
        fork_idx = max(0, min(fork_idx, len(parent.messages)))
        branch_id = f"b{int(time.time() * 1000) % 1000000}"
        name = name or f"branch-{branch_id}"
        branch = Branch(
            id=branch_id,
            name=name,
            parent_id=parent.id,
            forked_at_index=fork_idx,
            messages=copy.deepcopy(parent.messages[:fork_idx]),
            note=note,
        )
        self._branches[branch_id] = branch
        self._active_id = branch_id
        return branch

    def switch(self, branch_id: str) -> bool:
        if branch_id not in self._branches:
            return False
        self._active_id = branch_id
        return True

    def list_branches(self) -> list[Branch]:
        return sorted(self._branches.values(), key=lambda b: b.created_at)

    def delete(self, branch_id: str) -> bool:
        if branch_id == "main":
            return False  # never delete main
        if branch_id not in self._branches:
            return False
        del self._branches[branch_id]
        if self._active_id == branch_id:
            self._active_id = "main"
        return True

    def tree(self) -> str:
        """Render an ASCII tree of all branches."""
        lines: list[str] = []
        children: dict[str | None, list[Branch]] = {}
        for b in self._branches.values():
            children.setdefault(b.parent_id, []).append(b)
        def _render(parent_id: str | None, prefix: str) -> None:
            for b in children.get(parent_id, []):
                marker = " *" if b.id == self._active_id else ""
                lines.append(f"{prefix}{b.name} ({len(b.messages)} msgs){marker}")
                _render(b.id, prefix + "  ")
        _render(None, "")
        return "\n".join(lines) if lines else "(no branches)"

    def diff(self, branch_a: str, branch_b: str) -> str:
        """Show a quick diff of message counts and last messages between branches."""
        a = self._branches.get(branch_a)
        b = self._branches.get(branch_b)
        if not a or not b:
            return "branch not found"
        lines = [
            f"Diff: {a.name} ({len(a.messages)} msgs) vs {b.name} ({len(b.messages)} msgs)",
            f"  {a.name} last: {a.messages[-1].get('content', '')[:80] if a.messages else '(empty)'}",
            f"  {b.name} last: {b.messages[-1].get('content', '')[:80] if b.messages else '(empty)'}",
        ]
        return "\n".join(lines)
