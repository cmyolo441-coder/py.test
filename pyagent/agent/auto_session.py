"""
AutoSession State Management

Encapsulated, disk-backed state management that enables crash recovery and multi-terminal coordination.
Based on GSD Pi's AutoSession architecture.

Key features:
- All mutable auto-mode state in one encapsulated instance
- State lives on disk (.gsd/ directory + SQLite database)
- No in-memory state survives across sessions
- Explicit project root tracking
- Session locks with OS-level exclusivity
"""

import os
import json
import sqlite3
import fcntl
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict, field
from datetime import datetime
import hashlib


@dataclass
class SessionState:
    """Mutable auto-mode state encapsulated in one instance"""
    session_id: str
    project_root: str
    canonical_project_root: str
    base_path: str
    original_base_path: str
    current_milestone_id: Optional[str] = None
    current_slice_id: Optional[str] = None
    current_task_id: Optional[str] = None
    milestone_status: str = "pending"  # pending, ready, in_progress, paused, completed, cancelled
    dispatch_history: List[Dict[str, Any]] = field(default_factory=list)
    stuck_state: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class AutoSession:
    """
    Encapsulated, disk-backed state management for crash recovery and multi-terminal coordination.
    
    All state lives on disk in .gsd/ directory with SQLite database as the source of truth.
    No in-memory state survives across sessions - true crash recovery.
    """
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.canonical_project_root = self._find_canonical_root()
        self.base_path = os.getcwd()
        self.original_base_path = self.base_path
        
        # .gsd directory structure
        self.gsd_dir = self.canonical_project_root / ".gsd"
        self.runtime_dir = self.gsd_dir / "runtime"
        self.db_path = self.gsd_dir / "gsd.db"
        self.lock_file = self.runtime_dir / "session.lock"
        
        # Ensure directories exist
        self._ensure_directories()
        
        # Initialize database
        self._init_database()
        
        # Session state
        self._state: Optional[SessionState] = None
        self._lock_fd: Optional[int] = None
        
    def _find_canonical_root(self) -> Path:
        """Find the canonical project root by looking for .gsd directory"""
        current = Path.cwd()
        while current != current.parent:
            if (current / ".gsd").exists():
                return current
            current = current.parent
        return self.project_root
    
    def _ensure_directories(self):
        """Ensure all required directories exist"""
        self.gsd_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        
    def _init_database(self):
        """Initialize SQLite database with required schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project_root TEXT NOT NULL,
                canonical_project_root TEXT NOT NULL,
                base_path TEXT NOT NULL,
                original_base_path TEXT NOT NULL,
                current_milestone_id TEXT,
                current_slice_id TEXT,
                current_task_id TEXT,
                milestone_status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Dispatch history table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dispatch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        
        # Stuck state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stuck_state (
                session_id TEXT PRIMARY KEY,
                level INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                last_unit_type TEXT,
                last_unit_id TEXT,
                detected_at TEXT,
                recovery_action TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        
        # Metrics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                unit_type TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                total_cost_usd REAL DEFAULT 0.0,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        
        conn.commit()
        conn.close()
        
    def acquire_lock(self) -> bool:
        """
        Acquire OS-level exclusive lock on session.
        Returns True if lock acquired, False if already locked.
        """
        try:
            self._lock_fd = os.open(self.lock_file, os.O_WRONLY | os.O_CREAT)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write current process info
            lock_info = {
                "pid": os.getpid(),
                "session_id": self._generate_session_id(),
                "acquired_at": datetime.utcnow().isoformat()
            }
            os.write(self._lock_fd, json.dumps(lock_info).encode())
            
            return True
        except (IOError, BlockingIOError):
            return False
    
    def release_lock(self):
        """Release the session lock"""
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
            
            # Clean up lock file
            if self.lock_file.exists():
                self.lock_file.unlink()
    
    def _generate_session_id(self) -> str:
        """Generate unique session ID"""
        timestamp = datetime.utcnow().isoformat()
        content = f"{timestamp}-{os.getpid()}-{self.project_root}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def create_session(self) -> SessionState:
        """Create a new session and persist to database"""
        session_id = self._generate_session_id()
        
        state = SessionState(
            session_id=session_id,
            project_root=str(self.project_root),
            canonical_project_root=str(self.canonical_project_root),
            base_path=self.base_path,
            original_base_path=self.original_base_path
        )
        
        # Persist to database
        self._persist_state(state)
        
        self._state = state
        return state
    
    def load_session(self, session_id: str) -> Optional[SessionState]:
        """Load existing session from database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT session_id, project_root, canonical_project_root, base_path, original_base_path,
                   current_milestone_id, current_slice_id, current_task_id, milestone_status,
                   created_at, updated_at
            FROM sessions WHERE session_id = ?
        """, (session_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        # Load dispatch history
        dispatch_history = self._load_dispatch_history(session_id)
        
        # Load stuck state
        stuck_state = self._load_stuck_state(session_id)
        
        # Load metrics
        metrics = self._load_metrics(session_id)
        
        state = SessionState(
            session_id=row[0],
            project_root=row[1],
            canonical_project_root=row[2],
            base_path=row[3],
            original_base_path=row[4],
            current_milestone_id=row[5],
            current_slice_id=row[6],
            current_task_id=row[7],
            milestone_status=row[8],
            created_at=row[9],
            updated_at=row[10],
            dispatch_history=dispatch_history,
            stuck_state=stuck_state,
            metrics=metrics
        )
        
        self._state = state
        return state
    
    def _persist_state(self, state: SessionState):
        """Persist session state to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            INSERT OR REPLACE INTO sessions 
            (session_id, project_root, canonical_project_root, base_path, original_base_path,
             current_milestone_id, current_slice_id, current_task_id, milestone_status,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            state.session_id,
            state.project_root,
            state.canonical_project_root,
            state.base_path,
            state.original_base_path,
            state.current_milestone_id,
            state.current_slice_id,
            state.current_task_id,
            state.milestone_status,
            state.created_at,
            now
        ))
        
        # Persist dispatch history
        self._persist_dispatch_history(state.session_id, state.dispatch_history)
        
        # Persist stuck state
        self._persist_stuck_state(state.session_id, state.stuck_state)
        
        # Persist metrics
        self._persist_metrics(state.session_id, state.metrics)
        
        conn.commit()
        conn.close()
        
        # Update in-memory state
        state.updated_at = now
    
    def _load_dispatch_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Load dispatch history for session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT unit_type, unit_id, status, timestamp, metadata
            FROM dispatch_history WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                "unit_type": row[0],
                "unit_id": row[1],
                "status": row[2],
                "timestamp": row[3],
                "metadata": json.loads(row[4]) if row[4] else None
            })
        
        conn.close()
        return history
    
    def _persist_dispatch_history(self, session_id: str, history: List[Dict[str, Any]]):
        """Persist dispatch history for session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing history
        cursor.execute("DELETE FROM dispatch_history WHERE session_id = ?", (session_id,))
        
        # Insert new history
        for entry in history:
            cursor.execute("""
                INSERT INTO dispatch_history (session_id, unit_type, unit_id, status, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                entry["unit_type"],
                entry["unit_id"],
                entry["status"],
                entry["timestamp"],
                json.dumps(entry.get("metadata")) if entry.get("metadata") else None
            ))
        
        conn.commit()
        conn.close()
    
    def _load_stuck_state(self, session_id: str) -> Dict[str, Any]:
        """Load stuck state for session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT level, consecutive_failures, last_unit_type, last_unit_id, detected_at, recovery_action
            FROM stuck_state WHERE session_id = ?
        """, (session_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {}
        
        return {
            "level": row[0],
            "consecutive_failures": row[1],
            "last_unit_type": row[2],
            "last_unit_id": row[3],
            "detected_at": row[4],
            "recovery_action": row[5]
        }
    
    def _persist_stuck_state(self, session_id: str, stuck_state: Dict[str, Any]):
        """Persist stuck state for session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO stuck_state 
            (session_id, level, consecutive_failures, last_unit_type, last_unit_id, detected_at, recovery_action)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            stuck_state.get("level", 0),
            stuck_state.get("consecutive_failures", 0),
            stuck_state.get("last_unit_type"),
            stuck_state.get("last_unit_id"),
            stuck_state.get("detected_at"),
            stuck_state.get("recovery_action")
        ))
        
        conn.commit()
        conn.close()
    
    def _load_metrics(self, session_id: str) -> Dict[str, Any]:
        """Load metrics for session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT unit_type, unit_id, input_tokens, output_tokens, cache_read_tokens, 
                   cache_write_tokens, total_cost_usd, timestamp
            FROM metrics WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))
        
        metrics = {"entries": []}
        for row in cursor.fetchall():
            metrics["entries"].append({
                "unit_type": row[0],
                "unit_id": row[1],
                "input_tokens": row[2],
                "output_tokens": row[3],
                "cache_read_tokens": row[4],
                "cache_write_tokens": row[5],
                "total_cost_usd": row[6],
                "timestamp": row[7]
            })
        
        # Calculate totals
        total_input = sum(e["input_tokens"] for e in metrics["entries"])
        total_output = sum(e["output_tokens"] for e in metrics["entries"])
        total_cost = sum(e["total_cost_usd"] for e in metrics["entries"])
        
        metrics["totals"] = {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_cost_usd": total_cost
        }
        
        conn.close()
        return metrics
    
    def _persist_metrics(self, session_id: str, metrics: Dict[str, Any]):
        """Persist metrics for session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Clear existing metrics
        cursor.execute("DELETE FROM metrics WHERE session_id = ?", (session_id,))
        
        # Insert new metrics
        for entry in metrics.get("entries", []):
            cursor.execute("""
                INSERT INTO metrics (session_id, unit_type, unit_id, input_tokens, output_tokens,
                                    cache_read_tokens, cache_write_tokens, total_cost_usd, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                entry["unit_type"],
                entry["unit_id"],
                entry["input_tokens"],
                entry["output_tokens"],
                entry["cache_read_tokens"],
                entry["cache_write_tokens"],
                entry["total_cost_usd"],
                entry["timestamp"]
            ))
        
        conn.commit()
        conn.close()
    
    def update_milestone(self, milestone_id: str, status: str = "in_progress"):
        """Update current milestone and status"""
        if self._state is None:
            raise RuntimeError("No active session")
        
        self._state.current_milestone_id = milestone_id
        self._state.milestone_status = status
        self._persist_state(self._state)
    
    def update_slice(self, slice_id: str):
        """Update current slice"""
        if self._state is None:
            raise RuntimeError("No active session")
        
        self._state.current_slice_id = slice_id
        self._persist_state(self._state)
    
    def update_task(self, task_id: str):
        """Update current task"""
        if self._state is None:
            raise RuntimeError("No active session")
        
        self._state.current_task_id = task_id
        self._persist_state(self._state)
    
    def record_dispatch(self, unit_type: str, unit_id: str, status: str, metadata: Optional[Dict] = None):
        """Record a dispatch event"""
        if self._state is None:
            raise RuntimeError("No active session")
        
        dispatch_entry = {
            "unit_type": unit_type,
            "unit_id": unit_id,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata
        }
        
        self._state.dispatch_history.append(dispatch_entry)
        self._persist_state(self._state)
    
    def record_metrics(self, unit_type: str, unit_id: str, input_tokens: int = 0, 
                      output_tokens: int = 0, cache_read_tokens: int = 0, 
                      cache_write_tokens: int = 0, total_cost_usd: float = 0.0):
        """Record metrics for a unit execution"""
        if self._state is None:
            raise RuntimeError("No active session")
        
        metrics_entry = {
            "unit_type": unit_type,
            "unit_id": unit_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "total_cost_usd": total_cost_usd,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        self._state.metrics.setdefault("entries", []).append(metrics_entry)
        
        # Update totals
        entries = self._state.metrics["entries"]
        self._state.metrics["totals"] = {
            "input_tokens": sum(e["input_tokens"] for e in entries),
            "output_tokens": sum(e["output_tokens"] for e in entries),
            "total_cost_usd": sum(e["total_cost_usd"] for e in entries)
        }
        
        self._persist_state(self._state)
    
    def update_stuck_state(self, level: int, consecutive_failures: int, 
                          last_unit_type: str, last_unit_id: str, recovery_action: Optional[str] = None):
        """Update stuck detection state"""
        if self._state is None:
            raise RuntimeError("No active session")
        
        self._state.stuck_state = {
            "level": level,
            "consecutive_failures": consecutive_failures,
            "last_unit_type": last_unit_type,
            "last_unit_id": last_unit_id,
            "detected_at": datetime.utcnow().isoformat(),
            "recovery_action": recovery_action
        }
        
        self._persist_state(self._state)
    
    def get_state(self) -> Optional[SessionState]:
        """Get current session state"""
        return self._state
    
    def save_state(self):
        """Manually save current state to disk"""
        if self._state is None:
            raise RuntimeError("No active session")
        self._persist_state(self._state)
    
    def __enter__(self):
        """Context manager entry"""
        self.acquire_lock()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.save_state()
        self.release_lock()
        return False