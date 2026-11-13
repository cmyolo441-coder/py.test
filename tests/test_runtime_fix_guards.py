"""Regression tests for the runtime-area fixes (turn-loop guards, slash
commands, retry engine, error recovery, entry points, cloud server).

All offline and fast; app state is isolated under a temp FULLAGENT_HOME.
"""

import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

_tmp_home = tempfile.mkdtemp(prefix="runtime_fix_home_")
os.environ.setdefault("FULLAGENT_HOME", _tmp_home)

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pytest  # noqa: E402

from fullagent import errorrecovery, healer, planmode, resetcmd  # noqa: E402
from fullagent import retrycmd, retryhint, smartretry, todos, undocmd  # noqa: E402
from fullagent.kernel import EventLog  # noqa: E402
from fullagent.snapshots import SnapshotStore  # noqa: E402


# ---------------------------------------------------------------------------
# retryhint: stale streaks, bounded ledger, cleared by /reset and /retry
# ---------------------------------------------------------------------------

def test_retryhint_streak_goes_stale(monkeypatch):
    retryhint.reset_turn()
    clock = [1000.0]
    monkeypatch.setattr(retryhint.time, "monotonic", lambda: clock[0])
    assert retryhint.note_failure("edit_file", "ERROR: a") is None
    # an hour later (another turn, another task): not a "2nd attempt"
    clock[0] += 3600
    assert retryhint.note_failure("edit_file", "ERROR: b") is None
    # but a genuine quick repeat still hints
    clock[0] += 2
    h = retryhint.note_failure("edit_file", "ERROR: c")
    assert h is not None and "2 failed edit_file attempts" in h
    retryhint.reset_turn()


def test_retryhint_ledger_is_bounded():
    retryhint.reset_turn()
    for i in range(retryhint._MAX_TOOLS * 3):
        retryhint.note_failure(f"hallucinated_tool_{i}", "ERROR: unknown")
    assert len(retryhint._fails) <= retryhint._MAX_TOOLS
    retryhint.reset_turn()


def test_reset_clears_retryhint_ledger():
    retryhint.reset_turn()
    retryhint.note_failure("run_command", "ERROR: exit 1")
    agent = SimpleNamespace(messages=[{"role": "system", "content": "s"}],
                            turns=[], log=None)
    resetcmd.reset_conversation(agent)
    # first failure after /reset must not be reported as a repeat
    assert retryhint.note_failure("run_command", "ERROR: exit 1") is None
    retryhint.reset_turn()


# ---------------------------------------------------------------------------
# retrycmd: harness runtime notes are not the user's prompt
# ---------------------------------------------------------------------------

NOTE = ("[RUNTIME NOTE — harness guidance, subordinate to the system "
        "prompt]\nThis approach isn't working (2 failed edit_file attempts)")


def _derailed():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "fix the bug in a.py"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ERROR: nope"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "ERROR: nope"},
        {"role": "user", "content": NOTE},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c3"}]},
        {"role": "tool", "tool_call_id": "c3", "content": "ERROR: nope"},
        {"role": "user", "content": "[compliance-gate] Your previous reply"},
    ]


def test_repair_skips_runtime_notes():
    fixed = retrycmd.repair_conversation(_derailed())
    assert fixed == _derailed()[:2], fixed


def test_handle_retry_restarts_with_real_prompt():
    started = threading.Event()
    got = {}

    class UI:
        _busy = False
        _cancel_flag = threading.Event()

        def __init__(self, agent):
            self.agent = agent
            self.errors = []

        def print_info(self, *a):
            pass

        def print_error(self, t, *a):
            self.errors.append(t)

        def _run_turn_thread(self, text):
            got["prompt"] = text
            started.set()

    agent = SimpleNamespace(messages=_derailed(), turns=[], log=None)
    ui = UI(agent)
    retrycmd.handle_retry(ui, "")
    assert started.wait(5)
    assert got["prompt"] == "fix the bug in a.py"
    assert agent.messages == [{"role": "system", "content": "sys"}]
    assert not ui.errors


