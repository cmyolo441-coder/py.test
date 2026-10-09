"""DASHBOARD — live observability over the Temporal Kernel.

The whole system is already event-sourced; the dashboard is simply the
X-ray: a real-time projection of the ledger into one screen — cost,
tokens, goal progress, active sub-agents, routing savings, speculation
hit-rate, dead-ends, verdicts, loop alerts, and the live event stream.

Design (pure Python, stdlib only):
  * Every panel is a pure fold over the EventLog — the dashboard keeps no
    state of its own, so it can never disagree with the kernel.
  * render() returns plain text (the TUI colours it); snapshot() returns
    the raw dict for programmatic consumers.
  * tail() streams the newest events since a seq cursor, so the TUI can
    poll cheaply without re-rendering everything.
"""

from __future__ import annotations

import time

from .kernel import EventLog, fold
from ._foundation import get_logger

_log = get_logger("dashboard")

# panels the dashboard renders, in display order
PANELS = ("cost", "goal", "agents", "router", "speculator", "memory",
          "health", "engineering", "stream")


def _safe_float(v, default: float = 0.0) -> float:
    """float() that never raises — malformed event fields must not kill
    the render path."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int = 0) -> int:
    """int() that never raises — same contract as _safe_float."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _copy_snapshot(s: dict) -> dict:
    """Defensive copy of a snapshot for callers. The top-level dict copy
    is not enough: the crew_agents row list/dicts are mutable, and the
    old shallow copy let a caller corrupt the cached entry (rows edited
    in place would persist across renders until the log head moved)."""
    out = dict(s)
    agents = out.get("crew_agents")
    if isinstance(agents, list):
        out["crew_agents"] = [dict(a) for a in agents]
    return out


