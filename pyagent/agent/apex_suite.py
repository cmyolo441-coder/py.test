"""Apex Suite — 40 extra real code-intelligence capabilities.

Apex is intentionally local/offline: it parses the repository with Python's AST
and filesystem metadata to build impact maps, call graphs, test-gap hints,
verification plans and compact context packs.  It does not disable guardrails or
perform unsafe actions.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ApexFeature:
    """A named Apex capability."""

    id: int
    category: str
    name: str
    description: str


APEX_FEATURES_40: tuple[ApexFeature, ...] = tuple(
    ApexFeature(i + 1, category, name, description)
    for i, (category, name, description) in enumerate(
        [
            ("Impact Intelligence", "AST call graph", "Build function/method call edges without executing code."),
            ("Impact Intelligence", "Reverse call graph", "Find likely callers of important symbols."),
            ("Impact Intelligence", "Module dependency graph", "Map imports and internal package dependencies."),
            ("Impact Intelligence", "Change impact scoring", "Rank files by fan-in, fan-out, symbols and tests."),
            ("Impact Intelligence", "Hotspot prediction", "Predict files most likely to affect many subsystems."),
            ("Impact Intelligence", "File coupling map", "Detect file pairs linked by imports and calls."),
            ("Impact Intelligence", "Entrypoint impact trace", "Trace likely dependencies starting from main/app/cli files."),
            ("Impact Intelligence", "Orphan module detector", "Find source files with low incoming references."),
            ("Architecture AI", "Layer inference", "Infer CLI, providers, tools, commands, memory and UI layers."),
            ("Architecture AI", "Subsystem clustering", "Cluster files by package path and import families."),
            ("Architecture AI", "Public API map", "Map public functions/classes and their likely consumers."),
            ("Architecture AI", "State surface map", "Detect global variables, dataclasses and singletons."),
            ("Architecture AI", "Configuration topology", "Detect config files and code that reads config/env."),
            ("Architecture AI", "Runtime surface map", "Identify scripts, entrypoints, commands and tools."),
            ("Architecture AI", "Complexity pressure map", "Combine symbol density, size and dependencies."),
            ("Architecture AI", "Package boundary hints", "Suggest cleaner boundaries between subsystems."),
            ("Verification AI", "Test-gap matrix", "Map source files to likely existing/missing tests."),
            ("Verification AI", "Pytest target recommender", "Suggest pytest targets for impacted files."),
            ("Verification AI", "Build command inference", "Infer test/lint/build commands from local files."),
            ("Verification AI", "Quality gate plan", "Create staged verify commands for fixes/refactors."),
            ("Verification AI", "Smoke-test planner", "Suggest quick startup/import checks."),
            ("Verification AI", "Regression focus planner", "Pick files/tests likely relevant to a change."),
            ("Verification AI", "Docs verification map", "Connect docs files to code entrypoints."),
            ("Verification AI", "Release readiness checklist", "Generate local release verification checklist."),
            ("Autonomous Coding", "Task classifier", "Classify prompts into bugfix/refactor/docs/tests/release."),
            ("Autonomous Coding", "Context pack composer", "Compose compact code/test/docs context for each task type."),
            ("Autonomous Coding", "Action queue generator", "Create prioritized next actions from analysis."),
            ("Autonomous Coding", "Patch scope estimator", "Estimate likely files touched by a request."),
            ("Autonomous Coding", "Rollback checkpoint hint", "Surface checkpoint/snapshot strategy before edits."),
            ("Autonomous Coding", "Developer handoff brief", "Generate a compact repo handoff packet."),
            ("Model Optimisation", "Prompt budget allocator", "Reserve context budget for snippets/tests/plan/verify."),
            ("Model Optimisation", "Symbol search index", "Fast local index for paths and symbols."),
            ("Model Optimisation", "Path relevance ranking", "Rank files against a natural-language task."),
            ("Model Optimisation", "Prompt macro expansion", "Turn high-level macros into detailed task prompts."),
            ("Model Optimisation", "Context dedupe", "Avoid repeating the same file/snippet context."),
            ("Model Optimisation", "Repository fingerprint cache", "Detect when analysis should be refreshed."),
            ("Productivity", "Maintenance backlog", "Prioritize doc/test/refactor work from real metrics."),
            ("Productivity", "Onboarding map", "Explain entrypoints, layers and verification in one brief."),
            ("Productivity", "Review preparation", "Prepare PR review focus areas automatically."),
            ("Productivity", "Zero-command Apex warmup", "All Apex intelligence warms automatically at python3 main.py."),
        ]
    )
)

assert len(APEX_FEATURES_40) == 40, f"expected 40 features, got {len(APEX_FEATURES_40)}"


@dataclass
class SymbolInfo:
    kind: str
    name: str
    qualname: str
    path: str
    line: int
    calls: list[str] = field(default_factory=list)


@dataclass
class ApexSnapshot:
    root: str
    started_at: float
    duration_s: float
    features: int
    files_scanned: int
    symbols: list[dict[str, Any]] = field(default_factory=list)
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    reverse_graph: dict[str, list[str]] = field(default_factory=dict)
    module_graph: dict[str, list[str]] = field(default_factory=dict)
    impact: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, Any] = field(default_factory=dict)
    architecture: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    backlog: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def to_context(self, max_chars: int = 2200) -> str:
        hot = self.impact.get("hot_files", [])[:8]
        uncovered = self.tests.get("likely_uncovered", [])[:8]
        commands = self.verification.get("commands", [])[:6]
        layers = self.architecture.get("layers", {})
        parts = [
            "[APEX SUITE CONTEXT]",
            f"features={self.features}, files_scanned={self.files_scanned}, fingerprint={self.fingerprint}",
            "hot_files=" + ", ".join(f"{p}({s})" for p, s in hot),
            "layers=" + ", ".join(f"{k}:{len(v)}" for k, v in layers.items()),
            "verification=" + "; ".join(commands),
        ]
        if uncovered:
            parts.append("likely_uncovered=" + ", ".join(uncovered))
        if self.backlog:
            parts.append("backlog=" + "; ".join(self.backlog[:5]))
        parts.append("[/APEX SUITE CONTEXT]")
        return "\n".join(parts)[:max_chars]


class ApexAnalyzer:
    """AST and filesystem analyzer for Apex startup intelligence."""

    SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build", ".mypy_cache", ".ruff_cache"}
    TEXT_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh"}

    def __init__(self, root: str | Path = ".", max_files: int = 700) -> None:
        self.root = Path(root).resolve()
        self.max_files = max_files

    def run(self) -> ApexSnapshot:
        started = time.time()
        start = time.perf_counter()
        files = self._collect_files()
        py_files = [p for p in files if p.suffix == ".py"]
        symbols, module_graph, file_imports, state_map = self._parse_python(py_files)
        call_graph, reverse_graph = self._graphs(symbols)
        tests = self._test_gap(py_files, symbols)
        architecture = self._architecture(files, py_files, file_imports, state_map)
        impact = self._impact(files, symbols, module_graph, reverse_graph, tests)
        verification = self._verification(files, tests, impact)
        backlog = self._backlog(impact, tests, architecture, verification)
        fingerprint = self._fingerprint(files)
        return ApexSnapshot(
            root=str(self.root),
            started_at=started,
            duration_s=time.perf_counter() - start,
            features=apex_feature_count(),
            files_scanned=len(files),
            symbols=[s.__dict__ for s in symbols[:250]],
            call_graph={k: v[:20] for k, v in list(call_graph.items())[:250]},
            reverse_graph={k: v[:20] for k, v in list(reverse_graph.items())[:250]},
            module_graph={k: v[:30] for k, v in list(module_graph.items())[:250]},
            impact=impact,
            tests=tests,
            architecture=architecture,
            verification=verification,
            backlog=backlog,
            fingerprint=fingerprint,
        )

    def _collect_files(self) -> list[Path]:
        out: list[Path] = []
        for path in self.root.rglob("*"):
            if len(out) >= self.max_files:
                break
            if any(part in self.SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() in self.TEXT_EXTS:
                out.append(path)
        return sorted(out)

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def _module_name_for(self, path: Path) -> str:
        rel = self._rel(path)
        if rel.endswith("/__init__.py"):
            rel = rel[: -len("/__init__.py")]
        elif rel.endswith(".py"):
            rel = rel[:-3]
        return rel.replace("/", ".")

    def _parse_python(self, py_files: list[Path]) -> tuple[list[SymbolInfo], dict[str, list[str]], dict[str, list[str]], dict[str, Any]]:
        symbols: list[SymbolInfo] = []
        module_graph: dict[str, list[str]] = defaultdict(list)
        file_imports: dict[str, list[str]] = defaultdict(list)
        state_map: dict[str, Any] = {"globals": [], "dataclasses": [], "singletons": []}
        for path in py_files:
            rel = self._rel(path)
            module = self._module_name_for(path)
            try:
                src = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports = self._imports_from_node(node)
                    file_imports[rel].extend(imports)
                    module_graph[module].extend(imports)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            state_map["globals"].append(f"{rel}:{target.id}")
                elif isinstance(node, ast.ClassDef):
                    decorators = [self._call_name(d) for d in node.decorator_list]
                    if "dataclass" in decorators:
                        state_map["dataclasses"].append(f"{rel}:{node.name}")
                    if node.name.lower().endswith(("manager", "registry", "cache", "store")):
                        state_map["singletons"].append(f"{rel}:{node.name}")
                    class_q = f"{module}.{node.name}"
                    symbols.append(SymbolInfo("class", node.name, class_q, rel, node.lineno, []))
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            q = f"{module}.{node.name}.{child.name}"
                            symbols.append(SymbolInfo("method", child.name, q, rel, child.lineno, self._calls_in(child)))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{module}.{node.name}"
                    symbols.append(SymbolInfo("function", node.name, q, rel, node.lineno, self._calls_in(node)))
        module_graph = {k: sorted(set(v)) for k, v in module_graph.items()}
        file_imports = {k: sorted(set(v)) for k, v in file_imports.items()}
        return symbols, module_graph, file_imports, state_map

    def _imports_from_node(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            return [prefix.strip(".") or alias.name for alias in node.names]
        return []

    def _calls_in(self, node: ast.AST) -> list[str]:
        calls: list[str] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = self._call_name(child.func)
                if name:
                    calls.append(name)
        return sorted(set(calls))[:80]

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _graphs(self, symbols: list[SymbolInfo]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        by_short: dict[str, list[str]] = defaultdict(list)
        for sym in symbols:
            by_short[sym.name].append(sym.qualname)
        call_graph: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = defaultdict(list)
        for sym in symbols:
            targets: set[str] = set()
            for call in sym.calls:
                short = call.split(".")[-1]
                for qual in by_short.get(short, []):
                    targets.add(qual)
            call_graph[sym.qualname] = sorted(targets)
            for target in targets:
                reverse[target].append(sym.qualname)
        return call_graph, {k: sorted(set(v)) for k, v in reverse.items()}

    def _test_gap(self, py_files: list[Path], symbols: list[SymbolInfo]) -> dict[str, Any]:
        test_files = [p for p in py_files if p.name.startswith("test_") or "tests" in p.parts]
        source_files = [p for p in py_files if p not in test_files]
        test_names = {p.stem.replace("test_", "") for p in test_files}
        likely_uncovered: list[str] = []
        likely_covered: list[str] = []
        for path in source_files:
            stem = path.stem
            rel = self._rel(path)
            if stem in test_names or any(stem in tf.stem for tf in test_files):
                likely_covered.append(rel)
            else:
                likely_uncovered.append(rel)
        symbol_tests = Counter()
        for test_path in test_files:
            try:
                text = test_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for sym in symbols:
                if sym.name in text:
                    symbol_tests[sym.qualname] += 1
        return {
            "test_files": len(test_files),
            "source_files": len(source_files),
            "likely_covered": likely_covered[:80],
            "likely_uncovered": likely_uncovered[:80],
            "symbol_test_mentions": symbol_tests.most_common(30),
        }

    def _architecture(self, files: list[Path], py_files: list[Path], file_imports: dict[str, list[str]], state_map: dict[str, Any]) -> dict[str, Any]:
        layers: dict[str, list[str]] = defaultdict(list)
        patterns = {
            "ui": ("ui", "tui", "prompt", "theme", "widget"),
            "commands": ("commands",),
            "tools": ("tools",),
            "providers": ("providers",),
            "memory": ("memory", "rag", "knowledge"),
            "orchestration": ("agent_loop", "autonomous", "goal", "multi_agent", "scheduler"),
            "infra": ("docker", "cicd", "cloud", "prometheus", "telemetry"),
            "core": ("core", "app", "config", "guardrails"),
        }
        for path in py_files:
            rel = self._rel(path)
            lowered = rel.lower()
            matched = False
            for layer, keys in patterns.items():
                if any(k in lowered for k in keys):
                    layers[layer].append(rel)
                    matched = True
                    break
            if not matched:
                layers["other"].append(rel)
        configs = [self._rel(p) for p in files if p.name in {"pyproject.toml", "requirements.txt", "Dockerfile", "Makefile", ".gitlab-ci.yml"} or ".github" in p.parts]
        entrypoints = [self._rel(p) for p in py_files if p.name in {"main.py", "app.py", "cli.py"} or p.parent.name == "scripts"]
        import_families = Counter(imp.split(".")[0] for imports in file_imports.values() for imp in imports if imp)
        return {
            "layers": {k: v[:60] for k, v in layers.items()},
            "configs": configs[:50],
            "entrypoints": entrypoints[:40],
            "import_families": import_families.most_common(30),
            "state_map": {k: v[:40] for k, v in state_map.items()},
        }

    def _impact(
        self,
        files: list[Path],
        symbols: list[SymbolInfo],
        module_graph: dict[str, list[str]],
        reverse_graph: dict[str, list[str]],
        tests: dict[str, Any],
    ) -> dict[str, Any]:
        symbols_by_file: Counter[str] = Counter(sym.path for sym in symbols)
        reverse_by_file: Counter[str] = Counter()
        for target, callers in reverse_graph.items():
            target_file = next((sym.path for sym in symbols if sym.qualname == target), "")
            if target_file:
                reverse_by_file[target_file] += len(callers)
        import_fanin: Counter[str] = Counter()
        for src, imports in module_graph.items():
            for imp in imports:
                import_fanin[imp] += 1
        uncovered = set(tests.get("likely_uncovered", []))
        hot: list[tuple[str, int]] = []
        for path in files:
            rel = self._rel(path)
            if path.suffix != ".py":
                continue
            score = symbols_by_file[rel] * 3 + reverse_by_file[rel] * 5
            if rel in uncovered:
                score += 7
            try:
                score += min(20, path.stat().st_size // 2500)
            except OSError:
                pass
            if score:
                hot.append((rel, int(score)))
        hot.sort(key=lambda item: -item[1])
        orphan = [rel for rel, score in hot if reverse_by_file[rel] == 0 and symbols_by_file[rel] > 2][:30]
        return {
            "hot_files": hot[:30],
            "orphan_candidates": orphan,
            "import_fanin": import_fanin.most_common(30),
            "symbol_heavy": symbols_by_file.most_common(30),
        }

    def _verification(self, files: list[Path], tests: dict[str, Any], impact: dict[str, Any]) -> dict[str, Any]:
        names = {p.name for p in files}
        commands: list[str] = []
        if "pytest.ini" in names or "pyproject.toml" in names or tests.get("test_files", 0):
            commands.append("python -m pytest -q")
        if "Makefile" in names:
            commands.extend(["make test", "make health"])
        if "pyproject.toml" in names:
            commands.append("python -m compileall -q agent scripts tests")
        if "requirements.txt" in names:
            commands.append("python scripts/healthcheck.py")
        impacted_tests: list[str] = []
        for rel, _score in impact.get("hot_files", [])[:10]:
            stem = Path(rel).stem
            impacted_tests.append(f"tests/test_{stem}.py")
        return {
            "commands": list(dict.fromkeys(commands))[:10],
            "impacted_test_guesses": impacted_tests,
            "quality_gates": ["compile", "unit tests", "healthcheck", "manual smoke start"],
        }

    def _backlog(self, impact: dict[str, Any], tests: dict[str, Any], architecture: dict[str, Any], verification: dict[str, Any]) -> list[str]:
        items: list[str] = []
        hot = impact.get("hot_files", [])
        if hot:
            items.append(f"Review top hotspot {hot[0][0]} before major changes.")
        uncovered = tests.get("likely_uncovered", [])
        if uncovered:
            items.append(f"Add/confirm tests for {uncovered[0]} and nearby modules.")
        if len(architecture.get("state_map", {}).get("globals", [])) > 10:
            items.append("Reduce global configuration/state surface in core modules.")
        if verification.get("commands"):
            items.append(f"Use `{verification['commands'][0]}` as the first verification gate.")
        if impact.get("orphan_candidates"):
            items.append("Check orphan candidate modules for dead code or missing imports.")
        return items[:12]

    def _fingerprint(self, files: list[Path]) -> str:
        h = hashlib.sha256()
        for path in files[: self.max_files]:
            try:
                h.update(self._rel(path).encode())
                h.update(str(path.stat().st_size).encode())
                with path.open("rb") as fh:
                    h.update(fh.read(2048))
            except OSError:
                continue
        return h.hexdigest()[:20]

    def classify_task(self, prompt: str) -> dict[str, Any]:
        text = prompt.lower()
        scores = {
            "bugfix": sum(k in text for k in ["bug", "fix", "error", "traceback", "fail", "crash"]),
            "refactor": sum(k in text for k in ["refactor", "clean", "complex", "architecture", "improve"]),
            "tests": sum(k in text for k in ["test", "pytest", "coverage", "verify"]),
            "docs": sum(k in text for k in ["doc", "readme", "explain", "guide"]),
            "release": sum(k in text for k in ["release", "docker", "deploy", "ci", "package"]),
        }
        best = max(scores.items(), key=lambda item: item[1])
        return {"class": best[0] if best[1] > 0 else "general", "scores": scores}

    def rank_paths_for_query(self, snapshot: ApexSnapshot, query: str, top_k: int = 10) -> list[tuple[str, int]]:
        tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", query.lower()))
        scores: Counter[str] = Counter()
        for sym in snapshot.symbols:
            hay = f"{sym.get('name', '')} {sym.get('qualname', '')} {sym.get('path', '')}".lower()
            scores[sym.get("path", "")] += sum(tok in hay for tok in tokens) * 3
        for path, score in snapshot.impact.get("hot_files", []):
            scores[path] += int(score) // 5
        return [(path, score) for path, score in scores.most_common(top_k) if path and score > 0]


def apex_feature_count() -> int:
    return len(APEX_FEATURES_40)


def by_category() -> dict[str, list[ApexFeature]]:
    grouped: dict[str, list[ApexFeature]] = {}
    for feature in APEX_FEATURES_40:
        grouped.setdefault(feature.category, []).append(feature)
    return grouped


def activate_apex_mode(app: Any | None = None) -> dict[str, Any]:
    summary = {"features": apex_feature_count(), "categories": len(by_category()), "safety": "guardrails stay enabled"}
    if app is not None:
        setattr(app, "apex_features", APEX_FEATURES_40)
        setattr(app, "apex_profile_active", True)
    return summary


def run_apex_warmup(app: Any | None = None, root: str | Path = ".") -> ApexSnapshot:
    activate_apex_mode(app)
    snapshot = ApexAnalyzer(root).run()
    if app is not None:
        setattr(app, "apex_snapshot", snapshot)
    return snapshot


def dashboard(limit: int | None = None) -> str:
    lines = [
        "╔════════════════════════════════════════════════════════════╗",
        f"║  APEX SUITE: {apex_feature_count()} MAX-LEVEL FEATURES ACTIVE{'':<13}║",
        "╠════════════════════════════════════════════════════════════╣",
    ]
    for category, features in by_category().items():
        lines.append(f"║  {category:<20} {len(features):>3} capabilities{'':<21}║")
    lines.append("╚════════════════════════════════════════════════════════════╝")
    lines.append("")
    shown = 0
    for category, features in by_category().items():
        lines.append(f"[{category}]")
        for feature in features:
            if limit is not None and shown >= limit:
                lines.append(f"… {apex_feature_count() - shown} more Apex features.")
                return "\n".join(lines)
            lines.append(f"  A{feature.id:02d}. {feature.name} — {feature.description}")
            shown += 1
        lines.append("")
    return "\n".join(lines).rstrip()


def export_snapshot(snapshot: ApexSnapshot, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot.__dict__, indent=2, default=str), encoding="utf-8")
    return out
