"""Fun full-terminal effects: matrix rain and confetti celebration."""
from __future__ import annotations

import random
import time

from rich.console import Console
from rich.text import Text

_MATRIX_CHARS = "01アイウエオカキクケコサシスセソタチツテト"


def matrix_rain(console: Console, duration: float = 2.0, speed: float = 0.05) -> None:
    """Render a short 'Matrix' style digital-rain animation."""
    width = console.width
    height = console.height - 1
    columns = [random.randint(-height, 0) for _ in range(width)]
    end = time.time() + duration
    try:
        while time.time() < end:
            lines = [[" "] * width for _ in range(height)]
            for x in range(width):
                y = columns[x]
                if 0 <= y < height:
                    lines[y][x] = random.choice(_MATRIX_CHARS)
                if 0 <= y - 1 < height:
                    lines[y - 1][x] = random.choice(_MATRIX_CHARS)
                columns[x] += 1
                if columns[x] > height and random.random() > 0.95:
                    columns[x] = 0
            console.clear()
            body = Text()
            for row in lines:
                body.append("".join(row) + "\n", style="bold green")
            console.print(body)
            time.sleep(speed)
    finally:
        console.clear()


def confetti(console: Console, bursts: int = 5, delay: float = 0.12) -> None:
    """A short celebratory confetti animation."""
    symbols = "🎉✨🎊⭐💥🎈🌟"
    colors = ["red", "yellow", "green", "cyan", "magenta", "bright_white"]
    width = console.width
    for _ in range(bursts):
        line = Text()
        for _ in range(width // 2):
            line.append(random.choice(symbols), style=random.choice(colors))
        console.print(line)
        time.sleep(delay)
    console.print(Text("  🎊  Done!  🎊", style="bold green"))
