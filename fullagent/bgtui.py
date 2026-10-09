"""Background task TUI widget — presentation-only panel.

Shows a unified view of two kinds of background work:

* background shells from the sibling worker's ``fullagent/bgsh.py``
  (``list_background_tasks()`` → [{id, description, status, started_at}])
* subagents from ``agent.crew.list()`` → CrewAgent(id, nickname, state, task)

Presentation-only: no threads, no state beyond the last rendered lines
(the caller caches those). Both data sources are optional — anything
missing or raising is silently skipped. Stdlib only. Never imports
``.tui`` or ``.agent``: everything is duck-typed, and ``bgsh`` is
imported lazily inside functions with try/except.

Row conventions follow ``ParallelAgentsPanel`` in tui.py: a leading
marker glyph, ``[<status>]``, a human description, and elapsed time in
parentheses. Done/error rows are dimmer (prefixed ``·`` instead of
``●``), running rows get ``●``.
"""

from __future__ import annotations

import time

# Row markers — mirror the visual language of ParallelAgentsPanel:
# bright bullet for live rows, hollow/dim bullet for finished rows.
_RUN_MARKER = "\u25cf"  # ●
_IDLE_MARKER = "\u00b7"  # ·

_KILL_HINT = "use BashOutput <id> / TaskStop <id>"

_STATUS_ALIASES = {
    "started": "running",
    "in_progress": "running",
    "active": "running",
    "success": "done",
    "completed": "done",
    "finished": "done",
    "failed": "error",
    "killed": "stopped",
    "cancelled": "stopped",
}


def _norm_status(raw) -> str:
    s = str(raw or "running").strip().lower()
    return _STATUS_ALIASES.get(s, s)


def _elapsed(started_at, now: float) -> str:
    """Human elapsed like 12s / 3m40s / 1h02m. '—' when unknown."""
    try:
        secs = max(0, int(now - float(started_at)))
    except (TypeError, ValueError):
        return "—"
    if secs < 60:
        return f"{secs}s"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m{secs:02d}s"
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h{mins:02d}m"


def _row(tid, status: str, desc: str, started_at, now: float) -> tuple[bool, str]:
    """Return (is_running, formatted row text)."""
    st = _norm_status(status)
    running = st == "running"
    marker = _RUN_MARKER if running else _IDLE_MARKER
    desc = str(desc or "").replace("\n", " ").strip() or "(no description)"
    return running, f"{marker} {tid} [{st}] {desc} ({_elapsed(started_at, now)})"


def _bg_shell_rows(now: float) -> list[tuple[bool, str]]:
    """Rows from bgsh.list_background_tasks(), or [] on any failure."""
    try:
        from . import bgsh  # lazy: sibling worker's module may not exist
    except Exception:
        return []
    try:
        tasks = bgsh.list_background_tasks()
    except Exception:
        return []
    rows: list[tuple[bool, str]] = []
    for t in tasks or []:
        try:
            if hasattr(t, "get"):
                tid, status = t.get("id"), t.get("status")
                desc, started = t.get("description"), t.get("started_at")
            else:  # duck-typed object
                tid = getattr(t, "id", None)
                status = getattr(t, "status", None)
                desc = getattr(t, "description", None)
                started = getattr(t, "started_at", None)
            if tid is None:
                continue
            rows.append(_row(tid, status, desc, started, now))
        except Exception:
            continue
    return rows


def _crew_rows(agent) -> list[tuple[bool, str]]:
    """Rows from agent.crew.list(), or [] when there is no crew."""
    try:
        crew = getattr(agent, "crew", None)
        agents = crew.list() if crew is not None else []
    except Exception:
        return []
    rows: list[tuple[bool, str]] = []
    now = time.time()
    for a in agents or []:
        try:
            aid = getattr(a, "id", None)
            state = getattr(a, "state", None)
            nick = getattr(a, "nickname", "") or ""
            task = getattr(a, "task", "") or ""
            if aid is None:
                continue
            desc = f"{nick}: {task}" if nick and task else (task or nick)
            rows.append(_row(aid, state, desc, getattr(a, "spawned_at", None), now))
        except Exception:
            continue
    return rows


