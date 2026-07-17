"""Modular slash-command framework."""

from __future__ import annotations

from .base import Command, CommandContext, CommandResult
from .registry import CommandRegistry, build_command_registry

__all__ = ["Command", "CommandContext", "CommandResult", "CommandRegistry", "build_command_registry"]
