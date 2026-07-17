"""Command registry: resolves a token to its Command instance."""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult
from .builtin_commands import (
    AnimCommand,
    AutoCommand,
    ChatCommand,
    ClearCommand,
    ExitCommand,
    HelpCommand,
    ModelCommand,
    ModelsCommand,
    ProviderCommand,
    SaveCommand,
    TokensCommand,
    ToolsCommand,
)
from .config_command import ConfigCommand
from .persona_command import PersonaCommand
from .session_commands import ExportCommand
from .ui_commands import (
    KeysCommand,
    SpinnerCommand,
    ThemeCommand,
)


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in getattr(cmd, "aliases", ()):
            self._commands[alias] = cmd

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        seen: set[int] = set()
        result: list[Command] = []
        for cmd in self._commands.values():
            if id(cmd) not in seen:
                seen.add(id(cmd))
                result.append(cmd)
        return result

    def dispatch(self, app, raw_input: str) -> CommandResult:
        parts = raw_input.split(None, 1)
        name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        cmd = self.get(name)
        if cmd is None:
            from ..ui import UI
            app.ui.warn(f"Unknown command: {name}. Type /help for available commands.")
            return CommandResult()
        ctx = CommandContext(app=app, raw=raw_input, args=args)
        return cmd.run(ctx)


def build_command_registry() -> CommandRegistry:
    reg = CommandRegistry()
    for cmd_class in (
        ExitCommand,
        HelpCommand,
        ToolsCommand,
        ClearCommand,
        SaveCommand,
        TokensCommand,
        AutoCommand,
        AnimCommand,
        ChatCommand,
        ModelCommand,
        ModelsCommand,
        ProviderCommand,
        ConfigCommand,
        PersonaCommand,
        ExportCommand,
        ThemeCommand,
        SpinnerCommand,
        KeysCommand,
    ):
        reg.register(cmd_class())
    return reg
