"""
Knowledge Graph & Local Memory

SQLite-backed knowledge accumulation with markdown projections for human review.
Based on GSD Pi's knowledge graph and local memory system.

Key features:
- SQLite database as runtime source of truth
- Markdown projections in .gsd/ for human review
- KNOWLEDGE.md append-only register of project-specific rules
- Cross-session memory surviving context window boundaries
- Requirements, decisions, summaries, validation evidence stored
"""

import ast
import sqlite3
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
import threading
import hashlib


class KnowledgeType(Enum):
    """Types of knowledge entries"""
    REQUIREMENT = "requirement"
    DECISION = "decision"
    SUMMARY = "summary"
    RESEARCH = "research"
    VALIDATION_EVIDENCE = "validation_evidence"
    PROJECT_RULE = "project_rule"
    CONTEXT = "context"
    CODE_PATTERN = "code_pattern"
    API_NOTE = "api_note"


class KnowledgeStatus(Enum):
    """Status of knowledge entries"""
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


@dataclass
class KnowledgeEntry:
    """A single knowledge entry"""
    id: Optional[str] = None
    knowledge_type: KnowledgeType = KnowledgeType.CONTEXT
    title: str = ""
    content: str = ""
    status: KnowledgeStatus = KnowledgeStatus.ACTIVE
    source: str = ""  # e.g., "agent", "user", "system"
    confidence: float = 1.0  # 0.0 to 1.0
    tags: List[str] = field(default_factory=list)
    related_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    session_id: Optional[str] = None
    milestone_id: Optional[str] = None
    
    def __post_init__(self):
        """Generate ID if not provided"""
        if self.id is None:
            content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]
            timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            self.id = f"{self.knowledge_type.value}_{timestamp}_{content_hash}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "knowledge_type": self.knowledge_type.value,
            "title": self.title,
            "content": self.content,
            "status": self.status.value,
            "source": self.source,
            "confidence": self.confidence,
            "tags": self.tags,
            "related_ids": self.related_ids,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "session_id": self.session_id,
            "milestone_id": self.milestone_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'KnowledgeEntry':
        """Create from dictionary"""
        return cls(
            id=data.get("id"),
            knowledge_type=KnowledgeType(data.get("knowledge_type", "context")),
            title=data.get("title", ""),
            content=data.get("content", ""),
            status=KnowledgeStatus(data.get("status", "active")),
            source=data.get("source", ""),
            confidence=data.get("confidence", 1.0),
            tags=data.get("tags", []),
            related_ids=data.get("related_ids", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            session_id=data.get("session_id"),
            milestone_id=data.get("milestone_id")
        )


@dataclass
class KnowledgeQuery:
    """Query for knowledge retrieval"""
    knowledge_types: Optional[List[KnowledgeType]] = None
    status: Optional[KnowledgeStatus] = KnowledgeStatus.ACTIVE
    tags: Optional[List[str]] = None
    session_id: Optional[str] = None
    milestone_id: Optional[str] = None
    search_text: Optional[str] = None
    limit: int = 100
    offset: int = 0


@dataclass
class GraphNode:
    """A node in the codebase knowledge graph."""

    id: str
    kind: str
    name: str
    location: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """A directed relationship between two graph nodes."""

    source: str
    target: str
    kind: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class KnowledgeGraph:
    """
    SQLite-backed knowledge accumulation with markdown projections.
    
    Features:
    - Persistent knowledge storage in SQLite database
    - Markdown projections for human review
    - Cross-session memory surviving context window boundaries
    - Flexible querying and relationship tracking
    - Append-only KNOWLEDGE.md for project rules
    """
    
    def __init__(self, project_root: Optional[Union[str, os.PathLike[str]]] = None):
        # Lightweight in-memory code graph used by /kg, v3 tools, and tests.
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []
        self._adjacency: Dict[str, set[str]] = {}

        # When project_root is provided, also enable the original SQLite-backed
        # local-memory store.  When omitted, KnowledgeGraph behaves as a pure
        # in-memory code graph, so ``KnowledgeGraph()`` is cheap and side-effect
        # free.
        self.project_root: Optional[Path] = Path(project_root).resolve() if project_root is not None else None
        if self.project_root is None:
            return

        self.gsd_dir = self.project_root / ".gsd"
        self.db_path = self.gsd_dir / "knowledge.db"
        self.knowledge_md = self.gsd_dir / "KNOWLEDGE.md"
        
        # Thread-safe operations
        self._lock = threading.Lock()
        
        # Ensure directories exist
        self.gsd_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize database
        self._init_database()
        
        # Initialize markdown projections
        self._init_markdown_projections()

    # ------------------------------------------------------------------
    # In-memory code graph API
    # ------------------------------------------------------------------
    def add_node(self, node: GraphNode) -> None:
        """Add or replace a code graph node."""
        self.nodes[node.id] = node
        self._adjacency.setdefault(node.id, set())

    def add_edge(self, edge: GraphEdge) -> None:
        """Add a directed relationship if both endpoints are known."""
        if edge.source not in self.nodes or edge.target not in self.nodes:
            return
        key = (edge.source, edge.target, edge.kind)
        if any((e.source, e.target, e.kind) == key for e in self.edges):
            return
        self.edges.append(edge)
        self._adjacency.setdefault(edge.source, set()).add(edge.target)

    def find(self, name: str, kind: Optional[str] = None) -> List[GraphNode]:
        """Find nodes by case-insensitive substring match on name/id/location."""
        needle = name.lower()
        results = []
        for node in self.nodes.values():
            if kind and node.kind != kind:
                continue
            haystack = f"{node.name} {node.id} {node.location}".lower()
            if needle in haystack:
                results.append(node)
        return sorted(results, key=lambda n: (n.kind, n.name, n.location))

    def shortest_path(self, source: str, target: str) -> List[str]:
        """Return the shortest directed path between two node ids, if any."""
        if source not in self.nodes or target not in self.nodes:
            return []
        queue: List[tuple[str, List[str]]] = [(source, [source])]
        seen = {source}
        while queue:
            current, path = queue.pop(0)
            if current == target:
                return path
            for nxt in sorted(self._adjacency.get(current, set())):
                if nxt in seen:
                    continue
                seen.add(nxt)
                queue.append((nxt, path + [nxt]))
        return []

    def stats(self) -> Dict[str, Any]:
        """Return compact graph statistics."""
        by_kind: Dict[str, int] = {}
        edge_kinds: Dict[str, int] = {}
        for node in self.nodes.values():
            by_kind[node.kind] = by_kind.get(node.kind, 0) + 1
        for edge in self.edges:
            edge_kinds[edge.kind] = edge_kinds.get(edge.kind, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_kinds": by_kind,
            "edge_kinds": edge_kinds,
        }

    def dashboard(self) -> str:
        """Human-readable graph summary for terminal output."""
        stats = self.stats()
        lines = [
            "╔════════════════════════════════════════════════════╗",
            "║              KNOWLEDGE GRAPH                       ║",
            "╠════════════════════════════════════════════════════╣",
            f"║  Nodes: {stats['nodes']:<40}║",
            f"║  Edges: {stats['edges']:<40}║",
            "╚════════════════════════════════════════════════════╝",
            "",
            "Node kinds:",
        ]
        for kind, count in sorted(stats["node_kinds"].items()):
            lines.append(f"  - {kind}: {count}")
        if not stats["node_kinds"]:
            lines.append("  - (empty)")
        lines.append("\nTop nodes:")
        for node in list(sorted(self.nodes.values(), key=lambda n: (n.kind, n.name)))[:20]:
            loc = f" @ {node.location}" if node.location else ""
            lines.append(f"  [{node.kind}] {node.name}{loc}")
        return "\n".join(lines)
    
    def _init_database(self):
        """Initialize SQLite database with knowledge schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Knowledge entries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id TEXT PRIMARY KEY,
                knowledge_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                source TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                tags TEXT DEFAULT '[]',
                related_ids TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                session_id TEXT,
                milestone_id TEXT
            )
        """)
        
        # Relationships table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_relationships (
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                strength REAL DEFAULT 1.0,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_id, to_id, relationship_type),
                FOREIGN KEY (from_id) REFERENCES knowledge_entries(id) ON DELETE CASCADE,
                FOREIGN KEY (to_id) REFERENCES knowledge_entries(id) ON DELETE CASCADE
            )
        """)
        
        # Search index (FTS5)
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                id,
                title,
                content,
                tags,
                content_rowid=knowledge_entries.rowid
            )
        """)
        
        # Triggers to keep FTS in sync
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge_entries BEGIN
                INSERT INTO knowledge_fts(id, title, content, tags)
                VALUES (NEW.id, NEW.title, NEW.content, NEW.tags);
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge_entries BEGIN
                DELETE FROM knowledge_fts WHERE id = OLD.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge_entries BEGIN
                UPDATE knowledge_fts SET title = NEW.title, content = NEW.content, tags = NEW.tags
                WHERE id = NEW.id;
            END
        """)
        
        conn.commit()
        conn.close()
    
    def _init_markdown_projections(self):
        """Initialize markdown projection files"""
        if not self.knowledge_md.exists():
            self._write_knowledge_md_header()
    
    def _write_knowledge_md_header(self):
        """Write header to KNOWLEDGE.md"""
        header = """# Project Knowledge

