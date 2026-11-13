"""Goal-contract scope, enforcement and progress regressions.

From a real v4.1.0 log (a brand-new session whose first messages were
"hello" / "hii"):

1. a goal contract from an EARLIER session leaked into the new session's
   live context (the model mentioned it in every reply);
2. that goal was fully PROVEN but never closed, so EVERY tool call —
   TodoWrite, a shell command, SessionList, SessionNew — was blocked as
   an OrphanAction and the new request could not run at all;
3. the TUI goal bar showed "70%" for a goal whose clauses were all PROVEN.

Offline and deterministic: a fake chat_stream, a temp FULLAGENT_HOME.
"""

import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="goalscope_"))
_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

import fullagent.agent as agent_mod
from fullagent.client import StreamResult
from fullagent.config import Config
from fullagent.goal import (TERMINAL_STATES, ClauseState, GoalContract,
                            GoalStatus)
from fullagent.judge import Judge
from fullagent.kernel import EventLog


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _two_clause_goal(gc: GoalContract, artifact: Path) -> None:
    """The shape from the user's log: two clauses whose proofs are weak
    (file_exists = 0.70 confidence) but genuinely PROVEN."""
    artifact.write_text("payload")
    gc.set_goal(
        "Realgoagent harness: scan and package results into an artifact",
        [{"id": "C1", "text": "artifact exists", "kind": "ARTIFACT",
          "weight": 1.0,
          "proof": {"type": "file_exists", "path": str(artifact)}},
         {"id": "C2", "text": "artifact still there", "kind": "ARTIFACT",
          "weight": 1.0,
          "proof": {"type": "file_exists", "path": str(artifact)}}])


def _prove_all(gc: GoalContract) -> None:
    for cid in ("C1", "C2"):
        ok, detail = gc.prove_by_predicate(cid)
        assert ok, detail


@pytest.fixture()
def home(monkeypatch, tmp_path):
    import fullagent.config as config_mod
    from fullagent import multisess
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config_mod, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config_mod, "SESSIONS_DIR",
                        tmp_path / "app" / "sessions")
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    multisess.set_meta_path(tmp_path / "app" / "sessions_meta.json")
    config_mod.ensure_dirs()
    monkeypatch.chdir(tmp_path)
    made = []

    def make():
        ag = agent_mod.Agent(Config())
        ag.autonomy = 5
        ag._ensure_session()
        made.append(ag)
        return ag

    yield make
    multisess.set_meta_path(None)
    for ag in made:
        try:
            ag.subagents.shutdown(wait=False)
        except Exception:
            pass


def _run(ag, text):
    return ag.run_turn(text, lambda s: None, lambda s: None,
                       lambda e: None, lambda e: None, lambda s: None,
                       lambda t, a: True, should_cancel=lambda: False)


def _call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


# --------------------------------------------------------------------------
# Bug 1 — goals are scoped to the session that created them
# --------------------------------------------------------------------------

