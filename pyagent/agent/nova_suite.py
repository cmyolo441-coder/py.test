"""Nova Suite — 71 ultra-advanced local intelligence features.

Nova is the fifth automatic startup layer.  It wires 10 real working modules plus
this orchestrator into a 71-feature suite for symbol intelligence, similarity,
change forecasting, workflow DAGs, docs, quality, memory, prompts, execution
planning and insight synthesis.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .nova_change import forecast_changes, impact_context
from .nova_docs import analyze_docs, docs_context
from .nova_execution import build_execution_plan, execution_context, reorder_for_speed
from .nova_insights import insight_context, recommendations, synthesize_insights
from .nova_memory import compare_latest, remember_snapshot
from .nova_prompt import build_prompt_pack
from .nova_quality import analyze_quality, quality_backlog, quality_context
from .nova_similarity import analyze_similarity, similarity_context
from .nova_symbols import build_symbol_graph, symbol_context
from .nova_workflow import build_workflow, workflow_context


@dataclass(frozen=True)
class NovaFeature:
    id: int
    category: str
    name: str
    description: str


_ROWS: list[tuple[str, str, str]] = [
    # Symbol Intelligence (1-7)
    ("Symbol Intelligence", "Rich AST symbol graph", "Extract classes, functions and methods with qualnames."),
    ("Symbol Intelligence", "Signature extraction", "Capture function signatures for context and API maps."),
    ("Symbol Intelligence", "Decorator map", "Track decorators on public symbols."),
    ("Symbol Intelligence", "Call dependency closure", "Trace local function/method call dependencies."),
    ("Symbol Intelligence", "Reverse caller map", "Find likely callers of important symbols."),
    ("Symbol Intelligence", "Centrality ranking", "Rank symbols by call in/out centrality."),
    ("Symbol Intelligence", "Symbol query context", "Generate symbol context for a natural-language query."),
    # Similarity Intelligence (8-14)
    ("Similarity Intelligence", "Token shingling", "Build token shingles for supported text/code files."),
    ("Similarity Intelligence", "Jaccard clone scoring", "Score similar files with set similarity."),
    ("Similarity Intelligence", "Duplicate cluster builder", "Group similar paths into clone clusters."),
    ("Similarity Intelligence", "Cross-file coupling signal", "Feed similarity coupling into impact scoring."),
    ("Similarity Intelligence", "Top token profile", "Report high-frequency repository terms."),
    ("Similarity Intelligence", "Extension-aware comparison", "Avoid noisy cross-language clone matches."),
    ("Similarity Intelligence", "Similarity context pack", "Render concise similar-file context."),
    # Change Forecasting (15-21)
    ("Change Forecasting", "Impact score engine", "Score files by symbols, callers, imports and similarity."),
    ("Change Forecasting", "Patch scenario builder", "Generate small/medium/high-risk file scopes."),
    ("Change Forecasting", "Test priority map", "Guess tests for high-impact files."),
    ("Change Forecasting", "Reason-tagged impact", "Explain why each file is risky."),
    ("Change Forecasting", "Path-specific forecast", "Filter forecasts for selected files/symbols."),
    ("Change Forecasting", "Blast-radius summary", "Summarize likely change blast radius."),
    ("Change Forecasting", "Impact context injection", "Inject top impact forecasts into prompts."),
    # Workflow DAG (22-28)
    ("Workflow DAG", "Topological workflow planner", "Build dependency-aware engineering workflow nodes."),
    ("Workflow DAG", "DAG validation", "Detect missing dependencies and cycles."),
    ("Workflow DAG", "Verification node generation", "Attach inferred commands as verify nodes."),
    ("Workflow DAG", "Hot-file workflow metadata", "Carry impact files into workflow context."),
    ("Workflow DAG", "Workflow merge", "Merge multiple workflows into one combined DAG."),
    ("Workflow DAG", "Workflow stats", "Report nodes, commands and validity."),
    ("Workflow DAG", "Workflow context renderer", "Render workflow steps for model context."),
    # Docs Intelligence (29-35)
    ("Docs Intelligence", "Markdown heading inventory", "Scan docs headings and locations."),
    ("Docs Intelligence", "Docstring coverage", "Estimate public symbol docstring coverage."),
    ("Docs Intelligence", "Missing docstring list", "List public symbols lacking docstrings."),
    ("Docs Intelligence", "Suggested docs sections", "Suggest missing README/architecture sections."),
    ("Docs Intelligence", "TOC generator", "Generate heading-based table of contents."),
    ("Docs Intelligence", "Docs context renderer", "Render docs status for prompt context."),
    ("Docs Intelligence", "API docs pressure", "Flag low documentation coverage."),
    # Quality Intelligence (36-42)
    ("Quality Intelligence", "Per-file quality score", "Score files using size, docs, comments and function shape."),
    ("Quality Intelligence", "Worst-file ranking", "Rank low-quality files for improvement."),
    ("Quality Intelligence", "Long-line pressure", "Detect excessive very-long lines."),
    ("Quality Intelligence", "Comment-ratio signal", "Detect comment/doc scarcity in dense modules."),
    ("Quality Intelligence", "Average function length", "Track function length by file."),
    ("Quality Intelligence", "Quality backlog", "Generate actionable quality backlog items."),
    ("Quality Intelligence", "Quality prompt context", "Render compact quality context."),
    # Memory/Drift (43-49)
    ("Memory Drift", "Canonical snapshot JSON", "Serialize snapshots deterministically."),
    ("Memory Drift", "Snapshot fingerprint", "Hash analysis payloads for drift checks."),
    ("Memory Drift", "Local snapshot memory", "Remember Nova startup snapshots locally."),
    ("Memory Drift", "Latest snapshot compare", "Compare current snapshot to previous one."),
    ("Memory Drift", "History compaction", "Keep compact history of analysis records."),
    ("Memory Drift", "Drift metadata", "Attach current/previous ids to context."),
    ("Memory Drift", "Handoff export support", "Export snapshot JSON for handoff/debugging."),
    # Prompt Optimisation (50-56)
    ("Prompt Optimisation", "Prompt task classifier", "Classify prompts for context weighting."),
    ("Prompt Optimisation", "Weighted budget allocator", "Allocate prompt space across sections."),
    ("Prompt Optimisation", "Deduplicated sections", "Remove repeated context lines."),
    ("Prompt Optimisation", "Priority prompt pack", "Sort context sections by task priority."),
    ("Prompt Optimisation", "Macro library", "Provide Nova fix/refactor/test/docs/release macros."),
    ("Prompt Optimisation", "Macro expansion", "Expand macros into detailed engineering prompts."),
    ("Prompt Optimisation", "Rendered prompt pack", "Render bounded Nova context packets."),
    # Execution Planning (57-63)
    ("Execution Planning", "Command risk classification", "Classify commands as low/medium/high risk."),
    ("Execution Planning", "Purpose inference", "Infer why each verification command exists."),
    ("Execution Planning", "Prerequisite hints", "Attach dependencies/docker prerequisites."),
    ("Execution Planning", "Safe preview only", "Plan commands without executing them."),
    ("Execution Planning", "Speed reorder", "Prefer low-risk quick checks first."),
    ("Execution Planning", "Shell token parsing", "Parse command tokens robustly."),
    ("Execution Planning", "Execution context renderer", "Render command plan for model context."),
    # Insight Fusion (64-70)
    ("Insight Fusion", "Multi-report synthesis", "Combine impact, docs, quality, tests and runtime."),
    ("Insight Fusion", "Priority insight ranking", "Rank recommendations by priority."),
    ("Insight Fusion", "Category-specific advice", "Generate advice for tests/refactor/docs/impact/runtime."),
    ("Insight Fusion", "Top insight dashboard", "Summarize top repository pressure points."),
    ("Insight Fusion", "Recommendation dedupe", "Deduplicate repeated recommendations."),
    ("Insight Fusion", "Nova context synthesis", "Compose one compact Nova context packet."),
    ("Insight Fusion", "Zero-command warmup", "Warm all Nova intelligence automatically on startup."),
    # Orchestrator (71)
    ("Nova Orchestrator", "71-feature autostart fusion", "Activate and attach all Nova modules from python3 main.py."),
]

NOVA_FEATURES_71: tuple[NovaFeature, ...] = tuple(NovaFeature(i + 1, c, n, d) for i, (c, n, d) in enumerate(_ROWS))
assert len(NOVA_FEATURES_71) == 71, f"expected 71 features, got {len(NOVA_FEATURES_71)}"


@dataclass
class NovaSnapshot:
    root: str
    started_at: float
    duration_s: float
    features: int
    symbols: dict[str, Any] = field(default_factory=dict)
    similarity: dict[str, Any] = field(default_factory=dict)
    impact: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    docs: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    insights: dict[str, Any] = field(default_factory=dict)
    drift: dict[str, Any] = field(default_factory=dict)
    prompt_pack: str = ""
    recommendations: list[str] = field(default_factory=list)

    def to_context(self, max_chars: int = 3200) -> str:
        parts = [
            "[NOVA SUITE CONTEXT]",
            f"features={self.features}, duration={self.duration_s:.2f}s",
            f"symbols={self.symbols.get('stats', {})}",
            f"similarity={self.similarity.get('stats', {})}",
            f"impact={self.impact.get('stats', {})}",
            f"docs={self.docs.get('stats', {})}",
            f"quality={self.quality.get('stats', {})}",
            f"execution={self.execution.get('stats', {})}",
        ]
        if self.recommendations:
            parts.append("recommendations=" + "; ".join(self.recommendations[:8]))
        if self.drift:
            parts.append("drift=" + str(self.drift)[:350])
        if self.prompt_pack:
            parts.append(self.prompt_pack[:1800])
        parts.append("[/NOVA SUITE CONTEXT]")
        return "\n".join(parts)[:max_chars]


def nova_feature_count() -> int:
    return len(NOVA_FEATURES_71)


def by_category() -> dict[str, list[NovaFeature]]:
    grouped: dict[str, list[NovaFeature]] = {}
    for feature in NOVA_FEATURES_71:
        grouped.setdefault(feature.category, []).append(feature)
    return grouped


def activate_nova_mode(app: Any | None = None) -> dict[str, Any]:
    summary = {"features": nova_feature_count(), "categories": len(by_category()), "safety": "guardrails stay enabled"}
    if app is not None:
        setattr(app, "nova_features", NOVA_FEATURES_71)
        setattr(app, "nova_profile_active", True)
    return summary


def run_nova_warmup(app: Any | None = None, root: str | Path = ".") -> NovaSnapshot:
    activate_nova_mode(app)
    root_path = Path(root).resolve()
    started = time.time()
    t0 = time.perf_counter()

    graph = build_symbol_graph(root_path)
    sim = analyze_similarity(root_path)
    forecast = forecast_changes(graph, sim)
    docs_report = analyze_docs(root_path)
    quality_report = analyze_quality(root_path)

    commands = ["python -m pytest -q", "python scripts/healthcheck.py", "python -m compileall -q agent scripts tests"]
    exec_plan = reorder_for_speed(build_execution_plan(commands))
    workflow = build_workflow("Nova automatic engineering workflow", commands, [p for p, _s in forecast.stats().get("top", [])[:8]])

    data_for_insights = {
        "quality": {"stats": quality_report.stats()},
        "refactor": {"stats": {"findings": quality_report.stats().get("issues", 0)}},
        "tests": {"stats": {"missing_tests": len(forecast.scenarios.get("test_priority", []))}},
        "docs": {"stats": docs_report.stats()},
        "impact": {"stats": forecast.stats()},
        "runtime": {"stats": {"commands": commands}},
    }
    insights_report = synthesize_insights(data_for_insights)
    recs = [*recommendations(insights_report), *quality_backlog(quality_report, limit=5)]

    sources = {
        "symbols": symbol_context(graph, "agent", limit=15),
        "impact": impact_context(forecast, limit=12),
        "docs": docs_context(docs_report),
        "quality": quality_context(quality_report),
        "plan": workflow_context(workflow),
        "runtime": execution_context(exec_plan),
        "tests": "test priorities: " + ", ".join(forecast.scenarios.get("test_priority", [])[:12]),
    }
    prompt_pack = build_prompt_pack("Improve this terminal AI agent", sources, budget=6500).render()

    payload = {
        "symbols": graph.stats(),
        "similarity": sim.stats(),
        "impact": forecast.stats(),
        "docs": docs_report.stats(),
        "quality": quality_report.stats(),
        "execution": exec_plan.stats(),
        "recommendations": recs[:12],
    }
    drift = compare_latest(payload, kind="nova")
    remember_snapshot(payload, kind="nova")

    snapshot = NovaSnapshot(
        root=str(root_path),
        started_at=started,
        duration_s=time.perf_counter() - t0,
        features=nova_feature_count(),
        symbols={"stats": graph.stats(), "context": sources["symbols"]},
        similarity={"stats": sim.stats(), "context": similarity_context(sim)},
        impact={"stats": forecast.stats(), "context": sources["impact"]},
        workflow={"stats": workflow.stats(), "context": workflow_context(workflow)},
        docs={"stats": docs_report.stats(), "context": sources["docs"]},
        quality={"stats": quality_report.stats(), "context": sources["quality"]},
        execution={"stats": exec_plan.stats(), "context": sources["runtime"]},
        insights={"stats": insights_report.stats(), "context": insight_context(insights_report)},
        drift=drift,
        prompt_pack=prompt_pack,
        recommendations=list(dict.fromkeys(recs))[:15],
    )
    if app is not None:
        setattr(app, "nova_snapshot", snapshot)
    return snapshot


def dashboard(limit: int | None = None) -> str:
    lines = [
        "╔════════════════════════════════════════════════════════════╗",
        f"║  NOVA SUITE: {nova_feature_count()} ULTRA FEATURES ACTIVE{'':<17}║",
        "╠════════════════════════════════════════════════════════════╣",
    ]
    for category, features in by_category().items():
        lines.append(f"║  {category:<23} {len(features):>3} capabilities{'':<18}║")
    lines.append("╚════════════════════════════════════════════════════════════╝")
    lines.append("")
    shown = 0
    for category, features in by_category().items():
        lines.append(f"[{category}]")
        for feature in features:
            if limit is not None and shown >= limit:
                lines.append(f"… {nova_feature_count() - shown} more Nova features.")
                return "\n".join(lines)
            lines.append(f"  N{feature.id:02d}. {feature.name} — {feature.description}")
            shown += 1
        lines.append("")
    return "\n".join(lines).rstrip()


def export_snapshot(snapshot: NovaSnapshot, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot.__dict__, indent=2, default=str), encoding="utf-8")
    return out
