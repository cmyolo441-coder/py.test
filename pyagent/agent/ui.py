"""Clean terminal UI for the agent — Rich + prompt_toolkit."""

from __future__ import annotations

import threading
import time
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PTStyle
from rich.align import Align
from rich.box import ROUNDED
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import themes
from .config import PROMPT_HISTORY_FILE


class SlashCompleter(Completer):
    """Live fuzzy-matching dropdown for slash commands."""

    def __init__(self, source=None) -> None:
        self._source = source

    def _items(self) -> list[tuple[str, str]]:
        if self._source is not None:
            try:
                items = self._source()
                if items:
                    return items
            except Exception:
                pass
        return []

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        word = text.split()[0] if text.split() else text
        query = word.lower().lstrip("/").replace("-", "")
        shown = 0
        for cmd, desc in self._items():
            hay = cmd.lower().lstrip("/").replace("-", "")
            if not query or query in hay or all(ch in iter(hay) for ch in query):
                yield Completion(cmd, start_position=-len(word), display=cmd, display_meta=desc)
                shown += 1
                if shown >= 10:
                    break


class UI:
    def __init__(self, animations: bool = True) -> None:
        self.console = Console()
        self.animations = animations
        self._session: PromptSession | None = None
        self._command_source = None

    def set_command_source(self, source) -> None:
        self._command_source = source
        self._session = None

    def commands_list(self) -> list[tuple[str, str]]:
        if self._command_source is not None:
            try:
                items = self._command_source()
                if items:
                    return items
            except Exception:
                pass
        return []

    @property
    def session(self) -> PromptSession:
        if self._session is None:
            theme = themes.current()
            kb = self._build_key_bindings()
            self._session = PromptSession(
                history=FileHistory(str(PROMPT_HISTORY_FILE)),
                key_bindings=kb,
                completer=SlashCompleter(self.commands_list),
                complete_while_typing=True,
                multiline=False,
                mouse_support=False,
                wrap_lines=True,
                enable_history_search=False,
                auto_suggest=AutoSuggestFromHistory(),
                complete_in_thread=True,
                reserve_space_for_menu=6,
                bottom_toolbar=self._bottom_toolbar,
                style=PTStyle.from_dict({
                    "prompt": f"bold {theme.accent}",
                    "bottom-toolbar": f"bg:#181825 {theme.dim}",
                    "completion-menu.completion": f"bg:#1e1e2e {theme.text}",
                    "completion-menu.completion.current": f"bg:{theme.accent} #000000 bold",
                    "completion-menu.meta.completion": f"bg:#181825 {theme.dim}",
                    "completion-menu.meta.completion.current": f"bg:{theme.accent2} #000000",
                }),
            )
        return self._session

    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter", filter=has_completions & completion_is_selected)
        def _(event):
            buff = event.current_buffer
            if buff.complete_state and buff.complete_state.current_completion:
                buff.apply_completion(buff.complete_state.current_completion)

        @kb.add("enter", filter=has_completions)
        def _(event):
            buff = event.current_buffer
            if buff.complete_state and buff.complete_state.current_completion:
                buff.apply_completion(buff.complete_state.current_completion)
            else:
                buff.complete_next()

        @kb.add("enter")
        def _(event):
            buff = event.current_buffer
            if "\n" in buff.text:
                buff.insert_text("\n")
            else:
                buff.validate_and_handle()

        @kb.add("escape", "enter")
        def _(event):
            event.current_buffer.validate_and_handle()

        @kb.add("c-j")
        def _(event):
            event.current_buffer.insert_text("\n")

        return kb

    def _bottom_toolbar(self) -> HTML:
        return HTML(
            " <b>Enter</b> send │ <b>Ctrl+J</b> newline │ "
            "<b>Alt+Enter</b> send multiline │ <b>Esc</b> stop │ "
            "<b>Ctrl+C</b> cancel "
        )

    def reset_session(self) -> None:
        self._session = None

    # -- chrome ---------------------------------------------------------
    def show_banner(self, provider: str, model: str) -> None:
        theme = themes.current()
        self.console.print()
        self.console.print(f"[bold {theme.accent}]Terminal AI Agent[/]  [dim]v2.0[/]")
        self.console.print(f"[{theme.dim}]provider: {provider}/{model}  │  /help for commands[/]")
        self.console.print()

    def info(self, text: str) -> None:
        theme = themes.current()
        self.console.print(f"[{theme.accent2}]ℹ[/] {text}")

    def success(self, text: str) -> None:
        theme = themes.current()
        self.console.print(f"[{theme.ok}]✔[/] {text}")

    def warn(self, text: str) -> None:
        theme = themes.current()
        self.console.print(f"[{theme.warn}]⚠[/] {text}")

    def error(self, text: str) -> None:
        theme = themes.current()
        self.console.print(f"[{theme.err}]✖[/] {text}")

    # -- status bar -----------------------------------------------------
    def status_bar(self, provider: str, model: str, tokens: int) -> None:
        theme = themes.current()
        bar = Text()
        bar.append(f" {provider}/{model} ", style=f"bold {theme.ok}")
        bar.append("│ ", style=theme.dim)
        bar.append(f"~{tokens} tokens", style=f"bold {theme.warn}")
        self.console.print(bar)

    # -- input ----------------------------------------------------------
    def prompt(self) -> str:
        theme = themes.current()
        try:
            text = self.session.prompt(HTML(f'<prompt>❯ </prompt>'))
        except (EOFError, KeyboardInterrupt):
            raise
        return text

    def confirm(self, question: str) -> bool:
        answer = self.session.prompt(HTML(f'<prompt>{question} [y/N] </prompt>')).strip().lower()
        return answer in {"y", "yes"}

    # -- tool rendering -------------------------------------------------
    def tool_panel(self, name: str, arguments: dict[str, Any]) -> None:
        theme = themes.current()
        args_text = Text()
        if arguments:
            for k, v in arguments.items():
                val = repr(v)
                if len(val) > 300:
                    val = val[:300] + "…"
                args_text.append(f"  {k}", style=f"bold {theme.accent2}")
                args_text.append(" = ", style=theme.dim)
                args_text.append(f"{val}\n", style=theme.text)
        else:
            args_text.append("  (no args)", style=theme.dim)
        header = Text.assemble(("⚡ ", theme.warn), (name, f"bold {theme.accent2}"))
        self.console.print(
            Panel(args_text, title=header, border_style=theme.accent2, box=ROUNDED, expand=False, padding=(0, 1))
        )

    def tool_result(self, name: str, output: str, success: bool) -> None:
        theme = themes.current()
        color = theme.ok if success else theme.err
        icon = "✔" if success else "✖"
        preview = output if len(output) < 1600 else output[:1600] + "\n… [truncated]"
        header = Text.assemble((f"{icon} ", color), (name, f"bold {color}"))
        self.console.print(
            Panel(Text(preview, style=theme.text), title=header, border_style=color, box=ROUNDED, expand=False, padding=(0, 1))
        )

    def help(self) -> None:
        theme = themes.current()
        table = Table(title="Commands", border_style=theme.accent, box=ROUNDED)
        table.add_column("Command", style=f"bold {theme.accent2}")
        table.add_column("Description", style=theme.text)
        for cmd, desc in self.commands_list():
            table.add_row(cmd, desc)
        self.console.print(table)

    def list_models(self, provider: str, models: list[str], current: str) -> None:
        theme = themes.current()
        if not models:
            self.info(f"No preset models for '{provider}'. Use /model <name>.")
            return
        table = Table(title=f"Models · {provider}", border_style=theme.accent2, box=ROUNDED)
        table.add_column("", style=theme.ok)
        table.add_column("Model", style=f"bold {theme.accent2}")
        for m in models:
            table.add_row("●" if m == current else " ", m)
        self.console.print(table)

    def list_all_models(self, pairs: list[tuple[str, str]], current: str) -> None:
        theme = themes.current()
        if not pairs:
            self.info("No preset models configured.")
            return
        table = Table(title="All Models", border_style=theme.accent2, box=ROUNDED)
        table.add_column("", style=theme.ok)
        table.add_column("Provider", style=f"bold {theme.accent}")
        table.add_column("Model", style=f"bold {theme.accent2}")
        for prov, m in pairs:
            table.add_row("●" if m == current else " ", prov, m)
        self.console.print(table)

    def list_tools(self, tools: list[Any]) -> None:
        theme = themes.current()
        table = Table(title="Available Tools", border_style=theme.accent2, box=ROUNDED)
        table.add_column("Tool", style=f"bold {theme.ok}")
        table.add_column("Danger", style=theme.warn)
        table.add_column("Description")
        for t in tools:
            table.add_row(t.name, "⚠ yes" if t.dangerous else "no", t.description[:70])
        self.console.print(table)

    # -- assistant streaming --------------------------------------------
    def stream_response(self) -> ResponseRenderer:
        return ResponseRenderer(self.console, animations=self.animations)


