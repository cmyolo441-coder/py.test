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

    def _enrich_user_input(self, user_input: str) -> str:
        enriched = self._project_brain_context(user_input)
        enriched = self._omni_aion_context(enriched)
        enriched = self._titan_fabric_context(enriched)
        enriched = self._atlas_reactor_context(enriched)
        enriched = self._forgecore_context(enriched)
        enriched = self._megaforge_context(enriched)
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
        try:
            provider = get_provider(self.config)
        except ProviderError as exc:
            self.ui.error(str(exc))
            self.ui.info("Tip: for a zero-config local setup run `/provider ollama`, or set an API key env var (e.g. ZEN_API_KEY).")
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

    def run(self) -> None:
        if self.ui.animations:
            from .boot_sequence import play_boot_sequence
            play_boot_sequence(self.ui.console, self.config.provider, self.config.resolved_model(), on_stage=self._boot_stage, fast=False)
        else:
            self.ui.show_banner(self.config.provider, self.config.resolved_model())
        if not self.config.has_credentials():
            self.ui.warn(f"No credentials found for provider '{self.config.provider}'.")
        self.build_agent()
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
        else:
            self.ui.warn("No output from sub-agents — falling back to single agent.")
            self._handle_turn(user_input)
        self._pending_post_turn = {"final": merged, "turn_start": _time.perf_counter(), "in_tokens": len(user_input.split()), "used_tool": False}


def main() -> None:
    try:
        App().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
