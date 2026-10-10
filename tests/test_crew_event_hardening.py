"""Crew event hardening — PROOF, not promises.

User report: subagents failing with
    AttributeError: 'str' object has no attribute ...

Root cause chain: crew.* events are sealed in the shared EventLog and
read back by several consumers (TUI ParallelAgentsPanel, dashboard
fold, evolution fitness, report _gather, theater _summary, the
Notifier). Nothing guaranteed ``ev.data`` was a dict: ``EventLog.append``
did ``data = data or {}`` (a bare string passed straight through) and
every consumer called ``ev.data.get(...)`` — so one poisoned/legacy
event with string data crashed them all with the reported AttributeError.

The fixes:
  kernel.py  EventLog.append coerces non-dict payloads to
             {"_raw": ...} — ev.data is ALWAYS a dict from here on.
  crew.py    Crew._emit() validates every crew.* emission (dict +
             expected keys filled from _EVENT_DEFAULTS); CrewAgent.to_dict()
             coerces summary/error/files_touched to their contract types.
  tui.py     ParallelAgentsPanel already hardened by a fellow worker
             (ingest/poll_log/_on_* guards) — verified, not duplicated.
  evolution.py / theater.py / report.py / agent.py (Notifier.emit):
             isinstance guards so a poisoned in-memory event (legacy,
             hand-crafted) can never raise into the reader.

This test PROVES it:
  1. emission: append("crew.done", "boom") / None / 123 seals a dict.
  2. crew._emit: non-dict payload wrapped; missing keys defaulted.
  3. to_dict: summary=None, error=<exception>, files_touched="str"
     coerce cleanly.
  4. consumers: poisoned in-memory events (data="boom" — the pre-fix
     shape) fed to ParallelAgentsPanel.ingest/poll_log, evolution
     fitness, theater._summary, report._gather, Notifier.emit —
     none raise.
  5. end-to-end: a real spawn/done cycle still seals dicts and the
     panel ingests them.

Run:  python3 -m pytest tests/test_crew_event_hardening.py -q
   or: python3 -m unittest tests.test_crew_event_hardening -v
"""

import tempfile
import threading
import unittest
from pathlib import Path

from fullagent.crew import Crew, CrewAgent
from fullagent.kernel import Event, EventLog


def _make_log():
    td = tempfile.mkdtemp()
    return EventLog(Path(td) / "test.jsonl")


def _poisoned(seq, type_, data="boom"):
    """A pre-fix legacy in-memory event: data is a bare string."""
    return Event(seq=seq, id=f"poison-{seq}", parent=None, branch="main",
                 ts=0.0, type=type_, data=data)


def _stub_chat(*a, **k):
    from fullagent.client import StreamResult
    return StreamResult(content="STATUS: DONE\nSUMMARY: ok", tool_calls=[])


def _make_crew(log):
    from types import SimpleNamespace
    provider = SimpleNamespace(key="t", name="T", base_url="http://t",
                               api_key="t")
    model = SimpleNamespace(id="stub", provider="t", label="Stub",
                            supports_tools=False, supports_reasoning=False)
    effort = SimpleNamespace(key="low", label="LOW", color="#fff",
                             max_tokens=100, temperature=0.0,
                             reasoning_effort=None)
    return Crew(log, provider, model, effort, chat=_stub_chat,
                max_agents=2)


class TestAppendCoercion(unittest.TestCase):
    def test_string_payload_becomes_dict(self):
        log = _make_log()
        ev = log.append("crew.done", "boom")
        self.assertIsInstance(ev.data, dict)
        self.assertEqual(ev.data["_raw"], "boom")

    def test_none_payload_becomes_empty_dict(self):
        log = _make_log()
        ev = log.append("crew.done", None)
        self.assertEqual(ev.data, {})

    def test_int_payload_becomes_dict(self):
        log = _make_log()
        ev = log.append("crew.progress", 123)
        self.assertIsInstance(ev.data, dict)

    def test_dict_payload_untouched(self):
        log = _make_log()
        ev = log.append("crew.done", {"id": "crew-1"})
        self.assertEqual(ev.data, {"id": "crew-1"})

    def test_poisoned_event_survives_archive_roundtrip(self):
        log = _make_log()
        ev = log.append("crew.done", "boom")
        log.close()
        log2 = EventLog(log.path)
        got = [e for e in log2.events() if e.id == ev.id]
        self.assertTrue(got)
        self.assertIsInstance(got[0].data, dict)


class TestEmitValidation(unittest.TestCase):
    def test_emit_wraps_non_dict(self):
        log = _make_log()
        crew = _make_crew(log)
        try:
            ev = crew._emit("crew.done", "boom")
            self.assertIsInstance(ev.data, dict)
            self.assertIn("id", ev.data)  # defaults still applied
        finally:
            crew.shutdown(wait=False)

    def test_emit_fills_missing_keys(self):
        log = _make_log()
        crew = _make_crew(log)
        try:
            ev = crew._emit("crew.progress", {"id": "crew-9"})
            self.assertEqual(ev.data["phase"], "")
            self.assertEqual(ev.data["tools"], [])
            self.assertEqual(ev.data["step"], 0)
            ev2 = crew._emit("crew.spawn", {"id": "crew-9"})
            self.assertEqual(ev2.data["nickname"], "")
        finally:
            crew.shutdown(wait=False)

    def test_emit_coerces_tools_and_files(self):
        log = _make_log()
        crew = _make_crew(log)
        try:
            ev = crew._emit("crew.done",
                            {"id": "x", "tools": 5, "files_touched": "abc"})
            self.assertEqual(ev.data["tools"], [])
            self.assertEqual(ev.data["files_touched"], [])
        finally:
            crew.shutdown(wait=False)


