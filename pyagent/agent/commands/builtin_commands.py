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


class OrchestrateCommand(Command):
    name = "/orchestrate"
    aliases = ("/auto-orchestrate",)
    help = "Toggle auto-orchestration for complex tasks"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.config.auto_orchestrate = not ctx.config.auto_orchestrate
        ctx.config.save()
        state = "ON" if ctx.config.auto_orchestrate else "OFF"
        if ctx.config.auto_orchestrate:
            ctx.ui.success(f"Auto-orchestrate: {state} — complex tasks will use parallel sub-agents")
        else:
            ctx.ui.warn(f"Auto-orchestrate: {state} — all tasks use single agent")
        return CommandResult()


class SessionsCommand(Command):
    name = "/sessions"
    help = "List saved sessions"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..session_manager import get_session_manager

        mgr = get_session_manager()
        sessions = mgr.list_sessions(limit=20)

        if not sessions:
            ctx.ui.info("No saved sessions.")
            return CommandResult()

        ctx.ui.info("Recent sessions:")
        for s in sessions:
            summary = s.summary[:50] + "..." if len(s.summary) > 50 else s.summary
            ctx.ui.info(f"  {s.id}  {s.age_str:>10}  {s.provider}/{s.model}  {summary}")
        return CommandResult()


class SessionResumeCommand(Command):
    name = "/resume"
    help = "Resume a saved session (/resume <session-id>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /resume <session-id>")
            return CommandResult()

        from ..session_manager import get_session_manager

        mgr = get_session_manager()
        session = mgr.load(ctx.args)

        if session is None:
            ctx.ui.error(f"Session '{ctx.args}' not found.")
            return CommandResult()

        # Load messages into conversation
        ctx.app.conversation.messages = session.messages
        ctx.ui.success(f"Resumed session {ctx.args} ({session.info.message_count} messages)")
        return CommandResult()


class SessionDeleteCommand(Command):
    name = "/session-delete"
    help = "Delete a saved session (/session-delete <session-id>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /session-delete <session-id>")
            return CommandResult()

        from ..session_manager import get_session_manager

        mgr = get_session_manager()
        if mgr.delete(ctx.args):
            ctx.ui.success(f"Deleted session {ctx.args}")
        else:
            ctx.ui.error(f"Session '{ctx.args}' not found.")
        return CommandResult()


class HeadlessCommand(Command):
    name = "/headless"
    help = "Run a prompt in headless mode (no TUI)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /headless <prompt>")
            return CommandResult()

        from ..headless import HeadlessOptions, run_headless

        options = HeadlessOptions(
            timeout=120,
            output_format="text",
            verbose=True,
        )

        result = run_headless(ctx.args, options)

        if result.success:
            ctx.ui.console.print(result.output)
            ctx.ui.info(f"\nCompleted in {result.duration_s:.1f}s ({result.tool_calls} tool calls)")
        else:
            ctx.ui.error(f"Failed: {result.error}")
        return CommandResult()
