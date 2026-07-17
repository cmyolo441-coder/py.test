"""UI-oriented slash commands: themes, keyboard shortcuts."""

from __future__ import annotations

from .. import themes
from .base import Command, CommandContext, CommandResult


class ThemeCommand(Command):
    name = "/theme"
    help = "Switch UI theme (default/neon/pastel/matrix)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.info(f"Current: {themes.current().name}. Options: {', '.join(themes.names())}")
            return CommandResult()
        if themes.set_theme(ctx.args):
            ctx.ui.reset_session()
            ctx.ui.success(f"Theme set to {themes.current().name}")
        else:
            ctx.ui.error(f"Unknown theme '{ctx.args}'. Options: {', '.join(themes.names())}")
        return CommandResult()


class SpinnerCommand(Command):
    name = "/spinner"
    help = "Change the thinking spinner style"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.info("Spinner is built-in (braille). No options to change.")
        return CommandResult()


class KeysCommand(Command):
    name = "/keys"
    aliases = ("/shortcuts",)
    help = "Show keyboard shortcuts"

    def run(self, ctx: CommandContext) -> CommandResult:
        from rich.table import Table
        from rich.box import ROUNDED
        theme = themes.current()
        table = Table(title="Keyboard Shortcuts", border_style=theme.accent, box=ROUNDED)
        table.add_column("Key", style=f"bold {theme.accent2}")
        table.add_column("Action", style=theme.text)
        for k, a in [
            ("Enter", "Send message"),
            ("Alt+Enter", "Send multiline message"),
            ("Ctrl+J", "Insert newline"),
            ("Esc", "Stop model response"),
            ("/", "Open command menu"),
            ("Ctrl+C", "Cancel input"),
            ("Ctrl+D", "Exit"),
        ]:
            table.add_row(k, a)
        ctx.ui.console.print(table)
        return CommandResult()
