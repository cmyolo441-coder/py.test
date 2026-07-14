"""Omega semantic code index.

A dependency-free semantic-ish index for local repositories.  It combines token
inversion, AST symbol extraction and lightweight ranking so startup can prepare
real code navigation context without network calls or external services.
"""
from __future__ import annotations

import ast
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|[0-9]+")
_VALID_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build", ".mypy_cache", ".ruff_cache"}


def tokenize(text: str) -> list[str]:
    """Tokenize code/prose into lowercase searchable terms."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


@dataclass
class SymbolRecord:
    kind: str
    name: str
    qualname: str
    path: str
    line: int
    doc: str = ""


@dataclass
class SemanticDocument:
    path: str
    size: int
    digest: str
    tokens: Counter[str] = field(default_factory=Counter)
    symbols: list[SymbolRecord] = field(default_factory=list)


@dataclass
class SemanticIndex:
    root: str
    documents: dict[str, SemanticDocument] = field(default_factory=dict)
    inverted: dict[str, list[str]] = field(default_factory=dict)
    symbol_lookup: dict[str, list[SymbolRecord]] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        return {
            "documents": len(self.documents),
            "terms": len(self.inverted),
            "symbols": sum(len(d.symbols) for d in self.documents.values()),
            "symbol_names": len(self.symbol_lookup),
        }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_files(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if len(out) >= max_files:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in _VALID_EXTS:
            out.append(path)
    return sorted(out)


def _extract_symbols(path: Path, root: Path, text: str) -> list[SymbolRecord]:
    if path.suffix.lower() != ".py":
        return []
    rel = _rel(path, root)
    module = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    symbols: list[SymbolRecord] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            q = f"{module}.{node.name}"
            symbols.append(SymbolRecord("class", node.name, q, rel, node.lineno, ast.get_docstring(node) or ""))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mq = f"{module}.{node.name}.{child.name}"
                    symbols.append(SymbolRecord("method", child.name, mq, rel, child.lineno, ast.get_docstring(child) or ""))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = f"{module}.{node.name}"
            symbols.append(SymbolRecord("function", node.name, q, rel, node.lineno, ast.get_docstring(node) or ""))
    return symbols


def build_semantic_index(root: str | Path = ".", max_files: int = 800) -> SemanticIndex:
    """Build a local semantic index for supported text/code files."""
    root_path = Path(root).resolve()
    index = SemanticIndex(root=str(root_path))
    inverted_sets: dict[str, set[str]] = defaultdict(set)
    for path in _iter_files(root_path, max_files=max_files):
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(path, root_path)
        digest = hashlib.sha1(raw[:200_000]).hexdigest()[:16]
        toks = Counter(tokenize(text))
        symbols = _extract_symbols(path, root_path, text)
        doc = SemanticDocument(path=rel, size=len(raw), digest=digest, tokens=toks, symbols=symbols)
        index.documents[rel] = doc
        for token in toks:
            inverted_sets[token].add(rel)
        for sym in symbols:
            index.symbol_lookup.setdefault(sym.name.lower(), []).append(sym)
            index.symbol_lookup.setdefault(sym.qualname.lower(), []).append(sym)
    index.inverted = {term: sorted(paths) for term, paths in inverted_sets.items()}
    return index


def search_index(index: SemanticIndex, query: str, top_k: int = 10) -> list[tuple[str, float]]:
    """Rank paths by query-token overlap, symbol matches and path matches."""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    q_counts = Counter(q_tokens)
    scores: Counter[str] = Counter()
    for token, weight in q_counts.items():
        for path in index.inverted.get(token, []):
            doc = index.documents[path]
            scores[path] += min(8, doc.tokens.get(token, 0)) * weight
        for sym in index.symbol_lookup.get(token, []):
            scores[sym.path] += 12
    query_l = query.lower()
    for path, doc in index.documents.items():
        if any(part in path.lower() for part in q_counts):
            scores[path] += 6
        # Prefer smaller/denser files slightly when tied.
        scores[path] += min(3, len(doc.symbols))
        if query_l and query_l in path.lower():
            scores[path] += 20
    ranked = [(path, float(score)) for path, score in scores.most_common(top_k) if score > 0]
    return ranked


def symbol_summary(index: SemanticIndex, limit: int = 25) -> list[str]:
    """Return compact public symbol lines for dashboards/context."""
    rows: list[str] = []
    for doc in index.documents.values():
        for sym in doc.symbols:
            if sym.name.startswith("_"):
                continue
            rows.append(f"{sym.kind} {sym.qualname} @ {sym.path}:{sym.line}")
            if len(rows) >= limit:
                return rows
    return rows
