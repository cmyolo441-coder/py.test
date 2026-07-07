"""Application shell: wires config, provider, tools, memory, commands and UI."""

from __future__ import annotations

import sys

from .commands import build_command_registry
from .config import Config
from .core import Agent
from .memory import Conversation
from .plugins.loader import load_plugins
from .providers import get_provider
from .providers.factory import ProviderError
from .tools import build_default_registry
from .ui import UI
from .utils.logging import get_logger
# Enterprise subsystems (v2).
from .token_counter import get_token_counter
from .branching import BranchManager
from .telemetry import get_telemetry
from .profiler import get_profiler
from .recovery import get_recovery_manager, Checkpoint

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
        # Feed every registered command (name + aliases) into the UI so the
        # live `/` dropdown and `/help` list the full A-to-Z command surface.
        self.ui.set_command_source(self._command_items)
        # Load optional user plugins (extra tools).
        for tool in load_plugins():
            self.registry.register(tool)
            log.info("Loaded plugin tool: %s", tool.name)
        # Enterprise subsystems.
        self.token_counter = get_token_counter()
        self.branch_manager = BranchManager(list(self.conversation.messages))
        self.telemetry = get_telemetry()
        self.profiler = get_profiler()
        if getattr(self.config, "telemetry_enabled", False):
            self.telemetry.enable()
        if getattr(self.config, "profiler_enabled", False):
            self.profiler.enable()

    # ------------------------------------------------------------------
    def _command_items(self) -> list[tuple[str, str]]:
        """(command, description) pairs for every registered command + alias.

        Powers the live `/` completion menu and `/help` so nothing is hidden.
        """
        items: list[tuple[str, str]] = []
        for cmd in sorted(self.commands.all(), key=lambda c: c.name):
            desc = (cmd.help or "").strip()
            items.append((cmd.name, desc))
            for alias in getattr(cmd, "aliases", ()):  # surface aliases too
                items.append((alias, f"alias of {cmd.name}"))
        return items

    def build_agent(self) -> bool:
        try:
            provider = get_provider(self.config)
        except ProviderError as exc:
            self.ui.error(str(exc))
            self.ui.info(
                "Tip: for a zero-config local setup run `/provider ollama`, "
                "or set an API key env var (e.g. ZEN_API_KEY)."
            )
            return False
        self.agent = Agent(self.config, provider, self.registry, self.conversation)
        log.info("Agent ready: provider=%s model=%s", self.config.provider, self.config.resolved_model())
        return True

    # ------------------------------------------------------------------
    def _enter_goal_mode(self) -> None:
        """Save current config and apply goal-mode overrides (godmode)."""
        from .effort import get_effort

        cfg = self.config
        effort = get_effort("godmode")
        self._goal_config_backup = {
            "effort": getattr(cfg, "effort", "normal"),
            "temperature": cfg.temperature,
            "enable_tools": cfg.enable_tools,
            "auto_approve_tools": cfg.auto_approve_tools,
            "max_tool_iterations": cfg.max_tool_iterations,
        }
        cfg.effort = effort.name
        cfg.temperature = effort.temperature
        cfg.enable_tools = True
        cfg.auto_approve_tools = True
        cfg.max_tool_iterations = max(cfg.max_tool_iterations, 40)
        self.goal_mode = True
        self.ui.goal_mode = True

    def _exit_goal_mode(self) -> None:
        """Restore the config that was active before goal mode was entered."""
        if self._goal_config_backup is not None:
            for key, value in self._goal_config_backup.items():
                setattr(self.config, key, value)
            self._goal_config_backup = None
        self.goal_mode = False
        self.ui.goal_mode = False

    def _run_goal(self, goal_text: str) -> None:
        """Run a single goal while in persistent goal mode."""
        from .goal_mode import GoalMode

        run = GoalMode(self).run(goal_text)
        rounds = sum(1 for s in run.steps if s.kind == "execute")
        if run.cancelled:
            self.ui.warn(f"Goal Mode stopped by user after {rounds} round(s).")
        elif run.complete:
            self.ui.success(
                f"Goal achieved in {rounds} execution round(s)."
            )
        else:
            self.ui.warn(
                f"Goal not fully verified after {rounds} round(s). "
                "Type a refined goal to continue."
            )
        self.ui.status_bar(
            self.config.provider,
            self.config.resolved_model(),
            self.conversation.token_estimate(),
        )

    def run(self) -> None:
        # Animated boot sequence (skipped if --no-anim).
        if self.ui.animations:
            from .boot_sequence import play_boot_sequence
            play_boot_sequence(
                self.ui.console,
                self.config.provider,
                self.config.resolved_model(),
                on_stage=self._boot_stage,
                fast=False,
            )
        else:
            self.ui.show_banner(self.config.provider, self.config.resolved_model())
        if not self.config.has_credentials():
            self.ui.warn(f"No credentials found for provider '{self.config.provider}'.")
        self.build_agent()

        # Prompt to resume an interrupted goal if one exists.
        try:
            from .goal_history import get_goal_history
            interrupted = get_goal_history().find_interrupted()
            if interrupted:
                self.ui.warn(
                    f"Interrupted goal detected: '{interrupted.goal[:60]}…' "
                    f"({interrupted.id}). Use /goal-resume {interrupted.id} to continue."
                )
        except Exception:  # noqa: BLE001
            pass

        while True:
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
            # Auto-save recovery checkpoint after each turn.
            self._save_recovery_checkpoint()

    def _boot_stage(self, stage_index: int, stage_name: str) -> None:
        """Called by the boot sequence between animation frames — no-op for now."""
        pass

    def _save_recovery_checkpoint(self) -> None:
        """Save a recovery checkpoint of the current session."""
        try:
            rm = get_recovery_manager()
            cp = Checkpoint(
                session_id="default",
                provider=self.config.provider,
                model=self.config.resolved_model(),
                messages=list(self.conversation.messages),
                turn_count=len([m for m in self.conversation.messages if m.get("role") == "user"]),
                goal_mode=self.goal_mode,
            )
            rm.save(cp)
        except Exception:  # noqa: BLE001
            pass

    def run_once(self, prompt: str) -> None:
        """Run a single prompt non-interactively then return (for scripting)."""
        if not self.build_agent():
            return
        self._handle_turn(prompt)

    # ------------------------------------------------------------------
    def _handle_turn(self, user_input: str) -> None:
        assert self.agent is not None
        import time as _time
        from .cancellation import CancellationToken, EscListener
        from .context_manager import compress_messages
        from .token_counter import count_message_tokens

        # The prompt box already shows the user's input inline (it persists as
        # the on-screen record of the turn), so we don't re-render it as a
        # bubble or erase the box — that manual erasure is what used to make the
        # input box disappear.
        self._used_tool = False
        renderer = self.ui.stream_response()
        renderer.start_thinking()

        cancel_token = CancellationToken()
        turn_start = _time.perf_counter()

        # Auto-compact the conversation if configured and near the context limit.
        if getattr(self.config, "auto_compact", True):
            try:
                before = len(self.conversation.messages)
                self.conversation.messages = compress_messages(
                    self.conversation.messages,
                    self.config.resolved_model(),
                    self.config.provider,
                )
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

        # Count input tokens before the call.
        in_tokens = count_message_tokens(
            self.conversation.messages, self.config.resolved_model(), self.config.provider
        )

        try:
            with EscListener(cancel_token, on_cancel=_notify_cancel):
                final = self.agent.send(
                    user_input,
                    on_delta=on_delta if self.config.stream else None,
                    on_tool_start=on_tool_start,
                    on_tool_result=on_tool_result,
                    on_thinking=on_thinking,
                    cancel_token=cancel_token,
                )
        except Exception as exc:  # noqa: BLE001
            renderer.finish()
            log.exception("Turn failed")
            self.telemetry.record("turn", duration_s=_time.perf_counter() - turn_start, success=False)
            self.ui.error(f"{type(exc).__name__}: {exc}")
            return

        renderer.finish(final if not self.config.stream else None)
        if cancel_token.cancelled:
            self.ui.warn("Response stopped (Esc).")

        # Record real token usage for this turn.
        duration = _time.perf_counter() - turn_start
        out_tokens = count_message_tokens(
            [{"role": "assistant", "content": final or ""}],
            self.config.resolved_model(),
            self.config.provider,
        )
        self.token_counter.record_turn(
            model=self.config.resolved_model(),
            provider=self.config.provider,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            duration_s=duration,
            tool_calls=1 if self._used_tool else 0,
        )
        self.telemetry.record("turn", duration_s=duration, success=True)

        # Celebrate when a multi-step, tool-using task completes.
        if self._used_tool and self.ui.animations:
            from . import effects

            effects.confetti(self.ui.console, frames=12)

        # Live status bar footer for the turn — now uses the real token counter.
        try:
            from .widgets import render_status_bar
            snap = self.token_counter.snapshot()
            bar = render_status_bar(
                self.config.provider,
                self.config.resolved_model(),
                snap,
                theme_name=getattr(self.ui, "_theme_name", ""),
                goal_mode=self.goal_mode,
                effort=getattr(self.config, "effort", ""),
            )
            self.ui.console.print(bar)
        except Exception:  # noqa: BLE001
            # Fallback to the old status bar.
            self.ui.status_bar(
                self.config.provider,
                self.config.resolved_model(),
                self.conversation.token_estimate(),
            )


def main() -> None:
    try:
        App().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
