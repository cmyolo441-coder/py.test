"""Autonomous engine: research → plan → verify → execute → analyze → fix → verify.

Driven by the selected EffortLevel. For high-effort levels (ultrahype) it loops
until the task is genuinely complete, detecting fake/simulated code and asking
the model to make it real, verifying the result multiple times.

The engine is provider-agnostic: it calls a ``chat`` function you supply, so it
works with any real LLM backend configured in the app. It never fabricates
results — all reasoning is produced by the underlying model.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .effort import EffortLevel
from .fake_detector import report as fake_report
from .fake_detector import scan_text
from .systemprompts import AUTONOMOUS

# A chat function takes messages and returns the assistant's text reply.
ChatFn = Callable[[list[dict[str, Any]]], str]


@dataclass
class Phase:
    name: str
    output: str


@dataclass
class RunResult:
    task: str
    effort: str
    phases: list[Phase] = field(default_factory=list)
    final: str = ""
    complete: bool = False

    def summary(self) -> str:
        lines = [f"Task: {self.task}", f"Effort: {self.effort}",
                 f"Complete: {self.complete}", ""]
        for p in self.phases:
            lines.append(f"== {p.name} ==")
            lines.append(p.output.strip())
            lines.append("")
        return "\n".join(lines)


class AutonomousEngine:
    """Orchestrates a full autonomous work loop using a real chat backend."""

    def __init__(self, chat_fn: ChatFn, effort: EffortLevel) -> None:
        self.chat = chat_fn
        self.effort = effort

    def _ask(self, system: str, user: str) -> str:
        return self.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}]
        )

    def research(self, task: str) -> str:
        return self._ask(AUTONOMOUS["research"], f"Task:\n{task}")

    def plan(self, task: str, research: str) -> str:
        return self._ask(
            AUTONOMOUS["plan"],
            f"Task:\n{task}\n\nResearch:\n{research}",
        )

    def verify_plan(self, task: str, plan: str) -> tuple[bool, str]:
        critique = self._ask(
            AUTONOMOUS["verify_plan"],
            f"Task:\n{task}\n\nPlan:\n{plan}",
        )
        return critique.strip().upper().startswith("APPROVED"), critique

    def execute(self, task: str, plan: str, prior: str = "") -> str:
        return self._ask(
            AUTONOMOUS["execute"],
            f"Task:\n{task}\n\nPlan:\n{plan}\n\nPrior work to build on:\n{prior}",
        )

    def analyze(self, task: str, work: str) -> tuple[bool, str]:
        verdict = self._ask(
            AUTONOMOUS["analyze"],
            f"Task:\n{task}\n\nWork:\n{work}",
        )
        return verdict.strip().upper().startswith("COMPLETE"), verdict

    def fix(self, task: str, work: str, issues: str) -> str:
        return self._ask(
            AUTONOMOUS["fix"],
            f"Task:\n{task}\n\nCurrent work:\n{work}\n\nIssues to fix:\n{issues}",
        )

    def make_real(self, task: str, work: str, findings: str) -> str:
        return self._ask(
            AUTONOMOUS["make_real"],
            f"Task:\n{task}\n\nWork:\n{work}\n\nDetected issues:\n{findings}",
        )

    def run(self, task: str) -> RunResult:
        """Run the full autonomous loop according to the effort level."""
        e = self.effort
        result = RunResult(task=task, effort=e.name)
        research = ""
        plan = task

        if e.research:
            research = self.research(task)
            result.phases.append(Phase("research", research))

        if e.plan:
            plan = self.plan(task, research)
            result.phases.append(Phase("plan", plan))

        if e.verify_plan:
            approved, critique = self.verify_plan(task, plan)
            result.phases.append(Phase("verify-plan", critique))
            if not approved:
                plan = self.plan(task, research + "\n\nReviewer notes:\n" + critique)
                result.phases.append(Phase("revised-plan", plan))

        work = self.execute(task, plan)
        result.phases.append(Phase("execute", work))

        # Verification + fix iterations.
        for i in range(max(1, e.verification_passes)):
            complete, verdict = self.analyze(task, work)
            result.phases.append(Phase(f"analyze-{i + 1}", verdict))
            if complete:
                result.complete = True
            else:
                if i < e.max_fix_iterations:
                    work = self.fix(task, work, verdict)
                    result.phases.append(Phase(f"fix-{i + 1}", work))

            # Fake/simulated code detection pass.
            if e.detect_fake:
                findings = scan_text(work)
                if findings:
                    result.phases.append(Phase(f"fake-scan-{i + 1}", fake_report(work)))
                    work = self.make_real(task, work, fake_report(work))
                    result.phases.append(Phase(f"make-real-{i + 1}", work))

        result.final = work
        # Final completeness is true only if last analysis passed and no fakes remain.
        result.complete = result.complete and not scan_text(work)
        return result
