"""Slash command for Project Brain workspace intelligence."""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult


class BrainCommand(Command):
    name = "/brain"
    aliases = ("/radar", "/project-brain")
    help = "/brain [scan|timeline|next <goal>|error <traceback>] — project radar, timeline, and next actions"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..project_brain import ProjectBrain

        brain = ProjectBrain(".")
        args = ctx.args.strip()
        if not args or args == "scan":
            brain.scan()
            ctx.ui.print(brain.render_radar())
            return CommandResult()
        if args == "timeline":
            ctx.ui.print(brain.timeline())
            return CommandResult()
        if args.startswith("next"):
            goal = args[4:].strip()
            brain.scan()
            ctx.ui.print(brain.suggest_next_actions(goal=goal))
            return CommandResult()
        if args.startswith("error"):
            error_text = args[5:].strip()
            brain.scan()
            ctx.ui.print(brain.link_error(error_text))
            ctx.ui.print("\n" + brain.suggest_next_actions(error_text=error_text))
            return CommandResult()
        brain.scan()
        ctx.ui.print(brain.suggest_next_actions(goal=args))
        return CommandResult()
