"""Live swarm dashboard wired into the REAL fullagent.tui.UI, headless.

The real prompt_toolkit Application is built (pipe input, dummy output)
and keys go through its real KeyProcessor, so filters, eager bindings and
the buffer behave exactly as in a terminal. The subagent manager is a
thread-safe fake with the real manager's surface (snapshot / on_progress
/ stop / stop_all); worker threads fire progress events like the real
swarm does.
"""
import asyncio
import io
import os
import statistics
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="swarmtui_"))

from prompt_toolkit.application import create_app_session  # noqa: E402
from prompt_toolkit.application.current import set_app  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.key_binding.key_processor import KeyPress  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402
from rich.console import Console  # noqa: E402

from fullagent import config, swarmdash  # noqa: E402
from fullagent.agent import Turn  # noqa: E402
from fullagent.config import DEFAULT_MODEL_ID, Config  # noqa: E402
from fullagent.tui import UI  # noqa: E402


class FakeManager:
    """Thread-safe stand-in with ParallelSubagents' UI-facing surface."""

    def __init__(self, n=4, state="running"):
        self.lock = threading.Lock()
        self.callbacks = []
        self.stopped = []
        self.stop_all_calls = 0
        self.snapshots = 0
        self.workers = [
            {"id": f"sa-{i:04d}", "name": f"coder-{i}", "role": "coder",
             "state": state, "task": f"task {i}",
             "current_action": f"read_file f{i}.py", "progress_pct": 10,
             "run_ms": 1000, "elapsed_ms": 1000, "tokens_in": 10,
             "tokens_out": 5, "tokens_per_sec": 1.0, "tool_calls": 0,
             "steps": 1, "max_steps": 20, "recent": [], "delivered": False}
            for i in range(n)]
        self.pending = 0

    def on_progress(self, cb):
        with self.lock:
            if cb not in self.callbacks:
                self.callbacks.append(cb)

    def emit(self, idx=0, kind="subagent.progress"):
        for cb in list(self.callbacks):
            cb(None, kind)

    def set_state(self, idx, state):
        with self.lock:
            self.workers[idx]["state"] = state
            if state in ("done", "error", "cancelled"):
                self.workers[idx]["progress_pct"] = 100
        self.emit(idx, "subagent.done")

    def snapshot(self):
        with self.lock:
            self.snapshots += 1
            workers = [dict(w) for w in self.workers]
        active = sum(w["state"] in ("queued", "running", "cancelling")
                     for w in workers)
        counts = {}
        for w in workers:
            counts[w["state"]] = counts.get(w["state"], 0) + 1
        return {"workers": workers, "counts": counts, "active": active,
                "max_active": 16, "pending_results": self.pending,
                "tokens_per_sec": 12.0, "swarm_started_at": time.time() - 5}

    def stop(self, worker_id):
        with self.lock:
            self.stopped.append(worker_id)
            for w in self.workers:
                if w["id"] == worker_id:
                    w["state"] = "cancelled"
        self.emit(0, "subagent.cancelled")

    def stop_all(self):
        with self.lock:
            self.stop_all_calls += 1
            n = 0
            for w in self.workers:
                if w["state"] in ("queued", "running"):
                    w["state"] = "cancelled"
                    n += 1
        return n


def _agent(mgr):
    agent = SimpleNamespace(
        autonomy=3, session_id="swarm123", messages=[], turns=[],
        model=SimpleNamespace(id=DEFAULT_MODEL_ID, context_window=262144,
                              label="Test Model"),
        goal=SimpleNamespace(status=lambda: None),
        skill_forge=SimpleNamespace(registry={}),
        save_session=lambda: None)
    agent.subagents = mgr
    return agent


def run_with_ui(test_fn, mgr):
    """Build the real UI inside an event loop and run test_fn(ui)."""
    async def main():
        with create_pipe_input() as inp, \
                create_app_session(input=inp, output=DummyOutput()):
            cfg = Config()
            cfg.extra = dict(cfg.extra or {})
            ui = UI(cfg, _agent(mgr))
            ui.output = io.StringIO()
            ui.console = Console(file=ui.output, width=100,
                                 color_system=None)
            try:
                with set_app(ui.app):
                    return test_fn(ui)
            finally:
                ui.__dict__.pop("app", None)   # lets the pulse thread exit
    return asyncio.run(main())


def press(ui, *keys):
    kp = ui.app.key_processor
    for k in keys:
        if isinstance(k, KeyPress):
            kp.feed(k)
        elif len(k) == 1:
            kp.feed(KeyPress(k, k))
        else:
            kp.feed(KeyPress(k, ""))
    kp.process_keys()


