"""Constitutional AI — agent has its own constitution/principles it follows.

The constitution is a set of principles that govern agent behavior. Every
tool call, response, and decision passes through a "constitutional review"
that checks for violations.

Inspired by Anthropic's Constitutional AI paper. Principles are real,
enforced rules — not just prompts. The agent can never violate them.

Example principles:
  - "Never delete user data without explicit confirmation"
  - "Always cite sources for factual claims"
  - "Prefer open-source solutions over proprietary"
  - "Never execute destructive shell commands without --dry-run first"
  - "Always explain what you're about to do before doing it"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PrincipleSeverity(Enum):
    INVIOABLE = "invioable"  # can never be violated
    HIGH = "high"  # violation blocks the action
    MEDIUM = "medium"  # violation warns but allows
    LOW = "low"  # informational


@dataclass
class Principle:
    id: str
    text: str  # the principle in natural language
    severity: PrincipleSeverity
    checker: Any  # callable(action, context) -> (violated: bool, reason: str)
    description: str = ""


@dataclass
class ConstitutionalReview:
    action: str
    allowed: bool
    violations: list[tuple[str, str, PrincipleSeverity]] = field(default_factory=list)  # (principle_id, reason, severity)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_invioable_violation(self) -> bool:
        return any(s == PrincipleSeverity.INVIOABLE for _, _, s in self.violations)

    def summary(self) -> str:
        if not self.violations and not self.warnings:
            return f"✓ Constitutional review passed for: {self.action[:60]}"
        lines = [f"Constitutional review for: {self.action[:60]}"]
        lines.append(f"  allowed: {self.allowed}")
        for pid, reason, sev in self.violations:
            icon = "✗" if sev in (PrincipleSeverity.INVIOABLE, PrincipleSeverity.HIGH) else "⚠"
            lines.append(f"  {icon} [{sev.value}] {pid}: {reason}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


# --- Default principle checkers ---

def _check_no_destructive_without_confirmation(action: str, context: dict) -> tuple[bool, str]:
    """Never delete/destroy without explicit confirmation."""
    destructive_patterns = [
        r"\brm\s+-rf\b", r"\bdelete\b", r"\bdrop\b", r"\btruncate\b",
        r"\bformat\b", r"\bmkfs\b", r"\bshred\b",
    ]
    for pattern in destructive_patterns:
        if re.search(pattern, action, re.IGNORECASE):
            if not context.get("confirmed", False):
                return True, f"Destructive action detected ('{pattern}') without confirmation"
    return False, ""


def _check_explain_before_destructive(action: str, context: dict) -> tuple[bool, str]:
    """Always explain what you're about to do before destructive actions."""
    if context.get("is_destructive", False) and not context.get("explanation", ""):
        return True, "Destructive action attempted without prior explanation"
    return False, ""


def _check_no_secrets_in_output(action: str, context: dict) -> tuple[bool, str]:
    """Never output secrets/API keys."""
    secret_patterns = [
        r"sk-[A-Za-z0-9]{20,}", r"ghp_[A-Za-z0-9]{36}", r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN.*PRIVATE KEY-----",
    ]
    for pattern in secret_patterns:
        if re.search(pattern, action):
            return True, f"Secret detected in output (pattern: {pattern[:20]}...)"
    return False, ""


def _check_prefer_open_source(action: str, context: dict) -> tuple[bool, str]:
    """Prefer open-source solutions (warning, not block)."""
    proprietary = ["mongodb atlas", "aws dynamodb", "stripe", "twilio"]
    for prop in proprietary:
        if prop in action.lower():
            return False, ""  # this is a warning, not a violation
    return False, ""


def _check_safe_file_paths(action: str, context: dict) -> tuple[bool, str]:
    """Never write outside the project directory."""
    if "write_file" in context.get("tool", ""):
        path = context.get("args", {}).get("path", "")
        if path.startswith("..") or path.startswith("/etc") or path.startswith("/usr"):
            return True, f"File write outside project directory: {path}"
    return False, ""


def _check_rate_limit_respect(action: str, context: dict) -> tuple[bool, str]:
    """Respect rate limits — never spam an API."""
    if context.get("recent_calls", 0) > 50:
        return True, f"Rate limit exceeded: {context['recent_calls']} recent calls"
    return False, ""


class Constitution:
    """The agent's constitution — principles it must follow."""

    def __init__(self) -> None:
        self.principles: list[Principle] = [
            Principle(
                id="P001",
                text="Never delete user data without explicit confirmation",
                severity=PrincipleSeverity.INVIOABLE,
                checker=_check_no_destructive_without_confirmation,
                description="Data deletion requires user confirmation",
            ),
            Principle(
                id="P002",
                text="Always explain what you're about to do before destructive actions",
                severity=PrincipleSeverity.HIGH,
                checker=_check_explain_before_destructive,
                description="Transparency before destruction",
            ),
            Principle(
                id="P003",
                text="Never output secrets or API keys",
                severity=PrincipleSeverity.INVIOABLE,
                checker=_check_no_secrets_in_output,
                description="Secret protection",
            ),
            Principle(
                id="P004",
                text="Prefer open-source solutions over proprietary",
                severity=PrincipleSeverity.LOW,
                checker=_check_prefer_open_source,
                description="Open-source preference",
            ),
            Principle(
                id="P005",
                text="Never write files outside the project directory",
                severity=PrincipleSeverity.HIGH,
                checker=_check_safe_file_paths,
                description="Sandbox file writes",
            ),
            Principle(
                id="P006",
                text="Respect rate limits — never spam APIs",
                severity=PrincipleSeverity.MEDIUM,
                checker=_check_rate_limit_respect,
                description="API politeness",
            ),
        ]

    def review(self, action: str, context: dict | None = None) -> ConstitutionalReview:
        """Review an action against all principles. Returns the review result."""
        ctx = context or {}
        review = ConstitutionalReview(action=action, allowed=True)
        for principle in self.principles:
            try:
                violated, reason = principle.checker(action, ctx)
                if violated:
                    review.violations.append((principle.id, reason, principle.severity))
                    if principle.severity in (PrincipleSeverity.INVIOABLE, PrincipleSeverity.HIGH):
                        review.allowed = False
            except Exception as exc:  # noqa: BLE001
                review.warnings.append(f"Principle {principle.id} checker failed: {exc}")
        return review

    def review_tool_call(self, tool_name: str, args: dict, context: dict | None = None) -> ConstitutionalReview:
        """Convenience: review a tool call."""
        ctx = context or {}
        ctx["tool"] = tool_name
        ctx["args"] = args
        action_str = f"{tool_name}({args})"
        return self.review(action_str, ctx)

    def add_principle(self, principle: Principle) -> None:
        self.principles.append(principle)

    def dashboard(self) -> str:
        lines = [
            "╔═══════════════════════════════════════════════════════════╗",
            "║              📜  AGENT CONSTITUTION                        ║",
            "╠═══════════════════════════════════════════════════════════╣",
        ]
        for p in self.principles:
            icon = {"invioable": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}[p.severity.value]
            lines.append(f"║  {icon} {p.id}  {p.text:<46}║")
        lines.append("╚═══════════════════════════════════════════════════════════╝")
        return "\n".join(lines)


_constitution: Constitution | None = None


def get_constitution() -> Constitution:
    global _constitution
    if _constitution is None:
        _constitution = Constitution()
    return _constitution
