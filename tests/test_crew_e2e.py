"""End-to-end crew-system hardening test (worker 20/20).

Proves the whole subagent chain survives adversarial provider output:

  stub _chat -> Crew._checked_chat -> Crew._run_loop -> EventLog
  -> crew.done events -> ParallelAgentsPanel.ingest / poll_log

The stub returns deliberately MALFORMED duck-typed results per agent
(usage as a string, tool_calls containing strings/ints/None, content=None)
for the first three model calls, then a valid REAL StreamResult.
A sixth agent exercises the all-junk tool_calls path (turn ends on the
step's content — the loop's fail-fast for unusable tool calls).

Every stage must degrade gracefully: no agent may land with an
AttributeError, to_dict() must never raise, crew.done events must be
well-formed, and the TUI panel must ingest everything (including
hand-sealed malformed events) without raising.

Run from the repo root:
    python3 /tmp/crew_e2e_test.py
"""

import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.expanduser("~/workspace/pytest-repo"))

from fullagent.crew import Crew, CrewAgent, CrewError, AGENT_STATES  # noqa: E402
from fullagent.kernel import EventLog  # noqa: E402
from fullagent.client import StreamResult  # noqa: E402
from fullagent.tui import ParallelAgentsPanel  # noqa: E402


# ---------------------------------------------------------------------------
# adversarial stub chat
# ---------------------------------------------------------------------------

VALID_TOOL_CALL = {
    "id": "call_probe_1",
    "type": "function",
    "function": {
        "name": "read_file",
        "arguments": '{"path": "/tmp/crew-e2e-probe-target.txt"}',
    },
}


class MalformedChat:
    """Per-agent script. Model call n for agents A-E returns:

      n=0  MALFORMED: usage as a STRING, one valid tool call, str content
      n=1  MALFORMED: content=None, usage=None, tool_calls mixing junk
                     ("str", 42, None, nameless-dict) with ONE valid call
                     -> junk is dropped, the valid call still executes
      n=2  MALFORMED: usage dict with STRING values, tool_calls as a TUPLE
                     mixing a valid dict with a junk string, content=None
      n>=3 VALID: a real StreamResult (STATUS: DONE) -> agent lands done.

    Agent F gets a single all-junk turn: content="partial work done",
    tool_calls=["all", "junk"] -> the loop's fail-fast ends the turn on
    the step content (no usable tool calls), landing done with summary.
    """

    MARKERS = ("[e2e-A]", "[e2e-B]", "[e2e-C]", "[e2e-D]", "[e2e-E]",
               "[e2e-F]")

    def __init__(self):
        self._lock = threading.Lock()
        self._calls: dict[str, int] = {}

    def _agent_key(self, messages) -> str:
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                c = str(m.get("content") or "")
                for marker in self.MARKERS:
                    if marker in c:
                        return marker
        return "unknown"

    def __call__(self, provider, model, effort, messages, schemas, timeout):
        with self._lock:
            key = self._agent_key(messages)
            n = self._calls.get(key, 0)
            self._calls[key] = n + 1
        if key == "[e2e-F]":
            return SimpleNamespace(content="partial work done", reasoning="",
                                    tool_calls=["all", "junk"],
                                    finish_reason=None, usage=None,
                                    model="stub")
        if n == 0:
            return SimpleNamespace(
                content="still working on it", reasoning="",
                tool_calls=[dict(VALID_TOOL_CALL)],
                finish_reason=None, usage="this-is-not-a-dict", model="stub")
        if n == 1:
            return SimpleNamespace(
                content=None, reasoning="",
                tool_calls=["not-a-tool-call", 42, None,
                            {"function": {"name": ""}},
                            dict(VALID_TOOL_CALL)],
                finish_reason=None, usage=None, model="stub")
        if n == 2:
            return SimpleNamespace(
                content=None, reasoning="",
                tool_calls=(dict(VALID_TOOL_CALL), "junk-string"),
                finish_reason=None,
                usage={"prompt_tokens": "twelve", "completion_tokens": None},
                model="stub")
        return StreamResult(
            content="STATUS: DONE\nSUMMARY: finished clean after malformed rounds",
            reasoning="", tool_calls=[], finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5}, model="stub")


