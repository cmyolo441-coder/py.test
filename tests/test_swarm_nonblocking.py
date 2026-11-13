"""Non-blocking parallel swarm: async delivery, auto-wake and the live board.

Drives the REAL ParallelSubagents manager, the REAL Agent turn loop (with
a scripted provider at the chat_stream seam), the REAL Swarm tool and the
REAL fullagent.swarmboard renderer / tui.UI wake logic. Only the provider
boundary is injected; nothing under test is mocked.
"""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from fullagent import swarmboard
from fullagent.cliui import display_width
from fullagent.subagents import (NOTIFICATION_HEADER, SWARM_WAKE_PROMPT,
                                 TERMINAL_STATES, ParallelSubagents,
                                 describe_call)


class _Log:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def append(self, kind, data, **kwargs):
        with self.lock:
            ev = SimpleNamespace(id=f"ev-{len(self.events) + 1}")
            self.events.append((kind, data))
            return ev


def _result(content="", tool_calls=(), tokens=(7, 3)):
    return SimpleNamespace(
        content=content, tool_calls=list(tool_calls), reasoning="",
        usage={"prompt_tokens": tokens[0], "completion_tokens": tokens[1]})


def _manager(chat, tmp_path, tools=None, **kw):
    provider = SimpleNamespace(id="offline")
    model = SimpleNamespace(id="offline-model", supports_tools=True)
    effort = SimpleNamespace(key="low")
    return ParallelSubagents(_Log(), tools or {},
                             lambda _m="": (provider, model, effort),
                             workspace=tmp_path, chat=chat, **kw)


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


# ---------------------------------------------------------------------------
# orchestrator: hand-off returns at once, results arrive asynchronously
# ---------------------------------------------------------------------------

def test_spawn_returns_immediately_while_workers_keep_running(tmp_path):
    release = threading.Event()
    inside = []

    def chat(*_a, **_k):
        inside.append(1)
        release.wait(10)
        return _result("report")

    mgr = _manager(chat, tmp_path)
    try:
        t0 = time.monotonic()
        workers = mgr.spawn_many([f"task {i}" for i in range(6)])
        handoff = time.monotonic() - t0
        assert handoff < 0.5, f"spawn blocked the parent for {handoff:.2f}s"
        assert _wait(lambda: len(inside) == 6), "workers never ran in parallel"
        assert mgr.active_count() == 6
        assert mgr.pending_notifications() == 0
        assert mgr.drain_notifications() == ""
        release.set()
        assert _wait(lambda: mgr.pending_notifications() == 6)
        assert all(w.state == "done" for w in workers)
    finally:
        release.set()
        mgr.shutdown()


def test_drain_delivers_each_report_exactly_once(tmp_path):
    mgr = _manager(lambda *_a, **_k: _result("finding A"), tmp_path)
    try:
        workers = mgr.spawn_many(["one", "two", "three"], goal="audit goal")
        assert _wait(lambda: mgr.pending_notifications() == 3)
        note = mgr.drain_notifications()
        assert note.startswith(NOTIFICATION_HEADER)
        assert note.count("### sa-") == 3
        assert "finding A" in note and "goal: audit goal" in note
        assert "No subagents are still running." in note
        assert all(w.delivered for w in workers)
        assert mgr.pending_notifications() == 0
        assert mgr.drain_notifications() == ""  # never twice
    finally:
        mgr.shutdown()


def test_collect_and_output_consume_the_notification(tmp_path):
    mgr = _manager(lambda *_a, **_k: _result("collected"), tmp_path)
    try:
        workers = mgr.spawn_many(["a", "b"])
        mgr.collect(workers[0].batch_id, timeout=10)
        assert mgr.pending_notifications() == 0
        assert mgr.drain_notifications() == ""
    finally:
        mgr.shutdown()


def test_failures_and_cancellations_are_reported_too(tmp_path):
    def chat(_p, _m, _e, messages, *_a, should_cancel=None, **_k):
        task = messages[-1]["content"]
        if "boom" in task:
            raise RuntimeError("provider exploded")
        while not (should_cancel and should_cancel()):
            time.sleep(0.01)
        return _result("never")

    mgr = _manager(chat, tmp_path)
    try:
        bad, slow = mgr.spawn_many(["boom task", "slow task"])
        assert _wait(lambda: bad.state == "error")
        mgr.stop(slow.id)
        assert _wait(lambda: slow.state in TERMINAL_STATES)
        note = mgr.drain_notifications()
        assert "ERROR" in note and "provider exploded" in note
        assert "CANCELLED" in note
    finally:
        mgr.shutdown()


