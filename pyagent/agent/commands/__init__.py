"""Modular slash-command framework.

Each command is a small class registered in the ``CommandRegistry``. The App
dispatches ``/name args`` to the matching command. This keeps command logic out
of the main loop and makes adding commands trivial.
"""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult
from .registry import CommandRegistry, build_command_registry

__all__ = ["Command", "CommandContext", "CommandResult", "CommandRegistry", "build_command_registry"]