# ---------------------------------------------------------------------------
# smartretry: tree kill on timeout, no inherited stdin, budget clamps
# ---------------------------------------------------------------------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_smartretry_timeout_kills_grandchildren(tmp_path):
    pidfile = tmp_path / "pid"
    cmd = f"sleep 60 & echo $! > {pidfile}; wait"
    t0 = time.time()
    r = smartretry.run_with_retry(cmd, shell=True, max_attempts=1,
                                  timeout=1)
    assert time.time() - t0 < 15
    assert not r["success"] and r["attempts_log"][0]["note"] == "timeout"
    pid = int(pidfile.read_text().strip())
    deadline = time.time() + 5
    while _alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    try:
        assert not _alive(pid), "grandchild survived the attempt timeout"
    finally:
        if _alive(pid):
            os.kill(pid, 9)


def test_smartretry_child_gets_no_stdin(monkeypatch):
    seen = {}
    real = subprocess.Popen

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(smartretry.subprocess, "Popen", spy)
    r = smartretry.run_with_retry(["true"], max_attempts=1)
    assert r["success"]
    assert seen.get("stdin") is subprocess.DEVNULL
    if os.name == "posix":
        assert seen.get("start_new_session") is True


def test_smartretry_attempt_timeout_clamped_to_total_budget(monkeypatch):
    monkeypatch.setattr(smartretry, "TOTAL_TIME_CAP", 2.0)
    t0 = time.time()
    r = smartretry.run_with_retry(["sleep", "30"], max_attempts=3,
                                  timeout=600)
    assert time.time() - t0 < 10, "one attempt ran past the total cap"
    assert not r["success"]


def test_smartretry_max_attempts_capped(monkeypatch):
    calls = []
    monkeypatch.setattr(smartretry, "_run_once",
                        lambda *a: (calls.append(1) or (1, "", "x", "")))
    monkeypatch.setattr(smartretry.time, "sleep", lambda s: None)
    r = smartretry.run_with_retry(["x"], max_attempts=10 ** 6,
                                  base_delay=0.01, max_delay=0.01)
    assert len(calls) == smartretry.MAX_ATTEMPTS_CAP
    assert r["attempts"] == smartretry.MAX_ATTEMPTS_CAP


# ---------------------------------------------------------------------------
# undocmd: never rmtree a pre-existing directory, never follow symlinks
# ---------------------------------------------------------------------------

def _undo_agent(tmp_path):
    store = SnapshotStore(tmp_path / "store")
    log = EventLog(tmp_path / "log.jsonl")
    return SimpleNamespace(store=store, log=log)


def _snap(agent, paths, tool):
    snap = agent.store.take([str(p) for p in paths])
    agent.log.append("snapshot.taken",
                     {"tree": snap["tree"], "paths": list(snap["paths"]),
                      "before_tool": tool})


def test_undo_copy_dir_keeps_source(tmp_path, monkeypatch):
    work = tmp_path / "work"
    src = work / "src"
    src.mkdir(parents=True)
    (src / "precious.py").write_text("keep me\n")
    monkeypatch.chdir(work)
    agent = _undo_agent(tmp_path)
    dst = work / "dst"
    _snap(agent, [src, dst], "copy_path")
    import shutil
    shutil.copytree(src, dst)
    undocmd.undo_last(agent)
    assert (src / "precious.py").read_text() == "keep me\n", \
        "/undo deleted the copy SOURCE directory"


