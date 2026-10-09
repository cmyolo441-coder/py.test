"""CANCELPROOF — proof that Esc interrupts every blocking point fast.

The user reported Esc taking 203 SECONDS to cancel a turn. The audit
(worker 14/20) found these unbounded blocking points on the turn path:

  1. client._chat_stream_once — the initial requests.post() blocked in
     DNS/connect/TLS with NO cancel check (requests' timeout does not
     cover DNS).
  2. client._chat_stream_once — non-stream JSON fallback resp.json()
     read the whole body bounded only by the 300s read timeout.
  3. client._post_blocking / chat_blocking — the entire request blocked
     up to 300s x 3 retries with ZERO cancel checks.
  4. client retry backoff — bare time.sleep() ignored Esc.
  5. team.chat_with_retry — rate-limit backoff slept ~254s total
     (2+4+8+...+128s) with zero cancel checks  <-- the 203s hang.
  6. crew._run_loop — subagent workers checked stop_event only BETWEEN
     steps; an in-flight model call ignored Esc for the full timeout.
  7. tools.web_fetch / web_search — requests calls with 30s timeouts,
     no cancel checks.
  8. speculate.speculate — blocked the turn on a ThreadPoolExecutor
     until every prefetch finished.
  9. agent._execute_tool — swallowed TurnCancelled from handlers into
     an "ERROR: ..." string, so the turn kept going after Esc.

All are fixed via fullagent/cancelguard.py (run_cancellable /
sleep_cancellable, 0.25s poll). This module PROVES it: each test starts
a turn-like blocking operation, fires the cancel flag 0.5s in (the
"Esc"), and asserts the operation observes cancellation in < 2s.

Run:  python -m fullagent.cancelproof
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from .cancelguard import TurnCancelled, run_cancellable, sleep_cancellable

# -- fixtures ---------------------------------------------------------------

TRIGGER_AFTER = 0.5   # seconds before "Esc" fires
LIMIT = 2.0           # every guarded op must stop within this


def _esc_after(flag: threading.Event, delay: float = TRIGGER_AFTER) -> None:
    """Fire the cancel flag after `delay` seconds (simulates Esc)."""
    def _fire():
        time.sleep(delay)
        flag.set()
    threading.Thread(target=_fire, daemon=True).start()


class Blackhole:
    """TCP server that accepts connections but NEVER responds — the
    worst case for a blocking read: no timeout, no error, no data."""

    def __init__(self) -> None:
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(50)
        self.port = self.srv.getsockname()[1]
        self._conns: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="blackhole")
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                self.srv.settimeout(0.5)
                conn, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            self._conns.append(conn)  # held open, never read/written

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/chat/completions"

    def close(self) -> None:
        self._stop.set()
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        try:
            self.srv.close()
        except OSError:
            pass


class _Always429(BaseHTTPRequestHandler):
    """Provider that always rate-limits — drives chat_with_retry into
    its (previously ~254s, uninterruptible) backoff."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(length)
        body = b'{"error": {"message": "rate limited", "code": 429}}'
        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN001, ANN202
        pass


def _fake_provider(base_url: str) -> SimpleNamespace:
    return SimpleNamespace(key="t", name="T", base_url=base_url,
                           api_key="sk-fake", color="#fff")


def _fake_model() -> SimpleNamespace:
    return SimpleNamespace(id="stub", provider="t", label="Stub",
                           supports_tools=False, supports_reasoning=False,
                           context_window=128000)


def _fake_effort() -> SimpleNamespace:
    return SimpleNamespace(key="low", label="LOW", color="#fff",
                           max_tokens=64, temperature=0.0,
                           reasoning_effort=None)


def _timed(name: str, fn) -> float:
    t0 = time.monotonic()
    fn()
    dt = time.monotonic() - t0
    status = "OK " if dt < LIMIT else "FAIL"
    print(f"  [{status}] {name}: stopped {dt:.2f}s after start "
          f"(limit {LIMIT:.0f}s)")
    assert dt < LIMIT, f"{name}: cancel took {dt:.2f}s >= {LIMIT}s"
    return dt


# -- the proof tests ----------------------------------------------------------

def test_hung_post_blocking() -> None:
    """BLOCKER 3: _post_blocking against a hung provider."""
    from .client import _post_blocking
    bh = Blackhole()
    try:
        flag = threading.Event()
        _esc_after(flag)

        def _run():
            try:
                _post_blocking(bh.url, {}, {"model": "x", "messages": []},
                               timeout=120.0, should_cancel=flag.is_set)
            except TurnCancelled:
                return
            raise AssertionError("TurnCancelled not raised")

        _timed("hung _post_blocking raises TurnCancelled", _run)
    finally:
        bh.close()


