"""Bug: a reply that completed normally was labelled "⊘ cancelled".

Only the Esc / Ctrl+C key bindings can cancel a TUI turn. A bare Escape
also reaches those bindings when the terminal sends a report that
prompt_toolkit's VT100 parser does not know — e.g. focus in/out
(``ESC [ I`` / ``ESC [ O``, sent by a terminal left in focus-reporting
mode by another TUI), a cursor-position reply split across reads, or a
CSI-u key. The parser turns such a sequence into ``Escape`` followed by
the literal characters, and the busy-Esc binding cancelled the running
turn: the user switched windows while the reply finished and got
"⊘ cancelled". These tests drive the REAL UI headless, feeding raw
terminal bytes through prompt_toolkit's own parser and key processor.
"""
import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="fix_tui_cancel_"))

from prompt_toolkit.input.vt100_parser import Vt100Parser  # noqa: E402
from prompt_toolkit.key_binding.key_processor import _Flush  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402

from fullagent.agent import Turn  # noqa: E402

from test_swarm_dash_tui import FakeManager, _wait, press, run_with_ui  # noqa: E402,E501


def feed_bytes(ui, data: str, flush: bool = True) -> None:
    """Send raw terminal input exactly like Application.read_from_input:
    parse with the VT100 parser, feed the key processor, then flush the
    way the app does after its escape timeouts."""
    keys = []
    parser = Vt100Parser(keys.append)
    parser.feed(data)
    kp = ui.app.key_processor
    kp.feed_multiple(keys)
    kp.process_keys()
    if flush:
        n = len(keys)
        parser.flush()                      # ttimeoutlen expired
        kp.feed_multiple(keys[n:])
        kp.feed(_Flush)                     # timeoutlen expired
        kp.process_keys()


class StrayEscapeDoesNotCancel(unittest.TestCase):
    def test_focus_out_report_while_busy_does_not_cancel(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b[O")
            self.assertFalse(ui._cancel_flag.is_set(),
                             "focus-out report cancelled the turn")
            feed_bytes(ui, "\x1b[I")
            self.assertFalse(ui._cancel_flag.is_set(),
                             "focus-in report cancelled the turn")
            self.assertEqual(ui.buffer.text, "",
                             "focus report leaked into the prompt")
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_unknown_csi_sequence_while_busy_does_not_cancel(self):
        def body(ui):
            ui._busy = True
            # kitty/CSI-u key report and a DA1 reply: unknown to the parser
            feed_bytes(ui, "\x1b[97;5u")
            feed_bytes(ui, "\x1b[?62;c")
            self.assertFalse(ui._cancel_flag.is_set())
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_real_escape_key_still_cancels(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x1b")           # a lone Esc keypress
            self.assertTrue(ui._cancel_flag.is_set(),
                            "a real Esc must still cancel the turn")
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_ctrl_c_still_cancels(self):
        def body(ui):
            ui._busy = True
            feed_bytes(ui, "\x03")
            self.assertTrue(ui._cancel_flag.is_set())
            ui._busy = False
        run_with_ui(body, FakeManager(0))

    def test_completed_reply_not_labelled_cancelled(self):
        """End to end: the reply streams fully, the turn is still finishing
        (provider closing the stream) when the terminal sends a focus
        report; the footer must not say cancelled."""
        def body(ui):
            done = []
            reported = threading.Event()

            def run_turn(text, on_token, on_reasoning, on_tool_call,
                         on_tool_update, on_status, approve,
                         should_cancel=None, **kw):
                t = Turn(user_text=text)
                on_token("Hey! What can I do for you?\n")
                reported.wait(2)             # stream tail still open
                time.sleep(0.05)
                if should_cancel():
                    t.error = "cancelled"
                else:
                    t.assistant_text = "Hey! What can I do for you?"
                t.duration = 0.1
                done.append(t)
                return t

            ui.agent.run_turn = run_turn
            ui.agent._cancel_flag = threading.Event()
            for ch in "hii":
                press(ui, ch)
            press(ui, Keys.ControlM)
            self.assertTrue(_wait(lambda: ui._busy, 2))
            feed_bytes(ui, "\x1b[O")         # user switched windows
            reported.set()
            self.assertTrue(_wait(lambda: done and not ui._busy, 5))
            out = ui.output.getvalue()
            self.assertIn("Hey! What can I do for you?", out)
            self.assertNotIn("cancelled", out)
            self.assertEqual(done[0].error, "")
        run_with_ui(body, FakeManager(0))


if __name__ == "__main__":
    unittest.main()
