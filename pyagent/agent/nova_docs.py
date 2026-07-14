"""Nova documentation intelligence."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}


@dataclass
class DocsReport:
    markdown_files: int = 0
    headings: list[dict[str, Any]] = field(default_factory=list)
    public_symbols: int = 0
    documented_symbols: int = 0
    missing_docstrings: list[str] = field(default_factory=list)
    suggested_sections: list[str] = field(default_factory=list)

    def coverage(self) -> float:
        return (self.documented_symbols / self.public_symbols) if self.public_symbols else 1.0

    def stats(self) -> dict[str, Any]:
        return {
            "markdown_files": self.markdown_files,
            "headings": len(self.headings),
            "public_symbols": self.public_symbols,
            "documented_symbols": self.documented_symbols,
            "doc_coverage": round(self.coverage(), 3),
            "missing_docstrings": len(self.missing_docstrings),
        }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def analyze_docs(root: str | Path = ".", max_files: int = 600) -> DocsReport:
    root_path = Path(root).resolve()
    report = DocsReport()
    md_files = [p for p in root_path.rglob("*.md") if not any(part in _SKIP for part in p.parts)]
    report.markdown_files = len(md_files)
    for path in md_files[:max_files]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            m = re.match(r"^(#+)\s+(.+)", line.strip())
            if m:
                report.headings.append({"path": _rel(path, root_path), "line": i, "level": len(m.group(1)), "title": m.group(2)[:120]})
    for path in root_path.rglob("*.py"):
        if any(part in _SKIP for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        rel = _rel(path, root_path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                report.public_symbols += 1
                if ast.get_docstring(node):
                    report.documented_symbols += 1
                elif len(report.missing_docstrings) < 80:
                    report.missing_docstrings.append(f"{rel}:{node.lineno}:{node.name}")
    report.suggested_sections = suggest_sections(report)
    return report


def suggest_sections(report: DocsReport) -> list[str]:
    existing = "\n".join(h["title"].lower() for h in report.headings)
    sections = []
    for title in ["Installation", "Quickstart", "Architecture", "Commands", "Tools", "Configuration", "Testing", "Troubleshooting", "Contributing"]:
        if title.lower() not in existing:
            sections.append(title)
    if report.coverage() < 0.5:
        sections.append("API Reference")
    return sections[:12]


def docs_context(report: DocsReport) -> str:
    lines = [f"docs: {report.markdown_files} md files, {len(report.headings)} headings, docstring coverage {report.coverage():.1%}"]
    if report.suggested_sections:
        lines.append("suggest sections: " + ", ".join(report.suggested_sections))
    if report.missing_docstrings:
        lines.append("missing docstrings: " + ", ".join(report.missing_docstrings[:10]))
    return "\n".join(lines)


def make_toc(headings: list[dict[str, Any]], max_items: int = 30) -> str:
    rows = []
    for h in headings[:max_items]:
        indent = "  " * max(0, h.get("level", 1) - 1)
        rows.append(f"{indent}- {h.get('title')} ({h.get('path')}:{h.get('line')})")
    return "\n".join(rows)
