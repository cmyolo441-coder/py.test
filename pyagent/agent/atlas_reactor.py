"""ATLAS Reactor v1: autonomous task loop and adaptive solver.

ATLAS is the execution-control layer above Project Brain, OMNI-AION, and TITAN.
It builds real task routes, harvests local evidence, reads work-unit state,
produces adaptive next actions, diagnoses real verification failures, and stores
route memory for future continuation.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExecutionRoute:
    route_type: str
    phases: list[str]
    current_phase: str
    confidence: float


@dataclass(frozen=True)
class EvidencePack:
    active_work_unit: str | None
    changed_files: list[str]
    related_symbols: list[str]
    verification_commands: list[str]
    last_outcome: str | None
    artifact_dir: str | None


class AtlasReactor:
    """Closed-loop engineering route planner and evidence harvester."""

    def __init__(self, root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None, titan_fabric: Any | None = None) -> None:
        self.root = Path(root).resolve()
        self.project_brain = project_brain
        self.omni_aion = omni_aion
        self.titan_fabric = titan_fabric
        self.db_path = self.root / ".pyagent_atlas_reactor.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS routes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    goal TEXT NOT NULL,
                    route_type TEXT NOT NULL,
                    success INTEGER,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    goal TEXT NOT NULL,
                    diagnosis TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)

    def should_activate(self, user_input: str) -> bool:
        if "[ATLAS REACTOR EXECUTION PACKET]" in user_input:
            return False
        lower = user_input.lower().strip()
        if lower in {"continue", "cont", "resume", "aage", "chalu rakho"}:
            return True
        return bool(
            len(user_input.split()) >= 8
            or re.search(r"\b(add|fix|debug|implement|build|integrate|verify|test|continue|resume|advanced|powerful|agent|code|error|traceback|failed|failing)\b", lower)
            or re.search(r"[\w./-]+\.py\b", user_input)
        )

    def classify_route(self, user_input: str) -> ExecutionRoute:
        lower = user_input.lower()
        if lower.strip() in {"continue", "cont", "resume", "aage", "chalu rakho"}:
            return ExecutionRoute("continuation", ["load_state", "find_last_phase", "next_action", "verify", "closure"], "load_state", 0.9)
        if any(w in lower for w in ("fix", "debug", "error", "traceback", "failed", "failing", "crash")):
            return ExecutionRoute("repair_loop", ["capture_failure", "link_error", "inspect", "repair", "verify", "learn"], "capture_failure", 0.86)
        if any(w in lower for w in ("add", "implement", "build", "integrate", "create")):
            return ExecutionRoute("code_change", ["evidence_gathering", "impact_scan", "implementation", "verification", "closure"], "evidence_gathering", 0.84)
        if any(w in lower for w in ("test", "verify", "compile")):
            return ExecutionRoute("verification", ["detect_changed_files", "build_matrix", "run_or_suggest", "diagnose", "record"], "detect_changed_files", 0.82)
        return ExecutionRoute("engineering", ["evidence", "plan", "execute", "verify", "learn"], "evidence", 0.72)

    def harvest_evidence(self, user_input: str) -> EvidencePack:
        active_work_unit = None
        artifact_dir = None
        changed_files: list[str] = []
        verification_commands: list[str] = []
        last_outcome = None

        if self.titan_fabric is not None:
            try:
                active = self.titan_fabric.active_work_unit()
                if active:
                    active_work_unit = active.get("id")
                    artifact_dir = active.get("artifact_dir")
                changed_files = self.titan_fabric.detect_changed_files()
                matrix = self.titan_fabric.build_verification_matrix(changed_files)
                verification_commands = [m.command for m in matrix]
                state = getattr(self.titan_fabric, "state", {}) or {}
                if state.get("last_outcome"):
                    last_outcome = str(state["last_outcome"])[:500]
            except Exception:
                pass

        related_symbols: list[str] = []
        if self.project_brain is not None:
            try:
                snap = getattr(self.project_brain, "snapshot", None) or self.project_brain.scan()
                words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", user_input) if len(w) > 2}
                for sym in getattr(snap, "symbols", [])[:1000]:
                    name = str(sym.get("name", "")).lower()
                    file = str(sym.get("file", ""))
                    if name in words or any(Path(f).name == Path(file).name for f in changed_files):
                        related_symbols.append(f"{sym.get('kind')} {sym.get('name')} in {file}:{sym.get('line')}")
                    if len(related_symbols) >= 12:
                        break
            except Exception:
                pass

        return EvidencePack(active_work_unit, changed_files[:20], related_symbols[:12], verification_commands[:8], last_outcome, artifact_dir)

    def diagnose_failure_text(self, text: str) -> list[str]:
        lower = text.lower()
        diagnosis: list[str] = []
        syntax = re.search(r"File \"([^\"]+)\", line (\d+).*?(SyntaxError|IndentationError|NameError|ImportError|ModuleNotFoundError)", text, re.DOTALL)
        if syntax:
            diagnosis.append(f"{syntax.group(3)} at {syntax.group(1)}:{syntax.group(2)}")
            diagnosis.append("Next: inspect the failing line range, patch the smallest syntax/import issue, rerun compile/test.")
        if "no module named" in lower:
            diagnosis.append("Import path/dependency issue detected; inspect imports and package __init__ files.")
        if "assert" in lower or "failed" in lower:
            diagnosis.append("Test assertion failure detected; compare expected vs actual and inspect related function.")
        if not diagnosis:
            diagnosis.append("No structured failure detected; use latest stderr/stdout and changed files to choose smallest next inspection.")
        return diagnosis

    def continuation_context(self) -> list[str]:
        lines: list[str] = []
        if self.titan_fabric is not None:
            try:
                active = self.titan_fabric.active_work_unit()
                state = getattr(self.titan_fabric, "state", {}) or {}
                if active:
                    lines.append(f"active work unit: {active.get('id')} ({active.get('status')})")
                    lines.append(f"artifact dir: {active.get('artifact_dir')}")
                if state.get("last_outcome"):
                    lines.append("last outcome: " + str(state["last_outcome"])[:300])
            except Exception:
                pass
        with self._connect() as conn:
            rows = conn.execute("SELECT goal, route_type, success FROM routes ORDER BY id DESC LIMIT 3").fetchall()
        if rows:
            lines.append("recent routes:")
            for goal, route_type, success in rows:
                status = "unknown" if success is None else ("success" if success else "failed")
                lines.append(f"- {route_type} [{status}]: {goal[:80]}")
        return lines or ["no previous route state found"]

    def build_execution_packet(self, user_input: str) -> str:
        route = self.classify_route(user_input)
        evidence = self.harvest_evidence(user_input)
        continuation = self.continuation_context() if route.route_type == "continuation" or "continue" in user_input.lower() else []
        diagnosis = self.diagnose_failure_text(user_input) if route.route_type == "repair_loop" else []

        self.record_route(user_input, route, evidence, success=None)

        lines = ["[ATLAS REACTOR EXECUTION PACKET]"]
        lines += [
            "Route:",
            f"- type: {route.route_type}",
            f"- current_phase: {route.current_phase}",
            f"- phases: {' -> '.join(route.phases)}",
            f"- confidence: {route.confidence:.2f}",
            "Evidence Pack:",
            f"- active work unit: {evidence.active_work_unit or 'none'}",
            f"- artifact dir: {evidence.artifact_dir or 'none'}",
            f"- changed files: {', '.join(evidence.changed_files[:12]) or 'none detected'}",
        ]
        if evidence.related_symbols:
            lines.append("- related symbols:")
            lines.extend(f"  - {s}" for s in evidence.related_symbols[:10])
        lines.append("Verification Commands:")
        if evidence.verification_commands:
            lines.extend(f"- `{cmd}`" for cmd in evidence.verification_commands)
        else:
            lines.append("- none generated yet")
        if diagnosis:
            lines.append("Failure Diagnosis:")
            lines.extend(f"- {d}" for d in diagnosis)
        if continuation:
            lines.append("Continuation State:")
            lines.extend(f"- {c}" for c in continuation)
        lines.append("Adaptive Next Actions:")
        lines.extend(f"{i + 1}. {step}" for i, step in enumerate(self.next_actions(route, evidence)))
        lines.append("[/ATLAS REACTOR EXECUTION PACKET]")
        return "\n".join(lines)

    def next_actions(self, route: ExecutionRoute, evidence: EvidencePack) -> list[str]:
        if route.route_type == "continuation":
            return ["read latest artifact/outcome", "resume from failed or next incomplete phase", "verify after change", "close work unit"]
        if route.route_type == "repair_loop":
            return ["parse/link failure to file and symbol", "inspect smallest relevant code range", "patch minimal cause", "run targeted verification", "record route outcome"]
        if route.route_type == "code_change":
            return ["inspect integration points", "implement isolated module or minimal patch", "wire app/tool hooks", "run compileall/related tests", "write outcome artifacts"]
        if route.route_type == "verification":
            return ["use verification matrix", "run safe compile/test command", "diagnose nonzero exit", "store result in artifact"]
        return ["gather evidence", "choose smallest reversible action", "execute", "verify", "learn"]

    def enrich_prompt(self, user_input: str) -> str:
        if not self.should_activate(user_input):
            return user_input
        try:
            return f"{user_input}\n\n{self.build_execution_packet(user_input)}"
        except Exception:
            return user_input

    def record_route(self, goal: str, route: ExecutionRoute, evidence: EvidencePack, success: bool | None) -> None:
        payload = {"route": asdict(route), "evidence": asdict(evidence)}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO routes (ts, goal, route_type, success, payload) VALUES (?, ?, ?, ?, ?)",
                (time.time(), goal[:500], route.route_type, None if success is None else int(success), json.dumps(payload, sort_keys=True)),
            )

    def learn_turn(self, user_input: str, final: str, success: bool = True) -> None:
        route = self.classify_route(user_input)
        evidence = self.harvest_evidence(user_input)
        self.record_route(user_input, route, evidence, success=success)
        if not success or any(w in (final or "").lower() for w in ("traceback", "syntaxerror", "error", "failed")):
            diagnosis = self.diagnose_failure_text(final or user_input)
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO failures (ts, goal, diagnosis, payload) VALUES (?, ?, ?, ?)",
                    (time.time(), user_input[:500], " | ".join(diagnosis), json.dumps({"final": (final or "")[:2000]}, sort_keys=True)),
                )

    def dashboard(self) -> str:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
            failures = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
            recent = conn.execute("SELECT route_type, goal, success FROM routes ORDER BY id DESC LIMIT 5").fetchall()
        lines = ["ATLAS Reactor Dashboard", f"- routes recorded: {total}", f"- failures diagnosed: {failures}", "- recent routes:"]
        for route_type, goal, success in recent:
            status = "pending" if success is None else ("success" if success else "failed")
            lines.append(f"  - {route_type} [{status}]: {goal[:80]}")
        return "\n".join(lines)


def get_atlas_reactor(root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None, titan_fabric: Any | None = None) -> AtlasReactor:
    return AtlasReactor(root=root, project_brain=project_brain, omni_aion=omni_aion, titan_fabric=titan_fabric)
