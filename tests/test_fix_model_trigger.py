"""v4.1.2 report: the session model changed "on its own" — "✓ model →
Gemini 4 Argon … you picked it in the Ctrl+T menu" before the first
message, and later "✓ model → Mistral Large 4" followed by the previous
message being sent again.

Reproduced paths (real UI, headless, keys through the real KeyProcessor):

* every submitted line — slash commands included — is kept in the
  persistent input history. At an empty prompt ↑ recalls a previous
  "/model" (or its alias "/m"), and ONE ⏎ ran it silently (slash commands
  are not echoed): the model menu opened on the current model, the next
  ↑↑ meant for history moved it (ERNIE → Gemini) and ⏎ committed it — and
  the transcript blamed "the Ctrl+T menu";
* with the menu open, a pasted or edited message sat in the prompt under
  it; the ⏎ meant to send it committed a model and the next ⏎ sent the
  message (model line, then the same message again).

Now a model change needs an unambiguous, deliberate act; see the
MODEL_PICKER_* constants in fullagent/tui.py.
"""
import logging
import os
import tempfile
import threading
import time
import unittest
from collections import deque

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="fix_trig_"))

from prompt_toolkit.keys import Keys  # noqa: E402

from fullagent import tui  # noqa: E402

from test_fix_tui_display_cancel import feed_bytes  # noqa: E402
from test_swarm_dash_tui import FakeManager, press, run_with_ui  # noqa: E402

ERNIE = "ernie-5.5-flash-preview"
GEMINI = "gemini-4-argon"
MISTRAL = "mistral-large-4"
DELIBERATE = getattr(tui, "MODEL_PICKER_MIN_OPEN", 0.4) + 0.1


def _pause(s=0.06):
    threading.Event().wait(s)


def slow(ui, *keys, gap=0.06):
    """Keys pressed one at a time, as a human does."""
    for k in keys:
        _pause(gap)
        press(ui, k)


def seed_history(ui, entries):
    """Put entries in the persistent input history the way an earlier
    session left them, and load them into the prompt like prompt_toolkit's
    background history loader does."""
    for e in entries:
        ui.buffer.history.append_string(e)
    b = ui.buffer
    strings = list(b.history.get_strings())
    b._working_lines = deque(strings + [b.text])
    b._Buffer__working_index = len(strings)


def setup(ui, model=ERNIE):
    ui.cfg.model_id = model
    sent = []
    real = ui._dispatch

    def dispatch(text):
        sent.append(text)
        if text.startswith("/"):
            real(text)        # slash commands run for real
    ui._dispatch = dispatch
    return sent


