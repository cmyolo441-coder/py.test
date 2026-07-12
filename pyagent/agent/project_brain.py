"""Project Brain: live workspace intelligence for the terminal agent.

The ProjectBrain builds a local, real-time-ish map of the current project:
files, Python symbols, imports, tests, entrypoints, tracebacks, command events,
and goal timelines. It is designed to make the terminal agent act from real
workspace signals instead of guessing.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "env",
}

TRACEBACK_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>[^\n]+))?')


@dataclass
class PythonSymbol:
    kind: str
    name: str
    file: str
    line: int
    end_line: int | None = None
    signature: str = ""


@dataclass
class ProjectSnapshot:
    root: str
    scanned_at: float
    file_count: int
    python_file_count: int
    test_count: int
    entrypoints: list[str]
    config_files: list[str]
    recent_files: list[str]
    symbols: list[dict[str, Any]]
    imports: dict[str, list[str]]


class ProjectBrain:
    """Local project intelligence engine."""

    def __init__(self, root: str | Path = ".", db_path: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.db_path = Path(db_path) if db_path else self.root / ".pyagent_project_brain.sqlite3"
        self._init_db()
        self.snapshot: ProjectSnapshot | None = None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    graph TEXT NOT NULL
                )
            """)

    def remember_event(self, kind: str, title: str, payload: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, kind, title, payload) VALUES (?, ?, ?, ?)",
                (time.time(), kind, title, json.dumps(payload or {}, sort_keys=True)),
            )

    def iter_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if any(part in IGNORED_DIRS for part in path.relative_to(self.root).parts):
                continue
            if path.is_file():
                files.append(path)
        return files

    def _rel(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return str(path)

    def extract_python_symbols(self, path: Path) -> tuple[list[PythonSymbol], list[str]]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except Exception:
            return [], []

        symbols: list[PythonSymbol] = []
        imports: list[str] = []
        rel = self._rel(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                symbols.append(PythonSymbol("function", node.name, rel, node.lineno, getattr(node, "end_lineno", None), f"{node.name}({', '.join(args)})"))
            elif isinstance(node, ast.ClassDef):
                symbols.append(PythonSymbol("class", node.name, rel, node.lineno, getattr(node, "end_lineno", None), node.name))
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return symbols, sorted(set(imports))

    def detect_entrypoints(self, files: list[Path]) -> list[str]:
        hits: list[str] = []
        for path in files:
            rel = self._rel(path)
            name = path.name.lower()
            if name in {"main.py", "app.py", "cli.py"} or rel in {"main.py", "agent/cli.py", "agent/app.py"}:
                hits.append(rel)
                continue
            if path.suffix == ".py":
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    if "if __name__" in text and "__main__" in text:
                        hits.append(rel)
                except Exception:
                    pass
        return sorted(set(hits))

    def scan(self) -> ProjectSnapshot:
        files = self.iter_files()
        py_files = [p for p in files if p.suffix == ".py"]
        tests = [self._rel(p) for p in files if p.name.startswith("test_") or "/tests/" in f"/{self._rel(p)}"]
        config_names = {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "Dockerfile", "Makefile", "pytest.ini"}
        config_files = sorted(self._rel(p) for p in files if p.name in config_names or p.name.endswith((".yaml", ".yml", ".toml")))
        symbols: list[PythonSymbol] = []
        imports: dict[str, list[str]] = {}
        for path in py_files:
            s, imps = self.extract_python_symbols(path)
            symbols.extend(s)
            imports[self._rel(path)] = imps
        recent = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        snap = ProjectSnapshot(
            root=str(self.root),
            scanned_at=time.time(),
            file_count=len(files),
            python_file_count=len(py_files),
            test_count=len(tests),
            entrypoints=self.detect_entrypoints(files),
            config_files=config_files[:30],
            recent_files=[self._rel(p) for p in recent],
            symbols=[asdict(s) for s in symbols],
            imports=imports,
        )
        self.snapshot = snap
        self.remember_event("scan", "project scanned", {"files": snap.file_count, "python": snap.python_file_count, "tests": snap.test_count})
        return snap

    def render_radar(self) -> str:
        snap = self.snapshot or self.scan()
        lines = [
            "Project Radar",
            f"- Root: {snap.root}",
            f"- Files: {snap.file_count}",
            f"- Python files: {snap.python_file_count}",
            f"- Tests: {snap.test_count}",
            f"- Entrypoints: {', '.join(snap.entrypoints[:8]) or 'none detected'}",
            f"- Config: {', '.join(snap.config_files[:8]) or 'none detected'}",
            "- Recently changed:",
        ]
        lines.extend(f"  - {p}" for p in snap.recent_files[:8])
        return "\n".join(lines)

    def parse_traceback(self, text: str) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        for match in TRACEBACK_RE.finditer(text or ""):
            raw_path = match.group("path")
            path = Path(raw_path)
            rel = raw_path
            if path.is_absolute():
                try:
                    rel = str(path.relative_to(self.root))
                except ValueError:
                    rel = raw_path
            frames.append({"file": rel, "line": int(match.group("line")), "function": (match.group("func") or "").strip()})
        return frames

    def link_error(self, error_text: str) -> str:
        snap = self.snapshot or self.scan()
        frames = self.parse_traceback(error_text)
        if not frames:
            return "No Python traceback file/line frames detected."
        lines = ["Error Linker"]
        symbol_rows = snap.symbols
        for frame in frames[-5:]:
            file = frame["file"]
            line = frame["line"]
            owners = [s for s in symbol_rows if s["file"] == file and s["line"] <= line and (s.get("end_line") or s["line"]) >= line]
            owner = owners[-1] if owners else None
            if owner:
                lines.append(f"- {file}:{line} inside {owner['kind']} {owner['signature']}")
            else:
                lines.append(f"- {file}:{line}")
        self.remember_event("error", "traceback linked", {"frames": frames})
        return "\n".join(lines)

    def create_goal_graph(self, goal: str) -> dict[str, Any]:
        goal_lower = goal.lower()
        steps = ["scan project radar", "identify relevant files", "inspect smallest related code area"]
        if any(w in goal_lower for w in ("bug", "error", "crash", "fix", "fail")):
            steps += ["reproduce failure", "link error to code", "patch minimal logic", "run targeted verification"]
        elif any(w in goal_lower for w in ("add", "feature", "implement", "create")):
            steps += ["find integration points", "implement feature", "add/update tests", "verify behavior"]
        else:
            steps += ["choose next best action", "execute", "verify result"]
        graph = {"goal": goal, "status": "active", "steps": [{"id": i + 1, "title": s, "status": "pending"} for i, s in enumerate(steps)]}
        with self._connect() as conn:
            conn.execute("INSERT INTO goals (ts, goal, status, graph) VALUES (?, ?, ?, ?)", (time.time(), goal, "active", json.dumps(graph)))
        return graph

    def suggest_next_actions(self, goal: str = "", error_text: str = "") -> str:
        snap = self.snapshot or self.scan()
        lines = ["Next Best Actions"]
        frames = self.parse_traceback(error_text) if error_text else []
        if frames:
            last = frames[-1]
            lines.append(f"1. Inspect {last['file']} around line {last['line']}")
            lines.append("2. Find the owning function/class and its callers")
            lines.append("3. Run the smallest targeted test or command that reproduces it")
            lines.append("4. Patch the smallest failing logic")
            lines.append("5. Re-run verification")
        elif goal:
            graph = self.create_goal_graph(goal)
            for step in graph["steps"][:6]:
                lines.append(f"{step['id']}. {step['title']}")
        else:
            lines.append("1. Scan project radar")
            if snap.entrypoints:
                lines.append(f"2. Inspect likely entrypoint: {snap.entrypoints[0]}")
            if snap.test_count:
                lines.append("3. Run or inspect tests")
            lines.append("4. Continue from most recently changed files")
        self.remember_event("suggest", "next actions generated", {"goal": goal, "has_error": bool(error_text)})
        return "\n".join(lines)

    def timeline(self, limit: int = 20) -> str:
        with self._connect() as conn:
            rows = conn.execute("SELECT ts, kind, title FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        lines = ["Project Brain Timeline"]
        for ts, kind, title in reversed(rows):
            lines.append(f"- {time.strftime('%H:%M:%S', time.localtime(ts))} [{kind}] {title}")
        return "\n".join(lines)


def get_project_brain(root: str | Path = ".") -> ProjectBrain:
    return ProjectBrain(root)