def test_discard_drops_reports_of_cancelled_work(tmp_path):
    mgr = _manager(lambda *_a, **_k: _result("x"), tmp_path)
    try:
        mgr.spawn_many(["a", "b"])
        assert _wait(lambda: mgr.pending_notifications() == 2)
        assert mgr.discard_notifications() == 2
        assert mgr.drain_notifications() == ""
    finally:
        mgr.shutdown()


def test_large_swarm_notification_is_bounded(tmp_path):
    huge = "z" * 20_000
    mgr = _manager(lambda *_a, **_k: _result(huge), tmp_path)
    try:
        mgr.spawn_many([f"t{i}" for i in range(8)])
        assert _wait(lambda: mgr.pending_notifications() == 8)
        first = mgr.drain_notifications()
        assert len(first) < 12_000
        assert "TaskOutput" in first  # full report stays reachable
        # whatever did not fit arrives on the next drain, not lost
        rest = []
        while True:
            more = mgr.drain_notifications()
            if not more:
                break
            rest.append(more)
        total = first.count("### sa-") + sum(r.count("### sa-") for r in rest)
        assert total == 8
    finally:
        mgr.shutdown()


def test_token_and_time_accounting(tmp_path):
    def chat(*_a, **_k):
        time.sleep(0.15)
        return _result("ok", tokens=(100, 25))

    mgr = _manager(chat, tmp_path)
    try:
        (w,) = mgr.spawn_many(["measure me"])
        assert _wait(lambda: w.state == "done")
        row = w.to_dict()
        assert row["tokens_in"] == 100 and row["tokens_out"] == 25
        assert row["run_ms"] >= 140
        assert row["started_at"] > 0 and row["finished_at"] >= row["started_at"]
        snap = mgr.snapshot()
        assert snap["tokens_in"] == 100 and snap["max_steps"] == mgr.max_steps
        frozen = swarmboard.worker_seconds(row, time.time() + 60)
        assert 0.14 <= frozen < 5, "run time must freeze once settled"
    finally:
        mgr.shutdown()


def test_current_action_describes_the_tool_call(tmp_path):
    assert describe_call("read_file", {"path": "a/b.py"}) == "Read a/b.py"
    assert describe_call("search_files", {"pattern": "TODO"}) == "Search 'TODO'"
    assert describe_call("mystery", {}) == "mystery"


# ---------------------------------------------------------------------------
# agent loop: notifications reach the model without blocking
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent(monkeypatch, tmp_path):
    import fullagent.agent as agent_mod
    import fullagent.config as config_mod
    from fullagent.config import Config
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config_mod, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config_mod, "SESSIONS_DIR",
                        tmp_path / "app" / "sessions")
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    monkeypatch.chdir(tmp_path)
    config_mod.ensure_dirs()
    ag = agent_mod.Agent(Config())
    seen = []

    def scripted(provider, model, effort, messages, schemas, **kw):
        from fullagent.client import StreamResult
        wire = [m for m in messages if m.get("role") != "system"]
        seen.append(json.loads(json.dumps(wire, default=str)))
        script = ag._script
        if script:
            return script.pop(0)
        return StreamResult(content="done", reasoning="", tool_calls=[],
                            usage=None, model="test")

    monkeypatch.setattr(agent_mod, "chat_stream", scripted)
    ag._script = []
    ag._seen = seen
    yield ag
    mgr = ag.__dict__.get("subagents")
    if mgr is not None:
        mgr.shutdown()


def _run(ag, text):
    return ag.run_turn(text, lambda s: None, lambda s: None, lambda e: None,
                       lambda e: None, lambda s: None, lambda t, a: True,
                       should_cancel=lambda: False)


def _swarm_call(tasks, goal="parallel audit"):
    return {"id": "call_swarm", "type": "function",
            "function": {"name": "Swarm",
                         "arguments": json.dumps({"goal": goal,
                                                  "tasks": tasks})}}


