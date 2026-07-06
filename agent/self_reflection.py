"""Self-reflection and self-critique loop.

After generating a response, the agent critiques its own output, identifies
weaknesses, and regenerates an improved version. This significantly improves
response quality on complex tasks.

Loop: generate -> critique -> improve -> verify -> (repeat up to N times)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .quality_scorer import score_response


@dataclass
class ReflectionIteration:
    iteration: int
    output: str
    critique: str
    quality_score: int
    improved: bool = False


@dataclass
class ReflectionResult:
    original: str
    final: str
    iterations: list[ReflectionIteration] = field(default_factory=list)
    total_duration_s: float = 0.0
    converged: bool = False  # True if quality stopped improving

    @property
    def improvement(self) -> int:
        if not self.iterations:
            return 0
        return self.iterations[-1].quality_score - self.iterations[0].quality_score

    def summary(self) -> str:
        lines = [
            f"Self-reflection: {len(self.iterations)} iteration(s), {self.total_duration_s:.2f}s",
            f"  quality: {self.iterations[0].quality_score if self.iterations else 0} -> "
            f"{self.iterations[-1].quality_score if self.iterations else 0} (+{self.improvement})",
            f"  converged: {self.converged}",
        ]
        return "\n".join(lines)


CRITIC_SYS = (
    "You are a strict self-critique system. Given a prompt and a response, "
    "identify SPECIFIC weaknesses: factual errors, missing context, unclear "
    "explanations, missing edge cases, code bugs, security issues. Be concise "
    "but thorough. List issues as numbered points. If the response is already "
    "excellent, reply 'NO ISSUES FOUND'."
)

IMPROVE_SYS = (
    "You are an improvement system. Given a prompt, a response, and a list of "
    "issues, rewrite the response to fix ALL identified weaknesses. Preserve "
    "what was good. Be complete and precise. Output only the improved response."
)


class SelfReflection:
    """Runs the generate-critique-improve loop."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    def _chat(self, system: str, user: str) -> str:
        if self.app is None:
            return ""
        provider = self.app.agent.provider if getattr(self.app, "agent", None) else None
        if provider is None:
            return ""
        try:
            resp = provider.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
            return getattr(resp, "content", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    def reflect(
        self,
        prompt: str,
        response: str,
        max_iterations: int = 3,
        min_quality: int = 85,
    ) -> ReflectionResult:
        """Run the reflection loop on ``response`` for ``prompt``.

        Stops early if quality >= ``min_quality`` or if it stops improving.
        """
        start = time.perf_counter()
        result = ReflectionResult(original=response, final=response)
        current = response

        for i in range(max_iterations):
            score = score_response(prompt, current)
            iteration = ReflectionIteration(
                iteration=i,
                output=current,
                critique="",
                quality_score=score.score,
            )
            result.iterations.append(iteration)

            if score.score >= min_quality:
                result.converged = True
                result.final = current
                break

            # Critique.
            critique = self._chat(
                CRITIC_SYS,
                f"Prompt:\n{prompt}\n\nResponse:\n{current}",
            )
            iteration.critique = critique

            if "NO ISSUES FOUND" in critique.upper():
                result.converged = True
                result.final = current
                break

            # Improve.
            improved = self._chat(
                IMPROVE_SYS,
                f"Prompt:\n{prompt}\n\nResponse:\n{current}\n\nIssues to fix:\n{critique}",
            )
            if improved and improved.strip():
                iteration.improved = True
                new_score = score_response(prompt, improved)
                # Only accept the improvement if it's actually better.
                if new_score.score > score.score:
                    current = improved
                else:
                    result.converged = True
                    result.final = current
                    break
            else:
                result.converged = True
                result.final = current
                break

        result.final = current
        result.total_duration_s = time.perf_counter() - start
        return result
