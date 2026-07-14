"""Omega Suite — 49 next-level real working capabilities.

Omega combines seven focused modules (semantic, planner, refactor, tests,
runtime, context and suite orchestration).  Each module contributes seven real
local capabilities, for 49 additional features that warm automatically on
`python3 main.py`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .omega_context import compose_context_pack, prompt_budget_strategy
from .omega_planner import action_queue_from_backlog, create_plan, estimate_patch_scope
from .omega_refactor import analyze_refactors, refactor_backlog, refactor_stats
from .omega_runtime import analyze_runtime, runtime_brief
from .omega_semantic import build_semantic_index, search_index, symbol_summary
from .omega_tests import analyze_tests, pytest_targets_for_files, test_gap_summary


@dataclass(frozen=True)
class OmegaFeature:
    id: int
    category: str
    name: str
    description: str


_OMEGA_ROWS: list[tuple[str, str, str]] = [
    # Semantic Kernel (1-7)
    ("Semantic Kernel", "Token inverted index", "Build a local token-to-file search index."),
    ("Semantic Kernel", "AST symbol extraction", "Extract classes/functions/methods with locations."),
    ("Semantic Kernel", "Symbol lookup table", "Resolve symbol names and qualified names quickly."),
    ("Semantic Kernel", "Query path ranking", "Rank repository paths against natural-language prompts."),
    ("Semantic Kernel", "Public symbol summary", "Generate compact public API context lines."),
    ("Semantic Kernel", "Content fingerprinting", "Hash file content prefixes for drift checks."),
    ("Semantic Kernel", "Semantic stats", "Report documents, terms, symbols and lookup size."),
    # Planner Core (8-14)
    ("Planner Core", "Prompt task classifier", "Classify requests as bugfix/refactor/tests/docs/release/etc."),
    ("Planner Core", "Staged engineering plan", "Generate inspect/design/patch/verify steps."),
    ("Planner Core", "Context hint extraction", "Attach hot files and test gaps to plans."),
    ("Planner Core", "Action queue generator", "Convert backlog into prioritized action items."),
    ("Planner Core", "Prompt macro expansion", "Expand fix/refactor/tests/docs/review macros."),
    ("Planner Core", "Patch scope estimator", "Estimate likely files and risk for a request."),
    ("Planner Core", "Verification-aware steps", "Attach inferred verify commands to plan steps."),
    # Refactor Engine (15-21)
    ("Refactor Engine", "Complexity scoring", "Detect high cyclomatic complexity functions."),
    ("Refactor Engine", "Deep nesting detector", "Find heavily nested control-flow blocks."),
    ("Refactor Engine", "Long function detector", "Flag oversized functions by source lines."),
    ("Refactor Engine", "Argument pressure detector", "Detect functions with too many parameters."),
    ("Refactor Engine", "Duplicate structure hashing", "Find functions with similar AST structure."),
    ("Refactor Engine", "Large module detector", "Rank modules by size and refactor pressure."),
    ("Refactor Engine", "Global state pressure", "Count module-level assignment surfaces."),
    # Test Matrix (22-28)
    ("Test Matrix", "Source/test pairing", "Map source files to likely test files."),
    ("Test Matrix", "Missing test detector", "List source modules without obvious tests."),
    ("Test Matrix", "Pytest function extraction", "Extract test functions and line numbers."),
    ("Test Matrix", "Assertion counting", "Estimate assertion density per test function."),
    ("Test Matrix", "Verification command inference", "Infer pytest/make/health/compile commands."),
    ("Test Matrix", "Changed-file pytest targets", "Suggest pytest targets for impacted files."),
    ("Test Matrix", "Test gap summary", "Generate human-readable test backlog hints."),
    # Runtime Map (29-35)
    ("Runtime Map", "Entrypoint discovery", "Detect main/app/cli/scripts and package scripts."),
    ("Runtime Map", "Config file inventory", "Detect pyproject, requirements, pytest, Docker, CI and Makefile."),
    ("Runtime Map", "Dependency inventory", "Read requirements and pyproject dependencies."),
    ("Runtime Map", "Optional dependency groups", "Parse pyproject optional dependency groups."),
    ("Runtime Map", "Make target discovery", "Extract runnable Makefile targets."),
    ("Runtime Map", "CI/Docker surface map", "Detect workflow and Docker build surfaces."),
    ("Runtime Map", "Runtime command suggestions", "Suggest install/test/health/build commands."),
    # Context Engine (36-42)
    ("Context Engine", "Budget allocator", "Allocate prompt budget by section weight."),
    ("Context Engine", "Context dedupe", "Remove repeated lines before model injection."),
    ("Context Engine", "Priority context sections", "Sort context by usefulness and task priority."),
    ("Context Engine", "Query-aware budget strategy", "Adjust section budgets based on task wording."),
    ("Context Engine", "Context pack renderer", "Render bounded Omega prompt packets."),
    ("Context Engine", "Prompt compression", "Trim and dedupe context for model limits."),
    ("Context Engine", "Multi-source composition", "Combine semantic/test/runtime/refactor/plan intelligence."),
    # Autostart Fusion (43-49)
    ("Autostart Fusion", "Omega warmup snapshot", "Attach Omega analysis to the app at startup."),
    ("Autostart Fusion", "Omega model context", "Inject compact Omega context into user prompts."),
    ("Autostart Fusion", "Omega feature dashboard", "Expose an optional /omega49 dashboard."),
    ("Autostart Fusion", "Omega path relevance", "Rank paths for the first user query."),
    ("Autostart Fusion", "Omega recommendations", "Generate actionable refactor/test/runtime recommendations."),
    ("Autostart Fusion", "Omega export", "Export startup snapshot for handoff/debugging."),
    ("Autostart Fusion", "Zero-command activation", "All 49 capabilities activate on plain python3 main.py."),
]

OMEGA_FEATURES_49: tuple[OmegaFeature, ...] = tuple(
    OmegaFeature(i + 1, category, name, description)
    for i, (category, name, description) in enumerate(_OMEGA_ROWS)
)
assert len(OMEGA_FEATURES_49) == 49, f"expected 49 features, got {len(OMEGA_FEATURES_49)}"


@dataclass
class OmegaSnapshot:
    root: str
    started_at: float
    duration_s: float
    features: int
    semantic: dict[str, Any] = field(default_factory=dict)
    refactor: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    recommendations: list[str] = field(default_factory=list)

    def to_context(self, max_chars: int = 2600) -> str:
        parts = [
            "[OMEGA SUITE CONTEXT]",
            f"features={self.features}, duration={self.duration_s:.2f}s",
            f"semantic={self.semantic.get('stats', {})}",
            f"refactor={self.refactor.get('stats', {})}",
            f"tests={self.tests.get('stats', {})}",
            f"runtime={self.runtime.get('stats', {})}",
        ]
        if self.recommendations:
            parts.append("recommendations=" + "; ".join(self.recommendations[:7]))
        if self.context:
            parts.append(self.context[:1600])
        parts.append("[/OMEGA SUITE CONTEXT]")
        return "\n".join(parts)[:max_chars]


def omega_feature_count() -> int:
    return len(OMEGA_FEATURES_49)


def by_category() -> dict[str, list[OmegaFeature]]:
    grouped: dict[str, list[OmegaFeature]] = {}
    for feature in OMEGA_FEATURES_49:
        grouped.setdefault(feature.category, []).append(feature)
    return grouped


def activate_omega_mode(app: Any | None = None) -> dict[str, Any]:
    summary = {"features": omega_feature_count(), "categories": len(by_category()), "safety": "guardrails stay enabled"}
    if app is not None:
        setattr(app, "omega_features", OMEGA_FEATURES_49)
        setattr(app, "omega_profile_active", True)
    return summary


def run_omega_warmup(app: Any | None = None, root: str | Path = ".") -> OmegaSnapshot:
    """Run all Omega analyzers and attach a snapshot to the app."""
    activate_omega_mode(app)
    started = time.time()
    start = time.perf_counter()
    root_path = Path(root).resolve()

    semantic_index = build_semantic_index(root_path)
    semantic_stats = semantic_index.stats()
    semantic_ranked = search_index(semantic_index, "agent terminal ui tools commands tests", top_k=15)
    semantic = {
        "stats": semantic_stats,
        "ranked": semantic_ranked,
        "symbols": symbol_summary(semantic_index, limit=30),
    }

    ref_report = analyze_refactors(root_path)
    ref_backlog = refactor_backlog(ref_report, limit=12)
    refactor = {"stats": refactor_stats(ref_report), "backlog": ref_backlog, "summary": ref_report.summary(limit=8)}

    test_info = analyze_tests(root_path)
    tests = {
        "stats": test_info.stats(),
        "gaps": test_gap_summary(test_info, limit=15),
        "targets_for_hot": pytest_targets_for_files(test_info, [p for p, _s in semantic_ranked[:8]], max_targets=10),
    }

    runtime_surface = analyze_runtime(root_path)
    runtime = {"stats": runtime_surface.stats(), "brief": runtime_brief(runtime_surface)}

    intelligence = {
        "commands": runtime_surface.commands or test_info.commands,
        "hot_files": semantic_ranked[:10],
        "missing_tests": test_info.missing_tests[:10],
    }
    plan_obj = create_plan("Improve and verify this terminal AI agent", intelligence=intelligence)
    queue = action_queue_from_backlog([*ref_backlog, *tests["gaps"]], limit=10)
    patch_scope = estimate_patch_scope("Improve and verify this terminal AI agent", semantic_ranked, max_files=6)
    plan = {"summary": plan_obj.summary(), "queue": queue, "patch_scope": patch_scope}

    context_pack = compose_context_pack(
        "Improve and verify this terminal AI agent",
        {"semantic": semantic, "tests": tests, "runtime": runtime, "refactor": refactor, "plan": plan},
        budget=4800,
    )
    recommendations = [*ref_backlog[:5], *tests["gaps"][:5]]
    if runtime_surface.commands:
        recommendations.append("First verification gate: " + runtime_surface.commands[0])

    snapshot = OmegaSnapshot(
        root=str(root_path),
        started_at=started,
        duration_s=time.perf_counter() - start,
        features=omega_feature_count(),
        semantic=semantic,
        refactor=refactor,
        tests=tests,
        runtime=runtime,
        plan=plan,
        context=context_pack.render(),
        recommendations=recommendations[:15],
    )
    if app is not None:
        setattr(app, "omega_snapshot", snapshot)
    return snapshot


def dashboard(limit: int | None = None) -> str:
    lines = [
        "╔════════════════════════════════════════════════════════════╗",
        f"║  OMEGA SUITE: {omega_feature_count()} NEXT-LEVEL FEATURES ACTIVE{'':<10}║",
        "╠════════════════════════════════════════════════════════════╣",
    ]
    for category, features in by_category().items():
        lines.append(f"║  {category:<18} {len(features):>3} capabilities{'':<23}║")
    lines.append("╚════════════════════════════════════════════════════════════╝")
    lines.append("")
    shown = 0
    for category, features in by_category().items():
        lines.append(f"[{category}]")
        for feature in features:
            if limit is not None and shown >= limit:
                lines.append(f"… {omega_feature_count() - shown} more Omega features.")
                return "\n".join(lines)
            lines.append(f"  O{feature.id:02d}. {feature.name} — {feature.description}")
            shown += 1
        lines.append("")
    return "\n".join(lines).rstrip()


def export_snapshot(snapshot: OmegaSnapshot, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot.__dict__, indent=2, default=str), encoding="utf-8")
    return out
