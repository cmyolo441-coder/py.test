"""Multi-agent orchestration — sub-agents that collaborate on complex tasks.

Lets the main agent spawn specialist sub-agents that work in parallel, each
with its own persona, tool access and conversation memory. Results are
merged back into the main conversation.

Built-in specialists:
  * Researcher   — gathers information, cites sources
  * Coder        — writes/modifies code
  * Reviewer     — critiques code for bugs/security/style
  * Tester       — writes and runs tests
  * Planner      — breaks goals into steps
  * Debugger     — diagnoses and fixes bugs

Each sub-agent is a real agent turn with a specialist system prompt.
"""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field

from .logging_config import get_logger

log = get_logger("agent.multi_agent")

SPECIALISTS: dict[str, dict[str, str]] = {
    "researcher": {
        "system": (
            "You are a meticulous research assistant. Gather information thoroughly, "
            "cite sources, distinguish facts from inference. Summarise findings with "
            "bullet points. Cover edge cases and alternative approaches."
        ),
        "description": "Gathers information and cites sources",
    },
    "coder": {
        "system": (
            "You are a senior software engineer. Write production-grade, well-tested, "
            "idiomatic code. Read existing code first to match style. Explain "
            "trade-offs. Always verify your changes run."
        ),
        "description": "Writes and modifies code",
    },
    "reviewer": {
        "system": (
            "You are a strict senior code reviewer. Find bugs, security issues, "
            "performance problems and style violations. Group findings by severity "
            "(CRITICAL/HIGH/MEDIUM/LOW). For each: file:line, description, fix."
        ),
        "description": "Critiques code for bugs and security",
    },
    "tester": {
        "system": (
            "You are a senior test engineer. Write comprehensive tests covering "
            "happy path, edge cases and error conditions. Use the project's "
            "existing test framework. Run tests and ensure they pass."
        ),
        "description": "Writes and runs tests",
    },
    "planner": {
        "system": (
            "You are a principal architect. Break complex goals into concrete, "
            "ordered, actionable steps. Identify dependencies, risks and "
            "estimated complexity per step. Number every step."
        ),
        "description": "Breaks goals into actionable steps",
    },
    "debugger": {
        "system": (
            "You are an expert debugger. Reproduce bugs, diagnose root causes "
            "methodically, propose minimal fixes, verify the fix works, and "
            "check for similar bugs elsewhere. Report: root cause, fix, files changed."
        ),
        "description": "Diagnoses and fixes bugs",
    },
    "writer": {
        "system": (
            "You are a senior technical writer. Produce clear, scannable docs: "
            "short paragraphs, descriptive headings, code examples, callouts "
            "for gotchas. Always include a working quickstart."
        ),
        "description": "Writes documentation",
    },
    "security": {
        "system": (
            "You are an application security auditor. Review for OWASP Top 10, "
            "injection, auth flaws, secret leakage, insecure deps. For each "
            "issue: severity, description, proof, remediation."
        ),
        "description": "Security audit specialist",
    },
}


@dataclass
class SubAgentResult:
    specialist: str
    task: str
    output: str
    duration_s: float
    success: bool = True
    error: str = ""


@dataclass
class OrchestrationResult:
    task: str
    results: list[SubAgentResult] = field(default_factory=list)
    merged_output: str = ""
    total_duration_s: float = 0.0
    parallel: bool = True

    def summary(self) -> str:
        lines = [f"Multi-agent orchestration: '{self.task[:80]}'"]
        lines.append(f"  specialists: {len(self.results)}  parallel: {self.parallel}  total: {self.total_duration_s:.2f}s")
        for r in self.results:
            status = "✓" if r.success else "✗"
            lines.append(f"  {status} {r.specialist:<12} {r.duration_s:.2f}s  {r.task[:60]}")
        return "\n".join(lines)


class MultiAgentOrchestrator:
    """Spawns and coordinates specialist sub-agents."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    def _run_specialist(self, specialist: str, task: str) -> SubAgentResult:
        """Run a single specialist sub-agent turn."""
        spec = SPECIALISTS.get(specialist)
        if spec is None:
            return SubAgentResult(specialist, task, "", 0.0, False, f"unknown specialist: {specialist}")
        start = time.perf_counter()
        # Save and swap the system prompt.
        original_prompt = self.app.config.system_prompt
        self.app.config.system_prompt = spec["system"]
        try:
            # Use the agent's provider directly with a fresh one-shot conversation.
            from .context_manager import compress_messages
            messages = [
                {"role": "system", "content": spec["system"]},
                {"role": "user", "content": task},
            ]
            provider = self.app.agent.provider if self.app.agent else None
            if provider is None:
                return SubAgentResult(specialist, task, "(no agent available)", time.perf_counter() - start, False)
            messages = compress_messages(messages, self.app.config.resolved_model(), self.app.config.provider)
            resp = provider.chat(messages)
            output = getattr(resp, "content", "") or ""
            return SubAgentResult(specialist, task, output, time.perf_counter() - start)
        except Exception as exc:  # noqa: BLE001
            return SubAgentResult(specialist, task, "", time.perf_counter() - start, False, str(exc))
        finally:
            self.app.config.system_prompt = original_prompt

    def run_parallel(self, task: str, specialists: list[str]) -> OrchestrationResult:
        """Run multiple specialists concurrently on the same task."""
        start = time.perf_counter()
        results: list[SubAgentResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(specialists))) as pool:
            futures = {pool.submit(self._run_specialist, s, task): s for s in specialists}
            for fut in concurrent.futures.as_completed(futures, timeout=120):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    s = futures[fut]
                    results.append(SubAgentResult(s, task, "", 0.0, False, str(exc)))
        merged = self._merge_results(task, results)
        return OrchestrationResult(task, results, merged, time.perf_counter() - start, parallel=True)

    def run_sequential(self, task: str, specialists: list[str]) -> OrchestrationResult:
        """Run specialists one after another, each building on the previous."""
        start = time.perf_counter()
        results: list[SubAgentResult] = []
        cumulative = task
        for s in specialists:
            r = self._run_specialist(s, cumulative)
            results.append(r)
            if r.success:
                cumulative = f"Previous specialist ({s}) output:\n{r.output[:2000]}\n\nContinue with: {task}"
        merged = self._merge_results(task, results)
        return OrchestrationResult(task, results, merged, time.perf_counter() - start, parallel=False)

    def run_pipeline(self, task: str) -> OrchestrationResult:
        """Run a standard pipeline: planner -> coder -> reviewer -> tester."""
        return self.run_sequential(task, ["planner", "coder", "reviewer", "tester"])

    def _merge_results(self, task: str, results: list[SubAgentResult]) -> str:
        """Merge sub-agent outputs into a single coherent response."""
        if not results:
            return "(no sub-agent results)"
        if len(results) == 1:
            return results[0].output
        parts = [f"# Multi-agent results for: {task}\n"]
        for r in results:
            icon = "✓" if r.success else "✗"
            parts.append(f"\n## {icon} {r.specialist.title()}\n")
            parts.append(f"_{r.task[:100]}_\n")
            parts.append(r.output[:3000] if r.success else f"ERROR: {r.error}")
        parts.append("\n---\n_Synthesis: combine the above specialist outputs into a final answer._")
        return "\n".join(parts)


def list_specialists() -> list[tuple[str, str]]:
    return [(name, spec["description"]) for name, spec in SPECIALISTS.items()]
