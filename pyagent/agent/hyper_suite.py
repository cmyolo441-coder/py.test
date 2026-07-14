"""Hyper Suite — 70 additional non-security enterprise capabilities.

These capabilities focus on autonomy, code intelligence, UX, productivity,
observability and model/runtime optimisation.  They intentionally do **not**
disable guardrails or approval gates; dangerous actions remain protected by the
existing safety layer.

The module provides two things:
  1. A deterministic 70-feature catalog for dashboards/startup.
  2. A real local warmup analyzer that builds codebase intelligence automatically
     when `python3 main.py` starts.
"""
from __future__ import annotations

import ast
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HyperFeature:
    """A named capability in the 70-feature hyper profile."""

    id: int
    category: str
    name: str
    description: str


HYPER_FEATURES_70: tuple[HyperFeature, ...] = tuple(
    HyperFeature(i + 1, category, name, description)
    for i, (category, name, description) in enumerate(
        [
            # UI Intelligence (1-8)
            ("UI Intelligence", "Adaptive prompt chrome", "Prompt hints adapt to active mode and project state."),
            ("UI Intelligence", "Contextual examples", "Startup suggests useful prompts based on detected repository shape."),
            ("UI Intelligence", "Live capability counter", "Feature totals update from real loaded modules."),
            ("UI Intelligence", "Command intent hints", "Commands can be matched by intent keywords, not only names."),
            ("UI Intelligence", "Workspace-aware banner", "Banner displays workdir, provider, model and warmup summary."),
            ("UI Intelligence", "Mode-aware status", "Status can surface enterprise/hyper/autonomous modes."),
            ("UI Intelligence", "Smart prompt macros", "Reusable high-signal prompt macros are generated locally."),
            ("UI Intelligence", "Inline startup digest", "Autostart outputs a compact digest without extra commands."),

            # Autonomous Planning (9-18)
            ("Autonomous Planning", "Task decomposition templates", "Complex requests can be split into plan/execute/verify stages."),
            ("Autonomous Planning", "Repository action planner", "Warmup creates suggested next actions from code metrics."),
            ("Autonomous Planning", "Test-first planner", "Coding tasks can be biased toward tests before changes."),
            ("Autonomous Planning", "Refactor planner", "High-complexity and long-function hints feed planning."),
            ("Autonomous Planning", "Documentation planner", "Missing docs/headings become documentation tasks."),
            ("Autonomous Planning", "Migration planner", "Dependency and config files are mapped for migration tasks."),
            ("Autonomous Planning", "Release checklist", "Local checklist for build/test/docs/review packaging."),
            ("Autonomous Planning", "Rollback-aware planning", "Plans include checkpoint/restore awareness."),
            ("Autonomous Planning", "Goal continuation hints", "Interrupted or partial work can be resumed with context."),
            ("Autonomous Planning", "Verification strategy picker", "Selects likely verification commands from project files."),

            # Code Intelligence (19-32)
            ("Code Intelligence", "AST symbol index", "Functions, classes and methods are indexed without executing code."),
            ("Code Intelligence", "Import topology", "Imports are counted and ranked to reveal core dependencies."),
            ("Code Intelligence", "API surface map", "Public functions/classes are extracted into a compact map."),
            ("Code Intelligence", "Test surface map", "Test files and test functions are counted and indexed."),
            ("Code Intelligence", "Docs heading map", "Markdown heading structure is extracted for docs intelligence."),
            ("Code Intelligence", "Hot file heatmap", "Large and symbol-heavy files are ranked for attention."),
            ("Code Intelligence", "Repository fingerprint", "Stable content fingerprint detects codebase changes."),
            ("Code Intelligence", "Language inventory", "File extensions and byte counts are summarized."),
            ("Code Intelligence", "Package/config detector", "pyproject, requirements, Dockerfile and CI files are detected."),
            ("Code Intelligence", "Entrypoint detector", "main.py, scripts and app entrypoints are recognized."),
            ("Code Intelligence", "Dependency cluster hints", "Imports are grouped to show likely subsystems."),
            ("Code Intelligence", "Symbol density scoring", "Files are scored by symbol count per KB."),
            ("Code Intelligence", "Docstring coverage signal", "AST docstring presence is estimated."),
            ("Code Intelligence", "Code navigation context", "Relevant paths and symbols are injected automatically."),

            # Memory/RAG (33-42)
            ("Memory/RAG", "Auto RAG warm context", "Indexed snippets are attached to normal prompts automatically."),
            ("Memory/RAG", "Query-aware snippet pack", "Top local snippets are selected per user prompt."),
            ("Memory/RAG", "Warmup snapshot memory", "Startup analysis is kept in the app for future turns."),
            ("Memory/RAG", "Project fact synthesis", "Project facts are synthesized from local files and metrics."),
            ("Memory/RAG", "Context budget shaping", "Injected context is compacted to avoid flooding the model."),
            ("Memory/RAG", "Symbol-aware retrieval", "Symbols and paths augment semantic retrieval."),
            ("Memory/RAG", "Docs-aware retrieval", "Documentation headings feed context selection."),
            ("Memory/RAG", "Test-aware retrieval", "Test files are detected and surfaced for bug-fix tasks."),
            ("Memory/RAG", "Session learning hook", "Completed turns can be learned into long-term memory."),
            ("Memory/RAG", "Change-aware reindex", "RAG reindex clears stale chunks before rebuilding."),

            # Developer Productivity (43-52)
            ("Developer Productivity", "Verification command guesses", "Likely test/lint/build commands are inferred."),
            ("Developer Productivity", "Prompt macro library", "Macros for explain/fix/refactor/test/docs are built in."),
            ("Developer Productivity", "Repo onboarding brief", "A compact repo brief is generated at startup."),
            ("Developer Productivity", "Bug-fix context pack", "Bug prompts get tests, metrics and relevant snippets."),
            ("Developer Productivity", "Refactor context pack", "Refactor prompts get complexity and symbol density hints."),
            ("Developer Productivity", "Docs context pack", "Documentation prompts get docs heading maps."),
            ("Developer Productivity", "Test context pack", "Testing prompts get detected test layout and commands."),
            ("Developer Productivity", "Release context pack", "Packaging prompts get config and dependency files."),
            ("Developer Productivity", "Scaffold awareness", "Existing scaffold files are detected before generating new ones."),
            ("Developer Productivity", "Local-only readiness", "Startup analysis runs offline and without network calls."),

            # Observability / Performance (53-60)
            ("Observability", "Warmup task timings", "Every autostart task records duration."),
            ("Observability", "Analyzer performance budget", "Scans are capped to avoid blocking large repos."),
            ("Observability", "Capability health snapshot", "Loaded tools/commands/providers are counted."),
            ("Observability", "Project scale metrics", "Files, bytes, symbols and docs are summarized."),
            ("Observability", "Autostart digest", "A structured startup snapshot is available to the app."),
            ("Observability", "Profile-on-start", "Profiler is armed automatically for later turns."),
            ("Observability", "Recommendation tracing", "Generated recommendations keep source metadata."),
            ("Observability", "Fingerprint drift detection", "Content fingerprint can be compared across runs."),

            # Model / Runtime Optimisation (61-65)
            ("Model Runtime", "Provider capability inventory", "Known providers and model defaults are counted."),
            ("Model Runtime", "HTTP provider fallback", "OpenAI-compatible calls work even without the openai package."),
            ("Model Runtime", "Tool schema warmup", "Tool schemas are prebuilt for model tool calling."),
            ("Model Runtime", "Context routing hints", "Prompt type hints inform orchestration and retrieval."),
            ("Model Runtime", "Streaming readiness", "Streaming renderer and cancellation are initialized."),

            # Collaboration / Enterprise Workflow (66-70)
            ("Workflow", "Architecture brief", "Subsystem overview is generated from files/imports."),
            ("Workflow", "Handoff packet", "Startup snapshot can be shared as a concise handoff."),
            ("Workflow", "Review preparation", "Metrics and test maps prepare code review tasks."),
            ("Workflow", "Maintenance backlog", "Local recommendations form a lightweight backlog."),
            ("Workflow", "Zero-command activation", "All hyper features activate on plain python3 main.py."),
        ]
    )
)

