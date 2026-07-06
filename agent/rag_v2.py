"""RAG v2 — Embedding-based retrieval with a local vector store.

Upgrades the original TF-IDF rag.py with a real vector store:
  * Hash-based embeddings (no external deps) — 256-dim feature vectors
  * Cosine similarity search
  * Persistent index (JSON on disk)
  * Document chunking with overlap
  * Supports .py, .md, .txt, .json, .yaml files

Used by the agent to answer questions grounded in the local codebase
without sending the whole codebase to the LLM every turn.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_VALID_EXTS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp", ".h"}


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _hash_embedding(tokens: list[str], dim: int = 256) -> list[float]:
    """Deterministic hash-based embedding — no external deps required.

    Each token is hashed to a position in the vector; its contribution is
    its term frequency. The vector is L2-normalised so cosine similarity
    is a simple dot product.
    """
    vec = [0.0] * dim
    counts = Counter(tokens)
    for token, count in counts.items():
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign * math.log(1 + count)
    # L2 normalise.
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Document:
    id: str
    source: str  # file path
    chunk_index: int
    text: str
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """A persistent, dependency-free vector store for RAG."""

    def __init__(self, persist_path: Path | str | None = None, chunk_size: int = 800, overlap: int = 100) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.documents: list[Document] = []
        self._index_dirty = False
        self.persist_path = Path(persist_path) if persist_path else None
        if self.persist_path and self.persist_path.exists():
            self.load()

    def add_text(self, source: str, text: str, metadata: dict[str, Any] | None = None) -> int:
        """Chunk and embed a text blob. Returns number of chunks added."""
        added = 0
        start = 0
        chunk_idx = 0
        while start < len(text):
            chunk = text[start:start + self.chunk_size]
            if chunk.strip():
                tokens = _tokenize(chunk)
                if tokens:
                    doc_id = hashlib.sha1(f"{source}:{chunk_idx}".encode()).hexdigest()[:12]
                    doc = Document(
                        id=doc_id,
                        source=source,
                        chunk_index=chunk_idx,
                        text=chunk,
                        embedding=_hash_embedding(tokens),
                        metadata=metadata or {},
                    )
                    self.documents.append(doc)
                    added += 1
            chunk_idx += 1
            start += self.chunk_size - self.overlap
        self._index_dirty = True
        return added

    def add_file(self, path: Path | str) -> int:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return 0
        if p.suffix.lower() not in _VALID_EXTS:
            return 0
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        return self.add_text(str(p), text, metadata={"ext": p.suffix, "size": len(text)})

    def add_directory(self, path: Path | str, exclude_dirs: set[str] | None = None) -> int:
        skip = exclude_dirs or {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}
        total = 0
        for f in Path(path).rglob("*"):
            if any(part in skip for part in f.parts):
                continue
            if f.is_file():
                total += self.add_file(f)
        return total

    def search(self, query: str, top_k: int = 5, min_score: float = 0.05) -> list[tuple[float, Document]]:
        """Return the top_k most similar documents to ``query``."""
        q_tokens = _tokenize(query)
        if not q_tokens or not self.documents:
            return []
        q_emb = _hash_embedding(q_tokens)
        scored = [(_cosine(q_emb, doc.embedding), doc) for doc in self.documents]
        scored = [(s, d) for s, d in scored if s >= min_score]
        scored.sort(key=lambda x: -x[0])
        return scored[:top_k]

    def answer_context(self, query: str, top_k: int = 5) -> str:
        """Build a context string from the top matches for the LLM."""
        results = self.search(query, top_k=top_k)
        if not results:
            return "No relevant context found in the indexed documents."
        parts = []
        for score, doc in results:
            preview = doc.text[:500] + ("…" if len(doc.text) > 500 else "")
            parts.append(f"[{doc.source} #{doc.chunk_index} | score: {score:.3f}]\n{preview}")
        return "\n\n---\n\n".join(parts)

    def stats(self) -> dict[str, Any]:
        sources = {d.source for d in self.documents}
        return {
            "documents": len(self.documents),
            "sources": len(sources),
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
        }

    def clear(self) -> None:
        self.documents = []
        self._index_dirty = True

    def save(self) -> Path | None:
        if not self.persist_path:
            return None
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"documents": [asdict(d) for d in self.documents], "config": {"chunk_size": self.chunk_size, "overlap": self.overlap}}
        self.persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._index_dirty = False
        return self.persist_path

    def load(self) -> bool:
        if not self.persist_path or not self.persist_path.exists():
            return False
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self.documents = [Document(**d) for d in data.get("documents", [])]
            cfg = data.get("config", {})
            self.chunk_size = cfg.get("chunk_size", self.chunk_size)
            self.overlap = cfg.get("overlap", self.overlap)
            return True
        except (json.JSONDecodeError, OSError, TypeError):
            return False


# Process-wide singleton.
_store: VectorStore | None = None


def get_vector_store(persist: bool = True) -> VectorStore:
    global _store
    if _store is None:
        path = Path.home() / ".terminal_agent" / "vector_store.json" if persist else None
        _store = VectorStore(persist_path=path)
    return _store


def index_codebase(root: Path | str = ".") -> dict[str, Any]:
    """Index a codebase into the global vector store. Returns stats."""
    store = get_vector_store()
    added = store.add_directory(root)
    store.save()
    stats = store.stats()
    stats["newly_indexed"] = added
    return stats
