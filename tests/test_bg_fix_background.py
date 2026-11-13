"""Regression tests: things that piled up / slowed the agent over a session."""
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from fullagent import cronsched, multisess, speculate, watch
from fullagent.subagents import ParallelSubagents


class _Log:
    def __init__(self):
        self.events = []

    def append(self, *a, **k):
        self.events.append(a)
        return SimpleNamespace(seq=len(self.events), id="")


# -- speculator ---------------------------------------------------------------

def test_speculator_threads_do_not_pile_up_across_turns():
    release = threading.Event()

    def slow(name, args):
        release.wait(20)
        return "x"

    sp = speculate.Speculator(_Log(), runner=slow)
    base = threading.active_count()
    try:
        for i in range(200):
            sp.speculate(f"find foo{i} in src/a{i}.py and lib/b{i}.py", [])
        # was +800 threads (4 per turn, all alive); now hard-capped
        assert threading.active_count() - base <= speculate.MAX_INFLIGHT
    finally:
        release.set()


def _wait_cached(sp, n=1, timeout=5.0):
    deadline = time.monotonic() + timeout
    while len(sp._cache) < n and time.monotonic() < deadline:
        time.sleep(0.01)


def test_speculator_never_serves_content_older_than_a_write(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("old\n")
    sp = speculate.Speculator(_Log(), runner=lambda n, a: open(a["path"]).read())
    sp.speculate(f"look at {f}", [])
    _wait_cached(sp)
    # a write tool runs (the agent consults serve() before every call)...
    assert sp.serve("write_file", {"path": str(f)}) is None
    f.write_text("new\n")
    # ...so the prefetched pre-write content must not be served
    assert sp.serve("read_file", {"path": str(f)}) is None


def test_speculator_refuses_hit_when_file_changed_underneath(tmp_path):
    f = tmp_path / "b.py"
    f.write_text("v1\n")
    sp = speculate.Speculator(_Log(), runner=lambda n, a: open(a["path"]).read())
    sp.speculate(f"open {f}", [])
    _wait_cached(sp)
    f.write_text("v2 longer\n")  # changed by an external process
    assert sp.serve("read_file", {"path": str(f)}) is None


def test_speculator_still_serves_fresh_hits(tmp_path):
    f = tmp_path / "c.py"
    f.write_text("same\n")
    sp = speculate.Speculator(_Log(), runner=lambda n, a: open(a["path"]).read())
    sp.speculate(f"open {f}", [])
    _wait_cached(sp)
    assert sp.serve("read_file", {"path": str(f)}) == "same\n"


# -- cron ---------------------------------------------------------------------

def test_cron_lock_not_held_while_command_runs(tmp_path, monkeypatch):
    sched = cronsched.CronScheduler(tmp_path / "cron.db")
    try:
        sched.stop()
        cid, _ = sched.add("* * * * *", "sleep 1.5")
        with sched._lock:
            sched._conn.execute("UPDATE crons SET next_run = ? WHERE id = ?",
                                (time.time() - 1, cid))
            sched._conn.commit()
        t = threading.Thread(target=sched.run_due)
        t.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        rows = sched.list()  # used to block for the whole cron run
        assert time.monotonic() - t0 < 0.5
        assert rows and rows[0]["id"] == cid
        # claimed slot: a concurrent sweep must not double-fire it
        assert sched.run_due() == 0
        t.join(10)
        assert len(sched.runs(cid)) == 1
    finally:
        sched.close()


def test_cron_runs_history_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(cronsched, "RUNS_KEEP_PER_CRON", 5)
    sched = cronsched.CronScheduler(tmp_path / "cron.db")
    try:
        sched.stop()
        cid, _ = sched.add("* * * * *", "true")
        for i in range(12):
            sched._record_run(cid, time.time() + i, 0, "ok")
        n = sched._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert n == 5
    finally:
        sched.close()


@pytest.mark.parametrize("expr,after,expected", [
    ("*/15 * * * *", datetime(2026, 1, 1, 10, 7), datetime(2026, 1, 1, 10, 15)),
    ("0 9 * * 1-5", datetime(2026, 11, 13, 10, 0), datetime(2026, 11, 16, 9, 0)),
    ("0 0 1 1 *", datetime(2026, 3, 3, 3, 3), datetime(2027, 1, 1, 0, 0)),
    ("30 4 1,15 * 5", datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 2, 4, 30)),
    ("59 23 31 12 *", datetime(2026, 12, 31, 23, 59), datetime(2027, 12, 31, 23, 59)),
])
def test_cron_next_run_correct(expr, after, expected):
    assert cronsched.next_run_after(expr, after) == expected


def test_cron_impossible_expression_fails_fast():
    t0 = time.monotonic()
    with pytest.raises(ValueError):
        cronsched.next_run_after("0 0 30 2 *", datetime(2026, 1, 1))
    assert time.monotonic() - t0 < 0.2  # was ~527k minute steps


# -- watch --------------------------------------------------------------------

def test_watch_register_without_watches_starts_no_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("FULLAGENT_WATCHES_FILE", str(tmp_path / "w.json"))
    agent = SimpleNamespace(tools={})
    watch.register(agent)
    try:
        assert agent.watch_manager._thread is None
    finally:
        agent.watch_manager.stop()


def test_watch_skips_heavy_dirs(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "x.js").write_text("1")
    (tmp_path / "src").mkdir()
    mgr = watch.WatchManager(config_path=tmp_path / "w.json")
    try:
        mgr.add(str(tmp_path), "*", "true")
        mgr.stop()
        mgr.poll_once()
        (tmp_path / "src" / "y.js").write_text("2")
        (tmp_path / "node_modules" / "pkg" / "x.js").write_text("22")
        mgr.poll_once()
        tracked = {f for (_w, f) in mgr._state}
        assert str(tmp_path / "src" / "y.js") in tracked
        assert not any("node_modules" in f for f in tracked)
    finally:
        mgr.stop()


# -- multisess ---------------------------------------------------------------

def test_list_sessions_matches_per_session_scan(tmp_path):
    multisess.set_meta_path(tmp_path / "meta.json")
    try:
        evs = []
        for sid in ("s1", "s2"):
            for i in range(3):
                evs.append(SimpleNamespace(type="user.message", session=sid,
                                           data={"text": f"{sid} q{i}"}))
                evs.append(SimpleNamespace(type="assistant.message",
                                           session=sid, data={"text": "a"}))
        log = SimpleNamespace(events=lambda: evs)
        agent = SimpleNamespace(log=log, session_id="s1")
        meta = {"sessions": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]}
        multisess.save_meta(meta)
        rows = {r["id"]: r for r in multisess.list_sessions(agent)}
        for sid in ("s1", "s2", "s3"):
            assert rows[sid]["msg_count"] == len(
                multisess.messages_for_session(log, sid))
            assert rows[sid]["preview"] == multisess._last_user_preview(log, sid)
    finally:
        multisess.set_meta_path(None)


# -- subagents ---------------------------------------------------------------

def test_subagent_progress_listener_registration_is_idempotent():
    mgr = ParallelSubagents(_Log(), {}, lambda m: (None, None, None))
    try:
        cb = lambda w, k: None  # noqa: E731
        for _ in range(100):
            mgr.on_progress(cb)
        assert len(mgr._progress_callbacks) == 1
    finally:
        mgr.shutdown(wait=False)
