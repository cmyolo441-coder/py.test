"""Interpretable AI — explain every decision the agent makes.

Every tool call, model choice, and response gets a human-readable
explanation. This builds a decision tree that can be inspected.

The agent can answer "why did you do X?" by walking the decision tree.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionNode:
    """A single decision the agent made."""
    id: str
    timestamp: float
    decision_type: str  # "tool_call" | "model_choice" | "response" | "goal_step"
    action: str  # what was decided
    reasoning: str  # why it was decided
    alternatives: list[str] = field(default_factory=list)  # what else was considered
    inputs: dict[str, Any] = field(default_factory=dict)  # what informed the decision
    outcome: str = ""  # what happened as a result
    confidence: float = 1.0
    parent_id: str = ""  # for building a decision tree


class InterpretableAI:
    """Records and explains every agent decision."""

    def __init__(self) -> None:
        self.decisions: list[DecisionNode] = []
        self._counter = 0

    def record(
        self,
        decision_type: str,
        action: str,
        reasoning: str,
        alternatives: list[str] | None = None,
        inputs: dict | None = None,
        confidence: float = 1.0,
        parent_id: str = "",
    ) -> str:
        """Record a decision. Returns the decision ID."""
        self._counter += 1
        node = DecisionNode(
            id=f"D{self._counter:04d}",
            timestamp=time.time(),
            decision_type=decision_type,
            action=action,
            reasoning=reasoning,
            alternatives=alternatives or [],
            inputs=inputs or {},
            confidence=confidence,
            parent_id=parent_id,
        )
        self.decisions.append(node)
        # Keep memory bounded.
        if len(self.decisions) > 2000:
            self.decisions = self.decisions[-1000:]
        return node.id

    def explain(self, decision_id: str) -> str:
        """Explain a specific decision."""
        node = next((d for d in self.decisions if d.id == decision_id), None)
        if node is None:
            return f"Decision '{decision_id}' not found."
        lines = [
            f"Decision {node.id} ({node.decision_type}):",
            f"  Action: {node.action}",
            f"  Reasoning: {node.reasoning}",
            f"  Confidence: {node.confidence:.0%}",
            f"  Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(node.timestamp))}",
        ]
        if node.alternatives:
            lines.append(f"  Alternatives considered:")
            for alt in node.alternatives:
                lines.append(f"    - {alt}")
        if node.inputs:
            lines.append(f"  Inputs:")
            for k, v in node.inputs.items():
                lines.append(f"    {k}: {v}")
        if node.outcome:
            lines.append(f"  Outcome: {node.outcome}")
        if node.parent_id:
            lines.append(f"  Parent decision: {node.parent_id}")
            parent_explanation = self.explain(node.parent_id)
            lines.append(f"\n  Parent context:\n{parent_explanation}")
        return "\n".join(lines)

    def explain_last(self, n: int = 5) -> str:
        """Explain the last N decisions."""
        if not self.decisions:
            return "No decisions recorded yet."
        recent = self.decisions[-n:]
        lines = [f"Last {len(recent)} decision(s):"]
        for d in recent:
            lines.append(f"\n  {d.id} [{d.decision_type}] {d.action}")
            lines.append(f"    Reason: {d.reasoning}")
        return "\n".join(lines)

    def why(self, action_substring: str) -> str:
        """Answer 'why did you do X?' by finding matching decisions."""
        matches = [d for d in self.decisions if action_substring.lower() in d.action.lower()]
        if not matches:
            return f"No decision matching '{action_substring}'."
        lines = [f"Decisions matching '{action_substring}' ({len(matches)} found):"]
        for d in matches[-5:]:  # last 5
            lines.append(f"\n  {d.id}: {d.action}")
            lines.append(f"    Reason: {d.reasoning}")
            lines.append(f"    When: {time.strftime('%H:%M:%S', time.localtime(d.timestamp))}")
        return "\n".join(lines)

    def decision_tree(self) -> str:
        """Render the decision tree (parent-child relationships)."""
        if not self.decisions:
            return "(no decisions)"
        lines = ["Decision tree:"]
        # Build child index.
        children: dict[str, list[DecisionNode]] = {}
        roots: list[DecisionNode] = []
        for d in self.decisions:
            if d.parent_id:
                children.setdefault(d.parent_id, []).append(d)
            else:
                roots.append(d)

        def _render(node: DecisionNode, prefix: str) -> None:
            lines.append(f"{prefix}{node.id} [{node.decision_type}] {node.action[:60]}")
            for child in children.get(node.id, []):
                _render(child, prefix + "  ")

        for root in roots[-10:]:  # last 10 roots
            _render(root, "")
        return "\n".join(lines)

    def dashboard(self) -> str:
        lines = [
            f"Interpretable AI ({len(self.decisions)} decisions recorded):",
            f"  by type:",
        ]
        type_counts: dict[str, int] = {}
        for d in self.decisions:
            type_counts[d.decision_type] = type_counts.get(d.decision_type, 0) + 1
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"    {t:<20} {c}")
        return "\n".join(lines)


_interpreter: InterpretableAI | None = None


def get_interpreter() -> InterpretableAI:
    global _interpreter
    if _interpreter is None:
        _interpreter = InterpretableAI()
    return _interpreter