class HistoryRecallNeverChangesTheModel(unittest.TestCase):
    def test_up_enter_up_up_enter_on_recalled_model(self):
        """The exact first event: ↑ ⏎ ↑ ↑ ⏎ at an empty prompt after an
        earlier session ran /model. Old code: ERNIE → Gemini."""
        def body(ui):
            sent = setup(ui)
            seed_history(ui, ["hi", "/model"])
            slow(ui, Keys.Up)
            self.assertEqual(ui.buffer.text, "/model")
            slow(ui, Keys.ControlM, Keys.Up, Keys.Up, Keys.ControlM,
                 gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertNotIn("/model", sent)
            self.assertNotIn("✓ model", ui.output.getvalue())
        run_with_ui(body, FakeManager(0))

    def test_same_keys_as_one_burst(self):
        def body(ui):
            sent = setup(ui)
            seed_history(ui, ["hi", "/model"])
            press(ui, Keys.Up, Keys.ControlM, Keys.Up, Keys.Up, Keys.ControlM)
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertNotIn("/model", sent)
        run_with_ui(body, FakeManager(0))

    def test_recalled_alias_m_then_down_enter(self):
        """Second event shape: ↓ (one step) + ⏎ after recalling /m."""
        def body(ui):
            sent = setup(ui, GEMINI)
            seed_history(ui, ["/m"])
            slow(ui, Keys.Up, Keys.ControlM, Keys.Down, Keys.ControlM,
                 gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, GEMINI)
            self.assertNotIn("/m", sent)
        run_with_ui(body, FakeManager(0))

    def test_prefix_history_search_recall(self):
        def body(ui):
            sent = setup(ui)
            seed_history(ui, ["/model", "hello"])
            slow(ui, "/", Keys.Up)
            self.assertEqual(ui.buffer.text, "/model")
            slow(ui, Keys.ControlM, Keys.Up, Keys.Up, Keys.ControlM,
                 gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertNotIn("/model", sent)
        run_with_ui(body, FakeManager(0))

    def test_recalled_model_with_argument(self):
        def body(ui):
            setup(ui)
            seed_history(ui, [f"/model {GEMINI}"])
            slow(ui, Keys.Up, Keys.ControlM)
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertIn("came from history", ui._flash[0])
        run_with_ui(body, FakeManager(0))

    def test_second_separate_enter_confirms_and_label_names_it(self):
        def body(ui):
            sent = setup(ui)
            seed_history(ui, ["/model"])
            slow(ui, Keys.Up, Keys.ControlM)
            self.assertFalse(ui._overlay_open())
            slow(ui, Keys.ControlM, gap=DELIBERATE)   # confirm
            self.assertTrue(ui._overlay_open())
            self.assertEqual(sent, ["/model"])
            slow(ui, Keys.Up, Keys.Up)
            slow(ui, Keys.ControlM, gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, GEMINI)
            out = " ".join(ui.output.getvalue().split())
            self.assertIn("chosen in the model menu (opened with /model)", out)
            self.assertNotIn("you picked it", out)
        run_with_ui(body, FakeManager(0))

    def test_confirm_needs_enter_as_the_very_next_key(self):
        def body(ui):
            setup(ui)
            seed_history(ui, ["/model"])
            slow(ui, Keys.Up, Keys.ControlM)
            slow(ui, Keys.Down, Keys.Up)               # browsing history
            self.assertEqual(ui.buffer.text, "/model")
            slow(ui, Keys.ControlM, gap=DELIBERATE)
            self.assertFalse(ui._overlay_open())
        run_with_ui(body, FakeManager(0))

    def test_recalled_then_edited_runs_at_once(self):
        def body(ui):
            setup(ui)
            seed_history(ui, ["/model"])
            slow(ui, Keys.Up, *f" {GEMINI}", Keys.ControlM)
            self.assertEqual(ui.cfg.model_id, GEMINI)
            self.assertIn(f"set with /model {GEMINI}",
                          " ".join(ui.output.getvalue().split()))
        run_with_ui(body, FakeManager(0))

    def test_recalled_message_still_sends_on_one_enter(self):
        def body(ui):
            sent = setup(ui)
            seed_history(ui, ["oo subagents ko parallel me run karo"])
            slow(ui, Keys.Up, Keys.ControlM)
            self.assertEqual(sent, ["oo subagents ko parallel me run karo"])
        run_with_ui(body, FakeManager(0))

    def test_typed_slash_model_and_alias_open_the_menu(self):
        def body(ui):
            setup(ui)
            slow(ui, *"/m", Keys.ControlM)
            self.assertTrue(ui._overlay_open())
            slow(ui, Keys.Up, Keys.Up)
            slow(ui, Keys.ControlM, gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, GEMINI)
            self.assertIn("(opened with /m)",
                          " ".join(ui.output.getvalue().split()))
        run_with_ui(body, FakeManager(0))


class CtrlTMustBeALoneKey(unittest.TestCase):
    def test_lone_ctrl_t_opens(self):
        def body(ui):
            setup(ui)
            feed_bytes(ui, "\x14")
            self.assertTrue(ui._overlay_open())
        run_with_ui(body, FakeManager(0))

    def test_ctrl_t_inside_a_burst_does_nothing(self):
        def body(ui):
            setup(ui)
            feed_bytes(ui, "ab\x14")              # 0x14 behind typed text
            self.assertFalse(ui._overlay_open())
            ui.buffer.reset()
            _pause(0.1)
            feed_bytes(ui, "\x14\x1b[A\x1b[A\r")  # Ctrl+T + keys, one read
            self.assertFalse(ui._overlay_open())
            self.assertEqual(ui.cfg.model_id, ERNIE)
        run_with_ui(body, FakeManager(0))


class PickerEnterIsDeliberate(unittest.TestCase):
    def test_enter_too_soon_closes_without_change(self):
        def body(ui):
            setup(ui)
            feed_bytes(ui, "\x14")
            slow(ui, Keys.Up)
            slow(ui, Keys.ControlM, gap=0.05)
            self.assertFalse(ui._overlay_open())
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertEqual(ui._flash[0], "model menu closed — nothing changed")
        run_with_ui(body, FakeManager(0))

    def test_enter_in_a_key_burst_closes_without_change(self):
        def body(ui):
            setup(ui)
            feed_bytes(ui, "\x14")
            _pause(DELIBERATE)
            feed_bytes(ui, "\x1b[A\r")           # ↑ + ⏎ in one read
            self.assertFalse(ui._overlay_open())
            self.assertEqual(ui.cfg.model_id, ERNIE)
        run_with_ui(body, FakeManager(0))

    def test_deliberate_pick_commits_with_truthful_label_and_logs(self):
        def body(ui):
            setup(ui)
            with self.assertLogs("fullagent.tui", logging.WARNING) as logs:
                feed_bytes(ui, "\x14")
                slow(ui, Keys.Up, Keys.Up)
                slow(ui, Keys.ControlM, gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, GEMINI)
            out = " ".join(ui.output.getvalue().split())
            self.assertIn("chosen in the model menu (opened with Ctrl+T)", out)
            self.assertNotIn("you picked it", out)
            text = "\n".join(logs.output)
            self.assertIn("model menu opened with Ctrl+T (raw=['\\x14'])",
                          text)
            self.assertIn("model menu commit", text)
            self.assertIn("keys inside=", text)
            self.assertIn("Up", text)
        run_with_ui(body, FakeManager(0))

    def test_enter_while_prompt_holds_text_closes_and_sends_once(self):
        """Second event: a message in the prompt, the menu open over it,
        ↓ then ⏎ meant to send — no model change, the message is sent
        once by the next Enter."""
        def body(ui):
            sent = setup(ui, GEMINI)
            seed_history(ui, ["oo subagents"])
            slow(ui, Keys.Up)
            self.assertEqual(ui.buffer.text, "oo subagents")
            ui.open_model_selector(via="Ctrl+T", raw=["\x14"])
            slow(ui, Keys.Down)
            with self.assertLogs("fullagent.tui", logging.WARNING) as logs:
                slow(ui, Keys.ControlM, gap=DELIBERATE)
            self.assertIn("prompt holds text", "\n".join(logs.output))
            self.assertFalse(ui._overlay_open())
            self.assertEqual(ui.cfg.model_id, GEMINI)
            self.assertEqual(ui._flash[0], "model menu closed — nothing changed")
            self.assertEqual(ui.buffer.text, "oo subagents")
            slow(ui, Keys.ControlM)
            self.assertEqual(sent, ["oo subagents"])
        run_with_ui(body, FakeManager(0))

    def test_paste_closes_the_menu(self):
        def body(ui):
            sent = setup(ui)
            feed_bytes(ui, "\x14")
            slow(ui, Keys.Down)
            _pause()
            feed_bytes(ui, "\x1b[200~oo subagents\x1b[201~")
            self.assertFalse(ui._overlay_open())
            self.assertEqual(ui.buffer.text, "oo subagents")
            slow(ui, Keys.ControlM, gap=DELIBERATE)
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertEqual(sent, ["oo subagents"])
        run_with_ui(body, FakeManager(0))

    def test_editing_keys_close_the_menu_and_apply(self):
        def body(ui):
            setup(ui)
            slow(ui, *"hi")
            # Backspace, ←, Ctrl+F (→), Delete at the end
            for key, want in ((Keys.ControlH, "h"), (Keys.Left, "h"),
                              (Keys.ControlF, "h"), (Keys.Delete, "h")):
                ui.open_model_selector(via="Ctrl+T", raw=["\x14"])
                slow(ui, key)
                self.assertFalse(ui._overlay_open(), key)
                self.assertEqual(ui.buffer.text, want, key)
            self.assertEqual(ui.cfg.model_id, ERNIE)
        run_with_ui(body, FakeManager(0))

    def test_esc_enter_with_menu_open_changes_nothing(self):
        def body(ui):
            sent = setup(ui)
            seed_history(ui, ["/model"])
            slow(ui, Keys.Up)
            ui.open_model_selector(via="Ctrl+T", raw=["\x14"])
            _pause(DELIBERATE)
            feed_bytes(ui, "\x1b\r")
            self.assertEqual(ui.cfg.model_id, ERNIE)
            self.assertNotIn("/model", sent)
        run_with_ui(body, FakeManager(0))

    def test_menu_closes_itself_when_left_alone(self):
        def body(ui):
            setup(ui)
            old = getattr(tui, "MODEL_PICKER_IDLE_CLOSE", None)
            tui.MODEL_PICKER_IDLE_CLOSE = 0.3
            try:
                feed_bytes(ui, "\x14")
                slow(ui, Keys.Down)
                _pause(0.2)
                slow(ui, Keys.Down)                 # interaction re-arms
                _pause(0.2)
                self.assertTrue(ui._overlay_open())
                _pause(0.35)
                with self.assertLogs("fullagent.tui", logging.WARNING) as lg:
                    self.assertFalse(ui._overlay_open())
                self.assertIn("auto-closed", "\n".join(lg.output))
                slow(ui, Keys.ControlM, gap=DELIBERATE)
                self.assertEqual(ui.cfg.model_id, ERNIE)
            finally:
                tui.MODEL_PICKER_IDLE_CLOSE = old
        run_with_ui(body, FakeManager(0))


if __name__ == "__main__":
    unittest.main()
