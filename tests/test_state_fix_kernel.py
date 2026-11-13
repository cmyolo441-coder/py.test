"""Regression tests: per-turn state cost must not grow with session length.

Covers the event-log kernel (prune / chain cache / fold cache / delta
reads) and the budget governor that runs on every tool iteration.
Fast and deterministic: tiny retention windows, no network, no agent.
"""

import os
import sys
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="statefix_"))

from pathlib import Path as _Path

_REPO = str(_Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import fullagent.cortex as cortex
from fullagent.cortex import Budget, BudgetGovernor
from fullagent.kernel import EventLog, State, _fold_apply, fold


def _rebuilt_chain(log, branch):
    """What a cold _chain() rebuild returns (caches dropped)."""
    saved = dict(log._chains)
    log._chains.clear()
    try:
        return [e.id for e in log._chain(branch)]
    finally:
        log._chains.clear()
        log._chains.update(saved)


def _fresh_fold(log, branch=None, from_seq=-1):
    st = State(branch=branch or log.branch)
    for ev in log.events(branch):
        if ev.seq > from_seq:
            _fold_apply(st, ev)
    return st


def test_prune_keeps_chain_cache_equal_to_rebuild(tmp_path):
    log = EventLog(tmp_path / "k.jsonl", max_events=40)
    for i in range(60):
        log.append("user.message", {"text": f"m{i}"})
    log.fork(name="side")
    for i in range(70):
        log.append("tool.call", {"name": "t", "args": {"i": i}})
        if i == 30:
            log.rewind(log.head() - 5)
        # the cached chain must always equal a cold rebuild
        assert [e.id for e in log.events()] == _rebuilt_chain(log, "main")
    assert len(log) == 40
    # the steady-state append no longer throws the chain caches away
    assert "main" in log._chains
    log.checkout("side")
    log.append("user.message", {"text": "side wakes"})
    assert [e.id for e in log.events("side")] == _rebuilt_chain(log, "side")
    ok, msg = log.verify("side")
    assert ok, msg


def test_dormant_branch_does_not_force_full_scan_per_append(tmp_path,
                                                            monkeypatch):
    p = tmp_path / "d.jsonl"
    log = EventLog(p, max_events=30)
    log.append("user.message", {"text": "root"})
    log.fork(name="dormant")
    for i in range(100):
        log.append("tool.call", {"name": "t", "args": {}})
    log2 = EventLog(p, max_events=30)
    assert log2._extra, "dormant head should be pinned outside the window"
    calls = {"n": 0}
    real_values = dict.values

    class CountingDict(dict):
        def values(self):
            calls["n"] += 1
            return real_values(self)

    log2._by_id = CountingDict(log2._by_id)
    for i in range(20):
        log2.append("tool.call", {"name": "t", "args": {}})
    # the O(window) `referenced` scan used to run on EVERY append while a
    # pinned base existed; now it runs once and remembers the referrer
    assert calls["n"] <= 1, calls["n"]
    assert "dormant" in log2.branches()


def test_fold_cache_lists_stay_bounded_and_cover_window(tmp_path):
    win = 25
    log = EventLog(tmp_path / "f.jsonl", max_events=win)
    for i in range(400):
        log.append("user.message", {"text": f"u{i}"})
        log.append("goal.distance", {"distance": 0.5, "seq": i})
        fold(log)  # folds run every turn: the cache is extended forever
    st = fold(log)
    assert len(st.messages) <= 2 * win + 2
    assert len(st.distance_measures) <= 2 * win + 2
    # superset of exactly what a window fold contains
    fresh = _fresh_fold(log)
    assert st.messages[-len(fresh.messages):] == fresh.messages
    assert st.head_seq == fresh.head_seq


def test_delta_since_tracks_appends_and_detects_rewind(tmp_path):
    log = EventLog(tmp_path / "ds.jsonl", max_events=50)
    cur = log.cursor()
    assert cur == (None, -1)
    a = log.append("user.message", {"text": "a"})
    b = log.append("user.message", {"text": "b"})
    assert [e.id for e in log.delta_since(cur)] == [a.id, b.id]
    cur = log.cursor()
    assert log.delta_since(cur) == []
    c = log.append("user.message", {"text": "c"})
    assert [e.id for e in log.delta_since(cur)] == [c.id]
    cur = log.cursor()
    log.rewind(a.seq)
    assert log.delta_since(cur) is None  # chain diverged -> rebuild
    assert log.delta_since(None) is None
    # more than a window of appends since the cursor -> rebuild
    cur = log.cursor()
    for i in range(80):
        log.append("tool.call", {"name": "x"})
    assert log.delta_since(cur) is None


def test_budget_spend_incremental_matches_full_fold(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "b.jsonl", max_events=200)
    gov = BudgetGovernor(log, Budget())
    applied = {"n": 0}
    real = cortex._fold_apply

    def counting(st, ev):
        applied["n"] += 1
        real(st, ev)

    monkeypatch.setattr(cortex, "_fold_apply", counting)

    def expected():
        start = max(gov.baseline_seq, gov._session_start())
        st = _fresh_fold(log, from_seq=start)
        return {"usd": st.cost_usd, "steps": st.tool_calls,
                "tokens": st.tokens_in + st.tokens_out,
                "files": len(st.files_touched)}

    log.append("session.start", {"session_id": "s1"})
    for i in range(150):
        log.append("cost.incurred", {"usd": 0.01, "tokens_in": 3,
                                     "tokens_out": 2})
        log.append("tool.call", {"name": "write_file",
                                 "args": {"path": f"f{i % 7}.py"}})
        applied["n"] = 0
        assert gov.spend() == expected()
        if i > 2:
            # steady state: only the 2 new events are applied
            assert applied["n"] <= 2, applied["n"]
        if i == 60:
            gov.reset()
        if i == 90:
            log.append("session.start", {"session_id": "s2"})
        if i == 120:
            log.rewind(log.head() - 10)
    assert gov.spend() == expected()
    assert gov.enforce() is True
