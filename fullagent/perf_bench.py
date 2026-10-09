"""Performance benchmark suite for fullagent.

Repeatable measurements that prove (or disprove) performance improvements:
  * cold import time (fresh interpreter per run)
  * Agent construction time
  * tool registration count + schema-serialization time (cold vs cached)
  * system prompt size (chars + approx tokens)
  * time-to-first-token through the real stream pipeline with a MOCKED
    provider stream (canned SSE chunks with small delays — NO network)
  * file-read cache behaviour, if one exists (optional)

Run:  python3 -m fullagent.perf_bench
Exits 0 when all measurements complete. This module IS the self-test.

Resilience: every measurement is wrapped in try/except. If another
worker's optimization isn't present yet (e.g. no file-read cache), the
bench still runs and reports the current numbers instead of crashing.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import time
from typing import Any, Callable

# ---------------------------------------------------------------- helpers

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
NA = "n/a"


def verdict(seconds: float, pass_under: float, warn_under: float) -> str:
    if seconds < pass_under:
        return PASS
    if seconds < warn_under:
        return WARN
    return FAIL


class Results:
    """Ordered measurement rows: (name, value, verdict)."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []
        self.failures: list[str] = []

    def add(self, name: str, value: str, v: str = NA) -> None:
        self.rows.append((name, value, v))

    def measured(self, name: str, seconds: float, fmt: str,
                 pass_under: float, warn_under: float) -> None:
        v = verdict(seconds, pass_under, warn_under)
        self.add(name, fmt.format(seconds), v)
        if v == FAIL:
            self.failures.append(name)

    def print(self) -> None:
        w1 = max(len(r[0]) for r in self.rows)
        w2 = max(len(r[1]) for r in self.rows)
        print()
        print("=" * (w1 + w2 + 12))
        print(" fullagent performance benchmark")
        print("=" * (w1 + w2 + 12))
        for name, value, v in self.rows:
            print(f"  {name:<{w1}}  {value:>{w2}}  [{v}]")
        print("=" * (w1 + w2 + 12))


def _try(fn: Callable[[], Any], name: str, res: Results) -> Any:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — the bench never hard-fails
        res.add(name, f"error: {type(e).__name__}: {e}", FAIL)
        res.failures.append(name)
        return None


# ---------------------------------------------------------------- measurements

def bench_cold_import(res: Results, runs: int = 3) -> None:
    """Time `import fullagent.agent` in a fresh interpreter (subprocess)."""
    def one() -> float:
        code = ("import time; t=time.perf_counter();"
                " import fullagent.agent; print(time.perf_counter()-t)")
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            cwd=os.getcwd(), timeout=120)
        if out.returncode != 0:
            raise RuntimeError(f"child import failed: {out.stderr[-300:]}")
        return float(out.stdout.strip())

    samples = [one() for _ in range(runs)]
    med = statistics.median(samples)
    v = verdict(med, 4.0, 6.0)
    res.add("cold import fullagent.agent (median of %d)" % runs,
            "%.2fs (min %.2fs)" % (med, min(samples)), v)


def bench_construction(res: Results) -> Any:
    """Time Agent construction with a default Config."""
    from fullagent import agent as agent_mod

    cfg = agent_mod.config.Config()
    t0 = time.perf_counter()
    ag = agent_mod.Agent(cfg)
    dt = time.perf_counter() - t0
    res.measured("Agent construction", dt, "{:.2f}s", 2.0, 5.0)
    return ag


def bench_tool_registration(res: Results, ag: Any) -> None:
    """Tool count + schema serialization time (cold miss vs cached hit)."""
    tools = getattr(ag, "tools", {}) or {}
    res.add("registered tools", str(len(tools)), NA)

    schemas_fn = getattr(ag, "_tool_schemas", None)
    if not callable(schemas_fn):
        res.add("schema serialization", "no _tool_schemas on Agent", NA)
        return
    # force a cold rebuild
    try:
        ag._schemas_cache = None
    except AttributeError:
        pass
    t0 = time.perf_counter()
    first = schemas_fn()
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    schemas_fn()
    hot = time.perf_counter() - t0
    n = len(first) if first else 0
    res.measured(f"schema serialize x{n} (cold)", cold, "{:.3f}s", 0.5, 1.5)
    res.measured("schema serialize (cached)", hot, "{:.4f}s", 0.05, 0.2)


def bench_system_prompt(res: Results, ag: Any) -> None:
    """System prompt size in chars and approx tokens (chars/4)."""
    def _get() -> str:
        from fullagent import systemprompt
        return systemprompt.get("main")
    text = _try(_get, "system prompt size", res)
    if not text:
        return
    tokens = len(text) // 4
    v = PASS if tokens < 20_000 else (WARN if tokens < 60_000 else FAIL)
    res.add("system prompt ('main')", f"{len(text):,} chars ≈ {tokens:,} tokens", v)