def test_swarm_tool_hands_off_and_turn_ends_without_waiting(agent):
    from fullagent.client import StreamResult
    release = threading.Event()
    agent._ensure_session()
    agent.subagents.chat = lambda *_a, **_k: (release.wait(10),
                                              _result("worker report"))[1]
    agent._script = [
        StreamResult(content="", reasoning="", model="test", usage=None,
                     tool_calls=[_swarm_call([
                         {"task": "inspect auth", "role": "security",
                          "name": "auth"},
                         {"task": "inspect api", "role": "reviewer",
                          "name": "api"}])]),
        StreamResult(content="launched; continuing", reasoning="",
                     tool_calls=[], usage=None, model="test"),
    ]
    t0 = time.monotonic()
    turn = _run(agent, "run a swarm over the repo")
    took = time.monotonic() - t0
    try:
        assert turn.error == ""
        assert took < 5, f"turn waited on the swarm ({took:.1f}s)"
        swarm_ev = [t for t in turn.tools if t.name == "Swarm"]
        assert swarm_ev and "launched" in swarm_ev[0].result
        assert "Non-blocking" in swarm_ev[0].result
        assert agent.subagents.active_count() == 2  # still working
        assert agent.swarm_followup_needed()
    finally:
        release.set()
    assert _wait(lambda: agent.subagents.pending_notifications() == 2)

    # The follow-up (wake) turn hands the reports to the model.
    turn2 = _run(agent, SWARM_WAKE_PROMPT)
    assert turn2.error == ""
    last_wire = agent._seen[-1]
    users = [m for m in last_wire if m.get("role") == "user"]
    assert NOTIFICATION_HEADER in users[-1]["content"]
    assert users[-1]["content"].count("worker report") == 2
    # merged into the user message: never two user messages in a row
    roles = [m["role"] for m in last_wire]
    assert not any(a == b == "user" for a, b in zip(roles, roles[1:]))
    assert not agent.swarm_followup_needed()


def test_reports_arriving_mid_turn_are_injected_after_tool_results(agent):
    from fullagent.client import StreamResult
    agent._ensure_session()
    agent.subagents.chat = lambda *_a, **_k: _result("mid-turn finding")
    workers = agent.subagents.spawn_many(["background job"])
    assert _wait(lambda: workers[0].state == "done")
    agent._script = [
        StreamResult(content="", reasoning="", model="test", usage=None,
                     tool_calls=[{"id": "c1", "type": "function",
                                  "function": {"name": "TaskList",
                                               "arguments": "{}"}}]),
    ]
    _run(agent, "keep going")
    first, second = agent._seen[-2], agent._seen[-1]
    assert NOTIFICATION_HEADER in first[-1]["content"]  # step 1: merged
    assert not any(NOTIFICATION_HEADER in str(m.get("content"))
                   for m in second[len(first):])  # delivered only once


def test_swarm_tool_is_registered_and_never_delegated(agent):
    from fullagent.subagents import NEVER_DELEGATE
    agent._ensure_session()
    tool = agent.tools["Swarm"]
    assert tool.parameters["required"] == ["goal", "tasks"]
    assert "RETURN IMMEDIATELY" in tool.description
    assert "Swarm" in NEVER_DELEGATE


# ---------------------------------------------------------------------------
# board: stable height, stable order, exact width, linger then hide
# ---------------------------------------------------------------------------

COLORS = {"accent": "#c084fc", "cyan": "#22d3ee", "green": "#34d399",
          "yellow": "#facc15", "orange": "#fb923c", "red": "#f87171",
          "pink": "#f472b6", "fg": "#f8fafc", "dim": "#64748b",
          "border": "#5b6eae"}


def _rows(states, now=1000.0):
    out = []
    for i, st in enumerate(states):
        out.append({
            "id": f"sa-{i + 1:04d}", "name": f"worker-{i}",
            "role": "researcher", "state": st, "batch_id": "batch-0001",
            "task": f"inspect module {i}", "current_action": f"Read m{i}.py",
            "summary": "found things" if st == "done" else "",
            "steps": 2, "tool_calls": 3, "tokens_in": 1000 * (i + 1),
            "tokens_out": 100, "started_at": now - 10 if st != "queued" else 0,
            "finished_at": now - 1 if st in TERMINAL_STATES else 0})
    return {"workers": out, "max_steps": 20,
            "batches": {"batch-0001": {"goal": "scan the repo"}}}


def _text(row):
    return "".join(t for _, t in row)