class TestToDictHardening(unittest.TestCase):
    def test_corrupted_fields_coerce(self):
        a = CrewAgent(id="crew-1", nickname="n", role="coder", task="t")
        a.summary = None
        a.error = ValueError("kaboom")
        a.files_touched = "notalist"
        d = a.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["summary"], "")
        self.assertIn("kaboom", d["error"])
        self.assertEqual(d["files_touched"], ["notalist"])

    def test_normal_fields_unchanged(self):
        a = CrewAgent(id="crew-1", nickname="n", role="coder", task="t")
        a.summary = "done things"
        a.error = ""
        a.files_touched = ["a.py", "b.py"]
        d = a.to_dict()
        self.assertEqual(d["summary"], "done things")
        self.assertEqual(d["files_touched"], ["a.py", "b.py"])


class _FakeLog:
    """events() returns hand-crafted poisoned Events (data is a str)."""
    def __init__(self, events):
        self._events = events
        self._lock = threading.RLock()

    def events(self, branch=None, upto_seq=None):
        return list(self._events)


class TestConsumersSurvivePoison(unittest.TestCase):
    def setUp(self):
        self.poisoned = [
            _poisoned(1, "crew.spawn"),
            _poisoned(2, "crew.progress"),
            _poisoned(3, "crew.done"),
            _poisoned(4, "crew.message"),
            _poisoned(5, "crew.closed"),
            _poisoned(6, "crew.resumed"),
            _poisoned(7, "crew.force_stop"),
        ]

    def test_panel_ingest_each_poisoned_type(self):
        from fullagent.tui import ParallelAgentsPanel
        panel = ParallelAgentsPanel()
        for ev in self.poisoned:
            panel.ingest(ev.type, ev.data, ev.seq)  # must not raise

    def test_panel_poll_log_poisoned(self):
        from fullagent.tui import ParallelAgentsPanel
        panel = ParallelAgentsPanel()
        panel.poll_log(_FakeLog(self.poisoned))  # must not raise

    def test_panel_mixed_good_and_poisoned(self):
        from fullagent.tui import ParallelAgentsPanel
        log = _make_log()
        crew = _make_crew(log)
        panel = ParallelAgentsPanel()
        panel.poll_log(log)  # establishes the cursor (first poll may
                             # skip pre-existing history — must not raise)
        try:
            # events sealed AFTER the cursor was established are always
            # picked up, regardless of the first-poll history policy
            crew._emit("crew.spawn", {"id": "crew-1", "nickname": "alpha",
                                      "role": "coder", "task": "do it"})
            crew._emit("crew.done", {"id": "crew-1", "nickname": "alpha",
                                     "role": "coder", "state": "done"})
        finally:
            crew.shutdown(wait=False)
        panel.poll_log(log)  # must not raise
        panel.poll_log(_FakeLog(self.poisoned))  # must not raise
        self.assertEqual(panel.total_count, 1)

    def test_evolution_fitness_poisoned(self):
        from fullagent.evolution import EvolutionEngine
        eng = EvolutionEngine(_FakeLog(self.poisoned),
                              mutator=lambda r, b, k: [],
                              evaluator=lambda r, b: ("", 0.0))
        fit = eng.fitness()  # must not raise
        self.assertIsInstance(fit, dict)

    def test_theater_summary_poisoned(self):
        from fullagent.theater import _summary
        for ev in self.poisoned:
            s = _summary(ev)  # must not raise
            self.assertIsInstance(s, str)

    def test_report_gather_poisoned(self):
        from fullagent.report import _gather
        log = _make_log()
        ev = log.append("crew.done", "boom")  # coerced at seal time
        object.__setattr__(ev, "data", "boom")  # simulate legacy memory
        _gather(log)  # must not raise

    def test_notifier_emit_poisoned_payload(self):
        from fullagent.agent import Notifier
        log = _make_log()
        n = Notifier(log)
        with tempfile.NamedTemporaryFile(suffix=".jsonl",
                                         delete=False) as f:
            n.sink = "file:" + f.name
        self.assertTrue(n.emit("crew.done", "boom"))  # must not raise


class TestEndToEnd(unittest.TestCase):
    def test_spawn_done_cycle_seals_dicts(self):
        log = _make_log()
        crew = _make_crew(log)
        try:
            agent = crew.spawn("write a haiku", role="coder")
            states = crew.wait([agent.id], timeout=30)
            self.assertIn(agent.id, states)
            for ev in log.events():
                if ev.type.startswith("crew."):
                    self.assertIsInstance(
                        ev.data, dict,
                        f"{ev.type} sealed non-dict data: {ev.data!r}")
            from fullagent.tui import ParallelAgentsPanel
            panel = ParallelAgentsPanel()
            panel.poll_log(log)  # must not raise (first poll may skip
                                 # pre-existing history by design)
            # new-session activity after the cursor was established is
            # always picked up
            crew.send(agent.id, "one more thing")
            crew.wait([agent.id], timeout=30)
            panel.poll_log(log)  # must not raise
            self.assertGreaterEqual(panel.total_count, 1)
        finally:
            crew.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
