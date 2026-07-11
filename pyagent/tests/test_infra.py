from agent.backup import BackupManager
from agent.events import EventBus
from agent.secrets_manager import SecretsManager
from agent.taskqueue import TaskQueue
from agent.workflow import Workflow


def test_event_bus():
    bus = EventBus()
    received = []
    bus.subscribe("ping", lambda p: received.append(p))
    n = bus.publish("ping", {"x": 1})
    assert n == 1
    assert received == [{"x": 1}]


def test_workflow():
    wf = Workflow("demo")
    wf.add("step1", lambda c: {**c, "a": 1})
    wf.add("step2", lambda c: {**c, "b": c["a"] + 1})
    result = wf.run({})
    assert result == {"a": 1, "b": 2}


def test_workflow_condition():
    wf = Workflow("demo")
    wf.add("skip", lambda c: {**c, "z": 9}, condition=lambda c: False)
    result = wf.run({})
    assert "z" not in result
    assert "skipped:skip" in wf.history


def test_backup_and_versions(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("v1")
    mgr = BackupManager(tmp_path / ".backups")
    dest = mgr.backup(f)
    assert dest.endswith(".bak")
    assert len(mgr.versions("f.txt")) == 1


def test_secrets_roundtrip(tmp_path):
    mgr = SecretsManager(tmp_path / ".secrets.enc", key="k")
    mgr.set("token", "abc123")
    mgr2 = SecretsManager(tmp_path / ".secrets.enc", key="k")
    assert mgr2.get("token") == "abc123"


def test_taskqueue():
    q = TaskQueue(workers=2)
    results = []
    q.start()
    q.submit(lambda: results.append(1))
    import time
    time.sleep(0.3)
    q.stop()
    assert results == [1]