def _fold_crew_agents(log) -> list[dict]:
    """Fold crew.* events into per-agent display rows.

    Pure fold over the event log (no state kept): spawn order is stable,
    running agents show step/elapsed, completed ones collapse to one line.
    Defensive — handles crew.done without a prior crew.spawn, non-dict
    event payloads, and explicit null ids (no "None" ghost agents).
    Mirrors the crew.py lifecycle: crew.message (follow-up send)
    resurrects a settled agent back to running, crew.resumed restores
    its terminal state, and crew.closed retires only the named agent
    (unlike crew.force_stop, which stops the whole roster).
    """
    agents: dict[str, dict] = {}
    order: list[str] = []
    for ev in log.events():
        t = ev.type
        d = ev.data if isinstance(ev.data, dict) else {}
        if t == "crew.spawn":
            aid = str(d.get("id") or "")
            if aid and aid not in agents:
                agents[aid] = {
                    "id": aid,
                    "name": str(d.get("nickname") or aid),
                    "role": str(d.get("role") or ""),
                    "task": str(d.get("task") or ""),
                    "status": "running",
                    "step": 0,
                    "spawn_ts": getattr(ev, "ts", 0.0) or 0.0,
                    "elapsed_ms": 0,
                    "error": "",
                    "had_error": False,
                    "files": 0,
                }
                order.append(aid)
        elif t == "crew.progress":
            aid = str(d.get("id") or "")
            st = agents.get(aid)
            if st is not None and st["status"] == "running":
                try:
                    st["step"] = int(d.get("step", st["step"]))
                except (TypeError, ValueError):
                    pass
        elif t == "crew.done":
            aid = str(d.get("id") or "")
            st = agents.get(aid)
            if st is None and aid:
                st = {"id": aid,
                      "name": str(d.get("nickname") or aid),
                      "role": str(d.get("role") or ""),
                      "task": str(d.get("task") or ""),
                      "status": "running", "step": 0,
                      "spawn_ts": 0.0, "elapsed_ms": 0,
                      "error": "", "had_error": False, "files": 0}
                agents[aid] = st
                order.append(aid)
            if st is not None and st["status"] == "running":
                err = str(d.get("error") or "")
                state = str(d.get("state") or "")
                # crew.py seals state="blocked" for agents that finished
                # without a verdict — not an error, but not "done" either
                if err:
                    st["status"] = "error"
                elif state == "blocked":
                    st["status"] = "blocked"
                else:
                    st["status"] = "done"
                st["error"] = err[:80]
                st["had_error"] = bool(err)
                try:
                    st["elapsed_ms"] = int(d.get("elapsed_ms") or 0)
                except (TypeError, ValueError):
                    pass
                files = d.get("files_touched") or []
                st["files"] = len(files) if isinstance(files, list) else 0
        elif t == "crew.message":
            # crew.send() to a settled agent resubmits it — the agent is
            # running again with a cleared error. Without this the row
            # would stay frozen on its old terminal state forever.
            aid = str(d.get("id") or "")
            st = agents.get(aid)
            if st is not None and st["status"] != "running":
                st["status"] = "running"
                st["step"] = 0
                st["error"] = ""
                st["had_error"] = False
                st["spawn_ts"] = getattr(ev, "ts", 0.0) or 0.0
        elif t == "crew.resumed":
            # crew.resume() restores done/error from the agent's error
            # field — mirror it via had_error so the row leaves
            # "stopped" instead of sticking there forever
            aid = str(d.get("id") or "")
            st = agents.get(aid)
            if st is not None and st["status"] != "running":
                st["status"] = "error" if st.get("had_error") else "done"
        elif t == "crew.closed":
            # crew.closed carries ONE agent id (unlike crew.force_stop,
            # which is roster-wide). Retiring one agent must not flip
            # every other running agent to stopped. Only a running agent
            # is affected — closing an already-finished one keeps its
            # terminal state.
            aid = str(d.get("id") or "")
            st = agents.get(aid)
            if st is not None and st["status"] == "running":
                st["status"] = "stopped"
                st["error"] = "closed"
        elif t == "crew.force_stop":
            for aid in order:
                st = agents[aid]
                if st["status"] == "running":
                    st["status"] = "stopped"
                    st["error"] = "stopped"
    import time as _time
    now = _time.time()
    out: list[dict] = []
    for aid in order:
        st = agents[aid]
        if st["status"] == "running":
            if st["spawn_ts"]:
                secs = max(0, int(now - st["spawn_ts"]))
            else:
                secs = 0
            step_bit = f" step {st['step']}" if st["step"] else ""
            task = " ".join(st["task"].split())[:50]
            row = (f"\u25cb {st['name']} \u00b7 {st['role']} {task}"
                   f"{step_bit} \u00b7 {secs}s")
        elif st["status"] == "done":
            secs = st["elapsed_ms"] / 1000.0 if st["elapsed_ms"] else 0
            row = (f"\u2713 {st['name']} \u00b7 done in {secs:.0f}s"
                   + (f" \u00b7 {st['files']} files" if st["files"] else ""))
        elif st["status"] == "error":
            row = f"\u2717 {st['name']} \u00b7 {st['error'][:60]}"
        elif st["status"] == "blocked":
            row = (f"\u25d0 {st['name']} \u00b7 blocked"
                   + (f" \u00b7 {st['error'][:60]}" if st["error"] else ""))
        else:
            row = f"\u25a0 {st['name']} \u00b7 {st['error'][:60]}"
        out.append({"id": aid, "status": st["status"], "row": row})
    return out


