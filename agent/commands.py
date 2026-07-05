"""Command registry decoupling command names from their handlers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[..., str]
    aliases: tuple[str, ...] = ()


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command
        for alias in command.aliases:
            self._commands[alias] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def names(self) -> list[str]:
        return sorted({c.name for c in self._commands.values()})

    def all(self) -> list[Command]:
        seen = set()
        result = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result

    def execute(self, name: str, *args, **kwargs) -> str:
        cmd = self.get(name)
        if cmd is None:
            return f"Unknown command: {name}"
        return cmd.handler(*args, **kwargs)
