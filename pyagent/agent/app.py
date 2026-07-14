"""Application shell: wires config, provider, tools, memory, commands and UI."""

from __future__ import annotations

import re
import sys

from .branching import BranchManager
from .commands import build_command_registry
from .config import Config
from .core import Agent
from .memory import Conversation
from .plugins.loader import load_plugins
from .profiler import get_profiler
from .providers import get_provider
from .providers.factory import ProviderError
from .recovery import Checkpoint, get_recovery_manager
from .telemetry import get_telemetry

# Enterprise subsystems (v2).
from .token_counter import get_token_counter
from .tools import build_default_registry
from .ui import UI
from .utils.logging import get_logger

log = get_logger("agent.app")


class App:
    def __init__(self, animations: bool = True) -> None:
        import time

        self.config = Config.load()
        self.ui = UI(animations=animations)
        self.registry = build_default_registry()
        self.commands = build_command_registry()
        self.conversation = Conversation(self.config.system_prompt)
        self.agent: Agent | None = None
        self.enterprise_autostart = None
        self._enterprise_autostart_ran = False
        self.goal_mode = False
        self._goal_config_backup: dict | None = None
        self._started_at = time.time()
        self.project_brain = None
        self.omni_aion = None
        self.titan_fabric = None
        self.atlas_reactor = None
        self.forgecore = None
        self.megaforge = None
        self._project_brain_snapshot_at = 0.0
        self._init_project_brain()
        self._init_omni_aion()
        self._init_titan_fabric()
        self._init_atlas_reactor()
        self._init_forgecore()
        self._init_megaforge()
        try:
            from .enterprise_suite import activate_enterprise_mode

            self.enterprise_summary = activate_enterprise_mode(self)
        except Exception as exc:  # noqa: BLE001
            self.enterprise_summary = {"features": 0, "error": str(exc)}
        self.ui.set_command_source(self._command_items)
        for tool in load_plugins():
            self.registry.register(tool)
            log.info("Loaded plugin tool: %s", tool.name)
        self.token_counter = get_token_counter()
        self.branch_manager = BranchManager(list(self.conversation.messages))
        self.telemetry = get_telemetry()
        self.profiler = get_profiler()
        if getattr(self.config, "telemetry_enabled", False):
            self.telemetry.enable()
        if getattr(self.config, "profiler_enabled", False):
            self.profiler.enable()

    # ------------------------------------------------------------------
    def _init_project_brain(self) -> None:
        try:
            from .project_brain import ProjectBrain
            self.project_brain = ProjectBrain(".")
            self.project_brain.scan()
            import time as _time
            self._project_brain_snapshot_at = _time.time()
            log.info("Project Brain initialized")
        except Exception as exc:  # noqa: BLE001
            self.project_brain = None
            log.debug("Project Brain init skipped: %s", exc)

    def _init_omni_aion(self) -> None:
        try:
            from .omni_aion import OmniAION
            self.omni_aion = OmniAION(".", project_brain=self.project_brain)
            self.omni_aion.remember("startup", "OMNI-AION initialized", {"project_brain": self.project_brain is not None})
            log.info("OMNI-AION initialized")
        except Exception as exc:  # noqa: BLE001
            self.omni_aion = None
            log.debug("OMNI-AION init skipped: %s", exc)

    def _init_titan_fabric(self) -> None:
        try:
            from .titan_fabric import TitanFabric
            self.titan_fabric = TitanFabric(".", project_brain=self.project_brain, omni_aion=self.omni_aion)
            log.info("TITAN Fabric initialized")
        except Exception as exc:  # noqa: BLE001
            self.titan_fabric = None
            log.debug("TITAN Fabric init skipped: %s", exc)

    def _init_atlas_reactor(self) -> None:
        try:
            from .atlas_reactor import AtlasReactor
            self.atlas_reactor = AtlasReactor(".", project_brain=self.project_brain, omni_aion=self.omni_aion, titan_fabric=self.titan_fabric)
            log.info("ATLAS Reactor initialized")
        except Exception as exc:  # noqa: BLE001
            self.atlas_reactor = None
            log.debug("ATLAS Reactor init skipped: %s", exc)

    def _init_forgecore(self) -> None:
        try:
            from .forgecore import ForgeCore
            self.forgecore = ForgeCore(".", project_brain=self.project_brain, omni_aion=self.omni_aion, titan_fabric=self.titan_fabric, atlas_reactor=self.atlas_reactor)
            log.info("FORGECORE initialized")
        except Exception as exc:  # noqa: BLE001
            self.forgecore = None
            log.debug("FORGECORE init skipped: %s", exc)

    def _init_megaforge(self) -> None:
        try:
            from .megaforge import MegaForge
            self.megaforge = MegaForge(".", project_brain=self.project_brain, omni_aion=self.omni_aion, titan_fabric=self.titan_fabric, atlas_reactor=self.atlas_reactor, forgecore=self.forgecore)
            log.info("MEGAFORGE initialized")
        except Exception as exc:  # noqa: BLE001
            self.megaforge = None
            log.debug("MEGAFORGE init skipped: %s", exc)

    def _project_brain_context(self, user_input: str) -> str:
        if self.project_brain is None:
            return user_input
        text = user_input or ""
        lower = text.lower()
        looks_relevant = bool(
            "traceback" in lower
            or 'file "' in lower
            or re.search(r"\b(error|exception|bug|crash|fix|failing|failed|test|pytest|implement|add|code|file|function|class|module|import|cli|api)\b", lower)
            or re.search(r"[\w./-]+\.py\b", text)
        )
        if not looks_relevant:
            return user_input
        try:
            import time as _time
            if _time.time() - self._project_brain_snapshot_at > 30:
                self.project_brain.scan()
                self._project_brain_snapshot_at = _time.time()
            parts = ["[AUTO PROJECT BRAIN CONTEXT]", self.project_brain.render_radar()]
            if "traceback" in lower or 'file "' in lower:
                parts.append(self.project_brain.link_error(text))
            parts.append(self.project_brain.suggest_next_actions(goal=text, error_text=text if ('file "' in lower or "traceback" in lower) else ""))
            parts.append("[/AUTO PROJECT BRAIN CONTEXT]")
            self.project_brain.remember_event("turn", "auto context injected", {"input_preview": text[:300]})
            return f"{text}\n\n" + "\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            log.debug("Project Brain context injection skipped: %s", exc)
            return user_input

    def _omni_aion_context(self, user_input: str) -> str:
        if self.omni_aion is None:
            return user_input
        try:
            return self.omni_aion.enrich_prompt(user_input)
        except Exception as exc:  # noqa: BLE001
            log.debug("OMNI-AION enrichment skipped: %s", exc)
            return user_input

    def _titan_fabric_context(self, user_input: str) -> str:
        if self.titan_fabric is None:
            return user_input
        try:
            return self.titan_fabric.enrich_prompt(user_input)
        except Exception as exc:  # noqa: BLE001
            log.debug("TITAN Fabric enrichment skipped: %s", exc)
            return user_input

    def _atlas_reactor_context(self, user_input: str) -> str:
        if self.atlas_reactor is None:
            return user_input
        try:
            return self.atlas_reactor.enrich_prompt(user_input)
        except Exception as exc:  # noqa: BLE001
            log.debug("ATLAS Reactor enrichment skipped: %s", exc)
            return user_input

    def _forgecore_context(self, user_input: str) -> str:
        if self.forgecore is None:
            return user_input
        try:
            return self.forgecore.enrich_prompt(user_input)
        except Exception as exc:  # noqa: BLE001
            log.debug("FORGECORE enrichment skipped: %s", exc)
            return user_input

    def _megaforge_context(self, user_input: str) -> str:
        if self.megaforge is None:
            return user_input
        try:
            return self.megaforge.enrich_prompt(user_input)
        except Exception as exc:  # noqa: BLE001
            log.debug("MEGAFORGE enrichment skipped: %s", exc)
            return user_input

    def _enterprise_auto_context(self, user_input: str) -> str:
        """Inject autostart RAG/KG/security context automatically.

        Because startup now indexes and analyses the project automatically, the
        model gets relevant local context without the user needing `/kg`, `/rag`,
        `/sast`, or `/metrics` first.  The injected packet is compact and only
        includes local, precomputed information.
        """
        snapshot = getattr(self, "enterprise_autostart", None)
        if snapshot is None:
            return user_input
        try:
            parts = ["[ENTERPRISE AUTOSTART CONTEXT]"]
            metrics = snapshot.context.get("code metrics", {})
            sast = snapshot.context.get("sast scan", {})
            kg = snapshot.context.get("knowledge graph", {})
            rag = snapshot.context.get("rag index", {})
            if metrics:
                parts.append(
                    "Code metrics: "
                    f"files={metrics.get('files_scanned', 0)}, "
                    f"lines={metrics.get('total_lines', 0)}, "
                    f"functions={metrics.get('functions', 0)}, "
                    f"classes={metrics.get('classes', 0)}, "
                    f"max_complexity={metrics.get('max_complexity', 0)}"
                )
                suggestions = metrics.get("suggestions") or []
                if suggestions:
                    parts.append("Refactor hints: " + "; ".join(str(s) for s in suggestions[:3]))
            if sast:
                parts.append(
                    "Security scan: "
                    f"findings={sast.get('findings', 0)}, "
                    f"critical={sast.get('critical', 0)}, high={sast.get('high', 0)}, "
                    f"medium={sast.get('medium', 0)}, low={sast.get('low', 0)}"
                )
            if kg:
                parts.append(f"Knowledge graph: nodes={kg.get('nodes', 0)}, edges={kg.get('edges', 0)}")
            if rag:
                parts.append(f"RAG index: chunks={rag.get('documents', 0)}, sources={rag.get('sources', 0)}")
            hyper = snapshot.context.get("hyper suite", {})
            if hyper:
                parts.append(
                    "Hyper suite: "
                    f"features={hyper.get('features', 0)}, files={hyper.get('files', 0)}, "
                    f"functions={hyper.get('functions', 0)}, classes={hyper.get('classes', 0)}, "
                    f"tests={hyper.get('tests', 0)}, docs={hyper.get('docs', 0)}"
                )
                recs = hyper.get("recommendations") or []
                if recs:
                    parts.append("Hyper recommendations: " + "; ".join(str(r) for r in recs[:3]))
                hyper_ctx = hyper.get("context")
                if hyper_ctx:
                    parts.append(str(hyper_ctx)[:1800])
            apex = snapshot.context.get("apex suite", {})
            if apex:
                parts.append(
                    "Apex suite: "
                    f"features={apex.get('features', 0)}, files={apex.get('files_scanned', 0)}, "
                    f"symbols={apex.get('symbols', 0)}, fingerprint={apex.get('fingerprint', '')}"
                )
                hot = apex.get("hot_files") or []
                if hot:
                    parts.append("Apex hot files: " + "; ".join(f"{p}({s})" for p, s in hot[:5]))
                backlog = apex.get("backlog") or []
                if backlog:
                    parts.append("Apex backlog: " + "; ".join(str(item) for item in backlog[:4]))
                apex_ctx = apex.get("context")
                if apex_ctx:
                    parts.append(str(apex_ctx)[:2200])
            omega = snapshot.context.get("omega suite", {})
            if omega:
                parts.append(
                    "Omega suite: "
                    f"features={omega.get('features', 0)}, "
                    f"semantic_docs={omega.get('semantic', {}).get('stats', {}).get('documents', 0)}, "
                    f"symbols={omega.get('semantic', {}).get('stats', {}).get('symbols', 0)}, "
                    f"refactor_findings={omega.get('refactor', {}).get('stats', {}).get('findings', 0)}"
                )
                recs = omega.get("recommendations") or []
                if recs:
                    parts.append("Omega recommendations: " + "; ".join(str(r) for r in recs[:5]))
                omega_ctx = omega.get("context")
                if omega_ctx:
                    parts.append(str(omega_ctx)[:2600])
            nova = snapshot.context.get("nova suite", {})
            if nova:
                parts.append(
                    "Nova suite: "
                    f"features={nova.get('features', 0)}, "
                    f"symbols={nova.get('symbols', {}).get('stats', {}).get('symbols', 0)}, "
                    f"impact_items={nova.get('impact', {}).get('stats', {}).get('impacts', 0)}, "
                    f"quality={nova.get('quality', {}).get('stats', {}).get('overall_score', 0)}"
                )
                recs = nova.get("recommendations") or []
                if recs:
                    parts.append("Nova recommendations: " + "; ".join(str(r) for r in recs[:5]))
                nova_ctx = nova.get("context")
                if nova_ctx:
                    parts.append(str(nova_ctx)[:3200])
            zenith = snapshot.context.get("zenith suite", {})
            if zenith:
                parts.append(
                    "Zenith suite: "
                    f"features={zenith.get('features', 0)}, "
                    f"definitions={zenith.get('lsp', {}).get('stats', {}).get('definitions', 0)}, "
                    f"graph_nodes={zenith.get('graph', {}).get('stats', {}).get('nodes', 0)}, "
                    f"metrics_total={zenith.get('metrics', {}).get('stats', {}).get('total', 0)}"
                )
                zctx = zenith.get("context")
                if zctx:
                    parts.append(str(zctx)[:3800])
            quantum = snapshot.context.get("quantum suite", {})
            if quantum:
                parts.append(
                    "Quantum suite: "
                    f"features={quantum.get('features', 0)}, "
                    f"trace_nodes={quantum.get('trace', {}).get('stats', {}).get('nodes', 0)}, "
                    f"risk_items={quantum.get('risk', {}).get('stats', {}).get('items', 0)}, "
                    f"contracts={quantum.get('contracts', {}).get('stats', {}).get('contracts', 0)}"
                )
                qrecs = quantum.get("recommendations") or []
                if qrecs:
                    parts.append("Quantum recommendations: " + "; ".join(str(r) for r in qrecs[:5]))
                qctx = quantum.get("context")
                if qctx:
                    parts.append(str(qctx)[:4200])
            try:
                from .rag_v2 import get_vector_store

                store = get_vector_store()
                if store.documents:
                    ctx = store.answer_context(user_input, top_k=3)
                    if ctx and not ctx.startswith("No relevant context"):
                        parts.append("Relevant indexed codebase snippets:\n" + ctx[:2500])
            except Exception:  # noqa: BLE001
                pass
            parts.append("[/ENTERPRISE AUTOSTART CONTEXT]")
            return f"{user_input}\n\n" + "\n".join(parts)
        except Exception as exc:  # noqa: BLE001
            log.debug("Enterprise autostart context skipped: %s", exc)
            return user_input

    def _enrich_user_input(self, user_input: str) -> str:
        enriched = self._project_brain_context(user_input)
        enriched = self._omni_aion_context(enriched)
        enriched = self._titan_fabric_context(enriched)
        enriched = self._atlas_reactor_context(enriched)
        enriched = self._forgecore_context(enriched)
        enriched = self._megaforge_context(enriched)
        enriched = self._enterprise_auto_context(enriched)
        return enriched

    def _remember_project_turn(self, user_input: str, final: str, success: bool = True) -> None:
        if self.project_brain is not None:
            try:
                self.project_brain.remember_event("assistant_turn", "turn completed" if success else "turn failed", {"user_preview": user_input[:300], "assistant_preview": (final or "")[:500], "success": success})
            except Exception as exc:  # noqa: BLE001
                log.debug("Project Brain turn memory skipped: %s", exc)
        if self.omni_aion is not None:
            try:
                self.omni_aion.learn_outcome(user_input, final, success=success)
            except Exception as exc:  # noqa: BLE001
                log.debug("OMNI-AION outcome learning skipped: %s", exc)
        if self.titan_fabric is not None:
            try:
                self.titan_fabric.learn_turn(user_input, final, success=success)
            except Exception as exc:  # noqa: BLE001
                log.debug("TITAN Fabric outcome learning skipped: %s", exc)
        if self.atlas_reactor is not None:
            try:
                self.atlas_reactor.learn_turn(user_input, final, success=success)
            except Exception as exc:  # noqa: BLE001
                log.debug("ATLAS Reactor outcome learning skipped: %s", exc)
        if self.forgecore is not None:
            try:
                self.forgecore.learn_turn(user_input, final, success=success)
            except Exception as exc:  # noqa: BLE001
                log.debug("FORGECORE outcome learning skipped: %s", exc)
        if self.megaforge is not None:
            try:
                self.megaforge.learn_turn(user_input, final, success=success)
            except Exception as exc:  # noqa: BLE001
                log.debug("MEGAFORGE outcome learning skipped: %s", exc)

    def _command_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for cmd in sorted(self.commands.all(), key=lambda c: c.name):
            desc = (cmd.help or "").strip()
            items.append((cmd.name, desc))
            for alias in getattr(cmd, "aliases", ()):
                items.append((alias, f"alias of {cmd.name}"))
        return items

    def build_agent(self) -> bool:
        # Strict model ownership: if the selected model is known to belong to a
        # provider, always build that exact provider/model pair.  This prevents
        # a stale provider from answering with a different default model after
        # `/model agnes-2.5-pro`, `/model MiniMax-M2.7`, etc.
        if self.config.model:
            owner = self.config.provider_for_model(self.config.model)
            if owner and owner != self.config.provider:
                self.config.provider = owner
        try:
            provider = get_provider(self.config)
        except ProviderError as exc:
            self.ui.error(str(exc))
            self.ui.info("Tip: set the selected provider API key env var, or run `/provider ollama` for local mode.")
            return False
        self.agent = Agent(self.config, provider, self.registry, self.conversation)
        log.info("Agent ready: provider=%s model=%s", self.config.provider, self.config.resolved_model())
        return True

    # ------------------------------------------------------------------
    def _enter_goal_mode(self) -> None:
        from .effort import get_effort
        cfg = self.config
        effort = get_effort("godmode")
        self._goal_config_backup = {"effort": getattr(cfg, "effort", "normal"), "temperature": cfg.temperature, "max_tokens": cfg.max_tokens, "enable_tools": cfg.enable_tools, "auto_approve_tools": cfg.auto_approve_tools, "max_tool_iterations": cfg.max_tool_iterations}
        cfg.effort = effort.name
        cfg.temperature = effort.temperature
        cfg.max_tokens = max(cfg.max_tokens, 128000)
        cfg.enable_tools = True
        cfg.auto_approve_tools = True
        cfg.max_tool_iterations = max(cfg.max_tool_iterations, 40)
        self.goal_mode = True
        self.ui.goal_mode = True

    def _exit_goal_mode(self) -> None:
        if self._goal_config_backup is not None:
            for key, value in self._goal_config_backup.items():
                setattr(self.config, key, value)
            self._goal_config_backup = None
        self.goal_mode = False
        self.ui.goal_mode = False

    def _run_goal(self, goal_text: str) -> None:
        from .goal_mode import GoalMode
        enriched_goal = self._enrich_user_input(goal_text)
        run = GoalMode(self).run(enriched_goal)
        rounds = sum(1 for s in run.steps if s.kind == "execute")
        if run.cancelled:
            self.ui.warn(f"Goal Mode stopped by user after {rounds} round(s).")
        elif run.complete:
            self.ui.success(f"Goal achieved in {rounds} execution round(s).")
        else:
            self.ui.warn(f"Goal not fully verified after {rounds} round(s). Type a refined goal to continue.")
        self.ui.status_bar(self.config.provider, self.config.resolved_model(), self.conversation.token_estimate())

    def _run_enterprise_autostart(self, sync: bool = False) -> None:
        """Run safe local enterprise warmups once per process.

        Interactive startup must be fast: feature flags and registries are active
        immediately, while heavy repository indexing warms in a background
        thread.  One-shot mode can request ``sync=True`` to wait for the full
        context before sending the prompt.
        """
        if self._enterprise_autostart_ran:
            return
        self._enterprise_autostart_ran = True
        try:
            from .enterprise_suite import combined_feature_count

            total = combined_feature_count()
        except Exception:  # noqa: BLE001
            total = 0

        def _warm() -> None:
            try:
                from .autostart import run_enterprise_autostart

                snapshot = run_enterprise_autostart(self, root=".")
                self.enterprise_autostart = snapshot
                self.enterprise_autostart_status = (
                    f"ready: {snapshot.ok_count} OK / {snapshot.fail_count} failed in {snapshot.duration_s:.2f}s"
                )
            except Exception as exc:  # noqa: BLE001
                self.enterprise_autostart_status = f"failed: {type(exc).__name__}: {exc}"
                log.debug("Enterprise autostart failed", exc_info=True)

        if sync:
            self.ui.info(f"Loading {total} features and full local intelligence…")
            _warm()
            status = getattr(self, "enterprise_autostart_status", "ready")
            if status.startswith("failed"):
                self.ui.warn(f"Autostart {status}")
            else:
                self.ui.success(f"Autostart {status}")
            return

        # Fast interactive path: do not block the prompt for 15-20 seconds.
        self.enterprise_autostart_status = "warming in background"
        self.ui.success(f"Fast start: {total} features active. Deep code intelligence is warming in background.")
        import threading

        thread = threading.Thread(target=_warm, name="enterprise-autostart", daemon=True)
        thread.start()
        self.enterprise_autostart_thread = thread

    def run(self) -> None:
        if self.ui.animations:
            from .boot_sequence import play_boot_sequence
            play_boot_sequence(self.ui.console, self.config.provider, self.config.resolved_model(), on_stage=self._boot_stage, fast=False)
        else:
            self.ui.show_banner(self.config.provider, self.config.resolved_model())
        if not self.config.has_credentials():
            self.ui.warn(f"No credentials found for provider '{self.config.provider}'.")
        self.build_agent()
        self._run_enterprise_autostart(sync=False)
        try:
            from .goal_history import get_goal_history
            interrupted = get_goal_history().find_interrupted()
            if interrupted:
                self.ui.warn(f"Interrupted goal detected: '{interrupted.goal[:60]}…' ({interrupted.id}). Use /goal-resume {interrupted.id} to continue.")
        except Exception:  # noqa: BLE001
            pass

        while True:
            self._deferred_post_turn()
            try:
                user_input = self.ui.prompt().strip()
            except (EOFError, KeyboardInterrupt):
                self.ui.info("Goodbye! \U0001f44b")
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                result = self.commands.dispatch(self, user_input)
                if result.exit_app:
                    break
                continue
            if self.goal_mode:
                self._run_goal(user_input)
                continue
            if self.agent is None and not self.build_agent():
                continue
            self._handle_turn(user_input)
            self._save_recovery_checkpoint()

    def _boot_stage(self, stage_index: int, stage_name: str) -> None:
        pass

    def _save_recovery_checkpoint(self) -> None:
        try:
            rm = get_recovery_manager()
            cp = Checkpoint(session_id="default", provider=self.config.provider, model=self.config.resolved_model(), messages=list(self.conversation.messages), turn_count=len([m for m in self.conversation.messages if m.get("role") == "user"]), goal_mode=self.goal_mode)
            rm.save(cp)
        except Exception:  # noqa: BLE001
            pass

    def run_once(self, prompt: str) -> None:
        if not self.build_agent():
            return
        self._run_enterprise_autostart(sync=True)
        self._handle_turn(prompt)

    # ------------------------------------------------------------------
    def _deferred_post_turn(self) -> None:
        data = getattr(self, "_pending_post_turn", None)
        if data is None:
            return
        self._pending_post_turn = None
        import time as _time
        from .token_counter import count_message_tokens
        duration = _time.time() - data["turn_start"]
        out_tokens = count_message_tokens([{"role": "assistant", "content": data["final"] or ""}], self.config.resolved_model(), self.config.provider)
        self.token_counter.record_turn(model=self.config.resolved_model(), provider=self.config.provider, input_tokens=data["in_tokens"], output_tokens=out_tokens, duration_s=duration, tool_calls=1 if data.get("used_tool") else 0)
        self.telemetry.record("turn", duration_s=duration, success=True)
        try:
            from .widgets import render_status_bar
            snap = self.token_counter.snapshot()
            bar = render_status_bar(self.config.provider, self.config.resolved_model(), snap, theme_name=getattr(self.ui, "_theme_name", ""), goal_mode=self.goal_mode, effort=getattr(self.config, "effort", ""))
            self.ui.console.print(bar)
        except Exception:  # noqa: BLE001
            self.ui.status_bar(self.config.provider, self.config.resolved_model(), self.conversation.token_estimate())

    def _handle_turn(self, user_input: str) -> None:
        assert self.agent is not None
        from .cancellation import CancellationToken, EscListener
        from .context_manager import compress_messages
        from .task_analyzer import should_auto_orchestrate
        from .token_counter import count_message_tokens
        self._deferred_post_turn()
        original_user_input = user_input
        user_input = self._enrich_user_input(user_input)
        analysis = should_auto_orchestrate(original_user_input, self.config)
        if analysis.is_complex and analysis.recommended_specialists:
            self._handle_orchestrated_turn(user_input, analysis)
            return
        self._used_tool = False
        renderer = self.ui.stream_response()
        renderer.start_thinking()
        cancel_token = CancellationToken()
        if getattr(self.config, "auto_compact", True):
            try:
                before = len(self.conversation.messages)
                self.conversation.messages = compress_messages(self.conversation.messages, self.config.resolved_model(), self.config.provider)
                if len(self.conversation.messages) < before:
                    self.ui.info(f"  (compacted context: {before} -> {len(self.conversation.messages)} messages)")
            except Exception:  # noqa: BLE001
                pass

        def _notify_cancel() -> None:
            renderer.mark_cancelled()

        def on_delta(chunk: str) -> None:
            renderer.on_delta(chunk)

        def on_tool_start(tc) -> bool:
            renderer.finish()
            self.ui.tool_panel(tc.name, tc.arguments)
            tool = self.registry.get(tc.name)
            if tool and tool.dangerous and not self.config.auto_approve_tools:
                return self.ui.confirm(f"Run dangerous tool '{tc.name}'?")
            return True

        def on_tool_result(tc, output: str, success: bool) -> None:
            self._used_tool = True
            self.ui.tool_result(tc.name, output, success)

        def on_thinking(iteration: int) -> None:
            if iteration > 0:
                renderer.start_thinking("reasoning")

        in_tokens = count_message_tokens(self.conversation.messages, self.config.resolved_model(), self.config.provider)
        import time as _time
        turn_start = _time.perf_counter()
        try:
            with EscListener(cancel_token, on_cancel=_notify_cancel):
                final = self.agent.send(user_input, on_delta=on_delta if self.config.stream else None, on_tool_start=on_tool_start, on_tool_result=on_tool_result, on_thinking=on_thinking, cancel_token=cancel_token)
        except Exception as exc:  # noqa: BLE001
            renderer.finish()
            log.exception("Turn failed")
            self.telemetry.record("turn", duration_s=_time.perf_counter() - turn_start, success=False)
            self._remember_project_turn(original_user_input, f"{type(exc).__name__}: {exc}", success=False)
            self.ui.error(f"{type(exc).__name__}: {exc}")
            return
        renderer.finish(final if not self.config.stream else None)
        if cancel_token.cancelled:
            self.ui.warn("Response stopped (Esc).")
        self._remember_project_turn(original_user_input, final, success=True)
        try:
            self.branch_manager.sync(self.conversation.messages)
        except Exception:  # noqa: BLE001
            pass
        self._pending_post_turn = {"final": final, "turn_start": turn_start, "in_tokens": in_tokens, "used_tool": self._used_tool}

    def _handle_orchestrated_turn(self, user_input: str, analysis) -> None:
        import time as _time
        from .multi_agent import MultiAgentOrchestrator
        original_user_input = user_input.split("[AUTO PROJECT BRAIN CONTEXT]", 1)[0].split("[OMNI-AION COGNITIVE PACKET]", 1)[0].split("[TITAN FABRIC ENTERPRISE PACKET]", 1)[0].split("[ATLAS REACTOR EXECUTION PACKET]", 1)[0].split("[FORGECORE MISSION PACKET]", 1)[0].split("[MEGAFORGE ENTERPRISE PACKET]", 1)[0].strip()
        specialists_str = ", ".join(analysis.recommended_specialists)
        self.ui.info(f"\u26a1 Complex task detected — auto-orchestrating with {len(analysis.recommended_specialists)} specialists ({specialists_str})")
        self.ui.info(f"   Reason: {analysis.reason}")
        orch = MultiAgentOrchestrator(self)
        if analysis.orchestration_mode == "parallel":
            result = orch.run_parallel(user_input, analysis.recommended_specialists)
        elif analysis.orchestration_mode == "pipeline":
            result = orch.run_pipeline(user_input)
        else:
            result = orch.run_sequential(user_input, analysis.recommended_specialists)
        self.ui.console.print(result.summary())
        merged = result.merged_output
        if merged:
            self.conversation.add_user(user_input)
            self.conversation.add_assistant(merged)
            self.conversation.save()
            renderer = self.ui.stream_response()
            renderer.start_thinking()
            chunk_size = 50
            for i in range(0, len(merged), chunk_size):
                renderer.on_delta(merged[i:i + chunk_size])
                _time.sleep(0.01)
            renderer.finish(merged)
            self._remember_project_turn(original_user_input, merged, success=True)
            try:
                self.branch_manager.sync(self.conversation.messages)
            except Exception:  # noqa: BLE001
                pass
        else:
            self.ui.warn("No output from sub-agents — falling back to single agent.")
            self._handle_turn(user_input)
        self._pending_post_turn = {"final": merged, "turn_start": _time.perf_counter(), "in_tokens": len(user_input.split()), "used_tool": False}


def main() -> None:
    """Console-script entry point.

    Keep this in sync with top-level ``main.py`` so both `python main.py` and
    installed/binary `agent` support the same flags.
    """
    try:
        from . import __version__
        from .cli import parse_args

        args = parse_args()
        if args.version:
            print(f"terminal-agent {__version__}")
            return

        app = App(animations=not args.no_anim)
        if args.theme:
            from . import themes

            if not themes.set_theme(args.theme):
                print(f"Unknown theme '{args.theme}'. Options: {', '.join(themes.names())}")
        if args.spinner:
            from . import effects

            if args.spinner in effects.SPINNERS:
                app.ui.spinner = args.spinner
            else:
                print(f"Unknown spinner '{args.spinner}'. Options: {', '.join(effects.SPINNERS)}")
        if args.provider:
            app.config.provider = args.provider
            app.config.model = None
        if args.model:
            app.config.model = args.model
        if args.auto:
            app.config.auto_approve_tools = True

        if args.prompt:
            app.run_once(args.prompt)
        else:
            app.run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
