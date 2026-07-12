"""TITAN Fabric v1: real enterprise work-unit and artifact layer.

TITAN does not pretend to verify. It tracks real repo state, creates work units,
builds verification plans from actual changed files, analyzes Python symbol
impact, writes enterprise artifacts, and learns outcomes across sessions.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ARTIFACT_ROOT = Path(".pyagent_artifacts")
STATE_FILE = ARTIFACT_ROOT / "titan_state.json"


@dataclass(frozen=True)
class WorkUnit:
    id: str
    goal: str
    status: str
    created_at: float
    artifact_dir: str


@dataclass(frozen=True)
class VerificationCommand:
    command: str
    reason: str


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class TitanFabric:
    """Enterprise execution evidence layer for PyAgent."""

    def __init__(self, root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None) -> None:
        self.root = Path(root).resolve()
        self.project_brain = project_brain
        self.omni_aion = omni_aion
        self.artifact_root = self.root / ARTIFACT_ROOT
        self.state_file = self.root / STATE_FILE
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"work_units": [], "last_changed_files": [], "last_outcome": None}

    def _save_state(self) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def _run(self, command: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=timeout, check=False)

    def _slug(self, text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        return f"WU-{time.strftime('%Y%m%d-%H%M%S')}-{digest}"

    def create_work_unit(self, goal: str) -> WorkUnit:
        active = self.active_work_unit()
        if active and active.get("status") == "active":
            return WorkUnit(**active)
        wid = self._slug(goal)
        artifact_dir = self.artifact_root / wid
        artifact_dir.mkdir(parents=True, exist_ok=True)
        wu = WorkUnit(id=wid, goal=goal[:500], status="active", created_at=time.time(), artifact_dir=str(artifact_dir))
        self.state.setdefault("work_units", []).append(asdict(wu))
        self._save_state()
        self._write_json(artifact_dir / "work_unit.json", asdict(wu))
        return wu

    def active_work_unit(self) -> dict[str, Any] | None:
        for wu in reversed(self.state.get("work_units", [])):
            if wu.get("status") == "active":
                return wu
        return None

    def detect_changed_files(self) -> list[str]:
        files: list[str] = []
        try:
            proc = self._run(["git", "diff", "--name-only"], timeout=10)
            if proc.returncode == 0:
                files.extend(x.strip() for x in proc.stdout.splitlines() if x.strip())
            proc2 = self._run(["git", "status", "--porcelain"], timeout=10)
            if proc2.returncode == 0:
                for line in proc2.stdout.splitlines():
                    if len(line) > 3:
                        files.append(line[3:].strip())
        except Exception:
            pass
        if not files:
            try:
                candidates = [p for p in self.root.rglob("*.py") if ".git" not in p.parts and "__pycache__" not in p.parts]
                recent = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:10]
                files = [str(p.relative_to(self.root)) for p in recent]
            except Exception:
                files = []
        deduped: list[str] = []
        for f in files:
            if f and f not in deduped and not f.startswith(".pyagent_artifacts/"):
                deduped.append(f)
        self.state["last_changed_files"] = deduped[:50]
        self._save_state()
        return deduped[:50]

    def _symbols_in_file(self, rel_path: str) -> list[dict[str, Any]]:
        path = self.root / rel_path
        if not path.exists() or path.suffix != ".py":
            return []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        symbols: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({"kind": "function", "name": node.name, "line": node.lineno})
            elif isinstance(node, ast.ClassDef):
                symbols.append({"kind": "class", "name": node.name, "line": node.lineno})
        return sorted(symbols, key=lambda s: s["line"])

    def analyze_impact(self, changed_files: list[str]) -> dict[str, Any]:
        impacts = []
        for f in changed_files:
            path = self.root / f
            item: dict[str, Any] = {"file": f, "exists": path.exists(), "type": path.suffix if path.exists() else "unknown"}
            if path.suffix == ".py" and path.exists():
                item["symbols"] = self._symbols_in_file(f)[:30]
                labels = []
                if f.endswith("app.py"):
                    labels.append("runtime/app lifecycle")
                if "tools" in Path(f).parts:
                    labels.append("tooling surface")
                if "commands" in Path(f).parts:
                    labels.append("command surface")
                if "test" in path.name or "tests" in Path(f).parts:
                    labels.append("test surface")
                item["impact_labels"] = labels or ["python module"]
            impacts.append(item)
        return {"changed_files": changed_files, "impacts": impacts, "generated_at": time.time()}

    def build_verification_matrix(self, changed_files: list[str]) -> list[VerificationCommand]:
        commands: list[VerificationCommand] = []
        py_files = [f for f in changed_files if f.endswith(".py") and (self.root / f).exists()]
        if py_files:
            commands.append(VerificationCommand("python -m compileall " + " ".join(py_files[:20]), "compile changed Python files"))
        test_files = [f for f in py_files if Path(f).name.startswith("test_") or "tests" in Path(f).parts]
        if test_files:
            commands.append(VerificationCommand("python -m pytest " + " ".join(test_files[:10]), "run changed tests"))
        elif (self.root / "tests").exists() and py_files:
            stems = [Path(f).stem.replace("test_", "") for f in py_files[:3]]
            if stems:
                commands.append(VerificationCommand("python -m pytest -k " + " or ".join(stems), "run likely related tests by keyword"))
        if any(Path(f).name in {"main.py", "cli.py"} or f.endswith("app.py") for f in py_files):
            commands.append(VerificationCommand("python -m compileall main.py agent", "compile app entrypoints and agent package"))
        return commands

    def run_verification(self, commands: list[VerificationCommand], execute: bool = False) -> list[CommandResult]:
        results: list[CommandResult] = []
        if not execute:
            return results
        for vc in commands:
            start = time.perf_counter()
            proc = subprocess.run(vc.command, cwd=self.root, shell=True, capture_output=True, text=True, timeout=120, check=False)
            duration = int((time.perf_counter() - start) * 1000)
            results.append(CommandResult(vc.command, proc.returncode, proc.stdout[-4000:], proc.stderr[-4000:], duration))
        return results

    def quality_gate(self, results: list[CommandResult], planned: list[VerificationCommand]) -> dict[str, Any]:
        if not planned:
            return {"status": "no_verification_needed", "passed": True, "reason": "no changed Python/test files detected"}
        if not results:
            return {"status": "planned_not_run", "passed": None, "reason": "verification plan generated but not executed by TITAN"}
        failed = [r for r in results if r.exit_code != 0]
        return {"status": "failed" if failed else "passed", "passed": not failed, "failed_commands": [r.command for r in failed]}

    def write_artifacts(self, wu: WorkUnit, changed_files: list[str], impact: dict[str, Any], matrix: list[VerificationCommand], results: list[CommandResult] | None = None) -> None:
        artifact_dir = Path(wu.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(artifact_dir / "changed_files.json", changed_files)
        self._write_json(artifact_dir / "impact.json", impact)
        self._write_json(artifact_dir / "verification_matrix.json", [asdict(x) for x in matrix])
        self._write_json(artifact_dir / "verification_results.json", [asdict(x) for x in (results or [])])
        self._write_text(artifact_dir / "change_impact.md", self.render_impact(impact))
        self._write_text(artifact_dir / "verification_plan.md", self.render_verification_plan(matrix, results or []))
        self._write_text(artifact_dir / "decision_log.md", self.render_decision_log(wu, impact, matrix))

    def _write_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _write_text(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")

    def render_impact(self, impact: dict[str, Any]) -> str:
        lines = ["# Change Impact", "", f"Generated: {time.ctime(impact.get('generated_at', time.time()))}", ""]
        for item in impact.get("impacts", []):
            lines.append(f"## {item['file']}")
            lines.append(f"- exists: {item.get('exists')}")
            lines.append(f"- type: {item.get('type')}")
            if item.get("impact_labels"):
                lines.append("- labels: " + ", ".join(item["impact_labels"]))
            if item.get("symbols"):
                lines.append("- symbols:")
                for sym in item["symbols"][:20]:
                    lines.append(f"  - {sym['kind']} {sym['name']}:{sym['line']}")
            lines.append("")
        return "\n".join(lines)

    def render_verification_plan(self, matrix: list[VerificationCommand], results: list[CommandResult]) -> str:
        lines = ["# Verification Plan", ""]
        if not matrix:
            lines.append("No verification commands generated.")
        for cmd in matrix:
            lines.append(f"- `{cmd.command}` — {cmd.reason}")
        if results:
            lines += ["", "# Verification Results", ""]
            for r in results:
                lines.append(f"## `{r.command}`")
                lines.append(f"- exit_code: {r.exit_code}")
                lines.append(f"- duration_ms: {r.duration_ms}")
                if r.stdout:
                    lines.append("```stdout\n" + r.stdout + "\n```")
                if r.stderr:
                    lines.append("```stderr\n" + r.stderr + "\n```")
        return "\n".join(lines)

    def render_decision_log(self, wu: WorkUnit, impact: dict[str, Any], matrix: list[VerificationCommand]) -> str:
        return "\n".join([
            "# Decision Log",
            "",
            f"Work Unit: {wu.id}",
            f"Goal: {wu.goal}",
            "",
            "## Evidence",
            f"- Changed files detected: {len(impact.get('changed_files', []))}",
            f"- Verification commands planned: {len(matrix)}",
            "",
            "## Definition of Done",
            "- changed files documented",
            "- impact analyzed",
            "- verification plan generated",
            "- outcome recorded after assistant turn",
        ])

    def enrich_prompt(self, user_input: str) -> str:
        if not self.should_activate(user_input):
            return user_input
        wu = self.create_work_unit(user_input)
        changed = self.detect_changed_files()
        impact = self.analyze_impact(changed)
        matrix = self.build_verification_matrix(changed)
        self.write_artifacts(wu, changed, impact, matrix)
        packet = self.render_packet(wu, changed, impact, matrix)
        return f"{user_input}\n\n{packet}"

    def should_activate(self, user_input: str) -> bool:
        if "[TITAN FABRIC ENTERPRISE PACKET]" in user_input:
            return False
        lower = user_input.lower()
        return bool(
            len(user_input.split()) >= 8
            or any(w in lower for w in ("add", "fix", "debug", "implement", "advanced", "enterprise", "powerful", "continue", "test", "verify", "code", "agent"))
        )

    def render_packet(self, wu: WorkUnit, changed: list[str], impact: dict[str, Any], matrix: list[VerificationCommand]) -> str:
        lines = ["[TITAN FABRIC ENTERPRISE PACKET]"]
        lines += [
            "Work Unit:",
            f"- id: {wu.id}",
            f"- status: {wu.status}",
            f"- artifacts: {wu.artifact_dir}",
            "Repo State:",
            f"- changed files: {', '.join(changed[:12]) or 'none detected'}",
            "Impact Summary:",
        ]
        for item in impact.get("impacts", [])[:8]:
            labels = ", ".join(item.get("impact_labels", [])) if item.get("impact_labels") else item.get("type", "unknown")
            lines.append(f"- {item['file']}: {labels}")
        lines.append("Verification Matrix:")
        if matrix:
            lines.extend(f"- `{m.command}` — {m.reason}" for m in matrix)
        else:
            lines.append("- no verification command generated from current changed files")
        lines += [
            "Definition of Done:",
            "- artifacts written",
            "- changed files documented",
            "- impact analyzed",
            "- verification plan generated",
            "- outcome learned after response",
            "[/TITAN FABRIC ENTERPRISE PACKET]",
        ]
        return "\n".join(lines)

    def learn_turn(self, user_input: str, final: str, success: bool = True) -> None:
        active = self.active_work_unit()
        if not active:
            return
        artifact_dir = Path(active["artifact_dir"])
        outcome = {
            "ts": time.time(),
            "goal": user_input[:500],
            "success": success,
            "assistant_summary": (final or "")[:2000],
        }
        self._write_json(artifact_dir / "outcome.json", outcome)
        self._write_text(artifact_dir / "outcome.md", "# Outcome\n\n" + json.dumps(outcome, indent=2))
        self.state["last_outcome"] = outcome
        for wu in self.state.get("work_units", []):
            if wu.get("id") == active.get("id"):
                wu["status"] = "completed" if success else "failed"
                wu["completed_at"] = time.time()
                break
        self._save_state()

    def dashboard(self) -> str:
        units = self.state.get("work_units", [])
        active = [u for u in units if u.get("status") == "active"]
        completed = [u for u in units if u.get("status") == "completed"]
        lines = [
            "TITAN Dashboard",
            f"- active work units: {len(active)}",
            f"- completed work units: {len(completed)}",
            f"- last changed files: {', '.join(self.state.get('last_changed_files', [])[:8]) or 'none'}",
            f"- artifact root: {self.artifact_root}",
        ]
        return "\n".join(lines)


def get_titan_fabric(root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None) -> TitanFabric:
    return TitanFabric(root=root, project_brain=project_brain, omni_aion=omni_aion)
