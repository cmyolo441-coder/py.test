"""SQLite-backed persistence for conversations and audit events.

Provides durable, queryable storage without any external database server.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    details TEXT,
    created_at REAL NOT NULL
);
"""


class Database:
    """Thin, safe wrapper around a SQLite database."""

    def __init__(self, path: str | Path = "termianlagent.db") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def create_conversation(self, name: str) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO conversations(name, created_at, updated_at) "
            "VALUES (?, ?, ?)",
            (name, now, now),
        )
        self.conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = self.conn.execute(
            "SELECT id FROM conversations WHERE name = ?", (name,)
        ).fetchone()
        return row["id"]

    def add_message(self, conversation_id: int, role: str, content: str) -> int:
        now = time.time()
        cur = self.conn.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, now),
        )
        self.conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def list_conversations(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT name, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def audit(self, event: str, details: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log(event, details, created_at) VALUES (?, ?, ?)",
            (event, json.dumps(details or {}), time.time()),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
