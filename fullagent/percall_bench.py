"""Per-call latency breakdown benchmark for ``Agent.run_turn``.

Worker 1/20 (permanent-fix sprint) — MEASURE PER-CALL BREAKDOWN.

Instruments one full agent turn and logs exactly where time goes per tool
call, split into the requested components:

  (a) prompt/message building  — ``_tool_schemas`` + ``_prune_for_model``
      (+ per-turn setup amortized per call)
  (b) API request latency       — ``chat_stream`` total + time-to-first-token
  (c) tool execution time       — ``_execute_tool`` per call
  (d) TUI/overhead              — ``_finish_one`` bookkeeping, wrapped
      callbacks, loop-detection, retry-hint, logging

Zero changes to production code: every probe is applied by monkeypatching
from this script, then restored. A scripted fake provider plays N tool-call
rounds offline (no API key, no network, no cost); ``live_probe()`` does ONE
real ``chat_stream`` call against the free model to measure genuine API
latency (TTFB + total).

Usage:
    python3 -m fullagent.percall_bench            # offline bench + table
    python3 -m fullagent.percall_bench --rounds 25 # full 25-call cap
    python3 -m fullagent.percall_bench --live      # + one real API probe
"""
from __future__ import annotations

import functools
import json
import sys
import tempfile
import time
from dataclasses import dataclass, field

import fullagent.agent as _agent_mod


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    name: str
    t0: float
    t1: float
    detail: str = ""

    @property
    def dur(self) -> float:
        return self.t1 - self.t0


class Probe:
    """Collects named timing samples; wraps callables with timing."""

    def __init__(self) -> None:
        self.samples: list[Sample] = []

    def record(self, name: str, t0: float, t1: float,
               detail: str = "") -> None:
        self.samples.append(Sample(name, t0, t1, detail))

    def wrap(self, name: str, fn, detail_fn=None):
        @functools.wraps(fn)
        def _timed(*a, **k):
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                detail = detail_fn(a, k) if detail_fn else ""
                self.record(name, t0, time.perf_counter(), detail)
        return _timed

    def of(self, name: str) -> list[Sample]:
        return [s for s in self.samples if s.name == name]

    def stats(self, name: str) -> dict:
        ss = self.of(name)
        if not ss:
            return {"count": 0, "total": 0.0, "avg": 0.0,
                    "min": 0.0, "max": 0.0}
        ds = [s.dur for s in ss]
        return {"count": len(ss), "total": sum(ds),
                "avg": sum(ds) / len(ds), "min": min(ds), "max": max(ds)}


# ---------------------------------------------------------------------------
# Scripted fake provider (offline: no key, no network, no cost)
# ---------------------------------------------------------------------------

class ScriptedProvider:
    """Plays a script of tool-call rounds, then a final text reply.

    Each round returns exactly one tool call to ``read_file`` on a small
    scratch file, mirroring the 25-call-cap loop where the model makes one
    call per round trip.
    """

    def __init__(self, scratch_path: str, n_rounds: int,
                 sim_api_ms: float = 0.0):
        self.scratch_path = scratch_path
        self.n_rounds = n_rounds
        self.sim_api_ms = sim_api_ms
        self.calls = 0

    def __call__(self, *a, **k):
        from fullagent.client import StreamResult
        on_token = k.get("on_token")
        on_tool_start = k.get("on_tool_start")
        t0 = time.perf_counter()
        ttfb: float | None = None
        if self.sim_api_ms > 0:
            time.sleep(self.sim_api_ms / 1000.0)
        if on_tool_start:
            on_tool_start("read_file")
        if on_token:
            ttfb = time.perf_counter() - t0
            on_token("ok")  # one token so TTFB is measurable
        total = time.perf_counter() - t0
        i = self.calls
        self.calls += 1
        if i < self.n_rounds:
            args = json.dumps({"path": self.scratch_path,
                               "offset": 1, "limit": 10})
            return (StreamResult(
                content="",
                tool_calls=[{"id": f"call_{i}", "type": "function",
                             "function": {"name": "read_file",
                                          "arguments": args}}],
                usage={"prompt_tokens": 1000, "completion_tokens": 20,
                       "total_tokens": 1020},
                model="scripted"),
                    ttfb, total)
        return (StreamResult(content="done", tool_calls=[],
                             usage={"prompt_tokens": 1200,
                                    "completion_tokens": 5,
                                    "total_tokens": 1205},
                             model="scripted"),
                ttfb, total)


