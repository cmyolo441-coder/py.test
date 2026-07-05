"""Chat bubble rendering: distinct styled cards for user and assistant."""
from __future__ import annotations

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text


def user_bubble(text: str) -> Padding:
    panel = Panel(
        Text(text, style="white"),
        title="🧑 you",
        title_align="right",
        border_style="bright_blue",
        padding=(0, 2),
    )
    # Indent user messages to the right for a chat-app feel.
    return Padding(panel, (0, 0, 0, 12))


def assistant_bubble(text: str) -> Padding:
    panel = Panel(
        Markdown(text),
        title="🤖 assistant",
        title_align="left",
        border_style="green",
        padding=(0, 2),
    )
    return Padding(panel, (0, 12, 0, 0))


def tool_bubble(name: str, output: str) -> Padding:
    panel = Panel(
        Text(output, style="grey70"),
        title=f"🔧 {name}",
        title_align="left",
        border_style="magenta",
        padding=(0, 2),
    )
    return Padding(panel, (0, 8, 0, 4))


def render_conversation(console: Console, messages: list[dict]) -> None:
    renderables = []
    for m in messages:
        if m["role"] == "user":
            renderables.append(user_bubble(m["content"]))
        elif m["role"] == "assistant":
            renderables.append(assistant_bubble(m["content"]))
    console.print(Group(*renderables))
