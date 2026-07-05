"""Rich-powered terminal UI for the agent â€” the "100x better than Cursor CLI" edition.

Highlights:
- Animated flowing-gradient banner + typewriter subtitle
- A framed, glowing prompt box (prompt_toolkit) with live hints & multiline
- Shimmering, orbiting "thinking" indicator with rotating status words
- Animated tool-call cards and streamed markdown answers
"""

from __future__ import annotations

import threading
import time
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PTStyle
from rich.align import Align
from rich.box import HEAVY, ROUNDED
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import effects, themes
from .config import PROMPT_HISTORY_FILE

# Slash commands surfaced in the live completion menu.
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/goal", "\U0001f3af Goal Mode: autonomously achieve a goal (ultra effort)"),
    ("/help", "Show all commands"),
    ("/model", "Switch model or show current"),
    ("/models", "List preset models for the provider"),
    ("/provider", "Switch provider (zen/openai/anthropic/â€¦)"),
    ("/theme", "Switch UI theme (neon/cyberpunk/pastel/matrix)"),
    ("/tools", "List available tools"),
    ("/persona", "Switch assistant persona"),
    ("/spinner", "Change the thinking spinner style"),
    ("/matrix", "Play a Matrix rain animation"),
    ("/keys", "Show keyboard shortcuts overlay"),
    ("/auto", "Toggle auto-approve for dangerous tools"),
    ("/anim", "Toggle animations on/off"),
    ("/config", "Show current configuration"),
    ("/export", "Export the conversation"),
    ("/clear", "Clear conversation history"),
    ("/save", "Save conversation to disk"),
    ("/tokens", "Show estimated context size"),
    ("/effort", "Set effort level (godmode, enterprise...)"),
    ("/autorun", "Run a task with the autonomous engine"),
    ("/cat", "Print a file's contents"),
    ("/tree", "Show a directory tree"),
    ("/grep", "Search files for a regex"),
    ("/run", "Run a safe shell command"),
    ("/retry", "Re-send your last message"),
    ("/copy", "Copy the last reply to clipboard"),
    ("/exit", "Exit the agent"),
]


class SlashCompleter(Completer):
    """Live floating dropdown of slash commands with descriptions.

    Pulls the full command list from a provider callback when one is supplied,
    so EVERY registered command (name + aliases) shows up in the ``/`` menu.
    Falls back to the static list if no provider is set.
    """

    def __init__(self, source=None) -> None:  # noqa: ANN001
        self._source = source

    def _items(self) -> list[tuple[str, str]]:
        if self._source is not None:
            try:
                items = self._source()
                if items:
                    return items
            except Exception:  # noqa: BLE001 - never break input on error
                pass
        return SLASH_COMMANDS

    def get_completions(self, document, complete_event):  # noqa: ANN001
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        word = text.split()[0] if text.split() else text
        for cmd, desc in self._items():
            if cmd.startswith(word):
                yield Completion(
                    cmd,
                    start_position=-len(word),
                    display=cmd,
                    display_meta=desc,
                )


# Backwards-compatible module colors (default to the active theme).
def _t() -> themes.Theme:
    return themes.current()


ACCENT = themes.current().accent
ACCENT2 = themes.current().accent2
OK = themes.current().ok
WARN = themes.current().warn
ERR = themes.current().err
DIM = themes.current().dim


