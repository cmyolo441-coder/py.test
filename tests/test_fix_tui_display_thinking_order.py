"""Bug: the "Thinking" (reasoning) block was printed AFTER "✦ done".

The done flash ran in the turn's ``finally`` block, before the reasoning
panel (and the rendered Markdown reply) were printed, so a reasoning
model's turn ended "reply · ✦ done · Thinking …". The done line must
come after everything that belongs to the turn. Driven through the real
UI headless, with a fake agent that streams reasoning and a reply.
"""
import io
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="fix_tui_think_"))

from prompt_toolkit.keys import Keys  # noqa: E402
from rich.console import Console  # noqa: E402

from fullagent import fx  # noqa: E402
from fullagent.agent import Turn  # noqa: E402

from test_swarm_dash_tui import FakeManager, _wait, press, run_with_ui  # noqa: E402,E501


class _TTY(io.StringIO):
    def isatty(self):
        return True


def _fake_turn(reasoning="The user said hii.\nGreet them back.",
               reply="Hey! What can I do for you?"):
    def run_turn(text, on_token, on_reasoning, on_tool_call, on_tool_update,
                 on_status, approve, should_cancel=None, **kw):
        t = Turn(user_text=text)
        for ln in reasoning.splitlines(keepends=True):
            on_reasoning(ln)
        on_token(reply + "\n")
        t.reasoning = reasoning
        t.assistant_text = reply
        t.duration = 0.4
        t.usage = {"prompt_tokens": 12, "completion_tokens": 7}
        return t
    return run_turn


def _run(ui, render_md=False):
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=100, color_system=None,
                         force_terminal=True)
    ui.cfg.show_reasoning = True
    ui.cfg.extra["render_markdown"] = render_md
    ui.agent.run_turn = _fake_turn()
    ui.agent._cancel_flag = threading.Event()

    def fake_flash(console, text, **kw):
        console.print(text)

    with mock.patch.object(fx, "shimmer_flash", fake_flash), \
            mock.patch.object(sys, "stdout", _TTY()):
        for ch in "hii":
            press(ui, ch)
        press(ui, Keys.ControlM)
        # the fake turn can finish before a poll ever sees _busy, so wait
        # for the turn's last line instead of the busy flag toggling
        assert _wait(lambda: "0.4s" in ui.output.getvalue()
                     and not ui._busy, 5)
    return ui.output.getvalue()


class ThinkingBeforeDone(unittest.TestCase):
    def _check(self, out):
        self.assertIn("✦ done", out)
        self.assertIn("Thinking", out)
        self.assertIn("Hey! What can I do for you?", out)
        done = out.index("✦ done")
        self.assertLess(out.index("Thinking"), done,
                        "Thinking block printed after the done line:\n"
                        + out)
        self.assertLess(out.rindex("Hey! What can I do for you?"), done)
        # stats footer follows the done line
        self.assertLess(done, out.index("0.4s"))

    def test_streamed_reply(self):
        def body(ui):
            out = _run(ui)
            self._check(out)
            # the live-streamed reply must come AFTER its Thinking block
            self.assertLess(out.index("Thinking"),
                            out.index("Hey! What can I do for you?"),
                            "reply printed before its Thinking block:\n"
                            + out)
        run_with_ui(body, FakeManager(0))

    def test_markdown_reply_has_thinking_above_answer(self):
        def body(ui):
            out = _run(ui, render_md=True)
            self._check(out)
            self.assertLess(out.index("Thinking"),
                            out.rindex("Hey! What can I do for you?"))
        run_with_ui(body, FakeManager(0))


if __name__ == "__main__":
    unittest.main()
