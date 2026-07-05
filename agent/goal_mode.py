"""Goal Mode: a fully autonomous, maximum-capacity execution mode.

Goal Mode turns a single high-level goal into a finished result with zero
babysitting. It is the most advanced mode in the agent:

  * AUTO effort: forces the heaviest effort level ("godmode" / ultra) so the
    model plans deeply, executes with the real tool loop, self-verifies and
    keeps iterating until the goal is genuinely met.
  * FULL capacity: tool calling is enabled, dangerous tools are auto-approved
    for the duration of the run, and the per-turn tool budget is raised so the
    model can use the entire A-to-Z tool + command surface without stalling.
  * REAL execution: unlike a pure "reasoning" planner, Goal Mode drives the
    live ``Agent`` (which actually runs shell/file/git/etc. tools), so plans are
    carried out for real, not simulated.

The loop is: plan -> execute (real tools) -> verify -> (fix) -> repeat, guarded
by the effort level's round/verification budgets and by fake-code detection.
Everything is produced by the underlying model + real tools; nothing is faked.

Press Esc at any time to stop the run; the partial progress is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .cancellation import CancellationToken, EscListener
from .effort import EffortLevel, get_effort
from .fake_detector import scan_text


# The effort level Goal Mode always runs at (the ultra / most-advanced tier).
GOAL_EFFORT = "godmode"

# Outer verify/fix rounds. Each round is a FULL autonomous agent turn (which can
# itself run up to `max_tool_iterations` tool calls), so a modest outer cap still
# yields very deep work while preventing runaway/expensive loops. Esc stops early.
MAX_OUTER_ROUNDS = 12

# Inner per-turn tool budget used during Goal Mode (raised for full capacity).
GOAL_TOOL_BUDGET = 40


@dataclass
class GoalStep:
    kind: str          # "plan" | "execute" | "verify" | "fix" | "done"
    round: int
    text: str


@dataclass
class GoalRun:
    goal: str
    effort: str
    steps: list[GoalStep] = field(default_factory=list)
    complete: bool = False
    cancelled: bool = False
    final: str = ""


class GoalMode:
    """Drive the real Agent autonomously until a goal is complete.

    Parameters
    ----------
    app:
        The live ``App`` instance (gives us the agent, config, UI, tools).
    """

    # System guidance injected for the planning / verification sub-calls.
    PLANNER_SYS = (
        "You are an elite autonomous engineer operating in GOAL MODE at maximum "
        "effort. Break the goal into a concrete, ordered, end-to-end plan that "
        "you can execute using the available tools (shell, files, git, search, "
        "etc.). Be exhaustive and professional. Number every step."
    )
    VERIFIER_SYS = (
        "You are a strict QA lead. Given the goal and everything done so far, "
        "decide if the goal is FULLY and correctly achieved with no gaps, bugs, "
        "placeholders or simulated work. If it is complete, reply starting with "
        "'COMPLETE'. Otherwise reply starting with 'INCOMPLETE' and list the "
        "exact remaining work as numbered, actionable items."
    )

    def __init__(self, app) -> None:  # noqa: ANN001 - avoid import cycle
        self.app = app
        self.ui = app.ui

    # ------------------------------------------------------------------
    def _raw_chat(self, system: str, user: str) -> str:
        """One-shot model call with no tools (used for plan/verify reasoning)."""
        provider = self.app.agent.provider
        resp = provider.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}]
        )
        return getattr(resp, "content", "") or ""

    def _execute_turn(self, instruction: str, cancel_token: CancellationToken) -> str:
        """Run the real agent turn (with live tool execution) for an instruction."""
        renderer = self.ui.stream_response()
        renderer.start_thinking("executing")
        self.app._used_tool = False

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
        return final or ""

    # ------------------------------------------------------------------
    def run(self, goal: str) -> GoalRun:
        """Execute ``goal`` autonomously at maximum capacity until complete.

        Config overrides (godmode effort, auto-approve, etc.) are managed by
        the caller so that persistent goal mode can keep them active across
        multiple goal runs.
        """
        effort: EffortLevel = get_effort(GOAL_EFFORT)
        run = GoalRun(goal=goal, effort=effort.name)

        cancel_token = CancellationToken()
        self.ui.info(
            f"\U0001f3af Goal Mode executing \u2014 effort '{effort.name}' (ultra), "
            f"full tool capacity, auto-approve ON. Press Esc to stop."
        )

        try:
            with EscListener(cancel_token, on_cancel=lambda: None):
                run = self._loop(goal, effort, run, cancel_token)
        finally:
            pass  # Config managed by caller (_enter_goal_mode / _exit_goal_mode)
        return run

    def _loop(
        self,
        goal: str,
        effort: EffortLevel,
        run: GoalRun,
        cancel_token: CancellationToken,
    ) -> GoalRun:
        # 1) Plan.
        self.ui.info("\U0001f4dd Planning\u2026")
        plan = self._raw_chat(self.PLANNER_SYS, f"Goal:\n{goal}")
        run.steps.append(GoalStep("plan", 0, plan))
        self.ui.tool_result("plan", plan[:2000], True)

        transcript = f"PLAN:\n{plan}\n"
        max_rounds = max(1, min(effort.max_execution_rounds, MAX_OUTER_ROUNDS))

        for rnd in range(1, max_rounds + 1):
            if cancel_token.cancelled:
                run.cancelled = True
                break

            # 2) Execute the next chunk of the plan with real tools.
            self.ui.info(f"\u26a1 Executing (round {rnd}/{max_rounds})\u2026")
            instruction = (
                f"GOAL:\n{goal}\n\nPLAN:\n{plan}\n\nWORK SO FAR:\n{transcript[-6000:]}\n\n"
                "Continue executing the plan now. Use the available tools to do "
                "real work (create/modify files, run commands, etc.). Do only "
                "what is needed next; do not stop until the goal is done or you "
                "need verification. No placeholders or simulated output."
            )
            work = self._execute_turn(instruction, cancel_token)
            run.steps.append(GoalStep("execute", rnd, work))
            transcript += f"\nEXECUTE #{rnd}:\n{work}\n"

            if cancel_token.cancelled:
                run.cancelled = True
                break

            # ESC during execution: stop before spending a verify round.
            if cancel_token.cancelled:
                run.cancelled = True
                break

            # 3) Verify against the goal.
            verdict = self._raw_chat(
                self.VERIFIER_SYS,
                f"Goal:\n{goal}\n\nEverything done so far:\n{transcript[-8000:]}",
            )
            run.steps.append(GoalStep("verify", rnd, verdict))
            self.ui.tool_result(f"verify #{rnd}", verdict[:1500], True)

            complete = verdict.strip().upper().startswith("COMPLETE")
            fakes = scan_text(transcript) if effort.detect_fake else []
            if complete and not fakes:
                run.complete = True
                break

            # ESC during verify: stop before starting a fix round.
            if cancel_token.cancelled:
                run.cancelled = True
                break

            # 4) Fix / continue: feed the verifier's gaps back as the next task.
            gaps = verdict
            if fakes:
                gaps += "\n\nAlso: replace any fake/simulated/placeholder work with real implementations."
            fix_instruction = (
                f"GOAL:\n{goal}\n\nThe goal is not yet complete. Address every "
                f"remaining item below by doing real work with tools:\n{gaps}"
            )
            fix_work = self._execute_turn(fix_instruction, cancel_token)
            run.steps.append(GoalStep("fix", rnd, fix_work))
            transcript += f"\nFIX #{rnd}:\n{fix_work}\n"

        run.final = transcript
        if cancel_token.cancelled:
            run.cancelled = True
        return run