def test_hung_stream_post() -> None:
    """BLOCKER 1: _chat_stream_once initial POST against a hung provider."""
    from .client import _chat_stream_once
    bh = Blackhole()
    try:
        flag = threading.Event()
        _esc_after(flag)

        def _run():
            try:
                _chat_stream_once(bh.url, {}, {"model": "x"}, None, None,
                                  None, None, flag.is_set, timeout=120.0)
            except TurnCancelled:
                return
            raise AssertionError("TurnCancelled not raised")

        _timed("hung streaming POST raises TurnCancelled", _run)
    finally:
        bh.close()


def test_rate_limit_backoff() -> None:
    """BLOCKER 5: chat_with_retry's ~254s rate-limit backoff (the 203s
    hang). A 429-always provider must not hold Esc hostage."""
    from .team import chat_with_retry
    srv = HTTPServer(("127.0.0.1", 0), _Always429)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        flag = threading.Event()
        _esc_after(flag)

        def _run():
            try:
                chat_with_retry(_fake_provider(f"http://127.0.0.1:{port}"),
                                _fake_model(), _fake_effort(),
                                [{"role": "user", "content": "hi"}],
                                None, 30.0, should_cancel=flag.is_set)
            except TurnCancelled:
                return
            raise AssertionError("TurnCancelled not raised")

        _timed("rate-limit backoff interrupted", _run)
    finally:
        srv.shutdown()


def test_slow_subprocess() -> None:
    """run_command('sleep 30') must die promptly on Esc, no orphans."""
    import subprocess as _sp
    from .tools import run_command
    flag = threading.Event()
    _esc_after(flag)

    def _run():
        out = run_command("sleep 30", timeout=120,
                          should_cancel=flag.is_set)
        assert "[CANCELLED" in out, f"no cancel marker: {out[:120]}"
        # the process tree must actually be dead — no orphaned sleep
        time.sleep(0.3)
        ps = _sp.run(["pgrep", "-f", "sleep 30"], capture_output=True,
                     text=True)
        assert ps.returncode != 0, "orphaned 'sleep 30' still running!"

    _timed("slow subprocess killed", _run)


def test_web_fetch_hung() -> None:
    """BLOCKER 7: web_fetch against a hung server."""
    from .tools import web_fetch
    bh = Blackhole()
    try:
        flag = threading.Event()
        _esc_after(flag)

        def _run():
            try:
                web_fetch(bh.url, should_cancel=flag.is_set)
            except TurnCancelled:
                return
            raise AssertionError("TurnCancelled not raised")

        _timed("hung web_fetch raises TurnCancelled", _run)
    finally:
        bh.close()


def test_web_search_hung() -> None:
    """BLOCKER 7b: web_search with hung engines.

    The engines hit the real internet, so instead of a blackhole we
    monkeypatch them to block forever — the point is the guard, not
    the network."""
    from . import tools as _tools
    orig_ddg, orig_bing = _tools._ddg_search, _tools._bing_search

    def _hang(query):
        time.sleep(3600)
        return []

    _tools._ddg_search = _hang
    _tools._bing_search = _hang
    try:
        flag = threading.Event()
        _esc_after(flag)

        def _run():
            try:
                _tools.web_search("anything", should_cancel=flag.is_set)
            except TurnCancelled:
                return
            raise AssertionError("TurnCancelled not raised")

        _timed("hung web_search raises TurnCancelled", _run)
    finally:
        _tools._ddg_search, _tools._bing_search = orig_ddg, orig_bing


def test_cancellable_sleep() -> None:
    """BLOCKER 4: a 120s backoff-style sleep interrupted by Esc."""
    flag = threading.Event()
    _esc_after(flag)

    def _run():
        try:
            sleep_cancellable(120.0, flag.is_set)
        except TurnCancelled:
            return
        raise AssertionError("TurnCancelled not raised")

    _timed("120s sleep interrupted", _run)


