"""CREW — parallel subagents with a real lifecycle.

The Crew is the ONLY way to execute a subagent — PERSISTENT, addressable
agents with a real lifecycle, exactly like a lead engineer managing a
roster of specialists:

    spawn(task, role)         launch ONE subagent, returns immediately
    spawn_parallel(tasks)     launch N subagents AT ONCE — they run
                              concurrently, like Muse's own subagents
    send(id, message)         follow-up message into a living subagent's context
    wait(ids, timeout)        block until the named subagents finish
    poll()                    non-blocking status snapshot of every agent
    close(id)                 retire a subagent (aborts it promptly)
    resume(id)                bring a closed subagent back with full context

Each CrewAgent is a REAL agent: its own role brief, its own tool
whitelist, its own multi-step tool loop, its own message history that
SURVIVES follow-up messages — so you can iterate on a subagent instead
of re-spawning from scratch. Agents run CONCURRENTLY in a bounded
thread pool (default 10 workers): spawning returns at once and wait()
collects each verdict as its agent finishes.

Hard rules (mechanical, same discipline as the rest of FullAgent):
  * ONE bounded pool — at most max_agents subagents execute at once.
    Spawning past capacity raises CrewError (fail fast, no silent queue).
  * Thread-safe isolation: every agent owns its conversation state and
    its own mutex; tools are shared read-only; ALL EventLog writes go
    through the log's own RLock; file/command WRITES additionally pass
    through the SAME global _WRITE_LOCK as every other subsystem
    (invariant I7) — concurrent agents never corrupt each other's
    files or the log.
  * Every lifecycle transition is sealed in the event log: crew.spawn,
    crew.progress, crew.message, crew.done, crew.closed, crew.resumed.
    The crew history is replayable and auditable.
  * A failing subagent never kills the crew or the pool; it lands as
    an error report and can be sent a follow-up or closed.
  * Follow-ups reuse the subagent's full conversation — context is the
    dividend of persistence.
"""

from __future__ import annotations

import concurrent.futures
import itertools
import threading
import time
from typing import Callable
from ._foundation import get_logger

_log = get_logger("crew")
from dataclasses import dataclass, field

from . import systemprompt
from .config import PROVIDERS, model_by_id
from .kernel import EventLog, fold
from .team import (ROLES, DEFAULT_ROLE, MAX_WORKER_STEPS,
                   _WRITE_LOCK, chat_with_retry, parse_worker_final)
from .tools import Tool, build_registry, parse_tool_arguments

MAX_AGENTS = 10            # default pool size / roster ceiling
MAX_SEND_STEPS = 40        # tool-loop budget per follow-up message
WAIT_POLL_SECONDS = 0.05   # wait() fallback sleep granularity
WAIT_SLICE_SECONDS = 0.25  # wait() wakes this often to notice re-submits

# Codex-flavoured callsigns for the crew roster.
_CALLSIGNS = ("nova", "atlas", "echo", "lyra", "orion", "vega", "iris",
              "argo", "sable", "kepler", "juno", "helix", "drift", "onyx",
              "piper", "quill")

AGENT_STATES = ("running", "done", "blocked", "error", "closed")

_ROLE_ICON = {"researcher": "🔎", "coder": "👨‍💻", "tester": "🧪",
              "reviewer": "🧐", "analyst": "📊", "architect": "🏛️",
              "debugger": "🐞", "optimizer": "⚡", "refactorer": "🧹",
              "documenter": "📝", "devops": "🛠️", "integrator": "🔗",
              "planner": "🗺️"}


@dataclass
class CrewAgent:
    """One persistent subagent. The message history is the point: it
    survives follow-ups, so iteration never starts from zero."""
    id: str
    nickname: str
    role: str
    task: str
    state: str = "running"      # running | done | blocked | error | closed
    summary: str = ""
    error: str = ""
    messages: list = field(default_factory=list)   # full conversation
    files_touched: list = field(default_factory=list)
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    spawned_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    pending_messages: list = field(default_factory=list)
    model_id: str = ""          # per-agent model override ("" = crew default)
    read_only: bool = False     # tool restriction survives follow-ups/resume
    # Cooperative stop flag. force_stop()/close() set it; the worker
    # loop checks it every step and bails promptly. A plain boolean
    # would need the mutex on the hot path — an Event is lock-free.
    stop_event: threading.Event = field(default_factory=threading.Event,
                                        repr=False, compare=False)
    # Iteration generation. Every submit (spawn, follow-up re-submit,
    # queued-follow-up re-submit) bumps this under the agent mutex, and
    # the pool entry point carries the generation it was submitted with.
    # A loop iteration that lost a race — e.g. send() re-submitted a new
    # iteration while the old one's landing was still queued on the
    # mutex — sees a stale generation and must NOT finalise: finalising
    # would clobber the new iteration's "running" back to "done", emit a
    # duplicate crew.done, and leave the follow-up running invisibly in
    # the background while the UI shows "done".
    generation: int = 0
    # Per-agent mutex. Pool threads run agents concurrently, while the
    # *sovereign* thread (TUI, workflow executor, council) can call
    # `crew.send`, `crew.close`, `crew.resume` at any moment. Without
    # this lock, the worker reads `agent.state == "running"` and the
    # sovereign flips it to `"closed"` a microsecond later — the worker
    # then enqueues a follow-up run for an already-retired agent.
    # Worse: the `pending_messages` list is shared, so a `pop(0)` from
    # the worker interleaves with a sovereign `append`, silently losing
    # follow-ups.
    mutex: threading.RLock = field(default_factory=threading.RLock)

    @property
    def icon(self) -> str:
        return _ROLE_ICON.get(self.role, "◆")

    @property
    def elapsed_ms(self) -> int:
        end = self.finished_at or time.time()
        return int((end - self.spawned_at) * 1000)

    def to_dict(self) -> dict:
        return {"id": self.id, "nickname": self.nickname, "role": self.role,
                "task": self.task, "state": self.state,
                "model": self.model_id,
                "summary": self.summary[:600], "error": self.error[:300],
                "files_touched": self.files_touched[:12],
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "elapsed_ms": self.elapsed_ms}


