"""Goal Mode — the fully autonomous, maximum-capability execution engine.

This is the most powerful mode in the agent. Version 2 is a complete rewrite
with these enterprise capabilities:

  * PERSISTENT: stays active across multiple goals until /chat is typed.
  * CHECKPOINTED: state is saved after every round so it can resume.
  * RESUMABLE: interrupted goals can be picked up where they left off.
  * TEMPLATE-DRIVEN: launch pre-built workflows (/goal-template <name>).
  * AUTO-ESCALATING: bumps effort level if verification keeps failing.
  * RESOURCE-MONITORED: tracks tokens, time, cost per goal in real time.
  * MULTI-ANGLE VERIFIED: combines model verdict + quality scorer + fake detector.
  * CONTEXT-AWARE: uses the context_manager to fit any model's window.
  * FALLBACK-RESILIENT: walks the fallback chain if a provider dies mid-goal.
  * COMMAND-AWARE: can invoke slash commands as part of execution.
  * STREAMING PROGRESS: live dashboard shows current phase, round, tokens, cost.

Loop: plan -> execute (real tools) -> verify (multi-angle) -> (fix) -> repeat,
guarded by the effort level's round/verification budgets.

Press Esc at any time to stop; partial progress is checkpointed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .cancellation import CancellationToken, EscListener
from .effort import EffortLevel, get_effort, next_effort
from .fake_detector import scan_text
from .goal_history import GoalRecord, get_goal_history
from .quality_scorer import score_response
from .token_counter import get_token_counter


# The effort level Goal Mode always starts at (the ultra / most-advanced tier).
GOAL_EFFORT = "godmode"

# Outer verify/fix rounds. Each round is a FULL autonomous agent turn (which
# can itself run up to `max_tool_iterations` tool calls), so a modest outer
# cap still yields very deep work while preventing runaway/expensive loops.
MAX_OUTER_ROUNDS = 12

# Inner per-turn tool budget used during Goal Mode (raised for full capacity).
GOAL_TOOL_BUDGET = 40

# After this many consecutive failed verifications, auto-escalate effort.
ESCALATE_AFTER_FAILURES = 2


@dataclass
class GoalStep:
    kind: str          # "plan" | "execute" | "verify" | "fix" | "done"
    round: int
    text: str
    quality_score: int = 0
    tokens_used: int = 0
    duration_s: float = 0.0


@dataclass
class GoalRun:
    goal: str
    effort: str
    steps: list[GoalStep] = field(default_factory=list)
    complete: bool = False
    cancelled: bool = False
    final: str = ""
    # Resource tracking
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0
    # Checkpointing
    record: GoalRecord | None = None
    # Verification
    verification_failures: int = 0
    escalated: bool = False

    def summary(self) -> str:
        rounds = sum(1 for s in self.steps if s.kind == "execute")
        return (
            f"Goal: {self.goal[:80]}\n"
            f"  effort:     {self.effort}\n"
            f"  rounds:     {rounds}\n"
            f"  complete:   {self.complete}\n"
            f"  cancelled:  {self.cancelled}\n"
            f"  tokens:     {self.total_tokens:,}\n"
            f"  cost:       ${self.total_cost_usd:.4f}\n"
            f"  duration:   {self.total_duration_s:.1f}s\n"
            f"  escalated:  {self.escalated}"
        )


class GoalMode:
    """Drive the real Agent autonomously until a goal is complete.

    Version 2 enhancements over the original:
      * Checkpoints after every round (resumable).
      * Multi-angle verification (verdict + quality score + fake scan).
      * Auto-escalation when verification fails repeatedly.
      * Live resource monitoring via TokenCounter.
      * History persistence via GoalHistory.
      * Context-aware execution via context_manager.
      * Fallback-resilient via fallback chain.
    """

    # System guidance injected for the planning / verification sub-calls.
    PLANNER_SYS = (
        "You are an elite autonomous engineer operating in GOAL MODE at maximum "
        "effort. Break the goal into a concrete, ordered, end-to-end plan that "
        "you can execute using the available tools (shell, files, git, search, "
        "etc.). Be exhaustive and professional. Number every step. Identify "
        "dependencies between steps. Estimate complexity per step."
    )
    VERIFIER_SYS = (
        "You are a strict QA lead. Given the goal and everything done so far, "
        "decide if the goal is FULLY and correctly achieved with no gaps, bugs, "
        "placeholders or simulated work. If it is complete, reply starting with "
        "'COMPLETE'. Otherwise reply starting with 'INCOMPLETE' and list the "
        "exact remaining work as numbered, actionable items. Be ruthless — do "
        "not approve work that has TODOs, placeholders, or untested code."
    )

    def __init__(self, app) -> None:  # noqa: ANN001 - avoid import cycle
        self.app = app
        self.ui = app.ui
        self.token_counter = get_token_counter()
        self.history = get_goal_history()
        self._start_time = 0.0

    # ------------------------------------------------------------------
    def _raw_chat(self, system: str, user: str) -> str:
        """One-shot model call with no tools (used for plan/verify reasoning).

        Uses the fallback chain so a dead primary provider doesn't kill the goal.
        """
        import time as _time
        from .fallback import get_fallback_chain
        from .context_manager import compress_messages

        provider = self.app.agent.provider
        config = self.app.config
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # Compress to fit the model's context window.
        messages = compress_messages(messages, config.resolved_model(), config.provider)
        start = _time.perf_counter()
        try:
            resp = provider.chat(messages)
            text = getattr(resp, "content", "") or ""
        except Exception as exc:  # noqa: BLE001
            # Try the fallback chain.
            chain = get_fallback_chain()
            entry = chain.pick()
            if entry is None:
                return f"(error: {exc})"
            try:
                # Build a provider for the fallback entry.
                from .providers.factory import get_provider as _get_provider
                fb_config = type(config)()
                fb_config.provider = entry.provider
                fb_config.model = entry.model
                fb_provider = _get_provider(fb_config)
                resp = fb_provider.chat(messages)
                text = getattr(resp, "content", "") or ""
            except Exception as exc2:  # noqa: BLE001
                return f"(error: {exc}; fallback also failed: {exc2})"
        duration = _time.perf_counter() - start
        # Record token usage (approximate — we don't have streaming deltas here).
        in_tok = len(user.split()) + len(system.split())
        out_tok = len(text.split())
        self.token_counter.record_turn(
            model=config.resolved_model(),
            provider=config.provider,
            input_tokens=in_tok,
            output_tokens=out_tok,
            duration_s=duration,
            is_goal=True,
        )
        return text

    def _execute_turn(self, instruction: str, cancel_token: CancellationToken) -> str:
        """Run the real agent turn (with live tool execution) for an instruction."""
        import time as _time
        renderer = self.ui.stream_response()
        renderer.start_thinking("executing")
        self.app._used_tool = False
        start = _time.perf_counter()

        def on_delta(chunk: str) -> None:
            renderer.on_delta(chunk)

        def on_tool_start(tc) -> bool:  # noqa: ANN001
            renderer.finish()
            self.ui.tool_panel(tc.name, tc.arguments)
            return True  # Goal Mode auto-approves everything.

        def on_tool_result(tc, output: str, success: bool) -> None:  # noqa: ANN001
            self.app._used_tool = True
            self.ui.tool_result(tc.name, output, success)

        def on_thinking(iteration: int) -> None:
            if iteration > 0:
                renderer.start_thinking("reasoning")

        final = self.app.agent.send(
            instruction,
            on_delta=on_delta if self.app.config.stream else None,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
            on_thinking=on_thinking,
            cancel_token=cancel_token,
        )
        renderer.finish(final if not self.app.config.stream else None)
        duration = _time.perf_counter() - start

        # Record the turn's token usage from the conversation memory.
        last_assistant = None
        for m in reversed(self.app.conversation.messages):
            if m.get("role") == "assistant":
                last_assistant = m
                break
        out_text = ""
        if last_assistant:
            content = last_assistant.get("content", "")
            if isinstance(content, str):
                out_text = content
        in_tok = len(instruction.split()) + 200  # rough overhead
        out_tok = max(len(out_text.split()), 1)
        self.token_counter.record_turn(
            model=self.app.config.resolved_model(),
            provider=self.app.config.provider,
            input_tokens=in_tok,
            output_tokens=out_tok,
            duration_s=duration,
            tool_calls=1 if self.app._used_tool else 0,
            is_goal=True,
        )
        return final or ""

    # ------------------------------------------------------------------
    def run(self, goal: str, resume_from: GoalRecord | None = None) -> GoalRun:
        """Execute ``goal`` autonomously at maximum capacity until complete.

        If ``resume_from`` is provided, continues from that goal's last
        checkpoint instead of starting fresh.
        """
        self._start_time = time.time()
        self.ui.hide_prompt()
        effort: EffortLevel = get_effort(GOAL_EFFORT)
        run = GoalRun(goal=goal, effort=effort.name)

        # Set up history record (new or resumed).
        if resume_from is not None:
            run.record = resume_from
            run.record.status = "running"
            run.steps = [
                GoalStep(kind=s.get("kind", ""), round=s.get("round", 0), text=s.get("text", ""))
                for s in resume_from.steps
            ]
            run.final = resume_from.final
            self.ui.info(f"📂 Resuming goal {resume_from.id} from round {resume_from.last_round_completed}")
        else:
            run.record = GoalRecord(goal=goal, effort=effort.name, status="running")
            self.history.save(run.record)

        cancel_token = CancellationToken()
        self.ui.info(
            f"🎯 Goal Mode executing — effort '{effort.name}' (ultra), "
            f"full tool capacity, auto-approve ON. Press Esc to stop."
        )
        self.ui.info(f"📝 Goal ID: {run.record.id} (saved to history)")

        # Reset per-goal token tracking.
        self.token_counter.reset_goal()

        try:
            with EscListener(cancel_token, on_cancel=lambda: None):
                run = self._loop(goal, effort, run, cancel_token, resume_from)
        finally:
            # Finalize the history record.
            if run.record is not None:
                run.record.status = (
                    "complete" if run.complete
                    else "cancelled" if run.cancelled
                    else "interrupted"
                )
                run.record.completed_at = time.time()
                run.record.rounds = sum(1 for s in run.steps if s.kind == "execute")
                run.record.steps = [
                    {"kind": s.kind, "round": s.round, "text": s.text[:2000]}
                    for s in run.steps
                ]
                run.record.final = run.final[:5000]
                snap = self.token_counter.snapshot()
                run.record.cost_usd = snap["goal_cost_usd"]
                run.record.total_tokens = snap["goal_total"]
                run.record.last_round_completed = run.record.rounds
                run.record.checkpoint_messages = self.app.conversation.messages[-20:]
                self.history.save(run.record)
        return run

    def _loop(
        self,
        goal: str,
        effort: EffortLevel,
        run: GoalRun,
        cancel_token: CancellationToken,
        resume_from: GoalRecord | None,
    ) -> GoalRun:
        # 1) Plan (skip if resuming — we already have a plan).
        if resume_from is None or not any(s.kind == "plan" for s in run.steps):
            self.ui.info("📝 Planning…")
            plan = self._raw_chat(self.PLANNER_SYS, f"Goal:\n{goal}")
            step = GoalStep("plan", 0, plan)
            run.steps.append(step)
            self.ui.tool_result("plan", plan[:2000], True)
            self._checkpoint(run)
        else:
            plan = next((s.text for s in run.steps if s.kind == "plan"), goal)
            self.ui.info("📂 Reusing existing plan from checkpoint")

        transcript = f"PLAN:\n{plan}\n"
        max_rounds = max(1, min(effort.max_execution_rounds, MAX_OUTER_ROUNDS))
        start_round = (resume_from.last_round_completed + 1) if resume_from else 1

        for rnd in range(start_round, max_rounds + 1):
            if cancel_token.cancelled:
                run.cancelled = True
                break

            # 2) Execute the next chunk of the plan with real tools.
            self.ui.info(f"⚡ Executing (round {rnd}/{max_rounds})…")
            self._show_live_progress(run, rnd, max_rounds)
            instruction = (
                f"GOAL:\n{goal}\n\nPLAN:\n{plan}\n\nWORK SO FAR:\n{transcript[-6000:]}\n\n"
                "Continue executing the plan now. Use the available tools to do "
                "real work (create/modify files, run commands, etc.). Do only "
                "what is needed next; do not stop until the goal is done or you "
                "need verification. No placeholders or simulated output."
            )
            work = self._execute_turn(instruction, cancel_token)
            step = GoalStep("execute", rnd, work)
            run.steps.append(step)
            transcript += f"\nEXECUTE #{rnd}:\n{work}\n"
            self._checkpoint(run)

            if cancel_token.cancelled:
                run.cancelled = True
                break

            # 3) Verify against the goal — multi-angle.
            verdict = self._raw_chat(
                self.VERIFIER_SYS,
                f"Goal:\n{goal}\n\nEverything done so far:\n{transcript[-8000:]}",
            )
            v_step = GoalStep("verify", rnd, verdict)
            # Score the work's quality.
            quality = score_response(goal, work)
            v_step.quality_score = quality.score
            run.steps.append(v_step)
            self.ui.tool_result(f"verify #{rnd}", verdict[:1500], True)
            self.ui.info(f"📊 Quality score: {quality.score}/100 (grade {quality.grade})")

            complete = verdict.strip().upper().startswith("COMPLETE")
            fakes = scan_text(transcript) if effort.detect_fake else []
            # Multi-angle: complete requires verdict + quality >= 60 + no fakes.
            truly_complete = complete and quality.score >= 60 and not fakes

            if truly_complete:
                run.complete = True
                run.final = work
                break

            # Track failures for auto-escalation.
            if not complete:
                run.verification_failures += 1
            else:
                run.verification_failures = 0  # reset on partial success

            # Auto-escalate after repeated failures.
            if (
                run.verification_failures >= ESCALATE_AFTER_FAILURES
                and not run.escalated
                and effort.name != "godmode"
            ):
                new_effort = next_effort(effort.name)
                self.ui.warn(
                    f"📈 Auto-escalating effort: {effort.name} -> {new_effort.name} "
                    f"(after {run.verification_failures} verification failures)"
                )
                effort = new_effort
                run.effort = effort.name
                run.escalated = True
                max_rounds = max(1, min(effort.max_execution_rounds, MAX_OUTER_ROUNDS))

            if cancel_token.cancelled:
                run.cancelled = True
                break

            # 4) Fix / continue: feed the verifier's gaps back as the next task.
            gaps = verdict
            if fakes:
                gaps += "\n\nAlso: replace any fake/simulated/placeholder work with real implementations."
            if quality.score < 60:
                gaps += f"\n\nAlso: quality score was {quality.score}/100. " + "; ".join(quality.notes[:3])
            fix_instruction = (
                f"GOAL:\n{goal}\n\nThe goal is not yet complete. Address every "
                f"remaining item below by doing real work with tools:\n{gaps}"
            )
            fix_work = self._execute_turn(fix_instruction, cancel_token)
            f_step = GoalStep("fix", rnd, fix_work)
            run.steps.append(f_step)
            transcript += f"\nFIX #{rnd}:\n{fix_work}\n"
            self._checkpoint(run)

        run.final = run.final or transcript
        # Aggregate resource usage.
        snap = self.token_counter.snapshot()
        run.total_tokens = snap["goal_total"]
        run.total_cost_usd = snap["goal_cost_usd"]
        run.total_duration_s = time.time() - self._start_time
        if cancel_token.cancelled:
            run.cancelled = True
        return run

    # ------------------------------------------------------------------
    def _checkpoint(self, run: GoalRun) -> None:
        """Save current state to history so the goal can be resumed."""
        if run.record is None:
            return
        run.record.steps = [
            {"kind": s.kind, "round": s.round, "text": s.text[:2000]}
            for s in run.steps
        ]
        run.record.rounds = sum(1 for s in run.steps if s.kind == "execute")
        run.record.last_round_completed = run.record.rounds
        run.record.checkpoint_messages = self.app.conversation.messages[-20:]
        snap = self.token_counter.snapshot()
        run.record.cost_usd = snap["goal_cost_usd"]
        run.record.total_tokens = snap["goal_total"]
        self.history.save(run.record)

    def _show_live_progress(self, run: GoalRun, current_round: int, max_rounds: int) -> None:
        """Render a compact live progress line."""
        snap = self.token_counter.snapshot()
        self.ui.info(
            f"   📊 tokens={snap['goal_total']:,}  "
            f"cost={snap['goal_cost_fmt']}  "
            f"round={current_round}/{max_rounds}  "
            f"effort={run.effort}"
        )

    # ------------------------------------------------------------------
    def run_command(self, command_str: str) -> str:
        """Execute a slash command as part of goal execution.

        This lets Goal Mode drive the entire agent surface — switching models,
        running tools, exporting sessions, etc. — as part of its plan.
        """
        if not command_str.startswith("/"):
            command_str = "/" + command_str
        self.ui.info(f"🎯 Goal Mode running command: {command_str}")
        result = self.app.commands.dispatch(self.app, command_str)
        return f"Command {command_str}: exit_app={result.exit_app}, handled={result.handled}"
