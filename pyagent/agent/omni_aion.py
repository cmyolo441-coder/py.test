"""OMNI-AION v1: cognitive operating layer for PyAgent.

OMNI-AION is a real local reasoning fabric that enriches relevant user turns
with a compact cognitive packet: intent, constraints, hypotheses, simulation,
critic council, workflow, evidence hints, execution contract, tool strategy,
relevant memory, and future queue. It also records decisions/outcomes in
SQLite so the agent can keep continuity across sessions.
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
class IntentFrame:
    surface_intent: str
    deep_intent: str
    domain: str
    confidence: float
    constraints: list[str]
    success_definition: list[str]


@dataclass(frozen=True)
class Hypothesis:
    title: str
    rationale: str
    impact: int
    feasibility: int
    reuse_value: int

    @property
    def score(self) -> int:
        return self.impact + self.feasibility + self.reuse_value


@dataclass(frozen=True)
class CriticVote:
    critic: str
    verdict: str
    reason: str


@dataclass(frozen=True)
class ExecutionContract:
    goal: str
    allowed_focus: list[str]
    verification: list[str]
    done_when: list[str]


class OmniAION:
    """Cognitive packet generator + ledger + outcome learner."""

    def __init__(self, root: str | Path = ".", project_brain: Any | None = None, db_path: str | Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.project_brain = project_brain
        self.db_path = Path(db_path) if db_path else self.root / ".pyagent_omni_aion.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    goal TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    learning TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS future_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    item TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                )
            """)

    def remember(self, kind: str, title: str, payload: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ledger (ts, kind, title, payload) VALUES (?, ?, ?, ?)",
                (time.time(), kind, title, json.dumps(payload or {}, sort_keys=True)),
            )

    def should_activate(self, user_input: str) -> bool:
        if not user_input.strip():
            return False
        if "[OMNI-AION COGNITIVE PACKET]" in user_input:
            return False
        if len(user_input.split()) >= 8:
            return True
        patterns = [
            r"\b(add|implement|create|build|fix|debug|error|traceback|crash|test|pytest|refactor|integrate|auto|advanced|powerful|agent|code|file|function|class|module)\b",
            r"[\w./-]+\.py\b",
            r"file \".+\", line \d+",
        ]
        return any(re.search(p, user_input, re.IGNORECASE) for p in patterns)

    def analyze_intent(self, user_input: str) -> IntentFrame:
        text = user_input.lower()
        constraints: list[str] = []
        if "real" in text or "only real" in text:
            constraints.append("real implementation, not conceptual only")
        if "python" in text or ".py" in text:
            constraints.append("python codebase")
        if "auto" in text or "khud" in text:
            constraints.append("automatic activation")
        if "command" in text and ("nahi" in text or "not" in text):
            constraints.append("no manual command required")
        if not constraints:
            constraints.append("preserve existing behavior")

        if any(w in text for w in ("fix", "bug", "error", "traceback", "crash", "failed", "failing")):
            return IntentFrame("debug_or_fix", "restore correct behavior with smallest verified change", "debugging", 0.86, constraints, ["root cause identified", "minimal patch applied", "verification passes"])
        if any(w in text for w in ("add", "implement", "create", "build", "integrate")):
            return IntentFrame("feature_addition", "increase agent capability and integrate into runtime", "system_evolution", 0.84, constraints, ["feature exists in code", "feature is integrated", "compile or tests pass"])
        if any(w in text for w in ("advanced", "power", "next level", "max")):
            return IntentFrame("capability_expansion", "maximize autonomy, reasoning depth, and continuity", "agent_cognition", 0.82, constraints, ["more autonomous decisions", "better context", "learning recorded"])
        return IntentFrame("general_engineering", "complete the requested task using project evidence", "software_engineering", 0.72, constraints, ["task addressed", "result explained", "next step clear"])

    def relevant_memory(self, limit: int = 6) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT kind, title FROM ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            outcome_rows = conn.execute("SELECT goal, learning FROM outcomes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        memory = [f"{kind}: {title}" for kind, title in rows]
        memory.extend(f"learned from '{goal[:40]}': {learning[:90]}" for goal, learning in outcome_rows)
        return memory[:limit]

    def generate_hypotheses(self, intent: IntentFrame, user_input: str) -> list[Hypothesis]:
        base: list[Hypothesis] = []
        if intent.domain == "agent_cognition" or "advanced" in user_input.lower():
            base.extend([
                Hypothesis("Add cognitive packet before execution", "A compact internal packet can guide the LLM/tool loop without manual commands.", 9, 8, 9),
                Hypothesis("Persist decision/outcome learning", "SQLite ledger gives continuity and improves future task selection.", 8, 9, 8),
                Hypothesis("Use critic council before action", "Multiple engineering perspectives reduce shallow plans.", 8, 8, 8),
            ])
        if intent.surface_intent == "debug_or_fix":
            base.extend([
                Hypothesis("Traceback-to-symbol linking is key", "Errors usually point to exact file/line/symbol evidence.", 9, 9, 7),
                Hypothesis("Targeted verification beats broad guessing", "Small tests/compile checks validate faster.", 8, 9, 8),
            ])
        if intent.surface_intent == "feature_addition":
            base.extend([
                Hypothesis("Integrate at app lifecycle boundary", "App._handle_turn is closest to user-turn context and agent.send.", 9, 8, 8),
                Hypothesis("Keep new capability isolated", "Separate modules reduce breakage and make compile verification simple.", 8, 9, 9),
            ])
        if not base:
            base.append(Hypothesis("Build evidence-backed workflow", "Inspect, plan, edit, verify, learn is robust across tasks.", 8, 9, 8))
        return sorted(base, key=lambda h: h.score, reverse=True)[:5]

    def simulate(self, intent: IntentFrame, hypotheses: list[Hypothesis]) -> list[str]:
        lines = [f"Best path: {hypotheses[0].title if hypotheses else 'evidence-backed workflow'}"]
        if intent.domain in {"agent_cognition", "system_evolution"}:
            lines.append("Prefer isolated module + app hook over provider/core rewrites.")
            lines.append("Use compact packets to avoid flooding context.")
        if intent.surface_intent == "debug_or_fix":
            lines.append("Prefer traceback parsing and targeted verification before broad refactor.")
        lines.append("Record outcome so future similar tasks reuse the lesson.")
        return lines

    def critic_council(self, intent: IntentFrame) -> list[CriticVote]:
        votes = [
            CriticVote("Architect", "approve", "Keep cognition as a separate module and integrate through app lifecycle hooks."),
            CriticVote("Debugger", "approve", "Preserve traceback/error context and link it to project files."),
            CriticVote("Tester", "modify", "Every code evolution should end with compileall or targeted tests."),
            CriticVote("Maintainer", "approve", "Use dataclasses and SQLite ledger for readable persistent state."),
            CriticVote("Performance Engineer", "modify", "Inject compact context only for relevant tasks, not every tiny greeting."),
            CriticVote("UX Engineer", "approve", "Auto activation matches the user's preference: no manual slash command."),
            CriticVote("Integration Engineer", "approve", "App._handle_turn and goal mode are correct integration surfaces."),
            CriticVote("Continuity Engineer", "approve", "Outcome learning and future queue preserve long-running project memory."),
            CriticVote("Refactor Engineer", "modify", "Avoid large rewrites when targeted patches are enough."),
        ]
        if intent.surface_intent == "debug_or_fix":
            votes.append(CriticVote("Root-Cause Analyst", "approve", "Generate multiple hypotheses before fixing."))
        return votes

    def synthesize_workflow(self, intent: IntentFrame) -> list[str]:
        if intent.surface_intent == "debug_or_fix":
            return ["capture error", "link traceback to files/symbols", "inspect smallest area", "patch", "run targeted verification", "record learning"]
        if intent.surface_intent == "feature_addition":
            return ["identify integration point", "add isolated module", "wire into app flow", "compile", "record outcome", "enqueue next evolution"]
        if intent.domain == "agent_cognition":
            return ["analyze capability gap", "generate hypotheses", "simulate architecture", "critic council review", "create execution contract", "implement and learn"]
        return ["inspect evidence", "plan", "execute", "verify", "summarize"]

    def evidence_hints(self, user_input: str) -> list[str]:
        hints: list[str] = []
        lower = user_input.lower()
        if self.project_brain is not None:
            try:
                snap = getattr(self.project_brain, "snapshot", None) or self.project_brain.scan()
                if getattr(snap, "entrypoints", None):
                    hints.append("entrypoints: " + ", ".join(snap.entrypoints[:4]))
                if getattr(snap, "recent_files", None):
                    hints.append("recent files: " + ", ".join(snap.recent_files[:4]))
            except Exception:
                pass
        if "app.py" in lower or "auto" in lower:
            hints.append("likely hook: agent/app.py around _handle_turn(), _run_goal(), _handle_orchestrated_turn()")
        if "tool" in lower:
            hints.append("tool integration: agent/tools/catalog.py and ToolRegistry")
        if "command" in lower:
            hints.append("command integration: agent/commands/registry.py")
        if not hints:
            hints.append("inspect relevant file before editing; bind plan steps to real symbols")
        return hints[:6]

    def execution_contract(self, intent: IntentFrame, user_input: str) -> ExecutionContract:
        focus = ["current project files related to the request"]
        lower = user_input.lower()
        if "aion" in lower or intent.domain == "agent_cognition":
            focus = ["agent/omni_aion.py", "agent/app.py"]
        elif "tool" in lower:
            focus = ["agent/tools/", "agent/tools/catalog.py"]
        elif "command" in lower:
            focus = ["agent/commands/"]
        verify = ["python -m compileall changed_python_files"]
        if intent.surface_intent == "debug_or_fix":
            verify.append("run targeted failing test/command if known")
        return ExecutionContract(user_input[:240], focus, verify, intent.success_definition)

    def tool_strategy(self, intent: IntentFrame) -> list[str]:
        tools = ["read_file", "grep/find_files", "write_file for complete files", "run_shell for verification"]
        if intent.surface_intent == "debug_or_fix":
            tools.insert(0, "project_link_error")
        if intent.domain in {"agent_cognition", "system_evolution"}:
            tools.append("project_radar/project_next_actions")
        return tools

    def future_items(self, intent: IntentFrame) -> list[str]:
        items = ["compact context pack builder", "causal graph query command/tool", "workflow replay from ledger"]
        if intent.domain == "agent_cognition":
            items.insert(0, "evidence-locked execution planner")
            items.insert(1, "critic council scoring dashboard")
        return items[:5]

    def enqueue_future(self, items: list[str]) -> None:
        with self._connect() as conn:
            existing = {row[0] for row in conn.execute("SELECT item FROM future_queue WHERE status='pending'").fetchall()}
            for idx, item in enumerate(items):
                if item not in existing:
                    conn.execute("INSERT INTO future_queue (ts, item, priority, status) VALUES (?, ?, ?, 'pending')", (time.time(), item, 100 - idx))

    def build_cognitive_packet(self, user_input: str) -> str:
        intent = self.analyze_intent(user_input)
        hypotheses = self.generate_hypotheses(intent, user_input)
        simulation = self.simulate(intent, hypotheses)
        council = self.critic_council(intent)
        workflow = self.synthesize_workflow(intent)
        evidence = self.evidence_hints(user_input)
        contract = self.execution_contract(intent, user_input)
        strategy = self.tool_strategy(intent)
        memory = self.relevant_memory()
        future = self.future_items(intent)
        self.enqueue_future(future)
        self.remember("packet", "cognitive packet built", {"intent": asdict(intent), "goal": user_input[:300]})

        lines = ["[OMNI-AION COGNITIVE PACKET]"]
        lines += [
            "Intent:",
            f"- surface: {intent.surface_intent}",
            f"- deep: {intent.deep_intent}",
            f"- domain: {intent.domain}",
            f"- constraints: {', '.join(intent.constraints)}",
            "- success: " + "; ".join(intent.success_definition),
        ]
        if memory:
            lines.append("Relevant Memory:")
            lines.extend(f"- {m}" for m in memory[:5])
        lines.append("Hypotheses:")
        lines.extend(f"- [{h.score}] {h.title}: {h.rationale}" for h in hypotheses)
        lines.append("Simulation:")
        lines.extend(f"- {s}" for s in simulation)
        lines.append("Critic Council:")
        lines.extend(f"- {v.critic} ({v.verdict}): {v.reason}" for v in council[:9])
        lines.append("Workflow:")
        lines.extend(f"{i + 1}. {step}" for i, step in enumerate(workflow))
        lines.append("Evidence Hints:")
        lines.extend(f"- {h}" for h in evidence)
        lines.append("Execution Contract:")
        lines.extend([
            f"- goal: {contract.goal}",
            f"- focus: {', '.join(contract.allowed_focus)}",
            f"- verify: {', '.join(contract.verification)}",
            f"- done_when: {', '.join(contract.done_when)}",
        ])
        lines.append("Tool Strategy:")
        lines.extend(f"- {t}" for t in strategy)
        lines.append("Future Queue:")
        lines.extend(f"- {f}" for f in future)
        lines.append("[/OMNI-AION COGNITIVE PACKET]")
        return "\n".join(lines)

    def enrich_prompt(self, user_input: str) -> str:
        if not self.should_activate(user_input):
            return user_input
        try:
            return f"{user_input}\n\n{self.build_cognitive_packet(user_input)}"
        except Exception:
            return user_input

    def learn_outcome(self, user_input: str, final: str, success: bool = True) -> None:
        final_text = final or ""
        learning = "successful turn; reuse similar cognitive workflow" if success else "failed turn; inspect exception and reduce assumptions"
        lower = final_text.lower()
        if "compile" in lower and ("pass" in lower or "compiling" in lower):
            learning = "compile verification is useful after code evolution"
        if "error" in lower or "traceback" in lower or not success:
            learning = "failure/error signal should feed traceback linking and smaller next action"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO outcomes (ts, goal, success, summary, learning) VALUES (?, ?, ?, ?, ?)",
                (time.time(), user_input[:500], 1 if success else 0, final_text[:1000], learning),
            )
        self.remember("outcome", "turn outcome learned", {"success": success, "learning": learning})

    def dashboard(self) -> str:
        with self._connect() as conn:
            ledger_count = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
            outcome_count = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            queue = conn.execute("SELECT item, priority FROM future_queue WHERE status='pending' ORDER BY priority DESC, id ASC LIMIT 8").fetchall()
        lines = ["OMNI-AION Dashboard", f"- ledger events: {ledger_count}", f"- learned outcomes: {outcome_count}", "- future queue:"]
        lines.extend(f"  - [{priority}] {item}" for item, priority in queue)
        return "\n".join(lines)


def get_omni_aion(root: str | Path = ".", project_brain: Any | None = None) -> OmniAION:
    return OmniAION(root=root, project_brain=project_brain)
