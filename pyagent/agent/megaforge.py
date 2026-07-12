"""MEGAFORGE Enterprise v1: 30-feature large-project software factory layer.

MEGAFORGE turns large/complex product goals into real enterprise planning
artifacts: requirements, stories, architecture, services, APIs, data model,
events, roadmap, tests, CI/CD, deployment, observability, performance, release,
and dashboard files. It is deterministic, local, persistent, and auto-integrable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MEGAFORGE_ROOT = Path(".pyagent_megaforge")
STATE_FILE = MEGAFORGE_ROOT / "megaforge_state.json"


@dataclass(frozen=True)
class MegaProject:
    id: str
    goal: str
    status: str
    created_at: float
    project_dir: str


class MegaForge:
    """Enterprise software factory artifact generator."""

    def __init__(self, root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None, titan_fabric: Any | None = None, atlas_reactor: Any | None = None, forgecore: Any | None = None) -> None:
        self.root = Path(root).resolve()
        self.project_brain = project_brain
        self.omni_aion = omni_aion
        self.titan_fabric = titan_fabric
        self.atlas_reactor = atlas_reactor
        self.forgecore = forgecore
        self.megaforge_root = self.root / MEGAFORGE_ROOT
        self.state_file = self.root / STATE_FILE
        self.megaforge_root.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"projects": [], "last_project": None, "outcomes": []}

    def _save_state(self) -> None:
        self.megaforge_root.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def should_activate(self, user_input: str) -> bool:
        if "[MEGAFORGE ENTERPRISE PACKET]" in user_input:
            return False
        lower = user_input.lower()
        big_words = ("enterprise", "large", "big", "huge", "scale", "google", "amazon", "microsoft", "30 features", "project", "software", "platform", "saas", "microservice", "architecture", "roadmap")
        return bool(len(user_input.split()) >= 14 or any(w in lower for w in big_words))

    def _project_id(self, goal: str) -> str:
        digest = hashlib.sha1(goal.encode("utf-8")).hexdigest()[:8]
        return f"MF-{time.strftime('%Y%m%d-%H%M%S')}-{digest}"

    def create_project(self, goal: str) -> MegaProject:
        pid = self._project_id(goal)
        project_dir = self.megaforge_root / pid
        project_dir.mkdir(parents=True, exist_ok=True)
        project = MegaProject(pid, goal[:1200], "planned", time.time(), str(project_dir))
        self.state.setdefault("projects", []).append(asdict(project))
        self.state["last_project"] = pid
        self._save_state()
        self._write_json(project_dir / "project.json", asdict(project))
        return project

    def analyze_goal(self, goal: str) -> dict[str, Any]:
        lower = goal.lower()
        domains = []
        for key in ("ai", "agent", "terminal", "saas", "ecommerce", "fintech", "health", "education", "devops", "analytics"):
            if key in lower:
                domains.append(key)
        if not domains:
            domains = ["software_platform"]
        scale = "enterprise" if any(w in lower for w in ("enterprise", "large", "big", "google", "amazon", "microsoft", "scale")) else "standard"
        style = "microservices" if any(w in lower for w in ("microservice", "large", "enterprise", "scale")) else "modular_monolith_first"
        return {"domains": domains, "scale": scale, "architecture_style": style, "goal_words": len(goal.split()), "generated_at": time.time()}

    def workspace_context(self) -> dict[str, Any]:
        ctx: dict[str, Any] = {"entrypoints": [], "recent_files": [], "changed_files": []}
        if self.project_brain is not None:
            try:
                snap = getattr(self.project_brain, "snapshot", None) or self.project_brain.scan()
                ctx["entrypoints"] = getattr(snap, "entrypoints", [])[:8]
                ctx["recent_files"] = getattr(snap, "recent_files", [])[:12]
            except Exception:
                pass
        if self.titan_fabric is not None:
            try:
                ctx["changed_files"] = self.titan_fabric.detect_changed_files()[:12]
            except Exception:
                pass
        return ctx

    def generate_all_artifacts(self, project: MegaProject) -> dict[str, Any]:
        analysis = self.analyze_goal(project.goal)
        ctx = self.workspace_context()
        project_dir = Path(project.project_dir)
        artifacts = {
            "requirements.md": self.requirements(project, analysis),
            "user_stories.md": self.user_stories(project, analysis),
            "acceptance_criteria.md": self.acceptance_criteria(project, analysis),
            "domain_model.md": self.domain_model(project, analysis),
            "architecture.md": self.architecture(project, analysis, ctx),
            "services.md": self.services(project, analysis),
            "api_plan.md": self.api_plan(project, analysis),
            "data_model.md": self.data_model(project, analysis),
            "events.md": self.events(project, analysis),
            "integrations.md": self.integrations(project, analysis),
            "roadmap.md": self.roadmap(project, analysis),
            "work_breakdown.md": self.work_breakdown(project, analysis),
            "team_roles.md": self.team_roles(project, analysis),
            "testing.md": self.testing(project, analysis),
            "cicd.md": self.cicd(project, analysis),
            "deployment.md": self.deployment(project, analysis),
            "observability.md": self.observability(project, analysis),
            "performance.md": self.performance(project, analysis),
            "scalability.md": self.scalability(project, analysis),
            "data_migration.md": self.data_migration(project, analysis),
            "documentation.md": self.documentation(project, analysis),
            "sdk_clients.md": self.sdk_clients(project, analysis),
            "feature_flags.md": self.feature_flags(project, analysis),
            "configuration_matrix.md": self.configuration_matrix(project, analysis),
            "dependency_map.md": self.dependency_map(project, analysis),
            "release_plan.md": self.release_plan(project, analysis),
            "rollback_plan.md": self.rollback_plan(project, analysis),
            "cost_resources.md": self.cost_resources(project, analysis),
            "enterprise_dashboard.md": self.enterprise_dashboard_md(project, analysis, ctx),
            "replay.md": self.replay(project, analysis),
        }
        for name, content in artifacts.items():
            (project_dir / name).write_text(content, encoding="utf-8")
        dashboard = {"project": asdict(project), "analysis": analysis, "workspace": ctx, "artifacts": sorted(artifacts), "feature_count": 30}
        self._write_json(project_dir / "dashboard.json", dashboard)
        packet = self.packet(project, analysis, ctx, sorted(artifacts))
        (project_dir / "megaforge_packet.md").write_text(packet, encoding="utf-8")
        return dashboard

    def requirements(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._md("Requirements", p, ["Functional requirements", "Non-functional requirements", "Actors/personas", "Core workflows", "Edge cases", "Enterprise constraints"], a)

    def user_stories(self, p: MegaProject, a: dict[str, Any]) -> str:
        stories = [f"- As a {role}, I want {capability}, so that {outcome}." for role, capability, outcome in [
            ("operator", "a guided command/workflow surface", "large tasks are repeatable"),
            ("developer", "clear service and API boundaries", "implementation is parallelizable"),
            ("maintainer", "artifact-backed decisions", "future changes are explainable"),
            ("release owner", "verification and rollback plans", "deployments are controlled"),
        ]]
        return self._section("User Stories", p, stories)

    def acceptance_criteria(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Acceptance Criteria", p, ["- Every major feature has measurable done criteria.", "- Every service/API has owner, inputs, outputs, and verification.", "- Every release has test, deployment, and rollback artifacts.", "- Large workflows can be resumed from generated artifacts."])

    def domain_model(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Domain Model", p, ["- Project", "- Tenant/Workspace", "- User/Role", "- Service", "- Workflow", "- Artifact", "- Release", "- Event", "- Metric", "- Integration"])

    def architecture(self, p: MegaProject, a: dict[str, Any], ctx: dict[str, Any]) -> str:
        return self._section("Architecture", p, [f"- Recommended style: {a['architecture_style']}", "- Start with clear module/service boundaries.", "- Use API contracts between bounded contexts.", "- Keep workflow orchestration separate from domain logic.", f"- Existing entrypoints: {', '.join(ctx.get('entrypoints', [])) or 'none detected'}"])

    def services(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Service Boundary Planner", p, ["- Identity/Access service", "- Project/workspace service", "- Workflow orchestration service", "- Artifact service", "- Notification/event service", "- Reporting/analytics service", "- Integration gateway"])

    def api_plan(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("API Surface Planner", p, ["- REST endpoints for core resources", "- OpenAPI spec per service", "- Pagination/filtering conventions", "- Idempotent mutation operations", "- Webhook/event subscription surface", "- SDK generation targets"])

    def data_model(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Database Schema Planner", p, ["- users(id, email, status)", "- workspaces(id, name, plan)", "- projects(id, workspace_id, goal, status)", "- workflows(id, project_id, state)", "- artifacts(id, project_id, path, kind)", "- releases(id, project_id, version, status)", "- events(id, aggregate_id, type, payload, ts)"])

    def events(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Event Flow Planner", p, ["- ProjectCreated", "- RequirementsApproved", "- ServicePlanned", "- ImplementationStarted", "- VerificationCompleted", "- ReleasePrepared", "- RollbackRequested"])

    def integrations(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Integration Map", p, ["- Git provider", "- CI provider", "- Artifact storage", "- Issue tracker", "- Notification channel", "- Observability backend", "- Cloud/runtime platform"])

    def roadmap(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Milestone Roadmap", p, ["- M1: foundation architecture + data model", "- M2: core workflow/API implementation", "- M3: verification + artifacts", "- M4: deployment + observability", "- M5: enterprise hardening + scale improvements"])

    def work_breakdown(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Work Breakdown Structure", p, ["- Requirements epic", "- Architecture epic", "- Backend epic", "- Frontend/CLI epic", "- Data epic", "- QA epic", "- DevOps epic", "- Documentation epic"])

    def team_roles(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Team Role Split", p, ["- Product owner", "- Staff architect", "- Backend lead", "- Frontend/UX lead", "- QA lead", "- Platform/DevOps lead", "- Technical writer", "- Release manager"])

    def testing(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Test Strategy Planner", p, ["- Unit tests per module", "- Contract tests per API", "- Integration tests per workflow", "- CLI smoke tests", "- Regression test suite", "- Release verification checklist"])

    def cicd(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("CI/CD Plan Generator", p, ["- install dependencies", "- lint/format checks", "- compile/type checks", "- unit + integration tests", "- build artifacts", "- deploy to staging", "- promote to production with approval"])

    def deployment(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Deployment Topology Planner", p, ["- local dev profile", "- staging environment", "- production environment", "- worker/runtime separation", "- config per environment", "- artifact retention"])

    def observability(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Observability Plan", p, ["- structured logs", "- request/workflow traces", "- service metrics", "- error dashboards", "- release health dashboard", "- SLO-style latency/error tracking"])

    def performance(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Performance Plan", p, ["- cache read-heavy views", "- async long-running workflows", "- batch artifact processing", "- index high-cardinality queries", "- measure p95/p99 latencies"])

    def scalability(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Scalability Plan", p, ["- partition by workspace/project", "- queue background jobs", "- horizontal service scaling", "- artifact storage separation", "- read replicas for reporting"])

    def data_migration(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Data Migration Plan", p, ["- versioned migrations", "- backward-compatible schema changes", "- seed data strategy", "- migration dry run", "- rollback migration notes"])

    def documentation(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Documentation Plan", p, ["- README", "- architecture decision records", "- API docs", "- runbooks", "- onboarding guide", "- release notes"])

    def sdk_clients(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("SDK/Client Plan", p, ["- Python client", "- TypeScript client", "- CLI commands", "- OpenAPI-generated SDK", "- examples per workflow"])

    def feature_flags(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Feature Flag Plan", p, ["- per-workspace enablement", "- staged rollout", "- kill switch", "- experiment flags", "- migration flags"])

    def configuration_matrix(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Configuration Matrix", p, ["- development", "- test", "- staging", "- production", "- local overrides", "- environment variables", "- config validation"])

    def dependency_map(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Dependency Map", p, ["- runtime dependencies", "- build dependencies", "- service dependencies", "- external integrations", "- data stores", "- queues", "- artifact storage"])

    def release_plan(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Release Plan", p, ["- version branch", "- changelog", "- verification report", "- staging signoff", "- production rollout", "- post-release monitoring"])

    def rollback_plan(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Rollback Plan", p, ["- keep previous artifact", "- database rollback notes", "- feature flag disable path", "- traffic rollback", "- incident timeline capture"])

    def cost_resources(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("Cost/Resource Estimate", p, ["- compute: service + worker pools", "- storage: artifacts + database", "- network: API + event traffic", "- people: product/eng/QA/platform/docs", "- timeline: milestone-based"])

    def enterprise_dashboard_md(self, p: MegaProject, a: dict[str, Any], ctx: dict[str, Any]) -> str:
        return self._section("Enterprise Dashboard", p, [f"- feature artifacts: 30", f"- scale: {a['scale']}", f"- architecture: {a['architecture_style']}", f"- changed files: {', '.join(ctx.get('changed_files', [])) or 'none'}", f"- recent files: {', '.join(ctx.get('recent_files', [])[:6]) or 'none'}"])

    def replay(self, p: MegaProject, a: dict[str, Any]) -> str:
        return self._section("MegaForge Replay", p, ["- project analyzed", "- 30 enterprise artifacts generated", "- dashboard.json written", "- packet prepared for agent context"])

    def packet(self, p: MegaProject, a: dict[str, Any], ctx: dict[str, Any], artifacts: list[str]) -> str:
        lines = ["[MEGAFORGE ENTERPRISE PACKET]", "Mega Project:", f"- id: {p.id}", f"- status: {p.status}", f"- dir: {p.project_dir}", f"- scale: {a['scale']}", f"- architecture: {a['architecture_style']}", "30 Feature Artifacts:"]
        lines.extend(f"- {x}" for x in artifacts)
        lines += ["Workspace Signals:", f"- entrypoints: {', '.join(ctx.get('entrypoints', [])) or 'none'}", f"- changed: {', '.join(ctx.get('changed_files', [])) or 'none'}", "Enterprise Next Actions:", "1. review requirements.md and architecture.md", "2. implement service/data/API boundaries", "3. create tests from testing.md", "4. wire CI/CD and deployment plans", "5. use release/rollback docs for closure", "[/MEGAFORGE ENTERPRISE PACKET]"]
        return "\n".join(lines)

    def enrich_prompt(self, user_input: str) -> str:
        if not self.should_activate(user_input):
            return user_input
        try:
            project = self.create_project(user_input)
            self.generate_all_artifacts(project)
            packet = (Path(project.project_dir) / "megaforge_packet.md").read_text(encoding="utf-8")
            return f"{user_input}\n\n{packet}"
        except Exception:
            return user_input

    def learn_turn(self, user_input: str, final: str, success: bool = True) -> None:
        outcome = {"ts": time.time(), "input": user_input[:500], "success": success, "summary": (final or "")[:1500], "project": self.state.get("last_project")}
        self.state.setdefault("outcomes", []).append(outcome)
        self._save_state()
        pid = self.state.get("last_project")
        if pid:
            project_dir = self.megaforge_root / pid
            project_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(project_dir / "outcome.json", outcome)

    def dashboard(self) -> str:
        projects = self.state.get("projects", [])
        return "\n".join(["MEGAFORGE Dashboard", f"- projects: {len(projects)}", f"- last_project: {self.state.get('last_project') or 'none'}", f"- root: {self.megaforge_root}", "- features per project: 30"])

    def _md(self, title: str, p: MegaProject, headings: list[str], a: dict[str, Any]) -> str:
        lines = [f"# {title}", "", f"Project: {p.id}", f"Goal: {p.goal}", f"Scale: {a['scale']}", ""]
        for h in headings:
            lines.extend([f"## {h}", f"- Define {h.lower()} for: {p.goal[:160]}", ""])
        return "\n".join(lines)

    def _section(self, title: str, p: MegaProject, bullets: list[str]) -> str:
        return "\n".join([f"# {title}", "", f"Project: {p.id}", f"Goal: {p.goal}", "", *bullets, ""])

    def _write_json(self, path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def get_megaforge(root: str | Path = ".", project_brain: Any | None = None, omni_aion: Any | None = None, titan_fabric: Any | None = None, atlas_reactor: Any | None = None, forgecore: Any | None = None) -> MegaForge:
    return MegaForge(root=root, project_brain=project_brain, omni_aion=omni_aion, titan_fabric=titan_fabric, atlas_reactor=atlas_reactor, forgecore=forgecore)