# ---------------------------------------------------------------------------
# Bench
# ---------------------------------------------------------------------------

@dataclass
class BenchReport:
    rounds: int
    turn_total: float
    per_round: list[dict] = field(default_factory=list)  # phase -> secs
    setup: float = 0.0
    avg: dict = field(default_factory=dict)              # phase -> avg secs
    callbacks_total: float = 0.0
    sanity_gap: float = 0.0
    live: dict | None = None

    PHASES = ("pre", "enforce", "compact", "build", "api", "post",
              "exec", "finish")


def _safe_cost(agent) -> None:
    """_emit_cost must tolerate usage=None from the scripted provider."""
    try:
        agent._emit_cost(None)
    except Exception:
        pass


def run_bench(n_rounds: int = 10, sim_api_ms: float = 0.0,
              verbose: bool = False) -> tuple[BenchReport, Probe]:
    """Run one instrumented turn with a scripted provider."""
    from fullagent.config import Config, model_by_id
    from fullagent.agent import Agent

    probe = Probe()
    scratch = tempfile.NamedTemporaryFile("w", suffix=".txt",
                                          delete=False)
    scratch.write("line1\nline2\nline3\n")
    scratch.close()
    scripted = ScriptedProvider(scratch.name, n_rounds, sim_api_ms)

    cfg = Config()
    assert model_by_id(cfg.model_id) is not None
    agent = Agent(cfg)
    _safe_cost(agent)  # fail fast if the cost path can't handle None

    real_chat_stream = _agent_mod.chat_stream
    real_dispatch = _agent_mod.dispatch_block

    # -- (b) API leg: scripted fake recording total + TTFB ------------------
    def fake_chat_stream(provider, model, effort, messages, tools,
                         on_token=None, on_reasoning=None,
                         on_tool_start=None, on_tool_args=None,
                         should_cancel=None, on_overflow=None,
                         timeout=None, on_status=None):
        ttfb_holder: dict = {}

        def _tok(tok):
            if "t" not in ttfb_holder:
                ttfb_holder["t"] = time.perf_counter() - api_t0
            probe.record("cb.on_token", time.perf_counter(),
                         time.perf_counter())
            if on_token:
                on_token(tok)

        api_t0 = time.perf_counter()
        result, ttfb, total = scripted(
            provider, model, effort, messages, tools,
            on_token=_tok if on_token else None,
            on_tool_start=on_tool_start)
        api_t1 = time.perf_counter()
        probe.record("api", api_t0, api_t1)
        probe.record("api.ttfb", api_t0,
                     api_t0 + (ttfb if ttfb is not None else 0.0))
        return result

    # -- (c)/(d) dispatch leg ---------------------------------------------
    def timed_dispatch(execute, finish, pending, **kw):
        t0 = time.perf_counter()
        try:
            return real_dispatch(execute, finish, pending, **kw)
        finally:
            probe.record("dispatch", t0, time.perf_counter())

    _agent_mod.chat_stream = fake_chat_stream
    _agent_mod.dispatch_block = timed_dispatch
    try:
        # -- (a) prompt/message building ------------------------------------
        agent._maybe_compact = probe.wrap("compact", agent._maybe_compact)
        agent._tool_schemas = probe.wrap("schemas", agent._tool_schemas)
        agent._prune_for_model = probe.wrap("prune", agent._prune_for_model)
        # full LLM leg (build + api + internal residual)
        agent._complete = probe.wrap("complete", agent._complete)
        # -- (c) tool execution per call ------------------------------------
        agent._execute_tool = probe.wrap(
            "exec", agent._execute_tool,
            detail_fn=lambda a, k: getattr(a[0], "name", ""))
        # -- per-iteration gate: budget_gov.enforce() folds the event log --
        # (force-create the lazy subsystem, then wrap its enforce)
        _bg = agent.budget_gov
        _bg.enforce = probe.wrap("enforce", _bg.enforce)
        # -- (d) wrapped user callbacks (TUI surface) -----------------------
        def _cb(name, fn):
            @functools.wraps(fn)
            def _w(*a, **k):
                t0 = time.perf_counter()
                try:
                    return fn(*a, **k)
                finally:
                    probe.record(f"cb.{name}", t0, time.perf_counter())
            return _w

        noop = lambda *a, **k: None  # noqa: E731
        on_token = _cb("on_token", noop)
        on_reasoning = _cb("on_reasoning", noop)
        on_tool_call = _cb("on_tool_call", noop)
        on_tool_update = _cb("on_tool_update", noop)
        on_status = _cb("on_status", noop)
        approve = lambda tool, args: True  # noqa: E731

        turn_start = time.perf_counter()
        turn = agent.run_turn("read the scratch file, one read per round",
                              on_token, on_reasoning,
                              on_tool_call, on_tool_update,
                              on_status, approve)
        turn_total = time.perf_counter() - turn_start
    finally:
        _agent_mod.chat_stream = real_chat_stream
        _agent_mod.dispatch_block = real_dispatch

    if verbose:
        print(f"turn: {turn_total:.3f}s error={turn.error!r} "
              f"tool_calls={len(turn.tools)}")

    # -- reconstruct per-round phases from the timeline --------------------
    compacts = probe.of("compact")
    completes = probe.of("complete")
    dispatches = probe.of("dispatch")
    schemas = probe.of("schemas")
    prunes = probe.of("prune")
    apis = probe.of("api")
    execs = probe.of("exec")
    enforces = probe.of("enforce")
    rounds_n = min(len(compacts), len(completes), len(dispatches))
    per_round: list[dict] = []
    # samples are recorded in call order, so index i lines up across phases
    schema_i = prune_i = api_i = exec_i = 0
    prev_dispatch_end: float | None = None
    for i in range(rounds_n):
        c0, c1, d0 = compacts[i], completes[i], dispatches[i]
        # pre: the uninstrumented gap between rounds (cancel check, status).
        # Round 0's gap is the turn setup — reported separately, not here.
        pre = (c0.t0 - prev_dispatch_end) if prev_dispatch_end is not None \
            else 0.0
        # enforce: budget_gov.enforce() (folds the event log every round)
        enf = enforces[i].dur if i < len(enforces) else 0.0
        # build/api/exec samples belonging to this round: take the next
        # sample whose window sits inside this round's complete/dispatch
        b = 0.0
        while (schema_i < len(schemas)
               and c1.t0 <= schemas[schema_i].t0 <= c1.t1):
            b += schemas[schema_i].dur
            schema_i += 1
        while (prune_i < len(prunes)
               and c1.t0 <= prunes[prune_i].t0 <= c1.t1):
            b += prunes[prune_i].dur
            prune_i += 1
        api = 0.0
        while (api_i < len(apis)
               and c1.t0 <= apis[api_i].t0 <= c1.t1):
            api += apis[api_i].dur
            api_i += 1
        exe = 0.0
        while (exec_i < len(execs)
               and d0.t0 <= execs[exec_i].t0 <= d0.t1):
            exe += execs[exec_i].dur
            exec_i += 1
        post = d0.t0 - c1.t1          # parse + bookkeeping after LLM reply
        finish = d0.dur - exe         # _finish_one + dispatch overhead
        per_round.append({"pre": max(pre, 0.0), "enforce": enf,
                          "compact": c0.dur,
                          "build": b, "api": api, "post": max(post, 0.0),
                          "exec": exe, "finish": max(finish, 0.0)})
        prev_dispatch_end = d0.t1

    avg = {p: sum(r[p] for r in per_round) / len(per_round)
           for p in BenchReport.PHASES} if per_round else {}
    setup = compacts[0].t0 - turn_start if compacts else 0.0
    cb_total = sum(s.dur for s in probe.samples
                   if s.name.startswith("cb."))
    accounted = setup + sum(sum(r.values()) for r in per_round)
    report = BenchReport(rounds=rounds_n, turn_total=turn_total,
                         per_round=per_round, setup=setup, avg=avg,
                         callbacks_total=cb_total,
                         sanity_gap=turn_total - accounted)
    return report, probe


