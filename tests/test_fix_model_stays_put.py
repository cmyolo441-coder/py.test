"""v4.1.1 log: "✓ model → …" lines between plain messages, and the same
message answered twice, once under each model.

Investigation: nothing in the code changes the model on its own. The only
write to ``cfg.model_id`` is ``UI._choose_model``, reached from the Ctrl+T
picker or ``/model``; each repeated message in the log was submitted again
after a pick (nothing re-runs a turn). These tests pin that down:

* many plain turns: exactly one run per message, model untouched;
* a pick (picker or /model) starts no turn and re-sends nothing;
* the change line names the action that caused it;
* static guard: no other module may assign ``model_id`` on the config.
"""
import ast
import os
import pathlib
import tempfile
import threading
import unittest

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="fix_stay_"))

from prompt_toolkit.keys import Keys  # noqa: E402

from fullagent.agent import Turn  # noqa: E402
from fullagent.config import MODELS  # noqa: E402

from test_swarm_dash_tui import FakeManager, _wait, press, run_with_ui  # noqa: E402,E501

PKG = pathlib.Path(__file__).resolve().parent.parent / "fullagent"


def _install_fake_turn(ui, calls):
    def run_turn(text, on_token, on_reasoning, on_tool_call, on_tool_update,
                 on_status, approve, should_cancel=None, **kw):
        calls.append((text, ui.cfg.model_id))
        t = Turn(user_text=text)
        on_token("ok\n")
        t.assistant_text = "ok"
        t.duration = 0.05
        return t
    ui.agent.run_turn = run_turn
    ui.agent._cancel_flag = threading.Event()


def _submit(ui, text):
    for ch in text:
        press(ui, ch)
    press(ui, Keys.ControlM)


class ModelStaysPut(unittest.TestCase):
    def test_plain_turns_run_once_each_on_one_model(self):
        def body(ui):
            calls = []
            _install_fake_turn(ui, calls)
            start = ui.cfg.model_id
            msgs = ["hello", "kya ho raha hai", "hii", "hello", "do it"]
            for i, m in enumerate(msgs, 1):
                _submit(ui, m)
                self.assertTrue(_wait(lambda: len(calls) == i
                                      and not ui._busy, 5))
            self.assertEqual([c[0] for c in calls], msgs)
            self.assertEqual({c[1] for c in calls}, {start})
            self.assertEqual(ui.cfg.model_id, start)
            self.assertNotIn("✓ model", ui.output.getvalue())
        run_with_ui(body, FakeManager(0))

    def test_picker_pick_starts_no_turn_and_names_the_reason(self):
        def body(ui):
            calls = []
            _install_fake_turn(ui, calls)
            _submit(ui, "hello")
            self.assertTrue(_wait(lambda: len(calls) == 1
                                  and not ui._busy, 5))
            before = ui.cfg.model_id
            threading.Event().wait(0.1)   # Ctrl+T as a lone, human key
            press(ui, Keys.ControlT)
            press(ui, Keys.Down)
            # a picker Enter commits only once the menu was open a moment
            # (fix_model_trigger) — a human-speed pause
            threading.Event().wait(0.5)
            press(ui, Keys.ControlM)
            self.assertNotEqual(ui.cfg.model_id, before)
            self.assertIn("chosen in the model menu (opened with Ctrl+T)",
                          " ".join(ui.output.getvalue().split()))
            # nothing re-ran: the earlier message was not sent again
            threading.Event().wait(0.3)
            self.assertEqual(len(calls), 1)
            self.assertFalse(ui._busy)
        run_with_ui(body, FakeManager(0))

    def test_slash_model_names_the_reason_and_starts_no_turn(self):
        def body(ui):
            calls = []
            _install_fake_turn(ui, calls)
            other = next(m for m in MODELS if m.id != ui.cfg.model_id)
            _submit(ui, f"/model {other.id}")
            self.assertTrue(_wait(lambda: ui.cfg.model_id == other.id, 3))
            self.assertIn(f"set with /model {other.id}",
                          " ".join(ui.output.getvalue().split()))
            threading.Event().wait(0.3)
            self.assertEqual(calls, [])
        run_with_ui(body, FakeManager(0))


def _model_id_writes():
    """(file, line) of every ``<x>.model_id = …`` in the package."""
    hits = []
    for path in PKG.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "model_id":
                    hits.append((path.name, node.lineno,
                                 ast.unparse(t.value)))
    return hits


def test_only_the_users_pick_writes_the_session_model():
    writes = _model_id_writes()
    cfg_writes = [w for w in writes if "cfg" in w[2]]
    # tui._choose_model (the user's pick) and config.load's default for a
    # missing/unknown id are the only places allowed to set it
    assert {(f, v) for f, _, v in cfg_writes} <= {("tui.py", "self.cfg"),
                                                  ("config.py", "cfg")}, \
        cfg_writes
    tui_lines = [ln for f, ln, v in cfg_writes if f == "tui.py"]
    src = (PKG / "tui.py").read_text(encoding="utf-8").splitlines()
    for ln in tui_lines:
        # walk up to the enclosing def
        i = ln - 1
        while i >= 0 and not src[i].lstrip().startswith("def "):
            i -= 1
        assert "def _choose_model" in src[i], (ln, src[i])


if __name__ == "__main__":
    unittest.main()
