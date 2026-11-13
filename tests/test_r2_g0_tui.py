import io
import os
import tempfile
import threading
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from rich.console import Console  # noqa: E402

from fullagent import tui  # noqa: E402
from fullagent.agent import Turn  # noqa: E402


def _headless_ui(extra):
    ui = tui.UI.__new__(tui.UI)
    ui.cfg = SimpleNamespace(extra=extra, show_reasoning=False)
    ui._busy = False
    ui._cancel_flag = threading.Event()
    ui._focus_remaining = 0
    ui._status_text = ""
    ui._width = lambda: 80
    ui._start_spinner = lambda: None
    ui._stop_spinner = lambda: None
    ui._emit_user = lambda t: None
    ui._set_flash = lambda *a: None
    ui._print_turn_stats = lambda t: None
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=200, color_system=None)
    ui._invalidate = lambda: None
    return ui


def test_render_markdown_status_preview_is_single_line():
    ui = _headless_ui({"render_markdown": True})
    seen = []
    orig = tui.UI._set_status

    def spy(text):
        seen.append(text)
        orig(ui, text)

    ui._set_status = spy

    def fake_run(text, on_token, *a, **k):
        on_token("# Title\n\nHello world")
        return Turn(text, assistant_text="# Title\n\nHello world")

    ui.agent = SimpleNamespace(save_session=lambda: None, run_turn=fake_run)
    ui._run_turn_thread("hi")
    assert "# Title Hello world" in seen
    assert not any("\n" in s for s in seen), seen


def test_verify_log_lists_violations_line_by_line():
    ui = _headless_ui({})
    result = SimpleNamespace(ok=False, violations=[
        {"property": "no_rm", "why": "rm -rf ran"}])
    ui.agent = SimpleNamespace(
        formal=SimpleNamespace(audit_log=lambda: result))
    ui._cmd_verify("log")
    out = ui.output.getvalue()
    assert "HISTORY AUDIT — VIOLATIONS FOUND ✗" in out
    assert "  ⚠ no_rm: rm -rf ran" in out
