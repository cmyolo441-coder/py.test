"""Nova quality scoring."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

_SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}


@dataclass
class FileQuality:
    path: str
    score: float
    lines: int
    functions: int
    classes: int
    comments: int
    avg_function_len: float
    issues: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    files: list[FileQuality] = field(default_factory=list)

    def overall_score(self) -> float:
        return round(mean([f.score for f in self.files]) if self.files else 100.0, 2)

    def stats(self) -> dict[str, Any]:
        return {
            "files": len(self.files),
            "overall_score": self.overall_score(),
            "worst_files": [(f.path, f.score) for f in sorted(self.files, key=lambda x: x.score)[:12]],
            "issues": sum(len(f.issues) for f in self.files),
        }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _func_len(node: ast.AST) -> int:
    return max(1, (getattr(node, "end_lineno", getattr(node, "lineno", 1)) or 1) - (getattr(node, "lineno", 1) or 1) + 1)


def analyze_quality(root: str | Path = ".", max_files: int = 700) -> QualityReport:
    root_path = Path(root).resolve()
    result = QualityReport()
    for path in root_path.rglob("*.py"):
        if len(result.files) >= max_files:
            break
        if any(part in _SKIP for part in path.parts):
            continue
        result.files.append(analyze_file_quality(path, root_path))
    return result


def analyze_file_quality(path: Path, root: Path) -> FileQuality:
    rel = _rel(path, root)
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return FileQuality(rel, 0, 0, 0, 0, 0, 0.0, ["unreadable"])
    lines = src.splitlines()
    comments = sum(1 for line in lines if line.strip().startswith("#"))
    issues: list[str] = []
    functions = classes = 0
    func_lens: list[int] = []
    score = 100.0
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return FileQuality(rel, 10, len(lines), 0, 0, comments, 0.0, ["syntax-error"])
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes += 1
            if not ast.get_docstring(node) and not node.name.startswith("_"):
                score -= 1.0
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions += 1
            fl = _func_len(node)
            func_lens.append(fl)
            if fl > 70:
                issues.append(f"long function {node.name} ({fl} lines)")
                score -= min(12, fl / 20)
            if len(node.args.args) > 6:
                issues.append(f"many args {node.name}")
                score -= 3
            if not ast.get_docstring(node) and not node.name.startswith("_"):
                score -= 0.5
    if len(lines) > 800:
        issues.append("large file")
        score -= 10
    if lines and comments / len(lines) < 0.02 and functions + classes > 10:
        issues.append("very low comment ratio")
        score -= 4
    long_lines = sum(1 for line in lines if len(line) > 140)
    if long_lines:
        issues.append(f"{long_lines} very long lines")
        score -= min(8, long_lines / 5)
    avg_len = mean(func_lens) if func_lens else 0.0
    return FileQuality(rel, round(max(0.0, min(100.0, score)), 2), len(lines), functions, classes, comments, round(avg_len, 2), issues[:20])


def quality_backlog(report: QualityReport, limit: int = 10) -> list[str]:
    items: list[str] = []
    for f in sorted(report.files, key=lambda x: x.score)[:limit]:
        if f.issues:
            items.append(f"Improve {f.path} (score {f.score}): {', '.join(f.issues[:3])}")
    return items


def quality_context(report: QualityReport) -> str:
    stats = report.stats()
    lines = [f"quality score: {stats['overall_score']} across {stats['files']} files"]
    for path, score in stats["worst_files"][:8]:
        lines.append(f"- {score}: {path}")
    return "\n".join(lines)
