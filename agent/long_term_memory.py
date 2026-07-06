"""Long-term memory with embeddings — remembers facts across sessions.

Two tiers:
  * EPISODIC — records of past conversations/turns (what was asked, what was answered)
  * SEMANTIC — distilled facts the agent learned ("user prefers terse answers",
    "project uses pytest", "auth is in agent/auth.py")

Both use the v2 vector store for semantic retrieval. The agent queries
long-term memory at the start of each turn to ground its response in
relevant past context.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from .rag_v2 import VectorStore


@dataclass
class EpisodicMemory:
    """A single past interaction."""
    id: str
    timestamp: float
    user_input: str
    assistant_response: str
    provider: str = ""
    model: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class SemanticFact:
    """A distilled fact the agent learned."""
    id: str
    timestamp: float
    fact: str
    category: str  # "preference" | "project" | "user" | "environment" | "lesson"
    confidence: float = 1.0
    source: str = ""  # how was this fact learned


class LongTermMemory:
    """Persistent, embedding-indexed long-term memory."""

    def __init__(self) -> None:
        # Use separate vector stores for each tier.
        from pathlib import Path
        base = Path.home() / ".terminal_agent"
        self.episodic_store = VectorStore(persist_path=base / "memory_episodic.json", chunk_size=500, overlap=50)
        self.semantic_store = VectorStore(persist_path=base / "memory_semantic.json", chunk_size=300, overlap=30)
        self._facts: list[SemanticFact] = []
        self._episodes: list[EpisodicMemory] = []
        self._load_index()

    def record_episode(self, user_input: str, assistant_response: str, provider: str = "", model: str = "", tags: list[str] | None = None) -> str:
        ep_id = hashlib.sha1(f"{time.time()}:{user_input[:50]}".encode()).hexdigest()[:12]
        ep = EpisodicMemory(
            id=ep_id,
            timestamp=time.time(),
            user_input=user_input,
            assistant_response=assistant_response,
            provider=provider,
            model=model,
            tags=tags or [],
        )
        self._episodes.append(ep)
        # Index into vector store.
        text = f"USER: {user_input}\nASSISTANT: {assistant_response[:1000]}"
        self.episodic_store.add_text(f"episode:{ep_id}", text, metadata={"tags": tags or [], "timestamp": ep.timestamp})
        self.episodic_store.save()
        return ep_id

    def record_fact(self, fact: str, category: str = "lesson", confidence: float = 1.0, source: str = "") -> str:
        fact_id = hashlib.sha1(fact.encode()).hexdigest()[:12]
        sf = SemanticFact(
            id=fact_id,
            timestamp=time.time(),
            fact=fact,
            category=category,
            confidence=confidence,
            source=source,
        )
        self._facts.append(sf)
        self.semantic_store.add_text(f"fact:{fact_id}", fact, metadata={"category": category, "confidence": confidence})
        self.semantic_store.save()
        return fact_id

    def recall_episodes(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Find past episodes relevant to ``query``."""
        results = self.episodic_store.search(query, top_k=top_k)
        return [
            {"score": score, "text": doc.text, "source": doc.source, "metadata": doc.metadata}
            for score, doc in results
        ]

    def recall_facts(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Find learned facts relevant to ``query``."""
        results = self.semantic_store.search(query, top_k=top_k)
        return [
            {"score": score, "fact": doc.text, "category": doc.metadata.get("category", "unknown")}
            for score, doc in results
        ]

    def context_for_query(self, query: str) -> str:
        """Build a context string combining relevant facts and episodes."""
        facts = self.recall_facts(query, top_k=3)
        episodes = self.recall_episodes(query, top_k=2)
        parts = []
        if facts:
            parts.append("Relevant learned facts:")
            for f in facts:
                parts.append(f"  - [{f['category']}] {f['fact'][:200]} (score: {f['score']:.2f})")
        if episodes:
            parts.append("\nRelevant past interactions:")
            for e in episodes:
                parts.append(f"  - {e['text'][:300]} (score: {e['score']:.2f})")
        return "\n".join(parts) if parts else ""

    def list_facts(self, category: str | None = None) -> list[SemanticFact]:
        if category:
            return [f for f in self._facts if f.category == category]
        return list(self._facts)

    def list_episodes(self, limit: int = 20) -> list[EpisodicMemory]:
        return sorted(self._episodes, key=lambda e: -e.timestamp)[:limit]

    def forget_fact(self, fact_id: str) -> bool:
        before = len(self._facts)
        self._facts = [f for f in self._facts if f.id != fact_id]
        if len(self._facts) < before:
            self.semantic_store.documents = [d for d in self.semantic_store.documents if d.source != f"fact:{fact_id}"]
            self.semantic_store.save()
            return True
        return False

    def stats(self) -> dict[str, Any]:
        return {
            "episodes": len(self._episodes),
            "facts": len(self._facts),
            "episodic_chunks": len(self.episodic_store.documents),
            "semantic_chunks": len(self.semantic_store.documents),
            "facts_by_category": {
                cat: len([f for f in self._facts if f.category == cat])
                for cat in {"preference", "project", "user", "environment", "lesson"}
            },
        }

    def dashboard(self) -> str:
        s = self.stats()
        lines = [
            "Long-term memory:",
            f"  episodes:  {s['episodes']}",
            f"  facts:     {s['facts']}",
            "  by category:",
        ]
        for cat, count in s["facts_by_category"].items():
            lines.append(f"    {cat:<14} {count}")
        return "\n".join(lines)

    def _load_index(self) -> None:
        """Rebuild the in-memory fact/episode lists from the vector stores."""
        for doc in self.episodic_store.documents:
            if doc.source.startswith("episode:"):
                # We don't store the full episode in the vector text, so this is a
                # best-effort reconstruction. Real persistence would use a sidecar JSON.
                pass
        for doc in self.semantic_store.documents:
            if doc.source.startswith("fact:"):
                fact_id = doc.source.split(":", 1)[1]
                self._facts.append(SemanticFact(
                    id=fact_id,
                    timestamp=doc.metadata.get("timestamp", time.time()),
                    fact=doc.text,
                    category=doc.metadata.get("category", "lesson"),
                    confidence=doc.metadata.get("confidence", 1.0),
                    source="vector_store",
                ))


_memory: LongTermMemory | None = None


def get_long_term_memory() -> LongTermMemory:
    global _memory
    if _memory is None:
        _memory = LongTermMemory()
    return _memory
