"""Counterfactual reasoning — "what if we had done X instead?"

The agent can reason about alternative scenarios:
  - What if we had used a different algorithm?
  - What if we hadn't made that change?
  - What would have happened with a different config?

This uses the causal world model to simulate counterfactual paths.
"""
from __future__ import annotations

from dataclasses import dataclass

from .causal_model import get_causal_model


@dataclass
class CounterfactualResult:
    actual_action: str
    actual_outcome: str
    hypothetical_action: str
    predicted_outcome: str
    difference: str  # how the outcomes differ
    confidence: float = 0.0


class CounterfactualReasoner:
    """Reasons about 'what if' scenarios using the causal model."""

    def __init__(self) -> None:
        self.model = get_causal_model()

    def what_if(self, actual_action: str, hypothetical_action: str, actual_outcome: str = "") -> CounterfactualResult:
        """Compare what happened with what would have happened."""
        # Predict the hypothetical outcome.
        hyp_predictions = self.model.predict(hypothetical_action)
        predicted = hyp_predictions[0][0] if hyp_predictions else "(unknown — no data)"
        confidence = hyp_predictions[0][2] if hyp_predictions else 0.0

        # Compute the difference.
        if actual_outcome and predicted != "(unknown — no data)":
            if actual_outcome == predicted:
                difference = "No difference — same outcome predicted"
            else:
                difference = f"Actual: {actual_outcome} → Hypothetical: {predicted}"
        else:
            difference = "Cannot compare — insufficient data"

        return CounterfactualResult(
            actual_action=actual_action,
            actual_outcome=actual_outcome,
            hypothetical_action=hypothetical_action,
            predicted_outcome=predicted,
            difference=difference,
            confidence=confidence,
        )

    def alternative_history(self, action_sequence: list[str], change_index: int, new_action: str) -> list[CounterfactualResult]:
        """Rewrite history: what if at step `change_index` we did `new_action` instead?"""
        results: list[CounterfactualResult] = []
        for i, action in enumerate(action_sequence):
            if i < change_index:
                continue  # before the change, history is the same
            if i == change_index:
                hyp = new_action
            else:
                hyp = action  # after the change, we'd still do the same actions
            results.append(self.what_if(action, hyp))
        return results

    def regret_analysis(self, action_sequence: list[tuple[str, str]]) -> list[CounterfactualResult]:
        """For each (action, outcome) pair, find what would have been better."""
        results: list[CounterfactualResult] = []
        for action, outcome in action_sequence:
            # Find all actions that might have led to a better outcome.
            all_actions = list(self.model._action_counts.keys())
            for alt_action in all_actions:
                if alt_action == action:
                    continue
                cf = self.what_if(action, alt_action, outcome)
                if cf.predicted_outcome != outcome and cf.confidence > 0.3:
                    results.append(cf)
        return results[:20]  # top 20 regrets


_reasoner: CounterfactualReasoner | None = None


def get_counterfactual_reasoner() -> CounterfactualReasoner:
    global _reasoner
    if _reasoner is None:
        _reasoner = CounterfactualReasoner()
    return _reasoner