def test_board_height_never_shrinks_while_workers_settle():
    view = swarmboard.SwarmView(linger_s=1.0)
    heights, orders = [], []
    phases = [["running"] * 5,
              ["running", "done", "running", "running", "queued"],
              ["done", "done", "running", "error", "running"],
              ["done", "done", "done", "error", "running"]]
    for k, states in enumerate(phases):
        snap = _rows(states, now=1000 + k)
        rows = view.update(snap, now=1000 + k)
        board = swarmboard.render_board(rows, width=110, colors=COLORS,
                                        now=1000 + k, view=view)
        heights.append(len(board))
        orders.append([_text(r)[3:] for r in board[1:-1]])
    assert len(set(heights)) == 1, heights
    # rows keep their spawn order: worker-0 is always the first row
    assert all("worker-0" in o[0] for o in orders)


def test_board_rows_fit_the_terminal_exactly():
    view = swarmboard.SwarmView()
    snap = _rows(["running", "queued", "done", "error", "cancelling",
                  "running", "running", "done", "running", "running"])
    rows = view.update(snap, now=1000)
    for width in (24, 40, 60, 80, 100, 160):
        board = swarmboard.render_board(rows, width=width, colors=COLORS,
                                        now=1000, view=view, max_rows=6,
                                        goal="a very long goal " * 6)
        for row in board:
            assert display_width(_text(row)) <= max(24, width)
    head = _text(swarmboard.render_board(rows, width=160, colors=COLORS,
                                         now=1000, view=view)[0])
    assert "10 Agents" in head and "↓ to manage" in head


def test_board_overflow_keeps_live_workers_on_screen():
    view = swarmboard.SwarmView()
    snap = _rows(["done"] * 7 + ["running"] * 3)
    rows = view.update(snap, now=1000)
    board = swarmboard.render_board(rows, width=120, colors=COLORS,
                                    now=1000, view=view, max_rows=4)
    body = "\n".join(_text(r) for r in board)
    assert body.count("◉") == 3  # every running worker is visible
    assert "+6 more" in _text(board[-1])


def test_board_lingers_then_hides_and_reports_a_summary():
    view = swarmboard.SwarmView(linger_s=1.0)
    assert view.update(_rows(["running", "running"]), now=1000)
    done = _rows(["done", "error"])
    assert view.update(done, now=1001)  # just settled: still shown
    assert view.update(done, now=1001.5)
    assert view.update(done, now=1002.2) is None  # linger expired
    summary = view.last_summary
    assert summary["total"] == 2 and summary["done"] == 1
    assert summary["failed"] == 1 and summary["tokens"] == 3200
    # a later swarm opens a fresh session without the old members
    rows = view.update(_rows(["running"]), now=1010)
    assert [r["id"] for r in rows] == ["sa-0001"] and view.sessions == 2


def test_progress_is_eased_monotonic_and_only_full_when_settled():
    view = swarmboard.SwarmView()
    row = _rows(["running"])["workers"][0]
    seen = []
    for k, steps in enumerate([1, 3, 2, 6, 6, 6]):
        row = dict(row, steps=steps)
        seen.append(view.eased("w", swarmboard.target_progress(row, 20),
                               1000 + k * 0.25))
    assert seen == sorted(seen), "progress moved backwards"
    assert max(seen) < 1.0
    done = dict(row, state="done")
    assert swarmboard.target_progress(done, 20) == 1.0


def test_merge_snapshots_keeps_ids_and_goals_apart():
    a = _rows(["running"])
    b = _rows(["running", "done"])
    b["batches"] = {"batch-0001": {"goal": "demo goal"}}
    merged = swarmboard.merge_snapshots(a, b)
    ids = [w["id"] for w in merged["workers"]]
    assert len(ids) == len(set(ids)) == 3
    assert swarmboard.session_goal(merged, merged["workers"]) == "demo goal"


# ---------------------------------------------------------------------------
# TUI: auto-wake when the background swarm reports
# ---------------------------------------------------------------------------

def _ui(manager, *, busy=False, autowake=True):
    from fullagent.tui import UI
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra={"swarm_autowake": autowake})
    ui.agent = SimpleNamespace(subagents=manager)
    ui._busy = busy
    ui._turn_alive = False
    ui._cancel_flag = threading.Event()
    started = []
    ui._run_turn_thread = lambda text, synthetic=0: started.append(
        (text, synthetic))
    ui.started = started
    return ui


