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
from .session_commands import ExportCommand
from .persona_command import PersonaCommand
from .config_command import ConfigCommand
from .goal_command import GoalCommand
from .feature_commands import build_feature_commands
from .ui_commands import (
    KeysCommand,
    MatrixCommand,
    SpinnerCommand,
    StatusCommand,
    ThemeCommand,
)
from .enterprise_commands import build_enterprise_commands
from .v3_commands import build_v3_commands


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: list[Command] = []

    def register(self, command: Command) -> None:
        self._commands.append(command)

    def all(self) -> list[Command]:
        return list(self._commands)

    def resolve(self, token: str) -> Command | None:
        for cmd in self._commands:
            if cmd.matches(token):
                return cmd
        return None

    def dispatch(self, app, raw: str) -> CommandResult:
        parts = raw.split(maxsplit=1)
        token = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        command = self.resolve(token)
        if command is None:
            app.ui.error(f"Unknown command: {token} (try /help)")
            return CommandResult(handled=False)
        ctx = CommandContext(app=app, raw=raw, args=args)
        return command.run(ctx)


def build_command_registry() -> CommandRegistry:
    registry = CommandRegistry()
    for cmd in (
        HelpCommand(),
        ExitCommand(),
        ToolsCommand(),
        ModelCommand(),
        ModelsCommand(),
        ProviderCommand(),
        AutoCommand(),
        AnimCommand(),
        ClearCommand(),
        SaveCommand(),
        TokensCommand(),
        ExportCommand(),
        PersonaCommand(),
        ConfigCommand(),
        ThemeCommand(),
        SpinnerCommand(),
        KeysCommand(),
        MatrixCommand(),
        StatusCommand(),
        GoalCommand(),
        ChatCommand(),
    ):
        registry.register(cmd)
    # Register the 40 additional enterprise feature commands, skipping any
    # whose name/alias already exists to avoid clobbering built-ins.
    existing: set[str] = set()
    for cmd in registry.all():
        existing.add(cmd.name)
        existing.update(cmd.aliases)
    for cmd in build_feature_commands():
        tokens = {cmd.name, *cmd.aliases}
        if tokens & existing:
            continue
        registry.register(cmd)
        existing.update(tokens)
    # Register the 40 new enterprise commands (v2), skipping collisions.
    for cmd in build_enterprise_commands():
        tokens = {cmd.name, *cmd.aliases}
        if tokens & existing:
            continue
        registry.register(cmd)
        existing.update(tokens)
    # Register the v3 enterprise commands, skipping collisions.
    for cmd in build_v3_commands():
        tokens = {cmd.name, *cmd.aliases}
        if tokens & existing:
            continue
        registry.register(cmd)
        existing.update(tokens)
    return registry
