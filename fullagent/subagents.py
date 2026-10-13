"""Bounded, auditable parallel subagents for FullAgent.

Subagents have independent conversations and genuinely execute concurrently,
but deliberately do not inherit the host agent's unrestricted tool surface:

* role tool allowlists are intersected with the live registry;
* shell, deletion, move/copy, and arbitrary patch tools are never exposed;
* delegated writers need explicit, non-overlapping workspace path scopes;
* every delegated write passes the host's snapshot callback first;
* task batches are validated and capacity-checked atomically;
* workers are cancellable, bounded, and recorded in the shared event log.

The module is provider-agnostic: it uses the currently selected FullAgent model
through an injected runtime resolver and chat callable (normally chat_with_retry).
"""
from __future__ import annotations

import concurrent.futures
import inspect
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cancelguard import TurnCancelled
from .client import (assistant_message, normalize_tool_call,
                     safe_token_counts, chat_with_retry)
from .roles import ROLES
from .tools import Tool, parse_tool_arguments

MAX_GROUP_SIZE = 8
MAX_RETAINED = 40
MAX_TASK_CHARS = 12_000
MAX_CONTEXT_CHARS = 24_000
MAX_TOOL_OUTPUT_CHARS = 12_000
MAX_CALLS_PER_STEP = 12
MAX_TOTAL_TOOL_CALLS = 80
MAX_STEPS = 20
DEFAULT_WORKERS = 4
DEFAULT_ACTIVE_LIMIT = 8
CALL_TIMEOUT = 120.0
# Max seconds reset_session()/shutdown() wait for stop()ed workers to wind
# down before giving up the drain. A cooperative worker finishes within one
# cancel poll after stop_event is set; the bound only bites when a worker
# ignores cancellation entirely (e.g. a stuck provider call), in which case
# its pool thread is abandoned and reaps itself when the call returns.
DRAIN_TIMEOUT = 5.0
ACTIVE_STATES = frozenset({"queued", "running", "cancelling"})
TERMINAL_STATES = frozenset({"done", "blocked", "error", "cancelled"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "create_directory"})
NEVER_DELEGATE = frozenset({
    "run_command", "live_shell", "live_shell_reset", "delete_path",
    "copy_path", "move_path", "apply_patch", "Task", "TaskGroup",
    "TaskWriteGroup", "TaskOutput", "TaskCollect", "TaskList", "TaskStop",
})


class SubagentError(ValueError):
    """A task cannot be scheduled without violating an execution invariant."""


@dataclass
class Subagent:
    id: str
    batch_id: str
    name: str
    role: str
    task: str
    model_id: str
    write_scope: tuple[str, ...]
    write_directories: tuple[str, ...]
    provider: Any = field(repr=False)
    model: Any = field(repr=False)
    effort: Any = field(repr=False)
    tools: dict[str, Tool] = field(repr=False)
    session_id: str = ""
    messages: list[dict] = field(repr=False, default_factory=list)
    state: str = "queued"
    summary: str = ""
    error: str = ""
    current_action: str = "queued"
    steps: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    files_touched: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0
    spawn_event_id: str = ""
    stop_event: threading.Event = field(default_factory=threading.Event,
                                        repr=False, compare=False)

    def to_dict(self) -> dict:
        """A bounded, JSON-safe public record; prompt/history never leak here."""
        return {
            "id": str(self.id), "name": str(self.name),
            "batch_id": str(self.batch_id),
            "role": str(self.role), "task": str(self.task[:2000]),
            "model": str(self.model_id), "state": str(self.state),
            "summary": str(self.summary[:4000]),
            "error": str(self.error[:2000]),
            "current_action": str(self.current_action[:200]),
            "write_scope": list(self.write_scope),
            "steps": max(0, int(self.steps)),
            "tool_calls": max(0, int(self.tool_calls)),
            "tokens_in": max(0, int(self.tokens_in)),
            "tokens_out": max(0, int(self.tokens_out)),
            "files_touched": list(self.files_touched[:32]),
            "elapsed_ms": max(0, int(((self.finished_at or time.time())
                                      - self.created_at) * 1000)),
        }


