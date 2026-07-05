"""Built-in command implementations."""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult


class ExitCommand(Command):
    name = "/exit"
    aliases = ("/quit", "/q")
    help = "Exit the agent"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.info("Goodbye! \U0001f44b")
        return CommandResult(exit_app=True)


class HelpCommand(Command):
    name = "/help"
    aliases = ("/?",)
    help = "Show all commands"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.help()
        return CommandResult()


class ToolsCommand(Command):
    name = "/tools"
    help = "List available tools"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.list_tools(ctx.app.registry.all())
        return CommandResult()


class ModelCommand(Command):
    name = "/model"
    help = "Switch model or show current"

    def run(self, ctx: CommandContext) -> CommandResult:
        if ctx.args:
            # If the chosen model belongs to another provider (e.g. a zyloo
            # model while on zen), switch to that provider automatically so
            # credentials/base-url line up.
            owner = ctx.config.provider_for_model(ctx.args)
            if owner and owner != ctx.config.provider:
                ctx.config.provider = owner
            ctx.config.model = ctx.args
            ctx.config.save()
            ctx.app.build_agent()
            ctx.ui.success(f"Model set to {ctx.args} ({ctx.config.provider})")
        else:
            ctx.ui.info(f"Current model: {ctx.config.resolved_model()}")
        return CommandResult()


class ModelsCommand(Command):
    name = "/models"
    help = "List preset models across all providers"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.list_all_models(ctx.config.all_known_models(), ctx.config.resolved_model())
        return CommandResult()


class ProviderCommand(Command):
    name = "/provider"
    help = "Switch provider"

    def run(self, ctx: CommandContext) -> CommandResult:
        if ctx.args:
            ctx.config.provider = ctx.args
            ctx.config.model = None
            ctx.config.save()
            if ctx.app.build_agent():
                ctx.ui.success(f"Provider set to {ctx.args} ({ctx.config.resolved_model()})")
        else:
            ctx.ui.info(f"Current provider: {ctx.config.provider}")
        return CommandResult()


class AutoCommand(Command):
    name = "/auto"
    help = "Toggle auto-approve for dangerous tools"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.config.auto_approve_tools = not ctx.config.auto_approve_tools
        ctx.config.save()
        state = "ON" if ctx.config.auto_approve_tools else "OFF"
        ctx.ui.warn(f"Auto-approve dangerous tools: {state}")
        return CommandResult()


class AnimCommand(Command):
    name = "/anim"
    help = "Toggle animations"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.animations = not ctx.ui.animations
        state = "ON" if ctx.ui.animations else "OFF"
        ctx.ui.success(f"Animations: {state}")
        return CommandResult()


class ClearCommand(Command):
    name = "/clear"
    help = "Clear conversation history"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.app.conversation.reset(ctx.config.system_prompt)
        ctx.ui.success("Conversation cleared.")
        return CommandResult()


class SaveCommand(Command):
    name = "/save"
    help = "Save conversation to disk"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.app.conversation.save()
        ctx.ui.success("Conversation saved.")
        return CommandResult()


class TokensCommand(Command):
    name = "/tokens"
    help = "Show estimated context size"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.info(f"~{ctx.app.conversation.token_estimate()} tokens in context")


class ChatCommand(Command):
    name = "/chat"
    aliases = ("/normal", "/exitgoal")
    help = "Exit Goal Mode and return to normal chat"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.app.goal_mode:
            ctx.ui.info("Already in normal chat mode.")
            return CommandResult()
        ctx.app._exit_goal_mode()
        ctx.ui.success("Exited Goal Mode. Back to normal chat.")
        return CommandResult()
        return CommandResult()
