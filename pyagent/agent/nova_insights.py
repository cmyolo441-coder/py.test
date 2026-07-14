"""Nova insight synthesis."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Insight:
    title: str
    detail: str
    priority: int = 50
    category: str = "general"


@dataclass
class InsightReport:
    insights: list[Insight] = field(default_factory=list)

    def top(self, limit: int = 12) -> list[Insight]:
        return sorted(self.insights, key=lambda i: -i.priority)[:limit]

    def stats(self) -> dict[str, Any]:
        return {"insights": len(self.insights), "top": [(i.title, i.priority) for i in self.top(10)]}


def synthesize_insights(data: dict[str, Any]) -> InsightReport:
    insights: list[Insight] = []
    quality = data.get("quality", {}).get("stats", {})
    refactor = data.get("refactor", {}).get("stats", {})
    tests = data.get("tests", {}).get("stats", {})
    docs = data.get("docs", {}).get("stats", {})
    impact = data.get("impact", {}).get("stats", {})
    runtime = data.get("runtime", {}).get("stats", {})

    if quality.get("overall_score", 100) < 75:
        insights.append(Insight("Quality pressure", f"overall score {quality.get('overall_score')}", 90, "quality"))
    if refactor.get("findings", 0) > 20:
        insights.append(Insight("Refactor backlog", f"{refactor.get('findings')} refactor findings", 85, "refactor"))
    if tests.get("missing_tests", 0) > 10:
        insights.append(Insight("Test gaps", f"{tests.get('missing_tests')} source files likely missing tests", 88, "tests"))
    if docs.get("doc_coverage", 1.0) < 0.6:
        insights.append(Insight("Docs/API gap", f"docstring coverage {docs.get('doc_coverage')}", 70, "docs"))
    hot = impact.get("top", [])
    if hot:
        insights.append(Insight("Impact hotspot", f"top impacted file {hot[0][0]} score={hot[0][1]}", 92, "impact"))
    if runtime.get("commands"):
        insights.append(Insight("Verification ready", f"first command: {runtime['commands'][0]}", 65, "runtime"))
    if not insights:
        insights.append(Insight("Repository ready", "No major Nova pressure detected; focus on targeted improvements.", 60, "general"))
    return InsightReport(insights)


def insight_context(report: InsightReport, limit: int = 10) -> str:
    lines = ["nova insights:"]
    for ins in report.top(limit):
        lines.append(f"- [{ins.priority}] {ins.category}: {ins.title} — {ins.detail}")
    return "\n".join(lines)


def recommendations(report: InsightReport, limit: int = 10) -> list[str]:
    recs: list[str] = []
    for ins in report.top(limit):
        if ins.category == "tests":
            recs.append("Add focused tests for likely uncovered modules.")
        elif ins.category == "refactor":
            recs.append("Refactor the highest-score complexity hotspot first.")
        elif ins.category == "quality":
            recs.append("Improve worst quality files before large feature work.")
        elif ins.category == "docs":
            recs.append("Add docstrings/API documentation for public symbols.")
        elif ins.category == "impact":
            recs.append("Review impact hotspot before editing dependent modules.")
        elif ins.category == "runtime":
            recs.append("Run the first verification command after changes.")
    return list(dict.fromkeys(recs))[:limit]