class Dashboard:
    """Read-only live projection of the event log. Never writes."""

    def __init__(self, log: EventLog) -> None:
        self.log = log
        # SPEED: snapshot() folds the log plus a full event walk for the
        # crew count. The fold itself is incrementally cached, but the
        # walk is O(N) per call — cache the whole snapshot keyed by the
        # log head so repeated renders (polling tickers) are O(1).
        self._snap_cache: dict | None = None
        self._snap_head: int = -2

    # -- snapshot (raw dict) ---------------------------------------------------

    def snapshot(self) -> dict:
        head = self.log.head()
        if self._snap_cache is not None and self._snap_head == head:
            # Log unchanged since the last snapshot — the fold cache and
            # every count below would recompute identically. Return a
            # defensive copy so callers can't corrupt the cached entry
            # (the top-level copy alone would still share the mutable
            # crew_agents rows).
            return _copy_snapshot(self._snap_cache)
        s = self._snapshot_uncached()
        self._snap_cache = s
        self._snap_head = head
        return _copy_snapshot(s)

    def _snapshot_uncached(self) -> dict:
        st = fold(self.log)
        goal = st.goal
        proven = len(st.goal_done)
        clauses = len(goal.get("clauses") or []) if goal else 0

        # sub-agent activity from the crew roster
        crew_done = sum(1 for e in self.log.events()
                        if e.type == "crew.done")

        # routing + speculation dividends
        routed = len(st.router_decisions)
        routed_cost = sum(_safe_float(d.get("est_cost"))
                          for d in st.router_decisions)
        prefetched = sum(1 for e in st.spec_events
                         if e.get("type") == "spec.prefetch")
        spec_hits = sum(1 for e in st.spec_events
                        if e.get("type") == "spec.hit")
        spec_misses = sum(1 for e in st.spec_events
                          if e.get("type") == "spec.miss")

        # healing + skills + council activity
        heals = sum(1 for e in st.heal_events
                    if e.get("type") == "heal.lesson")
        skills = sum(1 for e in st.skill_events
                     if e.get("type") == "skill.registered")
        councils = sum(1 for e in st.council_events
                       if e.get("type") == "council.verdict")

        # v4 engineering subsystem activity
        taint_findings = sum(len(e.get("findings") or [])
                             for e in st.analysis_events
                             if e.get("type") == "analysis.taint")
        graph_entities = 0
        for e in st.graph_events:
            graph_entities = max(graph_entities,
                                 _safe_int(e.get("entities", 0)))
        cov_runs = [e for e in st.coverage_events
                    if e.get("type") == "coverage.result"]
        cov_last = (_safe_float(cov_runs[-1].get("percent", 0.0))
                    if cov_runs else None)
        fuzz_crashes = sum(1 for e in st.fuzz_events
                           if e.get("type") == "fuzz.crash")
        mut_reports = [e for e in st.mutation_events
                       if e.get("type") == "mutation.result"]
        _mut_score = mut_reports[-1].get("score") if mut_reports else None
        mut_last = (_safe_float(_mut_score)
                    if _mut_score is not None else None)

        # parallel agents detail: per-agent rows from crew.* events.
        # Fold is O(crew events) and runs once per log-head change thanks
        # to the snapshot cache above.
        crew_agents = _fold_crew_agents(self.log)

        return {
            "head_seq": st.head_seq,
            "crew_agents": crew_agents,
            "cost_usd": st.cost_usd,
            "tokens_in": st.tokens_in,
            "tokens_out": st.tokens_out,
            "tool_calls": st.tool_calls,
            "tool_errors": st.tool_errors,
            "commands_run": st.commands_run,
            "files_touched": len(st.files_touched),
            "goal_active": bool(goal and goal.get("statement")),
            "goal_statement": (goal or {}).get("statement", ""),
            "clauses_proven": proven,
            "clauses_total": clauses,
            "crew_done": crew_done,
            "routed": routed,
            "routed_cost": round(routed_cost, 4),
            "spec_prefetched": prefetched,
            "spec_hits": spec_hits,
            "spec_misses": spec_misses,
            "episodes": len(st.episodes),
            "dead_ends": len(st.dead_ends),
            "facts": len(st.facts),
            "verdicts": len(st.verdicts),
            "verdicts_failed": sum(1 for v in st.verdicts
                                   if not v.get("passed")),
            "loop_alerts": len(st.loop_alerts),
            "budget_events": len(st.budget_events),
            "heals": heals,
            "skills": skills,
            "councils": councils,
            "taint_findings": taint_findings,
            "graph_entities": graph_entities,
            "cov_runs": len(cov_runs),
            "cov_last": cov_last,
            "fuzz_crashes": fuzz_crashes,
            "mut_runs": len(mut_reports),
            "mut_last": mut_last,
        }

    # -- render (text) -----------------------------------------------------------

    def render(self, width: int = 62) -> str:
        s = self.snapshot()
        bar = "─" * width
        lines = ["◆ FULLAGENT LIVE DASHBOARD", bar]

        # cost panel
        lines.append(
            f" COST   ${s['cost_usd']:.4f}   "
            f"{s['tokens_in']}→{s['tokens_out']} tok   "
            f"tools {s['tool_calls']} (err {s['tool_errors']})   "
            f"cmds {s['commands_run']}   files {s['files_touched']}")

        # goal panel
        if s["goal_active"]:
            pct = (s["clauses_proven"] / s["clauses_total"] * 100
                   if s["clauses_total"] else 0)
            filled = int(round(pct / 100 * 20))
            gbar = "█" * filled + "░" * (20 - filled)
            lines.append(f" GOAL   [{gbar}] {pct:.0f}%  "
                         f"{s['clauses_proven']}/{s['clauses_total']} "
                         f"clauses  \"{s['goal_statement'][:34]}\"")
        else:
            lines.append(" GOAL   none active")

        # agents panel: per-agent rows (running with spinner step,
        # completed collapsed to one line each)
        agents = s.get("crew_agents") or []
        running = sum(1 for a in agents if a["status"] == "running")
        lines.append(f" AGENTS {running} running / {len(agents)} total   "
                     f"crew done {s['crew_done']}   councils {s['councils']}")
        for a in agents[:8]:  # cap: dashboard is a summary, not a ledger
            lines.append(f"   {a['row']}")

        # router panel
        lines.append(f" ROUTER {s['routed']} routed   "
                     f"est ${s['routed_cost']:.4f}")

        # speculator panel
        total_spec = s["spec_hits"] + s["spec_misses"]
        rate = (s["spec_hits"] / total_spec) if total_spec else 0.0
        lines.append(f" SPEC   prefetched {s['spec_prefetched']}   "
                     f"hits {s['spec_hits']}   misses {s['spec_misses']}   "
                     f"rate {rate:.0%}")

        # memory panel
        lines.append(f" MEMORY episodes {s['episodes']}   "
                     f"facts {s['facts']}   "
                     f"dead-ends {s['dead_ends']}   "
                     f"heals {s['heals']}   skills {s['skills']}")

        # health panel
        lines.append(f" HEALTH verdicts {s['verdicts']} "
                     f"(failed {s['verdicts_failed']})   "
                     f"loop alerts {s['loop_alerts']}   "
                     f"budget events {s['budget_events']}")

        # engineering panel (v4: analysis, graph, coverage, fuzz, mutation)
        cov = (f"{s['cov_last']:.0f}%" if s["cov_last"] is not None
               else "—")
        mut = (f"{s['mut_last']:.0%}" if s["mut_last"] is not None
               else "—")
        lines.append(f" ENGINE taint {s['taint_findings']}   "
                     f"graph {s['graph_entities']} ent   "
                     f"cov {cov} ({s['cov_runs']} runs)   "
                     f"fuzz ⚠{s['fuzz_crashes']}   "
                     f"mut {mut} ({s['mut_runs']} runs)")

        lines.append(bar)
        return "\n".join(lines)

    # -- event stream --------------------------------------------------------------

    def tail(self, since_seq: int = -1, limit: int = 12) -> list[dict]:
        """Newest events with seq > since_seq (oldest first), for a live
        ticker. Cheap: walks the chain once.

        The TUI polls this on every refresh tick. Walking the full chain
        and materialising every event before slicing was O(N) per call —
        for a 10k-event log with 200ms refresh the dashboard chewed CPU
        for no reason. We now walk in REVERSE and stop the moment the
        requested window is filled (or we cross the `since_seq` horizon)."""
        if limit <= 0:
            return []
        events = self.log.events()
        out: list[dict] = []
        # oldest first in the result, so reverse-walk then prepend/reverse
        # at the end
        for ev in reversed(events):
            if ev.seq <= since_seq:
                break
            out.append({"seq": ev.seq, "type": ev.type,
                        "actor": ev.actor,
                        "summary": _summarise(ev.type, ev.data)})
            if len(out) >= limit:
                break
        out.reverse()
        return out


