"""Base types for the command framework."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid circular imports at runtime
    from ..app import App


@dataclass
class CommandContext:
    """Everything a command needs to act on the running application."""

    app: App
    raw: str
    args: str

    @property
    def config(self) -> Any:
        return self.app.config

    @property
    def ui(self) -> Any:
        return self.app.ui


@dataclass
class CommandResult:
    exit_app: bool = False
    handled: bool = True


class Command(ABC):
    name: str = ""
    aliases: tuple[str, ...] = ()
    help: str = ""

    @abstractmethod
    def run(self, ctx: CommandContext) -> CommandResult:
        raise NotImplementedError

    def matches(self, token: str) -> bool:
        return token == self.name or token in self.aliases
