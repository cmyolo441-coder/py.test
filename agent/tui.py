"""A full-screen Textual TUI — a modern, Cursor-style chat interface.

Run with:  python main.py --tui   (requires the 'textual' package)
"""
from __future__ import annotations

try:
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import Footer, Header, Input, Markdown, Static
    from textual.binding import Binding

    _HAS_TEXTUAL = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_TEXTUAL = False


if _HAS_TEXTUAL:

    class MessageView(Markdown):
        """A single chat message rendered as markdown."""

    class ChatApp(App):
        """The main TUI application."""

        CSS = """
        Screen { background: $surface; }
        #chat { height: 1fr; padding: 1 2; }
        #input-box { dock: bottom; height: auto; border: round $accent; margin: 1 2; }
        #status { dock: bottom; height: 1; background: $panel; color: $text-muted; }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit"),
            Binding("ctrl+l", "clear", "Clear"),
            Binding("ctrl+p", "palette", "Commands"),
        ]

        def __init__(self, on_message=None, title: str = "termianlagent") -> None:
            super().__init__()
            self._on_message = on_message
            self._title = title

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with VerticalScroll(id="chat"):
                yield Static(f"# {self._title}\nType a message below. Ctrl+P for commands.")
            yield Input(placeholder="Message (paste long text supported)…", id="input-box")
            yield Static("ready", id="status")
            yield Footer()

        def add_message(self, role: str, text: str) -> None:
            chat = self.query_one("#chat", VerticalScroll)
            chat.mount(MessageView(f"**{role}:** {text}"))
            chat.scroll_end(animate=False)

        async def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            if not text:
                return
            self.add_message("you", text)
            event.input.value = ""
            if self._on_message is not None:
                reply = self._on_message(text)
                if reply:
                    self.add_message("assistant", reply)

        def action_clear(self) -> None:
            chat = self.query_one("#chat", VerticalScroll)
            for child in list(chat.children)[1:]:
                child.remove()

        def action_palette(self) -> None:
            self.query_one("#input-box", Input).value = "/"

    def run_tui(on_message=None) -> None:
        ChatApp(on_message=on_message).run()

else:

    def run_tui(on_message=None) -> None:  # type: ignore[no-redef]
        print("Textual is not installed. Run: pip install textual")


if __name__ == "__main__":
    run_tui()