def _summarise(type_: str, data: dict) -> str:
    """One-line human summary of an event for the ticker.

    Defensive: a single malformed event (non-dict payload, garbage
    numerics) must not take down the TUI's polling ticker.
    """
    if not isinstance(data, dict):
        return ""
    if type_ == "user.message":
        return str(data.get("text", ""))[:60]
    if type_ == "assistant.message":
        return str(data.get("text", ""))[:60]
    if type_ == "tool.call":
        return f"{data.get('name', '?')}"
    if type_ == "tool.result":
        return f"{data.get('name', '?')} -> {data.get('status', '?')}"
    if type_ == "cost.incurred":
        return f"${_safe_float(data.get('usd', 0)):.4f}"
    if type_ == "router.decision":
        return f"-> {data.get('model', '?')}"
    if type_ == "spec.hit":
        return f"cache hit {data.get('tool', '?')}"
    if type_ == "judge.verdict":
        return f"{'PASS' if data.get('passed') else 'FAIL'} " \
               f"{data.get('kind', '?')}"
    if type_ == "goal.distance":
        return f"distance {_safe_float(data.get('distance', 1)):.2f}"
    if type_ in ("heal.lesson", "skill.registered", "council.verdict"):
        return type_
    if type_ == "coverage.result":
        return (f"coverage {_safe_float(data.get('percent', 0)):.0f}% "
                f"{data.get('path', '')}")
    if type_ == "fuzz.crash":
        return f"crash {str(data.get('error', ''))[:40]}"
    if type_ == "mutation.result":
        return f"mutation score {_safe_float(data.get('score', 0)):.0%}"
    if type_ == "analysis.taint":
        return f"taint {len(data.get('findings') or [])} finding(s)"
    return ""


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        log = EventLog(Path(td) / "dash.jsonl")
        dash = Dashboard(log)

        # empty log still renders every panel without crashing
        text = dash.render()
        assert "FULLAGENT LIVE DASHBOARD" in text
        assert "GOAL   none active" in text

        # seed a realistic session
        log.append("user.message", {"text": "fix the parser"}, actor="human")
        log.append("tool.call", {"name": "read_file",
                                 "args": {"path": "p.py"}})
        log.append("tool.result", {"name": "read_file", "status": "done"})
        log.append("tool.result", {"name": "edit_file", "status": "error"})
        log.append("cost.incurred", {"usd": 0.05, "tokens_in": 100,
                                     "tokens_out": 40})
        log.append("goal.set", {"statement": "fix parser",
                                "clauses": [{"id": "C1"}, {"id": "C2"}]})
        log.append("goal.clause.done", {"clause": "C1"})
        log.append("router.decision", {"model": "stealth/union-alpha",
                                       "est_cost": 0.0})
        log.append("spec.prefetch", {"tool": "read_file"})
        log.append("spec.hit", {"tool": "read_file"})
        log.append("judge.verdict", {"passed": True, "kind": "exit_code"})
        log.append("judge.verdict", {"passed": False, "kind": "file_exists"})
        log.append("memory.episode", {"goal": "fix parser",
                                      "outcome": "success"})
        log.append("deadend.recorded", {"signature": "x", "reason": "y"})
        log.append("heal.lesson", {"root": "missing import"})
        log.append("skill.registered", {"name": "csv_clean"})
        log.append("council.verdict", {"decision": "thesis"})
        # v4 engineering events
        log.append("analysis.taint",
                   {"path": "p.py", "findings": [{"sink": "eval"}]})
        log.append("graph.entity", {"entities": 12, "relations": 9})
        log.append("coverage.result", {"path": "p.py", "percent": 83.0,
                                       "hit": 5, "total": 6})
        log.append("fuzz.run", {"target": "f", "iterations": 30})
        log.append("fuzz.crash", {"target": "f", "error": "TypeError"})
        log.append("mutation.result", {"path": "p.py", "score": 0.29,
                                       "killed": 2, "survived": 5})

        s = dash.snapshot()
        assert abs(s["cost_usd"] - 0.05) < 1e-9
        assert s["tool_calls"] == 1 and s["tool_errors"] == 1
        assert s["goal_active"] and s["clauses_proven"] == 1
        assert s["clauses_total"] == 2
        assert s["routed"] == 1 and s["spec_hits"] == 1
        assert s["verdicts"] == 2 and s["verdicts_failed"] == 1
        assert s["heals"] == 1 and s["skills"] == 1 and s["councils"] == 1
        assert s["taint_findings"] == 1 and s["graph_entities"] == 12
        assert s["cov_runs"] == 1 and s["cov_last"] == 83.0
        assert s["fuzz_crashes"] == 1
        assert s["mut_runs"] == 1 and abs(s["mut_last"] - 0.29) < 1e-9

        text = dash.render()
        assert "GOAL" in text and "50%" in text, text
        assert "ROUTER" in text and "SPEC" in text
        assert "HEALTH" in text
        assert "ENGINE" in text and "83%" in text and "29%" in text, text

        # tail streams only events after the cursor
        tail = dash.tail(since_seq=-1, limit=5)
        assert len(tail) == 5
        assert all(t["seq"] >= 0 for t in tail)
        head = log.head()
        assert dash.tail(since_seq=head) == []

    print("DASHBOARD SELF-TEST PASS")
