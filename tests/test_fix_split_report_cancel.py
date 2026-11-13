"""Bug (v4.1.1 log): a running turn ended "⊘ cancelled" with no key pressed.

The prompt asks the terminal for the cursor position (ESC[6n) while it
redraws, and the terminal answers ESC[25;1R. On a laggy ssh / web / docker
terminal the answer can arrive in two pieces. prompt_toolkit then emits the
lone ESC as an Escape key ~0.5 s later, the busy-Esc binding cancelled the
turn, and the tail "[25;1R" was typed into the prompt. Reproduced in a real
pseudo-terminal by delaying the tail 2 s (see the report); the old build
printed "⊘ cancelled" and left "[25;1R" in the input box.

Now a lone Escape never stops a turn: it arms, a second Esc within
ESC_CONFIRM_WINDOW stops it, Ctrl+C stops at once, report tails are dropped
from the prompt (and disarm the Esc that came with them), and the cancelled
line names the key the user pressed.
"""
import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="fix_split_report_"))

from prompt_toolkit.keys import Keys  # noqa: E402

from fullagent import tui as tui_mod  # noqa: E402
from fullagent.agent import Turn  # noqa: E402

from test_fix_tui_display_cancel import feed_bytes  # noqa: E402
from test_swarm_dash_tui import FakeManager, _wait, press, run_with_ui  # noqa: E402,E501


class SplitTerminalReport(unittest.TestCase):
    def test_lone_escape_only_arms(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b")
            self.assertFalse(ui._cancel_flag.is_set(),
                             "a single Escape byte stopped the turn")
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_split_cursor_report_never_cancels_or_leaks(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b")             # head arrives alone
            feed_bytes(ui, "[25;1R")           # tail, seconds later
            self.assertFalse(ui._cancel_flag.is_set())
            self.assertEqual(ui.buffer.text, "",
                             "report tail typed into the prompt")
            # the Escape that came with the tail must not count: a real
            # Esc after it only arms again
            feed_bytes(ui, "\x1b")
            self.assertFalse(ui._cancel_flag.is_set())
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_split_mouse_and_focus_tails_dropped(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b")
            feed_bytes(ui, "[<64;10;5M")
            self.assertEqual(ui.buffer.text, "")
            feed_bytes(ui, "\x1b")
            feed_bytes(ui, "[O")
            self.assertEqual(ui.buffer.text, "")
            self.assertFalse(ui._cancel_flag.is_set())
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_tail_dropped_but_typed_text_kept(self):
        def body(ui):
            for ch in "fix [I] bug":
                press(ui, ch)
            self.assertEqual(ui.buffer.text, "fix [I] bug")
            feed_bytes(ui, "[12;40R")
            self.assertEqual(ui.buffer.text, "fix [I] bug")
            press(ui, "!")
            self.assertEqual(ui.buffer.text, "fix [I] bug!")
        run_with_ui(body, FakeManager(0))

    def test_escape_twice_cancels_and_window_expires(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b")
            ui._esc_armed_at -= tui_mod.ESC_CONFIRM_WINDOW + 0.5
            feed_bytes(ui, "\x1b")             # too late: arms again
            self.assertFalse(ui._cancel_flag.is_set())
            feed_bytes(ui, "\x1b")
            self.assertTrue(ui._cancel_flag.is_set())
            self.assertEqual(ui._cancel_source, "Esc twice")
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_ctrl_c_cancels_at_once_and_is_named(self):
        def body(ui):
            seen = []

            def run_turn(text, on_token, on_reasoning, on_tool_call,
                         on_tool_update, on_status, approve,
                         should_cancel=None, **kw):
                t = Turn(user_text=text)
                end = time.monotonic() + 3
                while time.monotonic() < end and not should_cancel():
                    time.sleep(0.01)
                t.error = "cancelled" if should_cancel() else ""
                seen.append(t)
                return t

            ui.agent.run_turn = run_turn
            ui.agent._cancel_flag = threading.Event()
            for ch in "go":
                press(ui, ch)
            press(ui, Keys.ControlM)
            self.assertTrue(_wait(lambda: ui._busy, 2))
            feed_bytes(ui, "\x03")
            self.assertTrue(_wait(lambda: seen and not ui._busy, 5))
            out = ui.output.getvalue()
            self.assertIn("⊘ cancelled — you pressed Ctrl+C", out)
        run_with_ui(body, FakeManager(0))

    def test_turn_finishes_when_report_splits_mid_stream(self):
        """End to end: a reply streams for a while; a cursor report splits
        in the middle of it. The turn must finish normally."""
        def body(ui):
            done = []
            mid = threading.Event()
            go_on = threading.Event()

            def run_turn(text, on_token, on_reasoning, on_tool_call,
                         on_tool_update, on_status, approve,
                         should_cancel=None, **kw):
                t = Turn(user_text=text)
                on_token("Working through it… ")
                mid.set()
                go_on.wait(3)
                if should_cancel():
                    t.error = "cancelled"
                else:
                    on_token("all done.\n")
                    t.assistant_text = "Working through it… all done."
                t.duration = 0.2
                done.append(t)
                return t

            ui.agent.run_turn = run_turn
            ui.agent._cancel_flag = threading.Event()
            for ch in "please":
                press(ui, ch)
            press(ui, Keys.ControlM)
            self.assertTrue(mid.wait(2))
            feed_bytes(ui, "\x1b")
            feed_bytes(ui, "[31;1R")
            go_on.set()
            self.assertTrue(_wait(lambda: done and not ui._busy, 5))
            self.assertEqual(done[0].error, "")
            out = ui.output.getvalue()
            self.assertNotIn("cancelled", out)
            self.assertIn("all done.", out)
            self.assertEqual(ui.buffer.text, "")
        run_with_ui(body, FakeManager(0))

    def test_armed_esc_does_not_carry_into_next_turn(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b")
            ui._busy = False
            ui._reset_cancel_state()          # what every turn start does
            ui._busy = True
            feed_bytes(ui, "\x1b")
            self.assertFalse(ui._cancel_flag.is_set())
            ui._busy = False
        run_with_ui(body, FakeManager(0))


if __name__ == "__main__":
    unittest.main()