class CrewError(RuntimeError):
    """Raised for invalid lifecycle operations (unknown id, spawn at
    capacity, send to a closed agent)."""


class Crew:
    """Persistent, addressable subagents over a shared EventLog.

    Execution model: a bounded ThreadPoolExecutor. spawn() submits the
    agent's tool loop and returns at once; up to max_agents loops run
    truly concurrently. `chat` is injectable for tests:
    chat(provider, model, effort, messages, schemas, timeout) ->
    StreamResult. Production uses the rate-limit-hardened
    chat_with_retry from team.py.

    Call shutdown() when the crew is retired so pool threads don't
    linger past interpreter teardown.
    """

    def __init__(self, log: EventLog, provider, model, effort,
                 mastermind=None, max_agents: int = MAX_AGENTS,
                 chat=None) -> None:
        self.log = log
        self.provider = provider
        self.model = model
        self.effort = effort
        self.mastermind = mastermind
        self.max_agents = max(1, int(max_agents))
        self._chat = chat or chat_with_retry
        # CANCEL: Esc sets each agent's stop_event (via force_stop), but
        # the worker loop only checked it BETWEEN steps — a worker stuck
        # inside a blocking model call ignored it for up to `timeout`
        # seconds (and chat_with_retry's rate-limit backoff could add
        # ~254s). Detect once whether the chat callable honours a
        # should_cancel kwarg; _run_loop passes the stop flag through.
        import inspect as _inspect
        try:
            self._chat_takes_cancel = (
                "should_cancel" in
                _inspect.signature(self._chat).parameters)
        except (TypeError, ValueError):
            self._chat_takes_cancel = False
        self._agents: dict[str, CrewAgent] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()  # protects roster + _futures.
        # Re-entrant on purpose: spawn_parallel() holds it across its
        # atomic check-and-spawn loop while spawn()/_submit() acquire it
        # again on the same thread. No path ever takes agent.mutex and
        # then self._lock while holding the mutex, so _lock -> mutex
        # nesting stays deadlock-free.
        self._names = itertools.cycle(_CALLSIGNS)
        self._counter = 0
        # role tool whitelists carved from the main registry (read-only
        # after init — safe for concurrent readers)
        registry = build_registry()
        self._toolsets: dict[str, dict[str, Tool]] = {}
        for role, spec in ROLES.items():
            self._toolsets[role] = {n: registry[n] for n in spec["tools"]
                                    if n in registry}
        # The parallel executor: one bounded pool, max_agents workers.
        # Each subagent's tool loop is a task; they genuinely overlap.
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_agents,
            thread_name_prefix="crew:agent")
        # agent id -> Future of its CURRENT loop iteration. Replaced on
        # follow-up re-submits; pruned lazily by _prune_futures().
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._shutdown = False

    # -- internal ---------------------------------------------------------------

    def _submit(self, agent: CrewAgent, read_only: bool,
                max_steps: int, gen: int) -> bool:
        """Submit one loop iteration to the pool (thread-safe).

        Returns False when the crew is shut down — the caller must then
        settle the agent itself; it must never be left "running" with no
        future behind it (wait() would hang to timeout on a ghost)."""
        with self._lock:
            if self._shutdown:
                return False
            self._futures[agent.id] = self._pool.submit(
                self._execute, agent, read_only, max_steps, gen)
            return True

    def _execute(self, agent: CrewAgent, read_only: bool,
                 max_steps: int, gen: int) -> None:
        """Pool entry point. Never raises: a crash here must not poison
        the pool or the other agents — it lands as an error report."""
        try:
            with agent.mutex:
                if gen != agent.generation:
                    # Superseded before its thread even started (a newer
                    # iteration was submitted) — never run it.
                    return
                if agent.state == "closed" or agent.stop_event.is_set():
                    # retired before its thread started — never run it
                    return
            self._run_loop(agent, read_only, max_steps, gen)
        except BaseException as e:  # noqa: BLE001 — absolute last resort
            # BaseException, not just Exception: anything escaping the
            # loop (KeyboardInterrupt, SystemExit, ...) would otherwise
            # sit unretrieved on the Future and the subagent would die
            # invisibly. Record it instead.
            try:
                with agent.mutex:
                    if gen != agent.generation:
                        # a newer iteration owns the state now
                        return
                    agent.state = "error"
                    agent.error = f"{type(e).__name__}: {e}"
                    agent.finished_at = time.time()
                self.log.append("crew.done", agent.to_dict(),
                                actor=f"crew:{agent.id}")
            except Exception:
                pass

    def _prune_futures(self) -> None:
        """Drop finished futures of settled agents (lazy GC)."""
        with self._lock:
            stale = [aid for aid, fut in self._futures.items()
                     if fut.done() and (a := self._agents.get(aid)) is not None
                     and a.state != "running"]
            for aid in stale:
                del self._futures[aid]

    def _live_count(self) -> int:
        return sum(1 for a in self._agents.values()
                   if a.state == "running")

    # -- lifecycle -------------------------------------------------------------

    def spawn(self, task: str, role: str = DEFAULT_ROLE, name: str = "",
              context: str = "", read_only: bool = False,
              model_id: str = "") -> CrewAgent:
        """Launch a subagent; returns IMMEDIATELY with its handle. The
        agent runs CONCURRENTLY with the rest of the crew in the pool;
        wait()/poll() collects its verdict.

        model_id optionally overrides the model THIS subagent uses
        (Codex-style per-agent model override) — e.g. a cheap fast model
        for grunt work, the strongest model for the hard piece. Unknown
        ids fall back to the crew default with a sealed note.

        Raises CrewError when the crew is at capacity (max_agents
        running) — fail fast, never silently queue."""
        task = str(task or "").strip()
        if not task:
            raise CrewError("cannot spawn a subagent without a task")
        if role not in ROLES:
            role = DEFAULT_ROLE
        # Build the opening conversation BEFORE touching the roster.
        # mastermind.dispatch() can raise — the old order left a
        # "running" agent registered that was never started, so wait()
        # hung until timeout on a ghost.
        user = (f"Shared context:\n{context}\n\nYOUR TASK: {task}"
                if context else f"YOUR TASK: {task}")
        opening: list[dict] = []
        if self.mastermind is not None:
            opening, _ = self.mastermind.gate.dispatch(
                f"worker:{role}", opening)
        else:
            systemprompt.with_system(opening,
                                     systemprompt.worker(role,
                                                         self.max_agents))
        opening.append({"role": "user", "content": user})
        with self._lock:
            if self._shutdown:
                raise CrewError("crew is shut down — cannot spawn a subagent")
            if self._live_count() >= self.max_agents:
                raise CrewError(
                    f"crew is at capacity ({self.max_agents} agents "
                    f"running) — wait for one to finish or close one")
            self._counter += 1
            agent_id = f"crew-{self._counter}"
            nickname = str(name or "").strip() or next(self._names)
            while any(a.nickname == nickname
                      for a in self._agents.values()):
                nickname = f"{nickname}-{self._counter}"
            agent = CrewAgent(id=agent_id, nickname=nickname, role=role,
                              task=task, read_only=bool(read_only))
            agent.messages = opening
            agent.generation = 1  # first loop iteration
            override = model_by_id(str(model_id or "")) if model_id else None
            if override is not None:
                agent.model_id = override.id
            self._agents[agent_id] = agent
            self._order.append(agent_id)

        self.log.append("crew.spawn",
                        {"id": agent.id, "nickname": agent.nickname,
                         "role": role, "task": task[:300],
                         "read_only": bool(read_only),
                         "model": agent.model_id or self.model.id},
                        actor="sovereign")
        if not self._submit(agent, read_only, MAX_WORKER_STEPS,
                            agent.generation):
            # shutdown() raced us between registration and submit — settle
            # the agent instead of leaving a "running" ghost with no future
            with agent.mutex:
                agent.state = "error"
                agent.error = "crew shut down before the subagent started"
                agent.finished_at = time.time()
            self.log.append("crew.done", agent.to_dict(),
                            actor="sovereign")
            raise CrewError("crew is shut down — cannot spawn a subagent")
        return agent

    def spawn_parallel(self, tasks: list[dict | str],
                       role: str = DEFAULT_ROLE, context: str = "",
                       read_only: bool = False,
                       model_id: str = "") -> list[CrewAgent]:
        """Launch N subagents AT ONCE — the main parallel API. Every
        agent starts in the pool concurrently (up to max_agents) and
        they genuinely overlap in time.

        Each item is either a task string or a dict:
            {"task": ..., "role": ..., "name": ..., "context": ...,
             "read_only": ..., "model_id": ...}
        Per-item keys override the call-level defaults.

        Atomic capacity check: if the batch would exceed max_agents,
        NOTHING is spawned and CrewError is raised."""
        items = list(tasks or [])
        if not items:
            return []
        if role not in ROLES:
            role = DEFAULT_ROLE
        # Normalise + validate BEFORE touching the roster: a bad item must
        # fail the whole batch, never leave a partially-spawned one behind.
        prepared: list[dict] = []
        for item in items:
            if isinstance(item, str):
                item = {"task": item}
            if not isinstance(item, dict):
                raise CrewError(
                    "spawn_parallel items must be task strings or dicts, "
                    f"got {type(item).__name__} — nothing spawned")
            if not str(item.get("task", "")).strip():
                raise CrewError(
                    "spawn_parallel item is missing its task — "
                    "nothing spawned")
            prepared.append(item)
        # One lock hold across the capacity check AND every spawn: the
        # batch is atomic — either all items spawn or none does. A plain
        # check-then-loop would let a concurrent spawn() slip in between
        # and turn a later item's CrewError into a partial batch.
        # (self._lock is an RLock, so spawn()'s own acquisition on this
        # thread re-enters safely.)
        with self._lock:
            if self._live_count() + len(prepared) > self.max_agents:
                raise CrewError(
                    f"spawn_parallel of {len(prepared)} would exceed crew "
                    f"capacity ({self.max_agents} running) — "
                    f"{self._live_count()} already running")
            return [self.spawn(
                task=item.get("task", ""),
                role=item.get("role", role),
                name=item.get("name", ""),
                context=item.get("context", context),
                read_only=item.get("read_only", read_only),
                model_id=item.get("model_id", model_id))
                for item in prepared]

    def send(self, agent_id: str, message: str,
             interrupt: bool = False) -> CrewAgent:
        """Send a follow-up into a subagent's LIVING context.

        done/blocked/error agents start a new loop iteration with the
        message appended (full history preserved) — the iteration is
        submitted to the pool and runs concurrently. A running agent
        gets the message queued — it is delivered the moment the
        current loop finishes (interrupt=True jumps the queue: it is
        delivered before older pending follow-ups)."""
        agent = self._require(agent_id)
        message = str(message or "").strip()
        if not message:
            raise CrewError("cannot send an empty message")
        # Decide under the agent mutex; submit AFTER releasing it, so
        # lock ordering stays flat (never mutex -> pool-lock nesting).
        resubmit = False
        gen = 0
        prev_state = ""
        prev_error = ""
        with agent.mutex:
            if agent.state == "closed":
                raise CrewError(
                    f"agent {agent_id} is closed — resume it first")
            self.log.append("crew.message",
                            {"id": agent_id, "chars": len(message),
                             "interrupt": bool(interrupt)},
                            actor="sovereign")
            if agent.state == "running":
                if interrupt:
                    # priority: delivered first when the current loop
                    # lands, instead of queueing behind older follow-ups
                    agent.pending_messages.insert(0, message)
                else:
                    agent.pending_messages.append(message)
            else:
                prev_state = agent.state
                prev_error = agent.error
                if interrupt:
                    agent.summary = ""
                agent.messages.append({"role": "user",
                                       "content": f"FOLLOW-UP: {message}"})
                agent.state = "running"
                agent.error = ""
                agent.stop_event.clear()
                agent.generation += 1
                gen = agent.generation
                resubmit = True
        if resubmit:
            # keep the spawn-time tool restriction — a read-only subagent
            # must never gain write tools through a follow-up
            if not self._submit(agent, agent.read_only, MAX_SEND_STEPS, gen):
                # shutdown() raced the re-submit — roll back to the settled
                # state instead of stranding the agent "running" with no
                # future (the FOLLOW-UP stays in history as a record)
                with agent.mutex:
                    agent.state = prev_state
                    agent.error = prev_error
                raise CrewError(
                    "crew is shut down — follow-up not sent")
        return agent

    def wait(self, ids: list[str] | None = None,
             timeout: float = 30.0,
             should_cancel: "Callable[[], bool] | None" = None) -> dict[str, str]:
        """Block until the named subagents (default: all) leave the
        running state, or the timeout lands. Returns {id: state}.

        As-completed style: wakes the moment ANY awaited future
        finishes instead of polling blindly, but also notices
        follow-up re-submits (send() swaps in a fresh future).

        should_cancel: optional callback — if it returns True, wait()
        returns immediately and force-stops all running agents."""
        targets = [self._require(i) for i in ids] if ids else self.list()
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if should_cancel is not None and should_cancel():
                # FORCE STOP — user pressed Esc/Ctrl+C
                self.force_stop()
                break
            if all(a.state != "running" for a in targets):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with self._lock:
                futs = [self._futures.get(a.id) for a in targets
                        if a.state == "running"]
            futs = [f for f in futs if f is not None and not f.done()]
            if futs:
                # wake on the first completion, or on a short slice so
                # a re-submit (new future) is never missed
                concurrent.futures.wait(
                    futs, timeout=min(remaining, WAIT_SLICE_SECONDS),
                    return_when=concurrent.futures.FIRST_COMPLETED)
            else:
                # running but future not registered yet (spawn/send
                # window) — brief sleep, then re-check state
                time.sleep(min(WAIT_POLL_SECONDS, remaining))
        self._prune_futures()
        return {a.id: a.state for a in targets}

    def poll(self) -> dict[str, dict]:
        """Non-blocking status snapshot of every agent — the shape the
        TUI renders each frame without flicker::

            {agent_id: {"state": ..., "finished": bool,
                        "elapsed_ms": int, "tool_calls": int,
                        "summary": str}}
        """
        self._prune_futures()
        out: dict[str, dict] = {}
        for a in self.list():
            with self._lock:
                fut = self._futures.get(a.id)
            out[a.id] = {"state": a.state,
                         "finished": bool(fut.done()) if fut is not None
                         else a.state != "running",
                         "elapsed_ms": a.elapsed_ms,
                         "tool_calls": a.tool_calls,
                         "summary": a.summary[:200]}
        return out

    def force_stop(self) -> None:
        """Forcefully stop ALL running agents. Called on Esc/Ctrl+C.
        Sets the stop flag so in-flight loops bail at the next step;
        futures that never started are cancelled outright."""
        with self._lock:
            agents = list(self._agents.values())
            futs = dict(self._futures)
        stopped = 0
        for agent in agents:
            with agent.mutex:
                if agent.state == "running":
                    agent.state = "closed"
                    agent.error = "force-stopped by user"
                    agent.stop_event.set()
                    agent.finished_at = time.time()
                    stopped += 1
            fut = futs.get(agent.id)
            if fut is not None and not fut.done():
                fut.cancel()  # no-op if already running; harmless
        if stopped:
            self.log.append("crew.force_stop",
                            {"reason": "user_interrupt",
                             "stopped": stopped},
                            actor="sovereign")

    def close(self, agent_id: str) -> CrewAgent:
        """Retire a subagent. A running agent is aborted promptly (its
        in-flight loop bails at the next step). It keeps its history
        (resume() can bring it back) but refuses sends while closed."""
        agent = self._require(agent_id)
        with agent.mutex:
            if agent.state == "closed":
                return agent
            prev = agent.state
            agent.state = "closed"
            if prev == "running":
                agent.stop_event.set()
                agent.finished_at = time.time()
        with self._lock:
            fut = self._futures.get(agent_id)
        if fut is not None and not fut.done():
            fut.cancel()
        self.log.append("crew.closed",
                        {"id": agent_id, "prev_state": prev},
                        actor="sovereign")
        return agent

    def resume(self, agent_id: str) -> CrewAgent:
        """Bring a closed subagent back (state 'done', full context),
        so it can receive follow-ups again."""
        agent = self._require(agent_id)
        with agent.mutex:
            if agent.state != "closed":
                return agent
            agent.state = "done" if not agent.error else "error"
            agent.stop_event.clear()
        self.log.append("crew.resumed", {"id": agent_id},
                        actor="sovereign")
        return agent

    def shutdown(self, wait: bool = True,
                 cancel_futures: bool = False) -> None:
        """Retire the crew: stop accepting work and tear down the pool.
        Call on host teardown so pool threads don't linger."""
        with self._lock:
            self._shutdown = True
        self._pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    # -- queries ----------------------------------------------------------------

    def get(self, agent_id: str) -> CrewAgent | None:
        return self._agents.get(agent_id)

    def list(self) -> list[CrewAgent]:
        with self._lock:
            return [self._agents[i] for i in self._order]

    def running(self) -> list[CrewAgent]:
        return [a for a in self.list() if a.state == "running"]

    def _require(self, agent_id: str) -> CrewAgent:
        agent = self._agents.get(agent_id)
        if agent is None:
            with self._lock:
                known = ", ".join(self._order) or "none"
            raise CrewError(f"unknown subagent {agent_id!r} (known: {known})")
        return agent

    def status(self) -> dict:
        agents = self.list()
        return {"total": len(agents),
                "running": sum(1 for a in agents if a.state == "running"),
                "done": sum(1 for a in agents if a.state == "done"),
                "blocked": sum(1 for a in agents if a.state == "blocked"),
                "error": sum(1 for a in agents if a.state == "error"),
                "closed": sum(1 for a in agents if a.state == "closed"),
                "tool_calls": sum(a.tool_calls for a in agents),
                "tokens_in": sum(a.tokens_in for a in agents),
                "tokens_out": sum(a.tokens_out for a in agents)}

    def format(self, agents: list[CrewAgent] | None = None) -> str:
        """Compact multi-line report — the shape handed back to the LLM."""
        agents = agents if agents is not None else self.list()
        if not agents:
            return "crew is empty — spawn a subagent first"
        lines = []
        for a in agents:
            icon = {"done": "✓", "blocked": "◐", "error": "✗",
                    "closed": "⊘", "running": "…"}.get(a.state, "?")
            model_tag = (f" · {a.model_id}" if a.model_id
                         and a.model_id != self.model.id else "")
            head = (f"{a.icon} [{a.id}] {a.nickname} ({a.role}) {icon} "
                    f"{a.state} · {a.tool_calls} tools{model_tag} · "
                    f"{a.elapsed_ms}ms")
            lines.append(head)
            lines.append(f"  task: {a.task[:200]}")
            if a.files_touched:
                lines.append("  files: " + ", ".join(a.files_touched[:8]))
            if a.error:
                lines.append(f"  error: {a.error[:200]}")
            if a.summary:
                lines.append("  " + a.summary.replace("\n", "\n  ")[:1200])
        return "\n".join(lines)

    def format_status(self) -> str:
        s = self.status()
        lines = [f"CREW — {s['total']} subagent(s): "
                 f"{s['running']} running · {s['done']} done · "
                 f"{s['error']} error · {s['closed']} closed"]
        for a in self.list():
            lines.append(f"  {a.icon} [{a.id}] {a.nickname} ({a.role}) — "
                         f"{a.state}: {a.task[:70]}")
        return "\n".join(lines)

    # -- the worker loop ----------------------------------------------------------

    def _run_loop(self, agent: CrewAgent, read_only: bool,
                  max_steps: int, gen: int) -> None:
        """One subagent's bounded tool loop. Never raises: every failure
        lands in the agent's report and is sealed as crew.done. Runs on
        a pool thread, concurrently with the other agents."""
        if agent.state == "closed" or agent.stop_event.is_set():
            return
        spec = ROLES[agent.role]
        tools = dict(self._toolsets[agent.role])
        if read_only:
            tools = {n: t for n, t in tools.items()
                     if n not in ("write_file", "edit_file",
                                  "create_directory", "run_command")}
        # per-agent model override resolves its own provider (schemas must
        # follow the model that will actually serve this agent, not the
        # crew default — tool support differs between models)
        model = (model_by_id(agent.model_id) if agent.model_id
                 else None) or self.model
        provider = PROVIDERS.get(model.provider, self.provider)
        schemas = ([t.openai_schema() for t in tools.values()]
                   if model.supports_tools else None)
        # lightweight progress event — the TUI renders one row per
        # agent from these without re-rendering the world
        self.log.append("crew.progress",
                        {"id": agent.id, "phase": "started",
                         "nickname": agent.nickname, "role": agent.role},
                        actor=f"crew:{agent.id}")
        result = None
        try:
            for step in range(max_steps):
                if agent.stop_event.is_set():
                    break
                # CANCEL: thread the stop flag into the blocking model
                # call so Esc interrupts a hung provider in ~0.25s instead
                # of waiting out the full timeout (or the rate-limit
                # backoff). Custom chat callables without the kwarg keep
                # the old call shape — the stop check above still applies.
                if self._chat_takes_cancel:
                    result = self._chat(provider, model, self.effort,
                                        agent.messages, schemas, 120.0,
                                        should_cancel=(
                                            agent.stop_event.is_set))
                else:
                    result = self._chat(provider, model, self.effort,
                                        agent.messages, schemas, 120.0)
                if result.usage:
                    agent.tokens_in += int(
                        result.usage.get("prompt_tokens", 0) or 0)
                    agent.tokens_out += int(
                        result.usage.get("completion_tokens", 0) or 0)
                if not result.tool_calls:
                    break
                from .client import assistant_message
                agent.messages.append(assistant_message(
                    result.content, result.tool_calls,
                    getattr(result, "reasoning", "") or ""))
                tool_names = []
                for tc in result.tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "")
                    args = parse_tool_arguments(fn.get("arguments"))
                    agent.tool_calls += 1
                    tool_names.append(name)
                    tool = tools.get(name)
                    if tool is None:
                        out = (f"ERROR: tool '{name}' is not available to "
                               f"a {agent.role} subagent. Available: "
                               + ", ".join(tools))
                    else:
                        # I7 — writes serialise across ALL workers, crew
                        # and team alike, even though agents run in
                        # parallel. Reads stay fully concurrent.
                        lock = _WRITE_LOCK if spec["writes"] and name in (
                            "write_file", "edit_file", "create_directory",
                            "run_command") else None
                        try:
                            if lock:
                                with lock:
                                    out = tool.handler(**args)
                            else:
                                out = tool.handler(**args)
                            if name in ("write_file", "edit_file") and \
                                    out.startswith("OK"):
                                p = str(args.get("path", ""))
                                if p and p not in agent.files_touched:
                                    agent.files_touched.append(p)
                        except Exception as e:  # noqa: BLE001
                            out = f"ERROR: {type(e).__name__}: {e}"
                    agent.messages.append(
                        {"role": "tool", "tool_call_id": tc.get("id", ""),
                         "content": out[:6000]})
                if step % 2 == 0:
                    self.log.append("crew.progress",
                                    {"id": agent.id, "phase": "step",
                                     "step": step + 1,
                                     "tools": tool_names[:6]},
                                    actor=f"crew:{agent.id}")
            final = (result.content if result is not None else "") or ""
        except BaseException as e:  # noqa: BLE001 — a failing agent never kills the crew
            # BaseException, not just Exception: anything escaping the loop
            # must land as a visible error report, never an unretrieved
            # Future exception with the subagent dying invisibly.
            final = None
            loop_error: BaseException | None = e
        else:
            loop_error = None
        # The whole landing sequence runs under the agent mutex: a
        # sovereign send()/close()/resume() in this exact window could
        # otherwise resurrect a closed agent, clobber "closed" back to
        # "done", or park a follow-up in pending_messages that was then
        # never delivered. The re-submit (if any) happens AFTER the
        # mutex is released, keeping lock ordering flat.
        resubmit = False
        submit_gen = 0
        with agent.mutex:
            if gen != agent.generation:
                # Superseded: send() submitted a newer iteration while
                # this landing was queued on the mutex. That iteration
                # owns the state now — finalising here would clobber its
                # "running" back to "done" and seal a duplicate crew.done
                # while the follow-up still runs invisibly. Our loop's
                # messages stay in history; only the verdict is skipped.
                return
            if agent.state == "closed":
                # retired mid-loop — keep the closed state, never resurrect
                agent.pending_messages.clear()
            elif agent.state != "running":
                # Settled by a racing sovereign op (e.g. close() followed
                # by resume()) after our loop bailed — don't clobber its
                # verdict with a second crew.done.
                pass
            else:
                if loop_error is not None:
                    agent.state = "error"
                    agent.error = (f"{type(loop_error).__name__}: "
                                   f"{loop_error}")
                else:
                    state, summary = parse_worker_final(final or "")
                    agent.summary = summary[:1800]
                    agent.state = (state if state in ("done", "blocked")
                                   else "done")
                    if not (final or "").strip():
                        agent.error = "subagent returned an empty reply"
                        agent.state = "error"
                agent.finished_at = time.time()
                # deliver queued follow-ups, if any arrived mid-loop
                if agent.pending_messages and not agent.stop_event.is_set():
                    queued = agent.pending_messages.pop(0)
                    agent.messages.append({"role": "user",
                                           "content": f"FOLLOW-UP: {queued}"})
                    agent.state = "running"
                    agent.error = ""
                    agent.finished_at = 0.0
                    agent.generation += 1
                    submit_gen = agent.generation
                    resubmit = True
                else:
                    agent.pending_messages.clear()
                    self.log.append("crew.done", agent.to_dict(),
                                    actor=f"crew:{agent.id}")
        if resubmit:
            if not self._submit(agent, read_only, MAX_SEND_STEPS,
                                submit_gen):
                # shutdown() raced the re-submit — settle as an error
                # instead of stranding the agent "running" with no future
                with agent.mutex:
                    agent.state = "error"
                    agent.error = "crew shut down with a follow-up pending"
                    agent.finished_at = time.time()
                self.log.append("crew.done", agent.to_dict(),
                                actor=f"crew:{agent.id}")


