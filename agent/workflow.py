"""A minimal workflow engine to chain steps with conditional branching."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Step = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class WorkflowStep:
    name: str
    action: Step
    condition: Callable[[dict[str, Any]], bool] | None = None


class Workflow:
    """Runs a sequence of steps, threading a shared context through them."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.steps: list[WorkflowStep] = []
        self.history: list[str] = []

    def add(self, name: str, action: Step, condition: Callable | None = None) -> "Workflow":
        self.steps.append(WorkflowStep(name, action, condition))
        return self

    def run(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        ctx = dict(context or {})
        for step in self.steps:
            if step.condition is not None and not step.condition(ctx):
                self.history.append(f"skipped:{step.name}")
                continue
            ctx = step.action(ctx)
            self.history.append(f"ran:{step.name}")
        return ctx
