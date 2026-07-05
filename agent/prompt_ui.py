"""World-class interactive prompt using prompt_toolkit.

Features:
  - Slash-command autocompletion with a dropdown you navigate using
    PgUp / PgDown (and arrow keys).
  - Multiline editing with proper copy/paste of long text.
  - Persistent command history (Up/Down through past inputs).
  - Syntax-aware styling and a rich bottom toolbar.

Falls back gracefully to a basic input() if prompt_toolkit is unavailable.
"""
from __future__ import annotations

from typing import Iterable

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style

    _HAS_PTK = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_PTK = False


SLASH_COMMANDS = {
    "/help": "Show help",
    "/clear": "Clear conversation memory",
    "/save": "Save the current chat",
    "/load": "Load a saved chat",
    "/history": "List saved chats",
    "/model": "Switch model",
    "/persona": "Switch persona",
    "/usage": "Show token usage & cost",
    "/tools": "Toggle tool calling",
    "/theme": "Change UI theme",
    "/vivid": "Change vivid theme (neon, cyberpunk...)",
    "/health": "Show health status",
    "/metrics": "Show metrics",
    "/matrix": "Matrix rain animation",
    "/confetti": "Celebration confetti",
    "/banner": "Animated banner",
    "/exit": "Quit",
}


if _HAS_PTK:

    class SlashCompleter(Completer):
        """Completes slash commands with descriptions in the dropdown."""

        def get_completions(self, document, complete_event) -> Iterable:
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            word = text.split()[0] if text.split() else text
            for cmd, desc in SLASH_COMMANDS.items():
                if cmd.startswith(word):
                    yield Completion(
                        cmd,
                        start_position=-len(word),
                        display=cmd,
                        display_meta=desc,
                    )

    def _build_keybindings() -> "KeyBindings":
        kb = KeyBindings()

        @kb.add("pagedown")
        def _(event):
            """Move to next completion in the dropdown."""
            buff = event.app.current_buffer
            if buff.complete_state:
                buff.complete_next()
            else:
                buff.start_completion(select_first=True)

        @kb.add("pageup")
        def _(event):
            """Move to previous completion in the dropdown."""
            buff = event.app.current_buffer
            if buff.complete_state:
                buff.complete_previous()
            else:
                buff.start_completion(select_first=False)

        @kb.add("c-j")  # Ctrl+J inserts a newline for long multiline prompts
        def _(event):
            event.app.current_buffer.insert_text("\n")

        return kb

    _STYLE = Style.from_dict(
        {
            "prompt": "bold #00afff",
            "completion-menu.completion": "bg:#1c1c1c #ffffff",
            "completion-menu.completion.current": "bg:#00afff #000000",
            "completion-menu.meta.completion": "bg:#262626 #999999",
            "bottom-toolbar": "bg:#222222 #aaaaaa",
        }
    )

    def _bottom_toolbar() -> "HTML":
        return HTML(
            " <b>Enter</b> send  "
            "<b>Ctrl+J</b> newline  "
            "<b>PgUp/PgDn</b> pick command  "
            "<b>/</b> commands  "
            "<b>Ctrl+C</b> quit "
        )

    class AdvancedPrompt:
        """A reusable prompt session with all the bells and whistles."""

        def __init__(self, history_file: str = ".prompt_history") -> None:
            self.session: PromptSession = PromptSession(
                history=FileHistory(history_file),
                completer=SlashCompleter(),
                complete_while_typing=True,
                key_bindings=_build_keybindings(),
                style=_STYLE,
                bottom_toolbar=_bottom_toolbar,
                multiline=False,
                mouse_support=True,
            )

        def ask(self) -> str:
            return self.session.prompt(HTML("<prompt>you \u276f </prompt>"))

else:

    class AdvancedPrompt:  # type: ignore[no-redef]
        """Fallback prompt when prompt_toolkit isn't installed."""

        def __init__(self, history_file: str = ".prompt_history") -> None:
            pass

        def ask(self) -> str:
            return input("you \u276f ")


def get_prompt(history_file: str = ".prompt_history") -> AdvancedPrompt:
    return AdvancedPrompt(history_file)
