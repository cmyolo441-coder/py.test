"""Regression tests: goal / memory / semantic / brain state stays flat per
turn and correct, plus encoding and corrupt-data fixes in config, oracle
and the world model. Fast, deterministic, offline."""

import json
import os
import sys
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="statefix_"))

from pathlib import Path as _Path

_REPO = str(_Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from fullagent import brain as brain_mod
from fullagent.goal import GoalContract
from fullagent.kernel import EventLog
from fullagent.memory import Hippocampus
from fullagent.semantic import SemanticMemory


def _status_tuple(st):
    return (st.active, st.distance, st.focus, tuple(st.focus_history),
            st.closed_state,
            tuple((c.id, c.state, c.confidence) for c in st.clauses))


def _goal(log, statement="ship it"):
    GoalContract(log).set_goal(statement, [
        {"id": "C1", "text": "a", "weight": 0.5, "advisory": True},
        {"id": "C2", "text": "b", "weight": 0.5, "advisory": True}])


def test_goal_status_incremental_matches_cold_scan(tmp_path):
    log = EventLog(tmp_path / "g.jsonl", max_events=500)
    gc = GoalContract(log)
    _goal(log)
    for i in range(60):
        log.append("tool.call", {"name": "read_file", "args": {}})
        if i == 10:
            gc.prove_clause("C1", True, "human_approval")
        if i == 20:
            log.append("clause.regressed", {"clause": "C1"})
        if i == 30:
            gc.reaim()
            gc.measure()
        if i == 40:
            log.rewind(log.head() - 3)
        if i == 45:
            _goal(log, "second contract")
        if i == 50:
            log.append("goal.closed", {"state": "ABANDONED"})
        cold = GoalContract(log)  # no cache: full scan
        assert _status_tuple(gc.status()) == _status_tuple(cold.status())
        assert gc.gravity() == cold.gravity()


def test_goal_status_does_not_rewalk_log(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "g2.jsonl", max_events=2000)
    gc = GoalContract(log)
    _goal(log)
    for i in range(300):
        log.append("tool.call", {"name": "x", "args": {}})
    gc.status()
    walked = {"n": 0}
    real = EventLog.events

    def counting(self, *a, **k):
        walked["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(EventLog, "events", counting)
    for _ in range(20):
        log.append("tool.call", {"name": "x", "args": {}})
        gc.status()
        gc.measure()
        gc.reaim()
    assert walked["n"] == 0, "goal reads must extend incrementally"


def test_goal_measure_tolerates_corrupt_distance_records(tmp_path):
    log = EventLog(tmp_path / "g3.jsonl")
    gc = GoalContract(log)
    _goal(log)
    log.append("goal.distance", {"distance": "garbage", "seq": "x",
                                 "velocity": "fast"})
    rec = gc.measure()  # used to raise ValueError / TypeError
    assert rec["velocity"] == 0.0
    assert gc.status().velocity == 0.0


def test_semantic_incremental_matches_fresh_index(tmp_path):
    log = EventLog(tmp_path / "s.jsonl")
    hip = Hippocampus(log)
    sem = SemanticMemory(log)
    for i in range(40):
        log.append("fact.learned", {"fact": f"module{i} parses csv rows"})
        if i % 7 == 0:
            hip.record_dead_end(signature=f"sig{i}",
                                reason=f"regex csv attempt {i} failed")
        sem.recall("csv parser", k=3)
    fresh = SemanticMemory(log)
    fresh.reindex()
    assert sem.stats()["items"] == 46
    assert sem.stats() == fresh.stats()
    assert sem.recall("module33 csv", k=3) == fresh.recall("module33 csv",
                                                           k=3)
    log.rewind(5)
    sem.recall("csv", k=1)
    cold = SemanticMemory(log)
    cold.reindex()
    assert sem.stats() == cold.stats()


def test_memory_block_cache_and_dead_end_cap(tmp_path):
    log = EventLog(tmp_path / "m.jsonl")
    hip = Hippocampus(log)
    for i in range(30):
        hip.record_dead_end(signature=f"sig-{i}", reason=f"failed {i}")
    block = hip.context_block()
    lines = [ln for ln in block.splitlines() if ln.startswith("- sig-")]
    assert len(lines) == Hippocampus._MAX_DEAD_ENDS
    assert lines[0].startswith("- sig-29")  # newest first
    assert hip.is_dead_end("sig-0")  # the full ledger is still checked
    # unrelated events do not change the block (served from cache)...
    log.append("tool.call", {"name": "x"})
    assert hip.context_block() is block
    # ...but new memory content does
    log.append("fact.learned", {"fact": "tests use pytest"})
    assert "tests use pytest" in hip.context_block()


def test_brain_recall_does_not_rewrite_store_every_call(tmp_path,
                                                        monkeypatch):
    log = EventLog(tmp_path / "b.jsonl")
    path = tmp_path / "brain.json"
    b = brain_mod.Brain(log, path)
    b.remember("the parser lives in src/parser.py", kind="fact")
    saves = {"n": 0}
    real = brain_mod.Brain._save

    def counting(self):
        saves["n"] += 1
        real(self)

    monkeypatch.setattr(brain_mod.Brain, "_save", counting)
    for _ in range(25):
        assert b.recall("where is the parser", k=1)
    assert saves["n"] <= 1
    b.flush()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["memories"][0]["reviews"] == 25
    assert isinstance(brain_mod._tokens("a b"), frozenset)


def test_config_utf8_and_type_drift(tmp_path, monkeypatch):
    import fullagent.config as config_mod
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path)
    cfg_file.write_text(json.dumps({"theme": 5, "prompt": None,
                                    "note": "naïve → ✓"},
                                   ensure_ascii=False), encoding="utf-8")
    cfg = config_mod.Config.load()
    assert cfg.theme == "dracula" and cfg.prompt == "main"
    assert cfg.extra["note"] == "naïve → ✓"
    cfg.extra["obj"] = object()  # not JSON-serialisable: must not raise
    cfg.save()
    assert json.loads(cfg_file.read_text(encoding="utf-8"))["note"] \
        == "naïve → ✓"
    assert not list(tmp_path.glob("*.tmp"))


def test_oracle_constitution_is_read_as_utf8(tmp_path, monkeypatch):
    from fullagent.oracle import Oracle
    log = EventLog(tmp_path / "o.jsonl")
    mem = tmp_path / "memory"
    mem.mkdir()
    (tmp_path / "constitution.md").write_bytes(
        "always → be ✓ careful".encode("utf-8"))
    import locale
    monkeypatch.setattr(locale, "getpreferredencoding",
                        lambda *a, **k: "ascii")
    o = Oracle(log, memory_dir=mem)
    assert o.read_constitution() == "always → be ✓ careful"
    o.learn_fact("arrows → work")
    assert "arrows → work" in o.write_facts_md().read_text(encoding="utf-8")


def test_world_static_dependants_matches_edge_scan(tmp_path):
    from fullagent.world import WorldModel
    log = EventLog(tmp_path / "w.jsonl")
    wm = WorldModel(log, root=None)
    wm.edges = {}
    for src, dst in (("a.py", "b.py"), ("a.py", "c.py"), ("b.py", "c.py")):
        wm._edge(src, dst)
    assert wm._static_dependants("a.py") == ["b.py", "c.py"]
    wm.observe_turn(["a.py"], ["c.py"])
    assert wm.edges[("a.py", "c.py")].broken == 1
    wm._edge("a.py", "d.py")  # adjacency must refresh after a new edge
    assert wm._static_dependants("a.py") == ["b.py", "c.py", "d.py"]
