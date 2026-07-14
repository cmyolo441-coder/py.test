"""Nova symbol intelligence.

Builds a rich AST symbol graph: definitions, signatures, decorators, imports,
calls, centrality and query resolution.  It is local/offline and never executes
project code.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}


@dataclass
class NovaSymbol:
    kind: str
    name: str
    qualname: str
    path: str
    line: int
    signature: str = ""
    doc: str = ""
    decorators: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass
class NovaSymbolGraph:
    root: str
    symbols: list[NovaSymbol] = field(default_factory=list)
    imports_by_file: dict[str, list[str]] = field(default_factory=dict)
    calls: dict[str, list[str]] = field(default_factory=dict)
    reverse_calls: dict[str, list[str]] = field(default_factory=dict)
    centrality: dict[str, int] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        by_kind = Counter(s.kind for s in self.symbols)
        return {
            "symbols": len(self.symbols),
            "files": len({s.path for s in self.symbols}),
            "imports": sum(len(v) for v in self.imports_by_file.values()),
            "calls": sum(len(v) for v in self.calls.values()),
            "by_kind": dict(by_kind),
            "top_central": sorted(self.centrality.items(), key=lambda x: -x[1])[:15],
        }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _module(path: Path, root: Path) -> str:
    rel = _rel(path, root)
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    parts: list[str] = []
    for arg in [*node.args.posonlyargs, *node.args.args]:
        parts.append(arg.arg)
    if node.args.vararg:
        parts.append("*" + node.args.vararg.arg)
    for arg in node.args.kwonlyargs:
        parts.append(arg.arg + "=")
    if node.args.kwarg:
        parts.append("**" + node.args.kwarg.arg)
    return f"({', '.join(parts)})"


def _imports(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        prefix = "." * node.level + (node.module or "")
        return [prefix.strip(".") or a.name for a in node.names]
    return []


def _calls(node: ast.AST) -> list[str]:
    calls: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child.func)
            if name:
                calls.add(name)
    return sorted(calls)


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    return [name for d in node.decorator_list if (name := _call_name(d))]


def iter_python_files(root: Path, max_files: int = 900) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.py"):
        if len(files) >= max_files:
            break
        if any(part in _SKIP for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def build_symbol_graph(root: str | Path = ".", max_files: int = 900) -> NovaSymbolGraph:
    root_path = Path(root).resolve()
    graph = NovaSymbolGraph(root=str(root_path))
    by_short: dict[str, list[str]] = defaultdict(list)

    for path in iter_python_files(root_path, max_files=max_files):
        rel = _rel(path, root_path)
        mod = _module(path, root_path)
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        file_imports: list[str] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                file_imports.extend(_imports(node))
            elif isinstance(node, ast.ClassDef):
                qual = f"{mod}.{node.name}"
                sym = NovaSymbol("class", node.name, qual, rel, node.lineno, doc=ast.get_docstring(node) or "", decorators=_decorators(node))
                graph.symbols.append(sym)
                by_short[node.name].append(qual)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        q = f"{mod}.{node.name}.{child.name}"
                        ms = NovaSymbol("method", child.name, q, rel, child.lineno, _signature(child), ast.get_docstring(child) or "", _decorators(child), _calls(child))
                        graph.symbols.append(ms)
                        by_short[child.name].append(q)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{mod}.{node.name}"
                fs = NovaSymbol("function", node.name, q, rel, node.lineno, _signature(node), ast.get_docstring(node) or "", _decorators(node), _calls(node))
                graph.symbols.append(fs)
                by_short[node.name].append(q)
        graph.imports_by_file[rel] = sorted(set(file_imports))

    reverse: dict[str, set[str]] = defaultdict(set)
    for sym in graph.symbols:
        targets: set[str] = set()
        for call in sym.calls:
            short = call.split(".")[-1]
            targets.update(by_short.get(short, []))
        graph.calls[sym.qualname] = sorted(targets)
        for target in targets:
            reverse[target].add(sym.qualname)
    graph.reverse_calls = {k: sorted(v) for k, v in reverse.items()}
    graph.centrality = {s.qualname: len(graph.calls.get(s.qualname, [])) + len(graph.reverse_calls.get(s.qualname, [])) * 2 for s in graph.symbols}
    return graph


def query_symbols(graph: NovaSymbolGraph, query: str, limit: int = 20) -> list[NovaSymbol]:
    q = query.lower()
    scored: list[tuple[int, NovaSymbol]] = []
    for sym in graph.symbols:
        hay = f"{sym.kind} {sym.name} {sym.qualname} {sym.path} {sym.doc}".lower()
        score = 0
        if q in sym.name.lower():
            score += 50
        if q in sym.qualname.lower():
            score += 30
        if q in hay:
            score += 10
        score += graph.centrality.get(sym.qualname, 0)
        if score:
            scored.append((score, sym))
    return [s for _score, s in sorted(scored, key=lambda x: -x[0])[:limit]]


def dependency_closure(graph: NovaSymbolGraph, start: str, depth: int = 2) -> list[str]:
    """Return call dependency closure from a symbol qualname."""
    seen = {start}
    out: list[str] = []
    q: deque[tuple[str, int]] = deque([(start, 0)])
    while q:
        cur, d = q.popleft()
        if d >= depth:
            continue
        for nxt in graph.calls.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                out.append(nxt)
                q.append((nxt, d + 1))
    return out


def symbol_context(graph: NovaSymbolGraph, query: str, limit: int = 12) -> str:
    rows = []
    for sym in query_symbols(graph, query, limit=limit):
        rows.append(f"- {sym.kind} {sym.qualname}{sym.signature} @ {sym.path}:{sym.line}")
    return "\n".join(rows)