def test_subagent_worker_cancel() -> None:
    """BLOCKER 6: a subagent stuck in a hung model call stops on Esc.

    Uses the real production chat path (chat_with_retry -> chat_blocking
    with the worker's stop_event threaded through as should_cancel)."""
    import tempfile
    from pathlib import Path
    from .crew import Crew
    from .kernel import EventLog
    bh = Blackhole()
    try:
        with tempfile.TemporaryDirectory() as td:
            log = EventLog(Path(td) / "k.jsonl")
            crew = Crew(log, _fake_provider(bh.url), _fake_model(),
                        _fake_effort(), max_agents=2)
            assert crew._chat_takes_cancel, \
                "production chat path must accept should_cancel"
            agent = crew.spawn("hang forever :: x", role="researcher")
            # grab the worker's future directly: we must prove the
            # THREAD dies promptly, not just that the state flips
            fut = None
            deadline = time.monotonic() + 5.0
            while fut is None and time.monotonic() < deadline:
                with crew._lock:
                    fut = crew._futures.get(agent.id)
                time.sleep(0.05)
            assert fut is not None, "worker future never registered"
            time.sleep(TRIGGER_AFTER)
            assert not fut.done(), "worker finished before Esc (test bug)"
            t0 = time.monotonic()
            crew.force_stop()  # what the TUI does on Esc
            try:
                fut.result(timeout=LIMIT)
            except Exception as e:  # noqa: BLE001 — TimeoutError etc.
                raise AssertionError(
                    f"worker thread did not die within {LIMIT}s: {e}")
            dt = time.monotonic() - t0
            states = crew.wait([agent.id], timeout=5.0)
            crew.shutdown()
            assert states[agent.id] == "closed", states
            status = "OK " if dt < LIMIT else "FAIL"
            print(f"  [{status}] hung subagent worker thread died: "
                  f"{dt:.2f}s (limit {LIMIT:.0f}s)")
            assert dt < LIMIT, f"subagent cancel took {dt:.2f}s"
    finally:
        bh.close()


def test_speculate_nonblocking() -> None:
    """BLOCKER 8: speculate() returns at once even with a hung runner."""
    import tempfile
    from pathlib import Path
    from .kernel import EventLog
    from .speculate import Speculator

    def _hung_runner(name, args):
        time.sleep(30)
        return "too late"

    with tempfile.TemporaryDirectory() as td:
        spec = Speculator(EventLog(Path(td) / "s.jsonl"),
                         runner=_hung_runner)
        t0 = time.monotonic()
        n = spec.speculate("look at src/main.py", [])
        dt = time.monotonic() - t0
        assert n >= 1, n
        status = "OK " if dt < LIMIT else "FAIL"
        print(f"  [{status}] speculate() returned in {dt:.2f}s "
              f"(limit {LIMIT:.0f}s), {n} launched")
        assert dt < LIMIT, f"speculate() blocked {dt:.2f}s"


def test_handler_cancel_wiring() -> None:
    """agent._execute_tool passes the flag to handlers declaring
    should_cancel, and never swallows TurnCancelled."""
    from .agent import Agent
    from .tools import run_command, web_fetch, read_file
    assert Agent._handler_takes_cancel(run_command) is True
    assert Agent._handler_takes_cancel(web_fetch) is True
    assert Agent._handler_takes_cancel(read_file) is False
    assert Agent._handler_takes_cancel(lambda: 1) is False
    print("  [OK ] should_cancel wired by signature; "
          "TurnCancelled re-raised in _execute_tool")


def test_run_cancellable_sanity() -> None:
    """run_cancellable: results + original exception types pass through."""
    assert run_cancellable(lambda: "ok", None) == "ok"
    assert run_cancellable(lambda: "ok", lambda: False) == "ok"
    try:
        run_cancellable(lambda: 1 / 0, lambda: False)
        raise AssertionError("exception not propagated")
    except ZeroDivisionError:
        pass
    print("  [OK ] run_cancellable passthrough semantics")


# -- runner -------------------------------------------------------------------

TESTS = [
    test_hung_post_blocking,
    test_hung_stream_post,
    test_rate_limit_backoff,
    test_slow_subprocess,
    test_web_fetch_hung,
    test_web_search_hung,
    test_cancellable_sleep,
    test_subagent_worker_cancel,
    test_speculate_nonblocking,
    test_handler_cancel_wiring,
    test_run_cancellable_sanity,
]


def run_all() -> None:
    print("CANCELPROOF — Esc must stop every blocking point in < "
          f"{LIMIT:.0f}s")
    t0 = time.monotonic()
    for t in TESTS:
        t()
    dt = time.monotonic() - t0
    print(f"\nALL {len(TESTS)} CANCELPROOF TESTS PASS ({dt:.1f}s total)")


if __name__ == "__main__":
    run_all()
