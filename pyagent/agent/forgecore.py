"""FORGECORE v1: real mission graph, verification, repair, and replay runtime.

FORGECORE is the mission runtime above Project Brain, OMNI-AION, TITAN, and
ATLAS. It creates persistent missions, harvests real evidence, builds a mission
graph, runs narrowly allowlisted verification commands, converts failures into
repair objectives, and writes replay artifacts.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MISSION_ROOT = Path(".pyagent_missions")
STATE_FILE = MISSION_ROOT / "forgecore_state.json"


@dataclass(frozen=True)
class Mission:
    id: str
    goal: str
    status: str
    created_at: float
    mission_dir: str


@dataclass(frozen=True)
class MissionNode:
    id: str
    type: str
    status: str
    title: str


@dataclass(frozen=True)
class VerificationResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class ForgeCore:
    """Persistent mission runtime with real verification and repair objectives."""

    def __init__(self, root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None, titan_fabric: Any | None = None, atlas_reactor: Any | None = None) -> None:
        self.root = Path(root).resolve()
        self.project_brain = project_brain
        self.omni_aion = omni_aion
        self.titan_fabric = titan_fabric
        self.atlas_reactor = atlas_reactor
        self.mission_root = self.root / MISSION_ROOT
        self.state_file = self.root / STATE_FILE
        self.mission_root.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"missions": [], "active_mission": None, "last_repair": None}

    def _save_state(self) -> None:
        self.mission_root.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def should_activate(self, user_input: str) -> bool:
        if "[FORGECORE MISSION PACKET]" in user_input:
            return False
        lower = user_input.lower().strip()
        if lower in {"continue", "resume", "aage", "chalu rakho"}:
            return True
        return bool(
            len(user_input.split()) >= 8
            or re.search(r"\b(add|implement|build|fix|debug|verify|test|compile|feature|advanced|agent|code|error|traceback|continue|mission)\b", lower)
            or re.search(r"[\w./-]+\.py\b", user_input)
        )

    def _mission_id(self, goal: str) -> str:
        import hashlib
        return f"M-{time.strftime('%Y%m%d-%H%M%S')}-{hashlib.sha1(goal.encode('utf-8')).hexdigest()[:8]}"

    def active_mission(self) -> dict[str, Any] | None:
        active_id = self.state.get("active_mission")
        for mission in reversed(self.state.get("missions", [])):
            if mission.get("id") == active_id and mission.get("status") not in {"closed", "failed"}:
                return mission
        return None

    def create_or_get_mission(self, goal: str) -> Mission:
        active = self.active_mission()
        if active:
            return Mission(**active)
        mid = self._mission_id(goal)
        mission_dir = self.mission_root / mid
        mission_dir.mkdir(parents=True, exist_ok=True)
        mission = Mission(mid, goal[:800], "created", time.time(), str(mission_dir))
        self.state.setdefault("missions", []).append(asdict(mission))
        self.state["active_mission"] = mid
        self._save_state()
        self._write_json(mission_dir / "mission.json", asdict(mission))
        self._write_json(mission_dir / "graph.json", self.build_graph(mission))
        return mission

    def build_graph(self, mission: Mission) -> dict[str, Any]:
        nodes = [
            MissionNode("n1", "evidence", "pending", "Harvest project/repo evidence"),
            MissionNode("n2", "implementation", "pending", "Implement or guide required code change"),
            MissionNode("n3", "verification", "pending", "Run/plan verification commands"),
            MissionNode("n4", "repair", "pending", "Create repair objective if verification fails"),
            MissionNode("n5", "closure", "pending", "Write replay and close mission"),
        ]
        return {"mission_id": mission.id, "nodes": [asdict(n) for n in nodes], "edges": [["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n3", "n5"], ["n4", "n2"]]}

    def harvest_evidence(self, goal: str) -> dict[str, Any]:
        changed_files: list[str] = []
        verification: list[str] = []
        symbols: list[str] = []
        titan_dashboard = None
        atlas_dashboard = None

        if self.titan_fabric is not None:
            try:
                changed_files = self.titan_fabric.detect_changed_files()
                matrix = self.titan_fabric.build_verification_matrix(changed_files)
                verification = [m.command for m in matrix]
                titan_dashboard = self.titan_fabric.dashboard()
            except Exception:
                pass
        if self.atlas_reactor is not None:
            try:
                atlas_dashboard = self.atlas_reactor.dashboard()
            except Exception:
                pass
        if self.project_brain is not None:
            try:
                snap = getattr(self.project_brain, "snapshot", None) or self.project_brain.scan()
                for sym in getattr(snap, "symbols", [])[:500]:
                    file = str(sym.get("file", ""))
                    if any(Path(f).name == Path(file).name for f in changed_files):
                        symbols.append(f"{sym.get('kind')} {sym.get('name')} in {file}:{sym.get('line')}")
                    if len(symbols) >= 20:
                        break
            except Exception:
                pass
        evidence = {
            "goal": goal[:800],
            "changed_files": changed_files[:50],
            "verification_commands": verification[:12],
            "related_symbols": symbols[:20],
            "titan_dashboard": titan_dashboard,
            "atlas_dashboard": atlas_dashboard,
            "generated_at": time.time(),
        }
        return evidence

    def allowlisted_command(self, command: str) -> bool:
        command = command.strip()
        allowed_prefixes = (
            "python -m compileall ",
            "python3 -m compileall ",
            "python main.py --help",
            "python3 main.py --help",
            "git status --porcelain",
            "git diff --name-only",
        )
        if command in {"python main.py --help", "python3 main.py --help", "git status --porcelain", "git diff --name-only"}:
            return True
        return command.startswith(allowed_prefixes)

    def auto_runnable(self, commands: list[str]) -> list[str]:
        runnable = []
        for command in commands:
            if self.allowlisted_command(command) and ("pytest" not in command):
                runnable.append(command)
        return runnable[:3]

    def run_verification(self, commands: list[str]) -> list[VerificationResult]:
        results: list[VerificationResult] = []
        for command in self.auto_runnable(commands):
            start = time.perf_counter()
            proc = subprocess.run(command, cwd=self.root, shell=True, capture_output=True, text=True, timeout=120, check=False)
            duration = int((time.perf_counter() - start) * 1000)
            results.append(VerificationResult(command, proc.returncode, proc.stdout[-6000:], proc.stderr[-6000:], duration))
        return results

    def repair_objectives(self, results: list[VerificationResult]) -> list[dict[str, Any]]:
        repairs: list[dict[str, Any]] = []
        for result in results:
            if result.exit_code == 0:
                continue
            text = f"{result.stdout}\n{result.stderr}"
            match = re.search(r"File \"([^\"]+)\", line (\d+).*?(SyntaxError|IndentationError|NameError|ImportError|ModuleNotFoundError)", text, re.DOTALL)
            if match:
                repairs.append({
                    "repair_id": f"R-{int(time.time())}-{len(repairs)+1}",
                    "error_type": match.group(3),
                    "file": match.group(1),
                    "line": int(match.group(2)),
                    "objective": f"Inspect {match.group(1)} around line {match.group(2)} and fix {match.group(3)}.",
                    "verification": result.command,
                })
            else:
                repairs.append({
                    "repair_id": f"R-{int(time.time())}-{len(repairs)+1}",
                    "error_type": "CommandFailure",
                    "file": None,
                    "line": None,
                    "objective": f"Diagnose nonzero exit from `{result.command}` using captured stdout/stderr.",
                    "verification": result.command,
                })
        if repairs:
            self.state["last_repair"] = repairs[-1]
            self._save_state()
        return repairs

    def update_mission_status(self, mission: Mission, status: str) -> None:
        for item in self.state.get("missions", []):
            if item.get("id") == mission.id:
                item["status"] = status
                item["updated_at"] = time.time()
                break
        if status in {"closed", "failed"}:
            self.state["active_mission"] = None
        self._save_state()
        path = Path(mission.mission_dir) / "mission.json"
        data = asdict(mission)
        data["status"] = status
        data["updated_at"] = time.time()
        self._write_json(path, data)

    def build_packet(self, user_input: str) -> str:
        mission = self.create_or_get_mission(user_input)
        evidence = self.harvest_evidence(user_input)
        mission_dir = Path(mission.mission_dir)
        self.update_mission_status(mission, "evidence_ready")
        self._write_json(mission_dir / "evidence.json", evidence)
        results = self.run_verification(evidence.get("verification_commands", []))
        self._write_json(mission_dir / "command_results.json", [asdict(r) for r in results])
        repairs = self.repair_objectives(results)
        self._write_json(mission_dir / "repair_objectives.json", repairs)
        if repairs:
            self.update_mission_status(mission, "repair_needed")
        elif results and all(r.exit_code == 0 for r in results):
            self.update_mission_status(mission, "verified")
        self.write_replay(mission, evidence, results, repairs)

        lines = ["[FORGECORE MISSION PACKET]"]
        lines += [
            "Mission:",
            f"- id: {mission.id}",
            f"- status: {self.active_status(mission.id)}",
            f"- goal: {mission.goal}",
            f"- artifacts: {mission.mission_dir}",
            "Graph:",
            "- evidence -> implementation -> verification -> repair/closure",
            "Evidence:",
            f"- changed files: {', '.join(evidence.get('changed_files', [])[:12]) or 'none detected'}",
            f"- related symbols: {len(evidence.get('related_symbols', []))}",
            "Verification:",
        ]
        commands = evidence.get("verification_commands", [])
        if commands:
            lines.extend(f"- planned: `{c}`" for c in commands[:8])
        else:
            lines.append("- no verification commands generated")
        if results:
            lines.append("Verification Results:")
            lines.extend(f"- `{r.command}` exit={r.exit_code} duration_ms={r.duration_ms}" for r in results)
        else:
            lines.append("Verification Results:")
            lines.append("- no auto-runnable verification command executed")
        lines.append("Active Repair:")
        if repairs:
            lines.extend(f"- {r['objective']} | verify: `{r['verification']}`" for r in repairs)
        else:
            lines.append("- none")
        lines += [
            "Mission Runtime Rule:",
            "- if verification failed, fix active repair objective first, then rerun verification",
            "- if verification passed, close with replay/outcome",
            "[/FORGECORE MISSION PACKET]",
        ]
        return "\n".join(lines)

    def active_status(self, mission_id: str) -> str:
        for mission in self.state.get("missions", []):
            if mission.get("id") == mission_id:
                return str(mission.get("status", "unknown"))
        return "unknown"

    def enrich_prompt(self, user_input: str) -> str:
        if not self.should_activate(user_input):
            return user_input
        try:
            return f"{user_input}\n\n{self.build_packet(user_input)}"
        except Exception:
            return user_input

    def learn_turn(self, user_input: str, final: str, success: bool = True) -> None:
        active = self.active_mission()
        if not active:
            return
        mission = Mission(**active)
        mission_dir = Path(mission.mission_dir)
        outcome = {"ts": time.time(), "goal": user_input[:800], "success": success, "assistant_summary": (final or "")[:3000]}
        self._write_json(mission_dir / "outcome.json", outcome)
        if success and self.active_status(mission.id) == "verified":
            self.update_mission_status(mission, "closed")
        elif not success:
            self.update_mission_status(mission, "failed")
        self.write_replay(mission, self._read_json(mission_dir / "evidence.json", {}), self._read_results(mission_dir / "command_results.json"), self._read_json(mission_dir / "repair_objectives.json", []), outcome=outcome)

    def write_replay(self, mission: Mission, evidence: dict[str, Any], results: list[VerificationResult], repairs: list[dict[str, Any]], outcome: dict[str, Any] | None = None) -> None:
        lines = ["# Mission Replay", "", f"Mission: {mission.id}", f"Goal: {mission.goal}", f"Status: {self.active_status(mission.id)}", "", "## Timeline", "- mission created/loaded", "- evidence harvested", "- verification commands generated"]
        if results:
            lines.append("- auto-runnable verification executed")
        if repairs:
            lines.append("- repair objective generated")
        if outcome:
            lines.append("- assistant outcome learned")
        lines += ["", "## Evidence", f"- changed files: {', '.join(evidence.get('changed_files', [])[:20]) or 'none'}", f"- related symbols: {len(evidence.get('related_symbols', []))}", "", "## Verification Results"]
        if results:
            for r in results:
                lines.append(f"- `{r.command}` exit={r.exit_code} duration_ms={r.duration_ms}")
        else:
            lines.append("- none executed")
        lines += ["", "## Repairs"]
        if repairs:
            lines.extend(f"- {r.get('objective')}" for r in repairs)
        else:
            lines.append("- none")
        if outcome:
            lines += ["", "## Outcome", f"- success: {outcome.get('success')}", f"- summary: {outcome.get('assistant_summary', '')[:1000]}"]
        (Path(mission.mission_dir) / "replay.md").write_text("\n".join(lines), encoding="utf-8")

    def dashboard(self) -> str:
        missions = self.state.get("missions", [])
        active = self.active_mission()
        lines = ["FORGECORE Dashboard", f"- missions: {len(missions)}", f"- active: {active.get('id') if active else 'none'}", f"- last repair: {self.state.get('last_repair') or 'none'}", f"- mission root: {self.mission_root}"]
        return "\n".join(lines)

    def _write_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _read_json(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _read_results(self, path: Path) -> list[VerificationResult]:
        raw = self._read_json(path, [])
        results = []
        for item in raw:
            try:
                results.append(VerificationResult(**item))
            except Exception:
                pass
        return results


def get_forgecore(root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None, titan_fabric: Any | None = None, atlas_reactor: Any | None = None) -> ForgeCore:
    return ForgeCore(root=root, project_brain=project_brain, omni_aion=omni_aion, titan_fabric=titan_fabric, atlas_reactor=atlas_reactor)