class ParallelSubagents:
    """Manage concurrent specialist agents over one FullAgent session.

    ``runtime_resolver(model_id)`` returns ``(provider, model, effort)``.
    ``before_write(worker, tool, args)`` returns ``None`` when a recovery
    snapshot is ready, otherwise an explanatory string which blocks the write.
    ``after_write(worker, tool, args)`` may return a post-write warning.
    """

    def __init__(self, log, tools: dict[str, Tool],
                 runtime_resolver: Callable[[str], tuple],
                 before_write: Callable[[Subagent, Tool, dict], str | None]
                 | None = None, *, workspace: str | Path | None = None,
                 after_write: Callable[[Subagent, Tool, dict], str] | None = None,
                 max_workers: int = DEFAULT_WORKERS,
                 max_active: int = DEFAULT_ACTIVE_LIMIT,
                 max_steps: int = MAX_STEPS,
                 chat: Callable | None = None) -> None:
        if not callable(runtime_resolver):
            raise TypeError("runtime_resolver must be callable")
        self.log = log
        self.tools = tools
        self.runtime_resolver = runtime_resolver
        self.before_write = before_write
        self.after_write = after_write
        self.workspace = Path(workspace or Path.cwd()).expanduser().resolve()
        self.max_workers = max(1, min(16, int(max_workers)))
        self.max_active = max(1, min(24, int(max_active)))
        self.max_steps = max(1, min(64, int(max_steps)))
        self.chat = chat or chat_with_retry
        self._session_id = str(getattr(log, "session", "") or "")
        self._lock = threading.RLock()
        self._workers: dict[str, Subagent] = {}
        self._order: list[str] = []
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._counter = 0
        self._batch_counter = 0
        self._latest_batch_id = ""
        self._batch_members: dict[str, list[str]] = {}
        self._closed = False
        self._transitioning = False
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="fullagent:subagent")

    # -- validation and scope isolation -------------------------------------

    @staticmethod
    def _relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _normalise_scope(self, value: Any) -> tuple[tuple[str, ...],
                                                    tuple[str, ...]]:
        if value in (None, ""):
            return (), ()
        if not isinstance(value, (list, tuple)):
            raise SubagentError("write_scope must be a list of workspace paths")
        if len(value) > 16:
            raise SubagentError("write_scope supports at most 16 paths")
        paths: list[str] = []
        dirs: list[str] = []
        for raw in value:
            if not isinstance(raw, str) or not raw.strip():
                raise SubagentError("each write_scope entry must be a path")
            raw = raw.strip()
            candidate = Path(raw).expanduser()
            if ".." in candidate.parts:
                raise SubagentError("write_scope cannot contain '..'")
            full = candidate if candidate.is_absolute() else self.workspace / candidate
            resolved = full.resolve(strict=False)
            if not self._relative_to(resolved, self.workspace):
                raise SubagentError(
                    f"write_scope path escapes the workspace: {raw!r}")
            key = str(resolved)
            if key not in paths:
                paths.append(key)
            if raw.endswith(("/", "\\")) or (resolved.exists()
                                                   and resolved.is_dir()):
                dirs.append(key)
        return tuple(paths), tuple(dirs)

    @staticmethod
    def _scope_overlap(a: Subagent, b_paths: tuple[str, ...],
                       b_dirs: tuple[str, ...]) -> bool:
        for left in a.write_scope:
            lp = Path(left)
            for right in b_paths:
                rp = Path(right)
                if lp == rp:
                    return True
                if left in a.write_directories and ParallelSubagents._relative_to(rp, lp):
                    return True
                if right in b_dirs and ParallelSubagents._relative_to(lp, rp):
                    return True
        return False

    def _validate_item(self, item: Any, default_role: str,
                       default_context: str, model_id: str,
                       *, allow_writes: bool) -> dict:
        if isinstance(item, str):
            item = {"task": item}
        if not isinstance(item, dict):
            raise SubagentError("each task must be a string or an object")
        task = item.get("task")
        if not isinstance(task, str) or not task.strip():
            raise SubagentError("every subagent needs a non-empty task")
        task = task.strip()
        if len(task) > MAX_TASK_CHARS:
            raise SubagentError(f"task exceeds {MAX_TASK_CHARS:,} characters")
        role = item.get("role", default_role)
        if not isinstance(role, str) or role not in ROLES:
            valid = ", ".join(sorted(ROLES))
            raise SubagentError(f"unknown role {role!r}; available: {valid}")
        name = item.get("name", "")
        context = item.get("context", default_context)
        requested_model = item.get("model_id", item.get("model", model_id))
        if not isinstance(name, str) or not isinstance(context, str):
            raise SubagentError("name and context must be strings")
        if len(context) > MAX_CONTEXT_CHARS:
            raise SubagentError(
                f"shared context exceeds {MAX_CONTEXT_CHARS:,} characters")
        if not isinstance(requested_model, str):
            raise SubagentError("model_id must be a string")
        paths, dirs = self._normalise_scope(item.get("write_scope", ()))
        role_writes = bool(ROLES[role].get("writes"))
        if allow_writes and role_writes and not paths:
            raise SubagentError(
                "write-enabled tasks need a non-empty write_scope")
        if paths and not allow_writes:
            raise SubagentError(
                "TaskGroup is read-only; use TaskWriteGroup for scoped edits")
        if paths and not role_writes:
            raise SubagentError(f"role {role!r} does not support file writes")
        try:
            runtime = self.runtime_resolver(requested_model.strip())
        except Exception as exc:
            raise SubagentError(
                f"cannot resolve subagent model: {type(exc).__name__}: {exc}") from exc
        if not isinstance(runtime, (tuple, list)) or len(runtime) != 3:
            raise SubagentError(
                "runtime_resolver must return (provider, model, effort)")
        provider, model, effort = runtime
        if not getattr(model, "supports_tools", False):
            raise SubagentError(
                f"model {getattr(model, 'id', model)!r} does not support tools")
        names = ROLES[role].get("tools", ())
        worker_tools: dict[str, Tool] = {}
        for tool_name in names:
            tool = self.tools.get(tool_name)
            if tool is None or tool_name in NEVER_DELEGATE:
                continue
            if tool_name in WRITE_TOOLS and (not role_writes or not paths):
                continue
            worker_tools[tool_name] = tool
        return {"task": task, "role": role, "name": name.strip()[:80],
                "context": context, "model_id": str(getattr(model, "id", "")),
                "provider": provider, "model": model, "effort": effort,
                "tools": worker_tools, "write_scope": paths,
                "write_directories": dirs}

    # -- lifecycle -----------------------------------------------------------

    def _emit(self, kind: str, data: dict, worker: Subagent | None = None):
        safe_data = dict(data) if isinstance(data, dict) else {"_raw": str(data)}
        actor = f"subagent:{worker.id}" if worker else "sovereign"
        cause = worker.spawn_event_id if worker and worker.spawn_event_id else None
        session = worker.session_id if worker else self._session_id
        try:
            event = self.log.append(kind, safe_data, actor=actor,
                                    causation_id=cause, session=session)
        except TypeError:
            # Lightweight injected test logs may only accept positional args.
            event = self.log.append(kind, safe_data)
        return event

    def _prune(self, additional: int = 1) -> None:
        while len(self._order) + additional > MAX_RETAINED:
            oldest = next((aid for aid in self._order
                           if self._workers[aid].state in TERMINAL_STATES), None)
            if oldest is None:
                raise SubagentError(
                    f"subagent history is full ({MAX_RETAINED}); wait or stop work")
            self._order.remove(oldest)
            removed = self._workers.pop(oldest, None)
            self._futures.pop(oldest, None)
            if removed is not None:
                members = self._batch_members.get(removed.batch_id, [])
                self._batch_members[removed.batch_id] = [
                    aid for aid in members if aid != oldest]
                if not self._batch_members[removed.batch_id]:
                    self._batch_members.pop(removed.batch_id, None)
                    if self._latest_batch_id == removed.batch_id:
                        self._latest_batch_id = next(
                            reversed(self._batch_members), "")

    def spawn(self, task: str, role: str = "researcher", *, name: str = "",
              context: str = "", model_id: str = "",
              write_scope: tuple[str, ...] | list[str] = (),
              allow_writes: bool = False) -> Subagent:
        return self.spawn_many(
            [{"task": task, "role": role, "name": name,
              "context": context, "model_id": model_id,
              "write_scope": write_scope}],
            allow_writes=allow_writes)[0]

    def spawn_many(self, tasks: list[Any] | tuple[Any, ...], *,
                   role: str = "researcher", context: str = "",
                   model_id: str = "", allow_writes: bool = False
                   ) -> list[Subagent]:
        """Validate then atomically submit a bounded batch of concurrent tasks."""
        if not isinstance(tasks, (list, tuple)):
            raise SubagentError("tasks must be a list")
        if len(tasks) > MAX_GROUP_SIZE:
            raise SubagentError(f"a group supports at most {MAX_GROUP_SIZE} tasks")
        if not tasks:
            return []
        if not isinstance(role, str) or role not in ROLES:
            raise SubagentError(f"unknown default role: {role!r}")
        prepared = [self._validate_item(
            item, role, context, model_id, allow_writes=allow_writes)
            for item in tasks]
        for i, item in enumerate(prepared):
            for prior in prepared[:i]:
                if self._scope_overlap(
                        _scope_view(prior), item["write_scope"],
                        item["write_directories"]):
                    raise SubagentError(
                        "parallel writers have overlapping write_scope paths")
        with self._lock:
            if self._closed:
                raise SubagentError("subagent manager is shutting down")
            if self._transitioning:
                raise SubagentError("session is changing; try again")
            active = sum(w.state in ACTIVE_STATES for w in self._workers.values())
            if active + len(prepared) > self.max_active:
                raise SubagentError(
                    f"capacity is {self.max_active}; {active} active and "
                    f"{len(prepared)} requested")
            for item in prepared:
                for existing in self._workers.values():
                    if existing.state in ACTIVE_STATES and self._scope_overlap(
                            existing, item["write_scope"],
                            item["write_directories"]):
                        raise SubagentError(
                            f"write_scope conflicts with active subagent "
                            f"{existing.id}")
            self._prune(len(prepared))
            created: list[Subagent] = []
            self._batch_counter += 1
            batch_id = f"batch-{self._batch_counter:04d}"
            self._latest_batch_id = batch_id
            self._batch_members[batch_id] = []
            from .systemprompt import subagent_prompt
            for item in prepared:
                self._counter += 1
                aid = f"sa-{self._counter:04d}"
                worker = Subagent(
                    id=aid, batch_id=batch_id,
                    name=item["name"] or f"{item['role']}-{self._counter}",
                    role=item["role"], task=item["task"],
                    model_id=item["model_id"],
                    write_scope=item["write_scope"],
                    write_directories=item["write_directories"],
                    provider=item["provider"], model=item["model"],
                    effort=item["effort"], tools=item["tools"],
                    session_id=self._session_id)
                worker.messages = [
                    {"role": "system", "content": subagent_prompt(
                        worker.role, write_scope=list(worker.write_scope))},
                    {"role": "user", "content": (
                        (f"Shared context:\n{item['context']}\n\n"
                         if item["context"] else "")
                        + f"TASK: {worker.task}")},
                ]
                self._workers[aid] = worker
                self._order.append(aid)
                self._batch_members[batch_id].append(aid)
                created.append(worker)
            # Seal the full batch before any worker can emit progress.
            for worker in created:
                ev = self._emit("subagent.spawn", {
                    "id": worker.id, "name": worker.name,
                    "batch_id": batch_id,
                    "role": worker.role, "task": worker.task[:500],
                    "model": worker.model_id,
                    "write_scope": list(worker.write_scope),
                    "parallel_batch": [x.id for x in created],
                }, worker)
                worker.spawn_event_id = str(getattr(ev, "id", "") or "")
            submitted: list[str] = []
            try:
                for worker in created:
                    self._futures[worker.id] = self._pool.submit(
                        self._execute, worker)
                    submitted.append(worker.id)
            except RuntimeError as exc:
                # The pool shut down mid-batch (shutdown raced the submit
                # loop): every worker that never got a future must still
                # reach a terminal state and emit subagent.done, or it would
                # sit in "queued" forever, leak active capacity, and never
                # be prunable.
                stranded = [w for w in created if w.id not in submitted]
                for worker in stranded:
                    worker.state = "error"
                    worker.error = f"could not schedule worker: {exc}"
                    worker.finished_at = time.time()
                    self._emit("subagent.done", worker.to_dict(), worker)
                raise SubagentError(
                    f"could not schedule {len(stranded)} worker(s): {exc}"
                    ) from exc
            return created

    def get(self, worker_id: str) -> Subagent | None:
        with self._lock:
            return self._workers.get(str(worker_id or "").strip())

    def list(self) -> list[Subagent]:
        with self._lock:
            return [self._workers[aid] for aid in self._order
                    if aid in self._workers]

    def get_batch(self, batch_id: str = "") -> list[Subagent]:
        """Return retained workers from a named batch (latest by default)."""
        key = str(batch_id or "").strip()
        with self._lock:
            key = key or self._latest_batch_id
            if not key:
                return []
            if key not in self._batch_members:
                raise SubagentError(f"unknown or expired subagent batch: {key}")
            return [self._workers[aid] for aid in self._batch_members[key]
                    if aid in self._workers]

    def collect(self, batch_id: str = "", worker_ids: list[str] | tuple[str, ...]
                | None = None, *, timeout: float = 30.0,
                should_cancel: Callable[[], bool] | None = None) -> str:
        """Wait once, then return bounded final reports for one parallel batch."""
        if batch_id and worker_ids:
            raise SubagentError("provide batch_id or worker_ids, not both")
        if worker_ids is not None:
            if not isinstance(worker_ids, (list, tuple)) or not worker_ids:
                raise SubagentError("worker_ids must be a non-empty list")
            if len(worker_ids) > MAX_GROUP_SIZE:
                raise SubagentError(
                    f"collect supports at most {MAX_GROUP_SIZE} workers")
            if len(set(str(item) for item in worker_ids)) != len(worker_ids):
                raise SubagentError("worker_ids must not contain duplicates")
            workers = [self.get(str(aid)) for aid in worker_ids]
            if any(worker is None for worker in workers):
                missing = next(str(aid) for aid, worker in zip(worker_ids, workers)
                               if worker is None)
                raise SubagentError(f"unknown subagent: {missing}")
        else:
            workers = self.get_batch(batch_id)
        if not workers:
            return "No retained subagents in that batch."
        ids = [worker.id for worker in workers]
        states = self.wait(ids, timeout=timeout, should_cancel=should_cancel)
        reports = [self.format_worker(worker) for worker in workers]
        still_active = [aid for aid, state in states.items()
                        if state in ACTIVE_STATES]
        header = (f"COLLECTED {len(workers)} SUBAGENT REPORT(S)"
                  + (f" · {len(still_active)} still active after the wait"
                     if still_active else " · all tasks settled"))
        return header + "\n\n" + "\n\n".join(reports)

    def stop(self, worker_id: str) -> Subagent:
        with self._lock:
            worker = self._workers.get(str(worker_id or "").strip())
            if worker is None:
                raise SubagentError(f"unknown subagent: {worker_id}")
            if worker.state in TERMINAL_STATES:
                return worker
            worker.stop_event.set()
            worker.current_action = "cancellation requested"
            future = self._futures.get(worker.id)
            if future is not None and future.cancel():
                worker.state = "cancelled"
                worker.summary = "Cancelled before the worker started."
                worker.finished_at = time.time()
                self._emit("subagent.cancelled", worker.to_dict(), worker)
            else:
                worker.state = "cancelling"
                self._emit("subagent.progress", {
                    "id": worker.id, "state": "cancelling",
                    "action": worker.current_action}, worker)
            return worker

    def wait(self, worker_ids: list[str] | tuple[str, ...] | None = None,
             timeout: float = 30.0,
             should_cancel: Callable[[], bool] | None = None) -> dict[str, str]:
        """Wait up to ``timeout``; return the latest state for every requested id."""
        try:
            limit = max(0.0, min(90.0, float(timeout)))
        except (TypeError, ValueError):
            limit = 30.0
        with self._lock:
            # Strip ids: get()/stop() already do, so a whitespace-padded id
            # from the model must resolve here too instead of answering
            # "missing" instantly and skipping the wait.
            ids = ([str(x).strip() for x in worker_ids]
                   if worker_ids is not None
                   else list(self._order))
            futures = [self._futures[x] for x in ids
                       if x in self._futures
                       and not self._futures[x].done()]
        deadline = time.monotonic() + limit
        while futures and time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                # Esc/Ctrl+C: stop exactly what we were waiting on so the
                # workers do not keep burning model calls after this turn
                # dies, then let the turn abort via TurnCancelled.
                with self._lock:
                    targets = [self._workers[aid] for aid in ids
                               if aid in self._workers]
                for worker in targets:
                    if worker.state in ACTIVE_STATES:
                        try:
                            self.stop(worker.id)
                        except SubagentError:
                            pass
                raise TurnCancelled()
            done, pending = concurrent.futures.wait(
                futures, timeout=min(0.2, max(0.0, deadline - time.monotonic())),
                return_when=concurrent.futures.FIRST_COMPLETED)
            futures = list(pending)
            if not done and limit == 0:
                break
        with self._lock:
            return {aid: (self._workers[aid].state if aid in self._workers
                          else "missing") for aid in ids}

    def reset_session(self, session_id: str) -> None:
        """Cancel outgoing-session work, then discard its task view.

        Wait for every submitted future before returning so in-flight tools
        finish before the host snapshots or switches sessions. The wait is
        bounded by DRAIN_TIMEOUT: a worker that ignores cancellation cannot
        hang the session switch forever. Each worker's events are also
        pinned to its spawn session in ``_emit``.
        """
        next_session = str(session_id or "")
        with self._lock:
            if self._closed:
                return
            if self._transitioning:
                raise SubagentError("a session transition is already in progress")
            self._transitioning = True
        try:
            with self._lock:
                active_ids = [worker.id for worker in self._workers.values()
                              if worker.state in ACTIVE_STATES]
            for worker_id in active_ids:
                self.stop(worker_id)
            with self._lock:
                futures = list(self._futures.values())
            if futures:
                # Bounded drain: stop() set every worker's stop_event, so a
                # cooperative worker finishes within one cancel poll. Never
                # block a session switch on an uninterruptible worker; its
                # pool thread reaps itself when the blocking call returns.
                concurrent.futures.wait(futures, timeout=DRAIN_TIMEOUT)
            with self._lock:
                self._workers.clear()
                self._order.clear()
                self._futures.clear()
                self._batch_members.clear()
                self._latest_batch_id = ""
                self._session_id = next_session
        finally:
            with self._lock:
                self._transitioning = False

    # -- execution -----------------------------------------------------------

    def _may_write(self, worker: Subagent, tool_name: str,
                   args: dict) -> tuple[bool, str]:
        if tool_name not in WRITE_TOOLS:
            return True, ""
        if not worker.write_scope:
            return False, "delegated write blocked: no approved write_scope"
        raw = args.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return False, "delegated write blocked: tool needs a valid path"
        path = Path(raw).expanduser()
        if ".." in path.parts:
            return False, "delegated write blocked: path traversal is forbidden"
        path = path if path.is_absolute() else self.workspace / path
        resolved = path.resolve(strict=False)
        if not self._relative_to(resolved, self.workspace):
            return False, "delegated write blocked: path escapes the workspace"
        allowed = any(
            str(resolved) == scope
            or (scope in worker.write_directories
                and self._relative_to(resolved, Path(scope)))
            for scope in worker.write_scope)
        if not allowed:
            return False, (
                f"delegated write blocked: {resolved} is outside this worker's "
                f"write_scope ({', '.join(worker.write_scope)})")
        if self.before_write is not None:
            try:
                reason = self.before_write(worker, self.tools[tool_name], args)
            except Exception as exc:  # snapshot failure must fail closed
                return False, ("delegated write blocked: pre-write recovery "
                               f"snapshot failed ({type(exc).__name__}: {exc})")
            if reason:
                return False, str(reason)
        return True, ""

    @staticmethod
    def _event_args(args: dict) -> dict:
        out = {}
        for key, value in args.items():
            if key in {"content", "old_string", "new_string", "patch"}:
                out[key] = f"<{len(value)} chars>" if isinstance(value, str) else "<omitted>"
            else:
                out[key] = value
        try:
            encoded = json.dumps(out, ensure_ascii=False, default=str)
            if len(encoded) > 3000:
                return {"preview": encoded[:2800] + "…"}
        except Exception:
            return {"preview": str(out)[:2800]}
        return out

    def _execute(self, worker: Subagent) -> None:
        cancelled_before_start = False
        with self._lock:
            if worker.stop_event.is_set() or worker.state == "cancelled":
                if worker.state != "cancelled":
                    worker.state = "cancelled"
                    worker.summary = "Cancelled before the worker started."
                    worker.finished_at = time.time()
                    cancelled_before_start = True
                return_after_lock = True
            else:
                worker.state = "running"
                worker.current_action = "starting"
                worker.started_at = time.time()
                return_after_lock = False
            cancelled_data = worker.to_dict() if cancelled_before_start else None
        if return_after_lock:
            if cancelled_before_start:
                self._emit("subagent.cancelled", cancelled_data, worker)
            return
        try:
            self._emit("subagent.progress", {
                "id": worker.id, "state": "running", "step": 0,
                "action": "started"}, worker)
            final = ""
            for step in range(1, self.max_steps + 1):
                if worker.stop_event.is_set():
                    raise TurnCancelled()
                with self._lock:
                    worker.steps = step
                    worker.current_action = f"model step {step}/{self.max_steps}"
                result = self.chat(
                    worker.provider, worker.model, worker.effort,
                    worker.messages,
                    [tool.openai_schema() for tool in worker.tools.values()],
                    timeout=CALL_TIMEOUT,
                    should_cancel=worker.stop_event.is_set)
                if worker.stop_event.is_set():
                    raise TurnCancelled()
                if result is None:
                    raise RuntimeError("provider returned no result")
                normalizer = getattr(result, "normalize", None)
                if callable(normalizer):
                    normalizer()
                content = getattr(result, "content", "")
                if not isinstance(content, str):
                    content = ""
                calls = getattr(result, "tool_calls", [])
                if not isinstance(calls, (list, tuple)):
                    calls = []
                tin, tout = safe_token_counts(getattr(result, "usage", None))
                with self._lock:
                    worker.tokens_in += tin
                    worker.tokens_out += tout
                safe_calls = []
                seen = set()
                for raw in calls:
                    call = normalize_tool_call(raw)
                    if call is None:
                        continue
                    cid = call.get("id")
                    if cid in seen:
                        call = dict(call)
                        call["id"] = f"{cid}-{uuid.uuid4().hex[:8]}"
                    seen.add(call["id"])
                    safe_calls.append(call)
                safe_calls = safe_calls[:MAX_CALLS_PER_STEP]
                if not safe_calls:
                    final = content.strip()
                    if calls and not final:
                        final = "Provider returned no usable tool calls or final text."
                    break
                reasoning = getattr(result, "reasoning", "")
                if not isinstance(reasoning, str):
                    reasoning = ""
                worker.messages.append(assistant_message(content, safe_calls,
                                                         reasoning))
                for call in safe_calls:
                    if worker.stop_event.is_set():
                        raise TurnCancelled()
                    if worker.tool_calls >= MAX_TOTAL_TOOL_CALLS:
                        with self._lock:
                            worker.state = "blocked"
                        final = ("Worker reached its total tool-call limit; "
                                 "reporting the work completed so far.")
                        break
                    fn = call["function"]
                    name = fn["name"]
                    args = parse_tool_arguments(fn.get("arguments", "{}"))
                    with self._lock:
                        worker.tool_calls += 1
                        worker.current_action = name
                    started = time.monotonic()
                    tool = worker.tools.get(name)
                    if tool is None:
                        output = (f"ERROR: tool {name!r} is not available to "
                                  f"the {worker.role} subagent")
                    else:
                        allowed, why = self._may_write(worker, name, args)
                        if not allowed:
                            output = "ERROR: " + why
                        else:
                            try:
                                try:
                                    parameters = inspect.signature(
                                        tool.handler).parameters
                                    accepts_cancel = (
                                        "should_cancel" in parameters
                                        or any(p.kind is inspect.Parameter.VAR_KEYWORD
                                               for p in parameters.values()))
                                except (TypeError, ValueError):
                                    accepts_cancel = False
                                if accepts_cancel:
                                    output = tool.handler(
                                        **args,
                                        should_cancel=worker.stop_event.is_set)
                                else:
                                    output = tool.handler(**args)
                                if not isinstance(output, str):
                                    output = "" if output is None else str(output)
                            except TurnCancelled:
                                raise
                            except Exception as exc:  # one bad tool never kills peers
                                output = (f"ERROR: {type(exc).__name__}: {exc}")
                    if (tool is not None and name in WRITE_TOOLS
                            and not output.startswith("ERROR:")
                            and self.after_write is not None):
                        try:
                            warning = self.after_write(worker, tool, args)
                            if warning:
                                output += "\n" + str(warning)
                        except Exception as exc:  # report verification failures
                            output += ("\nWARNING: post-write verification "
                                       f"failed ({type(exc).__name__}: {exc})")
                    if (tool is not None and name in WRITE_TOOLS
                            and not output.startswith("ERROR:")):
                        path = args.get("path")
                        if isinstance(path, str) and path:
                            with self._lock:
                                if path not in worker.files_touched:
                                    worker.files_touched.append(path)
                    output = output[:MAX_TOOL_OUTPUT_CHARS]
                    self._emit("subagent.tool", {
                        "id": worker.id, "name": name,
                        "args": self._event_args(args),
                        "status": "error" if output.startswith("ERROR:") else "done",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "preview": output[:300],
                    }, worker)
                    worker.messages.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": output,
                    })
                    self._emit("subagent.progress", {
                        "id": worker.id, "state": "running",
                        "step": step, "action": name,
                        "tool_calls": worker.tool_calls}, worker)
                if worker.state == "blocked":
                    break
                final = content.strip()
            else:
                final = (final + "\n" if final else "") + (
                    f"Worker reached its {self.max_steps}-step limit; "
                    "reporting the work completed so far.")
                with self._lock:
                    worker.state = "blocked"
            if worker.stop_event.is_set():
                raise TurnCancelled()
            if not final:
                final = "Completed without a text summary."
            with self._lock:
                if worker.state != "blocked":
                    worker.state = "done"
                worker.summary = final[:4000]
        except TurnCancelled:
            with self._lock:
                worker.state = "cancelled"
                worker.summary = "Cancelled by request."
        except BaseException as exc:  # isolate even unexpected worker crashes
            with self._lock:
                worker.state = "error"
                worker.error = f"{type(exc).__name__}: {exc}"
                worker.summary = "Subagent failed; inspect its error in TaskList."
        finally:
            with self._lock:
                worker.finished_at = time.time()
                worker.current_action = worker.state
                state = worker.state
                data = worker.to_dict()
            self._emit("subagent.done", data, worker)

    # -- reporting / shutdown ------------------------------------------------

    def format_worker(self, worker: Subagent | None) -> str:
        if worker is None:
            return "ERROR: subagent not found"
        data = worker.to_dict()
        from .roles import role_label
        lines = [f"{data['id']} · {data['name']} · "
                 f"{role_label(data['role'])} ({data['role']}) · "
                 f"{data['state']} · batch {data['batch_id']}"]
        if data["current_action"] and data["state"] in ACTIVE_STATES:
            lines.append(f"Current: {data['current_action']}")
        if data["summary"]:
            lines.append("Result: " + data["summary"])
        if data["error"]:
            lines.append("Error: " + data["error"])
        lines.append(
            f"Work: {data['steps']} step(s), {data['tool_calls']} tool call(s), "
            f"{data['tokens_in']}→{data['tokens_out']} tokens · "
            f"{data['elapsed_ms'] / 1000:.1f}s elapsed")
        if data["write_scope"]:
            lines.append("Approved write scope: "
                         + ", ".join(data["write_scope"]))
        if data["files_touched"]:
            lines.append("Files touched: " + ", ".join(data["files_touched"]))
        return "\n".join(lines)

    def format_status(self) -> str:
        snapshot = self.snapshot()
        workers = snapshot["workers"]
        if not workers:
            return ("No subagents yet. Use TaskGroup for parallel read-only "
                    "delegation, or TaskWriteGroup for approval-gated, "
                    "file-scoped implementation.")
        counts = snapshot["counts"]
        lines = [
            f"PARALLEL CREW · {len(workers)} retained · "
            f"{snapshot['active']}/{snapshot['max_active']} active · "
            f"{counts['running']}/{snapshot['max_workers']} workers · "
            f"{counts['queued']} queued · {counts['done']} done · "
            f"{counts['blocked']} blocked · {counts['error']} errors",
            (f"Batch {snapshot['latest_batch_id']} · "
             f"{snapshot['tokens_in']}→{snapshot['tokens_out']} tokens"),
        ]
        icons = {"queued": "○", "running": "●", "cancelling": "◌",
                 "done": "✓", "blocked": "!", "error": "×",
                 "cancelled": "–"}
        for row in workers:
            task = " ".join(row["task"].split())
            if len(task) > 96:
                task = task[:93] + "..."
            lines.append(
                f"{icons.get(row['state'], '·')} {row['id']} "
                f"{row['state'].upper():<10} {row['role']:<12} "
                f"{row['name'][:20]:<20} {row['elapsed_ms'] / 1000:.1f}s · "
                f"{row['steps']} steps · {row['tool_calls']} tools")
            lines.append(f"  task: {task}")
            if row["state"] in ACTIVE_STATES and row["current_action"]:
                lines.append(f"  now:  {row['current_action']}")
            elif row["summary"]:
                summary = " ".join(row["summary"].split())
                lines.append(f"  result: {summary[:180]}"
                             + ("…" if len(summary) > 180 else ""))
        return "\n".join(lines)

    def snapshot(self) -> dict:
        """Return an immutable-style, JSON-safe live dashboard snapshot."""
        with self._lock:
            workers = [self._workers[aid].to_dict() for aid in self._order
                       if aid in self._workers]
            states = ("queued", "running", "cancelling", "done", "blocked",
                      "error", "cancelled")
            counts = {state: sum(row["state"] == state for row in workers)
                      for state in states}
            return {
                "workers": workers,
                "counts": counts,
                "active": sum(counts[state] for state in ACTIVE_STATES),
                "max_active": self.max_active,
                "max_workers": self.max_workers,
                "latest_batch_id": self._latest_batch_id,
                "tokens_in": sum(row["tokens_in"] for row in workers),
                "tokens_out": sum(row["tokens_out"] for row in workers),
            }

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            self._closed = True
            for worker in self._workers.values():
                if worker.state in ACTIVE_STATES:
                    worker.stop_event.set()
                    future = self._futures.get(worker.id)
                    if future is not None and future.cancel():
                        worker.state = "cancelled"
                        worker.summary = "Cancelled during manager shutdown."
                        worker.finished_at = time.time()
                        self._emit("subagent.cancelled", worker.to_dict(), worker)
                    else:
                        worker.state = "cancelling"
            draining = list(self._futures.values()) if wait else []
        if draining:
            # Bounded drain: every active worker was stop()ed above, so a
            # cooperative worker winds down within one cancel poll. Never
            # hang teardown forever on a worker that ignores cancellation
            # (e.g. a stuck provider call); its pool thread is abandoned and
            # reaps itself when the call finally returns.
            concurrent.futures.wait(draining, timeout=DRAIN_TIMEOUT)
        self._pool.shutdown(wait=False, cancel_futures=True)