def has_activity(agent) -> bool:
    """True if any background shell or subagent is currently running."""
    now = time.time()
    for running, _ in _bg_shell_rows(now) + _crew_rows(agent):
        if running:
            return True
    return False


class BackgroundPanel:
    """Presentation-only background-task panel.

    Call ``refresh(agent)`` on each TUI frame; it returns the lines to
    render. Running rows come first, finished rows after. The last
    rendered lines are kept on ``self.lines`` so the caller can cheaply
    cache/skip redundant paints.
    """

    def __init__(self) -> None:
        self.lines: list[str] = []

    def refresh(self, agent) -> list[str]:
        now = time.time()
        rows = _bg_shell_rows(now) + _crew_rows(agent)
        running = [r for is_run, r in rows if is_run]
        finished = [r for is_run, r in rows if not is_run]
        out: list[str] = [" BACKGROUND TASKS"]
        if not rows:
            out.append("  (none)")
        else:
            out.extend(f"  {r}" for r in running + finished)
            out.append(f"  {_KILL_HINT}")
        self.lines = out
        return out


# ---------------------------------------------------------------------------
# self-test: python3 -m fullagent.bgtui
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    class _FakeTask:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _FakeAgent:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _FakeCrew:
        def __init__(self, agents):
            self._agents = agents

        def list(self):
            return self._agents

    # 1. rows from both sources carry ids/statuses
    import sys
    import types

    fake_bgsh = types.ModuleType("bgsh")
    now = time.time()
    fake_bgsh.list_background_tasks = lambda: [
        {"id": "sh-1", "description": "pytest -q", "status": "running",
         "started_at": now - 12},
        {"id": "sh-2", "description": "curl probe", "status": "done",
         "started_at": now - 90},
        _FakeTask(id="sh-3", description="tail logs", status="error",
                  started_at=now - 5),
    ]
    sys.modules["fullagent.bgsh"] = fake_bgsh

    agent = _FakeAgent(crew=_FakeCrew([
        _FakeAgent(id="ag-1", nickname="scout", state="running",
                   task="grep for leaks", spawned_at=now - 30),
        _FakeAgent(id="ag-2", nickname="writer", state="done",
                   task="draft notes", spawned_at=now - 3600),
    ]))

    panel = BackgroundPanel()
    lines = panel.refresh(agent)
    text = "\n".join(lines)
    assert "sh-1" in text and "[running]" in text, "bg shell id/status"
    assert "sh-2" in text and "[done]" in text, "bg shell done row"
    assert "sh-3" in text and "[error]" in text, "duck-typed task row"
    assert "ag-1" in text and "scout" in text, "crew id/nickname"
    assert "ag-2" in text and "[done]" in text, "crew done row"
    assert "● sh-1" in text, "running row bright marker"
    assert "· sh-2" in text, "done row dim marker"
    assert "(12s)" in text, "elapsed seconds"
    assert "(1h00m)" in text, "elapsed hours"
    assert "BashOutput" in text and "TaskStop" in text, "kill hint"
    assert panel.lines is lines, "last-lines cache"
    assert has_activity(agent) is True, "activity with running rows"

    # 2. empty → no activity, '(none)' placeholder
    agent2 = _FakeAgent(crew=_FakeCrew([]))
    fake_bgsh.list_background_tasks = lambda: []
    lines2 = panel.refresh(agent2)
    assert has_activity(agent2) is False, "empty → no activity"
    assert "(none)" in "\n".join(lines2), "empty placeholder"

    # 3. bgsh import failure → crew-only panel, no crash
    del sys.modules["fullagent.bgsh"]
    agent3 = _FakeAgent(crew=_FakeCrew([
        _FakeAgent(id="ag-9", nickname="solo", state="running",
                   task="work", spawned_at=None),
    ]))
    lines3 = panel.refresh(agent3)
    assert "ag-9" in "\n".join(lines3), "crew works without bgsh"

    # 4. agent with no crew attr at all → '(none)', no activity
    agent4 = _FakeAgent()
    lines4 = panel.refresh(agent4)
    assert has_activity(agent4) is False
    assert "(none)" in "\n".join(lines4)

    print("PASS")
