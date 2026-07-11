"""UI-oriented slash commands: themes, spinners, overlays, effects."""

from __future__ import annotations

from .. import effects, themes
from .base import Command, CommandContext, CommandResult


class ThemeCommand(Command):
    name = "/theme"
    help = "Switch UI theme (neon/cyberpunk/pastel/matrix/solarized)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.list_themes(themes.current().name)
            ctx.ui.info("Usage: /theme <name>")
            return CommandResult()
        if themes.set_theme(ctx.args):
            ctx.ui.reset_session()  # restyle prompt box with the new palette
            ctx.ui.success(f"Theme set to {themes.current().name}")
            ctx.ui.list_themes(themes.current().name)
        else:
            ctx.ui.error(f"Unknown theme '{ctx.args}'. Options: {', '.join(themes.names())}")
        return CommandResult()


class SpinnerCommand(Command):
    name = "/spinner"
    help = "Change the thinking spinner style"

    def run(self, ctx: CommandContext) -> CommandResult:
        options = list(effects.SPINNERS.keys())
        if not ctx.args:
            ctx.ui.info(f"Current spinner: {ctx.ui.spinner}. Options: {', '.join(options)}")
            return CommandResult()
        if ctx.args in effects.SPINNERS:
            ctx.ui.spinner = ctx.args
            ctx.ui.success(f"Spinner set to {ctx.args}")
        else:
            ctx.ui.error(f"Unknown spinner '{ctx.args}'. Options: {', '.join(options)}")
        return CommandResult()


class KeysCommand(Command):
    name = "/keys"
    aliases = ("/shortcuts",)
    help = "Show keyboard shortcuts overlay"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.shortcuts()
        return CommandResult()


class MatrixCommand(Command):
    name = "/matrix"
    help = "Play a Matrix rain animation"

    def run(self, ctx: CommandContext) -> CommandResult:
        effects.matrix_rain(ctx.ui.console, seconds=2.5)
        return CommandResult()


class StatusCommand(Command):
    name = "/status"
    help = "Show the live status bar"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.status_bar(
            ctx.config.provider,
            ctx.config.resolved_model(),
            ctx.app.conversation.token_estimate(),
        )
        return CommandResult()