def _settled_manager(tmp_path, n=2):
    mgr = _manager(lambda *_a, **_k: _result("r"), tmp_path)
    mgr.spawn_many([f"t{i}" for i in range(n)])
    assert _wait(lambda: mgr.pending_notifications() == n)
    return mgr


def test_ui_wakes_the_agent_when_an_idle_swarm_reports(tmp_path):
    mgr = _settled_manager(tmp_path)
    try:
        ui = _ui(mgr)
        assert ui._maybe_swarm_wake() is True
        assert _wait(lambda: ui.started)
        assert ui.started[0] == (SWARM_WAKE_PROMPT, 2)
    finally:
        mgr.shutdown()


def test_ui_never_wakes_over_a_running_turn_or_when_disabled(tmp_path):
    mgr = _settled_manager(tmp_path)
    try:
        busy = _ui(mgr, busy=True)
        assert busy._maybe_swarm_wake() is False
        off = _ui(mgr, autowake=False)
        assert off._maybe_swarm_wake() is False
        time.sleep(0.05)
        assert not busy.started and not off.started
    finally:
        mgr.shutdown()


def test_cancelled_swarm_does_not_wake_the_agent(tmp_path):
    mgr = _settled_manager(tmp_path)
    try:
        ui = _ui(mgr)
        ui._swarm_wake_blocked = True  # what Esc / /swarm stop set
        assert ui._maybe_swarm_wake() is False
        assert mgr.pending_notifications() == 0  # reports discarded
    finally:
        mgr.shutdown()


def test_board_fragments_follow_a_real_swarm_session(tmp_path):
    from fullagent.tui import UI
    release = threading.Event()
    mgr = _manager(lambda *_a, **_k: (release.wait(10), _result("x"))[1],
                   tmp_path)
    try:
        mgr.spawn_many(["a", "b", "c"], goal="ui goal")
        ui = UI.__new__(UI)
        ui.agent = SimpleNamespace(subagents=mgr)
        ui._width = lambda: 120
        ui._invalidate = lambda: None
        assert _wait(lambda: mgr.active_count() == 3)
        ui._invalidate_agents()
        assert ui._agents_visible()
        text = "".join(t for _, t in ui._agents_fragments())
        assert "3 Agents" in text and "ui goal" in text
        lines_running = text.count("\n")
        release.set()
        assert _wait(lambda: mgr.active_count() == 0)
        ui._invalidate_agents()
        text_done = "".join(t for _, t in ui._agents_fragments())
        assert text_done.count("\n") == lines_running  # same height
        assert "3 done" in text_done
    finally:
        release.set()
        mgr.shutdown()


def test_demo_swarm_runs_offline_with_real_tools(tmp_path):
    from fullagent.swarmdemo import DEMO_GOAL, start_demo
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "README.md").write_text("# demo\n")
    mgr = start_demo({}, workers=3, workspace=tmp_path, speed=20, seed=1)
    try:
        assert _wait(lambda: mgr.active_count() == 0, timeout=20)
        snap = mgr.snapshot()
        assert {w["state"] for w in snap["workers"]} == {"done"}
        assert snap["batches"][snap["latest_batch_id"]]["goal"] == DEMO_GOAL
        assert snap["workers"][-1]["wave"] == 1  # synthesis waits
    finally:
        mgr.shutdown()


def test_straggler_waits_then_partial_reports_wake_the_agent(tmp_path):
    release = threading.Event()

    def chat(_p, _m, _e, messages, *_a, **_k):
        if "slow" in messages[-1]["content"]:
            release.wait(10)
        return _result("r")

    mgr = _manager(chat, tmp_path)
    try:
        mgr.spawn_many(["fast one", "slow one"])
        assert _wait(lambda: mgr.pending_notifications() == 1)
        ui = _ui(mgr)
        ui.cfg.extra["swarm_wake_partial_s"] = 0.3
        ui.__dict__["app"] = object()  # live app: timers allowed
        assert ui._maybe_swarm_wake() is False  # straggler still running
        assert _wait(lambda: ui.started, timeout=5), "partial wake never came"
        assert ui.started[0] == (SWARM_WAKE_PROMPT, 1)
    finally:
        release.set()
        mgr.shutdown()
