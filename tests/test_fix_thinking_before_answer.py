"""Bug: the "Thinking" block printed AFTER the streamed answer text.

The reply streams into the transcript live (on_token), but the reasoning
panel was only printed once ``run_turn`` returned, so a real terminal
showed::

    Hi there! How can I help you today?
      Thinking
      │ User says "hello"...
    ✦ done

Providers stream ``reasoning_content`` before ``content``; the TUI now
buffers each model call's reasoning and prints its Thinking block right
before that call's first visible output. Driven through the real UI
headless with fake agents that call the streaming callbacks in provider
order.
"""
import io
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="fix_think_before_"))

from prompt_toolkit.keys import Keys  # noqa: E402
from rich.console import Console  # noqa: E402

from fullagent import fx  # noqa: E402
from fullagent.agent import ToolEvent, Turn  # noqa: E402

from test_swarm_dash_tui import FakeManager, _wait, press, run_with_ui  # noqa: E402,E501


class _TTY(io.StringIO):
    def isatty(self):
        return True


THINK_1 = "User says hello.\nGreet them warmly."
REPLY_1 = "Hi there! How can I help you today?"
THINK_TOOL = "They want the files.\nCall list_dir."
TOOL_RESULT = "README.md  setup.py"
THINK_2 = "Got the listing.\nSummarize it."
REPLY_2 = "Here are the files: README.md and setup.py."


def _plain_turn(text, on_token, on_reasoning, on_tool_call, on_tool_update,
                on_status, approve, should_cancel=None, **kw):
    t = Turn(user_text=text)
    for ln in THINK_1.splitlines(keepends=True):
        on_reasoning(ln)
    for w in REPLY_1.split(" "):
        on_token(w + " ")
    on_token("\n")
    t.reasoning = THINK_1
    t.assistant_text = REPLY_1
    t.duration = 0.4
    t.usage = {"prompt_tokens": 12, "completion_tokens": 7}
    return t


def _tool_turn(text, on_token, on_reasoning, on_tool_call, on_tool_update,
               on_status, approve, should_cancel=None, **kw):
    t = Turn(user_text=text)
    # model call 1: reasoning, then a tool call (no content)
    for ln in THINK_TOOL.splitlines(keepends=True):
        on_reasoning(ln)
    ev = ToolEvent(name="list_dir", args={"path": "."})
    on_tool_call(ev)
    ev.status = "done"
    ev.result = TOOL_RESULT
    on_tool_update(ev)
    # model call 2: reasoning, then the answer
    for ln in THINK_2.splitlines(keepends=True):
        on_reasoning(ln)
    on_token(REPLY_2 + "\n")
    t.reasoning = THINK_TOOL + THINK_2
    t.assistant_text = REPLY_2
    t.duration = 0.4
    return t


def _reasoning_only_turn(text, on_token, on_reasoning, on_tool_call,
                         on_tool_update, on_status, approve,
                         should_cancel=None, **kw):
    t = Turn(user_text=text)
    on_reasoning("Only thinking, no answer.\n")
    t.reasoning = "Only thinking, no answer.\n"
    t.duration = 0.4
    return t


def _unstreamed_turn(text, on_token, on_reasoning, on_tool_call,
                     on_tool_update, on_status, approve, should_cancel=None,
                     **kw):
    # cassette replay / non-streaming fallback: reasoning never reaches
    # on_reasoning, only the finished Turn
    t = Turn(user_text=text)
    on_token(REPLY_1 + "\n")
    t.reasoning = THINK_1
    t.assistant_text = REPLY_1
    t.duration = 0.4
    return t


def _run(ui, fake, render_md=False, visible=True):
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=100, color_system=None,
                         force_terminal=True)
    ui.cfg.show_reasoning = visible
    ui.agent.thinking_visible = visible
    ui.agent.thinking_expanded = False
    ui.cfg.extra["render_markdown"] = render_md
    ui.agent.run_turn = fake
    ui.agent._cancel_flag = threading.Event()

    def fake_flash(console, text, **kw):
        console.print(text)

    with mock.patch.object(fx, "shimmer_flash", fake_flash), \
            mock.patch.object(sys, "stdout", _TTY()):
        for ch in "hi":
            press(ui, ch)
        press(ui, Keys.ControlM)
        assert _wait(lambda: "0.4s" in ui.output.getvalue()
                     and not ui._busy, 5)
    return ui.output.getvalue()


