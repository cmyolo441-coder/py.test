"""Omega autonomous planning helpers.

Turns local intelligence snapshots into concrete action plans, verification
stages and task-type routing hints.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    id: str
    title: str
    detail: str
    depends_on: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)


@dataclass
class OmegaPlan:
    task_class: str
    confidence: float
    steps: list[PlanStep]
    context_hints: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Omega plan: {self.task_class} ({self.confidence:.2f})"]
        for step in self.steps:
            dep = f" after {', '.join(step.depends_on)}" if step.depends_on else ""
            lines.append(f"  {step.id}. {step.title}{dep} — {step.detail}")
        return "\n".join(lines)


def classify_task(prompt: str) -> dict[str, Any]:
    """Classify a user prompt into a practical engineering task type."""
    text = prompt.lower()
    patterns = {
        "bugfix": ["bug", "fix", "error", "traceback", "fail", "failing", "crash", "issue"],
        "refactor": ["refactor", "clean", "complex", "architecture", "improve", "simplify"],
        "feature": ["add", "implement", "feature", "create", "build", "support"],
        "tests": ["test", "pytest", "coverage", "verify", "regression"],
        "docs": ["doc", "readme", "explain", "guide", "comment"],
        "release": ["release", "deploy", "docker", "ci", "package", "publish"],
        "analysis": ["analyze", "audit", "inspect", "review", "understand", "map"],
    }
    scores = {name: sum(1 for p in pats if p in text) for name, pats in patterns.items()}
    best_name, best_score = max(scores.items(), key=lambda item: item[1])
    total = sum(scores.values()) or 1
    return {"class": best_name if best_score else "general", "confidence": best_score / total, "scores": scores}


def create_plan(prompt: str, intelligence: dict[str, Any] | None = None) -> OmegaPlan:
    """Create a staged plan from a prompt and local intelligence."""
    cls = classify_task(prompt)
    intelligence = intelligence or {}
    commands = intelligence.get("commands") or ["python -m pytest -q"]
    hot_files = [p for p, _score in intelligence.get("hot_files", [])[:5]]
    missing_tests = intelligence.get("missing_tests", [])[:5]
    hints = []
    if hot_files:
        hints.append("hot files: " + ", ".join(hot_files))
    if missing_tests:
        hints.append("test gaps: " + ", ".join(missing_tests))

    steps: list[PlanStep] = [
        PlanStep("P1", "Inspect context", "Read relevant files, symbols, tests and startup analysis."),
        PlanStep("P2", "Design minimal change", "Choose the smallest safe patch scope based on impact map.", depends_on=["P1"]),
    ]
    task_class = cls["class"]
    if task_class == "bugfix":
        steps.extend([
            PlanStep("P3", "Reproduce or reason about failure", "Locate failing path and add/adjust a regression check.", depends_on=["P2"]),
            PlanStep("P4", "Patch root cause", "Fix the source path with minimal blast radius.", depends_on=["P3"]),
        ])
    elif task_class == "refactor":
        steps.extend([
            PlanStep("P3", "Preserve behavior", "Identify existing tests or create characterization checks.", depends_on=["P2"]),
            PlanStep("P4", "Refactor incrementally", "Split complexity and keep public API stable.", depends_on=["P3"]),
        ])
    elif task_class == "tests":
        steps.extend([
            PlanStep("P3", "Select uncovered targets", "Prioritize modules with missing/weak tests.", depends_on=["P2"]),
            PlanStep("P4", "Add focused tests", "Cover behavior and edge cases without brittle internals.", depends_on=["P3"]),
        ])
    elif task_class == "docs":
        steps.extend([
            PlanStep("P3", "Map docs gaps", "Compare entrypoints/features against current docs.", depends_on=["P2"]),
            PlanStep("P4", "Update docs", "Add clear usage, architecture and troubleshooting sections.", depends_on=["P3"]),
        ])
    else:
        steps.extend([
            PlanStep("P3", "Implement", "Apply the requested change using indexed context.", depends_on=["P2"]),
            PlanStep("P4", "Review", "Check consistency across commands, tools, docs and tests.", depends_on=["P3"]),
        ])
    steps.append(PlanStep("P5", "Verify", "Run the inferred verification gates.", depends_on=["P4"], verify=commands[:5]))
    return OmegaPlan(task_class=task_class, confidence=float(cls["confidence"]), steps=steps, context_hints=hints)


def action_queue_from_backlog(backlog: list[str], limit: int = 10) -> list[dict[str, Any]]:
    """Convert backlog strings into a prioritized action queue."""
    queue = []
    for idx, item in enumerate(backlog[:limit], 1):
        priority = "high" if idx <= 3 else "medium" if idx <= 7 else "low"
        queue.append({"id": f"A{idx}", "priority": priority, "action": item})
    return queue


def expand_prompt_macro(name: str, subject: str = "this repository") -> str:
    """Expand a high-level macro into a detailed engineering prompt."""
    macros = {
        "fix": f"Find the root cause of failures in {subject}, make the smallest safe patch, and verify it.",
        "refactor": f"Refactor the highest-impact complexity hotspot in {subject} without changing behavior.",
        "tests": f"Add focused regression tests for important uncovered behavior in {subject}.",
        "docs": f"Improve onboarding docs for {subject}, including architecture, run commands and troubleshooting.",
        "review": f"Review {subject} for maintainability, test gaps, runtime surfaces and next actions.",
    }
    return macros.get(name.lower(), f"Work on {subject}: {name}")


def estimate_patch_scope(prompt: str, ranked_paths: list[tuple[str, float]] | list[tuple[str, int]], max_files: int = 6) -> dict[str, Any]:
    """Estimate likely files touched by a task."""
    selected = [path for path, _score in ranked_paths[:max_files]]
    cls = classify_task(prompt)
    return {"task_class": cls["class"], "likely_files": selected, "risk": "high" if len(selected) > 4 else "medium" if len(selected) > 2 else "low"}
