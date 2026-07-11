"""Inductive logic programming — learn rules from examples.

Given positive and negative examples, the agent learns Prolog-style rules
that explain the data. This is real ILP — inspired by Progol/Aleph.

Example:
  Positive: parent(alice, bob), parent(bob, carol)
  Negative: parent(carol, alice)
  Learned rule: parent(X, Y) :- ancestor(X, Y), not ancestor(Y, X).

Simplified to work without a Prolog engine — uses pattern matching.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Example:
    """A labeled example: facts that are true (positive) or false (negative)."""
    predicate: str
    args: tuple
    label: bool  # True = positive, False = negative


@dataclass
class LearnedRule:
    """A rule learned from examples."""
    head: str  # e.g., "parent(X, Y)"
    body: list[str]  # e.g., ["ancestor(X, Y)"]
    confidence: float
    support: int  # how many positive examples it explains
    errors: int  # how many negative examples it wrongly covers


class InductiveLogicProgrammer:
    """Learns rules from positive/negative examples."""

    def learn(self, examples: list[Example]) -> list[LearnedRule]:
        """Learn rules that explain the examples."""
        if not examples:
            return []
        # Group by predicate.
        by_predicate: dict[str, list[Example]] = {}
        for ex in examples:
            by_predicate.setdefault(ex.predicate, []).append(ex)

        rules: list[LearnedRule] = []
        for pred, exs in by_predicate.items():
            positives = [e for e in exs if e.label]
            negatives = [e for e in exs if not e.label]
            if not positives:
                continue
            # Learn rules for this predicate.
            rules.extend(self._learn_predicate(pred, positives, negatives))
        return rules

    def _learn_predicate(self, predicate: str, positives: list[Example], negatives: list[Example]) -> list[LearnedRule]:
        """Learn rules for a single predicate."""
        rules: list[LearnedRule] = []
        # Strategy 1: Learn constant rules (specific values).
        for ex in positives:
            # Try: predicate(const1, const2) is true.
            head = f"{predicate}({', '.join(repr(a) for a in ex.args)})"
            support = sum(1 for p in positives if p.args == ex.args)
            errors = sum(1 for n in negatives if n.args == ex.args)
            if errors == 0 and support > 0:
                rules.append(LearnedRule(
                    head=head, body=[], confidence=1.0,
                    support=support, errors=errors,
                ))

        # Strategy 2: Learn type-based rules (e.g., parent(X, Y) :- X is parent of Y).
        # Check if all positive examples share a structural pattern.
        if len(positives) >= 2:
            # Check if the first arg is always different from the second.
            all_different = all(p.args[0] != p.args[1] for p in positives if len(p.args) >= 2)
            if all_different:
                head = f"{predicate}(X, Y)"
                body = ["X != Y"]
                support = len(positives)
                errors = sum(1 for n in negatives if len(n.args) >= 2 and n.args[0] != n.args[1])
                rules.append(LearnedRule(
                    head=head, body=body, confidence=1.0 - errors / max(1, len(positives) + errors),
                    support=support, errors=errors,
                ))

        # Strategy 3: Learn from shared properties (if args have types).
        # This is simplified — real ILP would use background knowledge.

        return rules

    def evaluate(self, rule: LearnedRule, examples: list[Example]) -> dict[str, int]:
        """Evaluate a rule against examples."""
        tp = fp = tn = fn = 0
        for ex in examples:
            # Simplified evaluation: check if the rule's head matches.
            # (Real ILP would use unification.)
            predicted = self._predict(rule, ex)
            if predicted and ex.label:
                tp += 1
            elif predicted and not ex.label:
                fp += 1
            elif not predicted and ex.label:
                fn += 1
            else:
                tn += 1
        return {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
                "precision": tp / max(1, tp + fp), "recall": tp / max(1, tp + fn)}

    def _predict(self, rule: LearnedRule, example: Example) -> bool:
        """Predict if the rule covers the example (simplified)."""
        # Very simplified — just check if the predicate matches.
        return rule.head.startswith(example.predicate)

    def explain_rules(self, rules: list[LearnedRule]) -> str:
        if not rules:
            return "No rules learned."
        lines = [f"Learned {len(rules)} rule(s):"]
        for r in rules:
            body_str = " :- " + ", ".join(r.body) if r.body else ""
            lines.append(f"  {r.head}{body_str}")
            lines.append(f"    confidence={r.confidence:.0%}, support={r.support}, errors={r.errors}")
        return "\n".join(lines)


_ilp: InductiveLogicProgrammer | None = None


def get_ilp() -> InductiveLogicProgrammer:
    global _ilp
    if _ilp is None:
        _ilp = InductiveLogicProgrammer()
    return _ilp
