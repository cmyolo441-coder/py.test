"""TUI `/agents` command — manage subagents (crew) from the TUI.

Claude Code CLI-style: list running subagents, spawn one, stop one,
or stop them all. All functions take a duck-typed ``agent`` that
exposes ``.crew`` (a Crew with ``spawn``/``list``/``get``/``close``/
``force_stop``) and RETURN display strings — the TUI wiring prints
them via ``ui.print_info``. No imports of ``.agent``/``.tui``/``.crew``
at module level, so there are no import cycles.

Usage from the TUI::

    /agents               list subagents (id, state, role, task)
    /agents spawn <text>  spawn a researcher subagent for <text>
    /agents stop <id>     close one subagent
    /agents stop-all      close every subagent

``python3 -m fullagent.agentscmd`` runs the built-in self-test.
"""

from __future__ import annotations

USAGE = (
    "usage:\n"
    "  /agents                 list subagents\n"
    "  /agents spawn <task>    spawn a researcher subagent\n"
    "  /agents stop <id>       close one subagent\n"
    "  /agents stop-all        close every subagent"
)


def _crew(agent):
    """Return agent.crew, or raise a clear error if the crew is absent."""
    crew = getattr(agent, "crew", None)
    if crew is None:
        raise RuntimeError("subagent crew not initialised on this agent")
    return crew


def _row(agent_obj) -> str:
    """One-line summary of a CrewAgent (duck-typed: getattr with defaults)."""
    aid = getattr(agent_obj, "id", "?")
    state = getattr(agent_obj, "state", "?")
    role = getattr(agent_obj, "role", "?") or "?"
    task = (getattr(agent_obj, "task", "") or "").strip()
    if len(task) > 80:
        task = task[:77] + "..."
    name = getattr(agent_obj, "nickname", "") or ""
    who = f"{aid} ({name})" if name else str(aid)
    return f"  {who:<24} {state:<8} {role:<12} {task}"


def agents_errors(agent) -> str:
    """Show FULL error messages for failed subagents (not truncated)."""
    agents = _crew(agent).list()
    failed = [a for a in agents if getattr(a, "state", "") == "error"]
    if not failed:
        return "no failed subagents"
    lines = [f"failed subagents ({len(failed)}):", ""]
    for a in failed:
        aid = getattr(a, "id", "?")
        name = getattr(a, "nickname", "") or ""
        error = getattr(a, "error", "") or "(no error message)"
        tb = getattr(a, "traceback", "") or ""
        lines.append(f"  {aid} ({name}):")
        lines.append(f"    {error}")
        if tb:
            lines.append(f"    Traceback (last 2000 chars):")
            for tline in tb[-2000:].split("\n"):
                lines.append(f"      {tline}")
        lines.append("")
    return "\n".join(lines)


def agents_list(agent) -> str:
    """List all subagents with id, state, role and task."""
    agents = _crew(agent).list()
    if not agents:
        return "no subagents — /agents spawn <task> to start one"
    lines = [f"subagents ({len(agents)}):"]
    lines += [_row(a) for a in agents]
    return "\n".join(lines)


def agents_spawn(agent, task_text: str) -> str:
    """Spawn a researcher subagent for ``task_text``; returns its id."""
    task_text = (task_text or "").strip()
    if not task_text:
        return "usage: /agents spawn <task text>"
    sub = _crew(agent).spawn(task_text, role="researcher")
    return f"✓ spawned subagent {sub.id} (researcher)\n  task: {task_text}"


def agents_stop(agent, agent_id: str) -> str:
    """Close one subagent by id."""
    agent_id = (agent_id or "").strip()
    if not agent_id:
        return "usage: /agents stop <id>"
    crew = _crew(agent)
    if crew.get(agent_id) is None:
        return f"unknown subagent id: {agent_id}"
    crew.close(agent_id)
    return f"✓ stopped subagent {agent_id}"


