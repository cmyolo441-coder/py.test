"""Parallel execution of independent read-only tool calls (Worker 11/20).

PERFORMANCE EMERGENCY: when the model emitted several independent tool
calls in one block (e.g. read 5 files before editing), Agent.run_turn
executed them strictly one-by-one, so the block cost the SUM of the
latencies. This module walks the block in model-emitted order and runs
maximal runs of *independent* read-only calls concurrently in a capped
ThreadPoolExecutor, so such a block costs ~MAX of the latencies.

Safety model — writes are NEVER parallelized, and order is sacred:
  * ``PARALLEL_READ_TOOLS`` is exactly the Speculator's
    ``SPECULATIVE_TOOLS`` whitelist (read_file, list_dir, file_info,
    search_files, glob_files), whose safety gate exists precisely so "a
    speculative write is structurally impossible".
  * ``dispatch_block()`` walks the block left to right and dispatches
    each parallel batch *at its position* in the walk. Execution order
    is therefore exactly the model's order: a read after a write always
    observes that write. Only independent reads overlap.
  * All five whitelisted tools are RISK_SAFE, so no approval prompt can
    fire from a worker thread; interactive PreToolUse hooks force a
    sequential fallback.
  * ``finish(tc, ev)`` replays all per-call bookkeeping in
    model-emitted order, so the transcript, event log and turn.tools
    are identical to the sequential path.

Thread-safety notes for the worker path (Agent._execute_tool):
  * EventLog.append, Hippocampus (brain RLock) and Speculator (its own
    lock) are already thread-safe; the read handlers are pure I/O
    (read_file sits on an lru_cache, which is thread-safe).
  * Agent serializes the two non-thread-safe touches (_error_counts
    read-modify-write, LoopDetector._last_sig) under _tool_exec_lock.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .speculate import SPECULATIVE_TOOLS

#: Tools that may run concurrently — the Speculator's read-only whitelist.
PARALLEL_READ_TOOLS = frozenset(SPECULATIVE_TOOLS)

#: Cap on fan-out: enough to hide I/O latency, small enough to stay polite.
PARALLEL_MAX_WORKERS = 8


def is_parallelizable(tool_name: str, *, hooks_enabled: bool = False) -> bool:
    """True only for structurally read-only tools.

    Interactive PreToolUse hooks force sequential execution — a hook may
    prompt the user, and prompts must never fan out across threads.
    """
    if hooks_enabled:
        return False
    return tool_name in PARALLEL_READ_TOOLS


def plan_batches(tool_names: list[str], *,
                 hooks_enabled: bool = False) -> list[list[int]]:
    """Partition call indices into parallel batches.

    Returns the maximal runs of >= 2 consecutive parallelizable calls.
    A lone parallelizable call runs sequentially (no thread-pool
    overhead), and any non-parallelizable tool flushes the run — so a
    mutating tool always executes strictly between its neighbours.
    """
    batches: list[list[int]] = []
    i, n = 0, len(tool_names)
    while i < n:
        if not is_parallelizable(tool_names[i], hooks_enabled=hooks_enabled):
            i += 1
            continue
        j = i
        while j < n and is_parallelizable(tool_names[j],
                                          hooks_enabled=hooks_enabled):
            j += 1
        if j - i >= 2:
            batches.append(list(range(i, j)))
        i = j
    return batches


def _run_batch(execute: Callable[..., None],
               batch: list[tuple[dict, Any]],
               *,
               approve: Any,
               on_status: Callable[[str], None],
               causation_id: str | None,
               on_tool_output: Callable[[str, str], None] | None) -> None:
    """Run one batch of ToolEvents concurrently.

    Results land on the events themselves. ``executor.map`` preserves
    input order; each worker fills its own ToolEvent, so completion
    order never affects the transcript. The ``with`` block joins every
    worker, so a worker exception propagates exactly as it would have
    sequentially (the turn dies with the error).
    """
    workers = min(PARALLEL_MAX_WORKERS, len(batch))

    def _run(item: tuple[dict, Any]) -> None:
        _, ev = item
        execute(ev, approve, on_status,
                causation_id=causation_id, on_tool_output=on_tool_output)

    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="paratool") as ex:
        list(ex.map(_run, batch))


def dispatch_block(
    execute: Callable[..., None],
    finish: Callable[[dict, Any], bool],
    pending: list[tuple[dict, Any]],
    *,
    approve: Any,
    on_status: Callable[[str], None],
    causation_id: str | None = None,
    on_tool_output: Callable[[str, str], None] | None = None,
    hooks_enabled: bool = False,
) -> None:
    """Execute one model-emitted tool-call block, in order.

    ``pending`` is ``[(tool_call, ToolEvent), ...]`` in model-emitted
    order. ``execute`` has the ``Agent._execute_tool`` call shape
    ``(ev, approve, on_status, causation_id=..., on_tool_output=...)``.
    ``finish(tc, ev)`` runs after every call in model-emitted order and
    returns True to stop the walk early (guardrails / cancellation).
    """
    i, n = 0, len(pending)
    while i < n:
        if is_parallelizable(pending[i][1].name,
                             hooks_enabled=hooks_enabled):
            j = i
            while j < n and is_parallelizable(pending[j][1].name,
                                              hooks_enabled=hooks_enabled):
                j += 1
            if j - i >= 2:
                _run_batch(execute, pending[i:j], approve=approve,
                           on_status=on_status, causation_id=causation_id,
                           on_tool_output=on_tool_output)
                for tc_b, ev_b in pending[i:j]:
                    if finish(tc_b, ev_b):
                        return
                i = j
                continue
        tc, ev = pending[i]
        execute(ev, approve, on_status,
                causation_id=causation_id, on_tool_output=on_tool_output)
        if finish(tc, ev):
            return
        i += 1


# ---------------------------------------------------------------------------
# Self-test — proves parallel speedup, result ordering, and write safety.
# Run:  python3 -m fullagent.parallel_tools
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from .tools import read_file as _real_read_file
    from .tools import write_file as _real_write_file

    LAT = 0.25  # simulated per-call I/O latency (slow FS / big files)

    def _ev(name: str, args: dict) -> SimpleNamespace:
        return SimpleNamespace(name=name, args=args, result="",
                               status="running", duration=0.0)

    with tempfile.TemporaryDirectory() as td:
        paths = []
        for i in range(5):
            p = Path(td) / f"f{i}.txt"
            p.write_text(f"content-of-file-{i}\n" * 20)
            paths.append(str(p))

        # -- 1. speedup: 5 parallel reads, real handler ------------------
        def _timed_execute(ev, approve, on_status,
                           causation_id=None, on_tool_output=None):
            t0 = time.time()
            time.sleep(LAT)  # the I/O latency sequential execution pays 5x
            ev.result = _real_read_file(**ev.args)  # the REAL handler
            ev.status = "done"
            ev.duration = time.time() - t0

        def _sequential(pending):
            t0 = time.time()
            for _, ev in pending:
                _timed_execute(ev, None, lambda s: None)
            return time.time() - t0

        seq_pending = [({"id": f"c{i}"}, _ev("read_file", {"path": p}))
                       for i, p in enumerate(paths)]
        t_seq = _sequential(seq_pending)

        par_pending = [({"id": f"c{i}"}, _ev("read_file", {"path": p}))
                       for i, p in enumerate(paths)]
        finished: list[str] = []
        t0 = time.time()
        dispatch_block(_timed_execute,
                       lambda tc, ev: finished.append(tc["id"]) or False,
                       par_pending, approve=None, on_status=lambda s: None)
        t_par = time.time() - t0

        # ordering: result[i] must be the content of file[i], and the
        # transcript (finish order) must be model order c0..c4
        for i, (_, ev) in enumerate(par_pending):
            # the real read_file handler line-numbers the text, so match
            # on the distinctive per-file marker, not the raw body
            assert f"content-of-file-{i}" in ev.result, \
                f"call {i} got the wrong file's content (order broken)"
            assert ev.result == seq_pending[i][1].result, \
                f"call {i}: parallel result differs from sequential"
        assert finished == [f"c{i}" for i in range(5)], \
            f"transcript order broken: {finished}"
        speedup = t_seq / t_par
        print(f"[1] 5 reads  sequential={t_seq:.2f}s  "
              f"parallel={t_par:.2f}s  speedup={speedup:.1f}x")
        assert speedup >= 3.0, \
            f"no meaningful parallel speedup: {speedup:.1f}x"

        # -- 2. batch planning: writes flush the batch --------------------
        names = ["read_file", "write_file", "read_file", "read_file",
                 "run_command", "glob_files"]
        batches = plan_batches(names)
        assert batches == [[2, 3]], f"bad batches: {batches}"
        assert plan_batches(["read_file"]) == [], "lone read must not batch"
        assert plan_batches(["write_file", "edit_file"]) == [], \
            "mutating tools must never batch"
        assert plan_batches(names, hooks_enabled=True) == [], \
            "hooks must force sequential"
        for w in ("write_file", "edit_file", "run_command", "live_shell",
                  "delete_path", "create_directory", "apply_patch",
                  "copy_path", "move_path"):
            assert not is_parallelizable(w), f"{w} must not parallelize"
        for r in ("read_file", "list_dir", "file_info", "search_files",
                  "glob_files"):
            assert is_parallelizable(r), f"{r} should parallelize"
            assert not is_parallelizable(r, hooks_enabled=True)
        print("[2] plan_batches: writes flush batches, mutating tools "
              "never parallelize, hooks force sequential — OK")

        # -- 3. mixed block: execution order == model order ---------------
        order: list[str] = []
        state = {"inflight": 0, "peak": 0}  # mutable cell (module scope)
        order_lock = threading.Lock()

        def _rec_execute(ev, approve, on_status,
                         causation_id=None, on_tool_output=None):
            with order_lock:
                state["inflight"] += 1
                state["peak"] = max(state["peak"], state["inflight"])
                cur = state["inflight"]
            try:
                time.sleep(0.15)
                with order_lock:
                    order.append(f"{ev.name}:{ev.args.get('path', '')}"
                                 f"@{cur}")
                ev.result, ev.status = "ok", "done"
            finally:
                with order_lock:
                    state["inflight"] -= 1

        mixed = [
            ({"id": "c0"}, _ev("read_file", {"path": "a"})),
            ({"id": "c1"}, _ev("write_file",
                               {"path": "b", "content": "x"})),
            ({"id": "c2"}, _ev("read_file", {"path": "c"})),
            ({"id": "c3"}, _ev("read_file", {"path": "d"})),
        ]
        transcript: list[str] = []
        dispatch_block(_rec_execute,
                       lambda tc, ev: transcript.append(tc["id"]) or False,
                       mixed, approve=None, on_status=lambda s: None)

        kinds = [o.split("@")[0].split(":")[0] for o in order]
        assert kinds == ["read_file", "write_file",
                         "read_file", "read_file"], \
            f"execution order broken: {kinds}"
        assert transcript == ["c0", "c1", "c2", "c3"], \
            f"transcript order broken: {transcript}"
        assert order.count("write_file:b@1") == 1, \
            f"write must run exactly once, alone: {order}"
        assert state["peak"] >= 2, "the two reads did not actually overlap"
        print(f"[3] mixed block exec order={kinds} transcript={transcript} "
              f"write ran once @inflight=1, reads overlapped "
              f"(peak={state['peak']}) — OK")

        # -- 4. read-after-write visibility with REAL handlers ------------
        target = str(Path(td) / "w.txt")
        Path(target).write_text("old\n")

        def _real_execute(ev, approve, on_status,
                          causation_id=None, on_tool_output=None):
            if ev.name == "write_file":
                ev.result = _real_write_file(**ev.args)
            else:
                ev.result = _real_read_file(**ev.args)
            ev.status = "done"

        w_pending = [
            ({"id": "c0"}, _ev("read_file", {"path": paths[0]})),
            ({"id": "c1"}, _ev("read_file", {"path": paths[1]})),
            ({"id": "c2"}, _ev("write_file",
                               {"path": target, "content": "new-data\n"})),
            ({"id": "c3"}, _ev("read_file", {"path": target})),
            ({"id": "c4"}, _ev("read_file", {"path": paths[2]})),
        ]
        seen: list[str] = []
        dispatch_block(_real_execute,
                       lambda tc, ev: seen.append(ev.result) or False,
                       w_pending, approve=None, on_status=lambda s: None)
        assert "new-data" in w_pending[3][1].result, \
            "read AFTER the write did not see the written content!"
        assert "new-data" not in w_pending[0][1].result
        print("[4] read-after-write sees the write (real handlers) — OK")

    print("\nparatools self-test: ALL PASS "
          f"(whitelist={sorted(PARALLEL_READ_TOOLS)}, "
          f"max_workers={PARALLEL_MAX_WORKERS})")
