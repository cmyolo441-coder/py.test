"""Qoder-style chrome: welcome card, HUD, footer and live agent board.

Drives real code only — ``fullagent.cliui`` layout helpers and the real
``fullagent.tui.UI`` fragment builders / key actions against stub objects.
Headless: rich writes to a StringIO console, no TTY needed.
"""
import io
import threading
import unittest
from types import SimpleNamespace

from rich.console import Console

from fullagent import cliui
from fullagent.agent import ToolEvent
from fullagent.config import DEFAULT_MODEL_ID
from fullagent.tui import UI


def _width(frags):
    return sum(cliui.display_width(text) for _, text in frags)


def _snapshot(states, role="researcher"):
    return {"workers": [
        {"id": f"ag-{i}", "state": state, "role": role,
         "current_action": f"Read src/file{i}.py",
         "summary": "done" if state == "done" else "",
         "elapsed_ms": i * 1000}
        for i, state in enumerate(states)
    ], "active": sum(1 for s in states if s in cliui.ACTIVE_STATES)}


class _Mgr:
    def __init__(self, states):
        self._snap = _snapshot(states)

    def snapshot(self):
        return self._snap


def _make_ui(states=None, skills=3, autonomy=3):
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra={}, model_id=DEFAULT_MODEL_ID,
                             effort="high", show_reasoning=False)
    ui._busy = False
    ui._cancel_flag = threading.Event()
    ui._focus_remaining = 0
    ui._status_text = ""
    ui._approve_request = None
    ui._width = lambda: 120
    ui._invalidate = lambda: None
    ui._start_spinner = lambda: None
    ui._stop_spinner = lambda: None
    ui._emit_user = lambda text: None

    flashes = []
    ui._set_flash = lambda text, color=None: flashes.append(text)
    ui.flashes = flashes

    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=100, color_system=None)

    calls = []

    def set_autonomy(level):
        calls.append(level)
        ui.agent.autonomy = max(0, min(5, int(level)))
        return "ok"

    ui.agent = SimpleNamespace(
        autonomy=autonomy, set_autonomy=set_autonomy,
        subagents=_Mgr(states or []),
        skill_forge=SimpleNamespace(registry={f"s{i}": None
                                              for i in range(skills)}),
        session_id="abcd1234",
        messages=[],
        model=SimpleNamespace(id=DEFAULT_MODEL_ID, context_window=262144,
                              label="Test Model"),
        autonomy_calls=calls,
    )
    ui._auto_prev = 3
    ui._detail_expanded = False
    ui._last_detail_event = None
    return ui



class CliuiLayoutTests(unittest.TestCase):
    def test_align_pads_to_exact_width(self):
        for width in (24, 60, 100, 137):
            line = cliui.align([("", "left")], [("", "right")], width)
            self.assertEqual(_width(line), width)

    def test_align_keeps_right_badge_on_narrow_terminal(self):
        line = cliui.align([("", "x" * 200)], [("", "tail")], 30)
        self.assertEqual(_width(line), 30)
        self.assertIn("tail", "".join(t for _, t in line))

    def test_agent_chip_names(self):
        self.assertEqual(cliui.short_role("researcher"), "Explore")
        self.assertEqual(cliui.short_role("coder"), "Code")
        self.assertEqual(cliui.short_role("mystery_role"), "Mystery Role")

    def test_agent_board_only_while_active_and_bounded(self):
        self.assertEqual(cliui.agent_board({"workers": []}, width=80,
                                           colors={}), [])
        idle = cliui.agent_board(_snapshot(["done"]), width=80, colors={})
        self.assertEqual(idle, [])
        board = cliui.agent_board(_snapshot(["running"] * 9), width=80,
                                  colors={})
        self.assertEqual(len(board), 1 + cliui.PANEL_LIMIT + 1)
        self.assertIn("9 Agents", "".join(t for _, t in board[0]))
        self.assertIn("Explore", "".join(t for _, t in board[1]))
        self.assertIn("+3 more", "".join(t for _, t in board[-1]))
        for row in board:
            self.assertLessEqual(_width(row), 80)


