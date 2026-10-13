import threading
import time
from types import SimpleNamespace

from fullagent import multisess
from fullagent.cancelguard import TurnCancelled
from fullagent.kernel import EventLog
from fullagent.subagents import ParallelSubagents


def test_session_changes_cancel_workers_and_keep_events_in_origin_session(
        tmp_path, monkeypatch):
    monkeypatch.setattr(multisess, "_META_PATH_OVERRIDE",
                        tmp_path / "sessions_meta.json")
    monkeypatch.setattr(multisess, "_reseat_todos", lambda *_args: None)

    log = EventLog(tmp_path / "events.jsonl", session="session-a")
    chat_entered = threading.Event()

    def chat(*_args, **kwargs):
        chat_entered.set()
        should_cancel = kwargs["should_cancel"]
        while not should_cancel():
            time.sleep(0.005)
        raise TurnCancelled()

    model = SimpleNamespace(id="test-model", provider="test",
                            supports_tools=True)
    runtime = lambda _model_id: (
        SimpleNamespace(id="test"), model, SimpleNamespace(key="low"))
    manager = ParallelSubagents(log, {}, runtime, workspace=tmp_path,
                                max_workers=1, chat=chat)

    class Agent:
        session_id = "session-a"
        messages = []
        turns = []

        def __init__(self):
            self.log = log
            self.subagents = manager

        def save_session(self):
            return None

        def _reseat_system_prompt(self):
            if not self.messages or self.messages[0].get("role") != "system":
                self.messages.insert(0, {"role": "system", "content": "test"})

    agent = Agent()
    try:
        worker_a = manager.spawn("work in session A")
        assert chat_entered.wait(2)

        session_b = multisess.create_session(agent, "session-b")
        assert agent.session_id == session_b
        assert worker_a.state == "cancelled"
        assert manager.list() == []

        chat_entered.clear()
        worker_b = manager.spawn("work in session B")
        assert chat_entered.wait(2)
        assert multisess.switch_session(agent, "session-a") == "session-a"
        assert worker_b.state == "cancelled"
        assert manager.list() == []

        subagent_events = [event for event in log.events()
                           if event.type.startswith("subagent.")]
        events_by_worker = {}
        for event in subagent_events:
            worker_id = event.data.get("id")
            if worker_id:
                events_by_worker.setdefault(worker_id, set()).add(event.session)
        assert events_by_worker[worker_a.id] == {"session-a"}
        assert events_by_worker[worker_b.id] == {session_b}
    finally:
        manager.shutdown(wait=True)
        log.close()
