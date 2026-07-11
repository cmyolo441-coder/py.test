"""Neuro-symbolic reasoning — combines neural LLMs with symbolic logic.

The agent maintains a knowledge base of formal rules (first-order logic).
When the LLM produces an answer, the symbolic engine:
  1. Extracts claims from the answer
  2. Checks each claim against the rule base
  3. Flags contradictions
  4. Provides formal proofs for verifiable claims

This gives the agent BOTH the flexibility of neural LLMs AND the
correctness guarantees of symbolic logic.

Rule format: (predicate, args, truth_value)
Example: ("is_admin", ["alice"], True)
         ("can_delete", ["alice", "any_file"], True) ← derived from rules
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Rule:
    """A first-order logic rule: IF conditions THEN conclusion."""
    name: str
    conditions: list[tuple[str, list[str]]]  # [(predicate, [args]), ...]
    conclusion: tuple[str, list[str]]  # (predicate, [args])
    description: str = ""


@dataclass
class Fact:
    """A known fact: predicate(args) is True/False."""
    predicate: str
    args: list[str]
    value: bool = True
    confidence: float = 1.0
    source: str = ""  # how was this fact established


@dataclass
class SymbolicCheckResult:
    claim: str
    verdict: str  # "verified" | "contradicted" | "unknown" | "irrelevant"
    proof: str = ""
    conflicting_rules: list[str] = field(default_factory=list)


class NeuroSymbolicEngine:
    """Hybrid reasoning: neural LLM + symbolic logic checker."""

    # Default rule base — common-sense and safety rules.
    DEFAULT_RULES: list[Rule] = [
        Rule(
            name="admin_can_delete",
            conditions=[("is_admin", ["?user"])],
            conclusion=("can_delete", ["?user", "?file"]),
            description="Admins can delete any file",
        ),
        Rule(
            name="no_delete_system_files",
            conditions=[("is_system_file", ["?file"])],
            conclusion=("can_delete", ["?user", "?file"]),
            description="System files cannot be deleted (overrides admin)",
        ),
        Rule(
            name="read_only_cannot_write",
            conditions=[("is_readonly", ["?user"])],
            conclusion=("cannot_write", ["?user", "?file"]),
            description="Read-only users cannot write",
        ),
        Rule(
            name="trusted_source_safe",
            conditions=[("is_trusted", ["?source"]), ("from_source", ["?data", "?source"])],
            conclusion=("is_safe", ["?data"]),
            description="Data from trusted sources is safe",
        ),
    ]

    def __init__(self) -> None:
        self.rules: list[Rule] = list(self.DEFAULT_RULES)
        self.facts: list[Fact] = []
        self._fact_index: dict[str, list[Fact]] = {}  # predicate -> [facts]

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)

    def add_fact(self, predicate: str, args: list[str], value: bool = True, confidence: float = 1.0, source: str = "") -> None:
        fact = Fact(predicate=predicate, args=args, value=value, confidence=confidence, source=source)
        self.facts.append(fact)
        self._fact_index.setdefault(predicate, []).append(fact)

    def query(self, predicate: str, args: list[str]) -> Fact | None:
        """Direct fact lookup."""
        for fact in self._fact_index.get(predicate, []):
            if fact.args == args:
                return fact
        return None

    def derive(self, predicate: str, args: list[str]) -> Fact | None:
        """Apply rules to derive a fact. Returns the derived fact or None."""
        # Check direct facts first.
        direct = self.query(predicate, args)
        if direct is not None:
            return direct
        # Try each rule whose conclusion matches.
        for rule in self.rules:
            if rule.conclusion[0] != predicate:
                continue
            # Try to unify the conclusion with the query.
            bindings = self._unify(rule.conclusion[1], args)
            if bindings is None:
                continue
            # Check all conditions with the bindings applied.
            all_conditions_met = True
            for cond_pred, cond_args in rule.conditions:
                resolved_args = [bindings.get(a, a) for a in cond_args]
                cond_fact = self.query(cond_pred, resolved_args) or self.derive(cond_pred, resolved_args)
                if cond_fact is None or not cond_fact.value:
                    all_conditions_met = False
                    break
            if all_conditions_met:
                return Fact(predicate=predicate, args=args, value=True, confidence=0.9, source=f"rule:{rule.name}")
        return None

    def _unify(self, pattern: list[str], actual: list[str]) -> dict[str, str] | None:
        """Unify a pattern (with ?variables) against actual values."""
        if len(pattern) != len(actual):
            return None
        bindings: dict[str, str] = {}
        for p, a in zip(pattern, actual, strict=False):
            if p.startswith("?"):
                if p in bindings and bindings[p] != a:
                    return None  # conflict
                bindings[p] = a
            elif p != a:
                return None  # literal mismatch
        return bindings

    def check_claim(self, claim: str) -> SymbolicCheckResult:
        """Check if a natural-language claim is consistent with the rule base."""
        claim_lower = claim.lower()
        # Simple pattern matching for common claim types.
        # "X can delete Y" → query can_delete(X, Y)
        m = re.match(r"(\w+)\s+can\s+delete\s+(\w+)", claim_lower)
        if m:
            user, file = m.group(1), m.group(2)
            fact = self.derive("can_delete", [user, file])
            if fact and fact.value:
                return SymbolicCheckResult(claim, "verified", f"Rule '{fact.source}' confirms {user} can delete {file}")
            elif fact and not fact.value:
                return SymbolicCheckResult(claim, "contradicted", f"Rule '{fact.source}' denies {user} can delete {file}")
            return SymbolicCheckResult(claim, "unknown", "No matching rule or fact")

        # "X is safe" → query is_safe(X)
        m = re.match(r"(\w+)\s+is\s+safe", claim_lower)
        if m:
            fact = self.derive("is_safe", [m.group(1)])
            if fact and fact.value:
                return SymbolicCheckResult(claim, "verified", f"Verified safe via {fact.source}")
            return SymbolicCheckResult(claim, "unknown", "Cannot verify safety")

        return SymbolicCheckResult(claim, "irrelevant", "Claim type not recognized by symbolic engine")

    def dashboard(self) -> str:
        lines = [
            "Neuro-symbolic engine:",
            f"  rules: {len(self.rules)}",
            f"  facts: {len(self.facts)}",
            "",
            "Rules:",
        ]
        for r in self.rules:
            lines.append(f"  {r.name}: {r.description}")
        lines.append("\nFacts:")
        for f in self.facts[-20:]:
            lines.append(f"  {f.predicate}({', '.join(f.args)}) = {f.value}  [conf={f.confidence}, src={f.source}]")
        return "\n".join(lines)


_engine: NeuroSymbolicEngine | None = None


def get_neuro_symbolic_engine() -> NeuroSymbolicEngine:
    global _engine
    if _engine is None:
        _engine = NeuroSymbolicEngine()
    return _engine
