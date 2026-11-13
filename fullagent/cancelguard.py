"""CANCELGUARD — prompt-cancellation primitives for the turn path.

Every blocking wait a turn can hit (model HTTP calls, retry backoffs,
web fetches and speculative prefetches) goes through
here, so Esc/Ctrl+C interrupts within ~CANCEL_POLL seconds instead of
hanging for minutes (the user reported Esc taking 203s — that was a
rate-limit backoff sleeping ~254s with zero cancel checks,
plus model POSTs that blocked in connect/DNS with no timeout at all).

Design rules:
  * Polling, never signals: the turn thread stays in charge; no
    cross-thread exceptions, no torn state.
  * `run_cancellable(fn, should_cancel)` runs fn() on a daemon worker
    and polls with a short timeout. On cancel it raises TurnCancelled on
    the caller and *abandons* the worker — the worker keeps running to
    completion in the background (harmless: it owns no turn state), and
    if it eventually produces a closeable result it is closed so no
    socket/connection leaks.
  * All helpers are exception-safe: a raising should_cancel callback is
    treated as "not cancelled", never as a crash.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class TurnCancelled(Exception):
    """Raised when the user cancels the turn (Esc/Ctrl+C).

    Lives here (a leaf module) so client.py, tools.py, speculate.py
    and agent.py can all share it without import cycles.
    client.py re-exports it for backwards compatibility.
    """


# Max seconds between cancellation checks on any guarded wait. This is
# the worst-case Esc latency for a guarded operation.
CANCEL_POLL = 0.25


def cancel_requested(should_cancel: Callable[[], bool] | None) -> bool:
    """True if cancellation was requested. Never raises — a broken
    callback is treated as "not cancelled" (keeps pumping)."""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def check_cancelled(should_cancel: Callable[[], bool] | None,
                    what: str = "operation") -> None:
    """Raise TurnCancelled if cancellation was requested."""
    if cancel_requested(should_cancel):
        raise TurnCancelled(f"{what} cancelled by user")


def sleep_cancellable(seconds: float,
                      should_cancel: Callable[[], bool] | None,
                      poll: float = CANCEL_POLL) -> None:
    """time.sleep() that raises TurnCancelled within `poll` seconds of
    the user pressing Esc. Replaces every bare time.sleep() on the turn
    path (retry backoffs, rate-limit waits)."""
    if seconds <= 0:
        check_cancelled(should_cancel, "sleep")
        return
    deadline = time.monotonic() + seconds
    while True:
        if cancel_requested(should_cancel):
            raise TurnCancelled("sleep cancelled by user")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll, remaining))


def run_cancellable(fn: Callable[[], Any],
                    should_cancel: Callable[[], bool] | None,
                    poll: float = CANCEL_POLL,
                    name: str = "operation") -> Any:
    """Run fn() and return its result, honouring cancellation.

    fn() runs on a daemon worker thread while this thread polls for
    completion every `poll` seconds. If should_cancel() fires, raises
    TurnCancelled within ~`poll` seconds — even if fn() is stuck in a
    blocking socket read, DNS lookup, or subprocess wait with no
    timeout of its own.

    On cancellation the worker is abandoned (it keeps running in the
    background until the OS unblocks it — it owns no turn state, so
    this is safe). If it later produces a result with a .close() method
    (e.g. a requests Response), it is closed to release the socket.

    If should_cancel is None the function runs inline — zero behavior
    change for callers with no cancellation source. Exceptions raised
    by fn() are re-raised on the caller with their original type.
    """
    if should_cancel is None:
        return fn()
    check_cancelled(should_cancel, name)

    _DONE = object()
    box: "queue.Queue" = queue.Queue(maxsize=1)
    abandoned = threading.Event()

    def _run() -> None:
        try:
            result = fn()
        # BaseException too: a SystemExit/KeyboardInterrupt raised by fn()
        # killed the worker without ever enqueueing an outcome, leaving the
        # caller polling forever (a silent hang — no cancel will arrive).
        except BaseException as e:  # noqa: BLE001 — re-raised on caller
            try:
                box.put((e, None))
            except Exception:
                pass
            return
        # If cancelled, close any closable resource regardless of timing.
        if abandoned.is_set():
            close = getattr(result, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        try:
            box.put((_DONE, result))
        except Exception:
            pass
        # Race: cancellation may land after enqueue — close then.
        if abandoned.is_set():
            close = getattr(result, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    worker = threading.Thread(target=_run, daemon=True,
                              name=f"cancelguard:{name}")
    worker.start()
    while True:
        try:
            kind, value = box.get(timeout=poll)
        except queue.Empty:
            if cancel_requested(should_cancel):
                abandoned.set()
                raise TurnCancelled(f"{name} cancelled by user")
            continue
        if kind is _DONE:
            return value
        raise kind



# ---------------------------------------------------------------------------
# Subprocess hygiene helpers (shared by hooks / webhook / plugins / mcp ...)
# ---------------------------------------------------------------------------

def kill_process_group(proc: "subprocess.Popen", sig: int | None = None,
                       wait: float | None = 5.0) -> None:
    """Kill ``proc`` and, when it leads its own process group, the whole
    group. Never raises.

    SAFETY: ``os.killpg(os.getpgid(pid))`` on a child that was NOT started
    with ``start_new_session`` resolves to the *agent's own* process group
    and would SIGKILL the agent (and its terminal job). The group is only
    signalled when the child really is its own group leader.
    """
    if sig is None:
        sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        if proc.poll() is None or os.name == "posix":
            done = False
            if os.name == "posix":
                try:
                    pgid = os.getpgid(proc.pid)
                    if pgid == proc.pid and pgid != os.getpgrp():
                        os.killpg(pgid, sig)
                        done = True
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            if not done and proc.poll() is None:
                try:
                    proc.send_signal(sig)
                except (ProcessLookupError, OSError):
                    pass
    except Exception:
        pass
    if wait is not None:
        try:
            proc.wait(timeout=wait)
        except Exception:
            pass


def run_in_group(args: Any, *, timeout: float, input: Any = None,
                 shell: bool = False, env: dict | None = None,
                 cwd: str | None = None, text: bool = False,
                 stdout: Any = subprocess.PIPE,
                 stderr: Any = subprocess.PIPE) -> "subprocess.CompletedProcess":
    """``subprocess.run`` replacement that cannot leak or hang.

    * the child gets its own session/process group, so a timeout (or any
      exception, incl. KeyboardInterrupt) kills the WHOLE tree — not just
      the ``sh -c`` wrapper, which used to leave grandchildren running
      forever (and holding our pipes open);
    * the child is always reaped (no zombies);
    * raises ``subprocess.TimeoutExpired`` on timeout, like ``run``.
    """
    kw: dict[str, Any] = {
        "stdin": subprocess.PIPE if input is not None else subprocess.DEVNULL,
        "stdout": stdout, "stderr": stderr, "shell": shell,
        "env": env, "cwd": cwd,
    }
    if text:
        kw.update(text=True, encoding="utf-8", errors="replace")
    if os.name == "posix":
        kw["start_new_session"] = True
    proc = subprocess.Popen(args, **kw)
    try:
        out, err = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_group(proc, wait=None)
        try:
            proc.communicate(timeout=2.0)
        except Exception:
            # something outside the group still holds a pipe: stop
            # reading, close our ends and reap the child.
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        raise subprocess.TimeoutExpired(args, timeout)
    except BaseException:
        kill_process_group(proc, wait=2.0)
        raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


# -- process tracking without monkeypatch nesting ---------------------------
#
# Agent._run_tool_with_timeout used to swap ``subprocess.Popen`` for a
# per-call closure and restore "the original" afterwards. With parallel
# tool calls the save/restore pairs interleave (A saves real, B saves A's
# wrapper, A restores real, B restores A's wrapper) and Popen ends up
# permanently wrapped — measured: 50 parallel batches of 4 calls left a
# 61-deep wrapper chain, every later Popen walked all of them and kept
# every Popen object alive in dead lists (and eventually RecursionError).
# ``track_popen()`` installs ONE subclass, refcounted, under a lock.

_TRACK_LOCK = threading.Lock()
_TRACKERS: dict[int, tuple] = {}   # key -> (spawned list, thread id|None)
_TRACK_SEQ = 0
_ORIG_POPEN: Any = None
_TrackedPopen: Any = None


def _make_tracked_class(base: Any) -> Any:
    class TrackedPopen(base):  # type: ignore[misc, valid-type]
        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            me = threading.get_ident()
            with _TRACK_LOCK:
                for lst, owner in _TRACKERS.values():
                    if owner is None or owner == me:
                        lst.append(self)
    TrackedPopen.__name__ = "Popen"
    TrackedPopen.__qualname__ = "Popen"
    return TrackedPopen


@contextmanager
def track_popen(thread_only: bool = False) -> Iterator[list]:
    """Collect every ``subprocess.Popen`` created (by any thread) while the
    context is open. Safe to nest and to use from many threads at once:
    ``subprocess.Popen`` is restored exactly when the last tracker exits.

    ``thread_only=True`` records only processes spawned by the thread that
    opened the context — so a timeout in one parallel tool call never
    kills the processes of another call running at the same time."""
    global _TRACK_SEQ, _ORIG_POPEN, _TrackedPopen
    spawned: list = []
    with _TRACK_LOCK:
        _TRACK_SEQ += 1
        key = _TRACK_SEQ
        if not _TRACKERS:
            current = subprocess.Popen
            if _TrackedPopen is None or current is not _TrackedPopen:
                _ORIG_POPEN = current
                _TrackedPopen = _make_tracked_class(current)
            subprocess.Popen = _TrackedPopen
        _TRACKERS[key] = (spawned,
                          threading.get_ident() if thread_only else None)
    try:
        yield spawned
    finally:
        with _TRACK_LOCK:
            _TRACKERS.pop(key, None)
            if not _TRACKERS and subprocess.Popen is _TrackedPopen:
                subprocess.Popen = _ORIG_POPEN

if __name__ == "__main__":
    import socket

    # 1. normal path: result passes through, exceptions keep their type
    assert run_cancellable(lambda: 42, None) == 42
    assert run_cancellable(lambda: 42, lambda: False) == 42
    try:
        run_cancellable(lambda: 1 / 0, lambda: False)
        raise AssertionError("ZeroDivisionError should propagate")
    except ZeroDivisionError:
        pass

    # 2. pre-cancelled: raises immediately, fn never runs
    ran = []
    try:
        run_cancellable(lambda: ran.append(1), lambda: True,
                        name="pre")
        raise AssertionError("should have raised")
    except TurnCancelled:
        pass
    assert ran == [], ran

    # 3. cancel DURING a hung blocking call (socket recv with no peer
    #    data — the classic unbounded read): must raise within 2s
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port))
    srv.accept()  # accepted end never sends -> recv blocks forever
    flag = threading.Event()
    t0 = time.monotonic()
    try:
        def _block_forever():
            c.recv(1 << 20)  # no timeout, no peer data: blocks
            return "never"
        th = threading.Thread(
            target=lambda: run_cancellable(
                _block_forever, flag.is_set, name="recv"),
            daemon=True)
        th.start()
        time.sleep(0.5)
        flag.set()  # Esc!
        th.join(timeout=5)
        dt = time.monotonic() - t0
        assert not th.is_alive(), "worker did not observe cancel"
        assert dt < 2.0, f"cancel took {dt:.2f}s"
        print(f"  hung-recv cancel latency: {dt:.2f}s")
    finally:
        c.close()
        srv.close()

    # 4. cancellable sleep: 60s sleep interrupted promptly
    flag2 = threading.Event()
    t0 = time.monotonic()
    th = threading.Thread(
        target=lambda: sleep_cancellable(60.0, flag2.is_set), daemon=True)
    th.start()
    time.sleep(0.5)
    flag2.set()
    th.join(timeout=5)
    dt = time.monotonic() - t0
    assert not th.is_alive()
    assert dt < 2.0, f"sleep cancel took {dt:.2f}s"
    print(f"  sleep cancel latency: {dt:.2f}s")

    # 5. uncancelled sleep still sleeps the full time
    t0 = time.monotonic()
    sleep_cancellable(0.6, None)
    assert 0.5 < time.monotonic() - t0 < 1.5

    print("CANCELGUARD SELF-TEST PASS")