def test_undo_create_directory_on_existing_dir_keeps_contents(tmp_path,
                                                              monkeypatch):
    work = tmp_path / "work"
    d = work / "pkg"
    d.mkdir(parents=True)
    (d / "mod.py").write_text("x = 1\n")
    monkeypatch.chdir(work)
    agent = _undo_agent(tmp_path)
    _snap(agent, [d], "create_directory")
    out = undocmd.undo_last(agent)
    assert (d / "mod.py").exists(), out
    # an empty directory the mutation created is still removed
    e = work / "fresh"
    _snap(agent, [e], "create_directory")
    e.mkdir()
    undocmd.undo_last(agent)
    assert not e.exists()


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_undo_removes_created_symlink_not_its_target(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    target = work / "important.py"
    target.write_text("do not delete\n")
    monkeypatch.chdir(work)
    agent = _undo_agent(tmp_path)
    link = work / "link.py"
    _snap(agent, [link], "write_file")
    link.symlink_to(target)
    undocmd.undo_last(agent)
    assert not link.is_symlink() and not link.exists()
    assert target.read_text() == "do not delete\n"


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_undo_restores_file_replaced_by_symlink(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    other = work / "other.py"
    other.write_text("other\n")
    a = work / "a.py"
    a.write_text("original\n")
    monkeypatch.chdir(work)
    agent = _undo_agent(tmp_path)
    _snap(agent, [a], "write_file")
    a.unlink()
    a.symlink_to(other)
    undocmd.undo_last(agent)
    assert not a.is_symlink() and a.read_text() == "original\n"
    assert other.read_text() == "other\n", "undo overwrote the link target"


# ---------------------------------------------------------------------------
# errorrecovery: the pruned request view must not mutate canonical dicts
# ---------------------------------------------------------------------------

def test_prune_does_not_mutate_shared_assistant_dicts():
    a_partial = {"role": "assistant", "content": "",
                 "tool_calls": [{"id": "x1"}, {"id": "x2"}]}
    canonical = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "o1"}]},
        {"role": "tool", "tool_call_id": "o1", "content": "r"},
        a_partial,
        {"role": "tool", "tool_call_id": "x1", "content": "r"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "n1"}]},
        {"role": "tool", "tool_call_id": "n1", "content": "r"},
    ]
    view = list(canonical)          # shallow, like prune_messages
    errorrecovery._repair_tool_pairing(view)
    assert a_partial["tool_calls"] == [{"id": "x1"}, {"id": "x2"}]
    fixed = next(m for m in view if m.get("tool_calls")
                 and m["tool_calls"][0].get("id") == "x1")
    assert fixed["tool_calls"] == [{"id": "x1"}]
    errorrecovery.prune_history_aggressive(view)
    assert canonical[2]["tool_calls"] == [{"id": "o1"}]


# ---------------------------------------------------------------------------
# small robustness fixes
# ---------------------------------------------------------------------------

def test_healer_accepts_non_string_error(tmp_path):
    h = healer.Healer(EventLog(tmp_path / "h.jsonl"))
    rep = h.heal(None, context=None)
    assert rep.diagnosis.root_cause == "unknown"
    rep = h.heal({"error": "ModuleNotFoundError: No module named 'x'"})
    assert rep.diagnosis.root_cause == "missing_module"


def test_planwrite_non_string_plan():
    agent = SimpleNamespace(tools={})
    planmode.register(agent)
    pw = agent.tools["PlanWrite"].handler
    assert pw(plan=None).startswith("ERROR")
    assert "Plan recorded" in pw(plan=123)
    assert agent.plan_text == "123"


def test_todos_save_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(todos, "TODOS_DIR", str(tmp_path))
    mgr = todos.TodoManager("atomic")
    mgr.set_todos([{"content": "a", "status": "pending"}])
    real_dump = todos.json.dump

    def boom(obj, fh, **kw):
        fh.write("[{\"content\": ")   # partial write, then crash
        raise OSError("disk full")

    monkeypatch.setattr(todos.json, "dump", boom)
    mgr.set_todos([{"content": "b", "status": "pending"}])
    monkeypatch.setattr(todos.json, "dump", real_dump)
    again = todos.TodoManager("atomic")
    assert [t["content"] for t in again.get_todos()] == ["a"]
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


# ---------------------------------------------------------------------------
# entry point: no Agent for a typo'd command; turn errors are clean
# ---------------------------------------------------------------------------

def test_headless_unknown_command_builds_no_agent(monkeypatch, capsys):
    import fullagent.__main__ as fm
    import fullagent.agent as fa

    def explode(*a, **k):
        raise AssertionError("Agent must not be constructed")

    monkeypatch.setattr(fa, "Agent", explode)
    assert fm._headless(["no-such-command"]) == 2
    assert "unknown command" in capsys.readouterr().err


