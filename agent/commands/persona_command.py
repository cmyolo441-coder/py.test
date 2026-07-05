"""Persona command: switch the agent's system prompt at runtime."""

from __future__ import annotations

from ..personas import get_prompt, list_personas
from .base import Command, CommandContext, CommandResult


class PersonaCommand(Command):
    name = "/persona"
    help = "Switch persona (/persona coder|sysadmin|researcher|concise|default)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            current = ", ".join(list_personas())
            ctx.ui.info(f"Available personas: {current}")
            return CommandResult()
        prompt = get_prompt(ctx.args)
        if prompt is None:
            ctx.ui.error(f"Unknown persona: {ctx.args}")
            return CommandResult()
        ctx.config.system_prompt = prompt
        ctx.config.save()
        ctx.app.conversation.reset(prompt)
        ctx.ui.success(f"Persona set to '{ctx.args}' (conversation reset).")
        return CommandResult()
