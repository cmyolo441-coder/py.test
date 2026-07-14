"""Zenith Suite — 97 next-level real working features.

Zenith is built from 16 focused modules plus this orchestrator.  Each module
contributes six local/offline capabilities (96 total), and the orchestrator adds
one zero-command startup fusion capability for a total of 97.
"""
from __future__ import annotations

import json, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .zenith_agents import agents_context, specialist_plan
from .zenith_cache import memoize_snapshot
from .zenith_code_map import build_code_map, code_map_context
from .zenith_config import analyze_config, config_context
from .zenith_context import compose_zenith_context
from .zenith_dependencies import analyze_dependencies, dependency_context
from .zenith_docs import build_docs_plan, docs_plan_context
from .zenith_graph import build_graph, centrality, dependency_layers, graph_stats, strongly_connected
from .zenith_handoff import build_handoff, handoff_context
from .zenith_lsp import build_lsp_index, workspace_symbols
from .zenith_metrics import compute_project_metrics, metrics_context
from .zenith_patch import patch_context, plan_patch
from .zenith_performance import analyze_performance, perf_context
from .zenith_release import build_release_plan, release_context
from .zenith_tests import prioritize_tests, test_plan_context
from .zenith_workflows import build_standard_workflow, simulate_workflow, workflow_text


@dataclass(frozen=True)
class ZenithFeature:
    id: int
    category: str
    name: str
    description: str

_MODULE_FEATURES: dict[str, list[tuple[str, str]]] = {
    "Graph Core": [("Directed graph builder","Build dependency graphs from local edges."),("Graph stats","Count nodes, edges and top degree nodes."),("Shortest paths","Resolve path between graph nodes."),("Transitive closure","Find reachable dependency surfaces."),("SCC detector","Detect cyclic clusters."),("Dependency layers","Topologically layer dependency graph.")],
    "LSP Index": [("Definitions index","Index class/function definitions."),("References index","Map name references by file and line."),("Goto definition","Resolve likely definitions."),("Find references","Return reference locations."),("File outline","Render symbol outline per file."),("Workspace symbols","Search symbols by query.")],
    "Dependency Brain": [("Import inventory","Parse Python imports."),("Internal/external split","Classify imports by package prefix."),("Declared deps reader","Read requirements and pyproject deps."),("Missing declaration hints","Find imported external deps not declared."),("Import hotspots","Rank most-used import families."),("Dependency context","Render dependency prompt context.")],
    "Metrics Fusion": [("Manageability score","Score project size pressure."),("Test presence score","Score test-file presence."),("Docs presence score","Score markdown presence."),("Feature readiness score","Blend feature count readiness."),("Weighted total","Compute composite weighted score."),("Metrics context","Render compact metrics context.")],
    "Test Zenith": [("Mutation candidates","Find branch/operator mutation points."),("Test target priority","Map hot files to tests."),("Verification commands","Suggest pytest targets."),("Mutation stats","Count mutation candidates."),("Fallback test target","Use full suite when mapping absent."),("Test context","Render compact test plan.")],
    "Docs Zenith": [("Public API extraction","Extract public API items."),("Docstring gap list","List undocumented public symbols."),("Guide skeleton","Generate project guide skeleton."),("API markdown rows","Render API surface bullets."),("Docs stats","Count API/missing docs."),("Docs context","Render docs plan context.")],
    "Release Zenith": [("Version detector","Read version from pyproject or package."),("Artifact detector","Detect package/docker artifacts."),("Release checklist","Build release checklist."),("Blocker detector","Flag release blockers."),("Verification binding","Attach commands to release plan."),("Release context","Render release plan context.")],
    "Cache Zenith": [("Persistent JSON cache","Store analysis payloads locally."),("TTL support","Expire cached entries."),("Snapshot fingerprint","Hash payloads deterministically."),("Latest snapshot","Track latest payload per kind."),("Change detector","Report whether snapshot changed."),("Cache stats","Report cache entries and path.")],
    "Context Zenith": [("Priority sections","Prioritize context sections."),("Deduplication","Remove repeated context lines."),("Budgeted rendering","Render bounded prompt packet."),("Source fusion","Compose many sources."),("Context stats","Measure rendered context."),("Zenith context markers","Wrap model-facing context.")],
    "Agent Router": [("Specialist catalog","Provide local specialist roles."),("Trigger matching","Route roles by task keywords."),("Specialist sequence","Create ordered specialist sequence."),("Tool focus hints","Attach tool families to roles."),("Fallback routing","Choose default architect/tester."),("Agent context","Render specialist context.")],
    "Workflow Zenith": [("Workflow builder","Build inspect-plan-edit-review-verify workflow."),("Dependency simulation","Simulate step finish times."),("Cycle detection","Detect invalid workflows."),("Critical duration","Estimate critical duration."),("Verification nodes","Append command verification nodes."),("Workflow context","Render workflow text.")],
    "Code Map Zenith": [("Layer inference","Infer UI/tools/providers/memory/core layers."),("Owner map","Assign files to layer owners."),("Boundary hints","Flag uncategorized agent files."),("Layer stats","Count files by layer."),("Code map context","Render layer context."),("Subsystem overview","Summarize repository packages." )],
    "Patch Zenith": [("Patch scope","Estimate likely changed files."),("Risk classifier","Classify patch risk by file count."),("Rationale builder","Explain scope choice."),("Diff preview","Generate unified diff text."),("Patch stats","Count scope and risk."),("Patch context","Render patch plan context.")],
    "Performance Zenith": [("Nested loop scan","Detect nested loops."),("Concat smell scan","Detect possible repeated concat."),("Container copy scan","Detect container copy allocation sites."),("Performance scores","Score perf findings."),("Perf stats","Count findings and files."),("Perf context","Render performance context.")],
    "Config Zenith": [("Config file detector","Detect common config files."),("Env var extraction","Find environment variables in code."),("Dataclass config map","Find dataclass config surfaces."),("Pyproject keys","Read top-level pyproject keys."),("Config stats","Report config surfaces."),("Config context","Render config context.")],
    "Handoff Zenith": [("Markdown handoff","Generate handoff markdown."),("Snapshot sections","Serialize snapshot sections."),("Timestamped report","Include creation timestamp."),("Handoff save","Write report to disk when requested."),("Handoff context","Render short handoff context."),("JSON-safe summary","Prepare snapshot for export.")],
}