class ResponseRenderer:
    """Live streaming renderer with thinking state."""

    _STREAM_FPS = 30

    def __init__(self, console: Console, animations: bool = True) -> None:
        self.console = console
        self.animations = animations
        self._buffer = ""
        self._thinking = False
        self._think_thread: threading.Thread | None = None
        self._cancelled = False

    def start_thinking(self, label: str = "thinking") -> None:
        self._thinking = True
        if self.animations:
            self._think_thread = threading.Thread(target=self._spin, args=(label,), daemon=True)
            self._think_thread.start()

    def _spin(self, label: str) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while self._thinking:
            frame = frames[i % len(frames)]
            print(f"\r  {frame} {label}…", end="", flush=True)
            time.sleep(0.1)
            i += 1
        print("\r" + " " * 40 + "\r", end="", flush=True)

    def on_delta(self, chunk: str) -> None:
        if self._thinking:
            self._thinking = False
            if self._think_thread:
                self._think_thread.join(timeout=0.5)
        self._buffer += chunk

    def mark_cancelled(self) -> None:
        self._cancelled = True

    def finish(self, full_text: str | None = None) -> None:
        self._thinking = False
        if self._think_thread:
            self._think_thread.join(timeout=0.5)
        text = full_text or self._buffer
        if text:
            theme = themes.current()
            self.console.print()
            self.console.print(Markdown(text))
            self.console.print()
        self._buffer = ""
