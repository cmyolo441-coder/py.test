import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

import threading
from types import SimpleNamespace

from fullagent import apex, subagents
from fullagent.cassette import Cassette


def test_cassette_record_after_torn_tail_survives_reload(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_bytes(b'{"key": "abc", "resp')  # crash mid-record, no newline
    msgs = [{"role": "user", "content": "hi"}]
    rec = Cassette(p, mode="record")
    rec.record("m", msgs, None, {"content": "ok"})
    rec.close()
    play = Cassette(p, mode="replay")
    assert play.replay("m", msgs, None) == {"content": "ok"}


def test_apex_rebuild_failed_layer_does_not_keep_old_state(monkeypatch):
    eng = apex.build_engine("# Rules\n1. Always answer briefly.\n", "m")
    assert eng.sentinel is not None
    import fullagent.sentinel as sentinel

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(sentinel, "build_payload", boom)
    eng.rebuild("# Rules\n1. Never answer briefly.\n")
    assert eng.report.layers["sentinel"] is False
    assert eng.sentinel is None
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert eng.turn_messages(msgs) is msgs


def _manager():
    log = SimpleNamespace(append=lambda *a, **k: SimpleNamespace(id="e"))
    model = SimpleNamespace(id="m", supports_tools=True)
    return subagents.ParallelSubagents(
        log, {}, lambda _m: (None, model, None), max_workers=2,
        chat=lambda *a, **k: SimpleNamespace(content="done", tool_calls=[],
                                             usage={}))


def test_synthesize_batch_includes_worker_diffs():
    mgr = _manager()
    try:
        ws = mgr.spawn_many([{"task": "t"}])
        mgr.wait([w.id for w in ws], timeout=5)
        mgr._capture_write_diff(ws[0], "write_file", "a.txt", "x\n", "y\n")
        synth = mgr.synthesize_batch(ws[0].batch_id)
        assert len(synth["diffs"]) == 1
        assert synth["diffs"][0]["path"] == "a.txt"
    finally:
        mgr.shutdown()


def test_dependency_wait_not_bounded_by_single_call_timeout():
    mgr = _manager()
    gate = threading.Event()

    def chat(_p, _m, _e, messages, *a, **k):
        if "TASK: slow" in messages[1]["content"]:
            gate.wait(5)
        return SimpleNamespace(content="ok", tool_calls=[], usage={})

    mgr.chat = chat
    try:
        ws = mgr.spawn_many([
            {"name": "up", "task": "slow"},
            {"name": "down", "task": "fast", "depends_on": ["up"],
             "timeout": 1},
        ])
        threading.Timer(1.5, gate.set).start()
        states = mgr.wait([w.id for w in ws], timeout=10)
        assert states[ws[1].id] == "done", ws[1].summary
    finally:
        gate.set()
        mgr.shutdown()
