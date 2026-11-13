"""Pure swarm dashboard renderer + timing helpers (fullagent.swarmdash).

Headless and deterministic: no prompt_toolkit app, explicit clocks.
"""
import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="swarmdash_"))

from fullagent import swarmdash as sd  # noqa: E402
from fullagent.tui import C  # noqa: E402

WIDTHS = (40, 48, 60, 80, 100, 120, 160, 220)


def _text(row):
    return "".join(t for _, t in row)


def _mixed_snapshot():
    return {
        "active": 3, "max_active": 8, "pending_results": 2,
        "tokens_per_sec": 86.4, "swarm_started_at": 1000.0,
        "workers": [
            {"id": "sa-1", "name": "coder-1", "role": "coder",
             "state": "running", "current_action": "edit_file src/app.py",
             "progress_pct": 62, "run_ms": 72_000, "tokens_in": 12_345,
             "tokens_out": 4100, "tokens_per_sec": 41.2, "tool_calls": 7,
             "lines_added": 12, "lines_removed": 3, "max_steps": 20,
             "steps": 12, "recent": ["read_file a", "edit_file b"],
             "files_touched": ["src/app.py", "src/b.py"],
             "task": "Implement the feature across the module"},
            {"id": "sa-2", "name": "researcher-2", "role": "researcher",
             "state": "queued", "task": "Survey the API surface",
             "progress_pct": 0, "run_ms": 0},
            {"id": "sa-3", "name": "tester-3", "role": "tester",
             "state": "done", "summary": "All 12 tests pass",
             "progress_pct": 100, "run_ms": 30_000, "delivered": False},
            {"id": "sa-4", "name": "broken-4", "role": "weird_role",
             "state": "error", "error": "boom\nwith\ttabs\x1b[31m",
             "run_ms": 1500},
            {"id": "sa-5", "name": "a-very-long-worker-name-that-overflows",
             "role": "reviewer", "state": "running", "steps": 3,
             "max_steps": 20, "elapsed_ms": 5000,
             "current_action": "日本語のアクション " * 10},
            {"id": "sa-6", "name": "c6", "role": "planner",
             "state": "cancelling", "current_action": "cancellation requested"},
            {"id": "sa-7", "name": "c7", "role": "devops",
             "state": "cancelled", "summary": "Cancelled"},
        ],
    }