class UiChromeTests(unittest.TestCase):
    def test_agents_board_visible_only_with_live_workers(self):
        live = _make_ui(["running", "queued"])
        self.assertTrue(live._agents_visible())
        text = "".join(t for _, t in live._agents_fragments())
        self.assertIn("2 Agents", text)
        self.assertIn("Explore", text)

        idle = _make_ui(["done", "done"])
        self.assertFalse(idle._agents_visible())
        self.assertEqual(idle._agents_fragments(), [])

    def test_hud_shows_mode_and_skill_count(self):
        auto = _make_ui(["running"], skills=21, autonomy=3)
        text = "".join(t for _, t in auto._hud_fragments())
        self.assertIn("AUTO", text)
        self.assertIn("21 skills", text)
        self.assertIn("⇧Tab", text)

        yolo = _make_ui([], skills=0, autonomy=5)
        self.assertIn("YOLO",
                      "".join(t for _, t in yolo._hud_fragments()))

    def test_footer_shows_model_context_cwd_and_agents(self):
        ui = _make_ui(["running"] * 8)
        text = "".join(t for _, t in ui._meta_fragments())
        self.assertIn(ui._model().label, text)
        self.assertIn("ctx", text)
        self.assertIn("context", text)
        self.assertIn("8 agents | 8 running", text)
        self.assertEqual(_width(ui._meta_fragments()), 120)

    def test_shift_tab_toggles_auto_mode_both_ways(self):
        ui = _make_ui([], autonomy=3)
        ui._toggle_auto_mode()
        self.assertEqual(ui.agent.autonomy, 4)
        ui._toggle_auto_mode()
        self.assertEqual(ui.agent.autonomy, 3)
        self.assertEqual(ui.agent.autonomy_calls, [4, 3])

    def test_banner_renders_logo_version_and_tips(self):
        ui = _make_ui([])
        ui.print_banner()
        out = ui.output.getvalue()
        self.assertIn("Tips for getting started", out)
        self.assertIn("FullAgent CLI", out)
        self.assertIn(ui._model().label, out)
        self.assertIn("abcd1234", out)
        for idx in range(1, 5):
            self.assertIn(f"{idx}.", out)


class DetailToggleTests(unittest.TestCase):
    def _shell_event(self, lines=60):
        stdout = "\n".join(f"line {i}" for i in range(lines))
        result = f"exit code: 0\n--- stdout ---\n{stdout}"
        return ToolEvent(name="run_command", args={"command": "seq"},
                         result=result, status="done")

    def test_ctrl_o_expands_last_block_and_collapses(self):
        ui = _make_ui([])
        ev = self._shell_event(60)

        compact = ui._live_rows(ev, 80)
        self.assertTrue(any("Ctrl+O" in seg for row in compact
                            for seg, _ in row))
        self.assertFalse(any("line 40" == seg for row in compact
                             for seg, _ in row))

        ui._last_detail_event = (ev, "live")
        ui._toggle_detail_view()
        self.assertTrue(ui._detail_expanded)
        out = ui.output.getvalue()
        self.assertIn("detail ·", out)
        self.assertIn("line 40", out)
        self.assertIn("line 59", out)
        self.assertTrue(ui.flashes and "expanded" in ui.flashes[-1])

        expanded = ui._live_rows(ev, 80)
        self.assertFalse(any("Ctrl+O" in seg for row in expanded
                             for seg, _ in row))

        ui._toggle_detail_view()
        self.assertFalse(ui._detail_expanded)

    def test_ctrl_o_without_history_only_flashes(self):
        ui = _make_ui([])
        ui._toggle_detail_view()
        self.assertTrue(ui._detail_expanded)
        self.assertEqual(ui.output.getvalue(), "")
        self.assertTrue(ui.flashes)


if __name__ == "__main__":
    unittest.main()
