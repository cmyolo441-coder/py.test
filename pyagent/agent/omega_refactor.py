"""Omega refactor intelligence.

Real AST-based refactor analysis: complexity, nesting, argument count,
large modules, duplicate function structure and global-state pressure.
"""
from __future__ import annotations

import ast
import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}


@dataclass
class RefactorFinding:
    rule: str
    path: str
    line: int
    symbol: str
    score: int
    message: str


@dataclass
class RefactorReport:
    files_scanned: int = 0
    findings: list[RefactorFinding] = field(default_factory=list)
    duplicate_groups: list[list[str]] = field(default_factory=list)
    module_sizes: list[tuple[str, int]] = field(default_factory=list)
    globals_by_file: dict[str, int] = field(default_factory=dict)

    def summary(self, limit: int = 10) -> str:
        lines = [f"Refactor report: {self.files_scanned} files, {len(self.findings)} findings"]
        for f in sorted(self.findings, key=lambda x: -x.score)[:limit]:
            lines.append(f"  [{f.score:>2}] {f.rule} {f.path}:{f.line} {f.symbol} — {f.message}")
        if self.duplicate_groups:
            lines.append(f"  duplicate groups: {len(self.duplicate_groups)}")
        return "\n".join(lines)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.BoolOp, ast.IfExp, ast.Assert, ast.Match)):
            score += 1
    return score


def _max_nesting(node: ast.AST) -> int:
    branch_types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)

    def visit(n: ast.AST, depth: int) -> int:
        next_depth = depth + 1 if isinstance(n, branch_types) else depth
        child_depths = [visit(c, next_depth) for c in ast.iter_child_nodes(n)]
        return max([next_depth, *child_depths]) if child_depths else next_depth

    return visit(node, 0)


def _hash_function(node: ast.AST) -> str:
    # Normalise names/line metadata by using AST dump without attributes.
    return hashlib.sha1(ast.dump(node, annotate_fields=False, include_attributes=False).encode()).hexdigest()[:12]


def _iter_py(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if len(out) >= max_files:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def analyze_refactors(root: str | Path = ".", max_files: int = 700) -> RefactorReport:
    """Analyze Python files and return refactor opportunities."""
    root_path = Path(root).resolve()
    report = RefactorReport()
    function_hashes: dict[str, list[str]] = defaultdict(list)
    for path in _iter_py(root_path, max_files=max_files):
        rel = _rel(path, root_path)
        report.files_scanned += 1
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        lines = src.count("\n") + 1
        report.module_sizes.append((rel, lines))
        globals_count = 0
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                globals_count += sum(1 for target in node.targets if isinstance(target, ast.Name))
        report.globals_by_file[rel] = globals_count
        if lines > 700:
            report.findings.append(RefactorFinding("large-module", rel, 1, path.stem, min(99, lines // 20), f"module has {lines} lines"))
        if globals_count > 15:
            report.findings.append(RefactorFinding("global-state", rel, 1, path.stem, globals_count, f"{globals_count} module-level assignments"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cx = _complexity(node)
            nesting = _max_nesting(node)
            args = len(getattr(node.args, "args", [])) + len(getattr(node.args, "kwonlyargs", []))
            length = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
            qual = node.name
            h = _hash_function(node)
            if length > 4:
                function_hashes[h].append(f"{rel}:{node.lineno}:{qual}")
            if cx > 10:
                report.findings.append(RefactorFinding("high-complexity", rel, node.lineno, qual, cx, f"complexity {cx}; split branches"))
            if nesting > 4:
                report.findings.append(RefactorFinding("deep-nesting", rel, node.lineno, qual, nesting * 3, f"nesting depth {nesting}"))
            if args > 6:
                report.findings.append(RefactorFinding("too-many-args", rel, node.lineno, qual, args * 2, f"{args} parameters"))
            if length > 80:
                report.findings.append(RefactorFinding("long-function", rel, node.lineno, qual, min(99, length // 2), f"{length} lines"))
    for locations in function_hashes.values():
        if len(locations) > 1:
            report.duplicate_groups.append(locations[:10])
            for loc in locations[:3]:
                path_s, line_s, name = loc.split(":", 2)
                report.findings.append(RefactorFinding("duplicate-structure", path_s, int(line_s), name, 18, "similar function structure elsewhere"))
    report.module_sizes.sort(key=lambda item: -item[1])
    return report


def refactor_backlog(report: RefactorReport, limit: int = 8) -> list[str]:
    """Turn refactor findings into concise backlog items."""
    items: list[str] = []
    for f in sorted(report.findings, key=lambda x: -x.score)[:limit]:
        items.append(f"{f.rule}: {f.path}:{f.line} `{f.symbol}` — {f.message}")
    if report.duplicate_groups:
        items.append(f"Consolidate {len(report.duplicate_groups)} duplicate function-structure group(s).")
    return items


def refactor_stats(report: RefactorReport) -> dict[str, Any]:
    return {
        "files_scanned": report.files_scanned,
        "findings": len(report.findings),
        "duplicates": len(report.duplicate_groups),
        "largest_modules": report.module_sizes[:10],
        "rules": dict(Counter(f.rule for f in report.findings).most_common()),
    }