def _stubs():
    provider = SimpleNamespace(key="t", name="T", base_url="http://t",
                               api_key="sk-fake", color="#fff")
    model = SimpleNamespace(id="stub", provider="t", label="Stub",
                            supports_tools=True, supports_reasoning=False)
    effort = SimpleNamespace(key="low", label="LOW", color="#fff",
                             max_tokens=100, temperature=0.0,
                             reasoning_effort=None)
    return provider, model, effort


_PASSED = []
_FAILED = []


def check(name, fn):
    try:
        fn()
    except AssertionError as e:
        _FAILED.append(name)
        print(f"FAIL  {name}: {e}", flush=True)
    except BaseException as e:  # noqa: BLE001
        _FAILED.append(name)
        print(f"ERROR {name}: {type(e).__name__}: {e}", flush=True)
    else:
        _PASSED.append(name)
        print(f"PASS  {name}", flush=True)


# ---------------------------------------------------------------------------
def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        log = EventLog(Path(td) / "crew-e2e.jsonl")
        provider, model, effort = _stubs()
        crew = Crew(log, provider, model, effort,
                    chat=MalformedChat(), max_agents=10)

        agents = {}

        def t1_spawn3():
            agents["a"] = crew.spawn("do work alpha [e2e-A]",
                                     role="coder", name="alpha")
            agents["b"] = crew.spawn("do work beta [e2e-B]",
                                     role="tester", name="beta")
            agents["c"] = crew.spawn("do work gamma [e2e-C]",
                                     role="coder", name="gamma")
            assert [x.id for x in agents.values()] == \
                ["crew-1", "crew-2", "crew-3"], \
                [x.id for x in agents.values()]
            # NOTE: with an instant stub an agent may be done/errored
            # before spawn() returns — the handle is valid regardless;
            # terminal-state assertions live in t3/t4.
            assert all(x.state in AGENT_STATES for x in agents.values())
        check("spawn 3 subagents via crew.spawn", t1_spawn3)

        def t2_parallel_mixed():
            batch = crew.spawn_parallel([
                "do work delta [e2e-D]",                      # str item
                {"task": "do work echo [e2e-E]",               # dict item
                 "role": "tester", "name": "echo"},
            ])
            assert len(batch) == 2
            agents["d"], agents["e"] = batch
            assert agents["d"].id == "crew-4" and agents["e"].id == "crew-5"
            agents["f"] = crew.spawn("do work foxtrot [e2e-F]",
                                     role="researcher", name="foxtrot")
            assert agents["f"].id == "crew-6"
        check("spawn_parallel mixed str/dict + all-junk agent", t2_parallel_mixed)

        def t3_wait():
            states = crew.wait(timeout=30.0)
            assert len(states) == 6, states
            for ag in agents.values():
                assert ag.state == "done", \
                    f"{ag.id} landed {ag.state!r}, error={ag.error!r}"
            assert all(s == "done" for s in states.values()), states
        check("wait(): all 6 reach terminal state done", t3_wait)

        def t4_no_attr_error():
            for ag in agents.values():
                blob = (ag.error or "") + "\n" + (ag.traceback or "")
                assert "AttributeError" not in blob, \
                    f"{ag.id} leaked AttributeError: {ag.error!r}"
                assert ag.state in AGENT_STATES, ag.state
                assert isinstance(ag.tokens_in, int) \
                    and isinstance(ag.tokens_out, int), \
                    f"{ag.id} token counters not ints"
            for k in ("a", "b", "c", "d", "e"):
                assert agents[k].tool_calls >= 1, \
                    f"{agents[k].id} executed no tool calls"
            # agent F: all-junk turn ends on step content, no tools run
            assert agents["f"].tool_calls == 0, agents["f"].tool_calls
            assert "partial work done" in agents["f"].summary, \
                agents["f"].summary
        check("no AttributeError; counters sane; F lands on content", t4_no_attr_error)

        def t5_to_dict():
            required = set(Crew._EVENT_DEFAULTS["crew.done"])
            for ag in agents.values():
                d = ag.to_dict()  # must never raise
                assert isinstance(d, dict)
                missing = required - set(d.keys())
                assert not missing, f"{ag.id} missing keys: {missing}"
                assert isinstance(d["state"], str)
                assert isinstance(d["tool_calls"], int)
                assert isinstance(d["files_touched"], list)
                assert isinstance(d["summary"], str)
        check("to_dict() works on all agents, contract holds", t5_to_dict)

        def t6_hostile_to_dict():
            hostile = CrewAgent(id="x", nickname=None, role="coder", task=None,
                                state="done", summary=None,
                                error=ValueError("boom"),
                                files_touched="not-a-list", tool_calls="3",
                                tokens_in=None, finished_at="bad")
            d = hostile.to_dict()
            assert isinstance(d, dict) and d["state"] == "done", d
        check("to_dict() survives hostile field types", t6_hostile_to_dict)

        def t7_event_log():
            required = set(Crew._EVENT_DEFAULTS["crew.done"])
            events = log.events()
            dones = [e for e in events if e.type == "crew.done"]
            spawns = [e for e in events if e.type == "crew.spawn"]
            assert len(spawns) == 6, f"{len(spawns)} crew.spawn"
            assert len(dones) == 6, \
                f"expected exactly 6 crew.done, got {len(dones)}"
            for e in dones:
                assert isinstance(e.data, dict), \
                    f"crew.done seq={e.seq} not a dict"
                missing = required - set(e.data.keys())
                assert not missing, f"crew.done seq={e.seq} missing {missing}"
                assert e.data["state"] == "done", e.data["state"]
            for e in events:
                if e.type.startswith("crew."):
                    assert isinstance(e.data, dict), \
                        f"{e.type} seq={e.seq} payload not a dict"
        check("event log: well-formed crew.done x6, no duplicates", t7_event_log)

        def t8_panel_ingest():
            panel = ParallelAgentsPanel()
            for e in log.events():
                panel.ingest(e.type, e.data, e.seq)  # must not raise
            assert len(panel._agents) == 6, len(panel._agents)
            for aid, st in panel._agents.items():
                assert st["status"] == ParallelAgentsPanel.DONE, \
                    f"panel row {aid} = {st['status']!r}"
            frags = panel._fragments(width=100)
            assert isinstance(frags, list) and frags, "no fragments rendered"
        check("TUI panel ingests all crew events, renders done rows", t8_panel_ingest)

        def t9_panel_poll_smoke():
            panel2 = ParallelAgentsPanel()
            panel2.poll_log(log)  # first-poll skip must not raise
        check("panel poll_log smoke (first-poll skip)", t9_panel_poll_smoke)

        def t10_panel_malformed():
            log.append("crew.spawn", "string-payload-not-a-dict", actor="test")
            log.append("crew.done", None, actor="test")
            log.append("crew.progress", {"id": "crew-1", "tools": "bash"},
                       actor="test")
            log.append("crew.progress", {"id": "crew-1", "tools": 42},
                       actor="test")
            log.append("crew.done", {"id": 12345, "error": ["not", "a", "str"]},
                       actor="test")
            panel3 = ParallelAgentsPanel()
            for e in log.events():
                if e.type.startswith("crew."):
                    panel3.ingest(panel3._safe_type(e),
                                  panel3._safe_data(e),
                                  panel3._safe_seq(e))  # must not raise
        check("panel survives hand-sealed malformed crew events", t10_panel_malformed)

        def t11_bad_batch_atomic():
            crew2 = Crew(log, provider, model, effort,
                         chat=MalformedChat(), max_agents=10)
            try:
                try:
                    crew2.spawn_parallel(["fine [e2e-A]", 12345])
                    raise SystemExit("non-str/dict item must raise")
                except CrewError:
                    pass
                assert crew2.status()["total"] == 0, "batch not atomic"
            finally:
                crew2.shutdown()
        check("malformed spawn_parallel item: CrewError, nothing spawned",
              t11_bad_batch_atomic)

        def t12_queries():
            snap = crew.poll()
            assert set(snap) == {f"crew-{i}" for i in range(1, 7)}, snap
            s = crew.status()
            assert s["done"] == 6 and s["running"] == 0, s
            rep = crew.format()
            assert "crew-1" in rep and "crew-6" in rep
        check("poll()/status()/format() sane after run", t12_queries)

        crew.shutdown()

    total = len(_PASSED) + len(_FAILED)
    print(f"\n=== {len(_PASSED)}/{total} passed ===")
    if _FAILED:
        print("FAILED:", _FAILED)
        sys.exit(1)
    print("CREW E2E HARDENING TEST: ALL ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
