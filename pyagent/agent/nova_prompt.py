"""Nova prompt/context optimisation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptSection:
    name: str
    content: str
    priority: int = 50


@dataclass
class PromptPack:
    goal: str
    sections: list[PromptSection] = field(default_factory=list)
    budget: int = 5000

    def render(self) -> str:
        remaining = self.budget
        parts = ["[NOVA PROMPT PACK]", f"goal: {self.goal}"]
        for section in sorted(self.sections, key=lambda s: -s.priority):
            text = f"\n### {section.name}\n{dedupe(section.content)}"
            if len(text) > remaining:
                text = text[: max(0, remaining - 20)] + "\n…[truncated]"
            parts.append(text)
            remaining -= len(text)
            if remaining <= 100:
                break
        parts.append("[/NOVA PROMPT PACK]")
        return "\n".join(parts)


def dedupe(text: str) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        key = re.sub(r"\s+", " ", line.strip().lower())
        if key and key not in seen:
            seen.add(key)
            out.append(line)
    return "\n".join(out)


def classify_prompt(goal: str) -> str:
    g = goal.lower()
    if any(k in g for k in ["fix", "bug", "error", "fail", "traceback"]):
        return "bugfix"
    if any(k in g for k in ["refactor", "clean", "complex"]):
        return "refactor"
    if any(k in g for k in ["test", "pytest", "coverage"]):
        return "tests"
    if any(k in g for k in ["doc", "readme", "explain"]):
        return "docs"
    if any(k in g for k in ["deploy", "release", "docker", "ci"]):
        return "release"
    return "general"


def budget_weights(task_type: str) -> dict[str, int]:
    base = {"symbols": 3, "impact": 3, "tests": 2, "runtime": 1, "docs": 1, "quality": 2, "plan": 3}
    if task_type == "bugfix":
        base["tests"] += 3; base["impact"] += 2
    elif task_type == "refactor":
        base["quality"] += 4; base["symbols"] += 2
    elif task_type == "tests":
        base["tests"] += 5
    elif task_type == "docs":
        base["docs"] += 5
    elif task_type == "release":
        base["runtime"] += 5
    return base


def allocate(total: int, weights: dict[str, int]) -> dict[str, int]:
    s = sum(weights.values()) or 1
    return {k: max(160, int(total * v / s)) for k, v in weights.items()}


def build_prompt_pack(goal: str, sources: dict[str, str], budget: int = 6000) -> PromptPack:
    task = classify_prompt(goal)
    budgets = allocate(budget, budget_weights(task))
    pack = PromptPack(goal=goal, budget=budget)
    priorities = {"plan": 95, "impact": 90, "symbols": 80, "tests": 75, "quality": 70, "runtime": 65, "docs": 60}
    for name, content in sources.items():
        chunk = str(content)[: budgets.get(name, 500)]
        pack.sections.append(PromptSection(name, chunk, priorities.get(name, 50)))
    return pack


def prompt_macros() -> dict[str, str]:
    return {
        "nova-fix": "Use Nova impact, tests and symbol context to fix the root cause with minimal blast radius.",
        "nova-refactor": "Use Nova quality and refactor maps to simplify the highest-risk hotspot safely.",
        "nova-tests": "Use Nova test-gap matrix to add focused regression tests.",
        "nova-docs": "Use Nova docs map to improve onboarding and API documentation.",
        "nova-release": "Use Nova runtime surface to prepare verification and release readiness.",
    }


def expand_macro(name: str, target: str = "the repository") -> str:
    macro = prompt_macros().get(name, name)
    return f"{macro}\nTarget: {target}"