class UI:
    def __init__(self, animations: bool = True) -> None:
        self.console = Console()
        self.animations = animations
        self.spinner = "braille"
        self.goal_mode = False
        self._bindings = self._build_key_bindings()
        self._session: PromptSession | None = None
        # Callback returning [(command, description), ...] for the live menu +
        # /help. Set by the App so every registered command is discoverable.
        self._command_source = None

    def set_command_source(self, source) -> None:  # noqa: ANN001
        """Register a callable that yields (command, description) pairs."""
        self._command_source = source
        self._session = None  # rebuild session so the completer picks it up

    def commands_list(self) -> list[tuple[str, str]]:
        """All slash commands (from the live source, or the static fallback)."""
        if self._command_source is not None:
            try:
                items = self._command_source()
                if items:
                    return items
            except Exception:  # noqa: BLE001
                pass
        return SLASH_COMMANDS

    # -- key bindings (Cursor-style smart multiline + safe paste) -------
    def _build_key_bindings(self) -> KeyBindings:
        """Build key bindings that make Enter smart and paste safe.

        Rules (mirrors modern AI CLIs / Cursor):
          - Enter on a single-line buffer  -> submit.
          - Enter when the buffer already spans multiple lines (e.g. after a
            multi-line paste) -> insert a newline instead of submitting, so a
            long pasted block is NEVER auto-sent by a trailing newline.
          - Alt+Enter / Esc then Enter     -> force submit from anywhere.
          - Ctrl+J / Shift+Enter           -> always insert a newline.
          - Enter while the completion menu is open -> accept the completion.
        """
        kb = KeyBindings()

        @kb.add("enter", filter=has_completions & completion_is_selected)
        def _(event):  # noqa: ANN001
            """Accept the highlighted slash-command completion."""
            event.current_buffer.complete_state = None

        @kb.add("enter", filter=has_completions)
        def _(event):  # noqa: ANN001
            """First Enter selects the current completion, does not submit."""
            buff = event.current_buffer
            if buff.complete_state and buff.complete_state.current_completion:
                buff.apply_completion(buff.complete_state.current_completion)
            else:
                buff.complete_next()

        @kb.add("enter")
        def _(event):  # noqa: ANN001
            """Smart Enter: submit single-line, newline for multi-line/paste.

            A long pasted block becomes multi-line text, so Enter inserts a
            newline instead of submitting. This prevents accidental auto-send
            of pasted content. Use Alt+Enter to submit.
            """
            buff = event.current_buffer
            if "\n" in buff.text:
                buff.insert_text("\n")
            else:
                buff.validate_and_handle()

        @kb.add("escape", "enter")  # Alt/Option + Enter -> force submit
        def _(event):  # noqa: ANN001
            event.current_buffer.validate_and_handle()

        @kb.add("c-j")  # Ctrl+J always inserts a newline
        def _(event):  # noqa: ANN001
            event.current_buffer.insert_text("\n")

        @kb.add("pagedown")
        def _(event):  # noqa: ANN001
            buff = event.current_buffer
            if buff.complete_state:
                buff.complete_next()
            else:
                buff.start_completion(select_first=True)

        @kb.add("pageup")
        def _(event):  # noqa: ANN001
            buff = event.current_buffer
            if buff.complete_state:
                buff.complete_previous()

        return kb

    # -- live theme accessors ------------------------------------------
    @property
    def accent(self) -> str:
        return themes.current().accent

    @property
    def accent2(self) -> str:
        return themes.current().accent2

    @property
    def ok(self) -> str:
        return themes.current().ok

    @property
    def warn(self) -> str:
        return themes.current().warn

    @property
    def err(self) -> str:
        return themes.current().err

    @property
    def dim(self) -> str:
        return themes.current().dim

    @property
    def session(self) -> PromptSession:
        """Lazily build the prompt session.

        Building it eagerly fails in non-interactive contexts (piped stdin,
        no console), so we defer creation until input is actually requested.
        """
        if self._session is None:
            theme = themes.current()
            self._session = PromptSession(
                history=FileHistory(str(PROMPT_HISTORY_FILE)),
                key_bindings=self._bindings,
                completer=SlashCompleter(self.commands_list),
                complete_while_typing=True,
                # Cursor-style editing: real multiline buffer so long/pasted
                # text stays editable and is never auto-submitted by a newline.
                multiline=True,
                # NOTE: enable_history_search is intentionally OFF. prompt_toolkit
                # force-disables complete_while_typing whenever history search is
                # on (see shortcuts/prompt.py), which would make the live `/`
                # command dropdown never appear. Ctrl+R still reverse-searches.
                # Mouse support is intentionally OFF: when enabled, prompt_toolkit
                # captures mouse events and the terminal's native click-drag text
                # selection / copy stops working. Keeping it off lets users
                # select and copy output (and prompt text) normally.
                mouse_support=False,
                wrap_lines=True,
                enable_history_search=False,
                bottom_toolbar=self._bottom_toolbar,
                prompt_continuation=self._prompt_continuation,
                style=PTStyle.from_dict(
                    {
                        "prompt": f"bold {theme.accent}",
                        "frame": theme.accent,
                        "hint": f"{theme.dim} italic",
                        "bottom-toolbar": f"bg:#181825 {theme.dim}",
                        "bottom-toolbar.text": f"bg:#181825 {theme.dim}",
                        "completion-menu.completion": f"bg:#1e1e2e {theme.text}",
                        "completion-menu.completion.current": f"bg:{theme.accent} #000000 bold",
                        "completion-menu.meta.completion": f"bg:#181825 {theme.dim}",
                        "completion-menu.meta.completion.current": f"bg:{theme.accent2} #000000",
                    }
                ),
            )
        return self._session

    def _bottom_toolbar(self) -> HTML:
        """Live hint bar under the prompt (Cursor-style)."""
        return HTML(
            " <b>Enter</b> send \u2502 <b>Alt+Enter</b> multi-line \u2502 "
            "<b>Esc</b> stop reply \u2502 <b>/</b> commands \u2502 "
            "<b>PgUp/PgDn</b> pick \u2502 <b>Ctrl+C</b> quit "
        )

    def _prompt_continuation(self, width: int, line_number: int, is_soft_wrap: bool) -> HTML:
        """Left gutter for continuation lines of a multi-line prompt."""
        if is_soft_wrap:
            return HTML("")
        return HTML('<frame>\u2502</frame> <hint>\u00b7 </hint>')

    def reset_session(self) -> None:
        """Drop the cached prompt session so a new theme restyles it."""
        self._session = None

    # -- chrome ---------------------------------------------------------
    def show_banner(self, provider: str, model: str) -> None:
        self.console.print()
        if self.animations:
            effects.animate_banner(self.console)
        else:
            self.console.print(
                Align.center(effects.gradient_text(effects.BANNER_ART, effects.GRADIENT_STOPS))
            )

        subtitle = "the world's most advanced terminal AI agent"
        if self.animations:
            effects.typewriter(self.console, subtitle, style="italic #22d3ee", delay=0.010)
        else:
            self.console.print(Align.center(Text(subtitle, style="italic #22d3ee")))

        status = Text.assemble(
            ("  \u25c6 provider ", self.dim), (provider, f"bold {self.ok}"),
            ("   \u25c6 model ", self.dim), (model, f"bold {self.ok}"),
            ("   \u25c6 theme ", self.dim), (themes.current().name, f"bold {self.accent}"),
        )
        self.console.print(Align.center(status))
        self.console.print(
            Align.center(Text("/help for commands \u00b7 Enter to send \u00b7 Alt+Enter for multi-line \u00b7 paste is safe \u00b7 /exit to quit", style=self.dim))
        )
        self.console.print(Align.center(Rule(style=self.accent)))
        self.console.print()

    def info(self, text: str) -> None:
        self.console.print(f"[{self.accent2}]\u2139[/] {text}")

    def success(self, text: str) -> None:
        self.console.print(f"[{self.ok}]\u2714[/] {text}")

    def warn(self, text: str) -> None:
        self.console.print(f"[{self.warn}]\u26a0[/] {text}")

    def error(self, text: str) -> None:
        self.console.print(f"[{self.err}]\u2716[/] {text}")

    # -- status bar -----------------------------------------------------
    def status_bar(self, provider: str, model: str, tokens: int, cost: float | None = None) -> None:
        """Render a one-line status bar: clock Â· provider Â· model Â· tokens Â· theme."""
        import datetime as _dt

        theme = themes.current()
        clock = _dt.datetime.utcnow().strftime("%H:%M:%S")
        bar = Text()
        bar.append(f" \u23f1 {clock} UTC ", style=f"bold {theme.accent2}")
        bar.append("\u2502", style=theme.dim)
        bar.append(f" \U0001f9e0 {provider}/{model} ", style=f"bold {theme.ok}")
        bar.append("\u2502", style=theme.dim)
        bar.append(f" \U0001f9ee {tokens} tok ", style=f"bold {theme.warn}")
        if cost is not None:
            bar.append("\u2502", style=theme.dim)
            bar.append(f" $ {cost:.4f} ", style=f"bold {theme.accent}")
        bar.append("\u2502", style=theme.dim)
        bar.append(f" \U0001f3a8 {theme.name} ", style=f"bold {theme.accent}")
        self.console.print(Panel(bar, border_style=theme.accent, box=ROUNDED, padding=0, expand=True))

    # -- input ----------------------------------------------------------
    def prompt(self) -> str:
        label = "you (GOAL MODE)" if self.goal_mode else "you"
        prompt_char = "\U0001f3af" if self.goal_mode else "\u276f"
        self.console.print(
            Text("\u256d\u2500 ", style=self.accent) + Text(label, style=f"bold {self.accent2}")
            + Text(" " + "\u2500" * 45, style=self.accent)
        )
        try:
            text = self.session.prompt(HTML(f'<frame>\u2502</frame> <prompt>{prompt_char} </prompt>'))
        finally:
            self.console.print(Text("\u2570" + "\u2500" * 50, style=self.accent))
        return text

    def confirm(self, question: str) -> bool:
        answer = self.session.prompt(HTML(f'<prompt>{question} [y/N] </prompt>')).strip().lower()
        return answer in {"y", "yes"}

    # -- chat bubbles ---------------------------------------------------
    def user_bubble(self, text: str) -> None:
        theme = themes.current()
        panel = Panel(
            Text(text, style=theme.text),
            title=Text("\U0001f464 you", style=f"bold {theme.user_bubble}"),
            title_align="right",
            border_style=theme.user_bubble,
            box=ROUNDED,
            padding=(0, 1),
        )
        self.console.print(Align.right(panel))

    def code_block(self, code: str, language: str = "python", title: str | None = None) -> None:
        """Render a syntax-highlighted code block."""
        theme = themes.current()
        syntax = Syntax(code, language, theme="monokai", line_numbers=True, word_wrap=True)
        self.console.print(
            Panel(
                syntax,
                title=Text(title or f"\U0001f4c4 {language}", style=f"bold {theme.accent2}"),
                border_style=theme.accent2,
                box=ROUNDED,
            )
        )

    # -- assistant streaming --------------------------------------------
    def stream_response(self) -> "ResponseRenderer":
        return ResponseRenderer(self.console, animations=self.animations, spinner=self.spinner)

    # -- overlays -------------------------------------------------------
    def shortcuts(self) -> None:
        theme = themes.current()
        table = Table(title="\u2328 Keyboard Shortcuts", border_style=theme.accent, title_style=f"bold {theme.accent}", box=ROUNDED)
        table.add_column("Key", style=f"bold {theme.accent2}")
        table.add_column("Action", style=theme.text)
        rows = [
            ("Enter", "Send (single-line) / newline when multi-line"),
            ("Alt+Enter", "Force-send a multi-line message"),
            ("Esc", "Stop the model's response mid-stream"),
            ("Ctrl+J", "Insert a newline"),
            ("Paste", "Long/multi-line paste is never auto-sent"),
            ("Mouse drag", "Select & copy output (native terminal copy)"),
            ("/", "Open the command menu (all commands)"),
            ("PgUp / PgDn", "Navigate the slash-command menu"),
            ("Tab / \u2191\u2193", "Complete / browse history"),
            ("Ctrl+R", "Reverse-search prompt history"),
            ("Ctrl+C", "Cancel current input"),
            ("Ctrl+D", "Exit the agent"),
            ("/goal", "Goal Mode: autonomous, ultra-effort execution"),
            ("/keys", "Show this overlay"),
            ("/theme", "Switch color theme live"),
        ]
        for k, a in rows:
            table.add_row(k, a)
        self.console.print(table)

    def list_themes(self, current: str) -> None:
        theme = themes.current()
        cards = []
        for name in themes.names():
            th = themes.THEMES[name]
            swatch = Text()
            for c in th.grad():
                swatch.append("\u2588\u2588", style=c)
            marker = "\u25cf " if name == current else "  "
            body = Group(
                Text(marker + name, style=f"bold {th.accent}"),
                swatch,
            )
            cards.append(Panel(body, border_style=th.accent, box=ROUNDED, padding=(0, 1)))
        self.console.print(Columns(cards, equal=True, expand=False))

    # -- tool rendering -------------------------------------------------
    def tool_panel(self, name: str, arguments: dict[str, Any]) -> None:
        theme = themes.current()
        args_text = Text()
        if arguments:
            for k, v in arguments.items():
                val = repr(v)
                if len(val) > 300:
                    val = val[:300] + "\u2026"
                args_text.append(f"  {k}", style=f"bold {theme.accent2}")
                args_text.append(" = ", style=theme.dim)
                args_text.append(f"{val}\n", style=theme.text)
        else:
            args_text.append("  (no args)", style=theme.dim)

        header = Text.assemble(("\u26a1 ", theme.warn), ("calling tool  ", theme.dim), (name, f"bold {theme.accent2}"))
        self.console.print(
            Panel(args_text, title=header, border_style=theme.accent2, box=ROUNDED, expand=False, padding=(0, 1))
        )

    def tool_result(self, name: str, output: str, success: bool) -> None:
        theme = themes.current()
        color = theme.ok if success else theme.err
        icon = "\u2714" if success else "\u2716"
        preview = output if len(output) < 1600 else output[:1600] + "\n\u2026 [truncated]"
        header = Text.assemble((f"{icon} ", color), (name, f"bold {color}"), ("  result", theme.dim))
        self.console.print(
            Panel(Text(preview, style=theme.text), title=header, border_style=color, box=ROUNDED, expand=False, padding=(0, 1))
        )

    def help(self) -> None:
        theme = themes.current()
        table = Table(title="\u2318 Commands", border_style=theme.accent, title_style=f"bold {theme.accent}", box=ROUNDED)
        table.add_column("Command", style=f"bold {theme.accent2}")
        table.add_column("Description", style=theme.text)
        for cmd, desc in self.commands_list():
            table.add_row(cmd, desc)
        self.console.print(table)

    def list_models(self, provider: str, models: list[str], current: str) -> None:
        theme = themes.current()
        if not models:
            self.info(f"No preset models listed for '{provider}'. Use /model <name>.")
            return
        table = Table(title=f"\u2727 Models \u00b7 {provider}", border_style=theme.accent2, box=ROUNDED)
        table.add_column("", style=theme.ok)
        table.add_column("Model", style=f"bold {theme.accent2}")
        for m in models:
            table.add_row("\u25cf" if m == current else " ", m)
        self.console.print(table)

    def list_all_models(self, pairs: list[tuple[str, str]], current: str) -> None:
        """Render every preset model across all providers as a picker.

        ``pairs`` is a list of (provider, model) tuples. The active model is
        marked with a filled dot.
        """
        theme = themes.current()
        if not pairs:
            self.info("No preset models configured. Use /model <name>.")
            return
        table = Table(title="\u2727 Models \u00b7 all providers", border_style=theme.accent2, box=ROUNDED)
        table.add_column("", style=theme.ok)
        table.add_column("Provider", style=f"bold {theme.accent}")
        table.add_column("Model", style=f"bold {theme.accent2}")
        for prov, m in pairs:
            table.add_row("\u25cf" if m == current else " ", prov, m)
        self.console.print(table)

    def list_tools(self, tools: list[Any]) -> None:
        theme = themes.current()
        table = Table(title="\U0001f6e0 Available Tools", border_style=theme.accent2, box=ROUNDED)
        table.add_column("Tool", style=f"bold {theme.ok}")
        table.add_column("Danger", style=theme.warn)
        table.add_column("Description")
        for t in tools:
            table.add_row(t.name, "\u26a0 yes" if t.dangerous else "no", t.description[:70])
        self.console.print(table)