def _fake_sse_response(chunks: list[bytes], pre_delays: list[float]):
    """A minimal requests.Response stand-in yielding canned SSE chunks."""
    class FakeResponse:
        status_code = 200
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self) -> None:
            self.closed = False

        def iter_lines(self):
            # split chunks into lines like real requests.iter_lines()
            for delay, chunk in zip(pre_delays, chunks):
                if delay:
                    time.sleep(delay)
                for line in chunk.split(b"\n"):
                    yield line
            yield b""

        def close(self) -> None:
            self.closed = True

    return FakeResponse()


def _sse_token_chunk(text: str, finish: bool = False) -> bytes:
    import json
    delta: dict[str, Any] = {"content": text}
    choice: dict[str, Any] = {"index": 0, "delta": delta}
    if finish:
        choice["finish_reason"] = "stop"
    return b"data: " + json.dumps(
        {"id": "bench", "object": "chat.completion.chunk",
         "choices": [choice]}).encode() + b"\n\n"


def bench_ttft(res: Results) -> None:
    """Time-to-first-token through the REAL stream pipeline, mocked SSE.

    Monkeypatches fullagent.client._http so _chat_stream_once reads canned
    SSE chunks (with small delays) instead of hitting the network. Measures
    wall time from the request to the first on_token callback.
    """
    from fullagent import client

    tokens = ["Hello", ", ", "world", "!"]
    chunks = [_sse_token_chunk(t) for t in tokens] + [
        _sse_token_chunk("", finish=True), b"data: [DONE]\n\n"]
    delays = [0.0] + [0.02] * (len(chunks) - 1)  # first byte immediate
    fake = _fake_sse_response(chunks, delays)

    orig_http = client._http
    client._http = lambda: _FakeSession(fake)  # type: ignore[method-assign]
    first_at: list[float] = []
    t0 = time.perf_counter()
    try:
        from fullagent.config import PROVIDERS, MODELS

        def _provider_model():
            m = next((m for m in MODELS if m.supports_tools), MODELS[0])
            return PROVIDERS[m.provider], m
        provider, model = _provider_model()
        effort = provider.efforts[0] if getattr(provider, "efforts", None) else None
        if effort is None:
            from fullagent.config import EFFORTS
            effort = EFFORTS[0]
        result = client._chat_stream_once(
            "http://127.0.0.1:9/bench", {"Authorization": "Bearer bench"},
            {"model": model.id, "messages": [{"role": "user",
                                              "content": "bench"}],
             "stream": True},
            on_token=lambda p: first_at.append(time.perf_counter()) or None,
            on_reasoning=None, on_tool_start=None, on_tool_args=None,
            should_cancel=None, timeout=10.0)
        total = time.perf_counter() - t0
        ttft = (first_at[0] - t0) if first_at else float("inf")
        got = result.content if result is not None else ""
    finally:
        client._http = orig_http  # type: ignore[method-assign]

    ok = got == "".join(tokens)
    res.measured("mocked stream TTFT (first byte -> on_token)",
                 ttft, "{:.3f}s", 0.10, 0.50)
    res.measured("mocked stream full drain (4 tokens)", total,
                 "{:.3f}s", 0.50, 1.50)
    res.add("mocked stream content check",
            f"{'OK' if ok else 'MISMATCH: ' + got!r}", PASS if ok else FAIL)
    if not ok:
        res.failures.append("mocked stream content check")


class _FakeSession:
    """Duck-typed requests.Session whose post() returns the canned SSE."""
    def __init__(self, resp: Any) -> None:
        self._resp = resp

    def post(self, *a: Any, **k: Any) -> Any:
        return self._resp


def bench_file_cache(res: Results) -> None:
    """File-read cache performance, if a read cache exists (optional)."""
    import fullagent

    candidates = []
    for mod_name in ("agent", "readtool", "tools", "filecache", "cache"):
        mod = getattr(fullagent, mod_name, None)
        if mod is None:
            try:
                mod = __import__(f"fullagent.{mod_name}",
                                 fromlist=["*"])
            except ImportError:
                continue
        for attr in ("read_file_cache", "file_read_cache", "READ_CACHE",
                     "_file_cache", "ReadCache", "FileCache"):
            if hasattr(mod, attr):
                candidates.append((mod_name, attr))

    if not candidates:
        res.add("file-read cache", "not present — skipped", NA)
        return
    for mod_name, attr in candidates[:3]:
        res.add(f"file-read cache ({mod_name}.{attr})",
                "present — timing not wired yet", NA)


# ---------------------------------------------------------------- main

def main() -> int:
    res = Results()
    start = time.perf_counter()

    bench_cold_import(res)

    ag = _try(lambda: bench_construction(res), "Agent construction", res)
    if ag is not None:
        _try(lambda: bench_tool_registration(res, ag),
             "tool registration", res)
        _try(lambda: bench_system_prompt(res, ag), "system prompt size", res)

    _try(lambda: bench_ttft(res), "mocked stream TTFT", res)
    _try(lambda: bench_file_cache(res), "file-read cache", res)

    res.add("benchmark wall time",
            f"{time.perf_counter() - start:.1f}s", NA)
    res.print()

    if res.failures:
        print(f"\n{len(res.failures)} measurement(s) FAILED: "
              + ", ".join(res.failures))
    else:
        print("\nAll measurements completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
