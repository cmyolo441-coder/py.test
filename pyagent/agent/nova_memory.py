"""Nova snapshot memory/cache utilities."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NovaMemoryRecord:
    id: str
    created_at: float
    kind: str
    payload: dict[str, Any]


@dataclass
class NovaMemoryStore:
    path: Path
    records: list[NovaMemoryRecord] = field(default_factory=list)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = [NovaMemoryRecord(**r) for r in data.get("records", [])]
        except (OSError, json.JSONDecodeError, TypeError):
            self.records = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"records": [asdict(r) for r in self.records[-50:]]}, indent=2, default=str), encoding="utf-8")

    def add(self, kind: str, payload: dict[str, Any]) -> NovaMemoryRecord:
        rid = fingerprint(payload)[:16]
        rec = NovaMemoryRecord(rid, time.time(), kind, payload)
        self.records.append(rec)
        self.save()
        return rec

    def latest(self, kind: str | None = None) -> NovaMemoryRecord | None:
        records = [r for r in self.records if kind is None or r.kind == kind]
        return records[-1] if records else None


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode()).hexdigest()


def diff_payloads(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    keys = set(old) | set(new)
    changed = {k: {"old": old.get(k), "new": new.get(k)} for k in keys if old.get(k) != new.get(k)}
    return {"changed_keys": sorted(changed), "changes": changed}


def get_store(path: str | Path | None = None) -> NovaMemoryStore:
    p = Path(path) if path else Path.home() / ".terminal_agent" / "nova_memory.json"
    store = NovaMemoryStore(p)
    store.load()
    return store


def remember_snapshot(payload: dict[str, Any], kind: str = "nova") -> NovaMemoryRecord:
    store = get_store()
    return store.add(kind, payload)


def compare_latest(payload: dict[str, Any], kind: str = "nova") -> dict[str, Any]:
    store = get_store()
    latest = store.latest(kind)
    if latest is None:
        return {"has_previous": False, "fingerprint": fingerprint(payload)[:16]}
    return {"has_previous": True, "previous_id": latest.id, "current_id": fingerprint(payload)[:16], **diff_payloads(latest.payload, payload)}


def compact_history(records: list[NovaMemoryRecord], limit: int = 10) -> list[dict[str, Any]]:
    return [{"id": r.id, "kind": r.kind, "created_at": r.created_at} for r in records[-limit:]]
