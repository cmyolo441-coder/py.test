"""Lightweight retrieval over local text documents (no external services).

Implements a TF-IDF style keyword ranking so the agent can answer questions
grounded in a folder of documents without needing an embedding API.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class Chunk:
    source: str
    text: str
    tokens: Counter = field(default_factory=Counter)


class DocumentIndex:
    """An in-memory TF-IDF index over document chunks."""

    def __init__(self, chunk_size: int = 800) -> None:
        self.chunk_size = chunk_size
        self.chunks: list[Chunk] = []
        self._df: Counter = Counter()

    def _add_text(self, source: str, text: str) -> None:
        for start in range(0, len(text), self.chunk_size):
            piece = text[start : start + self.chunk_size]
            if not piece.strip():
                continue
            tokens = Counter(tokenize(piece))
            self.chunks.append(Chunk(source=source, text=piece, tokens=tokens))
            for term in tokens:
                self._df[term] += 1

    def add_file(self, path: str | Path) -> int:
        p = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        before = len(self.chunks)
        self._add_text(str(p), text)
        return len(self.chunks) - before

    def add_directory(self, path: str | Path, pattern: str = "*.txt") -> int:
        total = 0
        for file in Path(path).rglob(pattern):
            if file.is_file():
                total += self.add_file(file)
        return total

    def _idf(self, term: str) -> float:
        n = len(self.chunks) or 1
        return math.log((n + 1) / (self._df.get(term, 0) + 1)) + 1

    def search(self, query: str, top_k: int = 3) -> list[tuple[float, Chunk]]:
        q_tokens = Counter(tokenize(query))
        if not q_tokens or not self.chunks:
            return []
        scored: list[tuple[float, Chunk]] = []
        for chunk in self.chunks:
            score = 0.0
            length = sum(chunk.tokens.values()) or 1
            for term, q_count in q_tokens.items():
                tf = chunk.tokens.get(term, 0) / length
                score += tf * self._idf(term) * q_count
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    def answer_context(self, query: str, top_k: int = 3) -> str:
        results = self.search(query, top_k)
        if not results:
            return "No relevant context found."
        parts = []
        for score, chunk in results:
            parts.append(f"[source: {chunk.source} | score: {score:.3f}]\n{chunk.text}")
        return "\n\n---\n\n".join(parts)
