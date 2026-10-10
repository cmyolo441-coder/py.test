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
import json
import threading
import time
from typing import Callable
from ._foundation import get_logger

_log = get_logger("crew")
from dataclasses import dataclass, field

from . import systemprompt
from .config import PROVIDERS, model_by_id, DEFAULT_MODEL_ID
from .kernel import EventLog, fold
from .team import (ROLES, DEFAULT_ROLE, MAX_WORKER_STEPS,
                   _WRITE_LOCK, chat_with_retry, parse_worker_final)
from .tools import Tool, build_registry, parse_tool_arguments
# Type guards for every provider/data boundary (deep-reliability audit):
# custom chat callables and provider results are untrusted — a usage
# string, a tool_calls list containing strings, or a non-str content
# must never crash the agent loop with AttributeError.
from .typeguard import ensure_dict, ensure_int, ensure_list, ensure_str
from . import typeguards as _typeguards  # central provider-boundary guards

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


# -- defensive serialization helpers -----------------------------------------
# CrewAgent.to_dict() runs inside exception handlers (crew.done logging).
# If to_dict itself raises on a malformed field, it masks the real error
# and breaks error reporting. Every helper below coerces and NEVER raises.


def _safe_str(value, limit: int = 0) -> str:
    """Best-effort str coercion with an optional length cap. Never raises."""
    try:
        if value is None:
            return ""
        if isinstance(value, str):
            s = value
        elif isinstance(value, (bytes, bytearray)):
            s = value.decode("utf-8", "replace")
        else:
            s = str(value)
    except Exception:
        return ""
    if limit and len(s) > limit:
        s = s[:limit]
    return s


def _safe_int(value) -> int:
    """Best-effort int coercion ("10" -> 10). Never raises."""
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            return int(float(value.strip()))
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_files(value) -> list:
    """Coerce files_touched to a list of short strings. Never raises."""
    try:
        if value is None:
            return []
        if isinstance(value, (str, bytes)):
            items = [value]
        else:
            items = list(value)  # TypeError for non-iterables -> below
    except TypeError:
        items = [value]
    except Exception:
        return []
    out = []
    for item in items[:12]:
        s = _safe_str(item, 200)
        if s:
            out.append(s)
    return out


