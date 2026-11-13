"""Regression: per-turn UI cost must stay flat as a session grows.

The user-visible bug: the longer a session ran, the slower the TUI got,
until it stopped answering keys — while the API itself answered fine.
Causes in the UI layer were O(session) work on prompt_toolkit's render
loop (context estimate over the whole history, goal fold, git
subprocess, skills disk scan), an O(n^2) reasoning preview, recursive
focus-mode continuations and unbounded per-session buffers.

Headless and deterministic: a fake clock drives the UI's TTL caches, the
real fragment builders run as the render loop would call them, and the
cost is compared between an early and a late window of the same session.
"""
import io
import statistics
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rich.console import Console

from fullagent import client
from fullagent.agent import ToolEvent, Turn
from fullagent.config import DEFAULT_MODEL_ID
from fullagent.thinking import ReasoningStream
from fullagent.tui import UI

_perf = time.perf_counter
_BODY = "value = compute(alpha, beta)  # a line of realistic code text\n"


def _make_ui(messages):
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra={"render_markdown": False},
                             model_id=DEFAULT_MODEL_ID, effort="high",
                             show_reasoning=False, auto_approve=True)
    ui._busy = False
    ui._cancel_flag = threading.Event()
    ui._focus_remaining = 0
    ui._status_text = ""
    ui._approve_request = None
    ui._spinner_i = 0
    ui._flash = None
    ui._diff_history = []
    ui._detail_expanded = False
    ui._last_detail_event = None
    ui._width = lambda: 120
    ui._invalidate = lambda: None
    ui._start_spinner = lambda: None
    ui._stop_spinner = lambda: None
    ui._set_flash = lambda *a, **k: None
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=120, color_system=None)
    ui.overlay = None
    ui.agent = SimpleNamespace(
        autonomy=3, session_id="abcd1234", messages=messages,
        model=SimpleNamespace(id=DEFAULT_MODEL_ID, context_window=262144,
                              label="Test Model"),
        save_session=lambda: None,
        goal=SimpleNamespace(status=lambda: None),
        skill_forge=SimpleNamespace(registry={}),
        thinking_visible=False, thinking_expanded=False)
    return ui


def _frame(ui):
    """Everything prompt_toolkit calls on its loop for one redraw."""
    ui._top_fragments()
    ui._bottom_fragments()
    ui._hud_fragments()
    ui._meta_fragments()
    ui._agents_visible()


class PerTurnCostFlatTests(unittest.TestCase):
    TURNS = 120
    TOKENS = 120

    def test_per_turn_ui_cost_flat_over_many_turns(self):
        msgs: list = []
        ui = _make_ui(msgs)
        clock = [1000.0]
        per_turn: list[float] = []

        def run(text, on_token, on_reasoning, on_tool_call, on_tool_update,
                *a, **kw):
            msgs.append({"role": "user", "content": text})
            for k in range(2):
                ev = ToolEvent("read_file", {"path": f"f{k}.py"},
                               result="", status="running")
                on_tool_call(ev)
                ev.result = "10 lines total, showing 1..10"
                ev.status = "done"
                on_tool_update(ev)
                # history grows by ~10KB per tool result
                msgs.append({"role": "tool", "tool_call_id": f"c{k}",
                             "content": _BODY * 160})
            for i in range(self.TOKENS):
                clock[0] += 0.02          # 50 tok/s of fake time
                on_token(f"w{i}" + ("\n" if i % 15 == 14 else " "))
                if i % 4 == 0:            # ~12 redraws per fake second
                    _frame(ui)
            msgs.append({"role": "assistant", "content": _BODY * 40})
            return Turn(text, assistant_text="ok")

        ui.agent.run_turn = run
        fake = lambda: clock[0]  # noqa: E731
        with patch("time.time", fake), patch("time.monotonic", fake):
            for t in range(self.TURNS):
                t0 = _perf()
                ui._run_turn_thread(f"turn {t}")
                per_turn.append(_perf() - t0)
                if len(ui.output.getvalue()) > 500_000:
                    ui.output.seek(0)
                    ui.output.truncate()
        early = statistics.median(per_turn[5:20])
        late = statistics.median(per_turn[-15:])
        self.assertLess(
            late / early, 2.5,
            f"per-turn UI cost grew {late / early:.2f}x over "
            f"{self.TURNS} turns ({early * 1e3:.2f}ms -> {late * 1e3:.2f}ms)")

    def test_render_loop_never_estimates_history_after_first_frame(self):
        """The context meter must not walk the message list on the
        render (event-loop) thread once the first frame is drawn."""
        msgs = [{"role": "user", "content": _BODY * 50}]
        ui = _make_ui(msgs)
        main = threading.get_ident()
        calls: list[int] = []
        real = client.estimate_tokens

        def spy(obj, model_id=""):
            calls.append(threading.get_ident())
            return real(obj, model_id)

        clock = [5000.0]
        fake = lambda: clock[0]  # noqa: E731
        with patch.object(client, "estimate_tokens", spy), \
                patch("time.time", fake):
            _frame(ui)                     # first frame: may be synchronous
            first = len(calls)
            for i in range(60):
                clock[0] += 0.5
                msgs.append({"role": "tool", "content": _BODY * (i + 1)})
                _frame(ui)
            for th in list(threading.enumerate()):
                if th.name.startswith("ui-refresh"):
                    th.join(5)
        on_loop = [c for c in calls[first:] if c == main]
        self.assertEqual(on_loop, [], "estimate_tokens ran on the render loop")
        self.assertGreater(len(calls), first,
                           "context meter must still refresh off-loop")

    def test_off_loop_refresh_is_single_flight(self):
        ui = _make_ui([])
        gate = threading.Event()
        started = []

        def slow():
            started.append(1)
            gate.wait(5)
            return len(started)

        clock = [10.0]
        with patch("time.time", lambda: clock[0]):
            self.assertEqual(ui._bg_cached("k", 1.0, lambda: 0), 0)
            for _ in range(50):
                clock[0] += 2.0
                ui._bg_cached("k", 1.0, slow)
            gate.set()
            for th in list(threading.enumerate()):
                if th.name.startswith("ui-refresh"):
                    th.join(5)
        self.assertEqual(len(started), 1,
                         "stale reads must not pile up refresh threads")


class ReasoningPreviewFlatTests(unittest.TestCase):
    def test_preview_cost_does_not_grow_with_stream_length(self):
        rs = ReasoningStream()

        def cost(n_calls=300):
            t0 = _perf()
            for _ in range(n_calls):
                rs.feed("reasoning about the next step ")
                rs.preview()
            return (_perf() - t0) / n_calls

        early = min(cost() for _ in range(3))
        for _ in range(40_000):            # ~1.2MB of reasoning
            rs.feed("reasoning about the next step ")
        late = min(cost() for _ in range(3))
        self.assertLess(late / early, 2.5,
                        f"preview grew {late / early:.1f}x with stream length")
        self.assertTrue(rs.preview().startswith("💭 …"))
        self.assertIn("next step", rs.preview())
        # full text is still available for the turn record
        self.assertEqual(rs.chars, len(rs.text))


if __name__ == "__main__":
    unittest.main()