# ---------------------------------------------------------------------------
# Self-test — a stub chat drives the full lifecycle deterministically,
# including a PROOF that agents genuinely overlap in time (parallel,
# not serial).
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    def _self_test() -> None:
        with tempfile.TemporaryDirectory() as td:
            log = EventLog(Path(td) / "crew-test.jsonl")
            provider = SimpleNamespace(key="t", name="T",
                                       base_url="http://t", api_key="sk-fake",
                                       color="#fff")
            model = SimpleNamespace(id="stub", provider="t", label="Stub",
                                    supports_tools=True, supports_reasoning=False)
            effort = SimpleNamespace(key="low", label="LOW", color="#fff",
                                     max_tokens=100, temperature=0.0,
                                     reasoning_effort=None)

            # --- parallel proof: 3 agents, each "works" 0.4s -------------
            timeline: list[tuple[str, str, float]] = []
            tlock = threading.Lock()
            WORK_S = 0.4

            def stub_chat(provider_, model_, effort_, messages, schemas,
                          timeout):
                last_user = next((m["content"] for m in reversed(messages)
                                  if m.get("role") == "user"), "")
                key = last_user.split("::")[0].strip() if "::" in last_user \
                    else last_user[-20:]
                key = key.replace("YOUR TASK:", "").strip()
                with tlock:
                    timeline.append((key, "start", time.monotonic()))
                time.sleep(WORK_S)  # simulated model latency
                with tlock:
                    timeline.append((key, "end", time.monotonic()))
                return SimpleNamespace(
                    content=f"STATUS: DONE\nSUMMARY: did {key}",
                    reasoning="", tool_calls=[], finish_reason="stop",
                    usage={"prompt_tokens": 10, "completion_tokens": 5})

            crew = Crew(log, provider, model, effort, chat=stub_chat,
                        max_agents=10)

            # spawn_parallel returns immediately; all three run at once
            t0 = time.monotonic()
            agents = crew.spawn_parallel(
                [{"task": f"job-{i} :: payload-{i}", "role": "coder"}
                 for i in range(3)])
            assert [a.id for a in agents] == ["crew-1", "crew-2", "crew-3"]
            assert all(a.state == "running" for a in agents)
            # poll() is non-blocking and sees them in flight
            snap = crew.poll()
            assert set(snap) == {"crew-1", "crew-2", "crew-3"}
            assert all(v["state"] == "running" for v in snap.values()), snap
            states = crew.wait(timeout=15.0)
            wall = time.monotonic() - t0
            assert all(s == "done" for s in states.values()), states
            for i, a in enumerate(agents):
                assert f"did job-{i}" in a.summary, a.summary
            # PARALLELISM PROOF: 3 x 0.4s serial would take >= 1.2s
            assert wall < 1.0, f"wall={wall:.2f}s — agents ran serially!"
            starts = [t for k, e, t in timeline if e == "start"]
            ends = [t for k, e, t in timeline if e == "end"]
            assert len(starts) == 3 and len(ends) == 3, timeline
            # all three windows overlap: the latest start precedes the
            # earliest end
            assert max(starts) < min(ends), \
                f"no overlap: starts={starts}, ends={ends}"

            # --- follow-up reuses the full conversation ------------------
            crew.send(agents[0].id, "now add error handling")
            crew.wait([agents[0].id], timeout=15.0)
            assert agents[0].state == "done"
            users = [m for m in agents[0].messages
                     if m.get("role") == "user"]
            assert len(users) == 2  # task + follow-up, history preserved

            # --- one crashing agent must not kill the others -------------
            def flaky_chat(provider_, model_, effort_, messages, schemas,
                           timeout):
                last_user = next((m["content"] for m in reversed(messages)
                                  if m.get("role") == "user"), "")
                if "CRASHME" in last_user:
                    raise RuntimeError("boom")
                return SimpleNamespace(content="STATUS: DONE\nSUMMARY: ok",
                                       reasoning="", tool_calls=[],
                                       finish_reason="stop", usage=None)
            crew2 = Crew(log, provider, model, effort, chat=flaky_chat,
                         max_agents=10)
            ok1 = crew2.spawn("steady work :: x", role="coder")
            bad = crew2.spawn("CRASHME :: y", role="coder")
            ok2 = crew2.spawn("more steady work :: z", role="coder")
            st = crew2.wait(timeout=15.0)
            assert st[bad.id] == "error" and "boom" in bad.error, st
            assert st[ok1.id] == "done" and st[ok2.id] == "done", st
            crew2.shutdown()

            # --- capacity is enforced, atomically ------------------------
            gate = threading.Event()

            def gated_chat(provider_, model_, effort_, messages, schemas,
                           timeout):
                gate.wait(timeout=10.0)
                return SimpleNamespace(content="STATUS: DONE\nSUMMARY: gated",
                                       reasoning="", tool_calls=[],
                                       finish_reason="stop", usage=None)
            crew3 = Crew(log, provider, model, effort, chat=gated_chat,
                         max_agents=2)
            g1 = crew3.spawn("gated one :: a", role="coder")
            g2 = crew3.spawn("gated two :: b", role="coder")
            try:
                crew3.spawn("gated three :: c", role="coder")
                raise AssertionError("over-capacity spawn must fail")
            except CrewError:
                pass
            # spawn_parallel is atomic: nothing spawns on overflow
            try:
                crew3.spawn_parallel(["x :: 1", "y :: 2"])
                raise AssertionError("over-capacity batch must fail")
            except CrewError:
                pass
            assert crew3.status()["total"] == 2
            gate.set()
            assert crew3.wait(timeout=15.0)[g1.id] == "done"
            crew3.shutdown()

            # --- close refuses sends; resume reopens ---------------------
            crew.close(agents[1].id)
            assert agents[1].state == "closed"
            try:
                crew.send(agents[1].id, "hi")
                raise AssertionError("send to closed agent must fail")
            except CrewError:
                pass
            crew.resume(agents[1].id)
            assert agents[1].state == "done"

            # --- unknown ids raise with the roster listed ----------------
            try:
                crew.wait(["crew-99"])
                raise AssertionError("unknown id must raise")
            except CrewError as e:
                assert "crew-1" in str(e)

            # --- lifecycle events are sealed in the log ------------------
            types = [e.type for e in log.events()]
            assert types.count("crew.spawn") >= 3
            assert types.count("crew.message") == 1
            assert types.count("crew.done") >= 4
            assert "crew.closed" in types and "crew.resumed" in types
            assert any(e.type == "crew.progress" and
                       e.data.get("phase") == "started" for e in log.events())

            # --- report renders ------------------------------------------
            rep = crew.format()
            assert "crew-1" in rep and "coder" in rep
            assert "CREW" in crew.format_status()

            crew.shutdown()
            print("CREW SELF-TEST PASS")

    _self_test()