def _lines(frags):
    return "".join(t for _, t in frags).split("\n") if frags else []


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class DashboardKeyTests(unittest.TestCase):
    def test_ctrl_g_toggles_and_esc_closes(self):
        def body(ui):
            self.assertFalse(ui._swarm_dash_open())
            press(ui, Keys.ControlG)
            self.assertTrue(ui._swarm_dash_open())
            text = "\n".join(_lines(ui._swarm_dash_fragments()))
            self.assertIn("SWARM", text)
            self.assertIn("coder-0", text)
            press(ui, Keys.ControlG)
            self.assertFalse(ui._swarm_dash_open())
            press(ui, Keys.ControlG)
            press(ui, KeyPress(Keys.Escape, "\x1b"))
            self.assertFalse(ui._swarm_dash_open())
        run_with_ui(body, FakeManager(3))

    def test_down_on_empty_prompt_opens_even_while_busy(self):
        def body(ui):
            ui._busy = True                    # a main turn is running
            press(ui, Keys.Down)
            self.assertTrue(ui._swarm_dash_open())
            # Esc closes the dashboard — it must NOT cancel the turn
            press(ui, KeyPress(Keys.Escape, "\x1b"))
            self.assertFalse(ui._swarm_dash_open())
            self.assertFalse(ui._cancel_flag.is_set())
            ui._busy = False
        run_with_ui(body, FakeManager(2))

    def test_down_without_swarm_is_history_not_dashboard(self):
        def body(ui):
            press(ui, Keys.Down)
            self.assertFalse(ui._swarm_dash_open())
        run_with_ui(body, FakeManager(2, state="done"))

    def test_slash_swarm_toggles(self):
        def body(ui):
            ui._handle_slash("/swarm")
            self.assertTrue(ui._swarm_dash_open())
            ui._handle_slash("/swarm")
            self.assertFalse(ui._swarm_dash_open())
        run_with_ui(body, FakeManager(1))

    def test_keys_do_not_leak_and_typing_resumes(self):
        def body(ui):
            press(ui, "h", "i")
            self.assertEqual(ui.buffer.text, "hi")
            press(ui, Keys.ControlG)
            press(ui, "j", "k", "z", "a", "b", " ", Keys.Enter,
                  Keys.ControlH, Keys.Left, Keys.ControlU, Keys.ControlW,
                  Keys.ControlD, Keys.ControlT, Keys.ControlR, Keys.Tab,
                  "q")
            self.assertFalse(ui._swarm_dash_open())   # q closed it
            self.assertEqual(ui.buffer.text, "hi")
            self.assertIsNone(ui.overlay)
            press(ui, "!")
            self.assertEqual(ui.buffer.text, "hi!")
        run_with_ui(body, FakeManager(3))

    def test_selection_expand_and_cancel_selected(self):
        mgr = FakeManager(4)

        def body(ui):
            press(ui, Keys.ControlG)
            ui._swarm_dash_fragments()        # first frame syncs the order
            st = ui._swarm_state()
            self.assertEqual(st.selected_id, "sa-0000")
            press(ui, Keys.Down, "j")
            self.assertEqual(st.selected_id, "sa-0002")
            press(ui, "k")
            self.assertEqual(st.selected_id, "sa-0001")
            press(ui, Keys.Enter)
            self.assertTrue(st.expanded)
            text = "\n".join(_lines(ui._swarm_dash_fragments()))
            self.assertIn("details", text)
            self.assertIn("task 1", text)
            press(ui, "x")
            self.assertTrue(_wait(lambda: mgr.stopped == ["sa-0001"]),
                            mgr.stopped)
            press(ui, "X")
            self.assertTrue(_wait(lambda: mgr.stop_all_calls == 1))
            self.assertEqual(ui.buffer.text, "")
        run_with_ui(body, mgr)

    def test_dashboard_rows_fit_terminal_width(self):
        def body(ui):
            press(ui, Keys.ControlG)
            for width in (60, 80, 133):
                ui._width = lambda w=width: w
                for line in _lines(ui._swarm_dash_fragments()):
                    self.assertEqual(swarmdash.display_width(line), width)
        run_with_ui(body, FakeManager(5))


