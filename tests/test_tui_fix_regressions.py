"""Regression tests for TUI-layer bugs fixed alongside the slowdown work."""
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from fullagent import bgtui, cliui
from fullagent.agent import ToolEvent, Turn
from fullagent.streamthrottle import StreamThrottle
from fullagent.tui import SafeFileHistory, UI


def _make_ui():
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra={"render_markdown": False},
                             show_reasoning=False, auto_approve=False)
    ui._busy = False
    ui._cancel_flag = threading.Event()
    ui._focus_remaining = 0
    ui._status_text = ""
    ui._approve_request = None
    ui._diff_history = []
    ui._width = lambda: 80
    ui._invalidate = lambda: None
    ui._start_spinner = lambda: None
    ui._stop_spinner = lambda: None
    ui._emit_user = lambda text: None
    ui._set_flash = lambda *a, **k: None
    ui._print_turn_stats = lambda turn: None
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=80, color_system=None)
    ui.agent = SimpleNamespace(save_session=lambda: None)
    return ui


class FooterTests(unittest.TestCase):
    COLORS = {k: "" for k in ("cyan", "dim", "yellow", "pink", "accent",
                              "green", "red")}

    def test_long_branch_is_shortened_before_context_label_drops(self):
        frags = cliui.status_fragments(
            width=120, colors=self.COLORS,
            model_label="DeepSeek V4.1 Flash 1017", effort_label="high",
            context_window=262144, used_pct=3, cwd="/home/user/project",
            branch="claude/project-thread-with-a-long-name",
            agents_running=8, agents_queued=0)
        text = "".join(t for _, t in frags)
        self.assertIn("262k context", text)
        self.assertIn("8 agents | 8 running", text)
        self.assertIn("◎ claude/", text)          # branch still shown
        self.assertEqual(sum(cliui.display_width(t) for _, t in frags), 120)

    def test_short_branch_untouched(self):
        text = "".join(t for _, t in cliui.status_fragments(
            width=120, colors=self.COLORS, model_label="M",
            effort_label="high", context_window=1_000_000, used_pct=1,
            cwd="/tmp", branch="main"))
        self.assertIn("◎ main ", text)
        self.assertIn("1M context", text)

    def test_binary_megabyte_window_label(self):
        self.assertEqual(cliui._compact_number(1_048_576), "1M")
        self.assertEqual(cliui._compact_number(1_500_000), "1.5M")
        self.assertEqual(cliui._compact_number(262_144), "262k")


class HistoryCapTests(unittest.TestCase):
    def test_only_newest_entries_are_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "history")
            with open(path, "w", encoding="utf-8") as fh:
                for i in range(3000):
                    fh.write(f"\n# t\n+entry {i}\n")
            hist = SafeFileHistory(path)
            loaded = list(hist.load_history_strings())
        self.assertEqual(len(loaded), SafeFileHistory.MAX_LOADED)
        self.assertEqual(loaded[0], "entry 2999")     # newest first

    def test_missing_file_is_empty(self):
        hist = SafeFileHistory("/nonexistent-dir-xyz/history")
        self.assertEqual(list(hist.load_history_strings()), [])


class BoundedStateTests(unittest.TestCase):
    def test_diff_history_is_bounded(self):
        ui = _make_ui()
        ui.cfg.auto_approve = True

        def run(text, on_token, on_reasoning, on_tool_call, on_tool_update,
                *a, **kw):
            for i in range(260):
                ev = ToolEvent("edit_file",
                               {"path": f"f{i}.py", "old_string": "a",
                                "new_string": "b"},
                               result="OK", status="done")
                on_tool_update(ev)
            return Turn(text)

        ui.agent.run_turn = run
        ui._find_hunk_line = lambda *a: 1
        ui._run_turn_thread("go")
        self.assertLessEqual(len(ui._diff_history), 200)
        self.assertEqual(ui._diff_history[-1]["path"], "f259.py")

    def test_background_panel_caps_finished_rows(self):
        now = time.time()
        tasks = [{"id": f"sh-{i}", "description": "job", "status": "done",
                  "started_at": now - 100 + i} for i in range(30)]
        tasks.append({"id": "sh-run", "description": "live",
                      "status": "running", "started_at": now})
        fake = SimpleNamespace(list_background_tasks=lambda: tasks)
        with patch.dict(sys.modules, {"fullagent.bgsh": fake}):
            import fullagent
            with patch.object(fullagent, "bgsh", fake, create=True):
                lines = bgtui.BackgroundPanel().refresh()
        text = "\n".join(lines)
        self.assertIn("sh-run", text)
        self.assertIn("sh-29", text)                  # newest finished kept
        self.assertNotIn("sh-0 ", text)
        self.assertIn("older finished", text)
        self.assertLess(len(lines), 12)


