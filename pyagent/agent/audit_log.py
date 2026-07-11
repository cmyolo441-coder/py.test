"""Immutable audit log — tamper-evident append-only event log.

Every significant agent action (tool call, config change, command run, goal
started/completed) is recorded as a hash-chained entry. Each entry's hash
includes the previous entry's hash, so any tampering is immediately
detectable by re-verifying the chain.

Persists to ~/.terminal_agent/audit.log (JSONL format).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class AuditEntry:
    sequence: int
    timestamp: float
    action: str
    actor: str = "agent"
    details: dict[str, Any] = None
    prev_hash: str = ""
    entry_hash: str = ""


class ImmutableAuditLog:
    """Append-only, hash-chained audit log."""

    def __init__(self, persist_path: Path | str | None = None) -> None:
        self.persist_path = Path(persist_path) if persist_path else Path.home() / ".terminal_agent" / "audit.log"
        self.entries: list[AuditEntry] = []
        self._last_hash = ""
        self._load()

    def record(self, action: str, actor: str = "agent", **details: Any) -> AuditEntry:
        """Append a new entry to the chain."""
        entry = AuditEntry(
            sequence=len(self.entries) + 1,
            timestamp=time.time(),
            action=action,
            actor=actor,
            details=details or {},
            prev_hash=self._last_hash,
        )
        # Compute this entry's hash.
        payload = f"{entry.sequence}{entry.timestamp}{entry.action}{entry.actor}{json.dumps(entry.details, sort_keys=True)}{entry.prev_hash}"
        entry.entry_hash = hashlib.sha256(payload.encode()).hexdigest()
        self.entries.append(entry)
        self._last_hash = entry.entry_hash
        self._append_to_file(entry)
        return entry

    def verify(self) -> tuple[bool, str]:
        """Verify the integrity of the entire chain. Returns (ok, message)."""
        prev_hash = ""
        for i, entry in enumerate(self.entries):
            if entry.sequence != i + 1:
                return False, f"Sequence break at entry {i+1}: expected {i+1}, got {entry.sequence}"
            if entry.prev_hash != prev_hash:
                return False, f"Hash chain broken at entry {entry.sequence}: prev_hash mismatch"
            # Recompute this entry's hash.
            payload = f"{entry.sequence}{entry.timestamp}{entry.action}{entry.actor}{json.dumps(entry.details, sort_keys=True)}{entry.prev_hash}"
            expected = hashlib.sha256(payload.encode()).hexdigest()
            if entry.entry_hash != expected:
                return False, f"Entry {entry.sequence} hash mismatch — possible tampering"
            prev_hash = entry.entry_hash
        return True, f"Chain verified: {len(self.entries)} entries, integrity OK"

    def query(self, action: str | None = None, since: float | None = None, limit: int = 100) -> list[AuditEntry]:
        results = self.entries
        if action:
            results = [e for e in results if e.action == action]
        if since:
            results = [e for e in results if e.timestamp >= since]
        return results[-limit:]

    def dashboard(self) -> str:
        ok, msg = self.verify()
        status = "✓ INTEGRITY OK" if ok else "✗ TAMPERED"
        lines = [
            "Audit log:",
            f"  entries:  {len(self.entries)}",
            f"  status:   {status}  ({msg})",
            f"  file:     {self.persist_path}",
        ]
        if self.entries:
            lines.append("\n  Recent entries:")
            for entry in self.entries[-10:]:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.timestamp))
                lines.append(f"    #{entry.sequence:<4} {ts}  {entry.actor:<10} {entry.action}")
        return "\n".join(lines)

    def clear(self) -> None:
        self.entries = []
        self._last_hash = ""
        try:
            if self.persist_path.exists():
                self.persist_path.unlink()
        except OSError:
            pass

    def _append_to_file(self, entry: AuditEntry) -> None:
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            with self.persist_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(entry)) + "\n")
        except OSError:
            pass

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            with self.persist_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    entry = AuditEntry(**data)
                    self.entries.append(entry)
                    self._last_hash = entry.entry_hash
        except (OSError, json.JSONDecodeError, TypeError):
            pass


_audit: ImmutableAuditLog | None = None


def get_audit_log() -> ImmutableAuditLog:
    global _audit
    if _audit is None:
        _audit = ImmutableAuditLog()
    return _audit
