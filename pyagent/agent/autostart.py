"""Automatic enterprise startup orchestration.

The user should not have to run `/index-codebase`, `/kg`, `/sast`, `/metrics`,
or `/features129` after `python3 main.py`.  This module performs the safe,
local, real-world warmups automatically at startup and stores a compact snapshot
for context injection on later turns.

Network calls, destructive actions, and server listeners are deliberately not
started here.  They are *armed* and available, but still protected by the normal
approval/guardrail layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class StartupTaskResult:
    """Result of one automatic startup task."""

    name: str
    ok: bool
    detail: str
    duration_s: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StartupSnapshot:
    """Whole automatic startup snapshot."""

    root: str
    started_at: float
    duration_s: float = 0.0
    tasks: list[StartupTaskResult] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def ok_count(self) -> int:
        return sum(1 for task in self.tasks if task.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for task in self.tasks if not task.ok)

    def summary(self) -> str:
        lines = [
            "Enterprise autostart:",
            f"  root:      {self.root}",
            f"  tasks:     {self.ok_count} ok / {self.fail_count} failed",
            f"  duration:  {self.duration_s:.2f}s",
        ]
        for task in self.tasks:
            icon = "✓" if task.ok else "✗"
            lines.append(f"  {icon} {task.name:<24} {task.detail} ({task.duration_s:.2f}s)")
        return "\n".join(lines)


def _run_task(name: str, fn: Callable[[], tuple[str, dict[str, Any]]]) -> StartupTaskResult:
    start = time.perf_counter()
    try:
        detail, metadata = fn()
        return StartupTaskResult(
            name=name,
            ok=True,
            detail=detail,
            duration_s=time.perf_counter() - start,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001 - autostart must never block the app
        return StartupTaskResult(
            name=name,
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            duration_s=time.perf_counter() - start,
            metadata={},
        )


class EnterpriseAutostart:
    """Run all safe local enterprise warmups automatically."""

    def __init__(self, app: Any, root: str | Path = ".") -> None:
        self.app = app
        self.root = Path(root).resolve()

    def run(self) -> StartupSnapshot:
        snapshot = StartupSnapshot(root=str(self.root), started_at=time.time())
        start = time.perf_counter()

        tasks: list[tuple[str, Callable[[], tuple[str, dict[str, Any]]]]] = [
            ("feature profile", self._feature_profile),
            ("hyper suite", self._hyper_suite),
            ("apex suite", self._apex_suite),
            ("omega suite", self._omega_suite),
            ("nova suite", self._nova_suite),
            ("zenith suite", self._zenith_suite),
            ("tool registry", self._tool_registry),
            ("command registry", self._command_registry),
            ("knowledge graph", self._knowledge_graph),
            ("rag index", self._rag_index),
            ("code metrics", self._code_metrics),
            ("sast scan", self._sast_scan),
            ("sbom inventory", self._sbom_inventory),
            ("long-term memory", self._long_term_memory),
            ("mcp schema", self._mcp_schema),
            ("browser status", self._browser_status),
            ("voice status", self._voice_status),
            ("observability", self._observability),
            ("plugin marketplace", self._plugin_marketplace),
            ("audit log", self._audit_log),
        ]

        for name, fn in tasks:
            result = _run_task(name, fn)
            snapshot.tasks.append(result)
            snapshot.context[name] = result.metadata

        snapshot.duration_s = time.perf_counter() - start
        setattr(self.app, "enterprise_autostart", snapshot)
        return snapshot

    # ------------------------------------------------------------------
    # Individual startup tasks
    # ------------------------------------------------------------------
    def _feature_profile(self) -> tuple[str, dict[str, Any]]:
        from .enterprise_suite import activate_enterprise_mode, by_category, combined_feature_count, feature_count

        summary = activate_enterprise_mode(self.app)
        metadata = {
            "enterprise_features": feature_count(),
            "combined_features": combined_feature_count(),
            "categories": {k: len(v) for k, v in by_category().items()},
            **summary,
        }
        return f"{metadata['combined_features']} total features active", metadata

    def _hyper_suite(self) -> tuple[str, dict[str, Any]]:
        from .hyper_suite import run_hyper_warmup

        snapshot = run_hyper_warmup(self.app, root=self.root)
        metadata = {
            "features": snapshot.features,
            "files": snapshot.inventory.get("files", 0),
            "functions": snapshot.symbols.get("functions", 0),
            "classes": snapshot.symbols.get("classes", 0),
            "tests": snapshot.tests.get("functions", 0),
            "docs": snapshot.docs.get("headings", 0),
            "fingerprint": snapshot.fingerprint,
            "recommendations": snapshot.recommendations,
            "context": snapshot.to_context(),
        }
        return f"{snapshot.features} hyper features, {metadata['files']} files analysed", metadata

    def _apex_suite(self) -> tuple[str, dict[str, Any]]:
        from .apex_suite import run_apex_warmup

        snapshot = run_apex_warmup(self.app, root=self.root)
        metadata = {
            "features": snapshot.features,
            "files_scanned": snapshot.files_scanned,
            "symbols": len(snapshot.symbols),
            "hot_files": snapshot.impact.get("hot_files", [])[:12],
            "tests": snapshot.tests,
            "architecture": snapshot.architecture,
            "verification": snapshot.verification,
            "backlog": snapshot.backlog,
            "fingerprint": snapshot.fingerprint,
            "context": snapshot.to_context(),
        }
        hot_count = len(snapshot.impact.get("hot_files", []))
        return f"{snapshot.features} apex features, {hot_count} impact hotspots", metadata

    def _omega_suite(self) -> tuple[str, dict[str, Any]]:
        from .omega_suite import run_omega_warmup

        snapshot = run_omega_warmup(self.app, root=self.root)
        metadata = {
            "features": snapshot.features,
            "semantic": snapshot.semantic,
            "refactor": snapshot.refactor,
            "tests": snapshot.tests,
            "runtime": snapshot.runtime,
            "plan": snapshot.plan,
            "recommendations": snapshot.recommendations,
            "context": snapshot.to_context(),
            "duration_s": snapshot.duration_s,
        }
        stats = snapshot.semantic.get("stats", {})
        return f"{snapshot.features} omega features, {stats.get('documents', 0)} docs indexed", metadata

    def _nova_suite(self) -> tuple[str, dict[str, Any]]:
        from .nova_suite import run_nova_warmup

        snapshot = run_nova_warmup(self.app, root=self.root)
        metadata = {
            "features": snapshot.features,
            "symbols": snapshot.symbols,
            "similarity": snapshot.similarity,
            "impact": snapshot.impact,
            "workflow": snapshot.workflow,
            "docs": snapshot.docs,
            "quality": snapshot.quality,
            "execution": snapshot.execution,
            "insights": snapshot.insights,
            "drift": snapshot.drift,
            "recommendations": snapshot.recommendations,
            "context": snapshot.to_context(),
            "duration_s": snapshot.duration_s,
        }
        sym_count = snapshot.symbols.get("stats", {}).get("symbols", 0)
        return f"{snapshot.features} nova features, {sym_count} symbols mapped", metadata

    def _zenith_suite(self) -> tuple[str, dict[str, Any]]:
        from .zenith_suite import run_zenith_warmup

        snapshot = run_zenith_warmup(self.app, root=self.root)
        metadata = {
            "features": snapshot.features,
            "graph": snapshot.graph,
            "lsp": snapshot.lsp,
            "dependencies": snapshot.dependencies,
            "metrics": snapshot.metrics,
            "tests": snapshot.tests,
            "docs": snapshot.docs,
            "release": snapshot.release,
            "code_map": snapshot.code_map,
            "performance": snapshot.performance,
            "config": snapshot.config,
            "patch": snapshot.patch,
            "workflow": snapshot.workflow,
            "agents": snapshot.agents,
            "cache": snapshot.cache,
            "context": snapshot.to_context(),
            "duration_s": snapshot.duration_s,
        }
        defs = snapshot.lsp.get("stats", {}).get("definitions", 0)
        return f"{snapshot.features} zenith features, {defs} LSP definitions", metadata

    def _tool_registry(self) -> tuple[str, dict[str, Any]]:
        tools = self.app.registry.all()
        dangerous = sum(1 for tool in tools if getattr(tool, "dangerous", False))
        return f"{len(tools)} tools ready", {"tools": len(tools), "dangerous": dangerous}

    def _command_registry(self) -> tuple[str, dict[str, Any]]:
        commands = self.app.commands.all()
        aliases = sum(len(getattr(cmd, "aliases", ())) for cmd in commands)
        return f"{len(commands)} commands ready", {"commands": len(commands), "aliases": aliases}

    def _knowledge_graph(self) -> tuple[str, dict[str, Any]]:
        from .knowledge_graph import build_graph_from_codebase

        kg = build_graph_from_codebase(self.root)
        stats = kg.stats()
        return f"{stats['nodes']} nodes / {stats['edges']} edges", stats

    def _rag_index(self) -> tuple[str, dict[str, Any]]:
        from .rag_v2 import index_codebase

        stats = index_codebase(self.root, clear_existing=True)
        return f"{stats['documents']} chunks from {stats['sources']} sources", stats

    def _code_metrics(self) -> tuple[str, dict[str, Any]]:
        from .code_metrics import analyze_codebase, suggest_refactoring

        report = analyze_codebase(self.root)
        suggestions = suggest_refactoring(report)
        metadata = {
            "files_scanned": report.files_scanned,
            "total_lines": report.total_lines,
            "functions": report.total_functions,
            "classes": report.total_classes,
            "max_complexity": report.max_complexity,
            "todos": report.total_todos,
            "fixmes": report.total_fixmes,
            "dead_code": len(report.dead_code),
            "duplicates": len(report.duplicates),
            "suggestions": suggestions[:8],
        }
        return f"{report.files_scanned} files, max cx {report.max_complexity}", metadata

    def _sast_scan(self) -> tuple[str, dict[str, Any]]:
        from .sast import scan_codebase

        report = scan_codebase(self.root)
        metadata = {
            "files_scanned": report.files_scanned,
            "findings": len(report.findings),
            "critical": report.critical_count,
            "high": report.high_count,
            "medium": report.medium_count,
            "low": report.low_count,
        }
        return f"{metadata['findings']} findings", metadata

    def _sbom_inventory(self) -> tuple[str, dict[str, Any]]:
        from .sbom import generate_sbom

        sbom = generate_sbom(root=str(self.root))
        components = sbom.get("components", [])
        metadata = {"components": len(components), "format": sbom.get("bomFormat", "")}
        return f"{len(components)} components", metadata

    def _long_term_memory(self) -> tuple[str, dict[str, Any]]:
        from .long_term_memory import get_long_term_memory

        mem = get_long_term_memory()
        stats = mem.stats()
        return f"{stats['facts']} facts / {stats['episodes']} episodes", stats

    def _mcp_schema(self) -> tuple[str, dict[str, Any]]:
        from .mcp_server import get_mcp_server

        server = get_mcp_server()
        tools = server.list_tools_as_mcp()
        return f"{len(tools)} MCP tools exposed", {"mcp_tools": len(tools)}

    def _browser_status(self) -> tuple[str, dict[str, Any]]:
        from .browser_automation import browser_status

        status = browser_status()
        return status.splitlines()[0][:80], {"status": status}

    def _voice_status(self) -> tuple[str, dict[str, Any]]:
        from .voice import voice_available

        available = voice_available()
        return "available" if available else "not available", {"available": available}

    def _observability(self) -> tuple[str, dict[str, Any]]:
        from .profiler import get_profiler
        from .prometheus_exporter import exporter_status
        from .telemetry import get_telemetry

        profiler = get_profiler()
        profiler.enable()
        telemetry = get_telemetry()
        # Keep telemetry local/in-memory unless the user explicitly persists it.
        status = exporter_status()
        metadata = {"profiler": profiler.enabled, "telemetry": telemetry.enabled, "prometheus": status}
        return "profiler on, telemetry local, prometheus armed", metadata

    def _plugin_marketplace(self) -> tuple[str, dict[str, Any]]:
        from .plugin_marketplace import list_available, list_installed

        available = list_available()
        installed = list_installed()
        metadata = {"available": len(available), "installed": len(installed)}
        return f"{len(available)} available / {len(installed)} installed", metadata

    def _audit_log(self) -> tuple[str, dict[str, Any]]:
        from .audit_log import get_audit_log

        log = get_audit_log()
        log.record("enterprise_autostart", actor="agent", features=129, root=str(self.root))
        ok, msg = log.verify()
        metadata = {"entries": len(log.entries), "verified": ok, "message": msg}
        return "verified" if ok else msg, metadata


def run_enterprise_autostart(app: Any, root: str | Path = ".") -> StartupSnapshot:
    """Convenience wrapper used by App."""
    return EnterpriseAutostart(app, root=root).run()
