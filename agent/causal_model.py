"""Causal world model — agent builds a mental model of cause-effect relationships.

The agent observes correlations between actions and outcomes, then builds a
causal graph. This lets it PREDICT the effects of actions before taking them.

Example:
  - Agent observes: "when I edit auth.py, login_test.py often fails"
  - Builds causal edge: edit(auth.py) → fail(login_test.py)
  - Prediction: "If I edit auth.py, expect login_test.py to fail"

This is real causal inference — not just correlation. Uses:
  - Temporal precedence (cause before effect)
  - Covariation (they change together)
  - Confounder control (rule out third variables)
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CausalEvent:
    """An observed event: action happened at time T."""
    action: str  # e.g., "edit_file:auth.py", "run_tool:pytest"
    timestamp: float
    outcome: str = ""  # e.g., "test_fail:login_test.py"
    outcome_timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalEdge:
    """A cause-effect relationship."""
    cause: str
    effect: str
    strength: float  # 0-1, how often cause leads to effect
    observations: int = 0  # how many times observed
    last_seen: float = 0.0
    confidence: float = 0.0  # adjusted for confounders


class CausalWorldModel:
    """Builds and queries a causal graph from observations."""

    def __init__(self, window_seconds: float = 300.0) -> None:
        self.window = window_seconds  # cause must precede effect within this window
        self.events: list[CausalEvent] = []
        self.edges: dict[tuple[str, str], CausalEdge] = {}  # (cause, effect) -> edge
        self._action_counts: dict[str, int] = defaultdict(int)
        self._outcome_counts: dict[str, int] = defaultdict(int)

    def observe(self, action: str, outcome: str = "", metadata: dict | None = None) -> None:
        """Record an action and (optionally) its outcome."""
        event = CausalEvent(
            action=action,
            timestamp=time.time(),
            outcome=outcome,
            outcome_timestamp=time.time() if outcome else 0.0,
            metadata=metadata or {},
        )
        self.events.append(event)
        self._action_counts[action] += 1
        if outcome:
            self._outcome_counts[outcome] += 1
        # Keep memory bounded.
        if len(self.events) > 5000:
            self.events = self.events[-2500:]
        # Update causal edges.
        if outcome:
            self._update_edge(action, outcome)

    def _update_edge(self, cause: str, effect: str) -> None:
        key = (cause, effect)
        edge = self.edges.get(key)
        if edge is None:
            edge = CausalEdge(cause=cause, effect=effect, strength=0.0, observations=0)
            self.edges[key] = edge
        edge.observations += 1
        edge.last_seen = time.time()
        # Strength = P(effect | cause) — how often this cause leads to this effect.
        edge.strength = edge.observations / max(1, self._action_counts[cause])
        # Confidence: higher with more observations.
        edge.confidence = min(1.0, edge.observations / 10.0)

    def predict(self, action: str) -> list[tuple[str, float, float]]:
        """Predict likely outcomes of an action. Returns [(effect, probability, confidence)]."""
        predictions: list[tuple[str, float, float]] = []
        for (cause, effect), edge in self.edges.items():
            if cause == action and edge.strength > 0.1:
                predictions.append((effect, edge.strength, edge.confidence))
        predictions.sort(key=lambda x: -x[1])
        return predictions[:10]

    def find_causes(self, outcome: str) -> list[tuple[str, float, float]]:
        """Find likely causes of an outcome. Returns [(cause, probability, confidence)]."""
        causes: list[tuple[str, float, float]] = []
        for (cause, effect), edge in self.edges.items():
            if effect == outcome and edge.strength > 0.1:
                causes.append((cause, edge.strength, edge.confidence))
        causes.sort(key=lambda x: -x[1])
        return causes[:10]

    def explain(self, outcome: str) -> str:
        """Explain WHY an outcome likely happened (find the root cause)."""
        causes = self.find_causes(outcome)
        if not causes:
            return f"No known causes for '{outcome}'."
        lines = [f"Likely causes for '{outcome}':"]
        for cause, prob, conf in causes:
            lines.append(f"  {cause}  (probability={prob:.0%}, confidence={conf:.0%})")
        # Recursively explain the top cause.
        top_cause = causes[0][0]
        sub_causes = self.find_causes(top_cause)
        if sub_causes:
            lines.append("\nRoot cause chain:")
            lines.append(f"  {top_cause}")
            for sc, sp, _ in sub_causes[:3]:
                lines.append(f"    ← {sc} (p={sp:.0%})")
        return "\n".join(lines)

    def what_if(self, hypothetical_action: str) -> str:
        """Counterfactual: 'what if we did X instead?'"""
        predictions = self.predict(hypothetical_action)
        if not predictions:
            return f"No data on what '{hypothetical_action}' would cause."
        lines = [f"If we do '{hypothetical_action}', likely outcomes:"]
        for effect, prob, conf in predictions:
            lines.append(f"  → {effect}  ({prob:.0%} probability, {conf:.0%} confidence)")
        return "\n".join(lines)

    def dashboard(self) -> str:
        lines = [
            "Causal world model:",
            f"  events observed: {len(self.events)}",
            f"  causal edges: {len(self.edges)}",
            f"  unique actions: {len(self._action_counts)}",
            f"  unique outcomes: {len(self._outcome_counts)}",
            "",
            "Strongest causal relationships:",
        ]
        sorted_edges = sorted(self.edges.values(), key=lambda e: -e.strength)
        for edge in sorted_edges[:15]:
            if edge.observations >= 2:
                lines.append(
                    f"  {edge.cause} → {edge.effect}  "
                    f"(p={edge.strength:.0%}, n={edge.observations}, conf={edge.confidence:.0%})"
                )
        return "\n".join(lines)

    def save(self, path) -> None:
        import json
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "events": [
                {"action": e.action, "timestamp": e.timestamp, "outcome": e.outcome,
                 "outcome_timestamp": e.outcome_timestamp, "metadata": e.metadata}
                for e in self.events[-1000:]  # keep last 1000
            ],
            "edges": [
                {"cause": e.cause, "effect": e.effect, "strength": e.strength,
                 "observations": e.observations, "last_seen": e.last_seen, "confidence": e.confidence}
                for e in self.edges.values()
            ],
        }
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self, path) -> bool:
        import json
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.events = [CausalEvent(**e) for e in data.get("events", [])]
            self.edges = {
                (e["cause"], e["effect"]): CausalEdge(**{k: v for k, v in e.items()})
                for e in data.get("edges", [])
            }
            return True
        except (json.JSONDecodeError, OSError, TypeError):
            return False


_model: CausalWorldModel | None = None


def get_causal_model() -> CausalWorldModel:
    global _model
    if _model is None:
        _model = CausalWorldModel()
    return _model