class ResponseRenderer:
    """Live streaming renderer with an animated thinking state.

    While waiting for the first token it runs a background shimmer animation.
    Once text streams in it live-renders markdown; the thinking loop stops.
    """

    def __init__(self, console: Console, animations: bool = True, spinner: str = "braille") -> None:
        self.console = console
        self.animations = animations
        self.spinner = spinner
        self._buffer = ""
        self._live: Live | None = None
        self._thinking = False
        self._thread: threading.Thread | None = None
        self._tick = 0
        self._label: str | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def mark_cancelled(self) -> None:
        """Flag that the user pressed Esc; switch the label to a stopping state."""
        self._cancelled = True
        self._label = "stopping"

    # -- thinking animation --------------------------------------------
    def start_thinking(self, label: str | None = None) -> None:
        self._label = label
        if self._live is None:
            self._live = Live(console=self.console, refresh_per_second=30, transient=True)
            self._live.start()
        if not self.animations:
            self._live.update(Text(" thinking\u2026", style=themes.current().dim))
            return
        self._thinking = True
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def _animate(self) -> None:
        while self._thinking:
            frame = effects.thinking_frame(self._tick, self._label, spinner=self.spinner)
            with self._lock:
                if self._live is not None and self._thinking:
                    try:
                        self._live.update(frame)
                    except Exception:
                        break
            self._tick += 1
            time.sleep(0.05)

    def _stop_thinking(self) -> None:
        self._thinking = False
        if self._thread is not None:
            self._thread.join(timeout=0.3)
            self._thread = None

    # -- streaming ------------------------------------------------------
    def on_delta(self, chunk: str) -> None:
        if self._thinking:
            self._stop_thinking()
            with self._lock:
                if self._live is not None:
                    self._live.stop()
                self._live = Live(console=self.console, refresh_per_second=24, transient=False)
                self._live.start()
        self._buffer += chunk
        if self._live is None:
            self._live = Live(console=self.console, refresh_per_second=24)
            self._live.start()
        self._live.update(Markdown(self._buffer))

    def finish(self, final_text: str | None = None) -> None:
        self._stop_thinking()
        text = final_text if final_text is not None else self._buffer
        with self._lock:
            if self._live is not None:
                if text:
                    self._live.update(Markdown(text))
                self._live.stop()
                self._live = None
            elif text:
                self.console.print(Markdown(text))
        self._buffer = ""