class RendererWidthTests(unittest.TestCase):
    def test_every_row_fits_width_exactly_for_mixed_states(self):
        snap = _mixed_snapshot()
        for expanded in (False, True):
            st = sd.DashState(open=True, expanded=expanded)
            for w in WIDTHS:
                rows = sd.render_dashboard(snap, width=w, colors=C, state=st,
                                           now=12.3, wall_now=1151.0)
                self.assertGreater(len(rows), 5)
                for r in rows:
                    self.assertEqual(sd.row_width(r), w,
                                     (w, expanded, _text(r)))
                    self.assertNotIn("\n", _text(r))

    def test_header_totals_and_rows(self):
        snap = _mixed_snapshot()
        rows = sd.render_dashboard(snap, width=160, colors=C,
                                   state=sd.DashState(open=True),
                                   now=0.0, wall_now=1151.0)
        text = "\n".join(_text(r) for r in rows)
        self.assertIn("SWARM", _text(rows[0]))
        self.assertIn("02:31", _text(rows[0]))           # wall time
        self.assertIn("3/8 active", text)
        self.assertIn("2 running", text)
        self.assertIn("1 queued", text)
        self.assertIn("1 done", text)
        self.assertIn("1 failed", text)
        self.assertIn("86 tok/s", text)
        self.assertIn("2 results → main", text)
        self.assertIn("coder-1", text)
        self.assertIn("Code", text)                       # role chip
        self.assertIn("62%", text)
        self.assertIn("01:12", text)                      # run_ms mm:ss
        self.assertIn("12k→4.1k", text)
        self.assertIn("41t/s", text)
        self.assertIn("+12 −3", text)
        self.assertIn("✓", text)
        self.assertIn("✗", text)
        # running rows come first
        body = [_text(r) for r in rows[4:]]
        first = next(i for i, t in enumerate(body) if "coder-1" in t)
        done = next(i for i, t in enumerate(body) if "tester-3" in t)
        self.assertLess(first, done)

    def test_selected_row_highlighted_and_detail_panel(self):
        snap = _mixed_snapshot()
        st = sd.DashState(open=True, expanded=True)
        sd.render_dashboard(snap, width=120, colors=C, state=st, now=0.0)
        st.selected_id = "sa-1"
        rows = sd.render_dashboard(snap, width=120, colors=C, state=st,
                                   now=0.0)
        sel = [r for r in rows if "coder-1" in _text(r)][0]
        self.assertTrue(any(sd.SELECTED_BG in s for s, _ in sel))
        text = "\n".join(_text(r) for r in rows)
        self.assertIn("details", text)
        self.assertIn("Implement the feature", text)
        self.assertIn("› edit_file b", text)
        self.assertIn("src/app.py", text)

    def test_never_raises_on_junk(self):
        junk = [None, 5, "x", [], {"workers": "nope"},
                {"workers": [None, 3, "s", {}]},
                {"workers": [{"state": object(), "id": None,
                              "tokens_in": "NaN", "progress_pct": "zz",
                              "run_ms": float("inf"), "recent": 7,
                              "files_touched": "notalist",
                              "name": b"bytes", "role": None}],
                 "active": "?", "max_active": None,
                 "tokens_per_sec": "fast", "swarm_started_at": "x"}]
        for snap in junk:
            for w in (1, 5, 11, 12, 40, 80):
                for st in (None, sd.DashState(open=True, expanded=True)):
                    rows = sd.render_dashboard(snap, width=w, colors=None,
                                               state=st)
                    self.assertTrue(rows)
                    for r in rows:
                        self.assertEqual(sd.row_width(r), w)
        # a broken colors mapping / width still renders something
        rows = sd.render_dashboard(_mixed_snapshot(), width="abc",
                                   colors="nope")
        self.assertTrue(rows)

    def test_narrow_terminal_keeps_essentials(self):
        rows = sd.render_dashboard(_mixed_snapshot(), width=40, colors=C,
                                   state=sd.DashState(open=True), now=0.0)
        text = "\n".join(_text(r) for r in rows)
        self.assertIn("coder-1", text)
        self.assertIn("01:12", text)
        self.assertIn("62%", text)
        self.assertIn("Esc" if "Esc" in text else "select", text)

    def test_empty_swarm_message(self):
        rows = sd.render_dashboard({"workers": []}, width=80, colors=C)
        self.assertIn("no subagents yet", "\n".join(_text(r) for r in rows))

    def test_list_scrolls_and_reserves_height(self):
        workers = [{"id": f"w{i}", "name": f"w{i}", "state": "running",
                    "role": "coder"} for i in range(30)]
        st = sd.DashState(open=True)
        rows = sd.render_dashboard({"workers": workers}, width=100,
                                   colors=C, state=st, max_list_rows=6)
        h = len(rows)
        st.end()
        rows = sd.render_dashboard({"workers": workers}, width=100,
                                   colors=C, state=st, max_list_rows=6)
        text = "\n".join(_text(r) for r in rows)
        self.assertIn("w29", text)
        self.assertIn("↑ 24 more", text)
        self.assertEqual(len(rows), h)
        # workers finishing/leaving never shrink the open dashboard
        rows = sd.render_dashboard({"workers": workers[:2]}, width=100,
                                   colors=C, state=st, max_list_rows=6)
        self.assertEqual(len(rows), h)


