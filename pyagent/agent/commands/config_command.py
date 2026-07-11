"""Config command: view and tweak runtime settings."""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult


class ConfigCommand(Command):
    name = "/config"
    help = "Show current configuration (/config temperature 0.3 to set)"

    def run(self, ctx: CommandContext) -> CommandResult:
        cfg = ctx.config
        if not ctx.args:
            lines = [
                f"provider          = {cfg.provider}",
                f"model             = {cfg.resolved_model()}",
                f"temperature       = {cfg.temperature}",
                f"max_tokens        = {cfg.max_tokens}",
                f"stream            = {cfg.stream}",
                f"enable_tools      = {cfg.enable_tools}",
                f"auto_approve_tools= {cfg.auto_approve_tools}",
                f"max_tool_iterations = {cfg.max_tool_iterations}",
            ]
            ctx.ui.console.print("\n".join(lines))
            return CommandResult()

        parts = ctx.args.split()
        if len(parts) != 2:
            ctx.ui.error("Usage: /config <key> <value>")
            return CommandResult()
        key, value = parts
        if not hasattr(cfg, key):
            ctx.ui.error(f"Unknown config key: {key}")
            return CommandResult()
        current = getattr(cfg, key)
        try:
            if isinstance(current, bool):
                new = value.lower() in {"1", "true", "yes", "on"}
            elif isinstance(current, int):
                new = int(value)
            elif isinstance(current, float):
                new = float(value)
            else:
                new = value
        except ValueError:
            ctx.ui.error(f"Invalid value for {key}: {value}")
            return CommandResult()
        setattr(cfg, key, new)
        cfg.save()
        ctx.app.build_agent()
        ctx.ui.success(f"{key} = {new}")
        return CommandResult()
