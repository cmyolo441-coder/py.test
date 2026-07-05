"""Animated spinner / status context manager built on rich.live."""
from __future__ import annotations

import threading
import time

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text


class ThinkingSpinner:
    """A context manager that shows an animated 'thinking' indicator."""

    def __init__(
        self,
        console: Console,
        message: str = "thinking",
        style: str = "bold cyan",
        spinner: str = "dots",
    ) -> None:
        self.console = console
        self.message = message
        self.style = style
        self.spinner = Spinner(spinner, text=Text(f" {message}…", style=style))
        self._live: Live | None = None

    def __enter__(self) -> "ThinkingSpinner":
        self._live = Live(self.spinner, console=self.console, refresh_per_second=12)
        self._live.start()
        return self

    def update(self, message: str) -> None:
        self.spinner.update(text=Text(f" {message}…", style=self.style))

    def __exit__(self, *exc) -> None:
        if self._live is not None:
            self._live.stop()
