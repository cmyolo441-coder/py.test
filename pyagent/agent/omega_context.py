"""Omega context composer.

Builds compact, deduplicated model context packs from semantic/runtime/test/
refactor/Omega intelligence.  It is purely local and budget-aware.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSection:
    title: str
    body: str
    priority: int = 50


@dataclass
class ContextPack:
    query: str
    budget: int
    sections: list[ContextSection] = field(default_factory=list)

    def render(self) -> str:
        ordered = sorted(self.sections, key=lambda s: -s.priority)
        parts: list[str] = ["[OMEGA CONTEXT PACK]"]
        remaining = self.budget
        for section in ordered:
            header = f"\n## {section.title}\n"
            body = dedupe_lines(section.body.strip())
            chunk = header + body
            if len(chunk) > remaining:
                chunk = chunk[: max(0, remaining - 20)] + "\n…[truncated]"
            if chunk.strip():
                parts.append(chunk)
                remaining -= len(chunk)
            if remaining <= 80:
                break
        parts.append("[/OMEGA CONTEXT PACK]")
        return "\n".join(parts)


def dedupe_lines(text: str) -> str:
    """Remove repeated lines while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        key = re.sub(r"\s+", " ", line.strip()).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out)


def allocate_budget(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Allocate character budget across context section names."""
    weight_sum = sum(max(1, v) for v in weights.values()) or 1
    return {name: max(200, int(total * max(1, weight) / weight_sum)) for name, weight in weights.items()}


def compose_context_pack(query: str, intelligence: dict[str, Any], budget: int = 4500) -> ContextPack:
    """Compose a query-aware Omega context pack."""
    pack = ContextPack(query=query, budget=budget)
    budgets = allocate_budget(budget, {"semantic": 4, "tests": 2, "runtime": 2, "refactor": 2, "plan": 3})

    semantic = intelligence.get("semantic", {})
    ranked = semantic.get("ranked", [])
    if ranked:
        body = "\n".join(f"- {path} score={score}" for path, score in ranked[:12])
        pack.sections.append(ContextSection("Semantic path ranking", body[: budgets["semantic"]], 90))

    symbols = semantic.get("symbols", [])
    if symbols:
        pack.sections.append(ContextSection("Public symbols", "\n".join(f"- {s}" for s in symbols[:25]), 75))

    tests = intelligence.get("tests", {})
    if tests:
        body = []
        stats = tests.get("stats", {})
        body.append(f"stats={stats}")
        for gap in tests.get("gaps", [])[:12]:
            body.append(f"gap: {gap}")
        for cmd in stats.get("commands", [])[:6]:
            body.append(f"verify: {cmd}")
        pack.sections.append(ContextSection("Test intelligence", "\n".join(body)[: budgets["tests"]], 80))

    runtime = intelligence.get("runtime", {})
    if runtime:
        body = runtime.get("brief") or str(runtime.get("stats", {}))
        pack.sections.append(ContextSection("Runtime surface", body[: budgets["runtime"]], 70))

    refactor = intelligence.get("refactor", {})
    if refactor:
        body = "\n".join(refactor.get("backlog", [])[:12]) or str(refactor.get("stats", {}))
        pack.sections.append(ContextSection("Refactor pressure", body[: budgets["refactor"]], 65))

    plan = intelligence.get("plan", {})
    if plan:
        body = plan.get("summary", str(plan))
        pack.sections.append(ContextSection("Suggested plan", body[: budgets["plan"]], 95))

    return pack


def prompt_budget_strategy(query: str, max_chars: int = 6000) -> dict[str, int]:
    """Return a section budget strategy based on task wording."""
    q = query.lower()
    weights = {"semantic": 4, "tests": 2, "runtime": 1, "refactor": 1, "plan": 3}
    if any(k in q for k in ["test", "pytest", "coverage"]):
        weights["tests"] += 4
    if any(k in q for k in ["refactor", "complex", "clean"]):
        weights["refactor"] += 4
    if any(k in q for k in ["deploy", "docker", "ci", "release"]):
        weights["runtime"] += 4
    return allocate_budget(max_chars, weights)


def compress_for_prompt(text: str, max_chars: int = 3000) -> str:
    """Deduplicate and trim text for prompt injection."""
    clean = dedupe_lines(text)
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 20] + "\n…[truncated]"
