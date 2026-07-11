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
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
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