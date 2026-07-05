"""Split-pane TUI: chat on the left, live file-tree on the right (Cursor-style).

Run with:  python -m agent.split_tui   (requires 'textual').
"""
from __future__ import annotations

from pathlib import Path

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, VerticalScroll
    from textual.widgets import DirectoryTree, Footer, Header, Input, Markdown, Static
    from textual.binding import Binding

    _HAS_TEXTUAL = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_TEXTUAL = False


if _HAS_TEXTUAL:

    class SplitChatApp(App):
        """Two-pane layout: conversation + project file tree."""

        CSS = """
        #body { height: 1fr; }
        #left { width: 2fr; border-right: solid $accent; }
        #right { width: 1fr; }
        #chat { height: 1fr; padding: 1 2; }
        #input-box { dock: bottom; border: round $accent; margin: 1 2; }
        #tree-title { background: $panel; color: $text; padding: 0 1; }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit"),
            Binding("ctrl+b", "toggle_tree", "Toggle files"),
        ]

        def __init__(self, on_message=None, root: str = ".") -> None:
            super().__init__()
            self._on_message = on_message
            self._root = root

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="body"):
                with VerticalScroll(id="left"):
                    with VerticalScroll(id="chat"):
                        yield Static("# termianlagent\nChat on the left, files on the right.")
                    yield Input(placeholder="Message… (paste supported)", id="input-box")
                with VerticalScroll(id="right"):
                    yield Static("📁 Files", id="tree-title")
                    yield DirectoryTree(self._root, id="tree")
            yield Footer()

        def add_message(self, role: str, text: str) -> None:
            chat = self.query_one("#chat", VerticalScroll)
            chat.mount(Markdown(f"**{role}:** {text}"))
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

        def on_directory_tree_file_selected(
            self, event: "DirectoryTree.FileSelected"
        ) -> None:
            """Preview a clicked file in the chat pane."""
            try:
                content = Path(event.path).read_text(encoding="utf-8", errors="replace")
                preview = content[:2000]
                self.add_message("file", f"`{event.path}`\n\n```\n{preview}\n```")
            except OSError as exc:
                self.add_message("file", f"Could not read {event.path}: {exc}")

        def action_toggle_tree(self) -> None:
            right = self.query_one("#right")
            right.display = not right.display

    def run_split_tui(on_message=None, root: str = ".") -> None:
        SplitChatApp(on_message=on_message, root=root).run()

else:

    def run_split_tui(on_message=None, root: str = ".") -> None:  # type: ignore[no-redef]
        print("Textual is not installed. Run: pip install textual")


if __name__ == "__main__":
    run_split_tui()