class ValueHelperTests(unittest.TestCase):
    def test_progress_bar_values(self):
        for cells in (1, 6, 10, 33):
            for pct in (-5, 0, 0.4, 12.5, 50, 99.9, 100, 250, "x", None):
                filled, empty = sd.bar_text(pct, cells)
                self.assertEqual(len(filled) + len(empty), cells)
        self.assertEqual(sd.bar_text(0, 10), ("", "░" * 10))
        self.assertEqual(sd.bar_text(100, 10), ("█" * 10, ""))
        self.assertEqual(sd.bar_text(50, 10), ("█" * 5, "░" * 5))
        filled, _ = sd.bar_text(55, 10)        # 5.5 cells → half block
        self.assertEqual(filled, "█" * 5 + "▌")
        frags = sd.bar_fragments(37, 10, C)
        self.assertEqual(sd.row_width(frags), 10)

    def test_progress_fallbacks(self):
        self.assertEqual(sd.progress_of({"state": "running",
                                         "progress_pct": 40}), 40)
        self.assertEqual(sd.progress_of({"state": "running", "steps": 5,
                                         "max_steps": 20}), 25)
        self.assertEqual(sd.progress_of({"state": "done"}), 100)
        self.assertEqual(sd.progress_of({"state": "queued"}), 0)
        self.assertEqual(sd.progress_of({"state": "running",
                                         "progress_pct": 400}), 100)

    def test_time_and_count_formatting(self):
        self.assertEqual(sd.fmt_clock(0), "00:00")
        self.assertEqual(sd.fmt_clock(59_999), "00:59")
        self.assertEqual(sd.fmt_clock(72_000), "01:12")
        self.assertEqual(sd.fmt_clock(3_723_000), "1:02:03")
        self.assertEqual(sd.fmt_clock(-5), "00:00")
        self.assertEqual(sd.fmt_clock("junk"), "00:00")
        self.assertEqual(sd.fmt_count(999), "999")
        self.assertEqual(sd.fmt_count(1234), "1.2k")
        self.assertEqual(sd.fmt_count(12_345), "12k")
        self.assertEqual(sd.fmt_count(1_250_000), "1.2M")
        self.assertEqual(sd.fmt_count(None), "0")

    def test_run_time_prefers_run_ms_and_ticks_with_age(self):
        w = {"state": "running", "run_ms": 1000, "elapsed_ms": 9000}
        self.assertEqual(sd.run_ms_of(w), 1000)
        self.assertEqual(sd.run_ms_of(w, age_s=0.5), 1500)
        self.assertEqual(sd.run_ms_of({"state": "running",
                                       "elapsed_ms": 9000}), 9000)
        self.assertEqual(sd.run_ms_of({"state": "queued", "run_ms": 0,
                                       "elapsed_ms": 9000}), 0)

    def test_eased_progress_glides(self):
        st = sd.DashState()
        self.assertEqual(st.ease("a", 10, 0.0), 10)
        v1 = st.ease("a", 90, 0.05)
        self.assertGreater(v1, 10)
        self.assertLess(v1, 90)
        v2 = st.ease("a", 90, 2.0)
        self.assertEqual(v2, 90)


class CompactBoardTests(unittest.TestCase):
    def _snap(self, states):
        return {"workers": [{"id": f"w{i}", "state": s, "role": "coder",
                             "current_action": f"step {i}"}
                            for i, s in enumerate(states)]}

    def test_height_stable_until_settled_plus_hold(self):
        keeper = sd.BoardKeeper(hold=1.5, limit=6)
        states = ["running"] * 4
        heights = []
        t = 0.0
        for i in range(4):
            plan = keeper.update(self._snap(states), t)
            rows = sd.compact_board(self._snap(states), plan, width=80,
                                    colors=C, now=t)
            heights.append(len(rows))
            for r in rows:
                self.assertEqual(sd.row_width(r), 80)
            states[i] = "done"
            t += 0.5
        self.assertEqual(set(heights), {5})
        plan = keeper.update(self._snap(states), t)    # all settled
        self.assertTrue(plan.visible)
        self.assertTrue(plan.settled)
        self.assertEqual(plan.height, 5)
        self.assertTrue(keeper.update(self._snap(states), t + 1.4).visible)
        self.assertFalse(keeper.update(self._snap(states), t + 1.6).visible)
        # the next swarm starts fresh
        plan = keeper.update(self._snap(["running"]), t + 2)
        self.assertEqual(plan.height, 2)

    def test_no_oscillation_when_states_flip(self):
        keeper = sd.BoardKeeper(limit=6)
        import random
        rng = random.Random(7)
        seen = set()
        for step in range(500):
            states = [rng.choice(["running", "queued", "done", "error",
                                  "cancelling"]) for _ in range(9)]
            states[0] = "running"
            plan = keeper.update(self._snap(states), step * 0.01)
            rows = sd.compact_board(self._snap(states), plan, width=100,
                                    colors=C, now=step * 0.01)
            seen.add(len(rows))
        self.assertEqual(seen, {1 + 6 + 1})

    def test_compact_board_keeps_existing_markers(self):
        snap = self._snap(["running", "queued"])
        plan = sd.BoardKeeper().update(snap, 0.0)
        text = "\n".join(_text(r) for r in sd.compact_board(
            snap, plan, width=100, colors=C, now=0.0))
        self.assertIn("2 Agents", text)
        self.assertIn("1 running", text)
        self.assertIn("↓ to manage", text)
        self.assertIn("◉", text)


