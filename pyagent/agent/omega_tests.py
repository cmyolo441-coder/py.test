"""Omega test intelligence.

Maps source modules to likely tests, extracts pytest functions/assertions and
infers verification commands from local project files.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}


@dataclass
class TestFunction:
    path: str
    name: str
    line: int
    assertions: int


@dataclass
class TestIntelligence:
    source_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    test_functions: list[TestFunction] = field(default_factory=list)
    likely_pairs: dict[str, list[str]] = field(default_factory=dict)
    missing_tests: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        return {
            "source_files": len(self.source_files),
            "test_files": len(self.test_files),
            "test_functions": len(self.test_functions),
            "assertions": sum(t.assertions for t in self.test_functions),
            "missing_tests": len(self.missing_tests),
            "commands": self.commands,
        }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_py(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if len(out) >= max_files:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def _is_test(path: Path) -> bool:
    return path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in path.parts


def _assertions_in(node: ast.AST) -> int:
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            count += 1
        if isinstance(child, ast.Call):
            name = ""
            if isinstance(child.func, ast.Attribute):
                name = child.func.attr
            elif isinstance(child.func, ast.Name):
                name = child.func.id
            if name.startswith("assert") or name in {"raises", "equal", "true", "false"}:
                count += 1
    return count


def analyze_tests(root: str | Path = ".", max_files: int = 700) -> TestIntelligence:
    """Build a test intelligence map for a Python project."""
    root_path = Path(root).resolve()
    py_files = _iter_py(root_path, max_files=max_files)
    info = TestIntelligence()
    test_paths = [p for p in py_files if _is_test(p)]
    source_paths = [p for p in py_files if not _is_test(p)]
    info.test_files = [_rel(p, root_path) for p in test_paths]
    info.source_files = [_rel(p, root_path) for p in source_paths]

    test_text_by_path: dict[str, str] = {}
    for path in test_paths:
        rel = _rel(path, root_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            test_text_by_path[rel] = text
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                info.test_functions.append(TestFunction(rel, node.name, node.lineno, _assertions_in(node)))

    for src in source_paths:
        rel = _rel(src, root_path)
        stem = src.stem
        matches: list[str] = []
        expected = {f"test_{stem}.py", f"{stem}_test.py"}
        for test in test_paths:
            t_rel = _rel(test, root_path)
            if test.name in expected or stem in test.name:
                matches.append(t_rel)
            elif stem in test_text_by_path.get(t_rel, ""):
                matches.append(t_rel)
        if matches:
            info.likely_pairs[rel] = sorted(set(matches))
        else:
            info.missing_tests.append(rel)

    info.commands = infer_test_commands(root_path, bool(info.test_files))
    return info


def infer_test_commands(root: Path, has_tests: bool = True) -> list[str]:
    """Infer useful verification commands from project files."""
    commands: list[str] = []
    pyproject = root / "pyproject.toml"
    if has_tests or (root / "pytest.ini").exists() or pyproject.exists():
        commands.append("python -m pytest -q")
    if (root / "Makefile").exists():
        try:
            content = (root / "Makefile").read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        for target in ["test", "health", "lint"]:
            if re.search(rf"^{target}:", content, flags=re.MULTILINE):
                commands.append(f"make {target}")
    if (root / "scripts" / "healthcheck.py").exists():
        commands.append("python scripts/healthcheck.py")
    commands.append("python -m compileall -q agent scripts tests")
    return list(dict.fromkeys(commands))


def pytest_targets_for_files(info: TestIntelligence, files: list[str], max_targets: int = 10) -> list[str]:
    """Return likely pytest targets for changed source files."""
    targets: list[str] = []
    for file in files:
        for match in info.likely_pairs.get(file, []):
            targets.append(match)
    if not targets:
        targets = info.test_files[:max_targets]
    return list(dict.fromkeys(targets))[:max_targets]


def test_gap_summary(info: TestIntelligence, limit: int = 12) -> list[str]:
    """Human-readable missing-test hints."""
    return [f"Add/confirm tests for {path}" for path in info.missing_tests[:limit]]