FEATURE_ROWS: list[tuple[str, str, str]] = []
for category, items in _MODULE_FEATURES.items():
    FEATURE_ROWS.extend((category, name, desc) for name, desc in items)
FEATURE_ROWS.append(("Zenith Orchestrator", "97-feature zero-command fusion", "Warm all Zenith modules automatically on python3 main.py."))
ZENITH_FEATURES_97 = tuple(ZenithFeature(i + 1, c, n, d) for i, (c, n, d) in enumerate(FEATURE_ROWS))
assert len(ZENITH_FEATURES_97) == 97, f"expected 97 features, got {len(ZENITH_FEATURES_97)}"

@dataclass
class ZenithSnapshot:
    root: str
    started_at: float
    duration_s: float
    features: int
    graph: dict[str, Any] = field(default_factory=dict)
    lsp: dict[str, Any] = field(default_factory=dict)
    dependencies: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    docs: dict[str, Any] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)
    code_map: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    patch: dict[str, Any] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    context: str = ""
    handoff: str = ""
    def to_context(self, max_chars: int = 3800) -> str:
        parts=["[ZENITH SUITE CONTEXT]", f"features={self.features}, duration={self.duration_s:.2f}s", f"graph={self.graph.get('stats',{})}", f"lsp={self.lsp.get('stats',{})}", f"deps={self.dependencies.get('stats',{})}", f"metrics={self.metrics.get('stats',{})}", f"tests={self.tests.get('stats',{})}", f"performance={self.performance.get('stats',{})}", f"config={self.config.get('stats',{})}"]
        if self.context: parts.append(self.context[:2200])
        if self.handoff: parts.append(self.handoff[:1000])
        parts.append("[/ZENITH SUITE CONTEXT]")
        return "\n".join(parts)[:max_chars]

def zenith_feature_count() -> int: return len(ZENITH_FEATURES_97)
def by_category() -> dict[str, list[ZenithFeature]]:
    grouped: dict[str, list[ZenithFeature]] = {}
    for f in ZENITH_FEATURES_97: grouped.setdefault(f.category, []).append(f)
    return grouped
def activate_zenith_mode(app: Any | None = None) -> dict[str, Any]:
    summary={"features":zenith_feature_count(),"categories":len(by_category()),"safety":"guardrails stay enabled"}
    if app is not None: setattr(app,'zenith_features',ZENITH_FEATURES_97); setattr(app,'zenith_profile_active',True)
    return summary