class BoardStabilityTests(unittest.TestCase):
    def test_board_height_stable_as_workers_finish_then_collapses(self):
        mgr = FakeManager(5)

        def body(ui):
            ui._board_keeper = swarmdash.BoardKeeper(hold=0.4)
            heights = []
            for i in range(5):
                ui._invalidate_agents()
                self.assertTrue(ui._agents_visible())
                heights.append(len(_lines(ui._agents_fragments())))
                mgr.set_state(i, "done")
            ui._invalidate_agents()
            self.assertTrue(ui._agents_visible())        # settle hold
            heights.append(len(_lines(ui._agents_fragments())))
            self.assertEqual(set(heights), {6}, heights)
            time.sleep(0.45)
            ui._invalidate_agents()
            self.assertFalse(ui._agents_visible())
            self.assertEqual(ui._agents_fragments(), [])
        run_with_ui(body, mgr)


class FloodTests(unittest.TestCase):
    WORKERS = 16
    EVENTS = 300            # per worker → 4800 progress events

    def test_thousands_of_events_do_not_flood_render_or_typing(self):
        mgr = FakeManager(self.WORKERS)

        def body(ui):
            inval = [0]
            lock = threading.Lock()

            def counting_invalidate():
                with lock:
                    inval[0] += 1

            ui._invalidate = counting_invalidate
            ui._agents_snapshot()            # wires the progress hook
            self.assertTrue(mgr.callbacks)
            press(ui, Keys.ControlG)         # dashboard open: worst case
            baseline_heights = len(_lines(ui._agents_fragments()))
            go = threading.Event()

            def worker(idx):
                go.wait()
                for n in range(self.EVENTS):
                    with mgr.lock:
                        w = mgr.workers[idx]
                        w["tool_calls"] += 1
                        w["progress_pct"] = 10 + n % 80
                        w["state"] = ("queued" if n % 50 == 49
                                      else "running")
                    mgr.emit(idx)
                    if n % 5 == 0:          # ~1.5 s of streaming
                        time.sleep(0.025)

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in range(self.WORKERS)]
            for t in threads:
                t.start()
            t0 = time.monotonic()
            go.set()
            frame_ms, key_ms, heights = [], [], set()
            # close the dashboard so the compact board + typing are timed
            press(ui, Keys.ControlG)
            while any(t.is_alive() for t in threads):
                f0 = time.perf_counter()
                ui._agents_visible()
                rows = ui._agents_fragments()
                ui._top_fragments()
                ui._hud_fragments()
                ui._meta_fragments()
                ui._swarm_dash_fragments()
                frame_ms.append((time.perf_counter() - f0) * 1000)
                heights.add(len(_lines(rows)))
                k0 = time.perf_counter()
                press(ui, "a")
                key_ms.append((time.perf_counter() - k0) * 1000)
                time.sleep(0.005)
            for t in threads:
                t.join()
            elapsed = time.monotonic() - t0
            total_events = self.WORKERS * self.EVENTS
            print(f"\nflood: {total_events} events in {elapsed:.2f}s -> "
                  f"{inval[0]} invalidates; frame median "
                  f"{statistics.median(frame_ms):.2f}ms max "
                  f"{max(frame_ms):.2f}ms; key median "
                  f"{statistics.median(key_ms):.2f}ms")
            self.assertGreater(len(frame_ms), 5)
            # coalesced: ≤ ~12 frames/s (+ leading frame + slack), not
            # one invalidate per event
            budget = elapsed * 12 + 4
            self.assertLessEqual(inval[0], budget,
                                 f"{inval[0]} invalidates for "
                                 f"{total_events} events in {elapsed:.2f}s")
            self.assertLess(inval[0], total_events / 20)
            self.assertEqual(heights, {baseline_heights},
                             "board height changed under event load")
            self.assertLess(statistics.median(frame_ms), 15.0, frame_ms[:20])
            self.assertLess(statistics.median(key_ms), 5.0, key_ms[:20])
            self.assertEqual(ui.buffer.text, "a" * len(key_ms))
        run_with_ui(body, mgr)


def _stub_ui(pending_fn=None, extra=None, buffer_text=""):
    """__new__-built UI (no app) driving the real turn path."""
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra=dict(extra or {"render_markdown": False}),
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
    ui._width = lambda: 100
    ui._invalidate = lambda: None
    ui._start_spinner = lambda: None
    ui._stop_spinner = lambda: None
    ui._set_flash = lambda *a, **k: None
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=100, color_system=None)
    ui.overlay = None
    ui.buffer = SimpleNamespace(text=buffer_text)
    ui.user_lines = []
    ui._emit_user = lambda text: ui.user_lines.append(text)
    turns = []
    started = threading.Event()

    def run(text, *a, **kw):
        turns.append(text)
        started.set()
        return Turn(text, assistant_text="ok")

    ui.agent = SimpleNamespace(
        autonomy=3, session_id="s", messages=[], turns=[],
        model=SimpleNamespace(id=DEFAULT_MODEL_ID, context_window=262144,
                              label="Test Model"),
        save_session=lambda: None, goal=SimpleNamespace(status=lambda: None),
        skill_forge=SimpleNamespace(registry={}), run_turn=run,
        thinking_visible=False, thinking_expanded=False)
    if pending_fn is not None:
        ui.agent.swarm_pending_results = pending_fn
    ui.turns, ui.started = turns, started
    return ui