class GovernorAndGateTests(unittest.TestCase):
    def test_governor_caps_frames_leading_and_trailing(self):
        clock = [0.0]
        fired = []
        gov = sd.RedrawGovernor(lambda: fired.append(clock[0]),
                                max_fps=10, clock=lambda: clock[0])
        self.assertEqual(gov.request(), "fired")          # leading edge
        for _ in range(500):                  # 50 ms of event storm
            clock[0] += 0.0001
            gov.request()
        self.assertEqual(len(fired), 1)
        self.assertTrue(gov.pending)
        self.assertFalse(gov.pump())                      # too early
        clock[0] = 0.1
        self.assertEqual(gov.due_in(), 0.0)
        self.assertTrue(gov.pump())                       # trailing edge
        self.assertEqual(len(fired), 2)
        self.assertIsNone(gov.due_in())
        self.assertFalse(gov.tick(0.25))
        clock[0] = 0.4
        self.assertTrue(gov.tick(0.25))

    def test_governor_threadsafe_under_load(self):
        count = [0]
        lock = threading.Lock()

        def fire():
            with lock:
                count[0] += 1

        gov = sd.RedrawGovernor(fire, max_fps=12)
        t0 = time.monotonic()

        def worker():
            for _ in range(2000):
                gov.request()

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - t0
        self.assertEqual(gov.requests, 32_000)
        self.assertLessEqual(count[0], elapsed * 12 + 2)

    def test_gate_debounces_one_followup(self):
        gate = sd.AutoContinueGate(debounce=0.8)
        kw = dict(idle=True, buffer_empty=True, enabled=True)
        gate.note_event(0.0)
        self.assertFalse(gate.check(0.1, pending=1, fingerprint={"a"}, **kw))
        gate.note_event(0.3)                              # another finishes
        self.assertFalse(gate.check(0.9, pending=2,
                                    fingerprint=frozenset("ab"), **kw))
        self.assertTrue(gate.check(1.2, pending=2,
                                   fingerprint=frozenset("ab"), **kw))
        # same unconsumed result set never loops
        self.assertFalse(gate.check(5.0, pending=2,
                                    fingerprint=frozenset("ab"), **kw))
        self.assertEqual(gate.fired, 1)

    def test_gate_respects_busy_typing_disabled(self):
        gate = sd.AutoContinueGate(debounce=0.0)
        self.assertFalse(gate.check(1, pending=1, idle=False,
                                    buffer_empty=True))
        self.assertFalse(gate.check(2, pending=1, idle=True,
                                    buffer_empty=False))
        self.assertFalse(gate.check(3, pending=1, idle=True,
                                    buffer_empty=True, enabled=False))
        self.assertFalse(gate.check(4, pending=0, idle=True,
                                    buffer_empty=True))
        self.assertTrue(gate.check(5, pending=1, idle=True,
                                   buffer_empty=True))

    def test_results_fingerprint(self):
        snap = _mixed_snapshot()
        for w in snap["workers"]:
            w.setdefault("delivered", True)
        fp = sd.results_fingerprint(snap, 1)
        self.assertEqual(fp, frozenset({"sa-3"}))
        self.assertEqual(sd.results_fingerprint({"workers": []}, 3),
                         ("n", 3))


if __name__ == "__main__":
    unittest.main()
