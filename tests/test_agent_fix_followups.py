"""Follow-up fixes in agent.py / snapshots.py found by the area audits."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="fu_home_"))
_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from fullagent.snapshots import SnapshotStore


def test_restore_keeps_preexisting_directory(tmp_path):
    store = SnapshotStore(tmp_path / "store")
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep.txt").write_text("user data")
    snap = store.take([str(src)])          # snapshot before copy/undo
    store.materialise(snap["tree"])
    assert (src / "keep.txt").read_text() == "user data"


def test_restore_keeps_nonempty_dir_created_later(tmp_path):
    store = SnapshotStore(tmp_path / "store")
    dst = tmp_path / "moved"
    snap = store.take([str(dst)])          # absent at snapshot time
    dst.mkdir()
    (dst / "moved_in.txt").write_text("moved user data")
    res = store.materialise(snap["tree"])
    assert (dst / "moved_in.txt").exists()
    assert res.get("kept_nonempty_dirs") == 1


def test_restore_removes_empty_dir_and_new_file(tmp_path):
    store = SnapshotStore(tmp_path / "store")
    d, f = tmp_path / "newdir", tmp_path / "new.txt"
    snap = store.take([str(d), str(f)])
    d.mkdir()
    f.write_text("x")
    res = store.materialise(snap["tree"])
    assert not d.exists() and not f.exists()
    assert res["removed"] == 2


def test_run_turn_resets_retryhint_streaks(monkeypatch, tmp_path):
    import fullagent.agent as agent_mod
    import fullagent.config as config_mod
    from fullagent import retryhint
    from fullagent.client import StreamResult
    from fullagent.config import Config
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config_mod, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config_mod, "SESSIONS_DIR",
                        tmp_path / "app" / "sessions")
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    config_mod.ensure_dirs()
    ag = agent_mod.Agent(Config())
    monkeypatch.setattr(agent_mod, "chat_stream",
                        lambda *a, **k: StreamResult(content="ok",
                                                     tool_calls=[],
                                                     usage=None, model="t"))
    retryhint.note_failure("edit_file", "ERROR: old_string not found")
    ag._error_counts.update({f"sig{i}": 1 for i in range(10)})
    ag.run_turn("hi", lambda s: None, lambda s: None, lambda e: None,
                lambda e: None, lambda s: None, lambda t, a: True,
                should_cancel=lambda: False)
    # first failure of the new turn must not read as a repeat
    assert retryhint.note_failure("edit_file",
                                  "ERROR: old_string not found") is None
    ag.reset()
    assert ag._error_counts == {}


def test_fold_skips_inf_and_nan_costs(tmp_path):
    from fullagent.kernel import EventLog, fold
    log = EventLog(tmp_path / "ev.jsonl")
    log.append("cost.incurred", {"usd": 0.5, "tokens_in": 10,
                                 "tokens_out": 2}, actor="t")
    log.append("cost.incurred", {"usd": float("nan"),
                                 "tokens_in": float("inf"),
                                 "tokens_out": "x"}, actor="t")
    st = fold(log)
    assert st.cost_usd == 0.5 and st.tokens_in == 10 and st.tokens_out == 2


def test_apex_wire_check_records_each_broken_wire_once():
    from fullagent import apex

    class Chain:
        def __init__(self):
            self.n = 0

        def append(self, *a, **k):
            self.n += 1

    eng = apex.ApexEngine.__new__(apex.ApexEngine)
    eng.sealed = "SEALED PROMPT " * 20
    eng.chain = Chain()
    for _ in range(50):
        eng.wire_check("tampered wire")
    eng.wire_check("another tampered wire")
    assert eng.chain.n == 2


def test_enforcex_semantic_clip_prefix_only():
    import time
    from fullagent.enforcex import semantic as sem
    big = ("word  \n\t " * 30000) + "tail"
    t = time.perf_counter()
    for _ in range(256):
        out = sem._clip(big, 100)
    assert time.perf_counter() - t < 1.0
    full = sem._WS_RE.sub(" ", big).strip()
    assert out == full[:99] + "…"
    assert sem._clip("  a  b  ", 100) == "a b"