def _safe_len(value) -> int:
    """Best-effort len() for a container that may be junk. Never raises."""
    try:
        return max(0, int(len(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


_tc_id_counter = itertools.count(1)


def _normalize_tool_call(tc):
    """Canonicalize ANY value into an OpenAI-style tool_call dict.

    isinstance guards only — never raises. Returns
    ``{"id": str, "type": "function",
      "function": {"name": str, "arguments": str}}``
    or None when the value is unrecoverable (not a dict at all, or no
    usable function name). String / None / int entries and partial
    dicts — the shapes that used to crash the loop as
    ``AttributeError: 'str' object has no attribute 'get'`` — are
    skipped by the caller via the None return.

    (client.normalize_tool_call no longer exists in client.py; the
    crew keeps this local copy so the _run_loop sanitize step keeps
    working. Idempotent: feeding its own output back returns an
    equal dict.)
    """
    if not isinstance(tc, dict):
        return None
    fn = tc.get("function")
    if isinstance(fn, str):
        # Some providers serialise the whole function object as a JSON
        # string. Parse it; a non-JSON string is the tool name itself.
        try:
            parsed = json.loads(fn)
            fn = parsed if isinstance(parsed, dict) else {
                "name": fn, "arguments": "{}"}
        except (ValueError, TypeError):
            fn = {"name": fn, "arguments": "{}"}
    if not isinstance(fn, dict):
        return None
    name = fn.get("name")
    if not isinstance(name, str) or not name:
        return None
    args = fn.get("arguments", "")
    if isinstance(args, str):
        arguments = args
    elif isinstance(args, (dict, list)):
        try:
            arguments = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            arguments = str(args)
    else:
        arguments = "" if args is None else str(args)
    tc_id = tc.get("id")
    if not isinstance(tc_id, str) or not tc_id:
        tc_id = f"call_auto_{next(_tc_id_counter)}"
    return {"id": tc_id, "type": "function",
            "function": {"name": name, "arguments": arguments}}


def _usage_pair(usage) -> tuple:
    """(prompt_tokens, completion_tokens) from ANY usage shape.

    Providers are inconsistent: usage may be a dict, None, a raw
    string, or a malformed dict. Never raises; returns (0, 0) for
    anything unusable. (Replaces the client.usage_tokens /
    safe_token_counts imports that no longer exist in client.py.)"""
    try:
        if isinstance(usage, dict):
            return (max(0, int(usage.get("prompt_tokens") or 0)),
                    max(0, int(usage.get("completion_tokens") or 0)))
    except (TypeError, ValueError, OverflowError):
        pass
    return (0, 0)


def _safe_elapsed_ms(agent) -> int:
    """elapsed_ms that survives malformed finished_at/spawned_at. Never raises."""
    try:
        end = agent.finished_at or time.time()
        return max(0, int((float(end) - float(agent.spawned_at)) * 1000))
    except (TypeError, ValueError, OverflowError):
        return 0


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
    traceback: str = ""  # full traceback for debugging (see /agents errors)
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
    # repr=False / compare=False like stop_event above: a lock's repr is
    # noise in logs and lock-identity must never decide agent equality.
    mutex: threading.RLock = field(default_factory=threading.RLock,
                                   repr=False, compare=False)

    @property
    def icon(self) -> str:
        """Role icon for status lines. NEVER raises: role may be any
        junk (non-string, or an unhashable type like a list — dict.get
        would raise TypeError on those), so the lookup is guarded and
        falls back to the generic ◆."""
        try:
            return _ROLE_ICON.get(self.role, "◆")
        except TypeError:
            # unhashable role (list/dict/set) — no icon, no crash
            return "◆"

    @property
    def elapsed_ms(self) -> int:
        return _safe_elapsed_ms(self)

    def to_dict(self) -> dict:
        """Serialize for crew.done logging. NEVER raises: this runs in
        exception handlers, so every field is coerced defensively — a
        crash here would mask the real error being reported.

        Defensive: every value is coerced to its contract type. A
        summary/error that is None or a non-string (an exception
        object, a leftover from a botched assignment) would otherwise
        poison the sealed crew.done event — ``self.summary[:600]``
        raises TypeError on None, and a non-str error breaks every
        consumer that calls ``data["error"]``-style string ops.
        files_touched must be a list of strings. The traceback is
        capped (it can be 100k+ chars of junk) and messages are
        reduced to a count — the full history is too large for the
        sealed event and may contain non-dict entries."""
        try:
            return {"id": _safe_str(self.id),
                    "nickname": _safe_str(self.nickname),
                    "role": _safe_str(self.role),
                    "task": _safe_str(self.task, 2000),
                    "state": _safe_str(self.state),
                    "model": _safe_str(self.model_id),
                    "summary": _safe_str(self.summary, 600),
                    "error": _safe_str(self.error),
                    "traceback": _safe_str(self.traceback, 4000),
                    "files_touched": _safe_files(self.files_touched),
                    "tool_calls": _safe_int(self.tool_calls),
                    "tokens_in": _safe_int(self.tokens_in),
                    "tokens_out": _safe_int(self.tokens_out),
                    "message_count": _safe_len(self.messages),
                    "elapsed_ms": _safe_elapsed_ms(self)}
        except Exception:
            # absolute last resort — a minimal dict rather than
            # propagating and masking the caller's real exception
            try:
                return {"id": _safe_str(getattr(self, "id", "")),
                        "nickname": "", "role": "", "task": "",
                        "state": "error", "model": "",
                        "summary": "", "error": "to_dict failed",
                        "traceback": "", "files_touched": [],
                        "tool_calls": 0, "tokens_in": 0, "tokens_out": 0,
                        "message_count": 0, "elapsed_ms": 0}
            except Exception:
                return {"id": "", "state": "error"}


class CrewError(RuntimeError):
    """Raised for invalid lifecycle operations (unknown id, spawn at
    capacity, send to a closed agent)."""


def _validate_spawn_args(task, role, name, context, read_only,
                         model_id) -> dict:
    """Validate + normalise spawn()/spawn_parallel() arguments.

    Raises CrewError with a clear message on wrong-typed input instead
    of letting a bad value sail through and explode later as a
    confusing AttributeError/TypeError on a pool thread. Rejected
    examples: a dict task silently becoming the task text "{'a': 1}",
    a list role crashing `role not in ROLES` with "unhashable type",
    a dict name becoming the nickname "{'n': 1}".

    None means "absent" for the optional fields (name/context/
    model_id); read_only is coerced with bool(). Unknown *string*
    roles still fall back to DEFAULT_ROLE — that is the documented
    behaviour custom agent types rely on (see agenttypes.py); only
    non-string roles are rejected.
    """
    if task is None:
        raise CrewError("cannot spawn a subagent without a task")
    if not isinstance(task, str):
        raise CrewError(
            f"spawn task must be a string, got {type(task).__name__}")
    task = task.strip()
    if not task:
        raise CrewError("cannot spawn a subagent without a task")
    if not isinstance(role, str):
        raise CrewError(
            f"spawn role must be a string, got {type(role).__name__}")
    if role not in ROLES:
        role = DEFAULT_ROLE
    if name is None:
        name = ""
    if not isinstance(name, str):
        raise CrewError(
            f"spawn name must be a string, got {type(name).__name__}")
    if context is None:
        context = ""
    if not isinstance(context, str):
        raise CrewError(
            f"spawn context must be a string, got {type(context).__name__}")
    if model_id is None:
        model_id = ""
    if not isinstance(model_id, str):
        raise CrewError(
            f"spawn model_id must be a string, got "
            f"{type(model_id).__name__}")
    return {"task": task, "role": role, "name": name.strip(),
            "context": context, "read_only": bool(read_only),
            "model_id": model_id.strip()}


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
        # Spawn-path type guards (fail fast HERE, not as a bare
        # AttributeError inside spawn() or on a pool thread):
        #   * mastermind must be None or expose gate.dispatch() — a str
        #     or other junk here used to die as
        #     AttributeError: 'str' object has no attribute 'gate'.
        #   * log must accept .append() — spawn() seals crew.spawn
        #     before the pool ever runs.
        #   * the chat override must be callable — a non-callable used
        #     to surface as TypeError deep inside the worker loop.
        if mastermind is not None:
            _gate = getattr(mastermind, "gate", None)
            if _gate is None or not callable(
                    getattr(_gate, "dispatch", None)):
                raise CrewError(
                    "Crew mastermind must be None or expose "
                    "gate.dispatch(), got "
                    f"{type(mastermind).__name__}")
        if not callable(getattr(log, "append", None)):
            raise CrewError(
                "Crew log must expose append(), got "
                f"{type(log).__name__}")
        self.mastermind = mastermind
        self.max_agents = max(1, int(max_agents))
        self._chat = chat or chat_with_retry
        if not callable(self._chat):
            raise CrewError(
                "Crew chat must be callable, got "
                f"{type(self._chat).__name__}")
        # CANCEL: Esc sets each agent's stop_event (via force_stop), but
        # the worker loop only checked it BETWEEN steps — a worker stuck
        # inside a blocking model call ignored it for up to `timeout`
        # seconds (and chat_with_retry's rate-limit backoff could add
        # ~254s). Detect whether the chat callable honours a
        # should_cancel kwarg; _run_loop passes the stop flag through.
        # Detection is callable-identity-cached (see
        # _chat_accepts_cancel): re-computed whenever self._chat is
        # swapped (tests monkeypatch it), and a **kwargs signature
        # counts as accepting the kwarg — passing should_cancel to a
        # callable that takes **kwargs is always safe.
        self._chat_takes_cancel = self._chat_accepts_cancel()
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

    def _chat_accepts_cancel(self) -> bool:
        """Whether self._chat can be called with should_cancel=....

        Cached on the callable's identity, so swapping self._chat
        (tests, hot-swapped providers) re-detects instead of reusing a
        stale flag. A **kwargs parameter also counts: such callables
        accept should_cancel without raising TypeError.
        """
        import inspect as _inspect
        chat = self._chat
        cached = getattr(self, "_chat_cancel_cache", None)
        if cached is not None and cached[0] is chat:
            return cached[1]
        try:
            params = _inspect.signature(chat).parameters.values()
            ok = any(p.name == "should_cancel" or
                     p.kind is _inspect.Parameter.VAR_KEYWORD
                     for p in params)
        except (TypeError, ValueError):
            ok = False
        self._chat_cancel_cache = (chat, ok)
        self._chat_takes_cancel = ok
        return ok

    # -- internal ---------------------------------------------------------------

    # Expected keys per crew event type (contract the consumers —
    # ParallelAgentsPanel, dashboard fold, evolution, report, theater,
    # notifier — read via .get()). _emit() fills any that are missing
    # with a sane default so a partially-built payload never makes a
    # consumer crash on a missing key.
    _EVENT_DEFAULTS: dict[str, dict] = {
        "crew.spawn": {"id": "", "nickname": "", "role": "", "task": "",
                       "read_only": False, "model": ""},
        "crew.progress": {"id": "", "phase": "", "nickname": "", "role": "",
                          "step": 0, "tools": []},
        "crew.done": {"id": "", "nickname": "", "role": "", "task": "",
                      "state": "", "model": "", "summary": "", "error": "",
                      "traceback": "", "files_touched": [],
                      "tool_calls": 0, "tokens_in": 0, "tokens_out": 0,
                      "message_count": 0, "elapsed_ms": 0},
        "crew.message": {"id": "", "chars": 0, "interrupt": False},
        "crew.closed": {"id": "", "prev_state": ""},
        "crew.resumed": {"id": ""},
        "crew.force_stop": {"reason": "", "stopped": 0},
    }

    def _emit(self, type_: str, data: dict | None,
              *, actor: str = "sovereign"):
        """Seal one crew.* event after validating the payload.

        Guarantees the event data is ALWAYS a dict carrying the keys
        every consumer expects: a non-dict payload (a bug would seal a
        bare string and crash every ``ev.data.get(...)`` reader with
        ``AttributeError: 'str' object has no attribute 'get'``) is
        wrapped as {"_raw": ...}, and missing contract keys are filled
        from _EVENT_DEFAULTS. This is the only path crew.py uses to
        seal crew.* events.
        """
        if not isinstance(data, dict):
            data = {"_raw": data}
        defaults = self._EVENT_DEFAULTS.get(type_)
        if defaults:
            for key, default in defaults.items():
                data.setdefault(key, default)
        # Value-shape hardening for the fields consumers iterate or
        # join: a non-list "tools"/"files_touched" (or a non-string
        # item inside) would blow up ", ".join / len() at render time.
        tools = data.get("tools")
        if tools is not None:
            data["tools"] = ([str(t) for t in tools]
                             if isinstance(tools, (list, tuple)) else [])
        files = data.get("files_touched")
        if files is not None:
            data["files_touched"] = ([str(p) for p in files]
                                     if isinstance(files, (list, tuple))
                                     else [])
        return self.log.append(type_, data, actor=actor)

    def _submit(self, agent: CrewAgent, read_only: bool,
                max_steps: int, gen: int) -> bool:
        """Submit one loop iteration to the pool (thread-safe).

        Returns False when the crew is shut down — the caller must then
        settle the agent itself; it must never be left "running" with no
        future behind it (wait() would hang to timeout on a ghost)."""
        if not isinstance(agent, CrewAgent):
            # Fail fast on the calling thread: a non-agent here would
            # otherwise explode on a pool thread as
            # AttributeError: 'str' object has no attribute 'id'.
            raise CrewError(
                "crew._submit requires a CrewAgent, got "
                f"{type(agent).__name__}")
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
        if not isinstance(agent, CrewAgent):
            # No agent to attach an error report to, and this entry
            # point must never raise (it runs on a pool thread) — seal
            # it in the log instead of dying as
            # AttributeError: 'str' object has no attribute 'mutex'.
            _log.error("crew._execute called with %s, not a CrewAgent — "
                       "dropping the iteration", type(agent).__name__)
            return
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
                import traceback as _tb
                _tb_str = "".join(_tb.format_exception(type(e), e, e.__traceback__))
                with agent.mutex:
                    if gen != agent.generation:
                        # a newer iteration owns the state now
                        return
                    agent.state = "error"
                    # Exception class + message + failing-operation
                    # context: a bare "AttributeError: ..." tells the
                    # user WHAT but not WHERE in the crew pipeline.
                    agent.error = (f"{type(e).__name__}: {e} "
                                   "(unexpected failure outside the "
                                   "subagent turn loop)")
                    # Store full traceback for /agents errors debugging
                    agent.traceback = _tb_str[-4000:]
                    agent.finished_at = time.time()
                self._emit("crew.done", agent.to_dict(),
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
        running) — fail fast, never silently queue. Also raises
        CrewError on wrong-typed arguments (non-string task/role/name/
        context/model_id) instead of coercing them into garbage or
        crashing later on a pool thread."""
        args = _validate_spawn_args(task, role, name, context,
                                    read_only, model_id)
        task, role = args["task"], args["role"]
        name, context = args["name"], args["context"]
        read_only, model_id = args["read_only"], args["model_id"]
        # Build the opening conversation BEFORE touching the roster.
        # mastermind.dispatch() can raise — the old order left a
        # "running" agent registered that was never started, so wait()
        # hung until timeout on a ghost.
        user = (f"Shared context:\n{context}\n\nYOUR TASK: {task}"
                if context else f"YOUR TASK: {task}")
        opening: list[dict] = []
        try:
            if self.mastermind is not None:
                dispatched = self.mastermind.gate.dispatch(
                    f"worker:{role}", opening)
                # Contract: (messages, report). A custom/mocked gate
                # returning None, a bare dict, or a non-list messages
                # payload used to escape as a raw TypeError on unpack
                # ("cannot unpack non-iterable NoneType") or as
                # AttributeError on opening.append — both far from the
                # real problem. Validate the shape and name it.
                if (not isinstance(dispatched, (list, tuple))
                        or len(dispatched) != 2):
                    raise CrewError(
                        "mastermind.gate.dispatch() must return "
                        "(messages, report), got "
                        f"{type(dispatched).__name__}")
                opening = dispatched[0]
                if (not isinstance(opening, list)
                        or not all(isinstance(m, dict)
                                   for m in opening)):
                    raise CrewError(
                        "mastermind.gate.dispatch() returned malformed "
                        "messages — expected a list of dicts, got "
                        f"{type(opening).__name__}")
            else:
                systemprompt.with_system(
                    opening, systemprompt.worker(role, self.max_agents))
        except CrewError:
            raise
        except Exception as e:  # noqa: BLE001 — name the failure
            raise CrewError(
                "could not build the subagent's opening messages: "
                f"{type(e).__name__}: {e}") from e
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
            nickname = name or next(self._names)
            while any(a.nickname == nickname
                      for a in self._agents.values()):
                nickname = f"{nickname}-{self._counter}"
            agent = CrewAgent(id=agent_id, nickname=nickname, role=role,
                              task=task, read_only=read_only)
            agent.messages = opening
            agent.generation = 1  # first loop iteration
            override = model_by_id(model_id) if model_id else None
            if override is not None:
                agent.model_id = override.id
            self._agents[agent_id] = agent
            self._order.append(agent_id)

        self._emit("crew.spawn",
                        {"id": agent.id, "nickname": agent.nickname,
                         "role": role, "task": task[:300],
                         "read_only": bool(read_only),
                         "model": agent.model_id or self._default_model_id()},
                        actor="sovereign")
        if not self._submit(agent, read_only, MAX_WORKER_STEPS,
                            agent.generation):
            # shutdown() raced us between registration and submit — settle
            # the agent instead of leaving a "running" ghost with no future
            with agent.mutex:
                agent.state = "error"
                agent.error = "crew shut down before the subagent started"
                agent.finished_at = time.time()
            self._emit("crew.done", agent.to_dict(),
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
        # Type-confusion guard: a bare string here used to be shredded
        # into one-character "tasks" by list(tasks) — spawn_parallel("do
        # x") silently launched 4 garbage subagents. A dict would have
        # iterated its keys the same way. tasks must be a real sequence
        # of items (None/empty is still a no-op empty batch).
        if tasks is None:
            return []
        if isinstance(tasks, (str, bytes, bytearray)) or not isinstance(
                tasks, (list, tuple)):
            raise CrewError(
                "spawn_parallel tasks must be a list of task strings or "
                f"dicts, got {type(tasks).__name__} — nothing spawned")
        items = list(tasks)
        if not items:
            return []
        if not isinstance(role, str):
            raise CrewError(
                "spawn_parallel role must be a string, got "
                f"{type(role).__name__} — nothing spawned")
        if role not in ROLES:
            role = DEFAULT_ROLE
        # Normalise + validate BEFORE touching the roster: a bad item must
        # fail the whole batch, never leave a partially-spawned one behind.
        # Every item goes through the SAME validation spawn() applies, so
        # a later item can never fail mid-batch after earlier ones spawned.
        prepared: list[dict] = []
        for i, item in enumerate(items):
            if isinstance(item, str):
                item = {"task": item}
            if not isinstance(item, dict):
                raise CrewError(
                    "spawn_parallel items must be task strings or dicts, "
                    f"got {type(item).__name__} — nothing spawned")
            try:
                prepared.append(_validate_spawn_args(
                    item.get("task", ""),
                    item.get("role", role),
                    item.get("name", ""),
                    item.get("context", context),
                    item.get("read_only", read_only),
                    item.get("model_id", model_id)))
            except CrewError as e:
                raise CrewError(
                    f"spawn_parallel item {i}: {e} — nothing spawned") from e
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
                task=p["task"],
                role=p["role"],
                name=p["name"],
                context=p["context"],
                read_only=p["read_only"],
                model_id=p["model_id"])
                for p in prepared]

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
            self._emit("crew.message",
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
                # a re-run starts clean — a stale traceback from the
                # previous iteration would mislead /agents errors
                agent.traceback = ""
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
            self._emit("crew.force_stop",
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
        self._emit("crew.closed",
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
        self._emit("crew.resumed", {"id": agent_id},
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
        # Type guard: an unhashable id (list/dict) used to die inside
        # dict.get as "TypeError: unhashable type" — far from the real
        # problem (a bad caller). Name it as a CrewError instead.
        if not isinstance(agent_id, str):
            raise CrewError(
                "subagent id must be a string, got "
                f"{type(agent_id).__name__}")
        agent = self._agents.get(agent_id)
        if agent is None:
            with self._lock:
                known = ", ".join(self._order) or "none"
            raise CrewError(f"unknown subagent {agent_id!r} (known: {known})")
        return agent

    def status(self) -> dict:
        """Never raises: agent counters may be junk (str/None), so the
        sums coerce defensively instead of blowing up the TUI poll."""
        agents = self.list()
        return {"total": len(agents),
                "running": sum(1 for a in agents if a.state == "running"),
                "done": sum(1 for a in agents if a.state == "done"),
                "blocked": sum(1 for a in agents if a.state == "blocked"),
                "error": sum(1 for a in agents if a.state == "error"),
                "closed": sum(1 for a in agents if a.state == "closed"),
                "tool_calls": sum(_safe_int(a.tool_calls) for a in agents),
                "tokens_in": sum(_safe_int(a.tokens_in) for a in agents),
                "tokens_out": sum(_safe_int(a.tokens_out) for a in agents)}

    def format(self, agents: list[CrewAgent] | None = None) -> str:
        """Compact multi-line report — the shape handed back to the LLM."""
        agents = agents if agents is not None else self.list()
        if not agents:
            return "crew is empty — spawn a subagent first"
        lines = []
        for a in agents:
            try:
                # every attribute read here is defensive: malformed
                # agent fields (task/error/summary as non-strings,
                # files_touched as non-strings, unhashable state) must
                # never crash a status report
                state = _safe_str(a.state)
                icon = {"done": "✓", "blocked": "◐", "error": "✗",
                        "closed": "⊘", "running": "…"}.get(state, "?")
                model_tag = (f" · {_safe_str(a.model_id)}"
                             if a.model_id
                             and _safe_str(a.model_id) != self._default_model_id()
                             else "")
                head = (f"{a.icon} [{_safe_str(a.id)}] "
                        f"{_safe_str(a.nickname)} ({_safe_str(a.role)}) "
                        f"{icon} {state} · "
                        f"{_safe_int(a.tool_calls)} tools{model_tag} · "
                        f"{_safe_elapsed_ms(a)}ms")
                lines.append(head)
                lines.append(f"  task: {_safe_str(a.task, 200)}")
                files = _safe_files(a.files_touched)
                if files:
                    lines.append("  files: " + ", ".join(files[:8]))
                err = _safe_str(a.error)
                if err:
                    # full error text — a truncated mystery here costs the
                    # sovereign more than the context it saves (see /agents
                    # errors for the traceback companion)
                    lines.append(f"  error: {err}")
                summ = _safe_str(a.summary, 1200)
                if summ:
                    lines.append("  " + summ.replace("\n", "\n  "))
            except Exception:
                # a single poisoned agent must not kill the whole report
                lines.append(f"  [unreadable agent]")
        return "\n".join(lines)

    def format_status(self) -> str:
        s = self.status()
        lines = [f"CREW — {s['total']} subagent(s): "
                 f"{s['running']} running · {s['done']} done · "
                 f"{s['error']} error · {s['closed']} closed"]
        for a in self.list():
            try:
                lines.append(f"  {a.icon} [{_safe_str(a.id)}] "
                             f"{_safe_str(a.nickname)} "
                             f"({_safe_str(a.role)}) — "
                             f"{_safe_str(a.state)}: {_safe_str(a.task, 70)}")
            except Exception:
                lines.append("  [unreadable agent]")
        return "\n".join(lines)

    # -- the worker loop ----------------------------------------------------------
    def _default_model_id(self) -> str:
        """Display id of the crew default model. self.model may be a
        bare model-id STRING rather than a Model object (Crew accepts
        both) — never touch ``self.model.id`` directly or spawn() /
        format() die with AttributeError: 'str' object has no attribute
        'id'."""
        if isinstance(self.model, str):
            return self.model
        return _safe_str(getattr(self.model, "id", "")) or "?"

    def _resolve_model(self, agent: CrewAgent):
        """Resolve the (model, provider) pair for one worker. Never
        returns a junk pair: every bad input — a non-string model_id, an
        unknown model id, a crew default that isn't a model-like object
        (e.g. a bare model-id string), an unknown provider key — falls
        back to the crew default (or the built-in default model as a last
        resort) and logs a crew.warn so the misconfiguration is visible
        instead of crashing the worker with AttributeError ('str' object
        has no attribute 'provider' / 'supports_tools').

        The ONE case that raises: the built-in default model itself is
        unusable (a broken model registry — config.py's import asserts
        make this unreachable in production). Then a CrewError is raised
        and _run_loop settles the agent with a visible error instead of
        letting the worker die with AttributeError on a pool thread.

        Accepts duck-typed model/provider objects (the self-tests use
        SimpleNamespace stubs) — only objects that lack the attributes
        the loop needs are treated as broken."""
        def _warn(reason: str, detail: str) -> None:
            try:
                self.log.append(
                    "crew.warn",
                    {"id": agent.id, "phase": "model-resolve",
                     "reason": reason, "detail": _safe_str(detail)[:200]},
                    actor=f"crew:{agent.id}")
            except Exception:  # noqa: BLE001 — logging must never break
                pass           # the worker

        def _model_like(m) -> bool:
            return (m is not None and hasattr(m, "provider")
                    and hasattr(m, "supports_tools"))

        # 1. per-agent override
        model = None
        mid = agent.model_id
        if isinstance(mid, str) and mid:
            model = model_by_id(mid)
            if model is None:
                _warn("unknown-model-id",
                      f"model_id {mid!r} is not a known model — "
                      "falling back to the crew default model")
        elif mid:
            _warn("bad-model-id",
                  f"model_id {mid!r} is not a string — "
                  "falling back to the crew default model")
        # 2. crew default
        if model is None:
            model = self.model
        if not _model_like(model):
            # self.model isn't model-like either (e.g. Crew was built
            # with a bare model-id string, or None). Try to resolve a
            # string as an id; otherwise take the built-in default,
            # which config.py guarantees exists (assert at import).
            resolved = (model_by_id(model)
                        if isinstance(model, str) else None)
            if resolved is None:
                _warn("bad-crew-model",
                      f"crew default model {model!r} is not usable — "
                      f"falling back to {DEFAULT_MODEL_ID!r}")
            model = resolved or model_by_id(DEFAULT_MODEL_ID)
            if not _model_like(model):
                # Last resort — config.py asserts at import that
                # DEFAULT_MODEL_ID resolves, so this is unreachable in
                # production. Never return a junk pair: a None/broken
                # model here would AttributeError in _run_loop. Raise a
                # clean CrewError instead — _run_loop settles the agent
                # with a visible error, and the crew keeps running.
                _warn("no-model",
                      f"built-in default model {DEFAULT_MODEL_ID!r} is "
                      "not usable — the model registry is broken")
                raise CrewError(
                    f"cannot resolve a usable model for subagent "
                    f"{agent.id}: built-in default {DEFAULT_MODEL_ID!r} "
                    "is not usable — the model registry is broken")
        # 3. provider follows the model that will actually serve the
        # agent (tool schemas differ between models)
        provider = None
        pkey = getattr(model, "provider", None)
        if isinstance(pkey, str):
            try:
                provider = PROVIDERS.get(pkey)
            except Exception:  # noqa: BLE001 — unhashable key etc.
                provider = None
        if provider is None:
            provider = self.provider
        if not getattr(provider, "base_url", None):
            _warn("bad-provider",
                  f"no usable provider for model "
                  f"{getattr(model, 'id', model)!r} "
                  f"(provider key {pkey!r}) — using the first "
                  "configured provider")
            provider = next(iter(PROVIDERS.values()), None)
        if not getattr(provider, "base_url", None):
            # No usable provider at all — junk here would AttributeError
            # inside the chat callable. Clean CrewError instead; _run_loop
            # settles the agent with a visible error.
            _warn("no-provider",
                  f"no usable provider for model "
                  f"{getattr(model, 'id', model)!r}")
            raise CrewError(
                f"cannot resolve a usable provider for subagent "
                f"{agent.id}: model {getattr(model, 'id', model)!r} names "
                f"provider key {pkey!r}, which is not configured")
        return model, provider


    def _checked_chat(self, provider, model, messages, schemas,
                      timeout: float, should_cancel=None):
        """Call self._chat and VALIDATE its reply before the worker loop
        touches it.

        WHY: _run_loop reads result.usage / result.tool_calls /
        result.content unconditionally. When an injected or misbehaving
        chat callable returned None, a plain string, or a dict, the loop
        died with a cryptic AttributeError ('str' object has no
        attribute 'usage') instead of naming the real problem. This
        wrapper turns every shape violation into a CrewError that names
        the offending type — the agent lands in 'error' with a message
        that points at the chat callable, and the crew keeps running.

        Contract enforced:
          * not None, not a str/bytes, not a dict
          * exposes .content, .tool_calls and .usage (duck-typed —
            a real StreamResult or anything shaped like it; the crew's
            own self-test stubs pass as long as they carry the attrs)
          * .content is a str (or None)
          * .tool_calls is a list/tuple — None is a shape violation,
            not "no tool calls" (silently treating it as [] would end
            the turn early on corrupted data)

        Errors raised BY the chat callable itself (APIError, retry
        exhaustion, TurnCancelled, ...) are NOT touched — they
        propagate unchanged so retry/cancel semantics stay intact."""
        # Detection runs per call (identity-cached): a swapped
        # self._chat is re-detected instead of trusting a stale flag.
        if self._chat_accepts_cancel():
            result = self._chat(provider, model, self.effort,
                                messages, schemas, timeout,
                                should_cancel=should_cancel)
        else:
            result = self._chat(provider, model, self.effort,
                                messages, schemas, timeout)
        where = "provider returned unusable result — the crew's chat callable"
        if result is None:
            raise CrewError(
                f"{where} returned None — expected a StreamResult (an "
                "object with .content, .tool_calls and .usage). Check the "
                "chat callable injected into Crew(..., chat=...).")
        if isinstance(result, (str, bytes, bytearray)):
            raise CrewError(
                f"{where} returned a {type(result).__name__}, not a "
                "StreamResult — expected an object with .content, "
                ".tool_calls and .usage. Wrap the text in a StreamResult.")
        if isinstance(result, dict):
            raise CrewError(
                f"{where} returned a dict, not a StreamResult — return "
                "the StreamResult itself (e.g. StreamResult(**d)) "
                "instead of a plain mapping.")
        missing = [a for a in ("content", "tool_calls", "usage")
                   if not hasattr(result, a)]
        if missing:
            raise CrewError(
                f"{where} returned {type(result).__name__}, which is "
                f"missing StreamResult attribute(s): {', '.join(missing)}.")
        content = result.content
        if content is not None and not isinstance(content, str):
            raise CrewError(
                f"{where} returned {type(result).__name__} whose "
                f".content is {type(content).__name__} — expected str "
                "(or None).")
        tc = result.tool_calls
        if not isinstance(tc, (list, tuple)):
            raise CrewError(
                f"{where} returned {type(result).__name__} with "
                f".tool_calls of type {type(tc).__name__} — expected a "
                "list of tool-call dicts (or an empty list). None is not "
                "accepted: return [] when there are no tool calls.")
        return result

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
        # crew default — tool support differs between models).
        # _resolve_model falls every bad input back to the crew default
        # with a crew.warn logged; it raises CrewError only when the
        # model registry itself is broken (built-in default unusable or
        # no usable provider at all). That case is settled here with a
        # visible agent error — never an AttributeError on a pool thread.
        try:
            model, provider = self._resolve_model(agent)
        except CrewError as e:
            with agent.mutex:
                agent.state = "error"
                agent.error = str(e)
                agent.finished_at = time.time()
            self._emit("crew.done", agent.to_dict(),
                       actor=f"crew:{agent.id}")
            return
        schemas = ([t.openai_schema() for t in tools.values()]
                   if getattr(model, "supports_tools", True) else None)
        # lightweight progress event — the TUI renders one row per
        # agent from these without re-rendering the world
        self._emit("crew.progress",
                        {"id": agent.id, "phase": "started",
                         "nickname": agent.nickname, "role": agent.role},
                        actor=f"crew:{agent.id}")
        result = None
        try:
            for step in range(max_steps):
                if agent.stop_event.is_set():
                    break
                # _checked_chat validates the reply AND threads the stop flag
                # into the blocking model call, so Esc interrupts a hung
                # provider in ~0.25s instead of waiting out the full
                # timeout (or the rate-limit backoff). Custom chat
                # callables without the kwarg keep the old call shape —
                # the stop check above still applies.
                result = self._checked_chat(provider, model, agent.messages,
                                            schemas, 120.0,
                                            should_cancel=(
                                                agent.stop_event.is_set))
                # Provider boundary (typeguards): coerce the reply's
                # fields to their promised types once, right where the
                # provider data enters the loop — a string where a
                # list/dict was expected used to crash below with
                # AttributeError ('str' object has no attribute ...).
                _content = _typeguards.ensure_str(
                    getattr(result, "content", ""))
                # _checked_chat allows list OR tuple; ensure_list only
                # keeps lists, so coerce tuples explicitly — otherwise a
                # tuple of tool calls would be silently dropped and the
                # turn would end early on valid data.
                _raw_tool_calls = getattr(result, "tool_calls", None)
                _tool_calls = (list(_raw_tool_calls)
                               if isinstance(_raw_tool_calls, (list, tuple))
                               else [])
                # _usage_pair handles every provider shape (dict, string,
                # None, malformed) without raising.
                _tin, _tout = _usage_pair(result.usage)
                agent.tokens_in += _tin
                agent.tokens_out += _tout
                if not _tool_calls:
                    break
                from .client import assistant_message
                # Sanitize ONCE per step, before history: raw tool-call
                # entries (strings, None, partial dicts) must never reach
                # the provider inside the message history — the loop below
                # skips them, but history is sent back on the next step.
                # reasoning is coerced too: _checked_chat doesn't validate
                # its type and a non-str would corrupt the history.
                safe_calls = [c for c in (_normalize_tool_call(tc)
                                          for tc in _tool_calls)
                              if c is not None]
                if not safe_calls:
                    # No USABLE tool calls this turn: every entry was
                    # garbage that _normalize_tool_call filtered (strings,
                    # None, partial dicts). The turn is over — end it on
                    # this step's content. Without this break the loop
                    # re-asks the provider with identical history up to
                    # max_steps times (96x real latency/cost) for a reply
                    # that can never produce a tool call.
                    break
                reasoning = getattr(result, "reasoning", "")
                if not isinstance(reasoning, str):
                    reasoning = ""
                agent.messages.append(assistant_message(
                    _content, safe_calls, reasoning))
                tool_names = []
                for tc in safe_calls:
                    # Already canonical — the guard stays as a no-op
                    # (_normalize_tool_call is idempotent).
                    tc = _normalize_tool_call(tc)
                    if tc is None:
                        continue
                    fn = tc["function"]
                    name = fn["name"]
                    args = parse_tool_arguments(fn["arguments"])
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
                            # Data boundary (typeguard): a misbehaving
                            # handler may return non-text (None, dict) —
                            # out.startswith below would raise
                            # AttributeError. Coerce here, before any str
                            # method is touched (default preserves the old
                            # str(out) fallback for non-str returns).
                            out = ensure_str(out, str(out))
                            if name in ("write_file", "edit_file") and \
                                    out.startswith("OK"):
                                p = str(args.get("path", ""))
                                if p and p not in agent.files_touched:
                                    agent.files_touched.append(p)
                        except Exception as e:  # noqa: BLE001
                            out = f"ERROR: {type(e).__name__}: {e}"
                    # out is coerced to str right after the handler call
                    # above; guard the id too (a non-str tool_call_id
                    # breaks history validation on the next provider
                    # call).
                    agent.messages.append(
                        {"role": "tool",
                         "tool_call_id": ensure_str(tc.get("id")),
                         "content": out[:6000]})
                if step % 2 == 0:
                    self._emit("crew.progress",
                                    {"id": agent.id, "phase": "step",
                                     "step": step + 1,
                                     "tools": tool_names[:6]},
                                    actor=f"crew:{agent.id}")
            # Data boundary (typeguard): final must be a str for the
            # report/log path — _checked_chat enforces this, but never
            # trust a field that crossed a provider boundary twice.
            final = _content if result is not None else ""
        except BaseException as e:  # noqa: BLE001 — a failing agent never kills the crew
            # BaseException, not just Exception: anything escaping the loop
            # must land as a visible error report, never an unretrieved
            # Future exception with the subagent dying invisibly.
            final = None
            loop_error: BaseException | None = e
            # Capture the traceback NOW (inside the except block, while
            # __traceback__ is alive) so /agents errors can show it: the
            # landing below only ever set agent.error, leaving
            # agent.traceback empty for the most common error path —
            # exactly when the user needs the traceback most.
            import traceback as _tb
            loop_tb = "".join(
                _tb.format_exception(type(e), e, e.__traceback__))
        else:
            loop_error = None
            loop_tb = ""
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
                    # Exception class + message + failing-operation
                    # context (the turn loop covers provider calls, tool
                    # dispatch and worker-final parsing).
                    agent.error = (f"{type(loop_error).__name__}: "
                                   f"{loop_error} (while executing the "
                                   "subagent turn loop)")
                    # Full traceback for /agents errors debugging (the
                    # field was previously only set by the outer
                    # _execute handler, so loop errors showed none).
                    agent.traceback = loop_tb[-4000:]
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
                    # stale traceback must not survive into the new run
                    agent.traceback = ""
                    agent.finished_at = 0.0
                    agent.generation += 1
                    submit_gen = agent.generation
                    resubmit = True
                else:
                    agent.pending_messages.clear()
                    self._emit("crew.done", agent.to_dict(),
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
                self._emit("crew.done", agent.to_dict(),
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