def test_headless_print_turn_exception_is_clean(monkeypatch, capsys):
    import fullagent.__main__ as fm
    import fullagent.agent as fa

    class FakeAgent:
        def __init__(self, cfg):
            pass

        def run_turn(self, *a, **k):
            raise RuntimeError("no API key configured")

    monkeypatch.setattr(fa, "Agent", FakeAgent)
    rc = fm._headless_print("hi", SimpleNamespace(output_format="text"))
    assert rc == 1
    assert "no API key configured" in capsys.readouterr().err


def test_headless_print_json_error_exit_code(monkeypatch, capsys):
    import fullagent.__main__ as fm
    import fullagent.agent as fa
    from fullagent import jsonout

    class FakeAgent:
        session_id = "s"

        def __init__(self, cfg):
            pass

        def run_turn(self, *a, **k):
            return SimpleNamespace(error="boom", assistant_text="")

    monkeypatch.setattr(fa, "Agent", FakeAgent)
    monkeypatch.setattr(jsonout, "turn_to_json", lambda *a, **k: {})
    monkeypatch.setattr(jsonout, "emit_json", lambda obj: None)
    assert fm._headless_print("hi", SimpleNamespace(
        output_format="json")) == 1


# ---------------------------------------------------------------------------
# cloudserve: cached token, no per-request regeneration, socket timeout
# ---------------------------------------------------------------------------

def _load_cloudserve(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cloudserve_under_test", _REPO / "cloudserve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CLOUD_DIR", str(tmp_path / "cloud"))
    monkeypatch.setattr(mod, "TOKEN_FILE", str(tmp_path / "cloud" / "token"))
    return mod


def test_cloud_token_cached_and_not_regenerated(tmp_path, monkeypatch):
    cs = _load_cloudserve(tmp_path, monkeypatch)
    created = []
    tok = cs.get_token(created)
    assert created and tok
    assert oct(os.stat(cs.TOKEN_FILE).st_mode & 0o777) == "0o600"
    assert cs.get_token() == tok
    # deleting the file mid-run must not silently mint a new token
    os.unlink(cs.TOKEN_FILE)
    assert cs.get_token() == tok
    # an explicit rotation (new file content) is picked up
    with open(cs.TOKEN_FILE, "w") as f:
        f.write("rotated-token\n")
    os.utime(cs.TOKEN_FILE, (time.time() + 5, time.time() + 5))
    assert cs.get_token() == "rotated-token"


def test_cloud_concurrent_first_requests_share_one_token(tmp_path,
                                                          monkeypatch):
    cs = _load_cloudserve(tmp_path, monkeypatch)
    got = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        got.append(cs.get_token())

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(got)) == 1


def test_cloud_idle_connection_is_dropped(tmp_path, monkeypatch):
    cs = _load_cloudserve(tmp_path, monkeypatch)
    cs.get_token()
    monkeypatch.setattr(cs.Handler, "timeout", 0.5)
    srv = cs.ThreadedServer(("127.0.0.1", 0), cs.Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        s = socket.create_connection(srv.server_address, timeout=5)
        s.settimeout(5)
        t0 = time.time()
        data = s.recv(1024)          # we never send a request line
        assert data == b"" or data   # server closed (or sent 408)
        assert time.time() - t0 < 4, "idle connection held open"
        s.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_cloud_health_requires_token(tmp_path, monkeypatch):
    import json
    import urllib.request
    import urllib.error
    cs = _load_cloudserve(tmp_path, monkeypatch)
    tok = cs.get_token()
    srv = cs.ThreadedServer(("127.0.0.1", 0), cs.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://%s:%d" % srv.server_address
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with pytest.raises(urllib.error.HTTPError) as ei:
            opener.open(base + "/health", timeout=5)
        assert ei.value.code == 401
        req = urllib.request.Request(
            base + "/health", headers={"Authorization": "Bearer " + tok})
        with opener.open(req, timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
    finally:
        srv.shutdown()
        srv.server_close()