def test_goal_is_scoped_to_its_session_in_the_shared_log(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", session="old11111")
    gc = GoalContract(log, judge=Judge(log))
    _two_clause_goal(gc, tmp_path / "b.tgz")
    _prove_all(gc)
    assert gc.status().active

    # a different session id on the same (shared, persistent) log
    log.session = "new22222"
    st = gc.status()
    assert not st.active, "another session's goal leaked into this one"
    assert gc.format() == "GOAL: none"

    # a cold reader (no scan cache) agrees
    assert not GoalContract(log).status().active

    # switching back restores the original session's goal + its proofs
    log.session = "old11111"
    st = gc.status()
    assert st.active and all(c.state == "PROVEN" for c in st.clauses)


def test_fresh_agent_process_does_not_inherit_previous_sessions_goal(
        home, tmp_path):
    old = home()
    _two_clause_goal(old.goal, tmp_path / "b.tgz")
    _prove_all(old.goal)
    assert old.goal.status().active

    # a brand-new start: new Agent, same persistent event log on disk
    new = home()
    assert new.session_id != old.session_id
    assert not new.goal.status().active
    sections = new._context_sections(query="hello")
    assert "goal" not in sections
    assert "Realgoagent" not in json.dumps(sections)


def test_new_and_switched_sessions_scope_the_goal(home, tmp_path):
    ag = home()
    _two_clause_goal(ag.goal, tmp_path / "b.tgz")
    first = ag.session_id
    assert ag.goal.status().active

    # SessionNew / /new
    out = ag.tools["SessionNew"].handler(name="fresh")
    assert "created session" in out
    assert not ag.goal.status().active
    assert "goal" not in ag._context_sections(query="hii")

    # SessionSwitch back — the goal belongs to that session, so it returns
    ag.tools["SessionSwitch"].handler(session_id=first)
    assert ag.goal.status().active

    # Agent.reset() (the /new fallback) also starts goal-free
    ag.reset()
    assert not ag.goal.status().active


# --------------------------------------------------------------------------
# Bug 2 — a settled goal stops gating; nothing blocks a new request
# --------------------------------------------------------------------------

def test_fully_proven_goal_auto_closes_and_never_orphans(home, tmp_path):
    ag = home()
    _two_clause_goal(ag.goal, tmp_path / "b.tgz")
    _prove_all(ag.goal)
    st = ag.goal.status()
    assert st.closed_state is None  # proven, but nobody closed it

    for name in ("run_command", "write_file", "TodoWrite", "SessionList",
                 "SessionNew", "read_file"):
        clause, orphan = ag._attribute(name)
        assert orphan is None, (name, orphan)
    st = ag.goal.status()
    assert st.closed_state in TERMINAL_STATES, "settled goal not closed"
    # an archived goal no longer rides along in the live context
    assert "goal" not in ag._context_sections(query="hii")


def test_new_request_runs_after_goal_is_fully_proven(home, tmp_path,
                                                     monkeypatch):
    """The user's exact situation: the goal is PROVEN (not closed), the
    user types "hii" and the model calls TodoWrite, a shell command and
    SessionList. None of them may be blocked."""
    ag = home()
    _two_clause_goal(ag.goal, tmp_path / "b.tgz")
    _prove_all(ag.goal)
    seen_system = []

    def chat(provider, model, effort, messages, schemas, **kw):
        seen_system.append(str(messages[0].get("content", "")))
        if len(seen_system) == 1:
            return StreamResult(content="", tool_calls=[
                _call("t1", "TodoWrite",
                      {"todos": [{"content": "greet", "status":
                                  "in_progress", "activeForm": "greeting"}]}),
                _call("t2", "run_command", {"command": "echo hi"}),
                _call("t3", "SessionList", {}),
            ], usage=None, model="t")
        return StreamResult(content="hi!", tool_calls=[], usage=None,
                            model="t")

    monkeypatch.setattr(agent_mod, "chat_stream", chat)
    turn = _run(ag, "hii")
    assert not turn.error, turn.error
    names = {t.name: t for t in turn.tools}
    assert set(names) >= {"TodoWrite", "run_command", "SessionList"}
    for t in turn.tools:
        assert t.status != "blocked", (t.name, t.result)
        assert "OrphanAction" not in (t.result or ""), (t.name, t.result)
    # the settled goal was archived before the model ever saw the turn
    assert "Realgoagent" not in seen_system[0]


def test_session_and_readonly_tools_are_never_goal_gated(home, monkeypatch):
    """Even in a contract state with no open clause that is NOT settled
    (a clause in an unknown/blocked state), session-management and
    read-only/planning tools must still run."""
    ag = home()
    stuck = GoalStatus(active=True, statement="x", clauses=[
        ClauseState(id="C1", text="t", weight=1.0, state="BLOCKED",
                    proof={"type": "exit_code"})])
    monkeypatch.setattr(ag.goal, "status", lambda: stuck)
    monkeypatch.setattr(ag.goal, "reaim", lambda *a, **k: None)
    for name in ("SessionNew", "SessionList", "SessionSwitch", "TodoWrite",
                 "TodoRead", "read_file", "list_directory"):
        _, orphan = ag._attribute(name)
        assert orphan is None, (name, orphan)


# --------------------------------------------------------------------------
# Bug 3 — goal progress reflects clause states
# --------------------------------------------------------------------------

def test_progress_is_100_when_every_clause_is_proven(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", session="s1")
    gc = GoalContract(log, judge=Judge(log))
    _two_clause_goal(gc, tmp_path / "b.tgz")
    st = gc.status()
    assert st.progress == 0.0
    gc.prove_by_predicate("C1")
    assert abs(gc.status().progress - 0.5) < 1e-9
    gc.prove_by_predicate("C2")
    st = gc.status()
    # distance keeps the §39.1 confidence-weighted meaning ...
    assert abs(st.distance - 0.30) < 1e-9
    # ... but the progress shown to the user follows clause states
    assert st.progress == 1.0
    assert "100% proven" in gc.format()


def _bare_ui(goal_status):
    from fullagent.config import DEFAULT_MODEL_ID
    from fullagent.tui import UI
    from rich.console import Console
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra={"render_markdown": False},
                             model_id=DEFAULT_MODEL_ID, effort="high",
                             show_reasoning=False, auto_approve=True)
    ui._busy = False
    ui._cancel_flag = threading.Event()
    ui._focus_remaining = 0
    ui._status_text = ""
    ui._approve_request = None
    ui._spinner_i = 0
    ui._flash = None
    ui._diff_history = []
    ui._detail_expanded = False
    ui._last_detail_event = None
    ui._width = lambda: 160
    ui._invalidate = lambda: None
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=160, color_system=None)
    ui.overlay = None
    ui.agent = SimpleNamespace(
        autonomy=3, session_id="abcd1234", messages=[],
        model=SimpleNamespace(id=DEFAULT_MODEL_ID, context_window=262144,
                              label="Test Model"),
        save_session=lambda: None,
        goal=SimpleNamespace(status=lambda: goal_status),
        skill_forge=SimpleNamespace(registry={}),
        thinking_visible=False, thinking_expanded=False)
    return ui


def _top_text(ui):
    return "".join(f[1] for f in ui._top_fragments())


def test_tui_goal_bar_full_when_all_proven_and_hidden_once_closed(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", session="s1")
    gc = GoalContract(log, judge=Judge(log))
    _two_clause_goal(gc, tmp_path / "b.tgz")
    _prove_all(gc)
    text = _top_text(_bare_ui(gc.status()))
    assert "◎ goal" in text
    assert "70%" not in text
    assert "▰▰▰▰▰ 100%" in text

    gc.close(fresh=False)
    text = _top_text(_bare_ui(gc.status()))
    assert "◎ goal" not in text, "closed goal still drawn in the bar"