class FocusContinuationTests(unittest.TestCase):
    def test_continuations_do_not_recurse(self):
        ui = _make_ui()
        ui._focus_remaining = 25
        depths = []

        def run(text, *a, **kw):
            f = sys._getframe()
            n = 0
            while f is not None:
                n += 1
                f = f.f_back
            depths.append(n)
            return Turn(text, assistant_text="x")

        ui.agent.run_turn = run
        ui.agent.focus_continue = lambda turn, left: "continue"
        ui._run_turn_thread("start")
        self.assertEqual(len(depths), 26)
        self.assertEqual(max(depths), min(depths),
                         "each focus continuation added stack frames")
        self.assertEqual(ui._focus_remaining, 0)


class ApprovalCancelTests(unittest.TestCase):
    def test_pending_approval_wakes_on_cancel(self):
        ui = _make_ui()
        ui._approval_line = lambda tool, args: ""
        ui._diff_preview = lambda tool, args: None
        tool = SimpleNamespace(name="run_command")
        result = []
        th = threading.Thread(
            target=lambda: result.append(ui._approve_blocking(tool, {})),
            daemon=True)
        th.start()
        time.sleep(0.05)
        ui._cancel_flag.set()
        th.join(3)
        self.assertFalse(th.is_alive(), "approval wait ignored the cancel")
        self.assertEqual(result, [False])
        self.assertIsNone(ui._approve_request)


class SlashWhileBusyTests(unittest.TestCase):
    def test_history_rewriting_commands_refused_mid_turn(self):
        ui = _make_ui()
        errors = []
        ui.print_error = errors.append
        routed = []
        ui._route_slash = lambda cmd, arg: routed.append(cmd)
        ui._busy = True
        for cmd in ("/compact", "/cmp", "/switch abc", "/new", "/rewind 3"):
            ui._handle_slash(cmd)
        self.assertEqual(routed, [])
        self.assertEqual(len(errors), 5)
        ui._handle_slash("/help")           # harmless commands still run
        self.assertEqual(routed, ["/help"])
        ui._busy = False
        ui._turn_alive = True               # watchdog-released worker
        ui._handle_slash("/compact")
        self.assertEqual(routed, ["/help"])
        ui._turn_alive = False
        ui._handle_slash("/compact")
        self.assertEqual(routed, ["/help", "/compact"])

    def test_agents_wait_does_not_block_the_event_loop(self):
        ui = _make_ui()
        release = threading.Event()
        shown = threading.Event()
        mgr = SimpleNamespace(wait=lambda ids, timeout=30: release.wait(5))
        ui.agent = SimpleNamespace(subagents=mgr,
                                   _ensure_session=lambda: None)
        ui._print_agents_dashboard = lambda m: shown.set()
        ui.print_error = lambda *a: None
        t0 = time.monotonic()
        ui._cmd_agents("wait all")
        self.assertLess(time.monotonic() - t0, 1.0,
                        "/agents wait blocked the UI thread")
        release.set()
        self.assertTrue(shown.wait(5))


class TrailingFrameTests(unittest.TestCase):
    def test_spinner_surfaces_a_stalled_stream_frame(self):
        ui = _make_ui()
        ui._start_spinner = UI._start_spinner.__get__(ui)
        ui._stop_spinner = UI._stop_spinner.__get__(ui)
        ui._spinner_i = 0
        th = StreamThrottle(0.05)
        th.update("first")
        ui._set_status(th.maybe_flush())
        th.update("first second")          # absorbed: inside the interval
        self.assertIsNone(th.maybe_flush())
        ui._active_throttle = th
        ui._start_spinner()
        try:
            deadline = time.time() + 2
            while ui._status_text != "first second" and time.time() < deadline:
                time.sleep(0.02)
        finally:
            ui._stop_spinner()
        self.assertEqual(ui._status_text, "first second")

    def test_tool_call_discards_superseded_preview(self):
        ui = _make_ui()
        statuses = []
        real = UI._set_status

        def spy(text):
            statuses.append(text)
            real(ui, text)

        ui._set_status = spy

        def run(text, on_token, on_reasoning, on_tool_call, on_tool_update,
                *a, **kw):
            on_token("partial ")
            on_token("line")                    # absorbed by the throttle
            on_tool_call(ToolEvent("glob_files", {"pattern": "*"},
                                   status="running"))
            th = ui.__dict__.get("_active_throttle")
            self.assertIsNone(th)
            self.assertIsNone(ui._stream_throttle.take_due())
            return Turn(text)

        ui.agent.run_turn = run
        with patch("fullagent.tui.time.monotonic", return_value=50.0):
            ui._run_turn_thread("go")
        self.assertIn("running glob_files…", statuses)
        self.assertIn("partial line", ui.output.getvalue())


if __name__ == "__main__":
    unittest.main()