def _scope_view(item: dict) -> Subagent:
    """Small structural adapter used to reuse the overlap predicate."""
    return Subagent(
        id="", batch_id="", name="", role="", task="", model_id="",
        write_scope=item["write_scope"],
        write_directories=item["write_directories"],
        provider=None, model=None, effort=None, tools={})


def _self_test() -> None:
    """Offline end-to-end smoke check for the background worker lifecycle."""
    from types import SimpleNamespace

    class _Log:
        def __init__(self):
            self.events = []

        def append(self, kind, data, **kwargs):
            event = SimpleNamespace(id=f"event-{len(self.events) + 1}")
            self.events.append((kind, data, kwargs))
            return event

    provider = SimpleNamespace(id="offline")
    model = SimpleNamespace(id="offline-model", supports_tools=True)
    effort = SimpleNamespace(key="low")

    def chat(*_args, **_kwargs):
        return SimpleNamespace(content="offline result", tool_calls=[],
                               usage={"prompt_tokens": 1,
                                      "completion_tokens": 1})

    log = _Log()
    manager = ParallelSubagents(
        log, {}, lambda _model_id: (provider, model, effort),
        max_workers=2, max_active=2, chat=chat)
    try:
        workers = manager.spawn_many(["smoke task one", "smoke task two"])
        states = manager.wait([worker.id for worker in workers], timeout=5)
        assert len(workers) == 2 and all(
            states.get(worker.id) == "done" for worker in workers)
        assert sum(kind == "subagent.done" for kind, _data, _kwargs
                   in log.events) == 2
        assert "2 retained" in manager.format_status()
    finally:
        manager.shutdown(wait=True)
    print("SUBAGENT SELF-TEST PASS (parallel lifecycle, events, status)")


if __name__ == "__main__":
    _self_test()
