"""Effort levels that control how hard the agent works on a task.

Inspired by Claude Code's effort/reasoning modes. Each level tunes how many
planning, execution and verification passes the autonomous engine performs.

  normal      — single pass, quick answers.
  ultramax    — heavy: deep planning + execution + 1 verification pass.
  ultracombo  — enterprise: research + plan + verify-plan + execute + 2 passes.
  ultrahype   — maximum: full autonomous loop until the task is truly complete,
                with fake-code detection and repeated self-verification.
  enterprise  — full SDLC: architecture, security audit, tests, docs, perf,
                adversarial review and multi-sample self-consistency.
  godmode     — absolute maximum: exhaustive research/design + every quality
                pass, looping until the result is provably perfect.

Each level also exposes advanced controls (self-consistency sampling,
adversarial review, architecture/security/performance/docs passes and a
working-context budget) consumed by the autonomous engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EffortLevel:
    name: str
    research: bool          # do a deep-research pass first
    plan: bool              # produce an explicit plan
    verify_plan: bool       # critique/verify the plan before executing
    max_execution_rounds: int
    verification_passes: int
    detect_fake: bool       # scan for placeholder/simulated code
    max_fix_iterations: int
    temperature: float
    description: str
    # --- advanced / enterprise controls (backward compatible defaults) ---
    self_consistency: int = 1         # sample N candidate solutions, pick best
    adversarial_review: bool = False  # red-team the result for security/bugs
    architecture_pass: bool = False   # explicit high-level design pass
    test_generation: bool = False     # generate & reason about tests
    security_audit: bool = False      # dedicated security/threat pass
    performance_pass: bool = False    # profile & optimize pass
    docs_pass: bool = False           # produce documentation
    max_context_tokens: int = 8000    # working-context budget
    escalate_on_failure: bool = False  # bump effort if verification keeps failing

    @property
    def rank(self) -> int:
        """Relative intensity, used for escalation and display."""
        return _ORDER.index(self.name) if self.name in _ORDER else 0


LEVELS: dict[str, EffortLevel] = {
    "normal": EffortLevel(
        "normal", False, False, False, 1, 0, False, 0, 0.7,
        "Quick single-pass responses.",
        self_consistency=1, max_context_tokens=8000,
    ),
    "ultramax": EffortLevel(
        "ultramax", False, True, False, 5, 1, True, 2, 0.5,
        "Heavy work for complex projects: plan, execute, verify once.",
        self_consistency=1, architecture_pass=True, test_generation=True,
        max_context_tokens=16000,
    ),
    "ultracombo": EffortLevel(
        "ultracombo", True, True, True, 12, 2, True, 4, 0.4,
        "Enterprise-grade: research, verified plan, execute, verify twice.",
        self_consistency=2, adversarial_review=True, architecture_pass=True,
        test_generation=True, docs_pass=True, max_context_tokens=32000,
    ),
    "ultrahype": EffortLevel(
        "ultrahype", True, True, True, 40, 3, True, 8, 0.3,
        "Maximum autonomy: full end-to-end loop until truly complete.",
        self_consistency=3, adversarial_review=True, architecture_pass=True,
        test_generation=True, security_audit=True, performance_pass=True,
        docs_pass=True, max_context_tokens=64000, escalate_on_failure=True,
    ),
    "enterprise": EffortLevel(
        "enterprise", True, True, True, 60, 4, True, 12, 0.25,
        "Enterprise SDLC: architecture, security audit, tests, docs, perf, "
        "adversarial review, multi-sample self-consistency.",
        self_consistency=4, adversarial_review=True, architecture_pass=True,
        test_generation=True, security_audit=True, performance_pass=True,
        docs_pass=True, max_context_tokens=96000, escalate_on_failure=True,
    ),
    "godmode": EffortLevel(
        "godmode", True, True, True, 120, 6, True, 24, 0.2,
        "Absolute maximum: exhaustive research, design, self-consistency, "
        "security + performance + adversarial passes, loop until perfect.",
        self_consistency=6, adversarial_review=True, architecture_pass=True,
        test_generation=True, security_audit=True, performance_pass=True,
        docs_pass=True, max_context_tokens=200000, escalate_on_failure=True,
    ),
}

# Ordered from lightest to heaviest (used for escalation).
_ORDER: list[str] = ["normal", "ultramax", "ultracombo", "ultrahype", "enterprise", "godmode"]


def get_effort(name: str) -> EffortLevel:
    return LEVELS.get(name.lower(), LEVELS["normal"])


def list_efforts() -> list[str]:
    return list(LEVELS)


def next_effort(name: str) -> EffortLevel:
    """Return the next heavier effort level (clamped at the maximum)."""
    key = name.lower()
    idx = _ORDER.index(key) if key in _ORDER else 0
    return LEVELS[_ORDER[min(idx + 1, len(_ORDER) - 1)]]
