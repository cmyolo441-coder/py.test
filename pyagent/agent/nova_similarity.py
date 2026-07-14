"""Nova similarity and clone intelligence."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|\d+")
_EXTS = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".sh"}
_SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}


@dataclass
class SimilarityReport:
    files: int = 0
    pairs: list[tuple[str, str, float]] = field(default_factory=list)
    clusters: list[list[str]] = field(default_factory=list)
    token_stats: dict[str, Any] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        return {"files": self.files, "pairs": len(self.pairs), "clusters": len(self.clusters), **self.token_stats}


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
        if any(part in _SKIP for part in path.parts):
            continue
        if path.is_file() and path.suffix.lower() in _EXTS:
            out.append(path)
    return sorted(out)


def token_set(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


def shingles(tokens: list[str], n: int = 5) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i+n]) for i in range(len(tokens)-n+1)}


def jaccard(a: set[Any], b: set[Any]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def analyze_similarity(root: str | Path = ".", max_files: int = 500, threshold: float = 0.42) -> SimilarityReport:
    root_path = Path(root).resolve()
    docs: dict[str, set[tuple[str, ...]]] = {}
    token_counter: Counter[str] = Counter()
    for path in _iter_files(root_path, max_files=max_files):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        toks = [t.lower() for t in _TOKEN.findall(text)]
        token_counter.update(toks)
        docs[_rel(path, root_path)] = shingles(toks, n=5)
    pairs: list[tuple[str, str, float]] = []
    items = list(docs.items())
    for i, (p1, s1) in enumerate(items):
        if not s1:
            continue
        for p2, s2 in items[i+1:]:
            if not s2:
                continue
            # Quick extension/path family filter keeps this cheap and useful.
            if Path(p1).suffix != Path(p2).suffix:
                continue
            score = jaccard(s1, s2)
            if score >= threshold:
                pairs.append((p1, p2, round(score, 3)))
    pairs.sort(key=lambda p: -p[2])
    return SimilarityReport(
        files=len(docs),
        pairs=pairs[:120],
        clusters=build_clusters(pairs),
        token_stats={"unique_tokens": len(token_counter), "top_tokens": token_counter.most_common(25)},
    )


def build_clusters(pairs: list[tuple[str, str, float]]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b, _score in pairs:
        union(a, b)
    groups: dict[str, list[str]] = defaultdict(list)
    for node in parent:
        groups[find(node)].append(node)
    return [sorted(v) for v in groups.values() if len(v) > 1][:50]


def similarity_context(report: SimilarityReport, limit: int = 10) -> str:
    lines = [f"similar files: {len(report.pairs)} pairs, {len(report.clusters)} clusters"]
    for a, b, score in report.pairs[:limit]:
        lines.append(f"- {score:.3f} {a} <-> {b}")
    return "\n".join(lines)