def _lazy_visible_turn(text, on_token, on_reasoning, on_tool_call,
                       on_tool_update, on_status, approve,
                       should_cancel=None, **kw):
    # the thinking module attaches agent.thinking_visible lazily — on the
    # first turn it can appear only once the turn is already running
    _lazy_visible_turn.agent.thinking_visible = True
    return _plain_turn(text, on_token, on_reasoning, on_tool_call,
                       on_tool_update, on_status, approve, should_cancel)


class ThinkingBeforeAnswer(unittest.TestCase):
    def test_plain_reply(self):
        def body(ui):
            out = _run(ui, _plain_turn)
            self.assertEqual(out.count("Thinking"), 1, out)
            think = out.index("Thinking")
            self.assertLess(think, out.index("Hi there!"), out)
            self.assertLess(out.index("Greet them warmly."),
                            out.index("Hi there!"), out)
            done = out.index("✦ done")
            self.assertLess(out.rindex("Hi there!"), done)
            self.assertLess(done, out.index("0.4s"))
        run_with_ui(body, FakeManager(0))

    def test_markdown_reply(self):
        def body(ui):
            out = _run(ui, _plain_turn, render_md=True)
            self.assertEqual(out.count("Thinking"), 1, out)
            self.assertLess(out.index("Thinking"), out.index("Hi there!"))
            self.assertLess(out.rindex("Hi there!"), out.index("✦ done"))
        run_with_ui(body, FakeManager(0))

    def test_tool_turn_each_step_thinks_before_its_output(self):
        def body(ui):
            out = _run(ui, _tool_turn)
            self.assertEqual(out.count("Thinking"), 2, out)
            first = out.index("Thinking")
            second = out.index("Thinking", first + 1)
            tool = out.index("list_dir")
            self.assertLess(out.index("Call list_dir."), tool, out)
            self.assertLess(first, tool, out)
            self.assertLess(tool, second, out)
            self.assertLess(second, out.index("Here are the files"), out)
            self.assertLess(out.index("Summarize it."),
                            out.index("Here are the files"), out)
            self.assertLess(out.rindex("Here are the files"),
                            out.index("✦ done"))
        run_with_ui(body, FakeManager(0))

    def test_reasoning_without_answer_still_prints_once(self):
        def body(ui):
            out = _run(ui, _reasoning_only_turn)
            self.assertEqual(out.count("Thinking"), 1, out)
            self.assertIn("Only thinking, no answer.", out)
            self.assertLess(out.index("Thinking"), out.index("✦ done"))
        run_with_ui(body, FakeManager(0))

    def test_unstreamed_reasoning_falls_back_to_turn_end(self):
        def body(ui):
            out = _run(ui, _unstreamed_turn)
            self.assertEqual(out.count("Thinking"), 1, out)
            self.assertLess(out.index("Thinking"), out.index("✦ done"))
        run_with_ui(body, FakeManager(0))

    def test_visibility_attached_mid_turn_still_shows_thinking(self):
        def body(ui):
            _lazy_visible_turn.agent = ui.agent
            out = _run(ui, _lazy_visible_turn, visible=False)
            self.assertEqual(out.count("Thinking"), 1, out)
            self.assertLess(out.index("Thinking"), out.index("Hi there!"))
        run_with_ui(body, FakeManager(0))

    def test_hidden_thinking_prints_nothing(self):
        def body(ui):
            out = _run(ui, _tool_turn, visible=False)
            self.assertNotIn("Thinking", out)
            self.assertNotIn("Summarize it.", out)
            self.assertIn("Here are the files", out)
        run_with_ui(body, FakeManager(0))


if __name__ == "__main__":
    unittest.main()
