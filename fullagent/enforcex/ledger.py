"""ledger — Immutable, tamper-evident enforcement ledger.

Every gate decision, detection and score the enforcement engine
produces lands here as an append-only record. Two integrity mechanisms
protect it:

  HASH CHAIN   — each entry carries entry_sha256 =
                 sha256(prev_hash || canonical(entry_without_hash)).
                 Rewriting or deleting any historical entry breaks
                 every subsequent link.
  MERKLE ROOT  — merkle_root(entries) computes a binary Merkle root
                 over the entry hashes (odd nodes are promoted), so a
                 checkpoint file can prove a whole batch in one line.

Persistence is JSONL (one JSON object per line) to a configurable path
with fsync on flush, and best-effort behavior on read errors: a torn or
truncated tail line is reported via `verify()` instead of crashing the
caller. Compaction rewrites the chain into a fresh file (preserving
verifiability by re-linking from a genesis anchor). The query API
supports lookup by rule_id, severity, time range and session. An
in-memory-only mode (path=None) keeps the ledger usable in tests.

Stdlib only, thread-safe, never raises from public read paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "LedgerEntry", "Ledger", "LedgerError", "GENESIS_HASH",
    "canonical_json", "sha256_hex", "merkle_root", "verify_chain_dicts",
    "SCHEMA_VERSION",
]


# ── constants ────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
CORRUPT_BACKUP_SUFFIX = ".corrupt"
MAX_LINE_BYTES = 1 << 20          # 1 MiB sanity cap per JSONL line
DEFAULT_MAX_ENTRIES = 100_000     # hard cap when reading untrusted files


class LedgerError(Exception):
    """Raised only by strict write paths (append with require_disk=True)."""


# ── hashing primitives ───────────────────────────────────────────────────

def _sanitize(obj: Any) -> Any:
    """Replace non-finite floats (NaN/inf) with None so canonical JSON
    never raises: the old allow_nan=False made seal()/persist() crash
    on any payload containing NaN, contradicting Ledger.append's
    "never raises" contract. Non-str dict keys are stringified the way json does."""
    if isinstance(obj, float):
        return obj if obj == obj and abs(obj) != float("inf") else None
    if isinstance(obj, dict):
        # non-str keys: json.dumps(sort_keys=True) raised TypeError on
        # mixed int/str keys, and int keys sorted numerically in memory
        # but as strings after reload, so the entry never re-verified.
        # Stringify them exactly as json would (1 -> "1", True -> "true").
        return {_json_key(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _json_key(k: Any) -> str:
    if isinstance(k, str):
        return k
    if k is None or isinstance(k, (bool, int, float)):
        try:
            return json.dumps(k)
        except ValueError:          # NaN/inf key
            return str(k)
    return str(k)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, tight separators, no NaN
    (non-finite floats are encoded as null, never raised)."""
    return json.dumps(_sanitize(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str, allow_nan=False)


def sha256_hex(text: Any) -> str:
    if isinstance(text, bytes):
        return hashlib.sha256(text).hexdigest()
    if not isinstance(text, str):
        # attestation-style coercion: hash the canonical form of
        # non-string input instead of raising AttributeError on .encode
        text = canonical_json(text)
    # surrogatepass: a payload string holding a lone surrogate (text
    # decoded with surrogateescape) made seal() -- and so append() --
    # raise UnicodeEncodeError despite the never-raises contract
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _jsonl_line(d: Dict[str, Any]) -> bytes:
    """One on-disk JSONL record. Canonical UTF-8 normally; pure-ASCII
    escapes when a lone surrogate cannot be encoded (json.loads restores
    the exact string, so the entry hash still verifies on reload)."""
    text = canonical_json(d)
    try:
        return (text + "\n").encode("utf-8")
    except UnicodeEncodeError:
        return (json.dumps(_sanitize(d), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True,
                           default=str, allow_nan=False) + "\n").encode("ascii")


def _entry_hash(entry: Dict[str, Any]) -> str:
    """Hash the canonical form of an entry minus its own hash fields."""
    payload = {k: v for k, v in entry.items()
               if k not in ("entry_sha256",)}
    return sha256_hex(canonical_json(payload))


def merkle_root(hashes: Sequence[str]) -> str:
    """Binary Merkle root over hex hashes; empty -> GENESIS_HASH.
    Odd node counts promote the last node (duplicated) upward."""
    if not hashes:
        return GENESIS_HASH
    # strip a literal "0x" prefix only: lstrip("0x") also ate leading
    # zero digits, so "0x00ab" and "0xab" produced the same root
    level = [h[2:] if isinstance(h, str) and h.startswith("0x")
             else str(h) for h in hashes]
    while len(level) > 1:
        nxt: List[str] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            nxt.append(sha256_hex(left + right))
        level = nxt
    return level[0]


# ── entry dataclass ──────────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    """One immutable enforcement event."""
    kind: str                          # decision | detection | score | event
    # defaults let callers build a detached entry from kind + payload
    # (foresight does LedgerEntry(kind=..., payload=...), which raised
    # TypeError and was silently skipped)
    ts: float = 0.0                    # epoch seconds
    seq: int = 0                       # position in this ledger
    prev_hash: str = GENESIS_HASH      # previous entry's entry_sha256
    payload: Dict[str, Any] = field(default_factory=dict)  # JSON-serializable
    rule_id: str = ""
    severity: str = ""
    session_id: str = ""
    entry_sha256: str = ""             # filled on seal()

    def seal(self) -> "LedgerEntry":
        self.entry_sha256 = _entry_hash(self.to_dict(without_hash=True))
        return self

    def to_dict(self, *, without_hash: bool = False) -> Dict[str, Any]:
        d = {
            "kind": str(self.kind or "event"),
            "ts": round(float(self.ts), 6),
            "seq": int(self.seq),
            "prev_hash": str(self.prev_hash),
            "payload": self.payload if isinstance(self.payload, dict)
                       else {"raw": str(self.payload)},
            "rule_id": str(self.rule_id or ""),
            "severity": str(self.severity or ""),
            "session_id": str(self.session_id or ""),
        }
        if not without_hash:
            d["entry_sha256"] = self.entry_sha256
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "LedgerEntry":
        if not isinstance(d, dict):
            d = {}
        try:
            ts = float(d.get("ts", 0.0))
        except (TypeError, ValueError):
            ts = 0.0
        try:
            seq = int(d.get("seq", 0))
        except (TypeError, ValueError):
            seq = 0
        payload = d.get("payload")
        return cls(
            kind=str(d.get("kind", "event")),
            ts=ts,
            seq=seq,
            prev_hash=str(d.get("prev_hash", GENESIS_HASH)),
            payload=payload if isinstance(payload, dict) else {},
            rule_id=str(d.get("rule_id", "")),
            severity=str(d.get("severity", "")),
            session_id=str(d.get("session_id", "")),
            entry_sha256=str(d.get("entry_sha256", "")),
        )

    def is_self_consistent(self) -> bool:
        if not self.entry_sha256:
            return False
        return _entry_hash(self.to_dict(without_hash=True)) == self.entry_sha256


_ENTRY_KEYS = frozenset(("kind", "ts", "seq", "prev_hash", "payload",
                         "rule_id", "severity", "session_id", "entry_sha256"))


def _raw_matches_entry(raw: Any, entry: "LedgerEntry") -> bool:
    """True when the raw record is exactly the sealed form of `entry`.

    from_dict() drops unknown keys and coerces types, so without this
    check a record with injected fields ("approved": true) or a
    retyped value ("seq": "0") still verified as untampered.
    """
    if not isinstance(raw, dict) or not set(raw) <= _ENTRY_KEYS:
        return False
    try:
        return canonical_json(raw) == canonical_json(entry.to_dict())
    except Exception:
        return False


def verify_chain_dicts(dicts: Sequence[Dict[str, Any]], *,
                       start_prev: str = GENESIS_HASH,
                       start_seq: int = 0) -> Tuple[bool, str]:
    """Verify hash linkage + self-consistency over raw dicts.
    Returns (ok, first_bad_reason). `start_prev` / `start_seq` anchor a
    window that does not begin at genesis (a trimmed in-memory tail)."""
    if not isinstance(dicts, (list, tuple)):
        return False, "chain is not a list of dicts"
    prev = start_prev
    expected_seq = start_seq
    for i, raw in enumerate(dicts):
        entry = LedgerEntry.from_dict(raw)
        if not _raw_matches_entry(raw, entry):
            return False, f"entry {i}: fields do not match the sealed record"
        if entry.seq != expected_seq:
            return False, (f"entry {i}: seq {entry.seq} != expected "
                           f"{expected_seq}")
        if entry.prev_hash != prev:
            return False, f"entry {i}: prev_hash breaks chain"
        if not entry.is_self_consistent():
            return False, f"entry {i}: entry_sha256 mismatch"
        prev = entry.entry_sha256
        expected_seq += 1
    return True, ""


# ── the ledger ───────────────────────────────────────────────────────────

class Ledger:
    """Append-only tamper-evident log with JSONL persistence."""

    def __init__(self, path: Optional[str] = None, *,
                 fsync: bool = True,
                 load_existing: bool = True,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.path = os.fspath(path) if path else ""
        self.fsync = bool(fsync)
        self.max_entries = max(64, int(max_entries))
        self._lock = threading.RLock()
        self._entries: List[LedgerEntry] = []
        self._seq: int = 0
        self._fh = None
        # chain anchor of the in-memory window: genesis unless older
        # entries were trimmed (memory cap) -- then the hash/seq the
        # first retained entry must link to
        self._anchor_prev: str = GENESIS_HASH
        self._anchor_seq: int = 0
        # on-disk repair state found by _load, applied on first write
        self._repair_offset: Optional[int] = None
        self._needs_newline = False
        self._load_issue = ""
        # entries sealed while the disk was unavailable, written (in
        # order) on the next successful persist so the file never gets
        # a hole in its hash chain
        self._pending: List[LedgerEntry] = []
        if self.path and load_existing:
            self._load()
        # entries already on disk are trusted structurally; new appends
        # chain onto whatever the tail is (verified on demand).

    # ── loading ──────────────────────────────────────────────────────

    def _load(self) -> None:
        entries: List[LedgerEntry] = []
        try:
            with open(self.path, "rb") as fh:
                offset = 0
                while True:
                    bline = fh.readline(MAX_LINE_BYTES + 1)
                    if not bline:
                        break
                    if len(bline) > MAX_LINE_BYTES:
                        self._repair_offset = offset
                        break
                    stripped = bline.strip()
                    if not stripped:
                        offset += len(bline)
                        continue
                    try:
                        raw = json.loads(stripped.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        self._repair_offset = offset   # torn tail
                        break
                    if not isinstance(raw, dict):
                        self._repair_offset = offset
                        break
                    entry = LedgerEntry.from_dict(raw)
                    if not self._load_issue and \
                            not _raw_matches_entry(raw, entry):
                        self._load_issue = (
                            f"entry seq {entry.seq}: fields do not match "
                            f"the sealed record")
                    entries.append(entry)
                    offset += len(bline)
                    if not bline.endswith(b"\n"):
                        self._needs_newline = True
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError):
            pass
        # cap memory on absurd files; keep the chain anchor of the
        # retained window so verify() still works and new appends keep
        # the on-disk seq numbering (it used to restart at max_entries,
        # forking the file's chain and failing every verify)
        if len(entries) > self.max_entries:
            entries = entries[-self.max_entries:]
            self._anchor_prev = entries[0].prev_hash
            self._anchor_seq = entries[0].seq
        self._entries = entries
        self._seq = (entries[-1].seq + 1) if entries else 0

    # ── appending ────────────────────────────────────────────────────

    def _repair_tail(self) -> None:
        """Before the first append: move a torn/garbage tail found by
        _load aside (to <path>.corrupt) and cut the file back to its
        last good record. Appending after a torn line used to bury it
        mid-file, so every later reload stopped there and silently lost
        all entries written after it."""
        if self._repair_offset is None and not self._needs_newline:
            return
        try:
            with open(self.path, "r+b") as fh:
                if self._repair_offset is not None:
                    fh.seek(self._repair_offset)
                    garbage = fh.read()
                    if garbage:
                        try:
                            with open(self.path + CORRUPT_BACKUP_SUFFIX,
                                      "ab") as bak:
                                bak.write(garbage)
                        except OSError:
                            pass
                    fh.truncate(self._repair_offset)
                    fh.seek(0, os.SEEK_END)
                if fh.tell() > 0:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        fh.write(b"\n")
        except FileNotFoundError:
            pass
        except OSError:
            return          # retry on the next open
        self._repair_offset = None
        self._needs_newline = False

    def _open_fh(self):
        if self._fh is None and self.path:
            parent = os.path.dirname(os.path.abspath(self.path)) or "."
            if not os.path.isdir(parent):
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError:
                    return None
            self._repair_tail()
            try:
                self._fh = open(self.path, "ab")
            except OSError:
                return None
        return self._fh

    def _persist(self, entry: Optional[LedgerEntry]) -> bool:
        """Write pending entries plus `entry`; True when all reached the
        file. A failed write is cut back so no torn line is left."""
        if entry is not None:
            self._pending.append(entry)
        if not self._pending:
            return True
        fh = self._open_fh()
        if fh is None:
            self._trim_pending()
            return False                # memory-only; never crash caller
        try:
            pos = fh.tell()
        except OSError:
            pos = None
        try:
            fh.write(b"".join(_jsonl_line(e.to_dict()) for e in self._pending))
            fh.flush()
            if self.fsync:
                os.fsync(fh.fileno())
        except (OSError, ValueError):
            try:
                if pos is not None:
                    fh.truncate(pos)
            except (OSError, ValueError):
                pass
            try:
                fh.close()
            except (OSError, ValueError):
                pass
            self._fh = None
            self._trim_pending()
            return False                # disk trouble degrades to memory
        self._pending.clear()
        return True

    def _trim_pending(self) -> None:
        if len(self._pending) > self.max_entries:
            del self._pending[:-self.max_entries]

    def _trim_memory(self) -> None:
        """Bound the in-memory window: the ledger is append-only, so
        without this every entry of a long session stayed in RAM."""
        slack = max(1, self.max_entries // 10)
        if len(self._entries) > self.max_entries + slack:
            drop = len(self._entries) - self.max_entries
            self._anchor_prev = self._entries[drop - 1].entry_sha256
            self._anchor_seq = self._entries[drop].seq
            del self._entries[:drop]

    def append(self, kind: str, payload: Dict[str, Any], *,
               rule_id: str = "", severity: str = "",
               session_id: str = "", ts: Optional[float] = None,
               require_disk: bool = False) -> Optional[LedgerEntry]:
        """Seal and append one entry. Returns the sealed entry; raises
        LedgerError only when require_disk and persistence failed.
        Never raises otherwise."""
        if not isinstance(payload, dict):
            payload = {"raw": str(payload)}
        try:
            ts_v = float(ts) if ts is not None else time.time()
        except (TypeError, ValueError, OverflowError):
            ts_v = time.time()
        if ts_v != ts_v or ts_v in (float("inf"), float("-inf")):
            # a NaN ts was hashed as null but reloaded as 0.0, so the
            # entry could never verify again
            ts_v = time.time()
        with self._lock:
            if self._entries:
                prev_hash = self._entries[-1].entry_sha256
            else:
                prev_hash = self._anchor_prev
            entry = LedgerEntry(
                kind=str(kind or "event"),
                ts=ts_v,
                seq=self._seq,
                prev_hash=prev_hash,
                payload=payload,
                rule_id=str(rule_id or ""),
                severity=str(severity or ""),
                session_id=str(session_id or ""),
            )
            entry.seal()
            if require_disk:
                parent_ok = bool(self.path) and os.path.isdir(
                    os.path.dirname(os.path.abspath(self.path)) or ".")
                if not parent_ok:
                    # nothing written, nothing appended: the old code
                    # created the directory, wrote the line and only
                    # then rolled back memory, leaving a duplicate seq
                    # on disk for the next append
                    raise LedgerError(f"cannot persist ledger at {self.path}")
                if not self._persist(entry):
                    if self._pending and self._pending[-1] is entry:
                        self._pending.pop()
                    raise LedgerError(f"cannot persist ledger at {self.path}")
            elif self.path:
                self._persist(entry)
            self._entries.append(entry)
            self._seq += 1
            self._trim_memory()
            return entry

    # convenience appenders -----------------------------------------------

    def append_decision(self, decision: Any, *, session_id: str = "") -> \
            Optional[LedgerEntry]:
        payload = decision.to_dict() if hasattr(decision, "to_dict") \
            else {"raw": str(decision)}
        action = ""
        if isinstance(payload, dict):
            action = str(payload.get("action", ""))
        return self.append("decision", payload,
                           severity="critical" if action == "block"
                           else ("high" if action in ("retry", "escalate")
                                 else "info"),
                           session_id=session_id)

    def append_score(self, score: Any, *, session_id: str = "") -> \
            Optional[LedgerEntry]:
        payload = score.to_dict() if hasattr(score, "to_dict") \
            else {"raw": str(score)}
        return self.append("score", payload, session_id=session_id)

    def append_event(self, name: str, **fields: Any) -> Optional[LedgerEntry]:
        payload = {"event": str(name)}
        payload.update(fields)
        return self.append("event", payload)

    # ── verification ─────────────────────────────────────────────────

    def verify(self) -> Tuple[bool, str, str]:
        """Full integrity check. Returns (ok, reason, merkle_root)."""
        with self._lock:
            dicts = [e.to_dict() for e in self._entries]
            anchor_prev, anchor_seq = self._anchor_prev, self._anchor_seq
            load_issue = self._load_issue
        ok, reason = verify_chain_dicts(dicts, start_prev=anchor_prev,
                                        start_seq=anchor_seq)
        if ok and load_issue:
            ok, reason = False, load_issue
        root = self.merkle()
        return ok, reason, root

    def merkle(self) -> str:
        with self._lock:
            return merkle_root([e.entry_sha256 for e in self._entries
                                if e.entry_sha256])

    def checkpoint(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """A one-line proof: count + merkle root + tail hash."""
        with self._lock:
            tail = self._entries[-1].entry_sha256 if self._entries else \
                GENESIS_HASH
            cp = {
                "schema": SCHEMA_VERSION,
                "ts": round(time.time(), 3),
                "count": len(self._entries),
                "merkle": self.merkle(),
                "tail": tail,
            }
        if isinstance(extra, dict):
            cp.update(extra)
        return cp

    # ── querying ─────────────────────────────────────────────────────

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __iter__(self) -> Iterator[LedgerEntry]:
        with self._lock:
            return iter(list(self._entries))

    def tail(self, n: int = 10) -> List[LedgerEntry]:
        with self._lock:
            return list(self._entries[-max(0, int(n)):])

    def by_rule(self, rule_id: str) -> List[LedgerEntry]:
        rid = str(rule_id or "")
        with self._lock:
            return [e for e in self._entries if e.rule_id == rid]

    def by_session(self, session_id: str) -> List[LedgerEntry]:
        sid = str(session_id or "")
        with self._lock:
            return [e for e in self._entries if e.session_id == sid]

    def by_kind(self, kind: str) -> List[LedgerEntry]:
        k = str(kind or "")
        with self._lock:
            return [e for e in self._entries if e.kind == k]

    def by_time(self, start: float = 0.0, end: Optional[float] = None) -> \
            List[LedgerEntry]:
        end = time.time() if end is None else float(end)
        with self._lock:
            return [e for e in self._entries
                    if start <= e.ts <= end]

    def query(self, *, rule_id: str = "", severity: str = "",
              session_id: str = "", kind: str = "",
              start: float = 0.0, end: Optional[float] = None,
              limit: int = 0) -> List[LedgerEntry]:
        """Combined filter; empty fields are wildcards."""
        end_v = time.time() if end is None else float(end)
        rid, sev, sid, k = (str(rule_id or ""), str(severity or ""),
                            str(session_id or ""), str(kind or ""))
        with self._lock:
            out = [e for e in self._entries
                   if (not rid or e.rule_id == rid)
                   and (not sev or e.severity == sev)
                   and (not sid or e.session_id == sid)
                   and (not k or e.kind == k)
                   and start <= e.ts <= end_v]
        if limit and limit > 0:
            out = out[-int(limit):]
        return out

    # ── compaction ───────────────────────────────────────────────────

    def compact(self, keep_last: int = 0,
                new_path: Optional[str] = None, *,
                force: bool = False) -> Dict[str, Any]:
        """Rewrite the ledger, keeping only the last `keep_last` entries
        (0 = all) re-chained from genesis, so the compacted file is
        fully verifiable on its own. Writes to new_path or replaces the
        current file atomically. Returns a summary dict.

        The chain is verified first: resealing a tampered chain would
        launder the tampering into a freshly valid file, so a broken
        chain is refused unless ``force=True``. After compacting to
        ``new_path`` the ledger continues in that file (appending to the
        old file would chain onto entries it does not contain)."""
        keep_last = max(0, int(keep_last))
        target = os.fspath(new_path) if new_path else self.path
        if not target:
            return {"compacted": 0, "reason": "memory-only ledger"}

        # one critical section: an append between the snapshot and the
        # file replace used to be written to the old file and lost
        with self._lock:
            ok, reason, _root = self.verify()
            if not ok and not force:
                return {"compacted": 0,
                        "reason": f"chain verification failed: {reason}"}
            keep = list(self._entries[-keep_last:] if keep_last
                        else self._entries)
            resealed: List[LedgerEntry] = []
            prev = GENESIS_HASH
            for i, e in enumerate(keep):
                fresh = LedgerEntry(
                    kind=e.kind, ts=e.ts, seq=i, prev_hash=prev,
                    payload=dict(e.payload), rule_id=e.rule_id,
                    severity=e.severity, session_id=e.session_id)
                fresh.seal()
                resealed.append(fresh)
                prev = fresh.entry_sha256

            tmp_path = target + ".compact.tmp"
            try:
                with open(tmp_path, "wb") as fh:
                    for e in resealed:
                        fh.write(_jsonl_line(e.to_dict()))
                    fh.flush()
                    os.fsync(fh.fileno())
                # close our handle before replacing / switching files
                if self._fh is not None:
                    try:
                        self._fh.close()
                    except OSError:
                        pass
                    self._fh = None
                os.replace(tmp_path, target)
            except OSError as exc:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                return {"compacted": 0, "reason": f"write failed: {exc}"}

            self.path = target
            self._entries = resealed
            self._seq = len(resealed)
            self._anchor_prev = GENESIS_HASH
            self._anchor_seq = 0
            self._pending = []
            self._repair_offset = None
            self._needs_newline = False
            self._load_issue = ""
        return {"compacted": len(resealed), "path": target,
                "merkle": merkle_root([e.entry_sha256 for e in resealed])}

    # ── export / import ────────────────────────────────────────────────

    def export_dicts(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._entries]

    def export_jsonl(self) -> str:
        with self._lock:
            return "".join(canonical_json(e.to_dict()) + "\n"
                           for e in self._entries)

    @classmethod
    def from_dicts(cls, dicts: Sequence[Any], *, path: Optional[str] = None,
                   verify: bool = True) -> "Ledger":
        """Build a ledger from raw dicts; failed verification keeps the
        entries but `verify()` will report the damage."""
        led = cls(path=None, load_existing=False)
        if not isinstance(dicts, (list, tuple)):
            return led
        prev = GENESIS_HASH
        for i, raw in enumerate(dicts):
            entry = LedgerEntry.from_dict(raw)
            if entry.seq == 0 and entry.entry_sha256 and not entry.prev_hash:
                entry.prev_hash = prev   # tolerate genesis-less exports
            led._entries.append(entry)
            prev = entry.entry_sha256 or prev
        led._seq = (led._entries[-1].seq + 1) if led._entries else 0
        if verify:
            led._verified = verify_chain_dicts(
                [e.to_dict() for e in led._entries])
        return led

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            kinds: Dict[str, int] = {}
            for e in self._entries:
                kinds[e.kind] = kinds.get(e.kind, 0) + 1
            return {
                "path": self.path,
                "entries": len(self._entries),
                "kinds": kinds,
                "merkle": self.merkle(),
                "seq": self._seq,
            }

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None


# ── self-test ────────────────────────────────────────────────────────────

def _self_test() -> int:
    import tempfile
    import shutil

    # hashing primitives
    assert len(sha256_hex("abc")) == 64
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert merkle_root([]) == GENESIS_HASH
    assert len(merkle_root(["a", "b", "c"])) == 64
    # merkle is order-sensitive
    assert merkle_root(["a", "b"]) != merkle_root(["b", "a"])
    # even/odd both hash fine and are deterministic
    assert merkle_root(["a", "b", "c"]) == merkle_root(["a", "b", "c"])
    assert merkle_root(["a"]) == "a"

    # entry seal + tamper detection
    e = LedgerEntry(kind="decision", ts=time.time(), seq=0,
                    prev_hash=GENESIS_HASH, payload={"action": "allow"})
    e.seal()
    assert e.is_self_consistent()
    bad = LedgerEntry.from_dict(e.to_dict())
    bad.payload["action"] = "block"
    assert not bad.is_self_consistent()

    # in-memory ledger basics
    led = Ledger(None)
    a = led.append("decision", {"action": "allow"}, rule_id="R01",
                   session_id="s1")
    b = led.append("decision", {"action": "block"}, rule_id="R02",
                   severity="critical", session_id="s1")
    assert a.seq == 0 and b.seq == 1
    assert b.prev_hash == a.entry_sha256
    ok, reason, root = led.verify()
    assert ok and root != GENESIS_HASH, reason

    # queries
    assert len(led.by_rule("R01")) == 1
    assert len(led.by_session("s1")) == 2
    assert len(led.by_kind("decision")) == 2
    assert len(led.by_time(start=time.time() - 60)) == 2
    q = led.query(severity="critical")
    assert len(q) == 1 and q[0].rule_id == "R02"
    assert len(led.query(limit=1)) == 1

    # tampering breaks the chain
    led._entries[0].payload["action"] = "block"
    ok, reason, _ = led.verify()
    assert not ok and "self" in reason or "sha" in reason, reason
    # repair by restoring
    led._entries[0].payload["action"] = "allow"
    ok, _, _ = led.verify()
    assert ok

    # deleting a middle entry breaks linkage
    led2 = Ledger(None)
    for i in range(4):
        led2.append("event", {"i": i})
    del led2._entries[1]
    for i, e in enumerate(led2._entries):
        e.seq = i
    ok, reason, _ = led2.verify()
    assert not ok and "prev_hash" in reason

    # decision/score convenience appenders
    class _D:
        def to_dict(self):
            return {"action": "block"}
    class _S:
        def to_dict(self):
            return {"total": 12.0}
    led3 = Ledger(None)
    led3.append_decision(_D(), session_id="x")
    led3.append_score(_S())
    led3.append_event("compact", note="manual")
    assert len(led3.by_kind("decision")) == 1
    assert led3.by_kind("decision")[0].severity == "critical"
    assert len(led3.by_kind("score")) == 1

    # JSONL persistence + reload
    tmp = tempfile.mkdtemp(prefix="enforcex-ledger-")
    try:
        lpath = os.path.join(tmp, "ledger.jsonl")
        l1 = Ledger(lpath)
        for i in range(10):
            l1.append("event", {"n": i}, rule_id=f"R{i:02d}",
                      session_id="sess")
        cp1 = l1.checkpoint()
        assert cp1["count"] == 10

        l2 = Ledger(lpath)
        assert len(l2) == 10
        ok, reason, root = l2.verify()
        assert ok and root == cp1["merkle"], reason

        # append continues the chain after reload
        l2.append("event", {"n": 10})
        assert len(l2) == 11
        ok, _, _ = l2.verify()
        assert ok

        # export / import round-trip
        raw = l2.export_dicts()
        l3 = Ledger.from_dicts(raw, verify=True)
        ok, reason, root = l3.verify()
        assert ok and root == l2.merkle(), reason

        # export_jsonl string parses back
        text = l2.export_jsonl()
        lines = [json.loads(x) for x in text.strip().splitlines()]
        assert len(lines) == 11
        l4 = Ledger.from_dicts(lines)
        ok, _, _ = l4.verify()
        assert ok

        # compaction: keep last 3, fully verifiable
        summary = l2.compact(keep_last=3)
        assert summary["compacted"] == 3, summary
        ok, reason, _ = l2.verify()
        assert ok and len(l2) == 3, reason
        l5 = Ledger(lpath)
        assert len(l5) == 3
        ok, _, _ = l5.verify()
        assert ok

        # compaction to a new path leaves the original intact
        l6 = Ledger(os.path.join(tmp, "other.jsonl"))
        for i in range(5):
            l6.append("event", {"n": i})
        alt = os.path.join(tmp, "alt.jsonl")
        s = l6.compact(keep_last=2, new_path=alt)
        assert s["compacted"] == 2 and len(l6) == 2
        assert os.path.exists(alt)

        # torn tail: append garbage, reload keeps parsed prefix
        with open(lpath, "a", encoding="utf-8") as fh:
            fh.write('{"kind": "event", "ts": 1, "seq": 99, "pa')
        l7 = Ledger(lpath)
        assert len(l7) == 3
        ok, _, _ = l7.verify()
        assert ok

        # corrupt whole file -> empty ledger, no crash
        cpath = os.path.join(tmp, "corrupt.jsonl")
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write("not json\n" * 3)
        l8 = Ledger(cpath)
        assert len(l8) == 0
        l8.append("event", {"recovered": True})
        assert len(l8) == 1

        # require_disk raises when the directory is missing
        missing_dir = os.path.join(tmp, "nope", "deep.jsonl")
        l9 = Ledger(missing_dir, load_existing=False)
        got = None
        try:
            l9.append("event", {"x": 1}, require_disk=True)
        except LedgerError as exc:
            got = exc
        assert got is not None
        # but the normal path creates the directory and works
        e_ok = l9.append("event", {"x": 2})
        assert e_ok is not None and os.path.exists(missing_dir)

        # non-dict payloads are tolerated
        l9.append("event", "just a string")
        assert len(l9) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("ledger._self_test: OK")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_self_test())