def run_zenith_warmup(app: Any | None = None, root: str | Path = ".") -> ZenithSnapshot:
    activate_zenith_mode(app); rp=Path(root).resolve(); started=time.time(); t0=time.perf_counter()
    deps=analyze_dependencies(rp); edges=[(src, imp) for src, imps in deps.imports_by_file.items() for imp in imps]
    g=build_graph(edges); lsp=build_lsp_index(rp); cm=build_code_map(rp)
    metrics=compute_project_metrics(rp, {"features": zenith_feature_count()}); tests=prioritize_tests(rp, [p for p,_ in graph_stats(g).get('top_out',[])[:8]])
    docs=build_docs_plan(rp); release=build_release_plan(rp, tests.commands); perf=analyze_performance(rp); cfg=analyze_config(rp)
    patch=plan_patch('Improve this terminal AI agent', [d.path for d in lsp.definitions[:8]], [p for p,_ in graph_stats(g).get('top_out',[])[:8]])
    wf=build_standard_workflow(tests.commands); agents=specialist_plan('Improve and verify this advanced terminal AI agent')
    sources={"symbols":"\n".join(workspace_symbols(lsp,'agent',30)),"impact":dependency_context(deps),"tests":test_plan_context(tests),"quality":metrics_context(metrics),"runtime":release_context(release),"docs":docs_plan_context(docs),"plan":workflow_text(wf),"performance":perf_context(perf),"config":config_context(cfg),"patch":patch_context(patch),"agents":agents_context(agents),"code_map":code_map_context(cm)}
    zctx=compose_zenith_context('Improve and verify this terminal AI agent', sources, 7600).render()
    handoff=build_handoff('Zenith Startup Handoff', {"graph":graph_stats(g),"deps":deps.stats(),"metrics":metrics.stats(),"tests":tests.stats(),"release":release.stats(),"perf":perf.stats()})
    payload={"graph":graph_stats(g),"lsp":lsp.stats(),"deps":deps.stats(),"metrics":metrics.stats(),"tests":tests.stats(),"perf":perf.stats(),"config":cfg.stats()}
    cache=memoize_snapshot('zenith', payload)
    snap=ZenithSnapshot(str(rp), started, time.perf_counter()-t0, zenith_feature_count(), {"stats":graph_stats(g),"scc":strongly_connected(g)[:10],"layers":dependency_layers(g)[:8],"centrality":sorted(centrality(g).items(), key=lambda x:-x[1])[:20]}, {"stats":lsp.stats(),"symbols":workspace_symbols(lsp,'agent',30)}, {"stats":deps.stats(),"context":dependency_context(deps)}, {"stats":metrics.stats(),"context":metrics_context(metrics)}, {"stats":tests.stats(),"context":test_plan_context(tests)}, {"stats":docs.stats(),"context":docs_plan_context(docs)}, {"stats":release.stats(),"context":release_context(release)}, {"stats":cm.stats(),"context":code_map_context(cm)}, {"stats":perf.stats(),"context":perf_context(perf)}, {"stats":cfg.stats(),"context":config_context(cfg)}, {"stats":patch.stats(),"context":patch_context(patch)}, {"stats":simulate_workflow(wf),"context":workflow_text(wf)}, agents, cache, zctx, handoff_context(handoff))
    if app is not None: setattr(app,'zenith_snapshot',snap)
    return snap

def dashboard(limit:int|None=None)->str:
    lines=["╔════════════════════════════════════════════════════════════╗", f"║  ZENITH SUITE: {zenith_feature_count()} MAX FEATURES ACTIVE{'':<15}║", "╠════════════════════════════════════════════════════════════╣"]
    for cat, feats in by_category().items(): lines.append(f"║  {cat:<22} {len(feats):>3} capabilities{'':<19}║")
    lines.append("╚════════════════════════════════════════════════════════════╝"); lines.append("")
    shown=0
    for cat, feats in by_category().items():
        lines.append(f"[{cat}]")
        for f in feats:
            if limit is not None and shown>=limit: lines.append(f"… {zenith_feature_count()-shown} more Zenith features."); return "\n".join(lines)
            lines.append(f"  Z{f.id:02d}. {f.name} — {f.description}"); shown+=1
        lines.append("")
    return "\n".join(lines).rstrip()
def export_snapshot(snapshot: ZenithSnapshot, path: str | Path) -> Path:
    out=Path(path); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(snapshot.__dict__,indent=2,default=str),encoding='utf-8'); return out
