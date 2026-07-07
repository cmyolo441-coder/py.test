"""The /goal command: run a goal fully autonomously at maximum capacity."""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult


class GoalCommand(Command):
    name = "/goal"
    aliases = ("/goalmode", "/g")
    help = "Autonomously work toward a goal end-to-end (/goal <goal>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        goal = ctx.args.strip()

        if not ctx.app.goal_mode:
            ctx.app._enter_goal_mode()

        if not goal:
            ctx.ui.info(
                "Goal Mode engaged! Type your goal as a message now, "
                "or /chat to exit."
            )
            return CommandResult()

        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()

        from ..goal_mode import GoalMode

        run = GoalMode(ctx.app).run(goal)

        # Summary.
        rounds = sum(1 for s in run.steps if s.kind == "execute")
        if run.cancelled:
            ctx.ui.warn(f"Goal Mode stopped by user after {rounds} round(s).")
        elif run.complete:
            ctx.ui.success(
                f"Goal achieved in {rounds} execution round(s). "
                "Still in Goal Mode \u2014 type more goals or /chat to exit."
            )
        else:
            ctx.ui.warn(
                f"Goal not fully verified after {rounds} round(s). "
                "Type a refined goal to continue."
            )

        ctx.ui.status_bar(
            ctx.config.provider,
            ctx.config.resolved_model(),
            ctx.app.conversation.token_estimate(),
        )
        return CommandResult()