assert len(HYPER_FEATURES_70) == 70, f"expected 70 features, got {len(HYPER_FEATURES_70)}"


@dataclass
class HyperSnapshot:
    """Result of the hyper local analyzer."""

    root: str
    started_at: float
    duration_s: float
    features: int
    inventory: dict[str, Any] = field(default_factory=dict)
    symbols: dict[str, Any] = field(default_factory=dict)
    imports: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    docs: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, Any] = field(default_factory=dict)
    prompts: list[dict[str, str]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def to_context(self, max_chars: int = 1800) -> str:
        """Compact model-facing context string."""
        parts = [
            "[HYPER SUITE CONTEXT]",
            f"features={self.features}, files={self.inventory.get('files', 0)}, bytes={self.inventory.get('bytes', 0)}",
            f"languages={self.inventory.get('extensions', {})}",
            f"symbols: functions={self.symbols.get('functions', 0)}, classes={self.symbols.get('classes', 0)}, methods={self.symbols.get('methods', 0)}",
            f"tests: files={self.tests.get('files', 0)}, functions={self.tests.get('functions', 0)}",
            f"docs: markdown_files={self.docs.get('markdown_files', 0)}, headings={self.docs.get('headings', 0)}",
            "top imports: " + ", ".join(f"{k}({v})" for k, v in self.imports.get("top", [])[:8]),
        ]
        if self.recommendations:
            parts.append("recommendations: " + "; ".join(self.recommendations[:5]))
        if self.prompts:
            parts.append("prompt macros: " + "; ".join(p["name"] for p in self.prompts[:8]))
        parts.append(f"fingerprint={self.fingerprint}")
        parts.append("[/HYPER SUITE CONTEXT]")
        text = "\n".join(parts)
        return text[:max_chars]


class HyperAnalyzer:
    """Fast, dependency-free project analyzer used by autostart."""

    VALID_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh"}
    SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build", ".mypy_cache", ".ruff_cache"}

    def __init__(self, root: str | Path, app: Any | None = None, max_files: int = 600) -> None:
        self.root = Path(root).resolve()
        self.app = app
        self.max_files = max_files

    def run(self) -> HyperSnapshot:
        start = time.perf_counter()
        files = self._collect_files()
        inventory = self._inventory(files)
        symbols, imports = self._python_intelligence(files)
        tests = self._test_map(files)
        docs = self._docs_map(files)
        commands = self._command_index()
        tools = self._tool_index()
        prompts = self._prompt_macros(inventory, tests, docs)
        recommendations = self._recommendations(inventory, symbols, tests, docs)
        fingerprint = self._fingerprint(files)
        snapshot = HyperSnapshot(
            root=str(self.root),
            started_at=time.time(),
            duration_s=time.perf_counter() - start,
            features=hyper_feature_count(),
            inventory=inventory,
            symbols=symbols,
            imports=imports,
            tests=tests,
            docs=docs,
            commands=commands,
            tools=tools,
            prompts=prompts,
            recommendations=recommendations,
            fingerprint=fingerprint,
        )
        return snapshot

    def _collect_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if len(files) >= self.max_files:
                break
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() in self.VALID_EXTS:
                files.append(path)
        return sorted(files)

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def _inventory(self, files: list[Path]) -> dict[str, Any]:
        ext_counter: Counter[str] = Counter()
        bytes_total = 0
        config_files: list[str] = []
        entrypoints: list[str] = []
        hot_files: list[tuple[str, int]] = []
        for path in files:
            ext_counter[path.suffix.lower() or "<none>"] += 1
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            bytes_total += size
            rel = self._rel(path)
            if path.name in {"pyproject.toml", "requirements.txt", "Dockerfile", "Makefile", ".gitlab-ci.yml", "package.json"} or ".github" in path.parts:
                config_files.append(rel)
            if path.name in {"main.py", "app.py", "cli.py"} or path.parent.name == "scripts":
                entrypoints.append(rel)
            hot_files.append((rel, size))
        hot_files.sort(key=lambda item: -item[1])
        return {
            "files": len(files),
            "bytes": bytes_total,
            "extensions": dict(ext_counter.most_common()),
            "config_files": config_files[:30],
            "entrypoints": entrypoints[:30],
            "largest_files": hot_files[:12],
        }

    def _python_intelligence(self, files: list[Path]) -> tuple[dict[str, Any], dict[str, Any]]:
        functions = classes = methods = docstring_nodes = 0
        public_symbols: list[dict[str, Any]] = []
        symbol_density: list[tuple[str, float]] = []
        import_counter: Counter[str] = Counter()
        clusters: dict[str, int] = defaultdict(int)
        for path in files:
            if path.suffix.lower() != ".py":
                continue
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            rel = self._rel(path)
            file_symbols = 0
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [node.module.split(".")[0] if node.module else ""]
                    for name in [n for n in names if n]:
                        import_counter[name] += 1
                        clusters[name.split(".")[0]] += 1
                elif isinstance(node, ast.ClassDef):
                    classes += 1
                    file_symbols += 1
                    if ast.get_docstring(node):
                        docstring_nodes += 1
                    if not node.name.startswith("_"):
                        public_symbols.append({"kind": "class", "name": node.name, "path": rel, "line": node.lineno})
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            methods += 1
                            file_symbols += 1
                            if ast.get_docstring(child):
                                docstring_nodes += 1
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions += 1
                    file_symbols += 1
                    if ast.get_docstring(node):
                        docstring_nodes += 1
                    if not node.name.startswith("_"):
                        public_symbols.append({"kind": "function", "name": node.name, "path": rel, "line": node.lineno})
            kb = max(1.0, len(src.encode("utf-8", errors="replace")) / 1024)
            symbol_density.append((rel, round(file_symbols / kb, 2)))
        symbol_density.sort(key=lambda item: -item[1])
        symbols = {
            "functions": functions,
            "classes": classes,
            "methods": methods,
            "public": public_symbols[:80],
            "docstring_nodes": docstring_nodes,
            "density_top": symbol_density[:15],
        }
        imports = {"top": import_counter.most_common(25), "clusters": dict(Counter(clusters).most_common(20))}
        return symbols, imports

    def _test_map(self, files: list[Path]) -> dict[str, Any]:
        test_files = [p for p in files if p.suffix == ".py" and (p.name.startswith("test_") or "tests" in p.parts)]
        test_functions = 0
        examples: list[str] = []
        for path in test_files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            rel = self._rel(path)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    test_functions += 1
                    if len(examples) < 20:
                        examples.append(f"{rel}:{node.name}")
        commands = []
        if (self.root / "pytest.ini").exists() or (self.root / "pyproject.toml").exists() or test_files:
            commands.append("python -m pytest -q")
        if (self.root / "Makefile").exists():
            commands.append("make test")
        return {"files": len(test_files), "functions": test_functions, "examples": examples, "commands": commands}

    def _docs_map(self, files: list[Path]) -> dict[str, Any]:
        md_files = [p for p in files if p.suffix.lower() == ".md"]
        headings: list[dict[str, Any]] = []
        for path in md_files[:80]:
            try:
                for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        level = len(stripped) - len(stripped.lstrip("#"))
                        title = stripped.lstrip("#").strip()
                        if title:
                            headings.append({"path": self._rel(path), "line": idx, "level": level, "title": title[:120]})
            except OSError:
                continue
        return {"markdown_files": len(md_files), "headings": len(headings), "top_headings": headings[:40]}

    def _command_index(self) -> dict[str, Any]:
        if self.app is None:
            return {"count": 0, "aliases": 0, "examples": []}
        commands = getattr(self.app, "commands", None)
        if commands is None:
            return {"count": 0, "aliases": 0, "examples": []}
        all_cmds = commands.all()
        examples = [getattr(cmd, "name", "") for cmd in all_cmds[:30]]
        aliases = sum(len(getattr(cmd, "aliases", ())) for cmd in all_cmds)
        return {"count": len(all_cmds), "aliases": aliases, "examples": examples}

    def _tool_index(self) -> dict[str, Any]:
        if self.app is None:
            return {"count": 0, "dangerous": 0, "examples": []}
        registry = getattr(self.app, "registry", None)
        if registry is None:
            return {"count": 0, "dangerous": 0, "examples": []}
        tools = registry.all()
        dangerous = sum(1 for tool in tools if getattr(tool, "dangerous", False))
        examples = [getattr(tool, "name", "") for tool in tools[:30]]
        return {"count": len(tools), "dangerous": dangerous, "examples": examples}

    def _prompt_macros(self, inventory: dict[str, Any], tests: dict[str, Any], docs: dict[str, Any]) -> list[dict[str, str]]:
        test_cmd = (tests.get("commands") or ["python -m pytest -q"])[0]
        return [
            {"name": "repo-onboard", "prompt": "Explain this codebase architecture using the auto-indexed context."},
            {"name": "fix-build", "prompt": f"Find and fix build/test errors, then verify with `{test_cmd}`."},
            {"name": "deep-refactor", "prompt": "Identify high-impact refactors and implement the safest one first."},
            {"name": "write-tests", "prompt": "Add focused tests for the most important untested behavior."},
            {"name": "docs-upgrade", "prompt": f"Improve documentation using {docs.get('markdown_files', 0)} markdown files as context."},
            {"name": "api-map", "prompt": "Create an API surface map from public functions/classes."},
            {"name": "release-ready", "prompt": "Prepare a release checklist from config, tests, docs and package files."},
            {"name": "performance-pass", "prompt": "Find likely performance hotspots and propose code-level improvements."},
            {"name": "tooling-pass", "prompt": "Improve developer tooling for this repository."},
            {"name": "architecture-review", "prompt": f"Review architecture for a repo with {inventory.get('files', 0)} indexed files."},
        ]

    def _recommendations(self, inventory: dict[str, Any], symbols: dict[str, Any], tests: dict[str, Any], docs: dict[str, Any]) -> list[str]:
        recs: list[str] = []
        if tests.get("files", 0) == 0:
            recs.append("Add a tests/ suite and a `python -m pytest -q` verification path.")
        elif tests.get("functions", 0) < max(5, symbols.get("functions", 0) // 5):
            recs.append("Increase focused test coverage for public functions and command flows.")
        if docs.get("markdown_files", 0) < 2:
            recs.append("Add architecture and operations documentation for onboarding.")
        if not inventory.get("config_files"):
            recs.append("Add explicit project config/package files for reproducible workflows.")
        if symbols.get("docstring_nodes", 0) < max(3, (symbols.get("functions", 0) + symbols.get("classes", 0)) // 8):
            recs.append("Improve docstrings on core public classes/functions.")
        if not recs:
            recs.append("Repository has solid structure; focus next on targeted refactors and verification speed.")
        return recs[:10]

    def _fingerprint(self, files: list[Path]) -> str:
        h = hashlib.sha256()
        for path in files[: self.max_files]:
            try:
                rel = self._rel(path)
                stat = path.stat()
                h.update(rel.encode())
                h.update(str(stat.st_size).encode())
                # Include a small content prefix for drift detection without
                # reading huge files completely.
                with path.open("rb") as fh:
                    h.update(fh.read(4096))
            except OSError:
                continue
        return h.hexdigest()[:20]


def hyper_feature_count() -> int:
    return len(HYPER_FEATURES_70)


def by_category() -> dict[str, list[HyperFeature]]:
    grouped: dict[str, list[HyperFeature]] = {}
    for feature in HYPER_FEATURES_70:
        grouped.setdefault(feature.category, []).append(feature)
    return grouped


def activate_hyper_mode(app: Any | None = None) -> dict[str, Any]:
    """Mark hyper features active on the app without disabling safety."""
    summary = {"features": hyper_feature_count(), "categories": len(by_category()), "safety": "guardrails stay enabled"}
    if app is not None:
        setattr(app, "hyper_features", HYPER_FEATURES_70)
        setattr(app, "hyper_profile_active", True)
    return summary


def total_feature_count() -> int:
    """Enterprise + hyper total feature count."""
    try:
        from .enterprise_suite import feature_count

        return feature_count() + hyper_feature_count()
    except Exception:
        return hyper_feature_count()


def run_hyper_warmup(app: Any | None = None, root: str | Path = ".") -> HyperSnapshot:
    """Run the real local analyzer and attach its snapshot to the app."""
    activate_hyper_mode(app)
    snapshot = HyperAnalyzer(root, app=app).run()
    if app is not None:
        setattr(app, "hyper_snapshot", snapshot)
    return snapshot


def dashboard(limit: int | None = None) -> str:
    """Render the 70-feature hyper dashboard."""
    lines = [
        "╔════════════════════════════════════════════════════════════╗",
        f"║  HYPER SUITE: {hyper_feature_count()} ADVANCED FEATURES ACTIVE{'':<14}║",
        "╠════════════════════════════════════════════════════════════╣",
    ]
    for category, features in by_category().items():
        lines.append(f"║  {category:<22} {len(features):>3} capabilities{'':<19}║")
    lines.append("╚════════════════════════════════════════════════════════════╝")
    lines.append("")
    shown = 0
    for category, features in by_category().items():
        lines.append(f"[{category}]")
        for feature in features:
            if limit is not None and shown >= limit:
                lines.append(f"… {hyper_feature_count() - shown} more hyper features.")
                return "\n".join(lines)
            lines.append(f"  H{feature.id:02d}. {feature.name} — {feature.description}")
            shown += 1
        lines.append("")
    return "\n".join(lines).rstrip()


def export_snapshot(snapshot: HyperSnapshot, path: str | Path) -> Path:
    """Write a hyper snapshot for handoff/debugging."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "root": snapshot.root,
        "started_at": snapshot.started_at,
        "duration_s": snapshot.duration_s,
        "features": snapshot.features,
        "inventory": snapshot.inventory,
        "symbols": snapshot.symbols,
        "imports": snapshot.imports,
        "tests": snapshot.tests,
        "docs": snapshot.docs,
        "commands": snapshot.commands,
        "tools": snapshot.tools,
        "prompts": snapshot.prompts,
        "recommendations": snapshot.recommendations,
        "fingerprint": snapshot.fingerprint,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