class AutoContinueTests(unittest.TestCase):
    def _gate(self, ui, debounce=0.8):
        rt = ui._swarm_rt()
        rt.gate.debounce = debounce
        return rt

    def test_fires_once_debounced_when_idle(self):
        pending = [0]
        ui = _stub_ui(lambda: pending[0])
        rt = self._gate(ui)
        now = 100.0
        self.assertFalse(ui._swarm_auto_continue_check(now))
        pending[0] = 1
        rt.gate.note_event(now)
        self.assertFalse(ui._swarm_auto_continue_check(now + 0.1))
        pending[0] = 3                       # two more finish right after
        rt.gate.note_event(now + 0.3)
        self.assertFalse(ui._swarm_auto_continue_check(now + 0.9))
        self.assertTrue(ui._swarm_auto_continue_check(now + 1.2))
        self.assertTrue(ui.started.wait(3))
        self.assertTrue(_wait(lambda: not ui._busy))
        # the same unconsumed result set never re-triggers
        self.assertFalse(ui._swarm_auto_continue_check(now + 5))
        self.assertEqual(len(ui.turns), 1)
        self.assertEqual(ui.turns[0], swarmdash.AUTO_CONTINUE_PROMPT)
        self.assertIs(type(ui.turns[0]), str)
        self.assertEqual(ui.user_lines, [],
                         "follow-up must not be echoed as a user message")
        out = ui.output.getvalue()
        self.assertIn("3 swarm results ready · continuing", out)
        self.assertNotIn("❯", out)

    def test_not_while_busy(self):
        ui = _stub_ui(lambda: 2)
        self._gate(ui, debounce=0.0)
        ui._busy = True
        self.assertFalse(ui._swarm_auto_continue_check(10.0))
        ui._busy = False
        ui._turn_alive = True
        self.assertFalse(ui._swarm_auto_continue_check(11.0))
        ui._turn_alive = False
        self.assertEqual(ui.turns, [])

    def test_not_when_user_typed(self):
        ui = _stub_ui(lambda: 2, buffer_text="half-typed message")
        self._gate(ui, debounce=0.0)
        self.assertFalse(ui._swarm_auto_continue_check(10.0))
        self.assertEqual(ui.turns, [])

    def test_not_when_disabled(self):
        ui = _stub_ui(lambda: 2, extra={"swarm_auto_continue": False})
        self._gate(ui, debounce=0.0)
        self.assertFalse(ui._swarm_auto_continue_check(10.0))
        ui.agent.cfg = SimpleNamespace(extra={"swarm_auto_continue": False})
        ui.cfg.extra["swarm_auto_continue"] = True
        self.assertFalse(ui._swarm_auto_continue_check(11.0))
        self.assertEqual(ui.turns, [])

    def test_absent_agent_api_never_fires(self):
        ui = _stub_ui(None)
        self._gate(ui, debounce=0.0)
        self.assertFalse(ui._swarm_auto_continue_check(10.0))

    def test_pulse_thread_fires_after_worker_events(self):
        """End to end: workers finish (events from threads) while the agent
        is idle; the pulse thread starts exactly ONE follow-up turn."""
        pending = [0]
        ui = _stub_ui(lambda: pending[0])
        self._gate(ui, debounce=0.3)
        mgr = FakeManager(3)
        ui.agent.subagents = mgr
        ui._agents_snapshot()                # hook progress events

        def finish(i):
            mgr.set_state(i, "done")
            with mgr.lock:
                pending[0] += 1

        threads = [threading.Thread(target=finish, args=(i,))
                   for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        mgr.emit(0, "subagent.done")
        self.assertTrue(ui.started.wait(5), "no follow-up turn started")

        def drained():
            pending[0] = 0               # the agent consumed the results
            return not ui._busy

        self.assertTrue(_wait(drained))
        time.sleep(0.5)
        self.assertEqual(len(ui.turns), 1, ui.turns)


if __name__ == "__main__":
    unittest.main()