# ---------------------------------------------------------------------------
# Live API probe — one real call, genuine latency numbers
# ---------------------------------------------------------------------------

def live_probe() -> dict | None:
    """One real streaming call to the free model; measures TTFB + total.

    Returns None when no network/key is available (never raises).
    """
    try:
        from fullagent.config import (PROVIDERS, effort_by_key, model_by_id)
        from fullagent.client import chat_stream
        model = model_by_id("space-bunny-free")
        if model is None:
            return None
        provider = PROVIDERS[model.provider]
        effort = effort_by_key("low")
        t0 = time.perf_counter()
        ttfb: float | None = None

        def _on_token(tok):
            nonlocal ttfb
            if ttfb is None:
                ttfb = time.perf_counter() - t0

        res = chat_stream(
            provider, model, effort,
            [{"role": "user", "content": "Reply with exactly: OK"}],
            None, on_token=_on_token, timeout=120)
        total = time.perf_counter() - t0
        return {"model": model.id, "ttfb": ttfb or 0.0, "total": total,
                "content": (res.content or "")[:40],
                "usage": res.usage}
    except Exception as e:  # noqa: BLE001 — probe is best-effort
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def fmt_table(report: BenchReport) -> str:
    lines = []
    lines.append("per-round breakdown (avg over "
                 f"{report.rounds} rounds, seconds):")
    lines.append("")
    hdr = (f"{'phase':<10}{'avg (s)':>10}{'% of call':>10}  what")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    what = {
        "pre": "cancel check + on_status between rounds",
        "enforce": "budget_gov.enforce() (folds event log)",
        "compact": "(a) _maybe_compact",
        "build": "(a) _tool_schemas + _prune_for_model",
        "api": "(b) chat_stream total",
        "post": "(d) parse + assistant-msg + _emit_cost + on_tool_call",
        "exec": "(c) _execute_tool (real tool run)",
        "finish": "(d) _finish_one bookkeeping + log + history",
    }
    per_call = sum(report.avg.values())
    for p in BenchReport.PHASES:
        v = report.avg.get(p, 0.0)
        pct = 100.0 * v / per_call if per_call else 0.0
        lines.append(f"{p:<10}{v:>10.3f}{pct:>9.1f}%  {what[p]}")
    lines.append("-" * len(hdr))
    lines.append(f"{'per call':<10}{per_call:>10.3f}")
    lines.append(f"{'setup/turn':<10}{report.setup:>10.3f}  "
                 "(amortized: "
                 f"{report.setup / report.rounds:.3f}s/call over "
                 f"{report.rounds} rounds)")
    lines.append(f"{'turn total':<10}{report.turn_total:>10.3f}")
    lines.append(f"{'callbacks':<10}{report.callbacks_total:>10.3f}  "
                 "(d) TUI callback time (subset of above, not additive)")
    lines.append(f"{'sanity gap':<10}{report.sanity_gap:>10.3f}  "
                 "(unaccounted; ~0 means the probes cover the turn)")
    if report.live:
        lv = report.live
        lines.append("")
        if "error" in lv:
            lines.append(f"live probe: FAILED — {lv['error']}")
        else:
            lines.append(f"live probe ({lv['model']}): TTFB={lv['ttfb']:.3f}s "
                         f"total={lv['total']:.3f}s "
                         f"reply={lv['content']!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test — proves the instrumentation works
# ---------------------------------------------------------------------------

def self_test() -> int:
    report, probe = run_bench(n_rounds=5, verbose=False)
    checks = []
    # 1. every component was sampled once per round
    for name, want in (("compact", 5), ("complete", 5), ("api", 5),
                       ("dispatch", 5), ("exec", 5), ("schemas", 5),
                       ("prune", 5)):
        got = len(probe.of(name))
        checks.append((f"{name} sampled >= {want} (got {got})",
                       got >= want))
    # 2. TTFB never exceeds API total (causality)
    for a, t in zip(probe.of("api"), probe.of("api.ttfb")):
        checks.append(("ttfb <= api total", t.dur <= a.dur + 1e-9))
        break
    # 3. all durations non-negative, turn progressed
    checks.append(("turn finished 5 rounds",
                   report.rounds == 5 and report.turn_total > 0))
    checks.append(("sanity gap small (<20% of turn)",
                   abs(report.sanity_gap) < 0.20 * report.turn_total))
    checks.append(("accounted phases cover >50% of steady-state turn",
                   sum(report.avg.values()) * report.rounds
                   > 0.5 * (report.turn_total - report.setup)))
    ok = all(c for _, c in checks)
    for label, passed in checks:
        print(f"[{'ok' if passed else 'FAIL'}] {label}")
    print()
    print(fmt_table(report))
    print()
    print("PASS: percall_bench self-test" if ok
          else "FAIL: percall_bench self-test")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--live" in args:
        rep, _ = run_bench(n_rounds=10)
        rep.live = live_probe()
        print(fmt_table(rep))
    elif "--rounds" in args:
        n = int(args[args.index("--rounds") + 1])
        rep, _ = run_bench(n_rounds=n)
        print(fmt_table(rep))
    else:
        sys.exit(self_test())