def agents_stop_all(agent) -> str:
    """Force-stop every subagent and report how many were closed."""
    crew = _crew(agent)
    before = len([a for a in crew.list()
                  if getattr(a, "state", "") == "running"])
    crew.force_stop()
    if before:
        return f"✓ stopped {before} subagent{'s' if before != 1 else ''}"
    return "no running subagents"


def handle(ui, arg: str) -> str:
    """Dispatch a `/agents` argument; returns the string for the UI to print.

    ``ui`` is accepted for future use (e.g. colour); output is returned
    so the wiring snippet stays one line: ``self.print_info(handle(self, arg))``.
    """
    text = (arg or "").strip()
    agent = _agent_from_ui(ui)
    if not text:
        return agents_list(agent)
    parts = text.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    agent = _agent_from_ui(ui)
    if sub == "spawn":
        return agents_spawn(agent, rest)
    if sub == "stop":
        return agents_stop(agent, rest)
    if sub in ("stop-all", "stopall"):
        return agents_stop_all(agent)
    if sub in ("list", "ls"):
        return agents_list(agent)
    if sub == "errors":
        return agents_errors(agent)
    return f"unknown subcommand: {sub}\n{USAGE}"


def _agent_from_ui(ui):
    """Extract the host agent from the TUI object (duck-typed)."""
    agent = getattr(ui, "agent", None)
    if agent is None:
        raise RuntimeError("/agents needs the TUI host agent")
    return agent


# ---------------------------------------------------------------------------
# self-test: `python3 -m fullagent.agentscmd`  →  PASS
# ---------------------------------------------------------------------------

class _FakeSubAgent:
    def __init__(self, aid, task, role="researcher", state="running",
                 nickname=""):
        self.id = aid
        self.task = task
        self.role = role
        self.state = state
        self.nickname = nickname


class _FakeCrew:
    def __init__(self):
        self.agents = {}

    def spawn(self, task, role="researcher", name=""):
        aid = f"sa-{len(self.agents) + 1}"
        sub = _FakeSubAgent(aid, task, role=role, nickname=name or aid)
        self.agents[aid] = sub
        return sub

    def list(self):
        return list(self.agents.values())

    def get(self, agent_id):
        return self.agents.get(agent_id)

    def close(self, agent_id):
        sub = self.agents[agent_id]
        sub.state = "closed"
        return sub

    def force_stop(self):
        for sub in self.agents.values():
            if sub.state == "running":
                sub.state = "closed"


class _FakeAgent:
    def __init__(self):
        self.crew = _FakeCrew()


class _FakeUI:
    def __init__(self):
        self.agent = _FakeAgent()


def _selftest() -> None:
    ui = _FakeUI()

    # /agents (list) with one agent present
    out = handle(ui, "")
    assert "sa-1" not in out, out  # empty crew first
    assert "no subagents" in out, out

    # spawn via handle
    out = handle(ui, "spawn research fastapi session tokens")
    assert "✓ spawned subagent sa-1" in out, out
    assert "researcher" in out, out

    out = handle(ui, "spawn second task")
    assert "sa-2" in out, out

    # list shows both with state/role/task
    out = agents_list(ui.agent)
    assert "sa-1" in out and "sa-2" in out, out
    assert "running" in out and "researcher" in out, out
    assert "fastapi session tokens" in out, out

    # stop one via handle
    out = handle(ui, "stop sa-1")
    assert "✓ stopped subagent sa-1" in out, out
    assert ui.agent.crew.get("sa-1").state == "closed"

    # stop unknown id
    out = agents_stop(ui.agent, "nope")
    assert "unknown subagent id" in out, out

    # stop-all via handle
    out = handle(ui, "stop-all")
    assert "✓ stopped 1 subagent" in out, out
    assert ui.agent.crew.get("sa-2").state == "closed"

    out = handle(ui, "stop-all")
    assert "no running subagents" in out, out

    # usage / unknown
    out = handle(ui, "bogus")
    assert "usage:" in out, out
    out = agents_spawn(ui.agent, "   ")
    assert "usage: /agents spawn" in out, out

    print("PASS")


if __name__ == "__main__":
    _selftest()
