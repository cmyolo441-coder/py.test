"""Session-related commands: export the current conversation."""

from __future__ import annotations

import time

from ..session import Session, export_json, export_markdown
from .base import Command, CommandContext, CommandResult


class ExportCommand(Command):
    name = "/export"
    help = "Export conversation to markdown (/export md|json [path])"

    def run(self, ctx: CommandContext) -> CommandResult:
        parts = ctx.args.split()
        fmt = parts[0] if parts else "md"
        default_name = f"session-{int(time.time())}.{ 'md' if fmt == 'md' else 'json' }"
        path = parts[1] if len(parts) > 1 else default_name

        session = Session(
            title="Exported conversation",
            provider=ctx.config.provider,
            model=ctx.config.resolved_model(),
            messages=ctx.app.conversation.messages,
        )
        try:
            if fmt == "json":
                out = export_json(session, path)
            else:
                out = export_markdown(session, path)
        except OSError as exc:
            ctx.ui.error(f"Export failed: {exc}")
            return CommandResult()
        ctx.ui.success(f"Exported to {out}")
        return CommandResult()
