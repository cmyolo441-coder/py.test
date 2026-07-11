"""Real streaming that interleaves live text output with tool execution.

The StreamingSession renders assistant tokens as they arrive and, when the
model requests a tool, pauses the stream, runs the tool, shows its result, and
resumes — all within one visible flow.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

ToolExecutor = Callable[[str, Any], str]


class StreamingSession:
    """Coordinates live streaming output with inline tool calls."""

    def __init__(self, console: Console, tool_executor: ToolExecutor) -> None:
        self.console = console
        self.tool_executor = tool_executor

    def render_stream(self, chunks: Iterator[str], title: str = "assistant") -> str:
        """Render streaming text chunks live as markdown and return full text."""
        full = ""
        with Live(console=self.console, refresh_per_second=20) as live:
            for chunk in chunks:
                full += chunk
                live.update(
                    Panel(Markdown(full), title=title, title_align="left",
                          border_style="green")
                )
        return full

    def run_tools(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Execute tool calls, printing each result inline, return tool messages."""
        messages = []
        for tc in tool_calls:
            output = self.tool_executor(tc["name"], tc.get("arguments", {}))
            self.console.print(
                Panel(output, title=f"🔧 {tc['name']}", title_align="left",
                      border_style="magenta")
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.get("id", ""), "content": output}
            )
        return messages

    def interleaved(
        self,
        provider_round: Callable[[list[dict[str, Any]]], dict[str, Any]],
        messages: list[dict[str, Any]],
        stream_fn: Callable[[list[dict[str, Any]]], Iterator[str]],
        max_rounds: int = 5,
    ) -> str:
        """Loop: run tools until the model produces a final streamed answer."""
        for _ in range(max_rounds):
            result = provider_round(messages)
            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                # No tools needed — stream the final answer live.
                return self.render_stream(stream_fn(messages))
            messages.append(
                {"role": "assistant", "content": result.get("content", ""),
                 "tool_calls": [
                     {"id": tc["id"], "type": "function",
                      "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                     for tc in tool_calls
                 ]}
            )
            messages.extend(self.run_tools(tool_calls))
        return "(reached max tool rounds)"