This file contains project-specific knowledge, rules, and decisions accumulated by the agent.

## Project Rules

### Code Patterns
- [No patterns documented yet]

### API Notes
- [No API notes documented yet]

## Requirements
- [No requirements documented yet]

## Decisions
- [No decisions documented yet]

## Research Notes
- [No research documented yet]

---
*Last updated: {timestamp}*
""".format(timestamp=datetime.utcnow().isoformat())
        
        with open(self.knowledge_md, 'w') as f:
            f.write(header)
    
    def add_entry(self, entry: KnowledgeEntry) -> str:
        """
        Add a knowledge entry to the graph.
        
        Returns:
            The ID of the created entry
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            now = datetime.utcnow().isoformat()
            
            cursor.execute("""
                INSERT INTO knowledge_entries 
                (id, knowledge_type, title, content, status, source, confidence, tags, 
                 related_ids, metadata, created_at, updated_at, session_id, milestone_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id,
                entry.knowledge_type.value,
                entry.title,
                entry.content,
                entry.status.value,
                entry.source,
                entry.confidence,
                json.dumps(entry.tags),
                json.dumps(entry.related_ids),
                json.dumps(entry.metadata),
                entry.created_at,
                now,
                entry.session_id,
                entry.milestone_id
            ))
            
            # Add relationships
            for related_id in entry.related_ids:
                self._add_relationship(cursor, entry.id, related_id, "related")
            
            conn.commit()
            conn.close()
            
            # Update markdown projections
            self._update_markdown_projections()
            
            return entry.id
    
    def _add_relationship(
        self,
        cursor: sqlite3.Cursor,
        from_id: str,
        to_id: str,
        relationship_type: str,
        strength: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Add a relationship between knowledge entries"""
        cursor.execute("""
            INSERT OR REPLACE INTO knowledge_relationships
            (from_id, to_id, relationship_type, strength, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            from_id,
            to_id,
            relationship_type,
            strength,
            json.dumps(metadata or {}),
            datetime.utcnow().isoformat()
        ))
    
    def get_entry(self, entry_id: str) -> Optional[KnowledgeEntry]:
        """Get a specific knowledge entry by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, knowledge_type, title, content, status, source, confidence, tags,
                   related_ids, metadata, created_at, updated_at, session_id, milestone_id
            FROM knowledge_entries WHERE id = ?
        """, (entry_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return KnowledgeEntry(
            id=row[0],
            knowledge_type=KnowledgeType(row[1]),
            title=row[2],
            content=row[3],
            status=KnowledgeStatus(row[4]),
            source=row[5],
            confidence=row[6],
            tags=json.loads(row[7]),
            related_ids=json.loads(row[8]),
            metadata=json.loads(row[9]),
            created_at=row[10],
            updated_at=row[11],
            session_id=row[12],
            milestone_id=row[13]
        )
    
    def query(self, query: KnowledgeQuery) -> List[KnowledgeEntry]:
        """Query knowledge entries based on criteria"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Build query
        conditions = []
        params = []
        
        if query.knowledge_types:
            type_list = [t.value for t in query.knowledge_types]
            placeholders = ",".join("?" * len(type_list))
            conditions.append(f"knowledge_type IN ({placeholders})")
            params.extend(type_list)
        
        if query.status:
            conditions.append("status = ?")
            params.append(query.status.value)
        
        if query.session_id:
            conditions.append("session_id = ?")
            params.append(query.session_id)
        
        if query.milestone_id:
            conditions.append("milestone_id = ?")
            params.append(query.milestone_id)
        
        if query.search_text:
            # Use FTS for text search
            cursor.execute("""
                SELECT id FROM knowledge_fts 
                WHERE knowledge_fts MATCH ?
                LIMIT ?
                OFFSET ?
            """, (query.search_text, query.limit, query.offset))
            
            search_ids = [row[0] for row in cursor.fetchall()]
            
            if search_ids:
                placeholders = ",".join("?" * len(search_ids))
                conditions.append(f"id IN ({placeholders})")
                params.extend(search_ids)
            else:
                # No search results
                conn.close()
                return []
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        sql = f"""
            SELECT id, knowledge_type, title, content, status, source, confidence, tags,
                   related_ids, metadata, created_at, updated_at, session_id, milestone_id
            FROM knowledge_entries
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        
        params.extend([query.limit, query.offset])
        
        cursor.execute(sql, params)
        
        entries = []
        for row in cursor.fetchall():
            entries.append(KnowledgeEntry(
                id=row[0],
                knowledge_type=KnowledgeType(row[1]),
                title=row[2],
                content=row[3],
                status=KnowledgeStatus(row[4]),
                source=row[5],
                confidence=row[6],
                tags=json.loads(row[7]),
                related_ids=json.loads(row[8]),
                metadata=json.loads(row[9]),
                created_at=row[10],
                updated_at=row[11],
                session_id=row[12],
                milestone_id=row[13]
            ))
        
        conn.close()
        return entries
    
    def get_related_entries(self, entry_id: str, relationship_type: Optional[str] = None) -> List[KnowledgeEntry]:
        """Get entries related to a specific entry"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if relationship_type:
            cursor.execute("""
                SELECT to_id FROM knowledge_relationships
                WHERE from_id = ? AND relationship_type = ?
            """, (entry_id, relationship_type))
        else:
            cursor.execute("""
                SELECT to_id FROM knowledge_relationships
                WHERE from_id = ?
            """, (entry_id,))
        
        related_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        # Fetch the actual entries
        entries = []
        for related_id in related_ids:
            entry = self.get_entry(related_id)
            if entry:
                entries.append(entry)
        
        return entries
    
    def update_entry(self, entry: KnowledgeEntry) -> bool:
        """Update an existing knowledge entry"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            now = datetime.utcnow().isoformat()
            
            cursor.execute("""
                UPDATE knowledge_entries
                SET title = ?, content = ?, status = ?, confidence = ?, tags = ?,
                    related_ids = ?, metadata = ?, updated_at = ?
                WHERE id = ?
            """, (
                entry.title,
                entry.content,
                entry.status.value,
                entry.confidence,
                json.dumps(entry.tags),
                json.dumps(entry.related_ids),
                json.dumps(entry.metadata),
                now,
                entry.id
            ))
            
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            
            if affected > 0:
                self._update_markdown_projections()
                return True
            
            return False
    
    def delete_entry(self, entry_id: str) -> bool:
        """Delete a knowledge entry"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM knowledge_entries WHERE id = ?", (entry_id,))
            
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            
            if affected > 0:
                self._update_markdown_projections()
                return True
            
            return False
    
    def _update_markdown_projections(self):
        """Update markdown projection files"""
        # Get project rules
        project_rules = self.query(KnowledgeQuery(
            knowledge_types=[KnowledgeType.PROJECT_RULE],
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Get API notes
        api_notes = self.query(KnowledgeQuery(
            knowledge_types=[KnowledgeType.API_NOTE],
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Get requirements
        requirements = self.query(KnowledgeQuery(
            knowledge_types=[KnowledgeType.REQUIREMENT],
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Get decisions
        decisions = self.query(KnowledgeQuery(
            knowledge_types=[KnowledgeType.DECISION],
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Get research
        research = self.query(KnowledgeQuery(
            knowledge_types=[KnowledgeType.RESEARCH],
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Build markdown content
        md_content = self._build_knowledge_md(
            project_rules, api_notes, requirements, decisions, research
        )
        
        with open(self.knowledge_md, 'w') as f:
            f.write(md_content)
    
    def _build_knowledge_md(
        self,
        project_rules: List[KnowledgeEntry],
        api_notes: List[KnowledgeEntry],
        requirements: List[KnowledgeEntry],
        decisions: List[KnowledgeEntry],
        research: List[KnowledgeEntry]
    ) -> str:
        """Build KNOWLEDGE.md content"""
        lines = [
            "# Project Knowledge",
            "",
            "This file contains project-specific knowledge, rules, and decisions accumulated by the agent.",
            "",
            "## Project Rules",
            ""
        ]
        
        # Code patterns
        lines.append("### Code Patterns")
        code_patterns = [r for r in project_rules if "pattern" in r.tags]
        if code_patterns:
            for rule in code_patterns:
                lines.append(f"- **{rule.title}**: {rule.content}")
        else:
            lines.append("- [No patterns documented yet]")
        lines.append("")
        
        # API notes
        lines.append("### API Notes")
        if api_notes:
            for note in api_notes:
                lines.append(f"- **{note.title}**: {note.content}")
        else:
            lines.append("- [No API notes documented yet]")
        lines.append("")
        
        # Requirements
        lines.append("## Requirements")
        if requirements:
            for req in requirements:
                status_emoji = "✅" if req.status == KnowledgeStatus.ACTIVE else "❌"
                lines.append(f"{status_emoji} **{req.title}**: {req.content}")
        else:
            lines.append("- [No requirements documented yet]")
        lines.append("")
        
        # Decisions
        lines.append("## Decisions")
        if decisions:
            for decision in decisions:
                lines.append(f"- **{decision.title}** ({decision.created_at}): {decision.content}")
        else:
            lines.append("- [No decisions documented yet]")
        lines.append("")
        
        # Research
        lines.append("## Research Notes")
        if research:
            for research_note in research:
                lines.append(f"- **{research_note.title}**: {research_note.content}")
        else:
            lines.append("- [No research documented yet]")
        lines.append("")
        
        lines.append(f"---")
        lines.append(f"*Last updated: {datetime.utcnow().isoformat()}*")
        
        return "\n".join(lines)
    
    def get_context_for_session(self, session_id: str) -> str:
        """
        Get relevant context for a session.
        
        Returns formatted context string with relevant knowledge.
        """
        # Get active knowledge for this session
        entries = self.query(KnowledgeQuery(
            session_id=session_id,
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Also get project-wide rules and requirements
        project_rules = self.query(KnowledgeQuery(
            knowledge_types=[KnowledgeType.PROJECT_RULE, KnowledgeType.REQUIREMENT],
            status=KnowledgeStatus.ACTIVE
        ))
        
        # Combine and format
        context_parts = ["# Relevant Project Knowledge"]
        
        if project_rules:
            context_parts.append("\n## Project Rules & Requirements")
            for entry in project_rules:
                context_parts.append(f"### {entry.title}")
                context_parts.append(entry.content)
        
        if entries:
            context_parts.append("\n## Session-Specific Knowledge")
            for entry in entries:
                context_parts.append(f"### {entry.title}")
                context_parts.append(entry.content)
        
        return "\n\n".join(context_parts)
    
    def cleanup_old_entries(self, older_than_days: int = 30, keep_types: Optional[List[KnowledgeType]] = None):
        """Clean up old knowledge entries"""
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if keep_types:
                type_list = [t.value for t in keep_types]
                placeholders = ",".join("?" * len(type_list))
                cursor.execute(f"""
                    DELETE FROM knowledge_entries
                    WHERE created_at < ? AND knowledge_type NOT IN ({placeholders})
                """, [cutoff.isoformat()] + type_list)
            else:
                cursor.execute("""
                    DELETE FROM knowledge_entries
                    WHERE created_at < ?
                """, (cutoff.isoformat(),))
            
            affected = cursor.rowcount
            conn.commit()
            conn.close()
            
            if affected > 0:
                self._update_markdown_projections()
    
    def export_to_json(self, output_path: Optional[Path] = None) -> str:
        """Export all knowledge to JSON file"""
        if output_path is None:
            output_path = self.gsd_dir / "knowledge_export.json"
        
        # Get all entries
        all_entries = self.query(KnowledgeQuery(limit=10000))
        
        data = {
            "export_timestamp": datetime.utcnow().isoformat(),
            "total_entries": len(all_entries),
            "entries": [entry.to_dict() for entry in all_entries]
        }
        
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        return str(output_path)
    
    def import_from_json(self, input_path: Path) -> int:
        """Import knowledge from JSON file"""
        with open(input_path, 'r') as f:
            data = json.load(f)
        
        imported_count = 0
        for entry_data in data.get("entries", []):
            entry = KnowledgeEntry.from_dict(entry_data)
            self.add_entry(entry)
            imported_count += 1
        
        return imported_count

# ---------------------------------------------------------------------------
# Codebase graph builder helpers
# ---------------------------------------------------------------------------
_CODE_GRAPH: Optional[KnowledgeGraph] = None


def _safe_rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_python_files(root: Path) -> List[Path]:
    ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "build", "dist"}
    files: List[Path] = []
    for path in root.rglob("*.py"):
        if any(part in ignored for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def _node_id(kind: str, rel: str, name: str = "") -> str:
    return f"{kind}:{rel}:{name}" if name else f"{kind}:{rel}"


def build_graph_from_codebase(root: Union[str, os.PathLike[str]] = ".") -> KnowledgeGraph:
    """Build an in-memory graph of Python files, imports, classes and functions.

    The graph intentionally avoids executing project code.  It uses Python's
    AST only, so it is fast, deterministic and safe to run on untrusted
    repositories.
    """
    global _CODE_GRAPH
    root_path = Path(root).resolve()
    kg = KnowledgeGraph()

    for file_path in _iter_python_files(root_path):
        rel = _safe_rel(file_path, root_path)
        file_id = _node_id("file", rel)
        kg.add_node(GraphNode(id=file_id, kind="file", name=file_path.name, location=rel, metadata={"path": rel}))

        try:
            source = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as exc:
            err_id = _node_id("syntax_error", rel, str(exc.lineno or 0))
            kg.add_node(GraphNode(id=err_id, kind="syntax_error", name=f"line {exc.lineno}", location=f"{rel}:{exc.lineno or 0}", metadata={"message": exc.msg}))
            kg.add_edge(GraphEdge(source=file_id, target=err_id, kind="has_error"))
            continue

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_names: List[str] = []
                if isinstance(node, ast.Import):
                    module_names = [alias.name for alias in node.names]
                else:
                    module_names = [node.module or ""]
                for module in [m for m in module_names if m]:
                    mod_id = f"module:{module}"
                    kg.add_node(GraphNode(id=mod_id, kind="module", name=module, location=f"{rel}:{getattr(node, 'lineno', 0)}"))
                    kg.add_edge(GraphEdge(source=file_id, target=mod_id, kind="imports"))
            elif isinstance(node, ast.ClassDef):
                class_id = _node_id("class", rel, node.name)
                kg.add_node(GraphNode(
                    id=class_id,
                    kind="class",
                    name=node.name,
                    location=f"{rel}:{node.lineno}",
                    metadata={"bases": [ast.unparse(b) if hasattr(ast, "unparse") else "" for b in node.bases]},
                ))
                kg.add_edge(GraphEdge(source=file_id, target=class_id, kind="defines"))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_id = _node_id("method", rel, f"{node.name}.{child.name}")
                        kg.add_node(GraphNode(id=method_id, kind="method", name=child.name, location=f"{rel}:{child.lineno}", metadata={"class": node.name}))
                        kg.add_edge(GraphEdge(source=class_id, target=method_id, kind="defines"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_id = _node_id("function", rel, node.name)
                kg.add_node(GraphNode(id=func_id, kind="function", name=node.name, location=f"{rel}:{node.lineno}"))
                kg.add_edge(GraphEdge(source=file_id, target=func_id, kind="defines"))

    _CODE_GRAPH = kg
    return kg


def get_knowledge_graph() -> KnowledgeGraph:
    """Return the last built code graph, or an empty one."""
    global _CODE_GRAPH
    if _CODE_GRAPH is None:
        _CODE_GRAPH = KnowledgeGraph()
    return _CODE_GRAPH
