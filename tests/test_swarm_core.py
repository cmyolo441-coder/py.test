"""Non-blocking parallel swarm: core manager + agent integration.

The main agent hands work to background subagents and keeps going; each
finished worker's report reaches the model automatically (exactly once)
at its next model call, without a blocking TaskCollect.
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="swarm_home_"))
_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

import fullagent.agent as agent_mod
from fullagent.client import StreamResult
from fullagent.config import Config
from fullagent.subagents import MAX_GROUP_SIZE, ParallelSubagents


class MemoryLog:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def append(self, kind, data, **kwargs):
        with self.lock:
            self.events.append((kind, data))
            return SimpleNamespace(id=f"ev-{len(self.events)}")


def _result(content="", tool_calls=(), tin=7, tout=3):
    return SimpleNamespace(content=content, tool_calls=list(tool_calls),
                           usage={"prompt_tokens": tin,
                                  "completion_tokens": tout},
                           reasoning="")


def _manager(tmp_path, chat, *, workers=4, active=32, tools=None):
    model = SimpleNamespace(id="m", provider="p", supports_tools=True)
    return ParallelSubagents(
        MemoryLog(), tools or {},
        lambda _mid: (SimpleNamespace(id="p"), model,
                      SimpleNamespace(key="low")),
        workspace=tmp_path, max_workers=workers, max_active=active,
        max_steps=4, chat=chat)


# --------------------------------------------------------------------------
# manager
# --------------------------------------------------------------------------

def test_sixteen_worker_swarm_respects_concurrency_and_accounts_tokens(
        tmp_path):
    lock = threading.Lock()
    live = {"now": 0, "peak": 0}

    def chat(*_a, **_k):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.05)
        with lock:
            live["now"] -= 1
        return _result("finding", tin=11, tout=5)

    mgr = _manager(tmp_path, chat, workers=6)
    try:
        workers = mgr.spawn_many([f"inspect area {i}"
                                  for i in range(MAX_GROUP_SIZE)])
        assert len(workers) == 16
        states = mgr.wait([w.id for w in workers], timeout=20)
        assert set(states.values()) == {"done"}
        assert 2 <= live["peak"] <= 6
        snap = mgr.snapshot()
        assert snap["tokens_in"] == 16 * 11
        assert snap["tokens_out"] == 16 * 5
        assert snap["pending_results"] == 16
        row = snap["workers"][0]
        assert row["progress_pct"] == 100
        assert row["run_ms"] >= 0 and row["max_steps"] == 4
        assert isinstance(row["recent"], list) and row["recent"]
    finally:
        mgr.shutdown()


def test_results_delivered_once_in_finish_order(tmp_path):
    gates = {i: threading.Event() for i in range(3)}

    def chat(_p, _m, _e, messages, *_a, **_k):
        import re
        idx = int(re.search(r"TASK: task (\d)", messages[-1]["content"]).group(1))
        gates[idx].wait(5)
        return _result(f"report {idx}")

    mgr = _manager(tmp_path, chat)
    try:
        ws = mgr.spawn_many(["task 0", "task 1", "task 2"])
        assert mgr.drain_results() == []          # nothing settled yet
        gates[2].set()
        mgr.wait([ws[2].id], timeout=5)
        gates[0].set()
        mgr.wait([ws[0].id], timeout=5)
        time.sleep(0.05)
        got = mgr.drain_results()
        assert [("report 2" in r, "report 0" in r) for r in got] == \
            [(True, False), (False, True)]
        assert mgr.drain_results() == []          # never twice
        gates[1].set()
        mgr.wait([ws[1].id], timeout=5)
        time.sleep(0.05)
        # collected explicitly -> not auto-delivered again
        mgr.collect(worker_ids=[ws[1].id], timeout=1)
        assert mgr.pending_results() == 0
        assert mgr.drain_results() == []
        assert all(w["delivered"] for w in mgr.snapshot()["workers"])
    finally:
        mgr.shutdown()


def test_stop_all_cancels_and_reports(tmp_path):
    release = threading.Event()

    def chat(*_a, should_cancel=None, **_k):
        while not release.wait(0.01):
            if should_cancel and should_cancel():
                break
        return _result("late")

    mgr = _manager(tmp_path, chat, workers=2)
    try:
        ws = mgr.spawn_many([f"t{i}" for i in range(5)])
        time.sleep(0.1)
        assert mgr.stop_all() == 5
        states = mgr.wait([w.id for w in ws], timeout=5)
        assert set(states.values()) == {"cancelled"}
        reports = mgr.drain_results(limit=16)
        assert len(reports) == 5
        assert all("cancelled" in r for r in reports)
    finally:
        release.set()
        mgr.shutdown()


def test_live_action_describes_tool_and_target(tmp_path):
    seen = threading.Event()
    calls = {"n": 0}

    def reader(path=""):
        seen.set()
        return "contents"

    from fullagent.tools import Tool
    tools = {"read_file": Tool("read_file", "read",
                               {"type": "object",
                                "properties": {"path": {"type": "string"}}},
                               reader)}

    def chat(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _result("", [{"id": "c1", "type": "function",
                                 "function": {"name": "read_file",
                                              "arguments": json.dumps(
                                                  {"path": "src/app.py"})}}])
        return _result("done")

    mgr = _manager(tmp_path, chat, tools=tools)
    try:
        w = mgr.spawn("look", role="researcher", allow_writes=False)
        mgr.wait([w.id], timeout=5)
        recent = mgr.snapshot()["workers"][0]["recent"]
        assert "read_file · src/app.py" in recent
    finally:
        mgr.shutdown()


def test_no_thread_leak_after_shutdown(tmp_path):
    before = threading.active_count()
    mgr = _manager(tmp_path, lambda *a, **k: _result("ok"), workers=8)
    ws = mgr.spawn_many([f"t{i}" for i in range(8)])
    mgr.wait([w.id for w in ws], timeout=5)
    mgr.shutdown(wait=True)
    deadline = time.monotonic() + 5
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert threading.active_count() <= before


# --------------------------------------------------------------------------
# agent: hand off and keep going
# --------------------------------------------------------------------------

@pytest.fixture()
def agent(monkeypatch, tmp_path):
    import fullagent.config as config_mod
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config_mod, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config_mod, "SESSIONS_DIR",
                        tmp_path / "app" / "sessions")
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    config_mod.ensure_dirs()
    monkeypatch.chdir(tmp_path)
    ag = agent_mod.Agent(Config())
    ag.autonomy = 5
    ag._ensure_session()
    yield ag
    try:
        ag.subagents.shutdown(wait=False)
    except Exception:
        pass


def _run(ag, text):
    return ag.run_turn(text, lambda s: None, lambda s: None,
                       lambda e: None, lambda e: None, lambda s: None,
                       lambda t, a: True, should_cancel=lambda: False)


def test_main_agent_hands_off_and_gets_results_without_waiting(
        agent, monkeypatch):
    release = threading.Event()

    def worker_chat(*_a, should_cancel=None, **_k):
        while not release.wait(0.01):
            if should_cancel and should_cancel():
                break
        return _result("worker says: the bug is in parser.py")

    agent.subagents.chat = worker_chat
    sent = []

    def main_chat(provider, model, effort, messages, schemas, **kw):
        sent.append([dict(m) for m in messages])
        n = len(sent)
        if n == 1:
            return StreamResult(content="", tool_calls=[{
                "id": "t1", "type": "function",
                "function": {"name": "TaskGroup", "arguments": json.dumps(
                    {"tasks": [{"task": "find the bug"},
                               {"task": "review tests"}]})}}],
                usage=None, model="t")
        return StreamResult(content=f"reply {n}", tool_calls=[],
                            usage=None, model="t")

    monkeypatch.setattr(agent_mod, "chat_stream", main_chat)
    t0 = time.perf_counter()
    turn = _run(agent, "investigate")
    # the main turn did NOT block on the still-running swarm
    assert time.perf_counter() - t0 < 5
    assert not turn.error, turn.error
    assert agent.subagents.snapshot()["active"] == 2
    assert agent.swarm_pending_results() == 0

    release.set()
    deadline = time.monotonic() + 10
    while agent.swarm_pending_results() < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert agent.swarm_pending_results() == 2

    _run(agent, "continue")
    notes = [m["content"] for m in sent[-1]
             if m.get("role") == "user"
             and str(m.get("content", "")).startswith("[SWARM RESULTS")]
    assert len(notes) == 1
    assert notes[0].count("the bug is in parser.py") == 2
    assert agent.swarm_pending_results() == 0
    # delivered exactly once: the next call carries no new note
    _run(agent, "again")
    notes_after = [m for m in sent[-1] if m.get("role") == "user"
                   and str(m.get("content", "")).startswith("[SWARM RESULTS")]
    assert len(notes_after) == 1   # the earlier note, still in history


def test_delivery_never_splits_a_tool_call_pair(agent):
    agent.messages.append({"role": "assistant", "content": "",
                           "tool_calls": [{"id": "x", "type": "function",
                                           "function": {"name": "f",
                                                        "arguments": "{}"}}]})
    mgr = agent.subagents
    mgr.chat = lambda *a, **k: _result("r")
    w = mgr.spawn("t", role="researcher", allow_writes=False)
    mgr.wait([w.id], timeout=5)
    time.sleep(0.05)
    assert agent._deliver_swarm_results() == 0
    assert agent.swarm_pending_results() == 1
    agent.messages.append({"role": "tool", "tool_call_id": "x",
                           "content": "ok"})
    assert agent._deliver_swarm_results() == 1
    assert agent.messages[-1]["content"].startswith("[SWARM RESULTS")


def test_swarm_size_from_config(monkeypatch, tmp_path):
    import fullagent.config as config_mod
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config_mod, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config_mod, "SESSIONS_DIR",
                        tmp_path / "app" / "sessions")
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    config_mod.ensure_dirs()
    cfg = Config()
    cfg.extra["swarm_concurrency"] = 12
    cfg.extra["swarm_capacity"] = "bad"
    ag = agent_mod.Agent(cfg)
    ag._ensure_session()
    try:
        assert ag.subagents.max_workers == 12
        assert ag.subagents.max_active == 32
        assert "12 model calls run at once" in ag.tools["TaskGroup"].description
    finally:
        ag.subagents.shutdown(wait=False)
